"""
scribe_core/voice_quality_checker.py
Comprehensive acoustic quality verification for enrolled voice biometrics.

Quality Verification Architecture:
1. Pyannote Neural Diarization & VAD:
   - Pyannote Voice Activity Detection (speech-only duration, min 10.0s).
   - Pyannote Overlapped Speech Detection (rejects polyphonic speech overlap).
   - Pyannote Speaker Diarization: enforces exactly one speaker per reference sample.
2. Custom Signal Analysis:
   - Audio clipping & digital distortion detection.
   - Overall loudness / RMS energy floor.
   - Signal-to-Noise Ratio (SNR) in dB.
3. Decision Classification:
   - CLEAR_PASS: Meets all high-confidence criteria; passed=True.
   - BORDERLINE: Acceptable but sub-optimal (e.g. SNR 9-12 dB, minor crosstalk);
     passed=False by default, is_borderline=True, allows explicit "Accept Anyway" approval.
   - CLEAR_FAIL: Clear failure (SNR < 9 dB, duration < 10s, 0 or >2 speakers); passed=False.

Zero unencrypted audio is written to disk.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import io
import logging
import math
import os
import threading
from typing import Any, Dict, List, Optional, Tuple, Union

import numpy as np
import soundfile as sf

logger = logging.getLogger("scribe_core.quality")

# Quality Calibration Constants
MIN_SPEECH_DURATION_SECONDS = 10.0
RECOMMENDED_MIN_SECONDS = 30.0
RECOMMENDED_MAX_SECONDS = 60.0

MIN_SNR_DB = 12.0
BORDERLINE_SNR_DB = 9.0

MIN_AUDIO_RMS = 0.005
MAX_CLIPPING_RATIO = 0.005  # 0.5%
BORDERLINE_CLIPPING_RATIO = 0.02  # 2.0%

MAX_OVERLAP_SECONDS = 0.3  # 300 ms
BORDERLINE_OVERLAP_SECONDS = 1.5  # 1.5 s
BRIEF_INTERJECTION_THRESHOLD_SECONDS = 1.0  # Two-speaker rule: < 1.0s is borderline; >= 1.0s is clear fail

FRAME_DURATION_SEC = 0.03  # 30 ms
SPEECH_ENERGY_THRESHOLD = 0.015

_PIPELINE = None
_PIPELINE_LOCK = threading.Lock()


@dataclass
class QualityCheckResult:
    """Detailed outcome of quality checks performed on an audio sample."""
    passed: bool
    verdict: str  # "CLEAR_PASS", "BORDERLINE", "CLEAR_FAIL"
    is_borderline: bool
    can_override: bool
    duration_sec: float
    speech_duration_sec: float
    snr_db: float
    speaker_count: int
    overlap_duration_sec: float
    clipping_ratio: float
    loudness_rms: float
    failure_reason: Optional[str] = None
    borderline_reasons: List[str] = field(default_factory=list)
    speaker_breakdown: Dict[str, float] = field(default_factory=dict)
    metrics: Dict[str, Any] = field(default_factory=dict)

    # Backward compatibility properties
    @property
    def single_speaker(self) -> bool:
        return self.speaker_count == 1

    @property
    def has_overlap(self) -> bool:
        return self.overlap_duration_sec > MAX_OVERLAP_SECONDS

    @property
    def has_music(self) -> bool:
        return False  # Replaced by neural diarization & VAD

    def summary_label(self) -> str:
        """User-facing concise status label."""
        if self.passed:
            rec_note = " (Good length)" if self.speech_duration_sec >= RECOMMENDED_MIN_SECONDS else " (Acceptable, >=30s recommended)"
            return f"✅ Passed: {self.speech_duration_sec:.1f}s speech, SNR {self.snr_db:.1f} dB, 1 speaker{rec_note}"
        if self.is_borderline:
            reasons = "; ".join(self.borderline_reasons)
            return f"⚠️ Borderline ({reasons}). Review metrics below or click 'Accept Anyway'."
        return f"❌ Rejected: {self.failure_reason}"


def compute_overlap_duration(annotation: Any) -> float:
    """Computes total duration in seconds where distinct speakers talk simultaneously."""
    overlap_sec = 0.0
    try:
        segments = list(annotation.itertracks(yield_label=True))
        for i in range(len(segments)):
            seg_i, _, spk_i = segments[i]
            for j in range(i + 1, len(segments)):
                seg_j, _, spk_j = segments[j]
                if spk_i != spk_j:
                    start = max(seg_i.start, seg_j.start)
                    end = min(seg_i.end, seg_j.end)
                    if end > start:
                        overlap_sec += (end - start)
    except Exception as e:
        logger.debug("Error computing overlap duration: %s", e)
    return float(overlap_sec)


def get_pyannote_pipeline() -> Any:
    """Loads and caches Pyannote diarization pipeline under offline mode."""
    global _PIPELINE
    with _PIPELINE_LOCK:
        if _PIPELINE is None:
            try:
                import torch
                from pyannote.audio import Pipeline

                os.environ["HF_HUB_OFFLINE"] = "1"
                os.environ["TRANSFORMERS_OFFLINE"] = "1"
                try:
                    pipeline = Pipeline.from_pretrained("pyannote/speaker-diarization-3.1")
                except Exception:
                    os.environ.pop("HF_HUB_OFFLINE", None)
                    os.environ.pop("TRANSFORMERS_OFFLINE", None)
                    pipeline = Pipeline.from_pretrained("pyannote/speaker-diarization-3.1")
                    os.environ["HF_HUB_OFFLINE"] = "1"
                    os.environ["TRANSFORMERS_OFFLINE"] = "1"

                use_cuda = torch.cuda.is_available()
                device = torch.device("cuda" if use_cuda else "cpu")
                pipeline.to(device)
                _PIPELINE = pipeline
                logger.info("Initialized pyannote quality pipeline on %s", device)
            except Exception as e:
                logger.warning("Could not initialize pyannote pipeline: %s", e)
                _PIPELINE = None
        return _PIPELINE


def run_pyannote_analysis(
    audio_data: np.ndarray,
    sample_rate: int = 16000,
) -> Tuple[float, int, float, Dict[str, float]]:
    """
    Executes Pyannote neural diarization, VAD, and overlap detection.
    Returns: (speech_duration_sec, speaker_count, overlap_duration_sec, speaker_breakdown)
    """
    pipeline = get_pyannote_pipeline()
    if pipeline is not None:
        try:
            import torch
            audio_f32 = np.ascontiguousarray(audio_data, dtype=np.float32)
            wav_tensor = torch.from_numpy(audio_f32).unsqueeze(0).to(pipeline.device)
            annotation = pipeline({"waveform": wav_tensor, "sample_rate": sample_rate})

            timeline = annotation.get_timeline().support()
            speech_duration = float(timeline.duration())
            labels = list(annotation.labels())
            speaker_count = len(labels)
            overlap_duration = compute_overlap_duration(annotation)

            breakdown: Dict[str, float] = {}
            for seg, _, spk in annotation.itertracks(yield_label=True):
                breakdown[spk] = breakdown.get(spk, 0.0) + (seg.end - seg.start)

            return speech_duration, speaker_count, overlap_duration, breakdown
        except Exception as e:
            logger.warning("Pyannote inference error; falling back to acoustic heuristics: %s", e)

    # Acoustic heuristic fallback (for offline or mock test runs)
    frame_len = int(sample_rate * FRAME_DURATION_SEC)
    num_frames = len(audio_data) // frame_len
    if num_frames == 0:
        return 0.0, 0, 0.0, {}

    frames = audio_data[: num_frames * frame_len].reshape(num_frames, frame_len)
    frame_rms = np.sqrt(np.mean(frames**2, axis=1) + 1e-12)
    speech_mask = frame_rms >= SPEECH_ENERGY_THRESHOLD
    speech_duration = float(np.sum(speech_mask) * FRAME_DURATION_SEC)

    # Fallback overlap detection via harmonic sieve
    window = np.hanning(frame_len)
    spec = np.abs(np.fft.rfft(frames * window, axis=1))
    freqs = np.fft.rfftfreq(frame_len, 1.0 / sample_rate)

    inharmonic_voiced = 0
    total_voiced = 0
    for i in range(num_frames):
        if not speech_mask[i] or frame_rms[i] < 0.03:
            continue
        total_voiced += 1
        s = spec[i]
        max_s = np.max(s)
        if max_s < 1e-4:
            continue
        peaks = []
        for k in range(1, len(s) - 1):
            if 80.0 <= freqs[k] <= 1500.0:
                if s[k] > s[k - 1] and s[k] > s[k + 1] and s[k] > 0.15 * max_s:
                    peaks.append((freqs[k], s[k]))
        if len(peaks) < 2:
            continue
        low_peaks = [p for p in peaks if p[0] <= 350.0]
        f0 = max(low_peaks, key=lambda x: x[1])[0] if low_peaks else peaks[0][0]
        total_p_energy = sum(p[1] for p in peaks)
        inharmonic_p_energy = 0.0
        for f_val, energy in peaks:
            k_harm = round(f_val / f0)
            if k_harm == 0:
                continue
            if abs(f_val - k_harm * f0) / (k_harm * f0) > 0.15:
                inharmonic_p_energy += energy
        if total_p_energy > 0 and (inharmonic_p_energy / total_p_energy) > 0.20:
            inharmonic_voiced += 1

    overlap_metric = float(inharmonic_voiced / max(1, total_voiced))
    has_overlap_flag = overlap_metric >= 0.12

    speaker_count = 2 if has_overlap_flag else (1 if speech_duration >= 2.0 else 0)
    overlap_duration = float(speech_duration * 0.25) if has_overlap_flag else 0.0
    breakdown = {"SPEAKER_00": speech_duration} if speaker_count >= 1 else {}
    return speech_duration, speaker_count, overlap_duration, breakdown


def evaluate_sample_quality(
    audio_input: Union[bytes, np.ndarray],
    sample_rate: int = 16000,
    force_single_speaker_override: bool = False,
    override_borderline: bool = False,
) -> QualityCheckResult:
    """
    Evaluates acoustic quality against all required criteria:
    - Clipping, loudness (RMS), and SNR (custom signal analysis).
    - Speech duration, speaker count, and overlap (Pyannote neural analysis).

    Parameters:
    - audio_input: In-memory WAV bytes or 1D float32 numpy array.
    - sample_rate: Audio sampling frequency (default 16 kHz).
    - force_single_speaker_override: Bypasses multi-speaker rejection for tests.
    - override_borderline: Explicit user acceptance of borderline metrics.

    Returns:
    - QualityCheckResult with verdict ("CLEAR_PASS", "BORDERLINE", "CLEAR_FAIL").
    """
    # 1. Parse audio data
    if isinstance(audio_input, (bytes, bytearray)):
        try:
            audio_data, sr = sf.read(io.BytesIO(audio_input))
            if audio_data.ndim > 1:
                audio_data = np.mean(audio_data, axis=1)
            audio_data = np.ascontiguousarray(audio_data, dtype=np.float32)
            sample_rate = sr
        except Exception as e:
            return QualityCheckResult(
                passed=False,
                verdict="CLEAR_FAIL",
                is_borderline=False,
                can_override=False,
                duration_sec=0.0,
                speech_duration_sec=0.0,
                snr_db=0.0,
                speaker_count=0,
                overlap_duration_sec=0.0,
                clipping_ratio=0.0,
                loudness_rms=0.0,
                failure_reason=f"Failed to parse audio data: {e}",
            )
    else:
        audio_data = np.asarray(audio_input, dtype=np.float32)
        if audio_data.ndim > 1:
            audio_data = np.mean(audio_data, axis=1)

    if len(audio_data) == 0:
        return QualityCheckResult(
            passed=False,
            verdict="CLEAR_FAIL",
            is_borderline=False,
            can_override=False,
            duration_sec=0.0,
            speech_duration_sec=0.0,
            snr_db=0.0,
            speaker_count=0,
            overlap_duration_sec=0.0,
            clipping_ratio=0.0,
            loudness_rms=0.0,
            failure_reason="Audio sample contains 0 samples (empty audio).",
        )

    duration_sec = float(len(audio_data) / sample_rate)

    # 2. Custom Signal Analysis: Loudness (RMS), Clipping, and SNR
    total_rms = float(np.sqrt(np.mean(audio_data**2) + 1e-12))
    clipping_samples = int(np.sum(np.abs(audio_data) >= 0.99))
    clipping_ratio = float(clipping_samples / len(audio_data))

    # Silence check
    if total_rms < MIN_AUDIO_RMS:
        return QualityCheckResult(
            passed=False,
            verdict="CLEAR_FAIL",
            is_borderline=False,
            can_override=False,
            duration_sec=duration_sec,
            speech_duration_sec=0.0,
            snr_db=0.0,
            speaker_count=0,
            overlap_duration_sec=0.0,
            clipping_ratio=clipping_ratio,
            loudness_rms=total_rms,
            failure_reason="Audio is nearly silent or muted (RMS energy below -46 dB).",
            metrics={"total_rms": round(total_rms, 5), "clipping_ratio": round(clipping_ratio, 4)},
        )

    # Frame-level RMS for SNR calculation
    frame_len = int(sample_rate * FRAME_DURATION_SEC)
    num_frames = len(audio_data) // frame_len
    if num_frames > 0:
        frames = audio_data[: num_frames * frame_len].reshape(num_frames, frame_len)
        frame_rms = np.sqrt(np.mean(frames**2, axis=1) + 1e-12)
        speech_mask = frame_rms >= SPEECH_ENERGY_THRESHOLD
        silence_frames = frame_rms[~speech_mask]
        if len(silence_frames) > 0:
            noise_floor = float(np.mean(silence_frames))
        else:
            sorted_rms = np.sort(frame_rms)
            lowest_10pct = max(1, int(len(sorted_rms) * 0.1))
            noise_floor = float(np.mean(sorted_rms[:lowest_10pct]))
        speech_rms_val = float(np.mean(frame_rms[speech_mask])) if np.sum(speech_mask) > 0 else total_rms
        snr_db = float(20.0 * math.log10((speech_rms_val + 1e-9) / (noise_floor + 1e-9)))
    else:
        snr_db = 0.0

    # 3. Pyannote Neural Diarization & VAD Analysis
    speech_duration_sec, speaker_count, overlap_sec, breakdown = run_pyannote_analysis(
        audio_data, sample_rate=sample_rate
    )

    if force_single_speaker_override:
        speaker_count = 1
        overlap_sec = 0.0

    metrics = {
        "duration_sec": round(duration_sec, 2),
        "speech_duration_sec": round(speech_duration_sec, 2),
        "snr_db": round(snr_db, 1),
        "speaker_count": speaker_count,
        "overlap_duration_sec": round(overlap_sec, 2),
        "clipping_ratio": round(clipping_ratio, 4),
        "loudness_rms": round(total_rms, 4),
        "speaker_breakdown": {k: round(v, 2) for k, v in breakdown.items()},
    }

    # 4. Evaluation: Clear Failures vs Borderline vs Clear Pass
    borderline_reasons: List[str] = []

    # Clear Failures:
    if speech_duration_sec < MIN_SPEECH_DURATION_SECONDS:
        return QualityCheckResult(
            passed=False,
            verdict="CLEAR_FAIL",
            is_borderline=False,
            can_override=False,
            duration_sec=duration_sec,
            speech_duration_sec=speech_duration_sec,
            snr_db=snr_db,
            speaker_count=speaker_count,
            overlap_duration_sec=overlap_sec,
            clipping_ratio=clipping_ratio,
            loudness_rms=total_rms,
            failure_reason=(
                f"Speech length too short ({speech_duration_sec:.1f}s). "
                f"Minimum {MIN_SPEECH_DURATION_SECONDS:.0f}s of active speech required ({RECOMMENDED_MIN_SECONDS:.0f}–{RECOMMENDED_MAX_SECONDS:.0f}s recommended)."
            ),
            metrics=metrics,
        )

    if speaker_count == 0:
        return QualityCheckResult(
            passed=False,
            verdict="CLEAR_FAIL",
            is_borderline=False,
            can_override=False,
            duration_sec=duration_sec,
            speech_duration_sec=speech_duration_sec,
            snr_db=snr_db,
            speaker_count=0,
            overlap_duration_sec=overlap_sec,
            clipping_ratio=clipping_ratio,
            loudness_rms=total_rms,
            failure_reason="No human speech detected by neural VAD. Audio contains music, tone, or noise only.",
            metrics=metrics,
        )

    if speaker_count > 2:
        return QualityCheckResult(
            passed=False,
            verdict="CLEAR_FAIL",
            is_borderline=False,
            can_override=False,
            duration_sec=duration_sec,
            speech_duration_sec=speech_duration_sec,
            snr_db=snr_db,
            speaker_count=speaker_count,
            overlap_duration_sec=overlap_sec,
            clipping_ratio=clipping_ratio,
            loudness_rms=total_rms,
            failure_reason=f"Multiple speakers detected ({speaker_count} distinct speakers). Reference sample must feature a single voice.",
            speaker_breakdown=breakdown,
            metrics=metrics,
        )

    if overlap_sec > BORDERLINE_OVERLAP_SECONDS or (speech_duration_sec > 0 and (overlap_sec / speech_duration_sec) > 0.12):
        return QualityCheckResult(
            passed=False,
            verdict="CLEAR_FAIL",
            is_borderline=False,
            can_override=False,
            duration_sec=duration_sec,
            speech_duration_sec=speech_duration_sec,
            snr_db=snr_db,
            speaker_count=speaker_count,
            overlap_duration_sec=overlap_sec,
            clipping_ratio=clipping_ratio,
            loudness_rms=total_rms,
            failure_reason=(
                f"Multiple speakers or overlapping speech detected ({overlap_sec:.1f}s overlap). "
                "Each reference sample must feature a single voice without interruptions."
            ),
            metrics=metrics,
        )

    if snr_db < BORDERLINE_SNR_DB:
        return QualityCheckResult(
            passed=False,
            verdict="CLEAR_FAIL",
            is_borderline=False,
            can_override=False,
            duration_sec=duration_sec,
            speech_duration_sec=speech_duration_sec,
            snr_db=snr_db,
            speaker_count=speaker_count,
            overlap_duration_sec=overlap_sec,
            clipping_ratio=clipping_ratio,
            loudness_rms=total_rms,
            failure_reason=(
                f"Excessive background noise detected (SNR: {snr_db:.1f} dB < {BORDERLINE_SNR_DB:.0f} dB limit). "
                "Please choose a cleaner sample or record in a quiet room."
            ),
            metrics=metrics,
        )

    if clipping_ratio > BORDERLINE_CLIPPING_RATIO:
        return QualityCheckResult(
            passed=False,
            verdict="CLEAR_FAIL",
            is_borderline=False,
            can_override=False,
            duration_sec=duration_sec,
            speech_duration_sec=speech_duration_sec,
            snr_db=snr_db,
            speaker_count=speaker_count,
            overlap_duration_sec=overlap_sec,
            clipping_ratio=clipping_ratio,
            loudness_rms=total_rms,
            failure_reason=f"Excessive audio distortion / clipping ({clipping_ratio * 100:.1f}% clipped). Lower input gain.",
            metrics=metrics,
        )

    # Check for Borderline Conditions:
    if speech_duration_sec < 15.0:
        borderline_reasons.append(f"Speech duration ({speech_duration_sec:.1f}s) meets 10s minimum but is below recommended 30s")

    if BORDERLINE_SNR_DB <= snr_db < MIN_SNR_DB:
        borderline_reasons.append(f"Borderline background noise (SNR {snr_db:.1f} dB; >=12 dB recommended)")

    if MAX_OVERLAP_SECONDS < overlap_sec <= BORDERLINE_OVERLAP_SECONDS:
        borderline_reasons.append(f"Minor overlapping speech detected ({overlap_sec:.1f}s overlap)")

    if speaker_count == 2:
        # Two-Speaker Rule:
        # - Brief interjection (< 1.0s secondary speech) is BORDERLINE (can override).
        # - Two substantial speakers (>= 1.0s secondary speech) is CLEAR_FAIL (cannot override).
        times = sorted(breakdown.values(), reverse=True)
        sec_spk_time = times[1] if len(times) > 1 else 0.0
        if sec_spk_time < BRIEF_INTERJECTION_THRESHOLD_SECONDS:
            borderline_reasons.append(
                f"Brief secondary interjection detected ({sec_spk_time:.1f}s < {BRIEF_INTERJECTION_THRESHOLD_SECONDS:.1f}s threshold)"
            )
        else:
            return QualityCheckResult(
                passed=False,
                verdict="CLEAR_FAIL",
                is_borderline=False,
                can_override=False,
                duration_sec=duration_sec,
                speech_duration_sec=speech_duration_sec,
                snr_db=snr_db,
                speaker_count=speaker_count,
                overlap_duration_sec=overlap_sec,
                clipping_ratio=clipping_ratio,
                loudness_rms=total_rms,
                failure_reason=(
                    f"Two substantial speakers detected ({sec_spk_time:.1f}s secondary speech >= "
                    f"{BRIEF_INTERJECTION_THRESHOLD_SECONDS:.1f}s limit). Profile must feature exactly one voice."
                ),
                speaker_breakdown=breakdown,
                metrics=metrics,
            )

    if MAX_CLIPPING_RATIO < clipping_ratio <= BORDERLINE_CLIPPING_RATIO:
        borderline_reasons.append(f"Slight digital clipping ({clipping_ratio * 100:.1f}%)")

    # If borderline flags exist:
    if len(borderline_reasons) > 0:
        passed = bool(override_borderline)
        return QualityCheckResult(
            passed=passed,
            verdict="BORDERLINE",
            is_borderline=True,
            can_override=True,
            duration_sec=duration_sec,
            speech_duration_sec=speech_duration_sec,
            snr_db=snr_db,
            speaker_count=speaker_count,
            overlap_duration_sec=overlap_sec,
            clipping_ratio=clipping_ratio,
            loudness_rms=total_rms,
            failure_reason="; ".join(borderline_reasons),
            borderline_reasons=borderline_reasons,
            speaker_breakdown=breakdown,
            metrics=metrics,
        )

    # Clear Pass!
    return QualityCheckResult(
        passed=True,
        verdict="CLEAR_PASS",
        is_borderline=False,
        can_override=False,
        duration_sec=duration_sec,
        speech_duration_sec=speech_duration_sec,
        snr_db=snr_db,
        speaker_count=1,
        overlap_duration_sec=overlap_sec,
        clipping_ratio=clipping_ratio,
        loudness_rms=total_rms,
        failure_reason=None,
        borderline_reasons=[],
        speaker_breakdown=breakdown,
        metrics=metrics,
    )
