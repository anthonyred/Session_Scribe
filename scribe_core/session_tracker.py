"""
scribe_core/session_tracker.py

Lightweight, zero-dependency session state and mid-point checkpoint tracker for Session Scribe.
Uses standard Python json with atomic disk writes (.tmp -> os.replace).

Storage locations:
  - Per-event workspace: <event root>\\scribe\\state\\session_state.json
  - Fallback / standalone: session_state.json in application root or state_dir.

Tracks per video:
  - file_path: Canonical path of the video.
  - total_duration: Total length in seconds.
  - completed_up_to: Last successfully processed timestamp in seconds (e.g. 70.0).
  - status: "pending" | "in_progress" | "completed" | "failed".
  - chunks: List of transcribed slices [{start, end, text, provider}].
  - full_transcript: Accumulated transcript text.
"""

from __future__ import annotations

import os
import json
import time
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Any

IST = timezone(timedelta(hours=5, minutes=30))
DEFAULT_STATE_FILENAME = "session_state.json"


def _now_str() -> str:
    return datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S IST")


def normalize_video_key(video_path: str) -> str:
    """Return normalized case-insensitive canonical path string for dictionary key."""
    if not video_path:
        return ""
    return os.path.normcase(os.path.abspath(os.path.normpath(video_path)))


def resolve_state_file(video_path: Optional[str] = None, state_dir: Optional[str] = None) -> str:
    """
    Determine the canonical session_state.json file path:
      1. If state_dir is given and is a directory or filename.
      2. If video_path is inside an event folder containing scribe\\state.
      3. Fallback: session_state.json in current working directory.
    """
    if state_dir:
        st_clean = os.path.abspath(state_dir)
        if os.path.isdir(st_clean) or not st_clean.lower().endswith(".json"):
            return os.path.join(st_clean, DEFAULT_STATE_FILENAME)
        return st_clean

    if video_path:
        abs_v = os.path.abspath(video_path)
        # Search upward for event folder containing 'scribe'
        cur = os.path.dirname(abs_v)
        while cur and cur != os.path.dirname(cur):
            scribe_dir = os.path.join(cur, "scribe")
            if os.path.isdir(scribe_dir):
                st_dir = os.path.join(scribe_dir, "state")
                os.makedirs(st_dir, exist_ok=True)
                return os.path.join(st_dir, DEFAULT_STATE_FILENAME)
            cur = os.path.dirname(cur)

        # 2. Check if session_state.json exists in video's folder or parent
        v_dir = os.path.dirname(abs_v)
        candidate = os.path.join(v_dir, DEFAULT_STATE_FILENAME)
        if os.path.exists(candidate):
            return candidate

    # Fallback to local cwd
    return os.path.join(os.getcwd(), DEFAULT_STATE_FILENAME)


def _load_raw_state(state_file: str) -> dict:
    """Load session state dict or initialize empty structure."""
    if not os.path.isfile(state_file):
        return {"version": "1.0", "videos": {}}
    try:
        with open(state_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {"version": "1.0", "videos": {}}
        if not isinstance(data.get("videos"), dict):
            data["videos"] = {}
        return data
    except Exception:
        return {"version": "1.0", "videos": {}}


def _save_raw_state(state_file: str, data: dict) -> None:
    """Atomically write data to state_file via temporary file replace."""
    os.makedirs(os.path.dirname(os.path.abspath(state_file)), exist_ok=True)
    tmp_file = state_file + ".tmp"
    with open(tmp_file, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp_file, state_file)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def init_video_state(
    video_path: str,
    total_duration: float = 0.0,
    state_dir: Optional[str] = None,
) -> dict:
    """
    Initialize or return existing tracking record for video_path.
    Status defaults to 'pending'.
    """
    state_file = resolve_state_file(video_path, state_dir)
    data = _load_raw_state(state_file)
    key = normalize_video_key(video_path)

    existing = data["videos"].get(key)
    if existing:
        if total_duration > 0.0 and existing.get("total_duration", 0.0) <= 0.0:
            existing["total_duration"] = round(float(total_duration), 3)
            _save_raw_state(state_file, data)
        return existing

    record = {
        "file_path": os.path.abspath(video_path),
        "total_duration": round(float(total_duration), 3),
        "completed_up_to": 0.0,
        "status": "pending",
        "chunks": [],
        "full_transcript": "",
        "updated_at": _now_str(),
        "error": None,
    }
    data["videos"][key] = record
    _save_raw_state(state_file, data)
    return record


def get_resume_time(video_path: str, state_dir: Optional[str] = None) -> float:
    """
    Return completed_up_to timestamp in seconds, or 0.0 if not tracked / new.
    """
    state_file = resolve_state_file(video_path, state_dir)
    data = _load_raw_state(state_file)
    key = normalize_video_key(video_path)
    rec = data["videos"].get(key)
    if not rec:
        return 0.0
    return float(rec.get("completed_up_to", 0.0))


def get_video_state(video_path: str, state_dir: Optional[str] = None) -> Optional[dict]:
    """Retrieve tracking record dictionary for video_path or None."""
    state_file = resolve_state_file(video_path, state_dir)
    data = _load_raw_state(state_file)
    key = normalize_video_key(video_path)
    return data["videos"].get(key)


def get_completed_chunks(video_path: str, state_dir: Optional[str] = None) -> List[dict]:
    """Retrieve list of completed chunks for video_path."""
    rec = get_video_state(video_path, state_dir)
    return list(rec.get("chunks", [])) if rec else []


def get_full_transcript(video_path: str, state_dir: Optional[str] = None) -> str:
    """Retrieve accumulated full transcript for video_path."""
    rec = get_video_state(video_path, state_dir)
    return str(rec.get("full_transcript", "")) if rec else ""


def save_chunk_progress(
    video_path: str,
    chunk_data: dict,
    new_timestamp: float,
    state_dir: Optional[str] = None,
    total_duration: Optional[float] = None,
) -> None:
    """
    Atomically update local state after successfully transcribing an audio chunk:
      - Appends chunk_data [{start, end, text, provider}] to chunks
      - Updates completed_up_to = max(current, new_timestamp)
      - Sets status to 'in_progress'
      - Appends text to full_transcript
    """
    state_file = resolve_state_file(video_path, state_dir)
    data = _load_raw_state(state_file)
    key = normalize_video_key(video_path)

    rec = data["videos"].get(key)
    if not rec:
        rec = {
            "file_path": os.path.abspath(video_path),
            "total_duration": round(float(total_duration or 0.0), 3),
            "completed_up_to": 0.0,
            "status": "in_progress",
            "chunks": [],
            "full_transcript": "",
            "updated_at": _now_str(),
            "error": None,
        }
        data["videos"][key] = rec

    if total_duration and total_duration > 0.0:
        rec["total_duration"] = round(float(total_duration), 3)

    # Clean chunk representation
    clean_chunk = {
        "start": round(float(chunk_data.get("start", 0.0)), 3),
        "end": round(float(chunk_data.get("end", new_timestamp)), 3),
        "text": (chunk_data.get("text") or "").strip(),
        "provider": chunk_data.get("provider", "unknown"),
    }
    if "segments" in chunk_data and isinstance(chunk_data["segments"], list):
        clean_chunk["segments"] = chunk_data["segments"]
    rec["chunks"].append(clean_chunk)

    prev_completed = float(rec.get("completed_up_to", 0.0))
    rec["completed_up_to"] = round(max(prev_completed, float(new_timestamp)), 3)
    rec["status"] = "in_progress"
    rec["updated_at"] = _now_str()
    rec["error"] = None

    # Rebuild accumulated full transcript from all chunk texts
    all_texts = [c.get("text", "").strip() for c in rec["chunks"] if c.get("text", "").strip()]
    rec["full_transcript"] = "\n".join(all_texts)

    _save_raw_state(state_file, data)


def finalize_transcription(
    video_path: str,
    state_dir: Optional[str] = None,
    full_transcript: Optional[str] = None,
    processing_time_sec: Optional[float] = None,
    failover_info: Optional[dict] = None,
) -> None:
    """
    Mark video as 'completed' and merge chunk transcripts.
    """
    state_file = resolve_state_file(video_path, state_dir)
    data = _load_raw_state(state_file)
    key = normalize_video_key(video_path)

    rec = data["videos"].get(key)
    if not rec:
        rec = {
            "file_path": os.path.abspath(video_path),
            "total_duration": 0.0,
            "completed_up_to": 0.0,
            "status": "completed",
            "chunks": [],
            "full_transcript": full_transcript or "",
            "updated_at": _now_str(),
            "error": None,
        }
        if processing_time_sec is not None:
            rec["processing_time_sec"] = round(float(processing_time_sec), 2)
        if failover_info is not None:
            rec["failover"] = failover_info
        data["videos"][key] = rec
    else:
        rec["status"] = "completed"
        if processing_time_sec is not None:
            rec["processing_time_sec"] = round(float(processing_time_sec), 2)
        if failover_info is not None:
            rec["failover"] = failover_info
        if full_transcript is not None:
            rec["full_transcript"] = full_transcript
        elif not rec.get("full_transcript") and rec.get("chunks"):
            all_texts = [c.get("text", "").strip() for c in rec["chunks"] if c.get("text", "").strip()]
            rec["full_transcript"] = "\n".join(all_texts)

        tot_dur = float(rec.get("total_duration", 0.0))
        if tot_dur > 0.0:
            rec["completed_up_to"] = tot_dur
        rec["updated_at"] = _now_str()
        rec["error"] = None

    _save_raw_state(state_file, data)


def mark_video_failed(
    video_path: str,
    error_msg: str,
    state_dir: Optional[str] = None,
) -> None:
    """Mark video status as 'failed' with error explanation."""
    state_file = resolve_state_file(video_path, state_dir)
    data = _load_raw_state(state_file)
    key = normalize_video_key(video_path)

    rec = data["videos"].get(key)
    if not rec:
        rec = {
            "file_path": os.path.abspath(video_path),
            "total_duration": 0.0,
            "completed_up_to": 0.0,
            "status": "failed",
            "chunks": [],
            "full_transcript": "",
            "updated_at": _now_str(),
            "error": error_msg,
        }
        data["videos"][key] = rec
    else:
        rec["status"] = "failed"
        rec["error"] = str(error_msg)
        rec["updated_at"] = _now_str()

    _save_raw_state(state_file, data)


def reset_video_state(video_path: str, state_dir: Optional[str] = None) -> None:
    """Clear progress state for a video (e.g. on user force re-transcribe)."""
    state_file = resolve_state_file(video_path, state_dir)
    data = _load_raw_state(state_file)
    key = normalize_video_key(video_path)
    if key in data["videos"]:
        del data["videos"][key]
        _save_raw_state(state_file, data)


def format_resume_status(completed_up_to: float, status: str = "") -> str:
    """
    Format human-readable status for UI tree display:
      - If completed_up_to > 0 and status != 'completed': 'Resuming at MM:SS'
      - If completed: 'Completed'
      - Otherwise: 'Pending'
    """
    if status == "completed":
        return "Completed"
    if completed_up_to > 0.0:
        mins = int(completed_up_to) // 60
        secs = int(completed_up_to) % 60
        return f"Resuming at {mins:02d}:{secs:02d}"
    if status:
        return status.capitalize()
    return "Pending"
