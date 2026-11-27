"""
Transcription Worker for Session Scribe
Runs as an independent subprocess to transcribe audio using Whisper medium on GPU.
Isolates VRAM usage so memory is 100% released on exit or cancellation.
Communicates progress via JSON lines over stdout.

Skip logic:
  Before each clip the worker checks whether a *completed* transcript already
  exists in either scribe\\test or scribe\\transcripts (both passed via job config).
  "Completed" means the file contains the "Transcription Completed:" header line
  and is non-empty — a .tmp file left by a crash is never the final name so it
  is automatically ignored.

  If the only completed copy is in scribe\\test and the current output is
  scribe\\transcripts, the file is copied (original kept) and the clip is skipped.

  Per-clip settings (model / language / glossary_hash) are stored in a single
  state file at scribe\\state\\clip_state.json (Task B).  The old .meta.json
  files that used to sit beside transcripts are migrated to scribe\\state\\old_meta\\
  on the first run after this change.

  If the current settings differ from the recorded settings, the clip is emitted
  as "clip_skipped_different_settings" (still skipped — user must use Force
  re-transcribe from the UI to redo it).

  Clips listed in job["force_clips"] bypass the skip check entirely.

  A speaker-name rename never touches the state file, so it can never cause a
  settings-differ mismatch or trigger re-transcription.
"""

import os
import sys
import json
import time
import hashlib
import argparse
import shutil
import subprocess
import re
from typing import Optional, List, Dict, Any, Tuple
from datetime import datetime, timedelta, timezone

# Allow importing scribe_core when running as a standalone subprocess.
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from scribe_core import clip_state as _clip_state  # noqa: E402
from scribe_core import cloud_engine as _cloud_engine  # noqa: E402
from scribe_core import session_tracker as _session_tracker  # noqa: E402
from scribe_core import usage_csv as _usage_csv  # noqa: E402
from scribe_core.clip_probe import ClipProbe  # noqa: E402
from scribe_core.multi_speaker import (  # noqa: E402
    format_speakers_header,
    format_speaker_line,
    assign_diarization_to_words,
)

IST = timezone(timedelta(hours=5, minutes=30))

# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args():
    parser = argparse.ArgumentParser(description="Session Scribe Transcription Worker")
    parser.add_argument("--job-config", required=True, help="Path to JSON file with job configuration")
    return parser.parse_args()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def resolve_ffmpeg():
    """Resolve ffmpeg binary dynamically from PATH."""
    bin_path = shutil.which("ffmpeg") or shutil.which("ffmpeg.exe")
    if bin_path:
        return bin_path
    candidates = [
        r"C:\ProgramData\chocolatey\bin\ffmpeg.exe",
        r"C:\Users\antho\scoop\shims\ffmpeg.exe"
    ]
    for c in candidates:
        if os.path.exists(c):
            return c
    raise RuntimeError("ffmpeg executable not found on PATH.")


def format_timestamp(dt: datetime) -> str:
    """Format datetime as YYYY-MM-DD HH:MM:SS."""
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def format_offset_timestamp(clip_start: datetime, offset_seconds: float) -> str:
    """Calculates absolute segment timestamp formatted as HH:MM:SS.mmm."""
    seg_dt = clip_start + timedelta(seconds=offset_seconds)
    return seg_dt.strftime("%H:%M:%S") + f".{int(seg_dt.microsecond / 1000):03d}"


def format_duration(seconds: float) -> str:
    """Format duration in seconds to HH:MM:SS."""
    td = timedelta(seconds=int(round(seconds)))
    total_seconds = int(td.total_seconds())
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    secs = total_seconds % 60
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def extract_audio(ffmpeg_bin: str, video_path: str, wav_path: str):
    """Extract mono 16 kHz WAV audio from video using ffmpeg."""
    os.makedirs(os.path.dirname(wav_path), exist_ok=True)
    cmd = [
        ffmpeg_bin,
        "-y",
        "-i", video_path,
        "-vn",
        "-acodec", "pcm_s16le",
        "-ar", "16000",
        "-ac", "1",
        wav_path
    ]
    subprocess.run(cmd, check=True, capture_output=True)


def probe_media_duration(ffmpeg_bin: str, media_path: str) -> float:
    """Safely obtain media duration in seconds via ClipProbe or ffmpeg."""
    try:
        probe = ClipProbe()
        if probe.is_available():
            d = probe.get_duration(media_path)
            if d > 0.0:
                return float(d)
    except Exception:
        pass
    try:
        cmd = [ffmpeg_bin, "-i", media_path]
        res = subprocess.run(cmd, capture_output=True, text=True, errors="ignore")
        m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", res.stderr)
        if m:
            h, mn, s = m.groups()
            return int(h) * 3600 + int(mn) * 60 + float(s)
    except Exception:
        pass
    return 0.0


def extract_audio_slice(
    ffmpeg_bin: str,
    media_path: str,
    output_wav: str,
    start_sec: float = 0.0,
    duration_sec: Optional[float] = None,
) -> float:
    """
    Extract mono 16 kHz WAV audio slice using ffmpeg starting at start_sec.
    Places -ss before -i for fast, accurate input seeking.
    Returns duration of the extracted audio in seconds.
    """
    os.makedirs(os.path.dirname(output_wav), exist_ok=True)
    cmd = [ffmpeg_bin, "-y"]
    if start_sec > 0.0:
        cmd.extend(["-ss", f"{start_sec:.3f}"])
    if duration_sec is not None and duration_sec > 0:
        cmd.extend(["-t", f"{duration_sec:.3f}"])
    cmd.extend([
        "-i", media_path,
        "-vn",
        "-acodec", "pcm_s16le",
        "-ar", "16000",
        "-ac", "1",
        output_wav,
    ])
    subprocess.run(cmd, check=True, capture_output=True)
    return _cloud_engine.get_wav_duration_seconds(output_wav)


def emit_json(data: dict):
    """Write JSON line to stdout and flush."""
    sys.stdout.write(json.dumps(data) + "\n")
    sys.stdout.flush()


def glossary_hash(text: str) -> str:
    """Return first 8 hex chars of the SHA-256 of the glossary string."""
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()[:8]


# ---------------------------------------------------------------------------
# Transcript completion helper
# ---------------------------------------------------------------------------

def is_completed_transcript(path: str) -> bool:
    """
    Return True iff *path* is a finished transcript.
    Criterion: file exists, is non-empty, and contains "Transcription Completed:".
    A .tmp file from a crash is never named {stem}.txt so it is automatically
    excluded (the atomic os.replace never ran for it).
    """
    if not os.path.isfile(path):
        return False
    try:
        size = os.path.getsize(path)
        if size < 50:
            return False
        # Read enough to find the header without loading the whole file.
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            head = f.read(4096)
        return "Transcription Completed:" in head
    except OSError:
        return False


# ---------------------------------------------------------------------------
# Skip-check logic
# ---------------------------------------------------------------------------

def find_completed_transcript(stem: str, output_dir: str, test_dir: str, transcripts_dir: str):
    """
    Search for a completed transcript in the canonical locations.

    Returns (found_path, location_tag) where location_tag is one of:
      "output"       – found in output_dir (already in the right place)
      "test"         – found only in test_dir  (may need copy for full run)
      "transcripts"  – found only in transcripts_dir
      None, None     – not found anywhere
    """
    check_order = []

    # Always check output_dir first (it's the right place for this job).
    if output_dir:
        check_order.append((output_dir, "output"))

    # Then check the other two canonical dirs (avoid duplicates).
    for d, tag in [(test_dir, "test"), (transcripts_dir, "transcripts")]:
        if d and os.path.normcase(os.path.abspath(d)) != os.path.normcase(os.path.abspath(output_dir or "")):
            check_order.append((d, tag))

    for d, tag in check_order:
        p = os.path.join(d, f"{stem}.txt")
        if is_completed_transcript(p):
            return p, tag

    return None, None


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    args = parse_args()
    if not os.path.exists(args.job_config):
        emit_json({"type": "error", "message": f"Job config file not found: {args.job_config}"})
        sys.exit(1)

    with open(args.job_config, "r", encoding="utf-8") as f:
        job = json.load(f)

    event_info    = job.get("event", {})
    clips         = job.get("clips", [])
    output_dir    = job.get("output_dir")
    work_dir      = job.get("work_dir")
    log_dir       = job.get("log_dir")
    language      = job.get("language") or "en"
    initial_prompt = (job.get("initial_prompt") or "").strip() or None
    model_name    = job.get("model", "medium")
    device        = job.get("device", "cuda")

    # Bug 2 additions: cross-dir skip context
    test_dir        = job.get("test_dir", "")
    transcripts_dir = job.get("transcripts_dir", "")
    force_clips     = set(job.get("force_clips", []))   # clip names to force-redo
    ghash           = job.get("glossary_hash", glossary_hash(job.get("initial_prompt") or ""))

    # Task B: state directory (replaces .meta.json sidecars beside transcripts)
    state_dir  = job.get("state_dir", "")
    state_path = os.path.join(state_dir, _clip_state.STATE_FILENAME) if state_dir else ""

    # Cloud Engine configuration
    engine                   = job.get("engine", "local")
    cloud_profile_data       = job.get("cloud_profile", {})
    single_request_limit_sec = float(job.get("single_request_limit_sec", 600.0))
    chunk_duration_sec       = float(job.get("chunk_duration_sec", 120.0))
    checkpoint_every_clip    = bool(job.get("checkpoint_every_clip", False))

    if cloud_profile_data:
        cloud_profile = _cloud_engine.ProviderProfile.from_dict(cloud_profile_data)
    else:
        cloud_profile = _cloud_engine.DEFAULT_PROFILES.get("nvidia_parakeet")

    nvidia_api_key = os.environ.get("NVIDIA_API_KEY") or _cloud_engine.get_nvidia_api_key()
    current_engine = engine
    cloud_configuration_error_occurred = False

    usage_tracker = None
    resolved_paths_map = job.get("resolved_paths") or {}
    if resolved_paths_map.get("scribe"):
        try:
            usage_tracker = _usage_csv.UsageTracker(event_scribe_dir=resolved_paths_map.get("scribe"))
        except Exception:
            pass

    glossary_terms = []
    if initial_prompt:
        glossary_terms = [t.strip() for t in re.split(r"[,;\n\r]+", initial_prompt) if t.strip()]

    os.makedirs(output_dir, exist_ok=True)
    os.makedirs(work_dir, exist_ok=True)
    if log_dir:
        os.makedirs(log_dir, exist_ok=True)
    if state_dir:
        os.makedirs(state_dir, exist_ok=True)

    ffmpeg_bin = resolve_ffmpeg()
    resolved = job.get("resolved_paths") or {}

    emit_json({
        "type": "init",
        "total_clips": len(clips),
        "output_dir": output_dir,
        "language": language,
        "initial_prompt": initial_prompt,
        "resolved_paths": resolved,
    })

    # Log setup
    log_file_path    = os.path.join(log_dir or output_dir, "transcription.log")
    low_conf_file_path = os.path.join(output_dir, "low_confidence_report.txt")

    def log(msg: str):
        timestamp = datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S IST")
        with open(log_file_path, "a", encoding="utf-8") as lf:
            lf.write(f"[{timestamp}] {msg}\n")

    log(f"--- Transcription Run Started. Total Clips: {len(clips)}, Model: {model_name}, Language: {language} ---")
    log("Resolved paths:")
    log(f"  Event root: {resolved.get('event_root', '')}")
    log(f"  data: {resolved.get('data', '')}")
    log(f"  scribe: {resolved.get('scribe', '')}")
    log(f"  manifest: {resolved.get('manifest', '')}")
    log(f"  output_dir: {output_dir}")
    log(f"  test_dir: {test_dir}")
    log(f"  transcripts_dir: {transcripts_dir}")
    log(f"  state_path: {state_path}")
    log(f"  glossary_hash: {ghash}")

    # Task B: one-time migration of legacy .meta.json files.
    if state_path:
        old_meta_dir = os.path.join(state_dir, _clip_state.OLD_META_SUBDIR)
        scan_dirs = [d for d in [output_dir, test_dir, transcripts_dir] if d]
        migrated = _clip_state.migrate_meta_json(scan_dirs, state_path, old_meta_dir)
        if migrated:
            log(f"Migrated {migrated} legacy .meta.json file(s) to {old_meta_dir}")

    # Pre-run summary (counts): scan skip status before loading the model.
    pre_done_output  = 0
    pre_done_test    = 0
    pre_to_transcribe = 0
    for clip in clips:
        stem, _ = os.path.splitext(clip.get("name", ""))
        name = clip.get("name", "")
        if name in force_clips:
            pre_to_transcribe += 1
            continue
        _path, loc = find_completed_transcript(stem, output_dir, test_dir, transcripts_dir)
        if loc == "output":
            pre_done_output += 1
        elif loc == "test":
            pre_done_test += 1
        elif loc == "transcripts":
            pre_done_output += 1  # already in transcripts, counts as done
        else:
            pre_to_transcribe += 1

    summary_line = (
        f"{len(clips)} clips: "
        f"{pre_done_output} done, "
        f"{pre_done_test} done (test only — will copy), "
        f"{pre_to_transcribe} to transcribe"
    )
    emit_json({"type": "run_summary", "message": summary_line})
    log(f"Pre-run summary: {summary_line}")

    # Model loading helper (lazy for cloud, eager for local)
    import torch
    _local_model = None

    def get_local_whisper_model():
        nonlocal _local_model
        if _local_model is None:
            t_model_start = time.time()
            emit_json({"type": "status", "message": f"Loading Whisper model '{model_name}' onto {device}..."})
            import whisper
            try:
                _local_model = whisper.load_model(model_name, device=device)
                model_load_time = time.time() - t_model_start
                emit_json({
                    "type": "model_loaded",
                    "model": model_name,
                    "device": device,
                    "load_time": round(model_load_time, 2)
                })
                log(f"Whisper model '{model_name}' loaded in {model_load_time:.2f}s on {device}.")
            except Exception as e:
                emit_json({"type": "error", "message": f"Failed to load Whisper model: {e}"})
                log(f"ERROR: Failed to load model: {e}")
                raise
        return _local_model

    if current_engine == "local":
        try:
            get_local_whisper_model()
        except Exception:
            sys.exit(1)
    else:
        log(f"Engine configured as '{current_engine}' (Profile: {cloud_profile.name if cloud_profile else 'None'}, "
            f"Single limit: {single_request_limit_sec}s, Chunk: {chunk_duration_sec}s, "
            f"Checkpoint every clip: {checkpoint_every_clip}). Whisper medium will be loaded lazily if fallback is needed.")

    overall_start_time      = datetime.now(IST)
    total_audio_seconds     = 0.0
    total_transcribe_seconds = 0.0
    processed_count  = 0
    skipped_count    = 0
    failed_count     = 0
    low_confidence_items = []
    all_failovers = []

    try:
        for idx, clip in enumerate(clips, 1):
            clip_path   = clip["path"]
            clip_name   = clip["name"]
            speaker     = clip.get("speaker", "Speaker")
            start_time_iso = clip.get("start_time")
            try:
                clip_start_dt = datetime.fromisoformat(start_time_iso)
            except Exception:
                clip_start_dt = datetime.now(IST)

            stem, _ = os.path.splitext(clip_name)
            transcript_txt_path = os.path.join(output_dir, f"{stem}.txt")

            # ------------------------------------------------------------------
            # Skip check (Bug 2 fix) — uses state file, not .meta.json sidecar
            # ------------------------------------------------------------------
            forced = clip_name in force_clips
            if not forced:
                found_path, found_loc = find_completed_transcript(
                    stem, output_dir, test_dir, transcripts_dir
                )
                if found_path is not None:
                    # Check state file for settings mismatch.
                    diff = (
                        _clip_state.settings_differ(state_path, stem, model_name, language, ghash, engine=current_engine)
                        if state_path
                        else False
                    )

                    if diff:
                        # Still skip — user must use Force re-transcribe in UI.
                        skipped_count += 1
                        rec = _clip_state.read_clip(state_path, stem) if state_path else {}
                        emit_json({
                            "type": "clip_skipped_different_settings",
                            "index": idx,
                            "total": len(clips),
                            "clip": clip_name,
                            "speaker": speaker,
                            "found_in": found_loc,
                            "sidecar_model": rec.get("model"),
                            "sidecar_glossary_hash": rec.get("glossary_hash"),
                            "current_glossary_hash": ghash,
                        })
                        log(f"Clip {idx}/{len(clips)} '{clip_name}' skipped — different settings "
                            f"(state model={rec.get('model')}, ghash={rec.get('glossary_hash')} "
                            f"vs current ghash={ghash}).")
                        continue

                    # Settings match (or no state record yet).
                    if found_loc != "output":
                        # Found in test dir but output is transcripts (or vice versa).
                        # Copy to output_dir so the full output folder is complete.
                        dest = transcript_txt_path
                        if not os.path.exists(dest):
                            shutil.copy2(found_path, dest)
                            log(f"Clip {idx}/{len(clips)} '{clip_name}': copied from {found_loc} → output_dir.")

                    skipped_count += 1
                    emit_json({
                        "type": "clip_skipped",
                        "index": idx,
                        "total": len(clips),
                        "clip": clip_name,
                        "speaker": speaker,
                        "reason": "skipped, already done",
                        "found_in": found_loc,
                    })
                    log(f"Clip {idx}/{len(clips)} '{clip_name}' skipped, already done (found in {found_loc}).")
                    continue

            # ------------------------------------------------------------------
            # Transcribe
            # ------------------------------------------------------------------
            emit_json({
                "type": "clip_start",
                "index": idx,
                "total": len(clips),
                "clip": clip_name,
                "speaker": speaker,
                "forced": forced,
            })
            log(f"Starting Clip {idx}/{len(clips)}: '{clip_name}' (Speaker: {speaker}, forced={forced}, Engine: {current_engine})")

            wav_path = os.path.join(work_dir, f"{stem}_temp.wav")
            clip_transcribe_start = datetime.now(IST)
            t0 = time.time()
            clip_failover_info = None

            try:
                # Query session_tracker for resume time
                resume_time = _session_tracker.get_resume_time(clip_path, state_dir)
                if forced:
                    _session_tracker.reset_video_state(clip_path, state_dir)
                    resume_time = 0.0

                # Probe media duration
                audio_dur = probe_media_duration(ffmpeg_bin, clip_path)
                tracker_rec = _session_tracker.get_video_state(clip_path, state_dir) or {}
                if audio_dur <= 0.0 and tracker_rec.get("total_duration", 0.0) > 0.0:
                    audio_dur = float(tracker_rec["total_duration"])

                # Fallback duration measurement if probe not available
                if audio_dur <= 0.0:
                    extract_audio(ffmpeg_bin, clip_path, wav_path)
                    audio_dur = _cloud_engine.get_wav_duration_seconds(wav_path)

                _session_tracker.init_video_state(clip_path, total_duration=audio_dur, state_dir=state_dir)

                # Recover any previously saved chunks
                saved_segments = []
                if resume_time > 0.0:
                    for ch in tracker_rec.get("chunks", []):
                        if "segments" in ch and ch["segments"]:
                            saved_segments.extend(ch["segments"])
                        elif ch.get("text"):
                            saved_segments.append({
                                "start": ch.get("start", 0.0),
                                "end": ch.get("end", ch.get("start", 0.0)),
                                "text": ch.get("text", "").strip(),
                            })
                    res_ts = format_offset_timestamp(clip_start_dt, resume_time)
                    log(f"Clip '{clip_name}': mid-point checkpoint found. Resuming from {res_ts} ({resume_time:.2f}s) with {len(saved_segments)} saved segments.")

                # Slicing in discrete windows (default 30s chunks)
                window_size = float(job.get("chunk_duration_sec", 30.0))
                cur_pos = resume_time
                all_segments = list(saved_segments)
                used_engine = "local"
                used_provider = "local"
                used_model_id = model_name

                while (audio_dur <= 0.0 and cur_pos == 0.0) or (cur_pos < audio_dur):
                    w_end = min(cur_pos + window_size, audio_dur) if audio_dur > 0.0 else (cur_pos + window_size)
                    w_dur = w_end - cur_pos
                    if w_dur < 0.1:
                        break

                    chunk_wav = os.path.join(work_dir, f"{stem}_chunk_{int(cur_pos)}.wav")
                    try:
                        # Extract audio slice with ffmpeg starting at -ss cur_pos
                        slice_dur = extract_audio_slice(
                            ffmpeg_bin, clip_path, chunk_wav, start_sec=cur_pos, duration_sec=w_dur
                        )
                        if slice_dur <= 0.01:
                            break

                        chunk_segs = []
                        chunk_provider = "local"

                        if current_engine == "cloud":
                            try:
                                with open(chunk_wav, "rb") as cf:
                                    audio_bytes = cf.read()
                                t_base = float(job.get("timeout_base", 15.0))
                                t_factor = float(job.get("timeout_factor", 2.0))
                                t_scaled = max(t_base, round(slice_dur * t_factor, 1))
                                log(f"Cloud request: slice {cur_pos:.1f}s-{cur_pos+slice_dur:.1f}s ({slice_dur:.1f}s), timeout={t_scaled:.1f}s (base={t_base:.1f}s, factor={t_factor:.1f})")
                                words = _cloud_engine.transcribe_audio_chunk_riva(
                                    audio_bytes=audio_bytes,
                                    profile=cloud_profile,
                                    api_key=nvidia_api_key,
                                    glossary_terms=glossary_terms,
                                    timeout_sec=t_scaled,
                                )
                                for w in words:
                                    w.start_time += cur_pos
                                    w.end_time += cur_pos
                                chunk_segs = _cloud_engine.build_segments_from_words(words)
                                chunk_provider = cloud_profile.id if cloud_profile else "nvidia_cloud"
                                used_engine = "cloud"
                                used_provider = chunk_provider
                                used_model_id = cloud_profile.function_id if cloud_profile else ""
                            except Exception as cloud_err:
                                t_fallback_decided = time.time()
                                decided_str = datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S IST")
                                cloud_err_str = str(cloud_err)

                                # Extract structured failover metrics from exception
                                fallback_reason = getattr(cloud_err, "fallback_reason", None)
                                if not fallback_reason:
                                    fallback_reason, is_ret = _cloud_engine.classify_cloud_error(cloud_err, cloud_err_str)
                                else:
                                    is_ret = (fallback_reason not in _cloud_engine.CONFIGURATION_ERROR_CLASSES and fallback_reason != _cloud_engine.FAILOVER_QUOTA)

                                retries = getattr(cloud_err, "retries", 3 if is_ret else 0)
                                retry_wait_seconds = getattr(cloud_err, "retry_wait_seconds", 0.0)
                                first_error_at = getattr(cloud_err, "first_error_at", decided_str)

                                # Configuration errors permanently switch off cloud for this run
                                if fallback_reason in _cloud_engine.CONFIGURATION_ERROR_CLASSES:
                                    cloud_configuration_error_occurred = True

                                resume_offset_str = format_offset_timestamp(clip_start_dt, cur_pos)
                                short_err = cloud_err_str.split("\n")[0][:80]
                                msg_notice = f"Cloud failed: {fallback_reason} — {short_err}, continuing locally from {resume_offset_str}"

                                log(f"[FALLBACK] {msg_notice} (Reason: {fallback_reason}, retries: {retries}, wait: {retry_wait_seconds:.1f}s)")
                                current_engine = "local"
                                log("Switched engine to local Whisper medium for remainder of this run.")

                                # Measure local model loading time
                                t_load_start = time.time()
                                local_model = get_local_whisper_model()
                                local_model_load_seconds = round(time.time() - t_load_start, 2)

                                # Mark timestamp when local decoding begins
                                t_local_started = time.time()
                                local_started_at = datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S IST")
                                t_first_error = getattr(cloud_err, "first_error_epoch", None)
                                if not t_first_error or t_first_error <= 0.0:
                                    t_first_error = t_fallback_decided
                                failover_time_sec = max(0.0, round(t_local_started - t_first_error, 1))

                                clip_failover_info = {
                                    "clip": clip_name,
                                    "first_error_at": first_error_at,
                                    "fallback_decided_at": decided_str,
                                    "local_started_at": local_started_at,
                                    "fallback_reason": fallback_reason,
                                    "error_class": fallback_reason,
                                    "message": msg_notice,
                                    "error": cloud_err_str,
                                    "retries": retries,
                                    "retry_wait_seconds": round(retry_wait_seconds, 2),
                                    "local_model_load_seconds": local_model_load_seconds,
                                    "resume_offset": round(cur_pos, 3),
                                    "resume_offset_str": resume_offset_str,
                                    "failover_time_sec": failover_time_sec,
                                }
                                all_failovers.append(clip_failover_info)

                                emit_json({
                                    "type": "fallback",
                                    "clip": clip_name,
                                    "failover_reason": fallback_reason,
                                    "error_class": fallback_reason,
                                    "message": msg_notice,
                                    "error": cloud_err_str,
                                    "resume_offset": round(cur_pos, 3),
                                    "resume_offset_str": resume_offset_str,
                                    "failover": clip_failover_info,
                                })

                        if current_engine == "local":
                            local_model = get_local_whisper_model()
                            transcribe_kwargs = {
                                "fp16": (device == "cuda"),
                                "word_timestamps": True,
                            }
                            if language:
                                transcribe_kwargs["language"] = language
                            if initial_prompt:
                                transcribe_kwargs["initial_prompt"] = initial_prompt

                            res_local = local_model.transcribe(chunk_wav, **transcribe_kwargs)
                            raw_segs = res_local.get("segments", [])
                            chunk_segs = []
                            for s in raw_segs:
                                s_words = []
                                if "words" in s and s["words"]:
                                    for w in s["words"]:
                                        s_words.append({
                                            "start": round(cur_pos + float(w.get("start", s["start"])), 3),
                                            "end": round(cur_pos + float(w.get("end", s["end"])), 3),
                                            "word": str(w.get("word", "")).strip(),
                                        })
                                seg_dict = {
                                    "start": round(cur_pos + float(s["start"]), 3),
                                    "end": round(cur_pos + float(s["end"]), 3),
                                    "text": s["text"].strip(),
                                    "avg_logprob": s.get("avg_logprob", 0.0),
                                    "no_speech_prob": s.get("no_speech_prob", 0.0),
                                    "compression_ratio": s.get("compression_ratio", 1.0),
                                }
                                if s_words:
                                    seg_dict["words"] = s_words
                                chunk_segs.append(seg_dict)
                            chunk_provider = "local"
                            used_engine = "local (fallback)" if (engine == "cloud") else "local"
                            used_provider = "local"
                            used_model_id = model_name

                        actual_new_ts = round(cur_pos + slice_dur, 3)
                        chunk_text = " ".join(s["text"].strip() for s in chunk_segs if s.get("text")).strip()
                        chunk_data = {
                            "start": round(cur_pos, 3),
                            "end": actual_new_ts,
                            "text": chunk_text,
                            "provider": chunk_provider,
                            "segments": chunk_segs,
                        }
                        _session_tracker.save_chunk_progress(
                            video_path=clip_path,
                            chunk_data=chunk_data,
                            new_timestamp=actual_new_ts,
                            state_dir=state_dir,
                            total_duration=audio_dur,
                        )
                        all_segments.extend(chunk_segs)
                        cur_pos = actual_new_ts

                        if state_path and cur_pos < audio_dur:
                            _clip_state.save_checkpoint(state_path, stem, {
                                "engine": used_engine,
                                "provider": used_provider,
                                "model_id": used_model_id,
                                "completed_segments": all_segments,
                                "resume_offset": cur_pos,
                            })

                    finally:
                        if os.path.exists(chunk_wav):
                            try:
                                os.remove(chunk_wav)
                            except OSError:
                                pass

                segments = all_segments
                if state_path:
                    _clip_state.clear_checkpoint(state_path, stem)

                t1 = time.time()
                clip_transcribe_end = datetime.now(IST)

                transcribe_dur_sec = t1 - t0
                total_transcribe_seconds += transcribe_dur_sec
                _session_tracker.finalize_transcription(
                    clip_path, state_dir=state_dir, processing_time_sec=round(transcribe_dur_sec, 2)
                )

                if not audio_dur and segments:
                    audio_dur = segments[-1]["end"]
                total_audio_seconds += audio_dur

                speed_factor = (audio_dur / transcribe_dur_sec) if transcribe_dur_sec > 0 else 0.0

                # 2.5 Multi-speaker diarization check (Task D)
                roster = clip.get("roster", [])
                is_multi_speaker = (clip.get("speaker_mode") == "panel") or len(roster) > 1

                diarized_segments = None
                diarization_dur_sec = 0.0
                identification_dur_sec = 0.0
                if is_multi_speaker:
                    # 4GB VRAM Guard: unload Whisper from GPU before running pyannote
                    if _local_model is not None:
                        log("[VRAM GUARD] Unloading Whisper model from GPU before running pyannote diarization.")
                        del _local_model
                        _local_model = None
                        import gc
                        gc.collect()
                        if torch.cuda.is_available():
                            torch.cuda.empty_cache()

                    hf_token = os.environ.get("HF_TOKEN") or ""
                    if hf_token:
                        diar_t0 = time.monotonic()
                        try:
                            log(f"[DIARIZATION] Running local pyannote diarization on '{wav_path}'...")
                            from pyannote.audio import Pipeline
                            pipeline = Pipeline.from_pretrained("pyannote/speaker-diarization-3.1", use_auth_token=hf_token)
                            if torch.cuda.is_available():
                                pipeline.to(torch.device("cuda"))
                            diar_result = pipeline(wav_path)
                            turns = []
                            for turn, _, spk_label in diar_result.itertracks(yield_label=True):
                                turns.append({"start": turn.start, "end": turn.end, "speaker": spk_label})
                            del pipeline
                            import gc
                            gc.collect()
                            if torch.cuda.is_available():
                                torch.cuda.empty_cache()

                            # Extract or approximate word timestamps for alignment
                            words = []
                            for s_item in segments:
                                if "words" in s_item and s_item["words"]:
                                    for w in s_item["words"]:
                                        words.append({
                                            "start": float(w.get("start", s_item["start"])),
                                            "end": float(w.get("end", s_item["end"])),
                                            "word": w.get("word", ""),
                                        })
                                else:
                                    s_words = s_item["text"].strip().split()
                                    if s_words:
                                        dur_w = (s_item["end"] - s_item["start"]) / len(s_words)
                                        for wi, w in enumerate(s_words):
                                            words.append({
                                                "start": s_item["start"] + wi * dur_w,
                                                "end": s_item["start"] + (wi + 1) * dur_w,
                                                "word": w,
                                            })

                            diarized_segments = assign_diarization_to_words(words, turns, roster)
                            diarization_dur_sec = round(time.monotonic() - diar_t0, 2)
                            log(f"[DIARIZATION] Aligned {len(diarized_segments)} multi-speaker turns in {diarization_dur_sec:.1f}s.")

                            # Persist cluster centroids and snippet time ranges to scribe\state\clusters\<clip>.json
                            ident_t0 = time.monotonic()
                            if state_dir:
                                try:
                                    from scribe_core.identification_engine import extract_and_save_diarized_clusters
                                    saved_cluster_path = extract_and_save_diarized_clusters(
                                        wav_path=wav_path,
                                        turns=turns,
                                        diarized_segments=diarized_segments,
                                        state_dir=state_dir,
                                        clip_name=clip_name,
                                    )
                                    identification_dur_sec = round(time.monotonic() - ident_t0, 2)
                                    if saved_cluster_path:
                                        log(f"[DIARIZATION] Persisted voice cluster centroids in {identification_dur_sec:.1f}s to '{saved_cluster_path}'.")
                                except Exception as c_err:
                                    log(f"[DIARIZATION WARNING] Could not persist cluster centroids: {c_err}")
                        except Exception as diar_err:
                            log(f"[DIARIZATION WARNING] Pyannote diarization failed: {diar_err}. Falling back to default session speaker.")

                # 3. Format Transcript File (matching user's exact format)
                transcription_duration_str = format_duration(transcribe_dur_sec)
                started_str   = format_timestamp(clip_transcribe_start) + " IST"
                completed_str = format_timestamp(clip_transcribe_end)   + " IST"
                clip_start_str = format_timestamp(clip_start_dt)

                lines = []
                lines.append("=== EVENT DETAILS ===")
                lines.append(f"Event Name: {event_info.get('name', '')}")
                lines.append(f"Date: {event_info.get('date', '')}")
                if event_info.get("organiser"):
                    lines.append(f"Organiser: {event_info.get('organiser')}")
                if event_info.get("venue"):
                    lines.append(f"Venue: {event_info.get('venue')}")
                if event_info.get("sponsors"):
                    lines.append(f"Sponsors: {event_info.get('sponsors')}")

                lines.append("")
                lines.append("=== TRANSCRIPT ===")
                lines.append(f"Video File: {clip_name}")
                lines.append(f"Start Time: {clip_start_str}")
                if is_multi_speaker:
                    lines.append(format_speakers_header(roster))
                else:
                    lines.append(f"Speaker: {speaker}")
                lines.append(f"Transcription Duration: {transcription_duration_str}")
                if diarization_dur_sec > 0.0:
                    lines.append(f"Diarization Duration: {format_duration(diarization_dur_sec)}")
                if identification_dur_sec > 0.0:
                    lines.append(f"Identification Duration: {format_duration(identification_dur_sec)}")
                lines.append(f"Transcription Started: {started_str}")
                lines.append(f"Transcription Completed: {completed_str}")
                lines.append("")

                if is_multi_speaker and diarized_segments:
                    for seg in diarized_segments:
                        spk_name = seg.get("speaker", speaker)
                        role = seg.get("role", "")
                        seg_start_str = format_offset_timestamp(clip_start_dt, seg["start"])
                        seg_end_str   = format_offset_timestamp(clip_start_dt, seg["end"])
                        seg_text      = seg.get("text", "").strip()
                        is_ovlp       = seg.get("is_overlapping", False)
                        ovlp_spks     = seg.get("overlapping_speakers")

                        line_str = format_speaker_line(
                            speaker_name=spk_name,
                            role=role,
                            start_sec=seg["start"],
                            end_sec=seg["end"],
                            text=seg_text,
                            is_multi_speaker=True,
                            is_overlapping=is_ovlp,
                            overlapping_speakers=ovlp_spks,
                        )
                        lines.append(line_str + "\n")
                else:
                    for seg in segments:
                        seg_start_str = format_offset_timestamp(clip_start_dt, seg["start"])
                        seg_end_str   = format_offset_timestamp(clip_start_dt, seg["end"])
                        seg_text      = seg["text"].strip()
                        lines.append(f"[{speaker}] [{seg_start_str} - {seg_end_str}] {seg_text}\n")

                    # Confidence signals check
                    if segments:
                        last_seg = segments[-1]
                        avg_logprob       = last_seg.get("avg_logprob", 0.0)
                        no_speech_prob    = last_seg.get("no_speech_prob", 0.0)
                        compression_ratio = last_seg.get("compression_ratio", 1.0)
                    else:
                        avg_logprob       = 0.0
                        no_speech_prob    = 1.0
                        compression_ratio = 1.0

                    is_doubtful = (
                        avg_logprob < -1.0 or
                        no_speech_prob > 0.6 or
                        compression_ratio > 2.4
                    )
                    if is_doubtful:
                        reasons = []
                        if avg_logprob < -1.0:
                            reasons.append(f"low logprob ({avg_logprob:.2f})")
                        if no_speech_prob > 0.6:
                            reasons.append(f"high no-speech prob ({no_speech_prob:.2f})")
                        if compression_ratio > 2.4:
                            reasons.append(f"high repetition ratio ({compression_ratio:.2f})")

                        low_conf_entry = (
                            f"Clip: {clip_name} | Time: [{seg_start_str} - {seg_end_str}] | "
                            f"Speaker: {speaker} | Flags: {', '.join(reasons)}\n"
                            f"  Text: {seg_text}\n"
                        )
                        low_confidence_items.append(low_conf_entry)

                # 4. Write transcript atomically (tmp → final rename).
                #    A cancelled run never leaves a file that looks complete.
                temp_out = transcript_txt_path + ".tmp"
                with open(temp_out, "w", encoding="utf-8") as out_f:
                    out_f.write("\n".join(lines))
                os.replace(temp_out, transcript_txt_path)

                # 5. Write settings to the state file (Task B: NOT beside the transcript).
                if state_path:
                    _clip_state.write_clip(
                        state_path,
                        stem=stem,
                        model=model_name,
                        language=language,
                        glossary_hash=ghash,
                        transcribed_at=completed_str,
                        engine=used_engine,
                        provider=used_provider,
                        model_id=used_model_id,
                        processing_time_sec=round(transcribe_dur_sec, 2),
                        diarization_time_sec=diarization_dur_sec,
                        identification_time_sec=identification_dur_sec,
                        failover_info=clip_failover_info,
                    )

                _session_tracker.finalize_transcription(
                    video_path=clip_path,
                    state_dir=state_dir,
                    processing_time_sec=round(transcribe_dur_sec, 2),
                    failover_info=clip_failover_info,
                )

                if usage_tracker:
                    try:
                        att_no = usage_tracker.get_next_attempt_number(clip_name)
                        att_row = {
                            "attempt_id": f"att_{datetime.now(IST).strftime('%Y%m%d%H%M%S')}_{idx}",
                            "run_id": job.get("run_id", "run_batch"),
                            "event": job.get("event", {}).get("name", ""),
                            "clip": clip_name,
                            "attempt_no_for_clip": att_no,
                            "started_at": format_timestamp(clip_start_dt) + " IST",
                            "ended_at": completed_str,
                            "time_taken_s": round(transcribe_dur_sec, 2),
                            "time_taken_hms": format_duration(transcribe_dur_sec),
                            "trigger": "batch",
                            "engine_start": cloud_profile.name if engine == "cloud" else "local",
                            "engine_end": used_engine,
                            "profile_model": cloud_profile.id if engine == "cloud" else "medium",
                            "outcome": "fallback" if clip_failover_info else "completed",
                            "audio_s_clip": round(audio_dur, 2),
                            "audio_s_sent": round(audio_dur, 2),
                            "requests": 1,
                            "retries": clip_failover_info.get("retries", 0) if clip_failover_info else 0,
                            "failover_first_error_at": clip_failover_info.get("first_error_at", "") if clip_failover_info else "",
                            "failover_local_started_at": clip_failover_info.get("local_started_at", "") if clip_failover_info else "",
                            "failover_time_s": clip_failover_info.get("failover_time_sec", 0.0) if clip_failover_info else 0.0,
                            "failover_reason": clip_failover_info.get("fallback_reason", "") if clip_failover_info else "",
                            "resume_offset_s": clip_failover_info.get("resume_offset", 0.0) if clip_failover_info else 0.0,
                            "local_decode_s": round(transcribe_dur_sec, 2),
                            "model_load_s": clip_failover_info.get("local_model_load_seconds", 0.0) if clip_failover_info else 0.0,
                            "archived_transcript_path": "",
                            "notes": clip_failover_info.get("error", "") if clip_failover_info else "",
                        }
                        usage_tracker.log_attempt(att_row)
                    except Exception:
                        pass

                processed_count += 1
                emit_json({
                    "type": "clip_complete",
                    "index": idx,
                    "total": len(clips),
                    "clip": clip_name,
                    "speaker": speaker,
                    "audio_duration": round(audio_dur, 2),
                    "transcribe_time": round(transcribe_dur_sec, 2),
                    "speed_factor": round(speed_factor, 1),
                    "segments_count": len(segments),
                    "output_file": transcript_txt_path,
                    "engine": used_engine,
                    "failover": clip_failover_info,
                })
                log(f"Completed Clip {idx}/{len(clips)}: '{clip_name}' via {used_engine} in {transcribe_dur_sec:.2f}s "
                    f"(Audio: {audio_dur:.1f}s, Speed: {speed_factor:.1f}x)")

            except Exception as clip_err:
                _session_tracker.mark_video_failed(clip_path, str(clip_err), state_dir=state_dir)
                failed_count += 1
                emit_json({
                    "type": "clip_error",
                    "index": idx,
                    "total": len(clips),
                    "clip": clip_name,
                    "error": str(clip_err)
                })
                log(f"ERROR on Clip {idx}/{len(clips)} '{clip_name}': {clip_err}")
            finally:
                # Always delete temp audio file immediately
                if os.path.exists(wav_path):
                    try:
                        os.remove(wav_path)
                    except Exception:
                        pass

    finally:
        # Release model from GPU if loaded
        if _local_model is not None:
            del _local_model
            import gc
            gc.collect()
            if device == "cuda":
                torch.cuda.empty_cache()

    overall_end_time  = datetime.now(IST)
    total_run_seconds = (overall_end_time - overall_start_time).total_seconds()
    overall_speed     = (total_audio_seconds / total_transcribe_seconds) if total_transcribe_seconds > 0 else 0.0

    # Write low confidence report if any
    if low_confidence_items:
        with open(low_conf_file_path, "w", encoding="utf-8") as lcf:
            lcf.write("=== LOW CONFIDENCE SEGMENTS REPORT ===\n")
            lcf.write(f"Generated: {format_timestamp(overall_end_time)} IST\n")
            lcf.write(f"Total Flagged Segments: {len(low_confidence_items)}\n\n")
            lcf.writelines(low_confidence_items)

    summary = {
        "type": "finished",
        "processed_count": processed_count,
        "skipped_count": skipped_count,
        "failed_count": failed_count,
        "total_clips": len(clips),
        "total_audio_duration_sec": round(total_audio_seconds, 2),
        "total_transcribe_time_sec": round(total_transcribe_seconds, 2),
        "overall_speed_factor": round(overall_speed, 1),
        "started_at": format_timestamp(overall_start_time) + " IST",
        "ended_at": format_timestamp(overall_end_time) + " IST",
        "total_run_time_str": format_duration(total_run_seconds),
        "low_confidence_flags_count": len(low_confidence_items),
        "low_confidence_report": low_conf_file_path if low_confidence_items else None,
        "fallbacks_count": len(all_failovers),
    }

    if all_failovers:
        avg_fo = sum(f["failover_time_sec"] for f in all_failovers) / len(all_failovers)
        longest_fo_rec = max(all_failovers, key=lambda f: f["failover_time_sec"])
        summary["avg_failover_sec"] = round(avg_fo, 1)
        summary["longest_failover_sec"] = longest_fo_rec["failover_time_sec"]
        summary["longest_failover_clip"] = longest_fo_rec.get("clip", "")
        log(f"Fallbacks Summary: {len(all_failovers)} clip(s) failed over to local Whisper medium.")
        log(f"  Average failover time: {avg_fo:.1f}s")
        log(f"  Longest failover time: {longest_fo_rec['failover_time_sec']:.1f}s on '{longest_fo_rec.get('clip', '')}'")
    else:
        log("Fallbacks Summary: 0 clips failed over.")

    emit_json(summary)
    log(f"--- Run Finished: {processed_count} processed, {skipped_count} skipped, "
        f"{failed_count} failed in {format_duration(total_run_seconds)} "
        f"(Speed: {overall_speed:.1f}x real-time) ---")


if __name__ == "__main__":
    main()
