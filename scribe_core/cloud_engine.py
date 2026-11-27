"""
scribe_core/cloud_engine.py

Hosted cloud transcription engine for Session Scribe.
Supports NVIDIA Riva gRPC (build.nvidia.com) and OpenAI-compatible HTTP providers.
Handles:
  - Provider profile management
  - Word-level timestamp segment building
  - Silence-aware chunking and checkpointing
  - Retry logic with exponential backoff
  - Audio cutting and seam-stitching for resume
  - Secret safety (API key never logged, stored, or leaked)
"""

from __future__ import annotations

import os
import sys
import io
import time
import math
import wave
import struct
import json
import hashlib
import subprocess
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass, asdict
from typing import List, Dict, Any, Optional, Tuple, Callable

IST = timezone(timedelta(hours=5, minutes=30))


# ---------------------------------------------------------------------------
# Failover Error Classes (12 Specific Diagnostic Categories)
# ---------------------------------------------------------------------------
FAILOVER_AUTH = "auth"
FAILOVER_KEY_NOT_FOUND = "key_not_found"
FAILOVER_FUNCTION_NOT_FOUND = "function_not_found"
FAILOVER_INVALID_ARGUMENT = "invalid_argument"
FAILOVER_TIMEOUT = "timeout"
FAILOVER_RATE_LIMIT = "rate_limit"
FAILOVER_QUOTA = "quota"
FAILOVER_NETWORK_TLS = "network_tls"
FAILOVER_EGRESS_GUARD = "egress_guard"
FAILOVER_CONSENT = "consent"
FAILOVER_PAYLOAD_TOO_LARGE = "payload_too_large"
FAILOVER_SERVER_ERROR = "server_error"

ALL_FAILOVER_CLASSES = [
    FAILOVER_AUTH,
    FAILOVER_KEY_NOT_FOUND,
    FAILOVER_FUNCTION_NOT_FOUND,
    FAILOVER_INVALID_ARGUMENT,
    FAILOVER_TIMEOUT,
    FAILOVER_RATE_LIMIT,
    FAILOVER_QUOTA,
    FAILOVER_NETWORK_TLS,
    FAILOVER_EGRESS_GUARD,
    FAILOVER_CONSENT,
    FAILOVER_PAYLOAD_TOO_LARGE,
    FAILOVER_SERVER_ERROR,
]

CONFIGURATION_ERROR_CLASSES = {
    FAILOVER_AUTH,
    FAILOVER_KEY_NOT_FOUND,
    FAILOVER_FUNCTION_NOT_FOUND,
    FAILOVER_INVALID_ARGUMENT,
    FAILOVER_EGRESS_GUARD,
    FAILOVER_CONSENT,
}

TRANSIENT_ERROR_CLASSES = {
    FAILOVER_TIMEOUT,
    FAILOVER_NETWORK_TLS,
    FAILOVER_SERVER_ERROR,
    FAILOVER_RATE_LIMIT,
    FAILOVER_QUOTA,
    FAILOVER_PAYLOAD_TOO_LARGE,
}


def classify_cloud_error(exc: Exception, raw_err_str: Optional[str] = None) -> Tuple[str, bool]:
    """
    Classify an exception into one of the 12 standard failure classes and return:
    (failover_class, is_retryable)
    where configuration errors return is_retryable=False and transient errors return is_retryable=True.
    """
    err_str = str(raw_err_str or exc).lower()

    # 1. Key not found
    if "key not found" in err_str or "empty api key" in err_str or "api key is empty" in err_str or "key is missing" in err_str:
        return FAILOVER_KEY_NOT_FOUND, False

    # 2. Auth error
    if (
        isinstance(exc, CloudAuthError)
        or "permissiondenied" in err_str
        or "permission_denied" in err_str
        or "unauthenticated" in err_str
        or "authorization failed" in err_str
        or "invalid api key" in err_str
        or "invalid key" in err_str
        or "401" in err_str
    ):
        return FAILOVER_AUTH, False

    # 3. Function not found
    if "not_found" in err_str or "function not found" in err_str or "function-id" in err_str or "404" in err_str:
        return FAILOVER_FUNCTION_NOT_FOUND, False

    # 4. Invalid argument (language, encoding, sample rate)
    if "invalid_argument" in err_str or "unsupported language" in err_str or "invalid argument" in err_str or "400" in err_str:
        return FAILOVER_INVALID_ARGUMENT, False

    # 5. Egress guard
    if "egress" in err_str or "egress_guard" in err_str or "outbound blocked" in err_str:
        return FAILOVER_EGRESS_GUARD, False

    # 6. Consent
    if "consent" in err_str or "consent not granted" in err_str or "user denied consent" in err_str:
        return FAILOVER_CONSENT, False

    # 7. Payload too large
    if (
        "out_of_range" in err_str
        or "payload too large" in err_str
        or "message larger than max" in err_str
        or "413" in err_str
    ):
        return FAILOVER_PAYLOAD_TOO_LARGE, True

    # 8. Timeout
    if (
        "deadline_exceeded" in err_str
        or "timeout" in err_str
        or "timed out" in err_str
        or isinstance(exc, TimeoutError)
    ):
        return FAILOVER_TIMEOUT, True

    # 9. Rate limit (429)
    if "429" in err_str or "too many requests" in err_str or "rate limit" in err_str:
        return FAILOVER_RATE_LIMIT, True

    # 10. Quota / Credits exhausted
    if (
        isinstance(exc, CloudCreditExhaustedError)
        or "resource_exhausted" in err_str
        or "credit" in err_str
        or "quota" in err_str
    ):
        return FAILOVER_QUOTA, False

    # 11. Network / TLS / Socket
    if (
        "unavailable" in err_str
        or "tls" in err_str
        or "ssl" in err_str
        or "certificate" in err_str
        or "connection refused" in err_str
        or "failed to connect" in err_str
        or "no route to host" in err_str
        or "socket" in err_str
        or "connection reset" in err_str
    ):
        return FAILOVER_NETWORK_TLS, True

    # 12. Default Server error
    return FAILOVER_SERVER_ERROR, True


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class CloudTranscriptionError(Exception):
    """Base exception for cloud transcription errors with failover handover diagnostics."""
    def __init__(
        self,
        message: str,
        fallback_reason: str = "server error",
        retries: int = 0,
        retry_wait_seconds: float = 0.0,
        first_error_at: str = "",
        fallback_decided_at: str = "",
        first_error_epoch: float = 0.0,
    ):
        super().__init__(message)
        self.fallback_reason = fallback_reason
        self.retries = retries
        self.retry_wait_seconds = retry_wait_seconds
        self.first_error_at = first_error_at
        self.fallback_decided_at = fallback_decided_at
        self.first_error_epoch = first_error_epoch


class CloudAuthError(CloudTranscriptionError):
    """Non-retryable authentication error (e.g. invalid or revoked API key)."""
    pass


class CloudCreditExhaustedError(CloudTranscriptionError):
    """Non-retryable quota or credit exhaustion error."""
    pass


class CloudRetryableError(CloudTranscriptionError):
    """Temporary failure (timeout, 429 rate limit, 5xx server error)."""
    pass


# ---------------------------------------------------------------------------
# Provider Profiles
# ---------------------------------------------------------------------------

@dataclass
class ProviderProfile:
    id: str
    name: str
    protocol: str              # "riva_grpc" or "openai_http"
    endpoint: str              # e.g. "grpc.nvcf.nvidia.com:443" or base URL
    function_id: str = ""      # for riva_grpc
    model_id: str = ""         # for openai_http
    language: str = "en-US"
    supports_boosting: bool = False
    enabled: bool = True

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "ProviderProfile":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


DEFAULT_PROFILES: Dict[str, ProviderProfile] = {
    "nvidia_parakeet": ProviderProfile(
        id="nvidia_parakeet",
        name="NVIDIA Parakeet TDT 0.6B v2 (English)",
        protocol="riva_grpc",
        endpoint="grpc.nvcf.nvidia.com:443",
        function_id="d3fe9151-442b-4204-a70d-5fcc597fd610",
        language="en-US",
        supports_boosting=True,
        enabled=True,
    ),
    "nvidia_whisper": ProviderProfile(
        id="nvidia_whisper",
        name="NVIDIA Whisper Large v3 (Multilingual)",
        protocol="riva_grpc",
        endpoint="grpc.nvcf.nvidia.com:443",
        function_id="b702f636-f60c-4a3d-a6f4-f3568c13bd7d",
        language="en",
        supports_boosting=True,
        enabled=True,
    ),
    "nemotron_http_template": ProviderProfile(
        id="nemotron_http_template",
        name="NVIDIA Nemotron 3.5 Transcribe (HTTP Template - Coming Soon)",
        protocol="openai_http",
        endpoint="https://integrate.api.nvidia.com/v1/audio/transcriptions",
        model_id="nvidia/nemotron-3.5-transcribe",
        language="en",
        supports_boosting=False,
        enabled=False,  # Disabled template profile for Nemotron / HTTP later
    ),
}


def get_enabled_profiles() -> List[ProviderProfile]:
    """Return list of currently enabled provider profiles."""
    return [p for p in DEFAULT_PROFILES.values() if p.enabled]


def get_profile_by_id(profile_id: str) -> Optional[ProviderProfile]:
    """Retrieve profile by ID or None."""
    return DEFAULT_PROFILES.get(profile_id)


# ---------------------------------------------------------------------------
# API Key Management & Language Mapping
# ---------------------------------------------------------------------------

def get_nvidia_api_key_info(project_root: Optional[str] = None) -> Tuple[str, str, str]:
    """
    Returns (api_key, found_status, source_str)
    where:
      api_key: The string key (never printed or logged)
      found_status: "yes" or "no"
      source_str: "environment", ".env", or "none"
    """
    key = os.environ.get("NVIDIA_API_KEY", "").strip()
    if key:
        return key, "yes", "environment"

    root = project_root or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env_file = os.path.join(root, ".env")
    if os.path.isfile(env_file):
        try:
            with open(env_file, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("NVIDIA_API_KEY="):
                        k = line.split("=", 1)[1].strip().strip('"').strip("'")
                        if k:
                            return k, "yes", ".env"
        except OSError:
            pass
    return "", "no", "none"


def get_nvidia_key_display_status(project_root: Optional[str] = None) -> str:
    """Return 'Key found: yes (source)' or 'Key found: no' for UI display without revealing key."""
    _, found, src = get_nvidia_api_key_info(project_root)
    if found == "yes":
        return f"Key found: yes ({src})"
    return "Key found: no"


def get_nvidia_api_key(project_root: Optional[str] = None) -> str:
    """
    Read NVIDIA_API_KEY from environment or local .env file.
    Never prints or logs the key value.
    """
    key, _, _ = get_nvidia_api_key_info(project_root)
    return key


def map_language_for_profile(language: str, profile_id: str) -> str:
    """
    Ensure the language code matches what the profile endpoint expects.
    e.g. Parakeet TDT v2 expects 'en-US' (or 'en'), Whisper expects 'en'.
    """
    lang = (language or "en").strip()
    if "parakeet" in profile_id.lower():
        if lang.lower() == "en":
            return "en-US"
        return lang
    elif "whisper" in profile_id.lower():
        if lang.lower() == "en-us":
            return "en"
        return lang
    return lang


def save_nvidia_api_key(key: str, project_root: Optional[str] = None) -> None:
    """Save key to git-ignored .env file."""
    root = project_root or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env_file = os.path.join(root, ".env")
    lines = []
    found = False
    if os.path.isfile(env_file):
        try:
            with open(env_file, "r", encoding="utf-8") as f:
                for line in f:
                    if line.strip().startswith("NVIDIA_API_KEY="):
                        lines.append(f"NVIDIA_API_KEY={key}\n")
                        found = True
                    else:
                        lines.append(line)
        except OSError:
            pass
    if not found:
        lines.append(f"NVIDIA_API_KEY={key}\n")
    with open(env_file, "w", encoding="utf-8") as f:
        f.writelines(lines)


# ---------------------------------------------------------------------------
# Segment Builder from Word Timestamps
# ---------------------------------------------------------------------------

@dataclass
class WordInfo:
    word: str
    start_time: float  # in seconds
    end_time: float    # in seconds


def build_segments_from_words(
    words: List[WordInfo],
    max_silence_gap: float = 0.8,
    max_segment_duration: float = 20.0,
) -> List[Dict[str, Any]]:
    """
    Construct natural sentence-like segments from word timestamps.
    Breaks at:
      1. Silence pause >= max_silence_gap (default 0.8s)
      2. Sentence boundary punctuation (., ?, !)
      3. Maximum segment duration (default 20.0s)
    """
    if not words:
        return []

    segments: List[Dict[str, Any]] = []
    cur_words: List[str] = []
    cur_words_info: List[Dict[str, Any]] = []
    seg_start: Optional[float] = None
    last_end: Optional[float] = None

    for w in words:
        w_word = getattr(w, "word", "") if not isinstance(w, dict) else w.get("word", "")
        w_start = getattr(w, "start_time", 0.0) if not isinstance(w, dict) else (w.get("start_time") if "start_time" in w else w.get("start", 0.0))
        w_end = getattr(w, "end_time", 0.0) if not isinstance(w, dict) else (w.get("end_time") if "end_time" in w else w.get("end", 0.0))

        if seg_start is None:
            seg_start = w_start

        # Check break conditions if we already have accumulated words
        should_break = False
        if cur_words and last_end is not None:
            gap = w_start - last_end
            duration = w_end - seg_start
            prev_word = cur_words[-1]

            if gap >= max_silence_gap:
                should_break = True
            elif duration >= max_segment_duration:
                should_break = True
            elif prev_word and prev_word[-1] in (".", "?", "!"):
                should_break = True

        if should_break and cur_words and seg_start is not None and last_end is not None:
            seg_text = " ".join(cur_words).strip()
            if seg_text:
                segments.append({
                    "start": round(seg_start, 3),
                    "end": round(last_end, 3),
                    "text": seg_text,
                    "words": list(cur_words_info),
                })
            cur_words = [w_word]
            cur_words_info = [{"word": w_word, "start": round(w_start, 3), "end": round(w_end, 3)}]
            seg_start = w_start
            last_end = w_end
        else:
            cur_words.append(w_word)
            cur_words_info.append({"word": w_word, "start": round(w_start, 3), "end": round(w_end, 3)})
            last_end = w_end

    # Flush final segment
    if cur_words and seg_start is not None and last_end is not None:
        seg_text = " ".join(cur_words).strip()
        if seg_text:
            segments.append({
                "start": round(seg_start, 3),
                "end": round(last_end, 3),
                "text": seg_text,
                "words": list(cur_words_info),
            })

    return segments


# ---------------------------------------------------------------------------
# Audio Chunking & Splicing
# ---------------------------------------------------------------------------

def get_wav_duration_seconds(wav_path: str) -> float:
    """Return duration of WAV file in seconds."""
    try:
        with wave.open(wav_path, "rb") as wf:
            frames = wf.getnframes()
            rate = wf.getframerate()
            return frames / float(rate) if rate > 0 else 0.0
    except Exception:
        return 0.0


def find_silence_split_points(
    wav_path: str,
    target_chunk_sec: float = 120.0,
    search_window_sec: float = 15.0,
    silence_threshold_amp: int = 400,
    min_silence_sec: float = 0.5,
) -> List[Tuple[float, float]]:
    """
    Plan silence-aware audio chunk intervals [(start_0, end_0), (start_1, end_1), ...].
    For clips longer than target_chunk_sec, looks for pauses >= min_silence_sec in a window
    [target - search_window, target + search_window] around each split boundary.
    If a pause is found, splits cleanly at the midpoint of the pause (0 overlap needed).
    If no pause >= min_silence_sec is found, splits at target_chunk_sec with 1.0s overlap.
    """
    dur = get_wav_duration_seconds(wav_path)
    if dur <= target_chunk_sec:
        return [(0.0, float(round(dur, 3)))]

    try:
        with wave.open(wav_path, "rb") as wf:
            framerate = wf.getframerate()
            n_frames = wf.getnframes()
            raw = wf.readframes(n_frames)

        import numpy as np
        samples = np.frombuffer(raw, dtype=np.int16)
        is_silent = np.abs(samples) < silence_threshold_amp
        min_samples = int(min_silence_sec * framerate)

        chunks: List[Tuple[float, float]] = []
        cur_t = 0.0

        while cur_t + target_chunk_sec < dur:
            win_start_sec = max(cur_t + 10.0, cur_t + target_chunk_sec - search_window_sec)
            win_end_sec = min(dur - 5.0, cur_t + target_chunk_sec + search_window_sec)

            found_split = None
            if win_end_sec > win_start_sec:
                idx_start = int(win_start_sec * framerate)
                idx_end = int(win_end_sec * framerate)
                sub_silent = is_silent[idx_start:idx_end]

                if len(sub_silent) >= min_samples:
                    padded = np.empty(len(sub_silent) + 2, dtype=bool)
                    padded[0] = False
                    padded[-1] = False
                    padded[1:-1] = sub_silent
                    diffs = np.diff(padded.view(np.int8))
                    run_starts = np.where(diffs == 1)[0]
                    run_ends = np.where(diffs == -1)[0]
                    run_lens = run_ends - run_starts
                    valid = run_lens >= min_samples

                    if np.any(valid):
                        v_starts = run_starts[valid]
                        v_ends = run_ends[valid]
                        midpoints = (v_starts + v_ends) / 2.0
                        target_sample_in_sub = (cur_t + target_chunk_sec - win_start_sec) * framerate
                        best_idx = np.argmin(np.abs(midpoints - target_sample_in_sub))
                        split_sample = idx_start + midpoints[best_idx]
                        found_split = float(split_sample / framerate)

            if found_split is not None:
                chunks.append((float(round(cur_t, 3)), float(round(found_split, 3))))
                cur_t = found_split
            else:
                split_sec = min(dur, cur_t + target_chunk_sec)
                chunks.append((float(round(cur_t, 3)), float(round(split_sec, 3))))
                if split_sec >= dur:
                    break
                cur_t = max(0.0, split_sec - 1.0)

        if cur_t < dur:
            chunks.append((float(round(cur_t, 3)), float(round(dur, 3))))

        return chunks

    except Exception:
        # Fallback if numpy not available or reading error occurs
        chunks = []
        t = 0.0
        while t < dur:
            end = min(dur, t + target_chunk_sec)
            chunks.append((float(round(t, 3)), float(round(end, 3))))
            if end >= dur:
                break
            t = max(0.0, end - 1.0)
        return chunks


def extract_audio_chunk(
    ffmpeg_bin: str,
    input_wav: str,
    output_wav: str,
    start_sec: float,
    end_sec: float,
) -> float:
    """
    Extract slice [start_sec, end_sec] from input_wav using ffmpeg.
    Returns duration of the extracted chunk.
    """
    duration = max(0.0, end_sec - start_sec)
    os.makedirs(os.path.dirname(output_wav), exist_ok=True)
    cmd = [
        ffmpeg_bin,
        "-y",
        "-ss", f"{start_sec:.3f}",
        "-t", f"{duration:.3f}",
        "-i", input_wav,
        "-acodec", "pcm_s16le",
        "-ar", "16000",
        "-ac", "1",
        output_wav,
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    return duration


def cut_audio_for_resume(
    ffmpeg_bin: str,
    input_wav: str,
    output_wav: str,
    resume_offset: float,
) -> float:
    """
    Cut audio from max(0.0, resume_offset - 0.5) seconds to end of file.
    Returns the actual start offset (seconds) used for cutting.
    """
    cut_start = max(0.0, resume_offset - 0.5)
    os.makedirs(os.path.dirname(output_wav), exist_ok=True)
    cmd = [
        ffmpeg_bin,
        "-y",
        "-ss", f"{cut_start:.3f}",
        "-i", input_wav,
        "-acodec", "pcm_s16le",
        "-ar", "16000",
        "-ac", "1",
        output_wav,
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    return cut_start


def stitch_segments(
    saved_segments: List[Dict[str, Any]],
    local_segments: List[Dict[str, Any]],
    resume_offset: float,
    cut_offset: float,
) -> List[Dict[str, Any]]:
    """
    Shift local_segments by cut_offset, drop any starting before resume_offset,
    and join to saved_segments in order.
    """
    joined = list(saved_segments)

    for seg in local_segments:
        abs_start = round(seg["start"] + cut_offset, 3)
        abs_end = round(seg["end"] + cut_offset, 3)

        if abs_start < resume_offset:
            if abs_end <= resume_offset:
                continue
            # Clamped start
            abs_start = resume_offset

        seg_copy = dict(seg)
        seg_copy["start"] = abs_start
        seg_copy["end"] = abs_end
        joined.append(seg_copy)

    # Sort by start time
    joined.sort(key=lambda s: s["start"])
    return joined


def make_silence_wav(duration_sec: float = 0.5, framerate: int = 16000) -> bytes:
    """Generate in-memory WAV bytes with valid headers and silence PCM audio."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(framerate)
        wf.writeframes(b"\x00\x00" * int(framerate * duration_sec))
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Cloud Riva Transcription Caller
# ---------------------------------------------------------------------------

def transcribe_audio_chunk_riva(
    audio_bytes: bytes,
    profile: ProviderProfile,
    api_key: str,
    glossary_terms: Optional[List[str]] = None,
    riva_client_module: Any = None,
    timeout_sec: Optional[float] = None,
) -> List[WordInfo]:
    """
    Invoke hosted Riva ASR via gRPC with retries on retryable errors.
    Returns list of WordInfo.
    """
    if not api_key:
        now_str = datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S IST")
        raise CloudAuthError(
            "NVIDIA API Key not provided or found",
            fallback_reason=FAILOVER_KEY_NOT_FOUND,
            retries=0,
            first_error_at=now_str,
            fallback_decided_at=now_str,
            first_error_epoch=time.time(),
        )

    riva_client = riva_client_module
    if riva_client is None:
        try:
            import riva.client as rcli
            riva_client = rcli
        except ImportError as e:
            raise CloudTranscriptionError(f"nvidia-riva-client package not available: {e}")

    auth = riva_client.Auth(
        uri=profile.endpoint,
        use_ssl=True,
        metadata_args=[
            ["function-id", profile.function_id],
            ["authorization", f"Bearer {api_key}"],
        ],
    )
    asr_client = riva_client.ASRService(auth)

    lang_code = map_language_for_profile(profile.language, profile.id)
    config = riva_client.RecognitionConfig(
        encoding=riva_client.AudioEncoding.LINEAR_PCM,
        sample_rate_hertz=16000,
        audio_channel_count=1,
        language_code=lang_code,
        max_alternatives=1,
        enable_automatic_punctuation=True,
        enable_word_time_offsets=True,
    )

    if profile.supports_boosting and glossary_terms:
        # Boost glossary terms
        riva_client.add_word_boosting_to_config(
            config,
            boosted_lm_words=glossary_terms,
            boosted_lm_score=10.0,
        )

    # Scaled timeout: default max(15.0, audio_duration * 2.0)
    if timeout_sec is None:
        audio_dur = len(audio_bytes) / 32000.0  # 16kHz mono 16-bit PCM = 32,000 bytes/sec
        timeout_sec = max(15.0, round(audio_dur * 2.0, 1))

    # Retries for timeouts / transient errors
    max_retries = 3
    backoffs = [2.0, 4.0, 8.0]
    last_err = None
    first_error_at = ""
    first_error_epoch = 0.0
    total_retry_wait_sec = 0.0
    fallback_reason = FAILOVER_SERVER_ERROR

    for attempt in range(max_retries):
        try:
            try:
                response = asr_client.offline_recognize(audio_bytes, config, timeout=timeout_sec)
            except TypeError:
                response = asr_client.offline_recognize(audio_bytes, config)

            words: List[WordInfo] = []
            for res in response.results:
                for alt in res.alternatives:
                    for w in alt.words:
                        words.append(WordInfo(
                            word=w.word,
                            start_time=w.start_time / 1000.0,
                            end_time=w.end_time / 1000.0,
                        ))
            return words

        except Exception as exc:
            t_now = time.time()
            if first_error_epoch == 0.0:
                first_error_epoch = t_now
            last_err = exc
            err_str = str(exc)
            now_str = datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S IST")
            if not first_error_at:
                first_error_at = now_str

            failover_cls, is_ret = classify_cloud_error(exc, err_str)
            fallback_reason = failover_cls

            # Configuration errors and quota exhaustion fall back immediately without retry
            if failover_cls in CONFIGURATION_ERROR_CLASSES or not is_ret or failover_cls == FAILOVER_QUOTA:
                if failover_cls in (FAILOVER_AUTH, FAILOVER_KEY_NOT_FOUND):
                    raise CloudAuthError(
                        f"NVIDIA API Key authorization failed ({failover_cls}): {exc}",
                        fallback_reason=failover_cls,
                        retries=attempt,
                        retry_wait_seconds=total_retry_wait_sec,
                        first_error_at=first_error_at,
                        fallback_decided_at=now_str,
                        first_error_epoch=first_error_epoch,
                    )
                elif failover_cls == FAILOVER_QUOTA:
                    raise CloudCreditExhaustedError(
                        f"NVIDIA quota or free credits exhausted: {exc}",
                        fallback_reason=failover_cls,
                        retries=attempt,
                        retry_wait_seconds=total_retry_wait_sec,
                        first_error_at=first_error_at,
                        fallback_decided_at=now_str,
                        first_error_epoch=first_error_epoch,
                    )
                else:
                    raise CloudTranscriptionError(
                        f"Cloud configuration error ({failover_cls}): {exc}",
                        fallback_reason=failover_cls,
                        retries=attempt,
                        retry_wait_seconds=total_retry_wait_sec,
                        first_error_at=first_error_at,
                        fallback_decided_at=now_str,
                        first_error_epoch=first_error_epoch,
                    )

            # Transient errors: retry with backoff up to max_retries
            if attempt < max_retries - 1:
                wait_s = backoffs[attempt]
                total_retry_wait_sec += wait_s
                time.sleep(wait_s)
            else:
                break

    decided_str = datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S IST")
    raise CloudRetryableError(
        f"Riva gRPC request failed after {max_retries} attempts: {last_err}",
        fallback_reason=fallback_reason,
        retries=max_retries,
        retry_wait_seconds=total_retry_wait_sec,
        first_error_at=first_error_at,
        fallback_decided_at=decided_str,
        first_error_epoch=first_error_epoch,
    )


# ---------------------------------------------------------------------------
# Diagnostics & Probe Helpers
# ---------------------------------------------------------------------------

def test_riva_connection(api_key: str, profile: ProviderProfile) -> Tuple[bool, str]:
    """Test authentication and RPC readiness for given profile."""
    if not api_key:
        return False, "API key is empty."
    try:
        import riva.client as rcli
        auth = rcli.Auth(
            uri=profile.endpoint,
            use_ssl=True,
            metadata_args=[
                ["function-id", profile.function_id],
                ["authorization", f"Bearer {api_key}"],
            ],
        )
        client = rcli.ASRService(auth)
        # 0.5s valid silence WAV payload with explicit LINEAR_PCM encoding
        audio = make_silence_wav(duration_sec=0.5)
        cfg = rcli.RecognitionConfig(
            encoding=rcli.AudioEncoding.LINEAR_PCM,
            sample_rate_hertz=16000,
            audio_channel_count=1,
            language_code=profile.language,
            max_alternatives=1,
        )
        client.offline_recognize(audio, cfg)
        return True, f"Successfully connected to {profile.name} via gRPC."
    except Exception as exc:
        err = str(exc)
        if "PermissionDenied" in err or "Authorization failed" in err:
            return False, "Authentication failed: NVIDIA_API_KEY was rejected (PermissionDenied)."
        return False, f"Connection test failed: {err}"


def fetch_nvidia_speech_models(api_key: str) -> List[Dict[str, Any]]:
    """
    Query NVCF catalog for speech models and check for Nemotron Transcribe.
    Returns list of dicts: {"name": str, "id": str, "is_nemotron_transcribe": bool, "status": str}
    """
    url = "https://api.nvcf.nvidia.com/v2/nvcf/functions"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {api_key}"})
    results = []
    try:
        with urllib.request.urlopen(req, timeout=12) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            functions = data.get("functions", [])

        speech_keywords = ["parakeet", "canary", "whisper", "asr", "riva", "conformer", "transcribe"]
        for fn in functions:
            name = fn.get("name", "")
            name_lower = name.lower()
            if any(kw in name_lower for kw in speech_keywords):
                is_nt = ("nemotron" in name_lower and "transcribe" in name_lower)
                results.append({
                    "name": name,
                    "id": fn.get("id", ""),
                    "status": fn.get("status", ""),
                    "is_nemotron_transcribe": is_nt,
                })
    except Exception as exc:
        results.append({
            "name": f"Error querying NVCF functions: {exc}",
            "id": "",
            "status": "ERROR",
            "is_nemotron_transcribe": False,
        })
    return results


# ---------------------------------------------------------------------------
# Real Test Transcription & Preflight Check (Bundled 5s Sample & Caching)
# ---------------------------------------------------------------------------

@dataclass
class PreflightStageResult:
    stage_name: str
    status: str       # "PASS" or "FAIL"
    duration_s: float
    message: str


@dataclass
class PreflightTestResult:
    success: bool
    stages: List[PreflightStageResult]
    total_duration_s: float
    error_class: Optional[str] = None
    error_message: Optional[str] = None


_PREFLIGHT_CACHE: Dict[str, Tuple[float, PreflightTestResult]] = {}
PREFLIGHT_CACHE_TTL_SEC: float = 600.0  # 10 minutes cache


def get_bundled_5s_sample_wav() -> bytes:
    """Return 5-second 16kHz mono 16-bit PCM WAV audio for preflight testing."""
    res_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "resources", "sample_5s.wav")
    if os.path.isfile(res_path):
        try:
            with open(res_path, "rb") as f:
                return f.read()
        except OSError:
            pass
    return make_silence_wav(duration_sec=5.0)


def run_test_transcription(
    profile: ProviderProfile,
    api_key: Optional[str] = None,
    riva_client_module: Any = None,
) -> PreflightTestResult:
    """
    Run bundled 5-second sample through the real cloud path stage-by-stage with timings.
    Never uploads event audio.
    """
    stages: List[PreflightStageResult] = []
    t_start = time.time()

    # Stage 1: Audio Prep
    s1_t0 = time.time()
    try:
        audio_bytes = get_bundled_5s_sample_wav()
        stages.append(PreflightStageResult(
            stage_name="1. Sample Audio Prep",
            status="PASS",
            duration_s=round(time.time() - s1_t0, 3),
            message=f"Bundled 5s WAV ready ({len(audio_bytes)} bytes, 16kHz mono PCM)",
        ))
    except Exception as e:
        stages.append(PreflightStageResult(
            stage_name="1. Sample Audio Prep",
            status="FAIL",
            duration_s=round(time.time() - s1_t0, 3),
            message=f"Failed to prepare sample audio: {e}",
        ))
        return PreflightTestResult(False, stages, round(time.time() - t_start, 3), FAILOVER_INVALID_ARGUMENT, str(e))

    # Stage 2: Request & Config Build
    s2_t0 = time.time()
    try:
        key = (api_key or get_nvidia_api_key()).strip()
        if not key:
            raise CloudTranscriptionError("NVIDIA API key not provided or found", fallback_reason=FAILOVER_KEY_NOT_FOUND)
        lang_code = map_language_for_profile(profile.language, profile.id)
        stages.append(PreflightStageResult(
            stage_name="2. Request Build",
            status="PASS",
            duration_s=round(time.time() - s2_t0, 3),
            message=f"Profile: {profile.name}, Function: {profile.function_id}, Lang: {lang_code}",
        ))
    except Exception as e:
        cls, _ = classify_cloud_error(e)
        stages.append(PreflightStageResult(
            stage_name="2. Request Build",
            status="FAIL",
            duration_s=round(time.time() - s2_t0, 3),
            message=str(e),
        ))
        return PreflightTestResult(False, stages, round(time.time() - t_start, 3), cls, str(e))

    # Stage 3: TLS Connection & Auth
    s3_t0 = time.time()
    try:
        rcli = riva_client_module
        if rcli is None:
            import riva.client as rcli_mod
            rcli = rcli_mod
        auth = rcli.Auth(
            uri=profile.endpoint,
            use_ssl=True,
            metadata_args=[
                ["function-id", profile.function_id],
                ["authorization", f"Bearer {key}"],
            ],
        )
        asr_client = rcli.ASRService(auth)
        stages.append(PreflightStageResult(
            stage_name="3. Connection & Auth",
            status="PASS",
            duration_s=round(time.time() - s3_t0, 3),
            message=f"gRPC channel initialized to {profile.endpoint} (SSL: True)",
        ))
    except Exception as e:
        cls, _ = classify_cloud_error(e)
        stages.append(PreflightStageResult(
            stage_name="3. Connection & Auth",
            status="FAIL",
            duration_s=round(time.time() - s3_t0, 3),
            message=f"Auth/Connection failed: {e}",
        ))
        return PreflightTestResult(False, stages, round(time.time() - t_start, 3), cls, str(e))

    # Stage 4: Cloud Transcription Call
    s4_t0 = time.time()
    words = []
    try:
        config = rcli.RecognitionConfig(
            encoding=rcli.AudioEncoding.LINEAR_PCM,
            sample_rate_hertz=16000,
            audio_channel_count=1,
            language_code=lang_code,
            max_alternatives=1,
            enable_automatic_punctuation=True,
            enable_word_time_offsets=True,
        )
        try:
            resp = asr_client.offline_recognize(audio_bytes, config, timeout=20.0)
        except TypeError:
            resp = asr_client.offline_recognize(audio_bytes, config)

        for res in resp.results:
            for alt in res.alternatives:
                for w in alt.words:
                    words.append(WordInfo(
                        word=w.word,
                        start_time=w.start_time / 1000.0,
                        end_time=w.end_time / 1000.0,
                    ))

        stages.append(PreflightStageResult(
            stage_name="4. Cloud Transcription Call",
            status="PASS",
            duration_s=round(time.time() - s4_t0, 3),
            message=f"Cloud Riva call succeeded in {time.time() - s4_t0:.2f}s",
        ))
    except Exception as e:
        cls, _ = classify_cloud_error(e)
        stages.append(PreflightStageResult(
            stage_name="4. Cloud Transcription Call",
            status="FAIL",
            duration_s=round(time.time() - s4_t0, 3),
            message=f"Transcription RPC failed: {e}",
        ))
        return PreflightTestResult(False, stages, round(time.time() - t_start, 3), cls, str(e))

    # Stage 5: Response Parsing
    s5_t0 = time.time()
    try:
        segs = build_segments_from_words(words)
        stages.append(PreflightStageResult(
            stage_name="5. Response Parsing",
            status="PASS",
            duration_s=round(time.time() - s5_t0, 3),
            message=f"Parsed {len(words)} words into {len(segs)} segment(s)",
        ))
    except Exception as e:
        stages.append(PreflightStageResult(
            stage_name="5. Response Parsing",
            status="FAIL",
            duration_s=round(time.time() - s5_t0, 3),
            message=f"Segment parse failed: {e}",
        ))
        return PreflightTestResult(False, stages, round(time.time() - t_start, 3), FAILOVER_SERVER_ERROR, str(e))

    total_dur = round(time.time() - t_start, 3)
    return PreflightTestResult(
        success=True,
        stages=stages,
        total_duration_s=total_dur,
    )


def run_cloud_preflight_check(
    profile: ProviderProfile,
    api_key: Optional[str] = None,
    use_cache: bool = True,
    riva_client_module: Any = None,
) -> PreflightTestResult:
    """
    Run preflight test, caching successful/failed results for 10 minutes (600s).
    """
    key = (api_key or get_nvidia_api_key()).strip()
    cache_key = f"{profile.id}:{hashlib.sha256(key.encode()).hexdigest()[:12]}"
    now = time.time()

    if use_cache and cache_key in _PREFLIGHT_CACHE:
        cached_time, cached_res = _PREFLIGHT_CACHE[cache_key]
        if now - cached_time < PREFLIGHT_CACHE_TTL_SEC:
            return cached_res

    res = run_test_transcription(profile, api_key=key, riva_client_module=riva_client_module)
    _PREFLIGHT_CACHE[cache_key] = (now, res)
    return res

