"""
Canonical event-folder layout for Session Scribe.

The user selects an event root: D:\\Events\\<event>
Derived paths:
  <event>\\data
  <event>\\data\\Photos
  <event>\\data\\Videos
  <event>\\scribe
  <event>\\scribe\\manifest.json
  <event>\\scribe\\glossary.txt
  <event>\\scribe\\test
  <event>\\scribe\\transcripts
  <event>\\scribe\\work
  <event>\\scribe\\logs
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

LEAF_FOLDER_NAMES = frozenset({
    "data",
    "videos",
    "photos",
    "scribe",
    "work",
    "test",
    "transcripts",
    "logs",
    "combined",
    "reports",
})

GLOSSARY_FILENAME = "glossary.txt"


def normalize_path(path: str) -> str:
    return os.path.normcase(os.path.abspath(os.path.normpath(path)))


def same_path(a: str, b: str) -> bool:
    if not a or not b:
        return False
    return normalize_path(a) == normalize_path(b)


def is_under_root(path: str, root: str) -> bool:
    """True if path is the root or a file/folder inside it (same drive)."""
    if not path or not root:
        return False
    try:
        npath = normalize_path(path)
        nroot = normalize_path(root)
    except (OSError, ValueError):
        return False
    if npath == nroot:
        return True
    prefix = nroot + os.sep
    return npath.startswith(prefix)


def proposed_event_root(selected: str) -> str:
    """Walk up from data/Videos/scribe/etc. to the event root."""
    if not selected:
        return selected
    p = os.path.abspath(selected)
    while os.path.basename(p).lower() in LEAF_FOLDER_NAMES:
        parent = os.path.dirname(p)
        if parent == p:
            break
        p = parent
    return p


def derive_event_paths(event_root: str) -> Dict[str, str]:
    root = os.path.abspath(event_root.strip())
    data = os.path.join(root, "data")
    scribe = os.path.join(root, "scribe")
    return {
        "event_root": root,
        "data": data,
        "photos": os.path.join(data, "Photos"),
        "videos": os.path.join(data, "Videos"),
        "scribe": scribe,
        "manifest": os.path.join(scribe, "manifest.json"),
        "glossary": os.path.join(scribe, GLOSSARY_FILENAME),
        "test": os.path.join(scribe, "test"),
        "transcripts": os.path.join(scribe, "transcripts"),
        "work": os.path.join(scribe, "work"),
        "logs": os.path.join(scribe, "logs"),
        "transcription_log": os.path.join(scribe, "logs", "transcription.log"),
        # Per-clip state (replaces .meta.json sidecars beside transcripts).
        "state": os.path.join(scribe, "state"),
        "combined": os.path.join(scribe, "combined"),
        "reports": os.path.join(scribe, "reports"),
    }


def format_paths_banner(paths: Dict[str, str]) -> str:
    return (
        f"Event root: {paths['event_root']}\n"
        f"data: {paths['data']}\n"
        f"scribe: {paths['scribe']}\n"
        f"manifest: {paths['manifest']}"
    )


def format_paths_log_header(paths: Dict[str, str]) -> str:
    return "Resolved paths:\n" + format_paths_banner(paths)


def expected_manifest_path(event_root: str) -> str:
    return os.path.join(os.path.abspath(event_root), "scribe", "manifest.json")


def manifest_belongs_to_event(
    manifest: Optional[Dict[str, Any]],
    manifest_path: str,
    event_root: str,
) -> bool:
    """
    A manifest belongs to the selected event only if it lives at
    <event>\\scribe\\manifest.json, and if it records event_root, that path matches.
    """
    if not event_root or not manifest_path:
        return False
    if not same_path(manifest_path, expected_manifest_path(event_root)):
        return False
    if not manifest:
        return False
    stored = manifest.get("event_root")
    if stored:
        return same_path(str(stored), event_root)
    return True


def validate_clips_for_event(clips: List[Dict[str, Any]], event_root: str) -> List[str]:
    errors: List[str] = []
    for clip in clips:
        name = clip.get("name") or "(unnamed clip)"
        path = clip.get("path") or ""
        if not path:
            errors.append(f"Clip '{name}' has no path in the manifest.")
            continue
        if not os.path.isfile(path):
            errors.append(f"Clip file is missing: {path}")
        if not is_under_root(path, event_root):
            errors.append(f"Clip is outside the selected event root:\n  {path}")
    return errors


def load_glossary(scribe_dir: str) -> str:
    path = os.path.join(scribe_dir, GLOSSARY_FILENAME)
    if not os.path.isfile(path):
        return ""
    with open(path, "r", encoding="utf-8") as f:
        return f.read().strip()


def save_glossary(scribe_dir: str, text: str) -> str:
    os.makedirs(scribe_dir, exist_ok=True)
    path = os.path.join(scribe_dir, GLOSSARY_FILENAME)
    with open(path, "w", encoding="utf-8") as f:
        f.write((text or "").strip())
    return path
