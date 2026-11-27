"""
Per-clip transcription state store for Session Scribe.

Replaces the {stem}.meta.json sidecar files that used to sit beside transcripts.
All per-clip settings are stored in a single JSON file:

    <event>\\scribe\\state\\clip_state.json

Structure
---------
{
  "version": "1",
  "clips": {
    "<stem>": {
      "model": "medium",
      "language": "en",
      "glossary_hash": "abc12345",
      "transcribed_at": "2026-10-28 09:00:00 IST",
      "completed": true
    },
    ...
  }
}

All writes are atomic (write to .tmp then os.replace) so a crash never
leaves a corrupt state file.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from typing import Any, Dict, List, Optional


STATE_FILENAME = "clip_state.json"
OLD_META_SUBDIR = "old_meta"


# ---------------------------------------------------------------------------
# Low-level I/O helpers
# ---------------------------------------------------------------------------

def _resolve_state_path(state_path: str) -> str:
    """Ensure state_path points to clip_state.json even if a directory was passed."""
    if not state_path:
        return ""
    if os.path.isdir(state_path) or not state_path.lower().endswith(".json"):
        return os.path.join(state_path, STATE_FILENAME)
    return state_path


def _load_raw(state_path: str) -> dict:
    """Load the raw state dict or return an empty skeleton."""
    state_path = _resolve_state_path(state_path)
    if not os.path.isfile(state_path):
        return {"version": "1", "clips": {}}
    try:
        with open(state_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data.get("clips"), dict):
            data["clips"] = {}
        return data
    except Exception:
        return {"version": "1", "clips": {}}


def _save_raw(state_path: str, data: dict) -> None:
    """Atomically write *data* to *state_path*."""
    state_path = _resolve_state_path(state_path)
    os.makedirs(os.path.dirname(state_path), exist_ok=True)
    tmp = state_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, state_path)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def read_clip(state_path: str, stem: str) -> dict:
    """Return the state record for one clip *stem*, or {} if absent."""
    return _load_raw(state_path)["clips"].get(stem, {})


def read_all(state_path: str) -> Dict[str, Any]:
    """Return the full clips dict (stem → record). Empty dict when no state file."""
    return _load_raw(state_path)["clips"]


def write_clip(
    state_path: str,
    stem: str,
    model: str = "medium",
    language: str = "en",
    glossary_hash: str = "",
    transcribed_at: str = "",
    engine: str = "local",
    provider: str = "local",
    model_id: str = "",
    processing_time_sec: Optional[float] = None,
    failover_info: Optional[dict] = None,
    failover_time_sec: Optional[float] = None,
    diarization_time_sec: Optional[float] = None,
    identification_time_sec: Optional[float] = None,
) -> None:
    """Upsert one clip's settings into the state file, atomically."""
    data = _load_raw(state_path)
    existing = data["clips"].get(stem, {})
    clip_rec = {
        "model": model,
        "language": language,
        "glossary_hash": glossary_hash,
        "transcribed_at": transcribed_at,
        "completed": True,
        "approved": False,
        "transcript_hash": "",
        "approved_at": "",
        "edited": existing.get("edited", False),
        "engine": engine,
        "provider": provider,
        "model_id": model_id,
    }
    if processing_time_sec is not None:
        clip_rec["processing_time_sec"] = round(float(processing_time_sec), 2)
    elif "processing_time_sec" in existing:
        clip_rec["processing_time_sec"] = existing["processing_time_sec"]

    if failover_time_sec is not None:
        clip_rec["failover_time_sec"] = round(float(failover_time_sec), 2)
    elif failover_info is not None and "failover_time_sec" in failover_info:
        clip_rec["failover_time_sec"] = failover_info["failover_time_sec"]
    elif "failover_time_sec" in existing:
        clip_rec["failover_time_sec"] = existing["failover_time_sec"]

    if failover_info is not None:
        clip_rec["failover"] = failover_info
        clip_rec["failover_info"] = failover_info
    elif "failover" in existing:
        clip_rec["failover"] = existing["failover"]
        clip_rec["failover_info"] = existing.get("failover_info", existing["failover"])

    if diarization_time_sec is not None:
        clip_rec["diarization_time_sec"] = round(float(diarization_time_sec), 2)
    elif "diarization_time_sec" in existing:
        clip_rec["diarization_time_sec"] = existing["diarization_time_sec"]

    if identification_time_sec is not None:
        clip_rec["identification_time_sec"] = round(float(identification_time_sec), 2)
    elif "identification_time_sec" in existing:
        clip_rec["identification_time_sec"] = existing["identification_time_sec"]

    data["clips"][stem] = clip_rec
    # Clear any mid-clip checkpoint once completed
    if "checkpoints" in data and stem in data["checkpoints"]:
        data["checkpoints"].pop(stem, None)
    _save_raw(state_path, data)


def save_checkpoint(
    state_path: str,
    stem: str,
    checkpoint_data: dict,
) -> None:
    """Atomically record a mid-clip progress checkpoint in the state file."""
    data = _load_raw(state_path)
    checkpoints = data.setdefault("checkpoints", {})
    checkpoints[stem] = checkpoint_data
    _save_raw(state_path, data)


def get_checkpoint(state_path: str, stem: str) -> Optional[dict]:
    """Return checkpoint dict for stem or None."""
    data = _load_raw(state_path)
    return data.get("checkpoints", {}).get(stem)


def clear_checkpoint(state_path: str, stem: str) -> None:
    """Remove checkpoint for stem."""
    data = _load_raw(state_path)
    if "checkpoints" in data and stem in data["checkpoints"]:
        del data["checkpoints"][stem]
        _save_raw(state_path, data)


def compute_transcript_hash(txt_path: str) -> Optional[str]:
    """Compute SHA-256 hash of transcript file, or None if file doesn't exist."""
    if not os.path.isfile(txt_path):
        return None
    try:
        with open(txt_path, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()
    except OSError:
        return None


def get_clip_approval(state_path: str, stem: str) -> bool:
    """Return True if clip is marked approved, False otherwise."""
    rec = read_clip(state_path, stem)
    return bool(rec.get("approved", False))


def set_clip_approval(
    state_path: str,
    stem: str,
    approved: bool,
    transcript_hash: Optional[str] = None,
    approved_at: Optional[str] = None,
) -> None:
    """Update approval status and content hash for one clip stem."""
    data = _load_raw(state_path)
    rec = data["clips"].setdefault(stem, {})
    rec["approved"] = bool(approved)
    rec["transcript_hash"] = transcript_hash or ""
    rec["approved_at"] = approved_at or ""
    _save_raw(state_path, data)


def set_multiple_clip_approvals(
    state_path: str,
    approvals: Dict[str, dict],
) -> None:
    """Batch update approval status and content hashes for multiple clips."""
    data = _load_raw(state_path)
    for stem, info in approvals.items():
        rec = data["clips"].setdefault(stem, {})
        rec["approved"] = bool(info.get("approved", False))
        rec["transcript_hash"] = info.get("transcript_hash", "")
        rec["approved_at"] = info.get("approved_at", "")
    _save_raw(state_path, data)


def clear_multiple_clip_approvals(
    state_path: str,
    stems: List[str],
) -> None:
    """Batch clear approvals for given stems."""
    data = _load_raw(state_path)
    for stem in stems:
        if stem in data["clips"]:
            data["clips"][stem]["approved"] = False
            data["clips"][stem]["transcript_hash"] = ""
            data["clips"][stem]["approved_at"] = ""
    _save_raw(state_path, data)


def validate_and_reconcile_approvals(
    state_path: str,
    transcripts_dir: str,
    clip_stems: List[str],
    test_dir: Optional[str] = None,
) -> List[str]:
    """
    Check all clips marked approved in state_path. If their transcript file
    on disk in transcripts_dir (or test_dir) does not exist or its content hash
    differs from the recorded transcript_hash, clear approval automatically.
    Returns the list of stems whose approval was revoked.
    """
    data = _load_raw(state_path)
    revoked: List[str] = []
    changed = False

    for stem in clip_stems:
        rec = data.get("clips", {}).get(stem)
        if not rec or not rec.get("approved"):
            continue

        txt_path = os.path.join(transcripts_dir, f"{stem}.txt")
        if not os.path.isfile(txt_path) and test_dir:
            test_path = os.path.join(test_dir, f"{stem}.txt")
            if os.path.isfile(test_path):
                txt_path = test_path

        current_hash = compute_transcript_hash(txt_path)
        recorded_hash = rec.get("transcript_hash")

        if not current_hash or current_hash != recorded_hash:
            rec["approved"] = False
            rec["transcript_hash"] = ""
            rec["approved_at"] = ""
            revoked.append(stem)
            changed = True

    if changed:
        _save_raw(state_path, data)

    return revoked


def set_clip_edited(state_path: str, stem: str, edited: bool = True, edited_at: Optional[str] = None) -> None:
    """Mark a clip as manually edited in the state file."""
    data = _load_raw(state_path)
    rec = data["clips"].setdefault(stem, {})
    rec["edited"] = bool(edited)
    if edited_at:
        rec["edited_at"] = edited_at
    rec["completed"] = True
    _save_raw(state_path, data)


def get_combined_stale(state_path: str) -> bool:
    """Return True if combined files are marked stale."""
    return bool(_load_raw(state_path).get("combined_stale", False))


def set_combined_stale(state_path: str, stale: bool) -> None:
    """Mark combined transcripts as stale or up to date in state file."""
    data = _load_raw(state_path)
    data["combined_stale"] = bool(stale)
    _save_raw(state_path, data)


def settings_differ(
    state_path: str,
    stem: str,
    model: str,
    language: str,
    ghash: str,
    engine: str = "local",
) -> bool:
    """
    Return True if the state file records *different* settings for *stem*.
    Returns False (no mismatch assumed) when no record exists — benefit of the doubt.
    The settings-difference check ignores manually edited clips.
    Engine differences count as different settings.
    """
    rec = read_clip(state_path, stem)
    if not rec:
        return False
    if rec.get("edited"):
        return False
    rec_engine = rec.get("engine", "local")
    if rec_engine != engine:
        return True
    return (
        rec.get("model") != model
        or rec.get("language") != language
        or rec.get("glossary_hash") != ghash
    )


# ---------------------------------------------------------------------------
# One-time migration
# ---------------------------------------------------------------------------

def migrate_meta_json(
    scan_dirs: List[str],
    state_path: str,
    old_meta_dir: str,
) -> int:
    """
    Scan *scan_dirs* for ``{stem}.meta.json`` files.  For each one found:

    1. Read its model / language / glossary_hash / transcribed_at.
    2. Merge into the state file (never overwrites an existing entry).
    3. Move the original file to *old_meta_dir* (NOT deleted).

    Returns the count of files migrated.
    Migration is idempotent: once the .meta.json files are moved there is
    nothing left to migrate on subsequent runs.
    """
    os.makedirs(old_meta_dir, exist_ok=True)
    data = _load_raw(state_path)
    count = 0

    for scan_dir in scan_dirs:
        if not os.path.isdir(scan_dir):
            continue
        try:
            entries = os.listdir(scan_dir)
        except OSError:
            continue

        for fname in entries:
            if not fname.endswith(".meta.json"):
                continue
            stem = fname[: -len(".meta.json")]
            fpath = os.path.join(scan_dir, fname)

            try:
                with open(fpath, "r", encoding="utf-8") as fh:
                    rec = json.load(fh)
            except Exception:
                rec = {}

            # Only write when no existing entry — don't overwrite newer state.
            if stem not in data["clips"]:
                data["clips"][stem] = {
                    "model": rec.get("model", ""),
                    "language": rec.get("language", ""),
                    "glossary_hash": rec.get("glossary_hash", ""),
                    "transcribed_at": rec.get("transcribed_at", ""),
                    "completed": True,
                }

            # Move the original; generate a unique name if needed.
            dest_name = fname
            dest = os.path.join(old_meta_dir, dest_name)
            if os.path.exists(dest):
                base = stem
                i = 1
                while os.path.exists(dest):
                    dest_name = f"{base}_{i}.meta.json"
                    dest = os.path.join(old_meta_dir, dest_name)
                    i += 1

            try:
                shutil.move(fpath, dest)
                count += 1
            except OSError:
                pass  # Leave it in place if move fails; try again next run.

    if count > 0:
        _save_raw(state_path, data)

    return count
