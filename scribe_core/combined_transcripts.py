"""
scribe_core/combined_transcripts.py

Creates and manages combined transcript text files per speaker in:
    <event root>\\scribe\\combined\\<speaker_name>_combined.txt

Rules:
- Replace spaces with underscores and strip Windows invalid filename characters (<>:"/\\|?*).
- Content is an exact concatenation of completed transcript .txt files in clip start-time order,
  separated by a single blank line.
- Only completed transcripts are included; missing/pending clips are omitted with warnings.
- Never creates an empty file.
- Writes atomically (temporary file then os.replace).
- Moves stale combined files for renamed speakers to scribe\\combined\\old\\.
- Output only: skipped by state and transcript scanning.
"""

from __future__ import annotations

import os
import re
import shutil
from typing import Any, Dict, List, Optional

from scribe_core.multi_speaker import substitute_line_start_speaker_tags


_INVALID_WIN_CHARS_RE = re.compile(r'[\\/*?:"<>|]')


def sanitize_speaker_filename(speaker_name: str) -> str:
    """
    Sanitize speaker name for a valid Windows filename.
    Replaces spaces with underscores and removes invalid Windows filename characters.
    Example: 'Kalyani Patra' -> 'Kalyani_Patra_combined.txt'
    """
    name = (speaker_name or "").strip()
    name = name.replace(" ", "_")
    name = _INVALID_WIN_CHARS_RE.sub("", name)
    name = name.strip("._")
    if not name:
        name = "Speaker"
    return f"{name}_combined.txt"


def is_completed_transcript_file(path: str) -> bool:
    """True if path is a finished transcript (non-empty + header present)."""
    if not os.path.isfile(path):
        return False
    try:
        if os.path.getsize(path) < 50:
            return False
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            head = f.read(4096)
        return "Transcription Completed:" in head
    except OSError:
        return False


def build_combined_transcripts(
    event_root: str,
    manifest_data: Dict[str, Any],
    alias_to_name: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """
    Generate one combined text file per speaker in <event root>\\scribe\\combined\\.
    When alias_to_name is provided (e.g. Vault v2 unlocked), aliases (S01, S02)
    are resolved to decrypted speaker names both for the filename and in the turn tags.
    When alias_to_name is None (Vault v2 locked/uninitialized), aliases/labels are preserved.

    Returns:
      {
        "summary": "Kalyani_Patra_combined.txt: 11 clips, Jeevan_D_C_combined.txt: 12 clips",
        "counts": {"Kalyani_Patra_combined.txt": 11, ...},
        "warnings": ["Speaker '...': 1 clip(s) left out (...)"],
        "created_files": ["...path..."],
      }
    """
    combined_dir = os.path.join(event_root, "scribe", "combined")
    transcripts_dir = os.path.join(event_root, "scribe", "transcripts")
    test_dir = os.path.join(event_root, "scribe", "test")
    os.makedirs(combined_dir, exist_ok=True)

    counts: Dict[str, int] = {}
    warnings: List[str] = []
    created_files: List[str] = []

    sessions = manifest_data.get("sessions", [])
    for session in sessions:
        speaker = (session.get("speaker") or "Speaker").strip()
        clips = session.get("clips", [])
        if not clips:
            continue

        # Sort clips in clip start-time order (ISO timestamp or name fallback)
        clips_sorted = sorted(
            clips,
            key=lambda c: str(c.get("start_time") or c.get("name") or "")
        )

        completed_texts: List[str] = []
        omitted_clips: List[str] = []

        for c in clips_sorted:
            name = c.get("name", "")
            stem, _ = os.path.splitext(name)
            txt_path = os.path.join(transcripts_dir, f"{stem}.txt")
            if not is_completed_transcript_file(txt_path):
                alt_txt = os.path.join(test_dir, f"{stem}.txt")
                if is_completed_transcript_file(alt_txt):
                    txt_path = alt_txt

            if is_completed_transcript_file(txt_path):
                try:
                    with open(txt_path, "r", encoding="utf-8", errors="ignore") as fh:
                        completed_texts.append(fh.read().rstrip())
                except OSError as e:
                    omitted_clips.append(f"{name} (read error: {e})")
            else:
                omitted_clips.append(name or stem or "unnamed")

        if omitted_clips:
            warnings.append(
                f"Speaker '{speaker}': {len(omitted_clips)} clip(s) left out ({', '.join(omitted_clips)})"
            )

        # Never create an empty file
        if not completed_texts:
            continue

        # For multi-speaker sessions, output is <session label>_combined.txt
        is_multi = session.get("speaker_mode") == "panel" or bool(session.get("roster"))
        session_label = (session.get("speaker") or f"Session_{session.get('session_id', 1)}").strip()
        effective_name = session_label
        if alias_to_name:
            if session_label in alias_to_name:
                effective_name = alias_to_name[session_label]
            elif speaker in alias_to_name:
                effective_name = alias_to_name[speaker]
            elif session.get("expected_speaker", {}).get("alias") in alias_to_name:
                effective_name = alias_to_name[session["expected_speaker"]["alias"]]

        filename = sanitize_speaker_filename(effective_name)
        target_path = os.path.join(combined_dir, filename)
        tmp_path = os.path.join(combined_dir, f"{filename}.tmp.{os.getpid()}")

        # Content is concatenated in clip start-time order with a single blank line between clips
        content = "\n\n".join(completed_texts)

        if alias_to_name:
            content, _ = substitute_line_start_speaker_tags(content, alias_to_name)

        try:
            with open(tmp_path, "w", encoding="utf-8") as fh:
                fh.write(content)

            # Move existing combined file to scribe\combined\old\ before replacing
            old_dir = os.path.join(combined_dir, "old")
            if os.path.isfile(target_path):
                os.makedirs(old_dir, exist_ok=True)
                old_target = os.path.join(old_dir, filename)
                if os.path.exists(old_target):
                    try:
                        os.remove(old_target)
                    except OSError:
                        pass
                try:
                    shutil.move(target_path, old_target)
                except OSError:
                    pass

            # Stale duplicate avoidance: archive any alternate active filenames for this session
            # (e.g. S01_combined.txt when writing Dr._Ada_Lovelace_combined.txt, or vice-versa)
            alias_name = (session.get("expected_speaker", {}).get("alias") or session_label).strip()
            alias_filename = sanitize_speaker_filename(alias_name)
            alternate_filenames = set()
            if alias_filename != filename:
                alternate_filenames.add(alias_filename)
            if alias_to_name:
                for a, r in alias_to_name.items():
                    if a == alias_name or r == effective_name:
                        afn = sanitize_speaker_filename(a)
                        rfn = sanitize_speaker_filename(r)
                        if afn != filename:
                            alternate_filenames.add(afn)
                        if rfn != filename:
                            alternate_filenames.add(rfn)

            for alt_fn in alternate_filenames:
                alt_path = os.path.join(combined_dir, alt_fn)
                if os.path.isfile(alt_path):
                    os.makedirs(old_dir, exist_ok=True)
                    old_alt = os.path.join(old_dir, alt_fn)
                    if os.path.exists(old_alt):
                        try:
                            os.remove(old_alt)
                        except OSError:
                            pass
                    try:
                        shutil.move(alt_path, old_alt)
                    except OSError:
                        pass

            os.replace(tmp_path, target_path)
            counts[filename] = len(completed_texts)
            created_files.append(target_path)
        except OSError as e:
            warnings.append(f"Could not write {filename}: {e}")
            if os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass

    summary = ", ".join(f"{name}: {cnt} clips" for name, cnt in counts.items())
    return {
        "summary": summary,
        "counts": counts,
        "warnings": warnings,
        "created_files": created_files,
    }


def archive_stale_combined_file(event_root: str, old_speaker_name: str) -> Optional[str]:
    """
    Move <old_speaker_name>_combined.txt from scribe\\combined\\ to scribe\\combined\\old\\.
    Returns the new archived path, or None if the old file did not exist.
    """
    combined_dir = os.path.join(event_root, "scribe", "combined")
    old_dir = os.path.join(combined_dir, "old")
    filename = sanitize_speaker_filename(old_speaker_name)
    src_path = os.path.join(combined_dir, filename)

    if not os.path.isfile(src_path):
        return None

    os.makedirs(old_dir, exist_ok=True)
    dst_path = os.path.join(old_dir, filename)

    if os.path.exists(dst_path):
        try:
            os.remove(dst_path)
        except OSError:
            pass

    shutil.move(src_path, dst_path)
    return dst_path
