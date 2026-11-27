"""
Stage 3 Tab: Transcription (Test Gate First, then Full Run)
Tkinter / ttk implementation with isolated subprocess execution, resume support,
and real-time progress / speed tracking.

Bug 1 fixes (manifest refresh):
  - Stage 2 calls ctx.notify_manifest_saved() after writing manifest.json;
    Stage 3 listens via ctx.bind_manifest_saved() and reloads immediately.
  - Backstop: <Map> event (tab becomes visible) re-reads the manifest if its
    mtime has changed since the last load.
  - "Reload" button added to the header row.
  - While a run is active, incoming reload requests set _pending_reload=True
    and show a banner; the reload is applied when the run ends or is cancelled.

Bug 2 fixes (skip already-done clips):
  - prepare_job_config now receives test_dir, transcripts_dir, force_clips, and
    glossary_hash; the worker checks both directories before transcribing.
  - Status column shows Pending / Done (test) / Done / Diff settings / File missing.
  - Status is computed from disk on every manifest load and on tab focus.
  - Right-click → "Force re-transcribe" toggles a per-clip flag that bypasses
    the skip check; it is the only way to redo a finished clip.
  - Pre-run summary ("N clips: X done, Y to transcribe") printed to the log.

Task A additions:
  - Pre-run guard: warns when any session still carries a "Speaker N" placeholder
    name, with "Go back" (abort) and "Continue anyway" choices.
  - Rename-existing-transcripts tool: dialog to rename the speaker label in
    finished .txt files without re-transcribing or touching the spoken text.
    Updates the manifest atomically.  Never touches the state file, so skip
    logic is unaffected and no clip is ever marked outdated by a rename.

Task B additions:
  - _clip_disk_status reads per-clip settings from scribe\\state\\clip_state.json
    instead of .meta.json sidecars beside the transcript.
  - state_dir is passed to prepare_job_config so the worker writes there.
"""

from datetime import datetime, timezone, timedelta
import hashlib
import json
import os
import re
import shutil
import sys
import threading
import time
import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext, font
from typing import Optional, Dict, Any, List, Tuple, Callable

IST = timezone(timedelta(hours=5, minutes=30))

from scribe_core.transcription_manager import TranscriptionManager
from scribe_core.event_roster_manager import EventRosterManager
from scribe_core.identification_engine import SpeakerCluster, IdentificationEngine, load_all_event_clusters
from gui.reidentify_dialog import ReidentifyDialog
from gui.vault_dialog import UnlockVaultDialog
from scribe_core.vault_v2 import VaultV2StorageEngine
from scribe_core.embedding_engine import LocalEmbeddingEngine
from scribe_core.enrollment_manager import EnrollmentManager
from gui.enrollment_dialog import VoiceProfileEnrollmentDialog
from scribe_core.combined_transcripts import is_completed_transcript_file
from scribe_core.multi_speaker import substitute_line_start_speaker_tags
from scribe_core.voice_vault import VoiceVault
from gui.vault_dialog import VaultStatusChip, ensure_vault_unlocked_preflight
from scribe_core.clip_state import (
    read_clip as _state_read_clip,
    compute_transcript_hash,
    get_clip_approval,
    set_clip_approval,
    set_multiple_clip_approvals,
    clear_multiple_clip_approvals,
    validate_and_reconcile_approvals,
    get_combined_stale,
    set_combined_stale,
    set_clip_edited,
)
from scribe_core.combined_transcripts import (
    build_combined_transcripts,
    archive_stale_combined_file,
    sanitize_speaker_filename,
)
from scribe_core import cloud_engine as _cloud_engine
from scribe_core import session_tracker as _session_tracker
from scribe_core import hf_preflight
from scribe_core import model_registry
from scribe_core import run_comparison
from scribe_core.usage_ledger import UsageLedger
from scribe_core.event_paths import (
    derive_event_paths,
    format_paths_log_header,
    manifest_belongs_to_event,
    save_glossary,
    validate_clips_for_event,
)

from .event_context import EventContext
from .paths_banner import PathsBanner
from .scrollable_tab import ScrollableTab, attach_universal_scrolling
from .zoom_helper import TextViewerZoomer
from . import theme


# ---------------------------------------------------------------------------
# Helpers (pure functions; no Tk imports needed)
# ---------------------------------------------------------------------------

def format_status_display(status: str) -> str:
    """Format status with symbols and text ensuring status is never conveyed by color alone."""
    if not status:
        return "Pending"
    s = status.strip()
    if s.startswith("● "):
        return s
    if s == "Queued":
        return "Queued"
    if s in ("Done", "Completed"):
        return "✓ Done"
    elif s == "Done (test)":
        return "✓ Done (test)"
    elif s == "Edited":
        return "✓ Edited"
    elif s in ("Running", "Transcribing…"):
        return "● Transcribing"
    elif s in ("Diarizing", "Diarizing…"):
        return "● Diarizing"
    elif s in ("Identifying", "Identifying…"):
        return "● Identifying"
    elif s == "Failed":
        return "✕ Failed"
    elif s == "File missing":
        return "✕ File missing"
    elif s in ("Diff settings", "Different settings"):
        return "! Different settings"
    elif s == "Pending (forced)":
        return "● Pending (forced)"
    elif s.startswith("Resuming at"):
        return f"● {s}" if not s.startswith("●") else s
    elif s == "Pending":
        return "Pending"
    return s


def format_approval_display(is_approved: bool) -> str:
    """Format approval cell with symbol and text."""
    return "✓ Approved" if is_approved else "— Not approved"


def _glossary_hash(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()[:8]


def _is_completed_transcript(path: str) -> bool:
    """True if *path* is a finished transcript (non-empty + header present)."""
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


def _clip_disk_status(clip: dict, paths: dict, current_ghash: str) -> str:
    """
    Inspect disk and return a status string for the clip list column.
    Reads per-clip settings from the state file (Task B) rather than
    .meta.json sidecars.

    Possible values: Pending | Done | Done (test) | Edited | Diff settings | File missing
    """
    clip_path = clip.get("path", "")
    if clip_path and not os.path.isfile(clip_path):
        return "File missing"

    stem, _ = os.path.splitext(clip.get("name", ""))
    if not stem:
        return "Pending"

    state_path = paths.get("state", "")

    transcripts_txt = os.path.join(paths["transcripts"], f"{stem}.txt")
    test_txt        = os.path.join(paths["test"],        f"{stem}.txt")

    # Check scribe\transcripts first.
    if _is_completed_transcript(transcripts_txt):
        rec = _state_read_clip(state_path, stem) if state_path else {}
        if rec and rec.get("edited"):
            return "Edited"
        if rec and rec.get("glossary_hash") and rec.get("glossary_hash") != current_ghash:
            return "Diff settings"
        return "Done"

    # Then scribe\test.
    if _is_completed_transcript(test_txt):
        rec = _state_read_clip(state_path, stem) if state_path else {}
        if rec and rec.get("edited"):
            return "Edited"
        if rec and rec.get("glossary_hash") and rec.get("glossary_hash") != current_ghash:
            return "Diff settings"
        return "Done (test)"

    # Mid-point resume state check
    if state_path and clip_path:
        st_rec = _session_tracker.get_video_state(clip_path, state_path)
        if st_rec:
            st_status = st_rec.get("status", "")
            completed_up_to = float(st_rec.get("completed_up_to", 0.0))
            if st_status == "completed":
                return "Completed"
            if completed_up_to > 0.0 and st_status in ("in_progress", "pending", "failed"):
                return _session_tracker.format_resume_status(completed_up_to, st_status)
            if st_status == "failed":
                return "Failed"

    return "Pending"


def format_duration_hhmmss(seconds: float) -> str:
    """Format duration in seconds to hh:mm:ss rounded to nearest second."""
    try:
        total_seconds = int(round(float(seconds)))
        if total_seconds < 0:
            total_seconds = 0
        hours = total_seconds // 3600
        minutes = (total_seconds % 3600) // 60
        secs = total_seconds % 60
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    except (ValueError, TypeError):
        return "—"


def _get_clip_time_taken(clip_name: str, paths: dict) -> str:
    """
    Get transcription time taken for a clip.
    1. Check processing_time_sec, diarization_time_sec, identification_time_sec in state file.
    2. Fallback: Parse 'Transcription Duration: ...' and 'Diarization Duration: ...' from transcript header on disk.
    3. Fallback: '—' if pending, missing, or not found.
    """
    if not clip_name or not paths:
        return "—"
    stem, _ = os.path.splitext(clip_name)
    state_path = paths.get("state", "")
    if state_path:
        rec = _state_read_clip(state_path, stem)
        if rec and rec.get("processing_time_sec") is not None:
            try:
                sec = float(rec["processing_time_sec"])
                disp = format_duration_hhmmss(sec)
                diar_sec = rec.get("diarization_time_sec")
                ident_sec = rec.get("identification_time_sec")
                if diar_sec or ident_sec:
                    parts = []
                    if diar_sec:
                        parts.append(f"Diar: {float(diar_sec):.1f}s")
                    if ident_sec:
                        parts.append(f"ID: {float(ident_sec):.1f}s")
                    return f"{disp} ({', '.join(parts)})"
                return disp
            except (ValueError, TypeError):
                pass

    # Fallback: check transcript file header on disk (transcripts, then test)
    for folder_key in ("transcripts", "test"):
        folder = paths.get(folder_key)
        if not folder:
            continue
        txt_path = os.path.join(folder, f"{stem}.txt")
        if os.path.isfile(txt_path):
            try:
                with open(txt_path, "r", encoding="utf-8", errors="ignore") as f:
                    dur_part = ""
                    diar_part = ""
                    ident_part = ""
                    for _ in range(40):
                        line = f.readline()
                        if not line:
                            break
                        if line.startswith("Transcription Duration:"):
                            dur_part = line.split("Transcription Duration:", 1)[1].strip()
                        elif line.startswith("Diarization Duration:"):
                            diar_part = line.split("Diarization Duration:", 1)[1].strip()
                        elif line.startswith("Identification Duration:"):
                            ident_part = line.split("Identification Duration:", 1)[1].strip()
                    if dur_part:
                        disp = dur_part
                        parts = dur_part.split(":")
                        if len(parts) == 2:
                            disp = f"00:{dur_part}"
                        extra = []
                        if diar_part:
                            extra.append(f"Diar: {diar_part}")
                        if ident_part:
                            extra.append(f"ID: {ident_part}")
                        if extra:
                            return f"{disp} ({', '.join(extra)})"
                        return disp
            except OSError:
                pass

    return "—"


def format_failover_time(seconds: Optional[float]) -> str:
    """Format failover duration in seconds to hh:mm:ss.s, or '—' if None or <= 0."""
    if seconds is None:
        return "—"
    try:
        sec = float(seconds)
        if sec <= 0:
            return "—"
        total_tenths = int(round(sec * 10))
        tenths = total_tenths % 10
        total_seconds = total_tenths // 10
        hours = total_seconds // 3600
        minutes = (total_seconds % 3600) // 60
        s = total_seconds % 60
        return f"{hours:02d}:{minutes:02d}:{s:02d}.{tenths}"
    except (ValueError, TypeError):
        return "—"


def _get_clip_failover_data(clip_name: str, paths: dict) -> tuple[Optional[float], Optional[dict]]:
    """
    Get failover time (seconds) and failover_info dict for a clip from clip_state.json.
    Returns (failover_time_sec, failover_info).
    """
    if not clip_name or not paths:
        return None, None
    stem, _ = os.path.splitext(clip_name)
    state_path = paths.get("state", "")
    if state_path:
        rec = _state_read_clip(state_path, stem)
        if rec:
            fo_sec = rec.get("failover_time_sec")
            fo_info = rec.get("failover_info") or rec.get("failover")
            if fo_sec is not None:
                try:
                    return float(fo_sec), fo_info
                except (ValueError, TypeError):
                    pass
            elif fo_info and fo_info.get("failover_time_sec") is not None:
                try:
                    return float(fo_info["failover_time_sec"]), fo_info
                except (ValueError, TypeError):
                    pass
    return None, None


def _parse_time_to_seconds(time_str: str) -> Optional[float]:
    """Parse time string (hh:mm:ss, mm:ss, or ss.s) into float seconds."""
    if not time_str or time_str == "—":
        return None
    try:
        cleaned = time_str.strip().rstrip("s")
        parts = cleaned.split(":")
        if len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
        elif len(parts) == 2:
            return int(parts[0]) * 60 + float(parts[1])
        else:
            return float(cleaned)
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# Rename helpers (Task A — no GPU, no re-transcription)
# ---------------------------------------------------------------------------

_PLACEHOLDER_RE = re.compile(r"^Speaker\s+\d+$")


def _is_placeholder_name(name: str) -> bool:
    return bool(_PLACEHOLDER_RE.match((name or "").strip()))


def _dry_run_rename(txt_path: str, old_name: str, new_name: str) -> list:
    """
    Return list of (line_num, old_line_stripped, new_line_stripped) for preview.
    Does NOT write anything.
    """
    changes = []
    try:
        with open(txt_path, "r", encoding="utf-8") as fh:
            lines = fh.readlines()
    except OSError:
        return []

    for i, line in enumerate(lines, 1):
        stripped = line.rstrip("\n\r")
        if stripped == f"Speaker: {old_name}":
            changes.append((i, stripped, f"Speaker: {new_name}"))
        elif line.startswith(f"[{old_name}]"):
            new_line = f"[{new_name}]" + line[len(f"[{old_name}]"):]
            changes.append((i, stripped, new_line.rstrip("\n\r")))
    return changes


def _apply_rename_transcript(txt_path: str, old_name: str, new_name: str) -> int:
    """
    Replace speaker labels in *txt_path* atomically.
    Only touches:
      - The "Speaker: OldName" header line (exact match)
      - Lines starting with "[OldName]" (segment lines)
    The spoken text after the timestamp is never modified.

    Returns the number of lines changed, or 0 if the file was unchanged.
    Raises OSError on I/O failure.
    """
    with open(txt_path, "r", encoding="utf-8") as fh:
        lines = fh.readlines()

    new_lines = []
    n_changed = 0
    for line in lines:
        orig = line
        stripped = line.rstrip("\n\r")
        if stripped == f"Speaker: {old_name}":
            # Preserve original line-ending style
            line = f"Speaker: {new_name}" + line[len(stripped):]
        elif line.startswith(f"[{old_name}]"):
            line = f"[{new_name}]" + line[len(f"[{old_name}]"):]
        new_lines.append(line)
        if line != orig:
            n_changed += 1

    if n_changed == 0:
        return 0

    tmp = txt_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.writelines(new_lines)
    os.replace(tmp, txt_path)
    return n_changed


def _update_manifest_speaker(manifest_path: str, session_id: int, new_name: str) -> None:
    """
    Replace the speaker name for *session_id* in the manifest JSON, atomically.
    """
    with open(manifest_path, "r", encoding="utf-8") as fh:
        data = json.load(fh)

    for sess in data.get("sessions", []):
        if sess.get("session_id") == session_id:
            sess["speaker"] = new_name
            break

    tmp = manifest_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
    os.replace(tmp, manifest_path)


# ---------------------------------------------------------------------------
# Transcript Viewer & Editor Dialog (Task 2)
# ---------------------------------------------------------------------------

class TranscriptEditorDialog(tk.Toplevel):
    """
    In-app Transcript Viewer and Editor.
    Supports:
      - Read mode (read-only) and Edit mode (interactive typing)
      - Dynamic title bar showing mode and '[modified]' indicator
      - Full keyboard shortcuts: Ctrl+S (save), Ctrl+F (find), Ctrl+Z/Y (undo/redo), word-navigation
      - Atomic saving with original UTF-8 encoding and newline (CRLF vs LF) preservation
      - First-time original transcript backup to scribe\\state\\originals\\<stem>.txt
      - External disk-change conflict detection (Reload or Save Anyway)
      - Header preservation validation (warns on missing/corrupted header lines)
      - Unsaved changes prompt (Save, Discard, Cancel) on close, double-click, tab switch, event switch, app close
      - Re-approval clearing, combined file stale marking, and UI refresh on save
      - Read-only protection when clip is currently being transcribed
    """

    def __init__(self, parent, stage3_tab: "Stage3Tab", clip: dict, target_txt: str, loc_desc: str, paths: dict):
        super().__init__(parent)
        self.stage3_tab = stage3_tab
        self.clip = clip
        self.clip_name = clip.get("name", "")
        self.stem, _ = os.path.splitext(self.clip_name)
        self.filename = f"{self.stem}.txt"
        self.target_txt = target_txt
        self.loc_desc = loc_desc
        self.paths = paths

        self._mode = "read"   # "read" or "edit"
        self._is_dirty = False
        self._newline = "\n"
        self._current_match_idx = -1
        self._matches: list[tuple[str, str]] = []
        self._replace_row_visible = False
        self.text_frame = None

        self.case_var = tk.BooleanVar(value=False)
        self.whole_word_var = tk.BooleanVar(value=False)
        self.allow_header_var = tk.BooleanVar(value=False)

        # Read file contents and detect original newline
        try:
            with open(self.target_txt, "rb") as fh:
                raw_bytes = fh.read()
            if b"\r\n" in raw_bytes:
                self._newline = "\r\n"
            else:
                self._newline = "\n"
            self._original_content = raw_bytes.decode("utf-8", errors="replace")
        except OSError as exc:
            self._original_content = ""
            messagebox.showerror("Error", f"Could not read {self.target_txt}:\n{exc}", parent=parent)
            self.destroy()
            return

        self._opened_content_hash = compute_transcript_hash(self.target_txt)

        theme.center_dialog(self, parent, width=780, height=560, min_width=680, min_height=480)
        self.transient(parent)
        self._update_title()

        self._build_ui()
        self._bind_events()
        self.protocol("WM_DELETE_WINDOW", self._on_close_requested)
        self.focus_set()

    def _build_ui(self):
        # Text area created first so zoomer can attach
        self.text_frame = ttk.Frame(self)
        self.txt_widget = scrolledtext.ScrolledText(
            self.text_frame,
            wrap=tk.WORD,
            font=("Consolas", 10),
            undo=True,
            maxundo=-1,
            autoseparators=True,
        )
        self.txt_widget.pack(fill=tk.BOTH, expand=True)

        # Resolve aliases to decrypted names for viewing if vault is unlocked
        display_content = self._original_content
        self._alias_to_name_map: Dict[str, str] = {}
        if self.stage3_tab.vault.is_unlocked():
            event_id = self.stage3_tab.manifest_data.get("event_id", "") if self.stage3_tab.manifest_data else ""
            if event_id:
                try:
                    roster_mgr = EventRosterManager(self.stage3_tab.vault)
                    self._alias_to_name_map = roster_mgr.get_alias_to_name_map(event_id)
                    display_content, _ = substitute_line_start_speaker_tags(display_content, self._alias_to_name_map)
                except Exception:
                    pass

        self.txt_widget.insert(tk.END, display_content)
        self.txt_widget.edit_modified(False)
        self.txt_widget.configure(state="disabled")

        # Configure search highlighting tags
        self.txt_widget.tag_configure("find_match", background="#ffe082", foreground="#000000")
        self.txt_widget.tag_configure("find_active", background="#ff9800", foreground="#ffffff")
        try:
            self.txt_widget.tag_raise("find_match", tk.SEL)
        except Exception:
            pass
        try:
            self.txt_widget.tag_raise("find_active", "find_match")
        except Exception:
            pass
        try:
            self.txt_widget.tag_raise("find_active")
        except Exception:
            pass

        # Zoom helper
        self.zoomer = TextViewerZoomer(self.txt_widget, base_font_family="Consolas", base_font_size=10)

        # Top toolbar (two clean rows to prevent window stretching)
        toolbar = ttk.Frame(self, padding=(8, 6))
        toolbar.pack(fill=tk.X, side=tk.TOP)

        # Row 1: Header information (truncates with tooltip if too long)
        hdr_row = ttk.Frame(toolbar)
        hdr_row.pack(fill=tk.X, pady=(0, 4))

        speaker_name = self.clip.get("speaker", "Unknown")
        full_info = f"Clip: {self.clip_name}   |   Speaker: {speaker_name}   |   Location: {self.loc_desc}"
        display_info = full_info if len(full_info) <= 75 else full_info[:72] + "..."
        lbl_info = ttk.Label(hdr_row, text=display_info, font="SS_BodyBold")
        lbl_info.pack(side=tk.LEFT)
        theme.ToolTip(lbl_info, full_info)

        # Row 2: Action buttons row
        btn_row = ttk.Frame(toolbar)
        btn_row.pack(fill=tk.X)

        self.btn_save = ttk.Button(btn_row, text="Save", command=self.save_file, state=tk.DISABLED)
        self.btn_save.pack(side=tk.LEFT, padx=(0, 4))

        self.btn_find = ttk.Button(btn_row, text="Find", command=self._open_find_bar)
        self.btn_find.pack(side=tk.LEFT, padx=4)

        self.btn_mode = ttk.Button(btn_row, text="Edit", command=self._toggle_mode)
        self.btn_mode.pack(side=tk.LEFT, padx=4)

        # Zoom toolbar controls attached to right of btn_row
        zoom_frame = self.zoomer.attach_toolbar_controls(btn_row)
        zoom_frame.pack(side=tk.RIGHT, padx=(4, 0))

        # Find & Replace bar (hidden initially)
        self.find_frame = ttk.Frame(self, padding=(8, 4), relief=tk.RIDGE)

        # Find row
        find_row = ttk.Frame(self.find_frame)
        find_row.pack(fill=tk.X)

        self.btn_toggle_replace = ttk.Button(
            find_row, text="▼ Replace", width=10, command=self._toggle_replace_row
        )
        self.btn_toggle_replace.pack(side=tk.LEFT, padx=(0, 6))

        ttk.Label(find_row, text="Find:").pack(side=tk.LEFT, padx=(0, 4))
        self.find_entry = ttk.Entry(find_row, width=24)
        self.find_entry.pack(side=tk.LEFT, padx=4)

        ttk.Button(find_row, text="Next", command=self._find_next).pack(side=tk.LEFT, padx=2)
        ttk.Button(find_row, text="Previous", command=self._find_prev).pack(side=tk.LEFT, padx=2)

        self.chk_case = ttk.Checkbutton(
            find_row,
            text="Match case",
            variable=self.case_var,
            command=self._on_find_case_toggle,
        )
        self.chk_case.pack(side=tk.LEFT, padx=6)

        self.chk_whole = ttk.Checkbutton(
            find_row,
            text="Whole word",
            variable=self.whole_word_var,
            command=self._on_find_case_toggle,
        )
        self.chk_whole.pack(side=tk.LEFT, padx=6)

        self.lbl_find_status = ttk.Label(find_row, text="", font=("Segoe UI", 8), foreground=theme.COLOR_TEXT_SECONDARY)
        self.lbl_find_status.pack(side=tk.LEFT, padx=8)

        ttk.Button(find_row, text="✕", width=3, command=self._hide_find_bar).pack(side=tk.RIGHT, padx=2)

        # Replace row (nested inside find_frame, toggled via btn_toggle_replace or Ctrl+H)
        self.replace_frame = ttk.Frame(self.find_frame, padding=(0, 4))

        # Spacer to align replace field under find field
        lbl_rep_spacer = ttk.Label(self.replace_frame, text="", width=10)
        lbl_rep_spacer.pack(side=tk.LEFT, padx=(0, 6))

        ttk.Label(self.replace_frame, text="Replace:").pack(side=tk.LEFT, padx=(0, 4))
        self.replace_entry = ttk.Entry(self.replace_frame, width=24)
        self.replace_entry.pack(side=tk.LEFT, padx=4)

        self.btn_replace = ttk.Button(self.replace_frame, text="Replace", command=self._replace_current)
        self.btn_replace.pack(side=tk.LEFT, padx=2)

        self.btn_replace_all = ttk.Button(self.replace_frame, text="Replace All", command=self._replace_all)
        self.btn_replace_all.pack(side=tk.LEFT, padx=2)

        self.chk_allow_header = ttk.Checkbutton(
            self.replace_frame,
            text="Also change headers and timestamps",
            variable=self.allow_header_var,
        )
        self.chk_allow_header.pack(side=tk.LEFT, padx=6)

        self.lbl_replace_hint = ttk.Label(self.replace_frame, text="", font=("Segoe UI", 8), foreground=theme.COLOR_TEXT_SECONDARY)
        self.lbl_replace_hint.pack(side=tk.LEFT, padx=8)

        # Bottom status bar (packed first so it stays pinned at bottom)
        status_frame = ttk.Frame(self, padding=(8, 4))
        status_frame.pack(fill=tk.X, side=tk.BOTTOM)

        self.status_lbl_var = tk.StringVar(value="Read mode — click 'Switch to Edit Mode' to make changes.")
        ttk.Label(status_frame, textvariable=self.status_lbl_var, font=("Segoe UI", 8), foreground=theme.COLOR_TEXT_SECONDARY).pack(side=tk.LEFT)

        ttk.Button(status_frame, text="Close", command=self._on_close_requested).pack(side=tk.RIGHT, padx=4)

        # Pack text_frame below toolbar / find_frame
        self.text_frame.pack(fill=tk.BOTH, expand=True, padx=8, pady=(2, 4))

        self._update_replace_btn_states()

    def _bind_events(self):
        self.bind("<Control-s>", lambda _e: (self.save_file(), "break")[1])
        self.bind("<Control-S>", lambda _e: (self.save_file(), "break")[1])

        # Ctrl+F / Ctrl+f to open find bar
        for w in (self, self.txt_widget, self.find_entry, self.replace_entry):
            w.bind("<Control-f>", self._on_ctrl_f)
            w.bind("<Control-F>", self._on_ctrl_f)
            w.bind("<Control-h>", self._on_ctrl_h)
            w.bind("<Control-H>", self._on_ctrl_h)

        # F3 and Shift+F3 for find next / prev
        self.bind("<F3>", lambda _e: (self._find_next(), "break")[1])
        self.bind("<Shift-F3>", lambda _e: (self._find_prev(), "break")[1])
        self.txt_widget.bind("<F3>", lambda _e: (self._find_next(), "break")[1])
        self.txt_widget.bind("<Shift-F3>", lambda _e: (self._find_prev(), "break")[1])
        self.find_entry.bind("<F3>", lambda _e: (self._find_next(), "break")[1])
        self.find_entry.bind("<Shift-F3>", lambda _e: (self._find_prev(), "break")[1])

        # Enter and Shift+Enter in find entry
        self.find_entry.bind("<Return>", lambda _e: (self._find_next(), "break")[1])
        self.find_entry.bind("<Shift-Return>", lambda _e: (self._find_prev(), "break")[1])

        # Enter in replace entry -> replace current
        self.replace_entry.bind("<Return>", lambda _e: (self._replace_current(), "break")[1])

        # Key release in find entry for live search and button states
        self.find_entry.bind("<KeyRelease>", self._on_find_change)
        self.replace_entry.bind("<KeyRelease>", lambda _e: self._update_replace_btn_states())

        # Escape closes find bar if open, else closes dialog
        self.bind("<Escape>", self._on_escape)
        self.txt_widget.bind("<Escape>", self._on_escape)
        self.find_entry.bind("<Escape>", self._on_escape)
        self.replace_entry.bind("<Escape>", self._on_escape)

        # Zoom shortcuts and gestures
        self.zoomer.bind_events(self)
        self.bind("<Control-MouseWheel>", self.zoomer._on_ctrl_mousewheel)
        self.txt_widget.bind("<Control-MouseWheel>", self.zoomer._on_ctrl_mousewheel)

        self.txt_widget.bind("<<Modified>>", self._on_text_modified)

    def _on_ctrl_f(self, _event=None):
        self._open_find_bar()
        return "break"

    def _on_ctrl_h(self, _event=None):
        self._open_replace_bar()
        return "break"

    def _on_escape(self, _event=None):
        if self.find_frame.winfo_ismapped():
            self._hide_find_bar()
            return "break"
        else:
            self._on_close_requested()
            return "break"

    def _update_title(self):
        mode_str = "Edit" if self._mode == "edit" else "Read"
        title_str = f"Transcript {'Editor' if self._mode == 'edit' else 'Viewer'} — [{mode_str}] {self.filename}"
        if self._is_dirty:
            title_str += " [modified]"
        self.title(title_str)

    def _set_dirty(self, dirty: bool):
        if self._is_dirty != dirty:
            self._is_dirty = dirty
            self._update_title()
            if self._mode == "edit":
                self.btn_save.configure(state=tk.NORMAL if self._is_dirty else tk.NORMAL)

    def _on_text_modified(self, _event=None):
        if self._mode == "edit":
            try:
                if self.txt_widget.edit_modified():
                    cur = self.txt_widget.get("1.0", "end-1c")
                    self._set_dirty(cur != self._original_content)
                    self.txt_widget.edit_modified(False)
            except Exception:
                pass

    def _toggle_mode(self):
        if self._mode == "read":
            self.set_mode("edit")
        else:
            self.set_mode("read")

    def set_mode(self, mode: str):
        if mode == "edit":
            # Guard: check if clip is currently being transcribed
            if self.stage3_tab.is_running and self.stage3_tab._currently_transcribing_clip == self.clip_name:
                messagebox.showinfo(
                    "Read-Only",
                    f"Clip '{self.clip_name}' is currently being transcribed by an active run.\n\n"
                    "Editing is disabled while transcription is writing to this file.",
                    parent=self,
                )
                return

            self._mode = "edit"
            self.txt_widget.configure(state="normal")
            self.btn_mode.configure(text="Read")
            self.btn_save.configure(state=tk.NORMAL)
            self.status_lbl_var.set("Edit mode — Ctrl+S to save, Ctrl+F to find, Ctrl+H to replace, Ctrl+Z to undo.")
            self._update_title()
            self._update_replace_btn_states()
            self.txt_widget.focus_set()
        else:
            if self._is_dirty:
                if not self.prompt_unsaved_if_dirty():
                    return
            self._mode = "read"
            self.txt_widget.configure(state="disabled")
            self.btn_mode.configure(text="Edit")
            self.btn_save.configure(state=tk.DISABLED)
            self.status_lbl_var.set("Read mode — click 'Switch to Edit Mode' to make changes.")
            self._update_title()
            self._update_replace_btn_states()

    def _update_replace_btn_states(self):
        if self._mode == "read":
            self.replace_entry.configure(state=tk.DISABLED)
            self.btn_replace.configure(state=tk.DISABLED)
            self.btn_replace_all.configure(state=tk.DISABLED)
            self.chk_allow_header.configure(state=tk.DISABLED)
            self.lbl_replace_hint.configure(text="Switch to Edit Mode to replace", foreground="#d9534f")
        else:
            self.replace_entry.configure(state=tk.NORMAL)
            self.chk_allow_header.configure(state=tk.NORMAL)
            has_query = bool(self.find_entry.get())
            state = tk.NORMAL if has_query else tk.DISABLED
            self.btn_replace.configure(state=state)
            self.btn_replace_all.configure(state=state)
            if not has_query:
                self.lbl_replace_hint.configure(text="", foreground="#555")

    def _open_find_bar(self):
        selected_text = ""
        try:
            if self.txt_widget.tag_ranges(tk.SEL):
                sel = self.txt_widget.get(tk.SEL_FIRST, tk.SEL_LAST)
                if sel and "\n" not in sel:
                    selected_text = sel
        except Exception:
            selected_text = ""

        if not self.find_frame.winfo_ismapped():
            self.find_frame.pack(fill=tk.X, padx=8, pady=(0, 4), before=self.text_frame)

        if selected_text:
            self.find_entry.delete(0, tk.END)
            self.find_entry.insert(0, selected_text)

        self.find_entry.focus_set()
        self.find_entry.selection_range(0, tk.END)
        self._update_replace_btn_states()

        if self.find_entry.get():
            self._find_match(forward=True, is_nav=False)

    def _toggle_replace_row(self):
        if self._replace_row_visible:
            self.replace_frame.pack_forget()
            self._replace_row_visible = False
            self.btn_toggle_replace.configure(text="▼ Replace")
        else:
            self.replace_frame.pack(fill=tk.X, expand=True, pady=(4, 0))
            self._replace_row_visible = True
            self.btn_toggle_replace.configure(text="▲ Replace")
            self._update_replace_btn_states()

    def _open_replace_bar(self):
        if not self.find_frame.winfo_ismapped():
            self.find_frame.pack(fill=tk.X, padx=8, pady=(0, 4), before=self.text_frame)

        if not self._replace_row_visible:
            self.replace_frame.pack(fill=tk.X, expand=True, pady=(4, 0))
            self._replace_row_visible = True
            self.btn_toggle_replace.configure(text="▲ Replace")

        selected_text = ""
        try:
            if self.txt_widget.tag_ranges(tk.SEL):
                sel = self.txt_widget.get(tk.SEL_FIRST, tk.SEL_LAST)
                if sel and "\n" not in sel:
                    selected_text = sel
        except Exception:
            selected_text = ""

        if selected_text:
            self.find_entry.delete(0, tk.END)
            self.find_entry.insert(0, selected_text)

        self._update_replace_btn_states()

        if self.find_entry.get():
            self._find_match(forward=True, is_nav=False)
            if self._mode == "edit":
                self.replace_entry.focus_set()
                self.replace_entry.selection_range(0, tk.END)
            else:
                self.find_entry.focus_set()
        else:
            self.find_entry.focus_set()
            self.find_entry.selection_range(0, tk.END)

    def _toggle_find_bar(self):
        self._open_find_bar()

    def _hide_find_bar(self):
        self.find_frame.pack_forget()
        self.replace_frame.pack_forget()
        self._replace_row_visible = False
        self.btn_toggle_replace.configure(text="▼ Replace")
        self.txt_widget.tag_remove("find_match", "1.0", tk.END)
        self.txt_widget.tag_remove("find_active", "1.0", tk.END)
        self.lbl_find_status.configure(text="")
        self.lbl_replace_hint.configure(text="")
        self._current_match_idx = -1
        self._matches = []
        self.txt_widget.focus_set()

    def _on_find_change(self, event=None):
        if event and event.keysym in (
            "Control_L", "Control_R", "Shift_L", "Shift_R", "Alt_L", "Alt_R",
            "Caps_Lock", "Return", "F3", "Escape", "Up", "Down", "Prior", "Next"
        ):
            return
        self._update_replace_btn_states()
        self._find_match(forward=True, is_nav=False)

    def _on_find_case_toggle(self):
        self._find_match(forward=True, is_nav=False)

    def _find_next(self):
        self._find_match(forward=True, is_nav=True)

    def _find_prev(self):
        self._find_match(forward=False, is_nav=True)

    def _get_protected_spans(self) -> list[tuple[str, str]]:
        r"""
        Returns list of (start_idx, end_idx) strings for protected regions.
        Protected regions:
          1. Header block at top of transcript up to first segment line.
          2. Segment line prefixes matching r'^(\[.+?\]\s*\[.+?\]\s*)'.
        """
        spans = []
        text = self.txt_widget.get("1.0", tk.END)
        lines = text.splitlines(keepends=True)

        seg_re = re.compile(r"^(\[.+?\]\s*\[.+?\]\s*)")
        first_seg_line = None
        for i, line in enumerate(lines, 1):
            if seg_re.match(line):
                first_seg_line = i
                break

        if first_seg_line is not None:
            if first_seg_line > 1:
                spans.append(("1.0", f"{first_seg_line}.0"))
            for i in range(first_seg_line, len(lines) + 1):
                line = lines[i - 1]
                m = seg_re.match(line)
                if m:
                    prefix_len = len(m.group(1))
                    spans.append((f"{i}.0", f"{i}.{prefix_len}"))
        else:
            header_keys = (
                "=== EVENT DETAILS ===", "=== TRANSCRIPT ===",
                "Event Name:", "Date:", "Organiser:", "Venue:", "Sponsors:",
                "Video File:", "Start Time:", "Speaker:", "Transcription Duration:",
                "Transcription Started:", "Transcription Completed:"
            )
            for i, line in enumerate(lines, 1):
                stripped = line.strip()
                if any(stripped.startswith(k) for k in header_keys):
                    spans.append((f"{i}.0", f"{i + 1}.0"))

        return spans

    def _is_match_protected(self, m_start: str, m_end: str, protected_spans: list[tuple[str, str]]) -> bool:
        """True if [m_start, m_end) overlaps any span in protected_spans."""
        if self.allow_header_var.get():
            return False
        for p_start, p_end in protected_spans:
            if not (self.txt_widget.compare(m_end, "<=", p_start) or self.txt_widget.compare(m_start, ">=", p_end)):
                return True
        return False

    def _replace_current(self):
        """Replace the current match and move to next match."""
        if self._mode != "edit":
            messagebox.showinfo("Read-Only", "Switch to Edit Mode to replace.", parent=self)
            return

        query = self.find_entry.get()
        if not query:
            return

        if not self._matches or self._current_match_idx < 0 or self._current_match_idx >= len(self._matches):
            self._find_match(forward=True, is_nav=False)
            if not self._matches:
                self.lbl_replace_hint.configure(text="No match to replace.", foreground="#d9534f")
                return

        target_start, target_end = self._matches[self._current_match_idx]

        # Check protected status
        protected_spans = self._get_protected_spans()
        if self._is_match_protected(target_start, target_end, protected_spans):
            self.lbl_replace_hint.configure(
                text="Skipped: match is in protected header/timestamp area.", foreground="#e67e00"
            )
            self._find_next()
            return

        replacement = self.replace_entry.get()

        # Perform replacement
        self.txt_widget.delete(target_start, target_end)
        if replacement:
            self.txt_widget.insert(target_start, replacement)

        self._set_dirty(True)
        self.lbl_replace_hint.configure(text="Replaced 1 occurrence.", foreground="#28a745")

        # Refresh matches and advance
        self._find_match(forward=True, is_nav=True)

    def _replace_all(self):
        """Replace all eligible matches in a single undo step."""
        if self._mode != "edit":
            messagebox.showinfo("Read-Only", "Switch to Edit Mode to replace.", parent=self)
            return

        query = self.find_entry.get()
        if not query:
            return

        # Ensure matches are freshly scanned
        self._find_match(forward=True, is_nav=False)
        if not self._matches:
            messagebox.showinfo("Replace All", "No matches found.", parent=self)
            return

        protected_spans = self._get_protected_spans()
        allowed_matches = []
        skipped_count = 0

        for m_start, m_end in self._matches:
            if self._is_match_protected(m_start, m_end, protected_spans):
                skipped_count += 1
            else:
                allowed_matches.append((m_start, m_end))

        if not allowed_matches:
            if skipped_count > 0:
                messagebox.showinfo(
                    "Replace All",
                    f"All {skipped_count} occurrences are in protected header/timestamp areas.\n\n"
                    "Check 'Also change headers and timestamps' to replace them.",
                    parent=self,
                )
            else:
                messagebox.showinfo("Replace All", "No matches found.", parent=self)
            return

        replacement = self.replace_entry.get()
        count = len(allowed_matches)

        if replacement:
            confirm_msg = f"Replace {count} occurrence{'s' if count != 1 else ''} of '{query}' with '{replacement}'?"
        else:
            confirm_msg = f"Delete {count} occurrence{'s' if count != 1 else ''} of '{query}'?"

        if skipped_count > 0:
            confirm_msg += f"\n\n({skipped_count} protected occurrence{'s' if skipped_count != 1 else ''} in headers/timestamps will be skipped)"

        if not messagebox.askyesno("Confirm Replace All", confirm_msg, parent=self):
            return

        # Perform replacement as a single undo step in reverse order
        self.txt_widget.configure(autoseparators=False)
        self.txt_widget.edit_separator()

        for m_start, m_end in reversed(allowed_matches):
            self.txt_widget.delete(m_start, m_end)
            if replacement:
                self.txt_widget.insert(m_start, replacement)

        self.txt_widget.edit_separator()
        self.txt_widget.configure(autoseparators=True)

        self._set_dirty(True)

        # Refresh find matches
        self._find_match(forward=True, is_nav=False)

        res_msg = f"Replaced {count} occurrence{'s' if count != 1 else ''}."
        if skipped_count > 0:
            res_msg += f" ({skipped_count} protected occurrence{'s' if skipped_count != 1 else ''} skipped)"
        self.lbl_replace_hint.configure(text=res_msg, foreground="#28a745")
        messagebox.showinfo("Replace All Complete", res_msg, parent=self)

    def _find_match(self, forward: bool = True, is_nav: bool = False):
        query = self.find_entry.get()
        self._update_replace_btn_states()

        if not query:
            self.lbl_find_status.configure(text="")
            self.txt_widget.tag_remove("find_match", "1.0", tk.END)
            self.txt_widget.tag_remove("find_active", "1.0", tk.END)
            self._current_match_idx = -1
            self._matches = []
            return

        # Clear existing tags
        self.txt_widget.tag_remove("find_match", "1.0", tk.END)
        self.txt_widget.tag_remove("find_active", "1.0", tk.END)

        nocase = not self.case_var.get()
        whole_word = self.whole_word_var.get()

        matches = []
        pos = "1.0"
        while True:
            pos = self.txt_widget.search(query, pos, stopindex=tk.END, nocase=nocase)
            if not pos:
                break
            end_pos = f"{pos}+{len(query)}c"

            if whole_word:
                is_boundary_start = True
                if self.txt_widget.compare(pos, ">", "1.0"):
                    ch_before = self.txt_widget.get(f"{pos}-1c")
                    if ch_before.isalnum() or ch_before == "_":
                        is_boundary_start = False

                is_boundary_end = True
                ch_after = self.txt_widget.get(end_pos)
                if ch_after and (ch_after.isalnum() or ch_after == "_"):
                    is_boundary_end = False

                if is_boundary_start and is_boundary_end:
                    matches.append((pos, end_pos))
                    self.txt_widget.tag_add("find_match", pos, end_pos)
            else:
                matches.append((pos, end_pos))
                self.txt_widget.tag_add("find_match", pos, end_pos)

            pos = end_pos

        self._matches = matches

        if not matches:
            self.lbl_find_status.configure(text="No matches")
            self._current_match_idx = -1
            return

        wrapped = False
        if not is_nav:
            # Locate first match at or after INSERT cursor
            try:
                cursor_pos = self.txt_widget.index(tk.INSERT)
                target_idx = 0
                for i, (m_start, m_end) in enumerate(matches):
                    if self.txt_widget.compare(m_start, ">=", cursor_pos):
                        target_idx = i
                        break
                self._current_match_idx = target_idx
            except Exception:
                self._current_match_idx = 0
        else:
            # Navigation: Next or Previous
            cursor_moved = False
            cursor_pos = "1.0"
            try:
                cursor_pos = self.txt_widget.index(tk.INSERT)
                if 0 <= self._current_match_idx < len(matches):
                    cur_start = matches[self._current_match_idx][0]
                    if self.txt_widget.compare(cursor_pos, "!=", cur_start):
                        cursor_moved = True
                else:
                    cursor_moved = True
            except Exception:
                cursor_moved = True

            if forward:
                if cursor_moved:
                    found_idx = None
                    try:
                        for i, (m_start, m_end) in enumerate(matches):
                            if self.txt_widget.compare(m_start, ">", cursor_pos):
                                found_idx = i
                                break
                    except Exception:
                        pass
                    if found_idx is not None:
                        self._current_match_idx = found_idx
                        wrapped = False
                    else:
                        self._current_match_idx = 0
                        wrapped = True
                else:
                    next_idx = self._current_match_idx + 1
                    if next_idx >= len(matches):
                        next_idx = 0
                        wrapped = True
                    self._current_match_idx = next_idx
            else:
                if cursor_moved:
                    found_idx = None
                    try:
                        for i in range(len(matches) - 1, -1, -1):
                            m_start = matches[i][0]
                            if self.txt_widget.compare(m_start, "<", cursor_pos):
                                found_idx = i
                                break
                    except Exception:
                        pass
                    if found_idx is not None:
                        self._current_match_idx = found_idx
                        wrapped = False
                    else:
                        self._current_match_idx = len(matches) - 1
                        wrapped = True
                else:
                    prev_idx = self._current_match_idx - 1
                    if prev_idx < 0:
                        prev_idx = len(matches) - 1
                        wrapped = True
                    self._current_match_idx = prev_idx

        if self._current_match_idx < 0 or self._current_match_idx >= len(matches):
            self._current_match_idx = 0

        target_start, target_end = matches[self._current_match_idx]
        self.txt_widget.tag_add("find_active", target_start, target_end)
        self.txt_widget.mark_set(tk.INSERT, target_start)
        self.txt_widget.see(target_start)

        status_text = f"{self._current_match_idx + 1} of {len(matches)}"
        if wrapped:
            status_text += " (wrapped)"
        self.lbl_find_status.configure(text=status_text)

    def _check_headers(self, content: str) -> bool:
        missing = []
        if "Transcription Completed:" not in content:
            missing.append("Transcription Completed: line")
        if "Speaker:" not in content:
            missing.append("Speaker: line")
        if "Video File:" not in content and "=== TRANSCRIPT ===" not in content:
            missing.append("Clip / transcript header")

        if missing:
            msg = (
                "Critical header elements appear to be missing or damaged:\n\n"
                + "\n".join(f"• {m}" for m in missing)
                + "\n\n"
                "Damaging these header lines may prevent Session Scribe or downstream tools "
                "from recognizing this file as a completed transcript or identifying its speaker.\n\n"
                "Do you want to save anyway?"
            )
            return messagebox.askyesno("Header Warning", msg, parent=self)
        return True

    def _check_disk_conflict(self) -> str:
        """
        Check if target file changed on disk while viewer was open.
        Returns: 'ok', 'save_anyway', 'reload', or 'cancel'.
        """
        if not os.path.isfile(self.target_txt):
            return "ok"
        current_hash = compute_transcript_hash(self.target_txt)
        if current_hash == self._opened_content_hash:
            return "ok"

        # Conflict detected!
        dialog = tk.Toplevel(self)
        dialog.title("File Conflict Detected")
        theme.center_dialog(dialog, self, width=560, height=280, min_width=480, min_height=250)
        dialog.transient(self)
        dialog.grab_set()
        dialog.resizable(True, True)

        choice = tk.StringVar(value="cancel")

        def choose(val):
            choice.set(val)
            dialog.destroy()

        dialog.bind("<Escape>", lambda _e: choose("cancel"))

        btn_row = ttk.Frame(dialog)
        btn_row.pack(fill=tk.X, padx=14, pady=12, side=tk.BOTTOM)

        ttk.Button(btn_row, text="Cancel", command=lambda: choose("cancel")).pack(side=tk.RIGHT, padx=4)
        ttk.Button(btn_row, text="Save Anyway", command=lambda: choose("save_anyway")).pack(side=tk.RIGHT, padx=4)
        ttk.Button(btn_row, text="Reload from Disk", command=lambda: choose("reload")).pack(side=tk.RIGHT, padx=4)

        ttk.Label(
            dialog,
            text="⚠  File Changed on Disk",
            font=("Segoe UI", 10, "bold"),
            foreground="#d9534f",
        ).pack(anchor=tk.W, padx=14, pady=(14, 4))

        lbl_conflict_msg = ttk.Label(
            dialog,
            text=(
                f"The transcript file on disk has changed since you opened it:\n"
                f"  {self.target_txt}\n\n"
                "This can occur if the clip was re-transcribed or modified externally.\n\n"
                "• Reload: Discard your unsaved edits and reload from disk\n"
                "• Save anyway: Overwrite the changes on disk with your edits\n"
                "• Cancel: Return to editing without saving"
            ),
            font=("Segoe UI", 9),
            justify=tk.LEFT,
            wraplength=520,
        )
        lbl_conflict_msg.pack(anchor=tk.W, fill=tk.BOTH, expand=True, padx=14, pady=4)

        def _on_conflict_cfg(e):
            if e.widget == dialog and lbl_conflict_msg.winfo_exists():
                lbl_conflict_msg.configure(wraplength=max(200, e.width - 28))
        dialog.bind("<Configure>", _on_conflict_cfg)

        self.wait_window(dialog)
        return choice.get()

    def save_file(self) -> bool:
        if self._mode != "edit":
            return True

        content = self.txt_widget.get("1.0", "end-1c")

        # 1. Disk conflict check
        conflict_res = self._check_disk_conflict()
        if conflict_res == "cancel":
            return False
        elif conflict_res == "reload":
            try:
                with open(self.target_txt, "rb") as fh:
                    raw = fh.read()
                self._original_content = raw.decode("utf-8", errors="replace")
                self.txt_widget.delete("1.0", tk.END)
                self.txt_widget.insert(tk.END, self._original_content)
                self._opened_content_hash = compute_transcript_hash(self.target_txt)
                self._set_dirty(False)
                messagebox.showinfo("Reloaded", "Transcript reloaded from disk.", parent=self)
            except OSError as exc:
                messagebox.showerror("Error", f"Could not reload file:\n{exc}", parent=self)
            return True

        # 2. Header check
        if not self._check_headers(content):
            return False

        # 3. First save original backup to scribe\\state\\originals\\<stem>.txt
        orig_dir = os.path.join(self.paths["state"], "originals")
        os.makedirs(orig_dir, exist_ok=True)
        orig_file = os.path.join(orig_dir, f"{self.stem}.txt")
        if not os.path.isfile(orig_file):
            if os.path.isfile(self.target_txt):
                try:
                    shutil.copy2(self.target_txt, orig_file)
                except OSError:
                    pass

        # 4. Atomic write with newline and UTF-8 preservation
        # If alias map was used for decrypted display, map names back to aliases for compute-zone disk storage
        if hasattr(self, "_alias_to_name_map") and self._alias_to_name_map:
            reverse_map = {name: alias for alias, name in self._alias_to_name_map.items() if alias and name}
            content, _ = substitute_line_start_speaker_tags(content, reverse_map)

        content_normalized = content.replace("\r\n", "\n")
        if self._newline == "\r\n":
            content_to_write = content_normalized.replace("\n", "\r\n")
        else:
            content_to_write = content_normalized

        tmp_path = self.target_txt + ".tmp"
        try:
            with open(tmp_path, "wb") as fh:
                fh.write(content_to_write.encode("utf-8"))
            os.replace(tmp_path, self.target_txt)
        except OSError as exc:
            messagebox.showerror("Save Error", f"Could not write to {self.target_txt}:\n{exc}", parent=self)
            return False

        # 5. Consequences of save
        now_str = datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S IST")
        set_clip_edited(self.paths["state"], self.stem, edited=True, edited_at=now_str)
        clear_multiple_clip_approvals(self.paths["state"], [self.stem])
        set_combined_stale(self.paths["state"], True)

        self._opened_content_hash = compute_transcript_hash(self.target_txt)
        self._original_content = content
        self._set_dirty(False)

        self.stage3_tab._refresh_status_column()
        self.stage3_tab._update_combine_and_approval_ui()

        messagebox.showinfo(
            "Saved",
            f"Transcript for '{self.clip_name}' saved successfully.\n\n"
            "• Manual edits saved to disk.\n"
            "• Approval for this clip has been cleared.\n"
            "• Existing combined files are marked stale.\n\n"
            "Please review and re-approve when ready.",
            parent=self,
        )
        return True

    def prompt_unsaved_if_dirty(self) -> bool:
        """
        Prompt user if there are unsaved edits.
        Returns:
          True  -> safe to proceed (saved or discarded).
          False -> user clicked Cancel; keep user in place.
        """
        if not self._is_dirty:
            return True

        ans = messagebox.askyesnocancel(
            "Unsaved Changes",
            f"You have unsaved changes in '{self.filename}'.\n\n"
            "• Click Yes to Save changes\n"
            "• Click No to Discard changes\n"
            "• Click Cancel to Stay here",
            parent=self,
        )
        if ans is None:
            # Cancel -> stay where they are
            return False
        elif ans is True:
            # Yes -> Save
            return self.save_file()
        else:
            # No -> Discard
            self._is_dirty = False
            return True

    def _on_close_requested(self):
        if not self.prompt_unsaved_if_dirty():
            return
        if self.stage3_tab._active_viewer is self:
            self.stage3_tab._active_viewer = None
        self.destroy()


# ---------------------------------------------------------------------------
# Re-transcribe with Model Choice Dialog (Part D)
# ---------------------------------------------------------------------------

class ModeCard(tk.Frame):
    """Segmented selection card for Replace vs Compare execution mode."""
    def __init__(
        self,
        parent: tk.Misc,
        mode_key: str,
        title: str,
        subtitle: str,
        is_selected: bool,
        on_select: Callable[[str], None],
    ):
        super().__init__(
            parent,
            bg=theme.COLOR_CARD_BG,
            highlightbackground=theme.COLOR_PRIMARY_BTN if is_selected else theme.COLOR_BORDER,
            highlightthickness=2 if is_selected else 1,
            padx=12,
            pady=10,
            cursor="hand2",
            takefocus=1,
        )
        self.mode_key = mode_key
        self.on_select = on_select
        self.is_selected = is_selected

        top_row = tk.Frame(self, bg=theme.COLOR_CARD_BG)
        top_row.pack(fill=tk.X)

        self.indicator_canvas = tk.Canvas(top_row, width=20, height=20, bg=theme.COLOR_CARD_BG, highlightthickness=0)
        self.indicator_canvas.pack(side=tk.LEFT, padx=(0, 8))
        self._draw_indicator()

        self.lbl_title = tk.Label(
            top_row,
            text=title,
            font=("Segoe UI", 10, "bold"),
            bg=theme.COLOR_CARD_BG,
            fg=theme.COLOR_TEXT_PRIMARY,
        )
        self.lbl_title.pack(side=tk.LEFT, anchor=tk.W)

        self.lbl_sub = tk.Label(
            self,
            text=subtitle,
            font=("Segoe UI", 9),
            bg=theme.COLOR_CARD_BG,
            fg=theme.COLOR_TEXT_SECONDARY,
            wraplength=280,
            justify=tk.LEFT,
        )
        self.lbl_sub.pack(fill=tk.X, anchor=tk.W, pady=(4, 0))

        for w in (self, top_row, self.indicator_canvas, self.lbl_title, self.lbl_sub):
            w.bind("<Button-1>", lambda _e: self.on_select(self.mode_key))
            w.bind("<Enter>", self._on_enter)
            w.bind("<Leave>", self._on_leave)

        self.bind("<FocusIn>", self._on_focus_in)
        self.bind("<FocusOut>", self._on_focus_out)
        self.bind("<space>", lambda _e: self.on_select(self.mode_key))
        self.bind("<Return>", lambda _e: self.on_select(self.mode_key))

    def _draw_indicator(self):
        self.indicator_canvas.delete("all")
        if self.is_selected:
            self.indicator_canvas.create_oval(2, 2, 18, 18, fill=theme.COLOR_PRIMARY_BTN, outline=theme.COLOR_PRIMARY_BTN)
            self.indicator_canvas.create_line(6, 10, 9, 13, 14, 6, fill="white", width=2)
        else:
            self.indicator_canvas.create_oval(2, 2, 18, 18, fill=theme.COLOR_CARD_BG, outline=theme.COLOR_BORDER, width=2)

    def set_selected(self, selected: bool):
        self.is_selected = selected
        self.configure(
            highlightbackground=theme.COLOR_PRIMARY_BTN if selected else theme.COLOR_BORDER,
            highlightthickness=2 if selected else 1,
        )
        self._draw_indicator()

    def _on_enter(self, _e=None):
        if not self.is_selected:
            self.configure(highlightbackground=theme.COLOR_BORDER_FOCUS or theme.COLOR_PRIMARY_BTN)

    def _on_leave(self, _e=None):
        if not self.is_selected:
            self.configure(highlightbackground=theme.COLOR_BORDER)

    def _on_focus_in(self, _e=None):
        self.configure(highlightcolor=theme.COLOR_PRIMARY_BTN, highlightbackground=theme.COLOR_PRIMARY_BTN, highlightthickness=2)

    def _on_focus_out(self, _e=None):
        self.configure(highlightthickness=2 if self.is_selected else 1, highlightbackground=theme.COLOR_PRIMARY_BTN if self.is_selected else theme.COLOR_BORDER)


class ModelCard(tk.Frame):
    """
    Selectable model row card with crisp engine icons, 22px indicator,
    chips for VRAM/Speed/Cost, and disabled 'Coming Soon' state.
    """
    def __init__(
        self,
        parent: tk.Misc,
        model_info: model_registry.ModelInfo,
        is_selected: bool,
        on_select: Callable[[str], None],
        on_navigate: Optional[Callable[[str], None]] = None,
    ):
        self.model_info = model_info
        self.is_selected = is_selected
        self.is_disabled = model_info.is_disabled_template
        self.on_select = on_select
        self.on_navigate = on_navigate

        bg_col = theme.COLOR_APP_BG if self.is_disabled else theme.COLOR_CARD_BG
        border_col = theme.COLOR_PRIMARY_BTN if (self.is_selected and not self.is_disabled) else theme.COLOR_BORDER
        bd_w = 2 if (self.is_selected and not self.is_disabled) else 1

        super().__init__(
            parent,
            bg=bg_col,
            highlightbackground=border_col,
            highlightthickness=bd_w,
            highlightcolor=theme.COLOR_PRIMARY_BTN,
            padx=12,
            pady=10,
            cursor="arrow" if self.is_disabled else "hand2",
            takefocus=0 if self.is_disabled else 1,
        )

        # 1. Left Indicator Canvas (22x22)
        self.ind_canvas = tk.Canvas(self, width=22, height=22, bg=bg_col, highlightthickness=0)
        self.ind_canvas.pack(side=tk.LEFT, padx=(0, 10))
        self._draw_indicator()

        # 2. Engine Icon Canvas (28x28)
        self.icon_canvas = tk.Canvas(self, width=28, height=28, bg=bg_col, highlightthickness=0)
        self.icon_canvas.pack(side=tk.LEFT, padx=(0, 12))
        self._draw_engine_icon()

        # 3. Text and Chips Area
        info_frame = tk.Frame(self, bg=bg_col)
        info_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # Top line: Name + optional Coming Soon badge
        name_line = tk.Frame(info_frame, bg=bg_col)
        name_line.pack(fill=tk.X, anchor=tk.W)

        fg_title = theme.COLOR_TEXT_MUTED if self.is_disabled else theme.COLOR_TEXT_PRIMARY
        self.lbl_name = tk.Label(
            name_line,
            text=model_info.name,
            font=("Segoe UI", 10, "bold"),
            bg=bg_col,
            fg=fg_title,
        )
        self.lbl_name.pack(side=tk.LEFT)

        if self.is_disabled:
            badge = tk.Label(
                name_line,
                text="Coming soon",
                font=("Segoe UI", 8, "bold"),
                bg="#382806" if theme.PALETTE_DARK["card_bg"] == theme.COLOR_CARD_BG else "#FEF3C7",
                fg="#F59E0B" if theme.PALETTE_DARK["card_bg"] == theme.COLOR_CARD_BG else "#B45309",
                padx=6,
                pady=1,
                relief="flat",
            )
            badge.pack(side=tk.LEFT, padx=8)

        # Middle line: Description
        fg_desc = theme.COLOR_TEXT_MUTED if self.is_disabled else theme.COLOR_TEXT_SECONDARY
        self.lbl_desc = tk.Label(
            info_frame,
            text=model_info.description,
            font=("Segoe UI", 9),
            bg=bg_col,
            fg=fg_desc,
            anchor=tk.W,
            justify=tk.LEFT,
        )
        self.lbl_desc.pack(fill=tk.X, pady=(2, 6))

        # Bottom line: Chips row (VRAM, Speed, Cost)
        chips_row = tk.Frame(info_frame, bg=bg_col)
        chips_row.pack(fill=tk.X, anchor=tk.W)

        chip_bg = theme.COLOR_ROW_ODD if not self.is_disabled else bg_col
        chip_fg = theme.COLOR_TEXT_SECONDARY if not self.is_disabled else theme.COLOR_TEXT_MUTED

        # Chip 1: VRAM / Resource
        vram_str = f"VRAM: {model_info.vram_mb} MB" if model_info.provider == "local" else "Cloud Hosted"
        self._create_chip(chips_row, vram_str, chip_bg, chip_fg)

        # Chip 2: Speed
        speed_str = f"Speed: ~{model_info.speed_factor:.1f}x RT" if model_info.speed_factor > 0 else "Speed: Fast"
        self._create_chip(chips_row, speed_str, chip_bg, chip_fg)

        # Chip 3: Cost
        cost_str = "Cost: Free (Local)" if model_info.cost_per_minute_usd == 0.0 else f"Cost: ${model_info.cost_per_minute_usd:.3f}/min"
        self._create_chip(chips_row, cost_str, chip_bg, chip_fg)

        # Bind clicks & hover across all children
        if not self.is_disabled:
            for w in (self, self.ind_canvas, self.icon_canvas, info_frame, name_line, self.lbl_name, self.lbl_desc, chips_row):
                w.bind("<Button-1>", lambda _e: self.on_select(self.model_info.id))
                w.bind("<Enter>", self._on_enter)
                w.bind("<Leave>", self._on_leave)

            self.bind("<FocusIn>", self._on_focus_in)
            self.bind("<FocusOut>", self._on_focus_out)
            self.bind("<space>", lambda _e: self.on_select(self.model_info.id))
            self.bind("<Return>", lambda _e: self.on_select(self.model_info.id))
            if self.on_navigate:
                self.bind("<Up>", lambda _e: self.on_navigate("up"))
                self.bind("<Down>", lambda _e: self.on_navigate("down"))

    def _create_chip(self, parent: tk.Widget, text: str, bg: str, fg: str):
        lbl = tk.Label(
            parent,
            text=text,
            font=("Segoe UI", 8),
            bg=bg,
            fg=fg,
            padx=6,
            pady=2,
            relief="solid",
            bd=1,
            highlightthickness=0,
        )
        lbl.pack(side=tk.LEFT, padx=(0, 6))
        if not self.is_disabled:
            lbl.bind("<Button-1>", lambda _e: self.on_select(self.model_info.id))

    def _draw_indicator(self):
        self.ind_canvas.delete("all")
        bg_col = theme.COLOR_APP_BG if self.is_disabled else theme.COLOR_CARD_BG
        if self.is_disabled:
            self.ind_canvas.create_oval(2, 2, 20, 20, outline=theme.COLOR_TEXT_MUTED, width=1, dash=(2, 2))
        elif self.is_selected:
            self.ind_canvas.create_oval(2, 2, 20, 20, fill=theme.COLOR_PRIMARY_BTN, outline=theme.COLOR_PRIMARY_BTN)
            self.ind_canvas.create_line(6, 11, 10, 15, 16, 7, fill="white", width=2)
        else:
            self.ind_canvas.create_oval(2, 2, 20, 20, fill=bg_col, outline=theme.COLOR_BORDER, width=2)

    def _draw_engine_icon(self):
        self.icon_canvas.delete("all")
        col = theme.COLOR_TEXT_MUTED if self.is_disabled else theme.COLOR_PRIMARY_BTN
        if self.model_info.provider == "local":
            # Local GPU silicon chip
            self.icon_canvas.create_rectangle(6, 6, 22, 22, outline=col, width=2)
            self.icon_canvas.create_rectangle(10, 10, 18, 18, fill=col, outline=col)
            # Pins
            for p in (9, 14, 19):
                self.icon_canvas.create_line(p, 2, p, 6, fill=col, width=2)
                self.icon_canvas.create_line(p, 22, p, 26, fill=col, width=2)
                self.icon_canvas.create_line(2, p, 6, p, fill=col, width=2)
                self.icon_canvas.create_line(22, p, 26, p, fill=col, width=2)
        else:
            # NVIDIA Cloud shape with network nodes
            self.icon_canvas.create_oval(4, 12, 14, 22, fill=col, outline=col)
            self.icon_canvas.create_oval(10, 6, 20, 18, fill=col, outline=col)
            self.icon_canvas.create_oval(16, 11, 25, 22, fill=col, outline=col)
            self.icon_canvas.create_rectangle(9, 14, 21, 22, fill=col, outline=col)
            # Center lightning or dot
            self.icon_canvas.create_oval(12, 13, 16, 17, fill="white", outline="white")

    def set_selected(self, selected: bool):
        self.is_selected = selected
        if not self.is_disabled:
            border_col = theme.COLOR_PRIMARY_BTN if selected else theme.COLOR_BORDER
            bd_w = 2 if selected else 1
            self.configure(highlightbackground=border_col, highlightthickness=bd_w)
        self._draw_indicator()

    def _on_enter(self, _e=None):
        if not self.is_disabled and not self.is_selected:
            self.configure(highlightbackground=theme.COLOR_BORDER_FOCUS or theme.COLOR_PRIMARY_BTN)

    def _on_leave(self, _e=None):
        if not self.is_disabled and not self.is_selected:
            self.configure(highlightbackground=theme.COLOR_BORDER)

    def _on_focus_in(self, _e=None):
        if not self.is_disabled:
            self.configure(highlightcolor=theme.COLOR_PRIMARY_BTN, highlightbackground=theme.COLOR_PRIMARY_BTN, highlightthickness=2)

    def _on_focus_out(self, _e=None):
        if not self.is_disabled:
            self.configure(highlightthickness=2 if self.is_selected else 1, highlightbackground=theme.COLOR_PRIMARY_BTN if self.is_selected else theme.COLOR_BORDER)


class ReTranscribeDialog(tk.Toplevel):
    """
    Dialog for re-transcribing selected clips with choice of model
    and Replace vs Compare execution mode.
    Redesigned with rich selectable cards, sticky footer, live progress,
    and universal smooth touch/mouse scrolling.
    """

    def __init__(
        self,
        parent: tk.Misc,
        clips: List[Dict[str, Any]],
        on_start_callback: Callable[..., None],
        event_root: str = "",
    ):
        super().__init__(parent)
        self.title("Re-transcribe with Model Choice")
        theme.style_toplevel(self)
        theme.center_dialog(self, parent, width=720, height=640)
        self.minsize(680, 560)
        self.resizable(True, True)
        self.transient(parent.winfo_toplevel())
        self.grab_set()

        self.clips = clips
        self.on_start_callback = on_start_callback
        self.event_root = event_root

        total_dur = sum(c.get("duration", 0.0) for c in clips)
        self.total_dur_sec = total_dur

        self.mode_var = tk.StringVar(value="compare")
        self.model_var = tk.StringVar(value="whisper-medium")
        self.rate_warn_var = tk.StringVar(value="")
        self.est_info_var = tk.StringVar(value="")
        self.progress_msg_var = tk.StringVar(value="")
        self.is_transcribing = False

        self._mode_cards: Dict[str, ModeCard] = {}
        self._model_cards: Dict[str, ModelCard] = {}

        self._build_ui()
        self._update_estimates()
        self.bind("<Escape>", lambda _e: self._on_escape())

    def _on_escape(self):
        if not self.is_transcribing:
            self.destroy()

    def _build_ui(self):
        # ------------------------------------------------------------------
        # 1. Sticky Footer (Packed at bottom FIRST so it's always visible)
        # ------------------------------------------------------------------
        self.footer_frame = tk.Frame(self, bg=theme.COLOR_CARD_BG, padx=16, pady=12, highlightthickness=1, highlightbackground=theme.COLOR_BORDER)
        self.footer_frame.pack(side=tk.BOTTOM, fill=tk.X)

        # Rate-limit Warning bar inside footer
        self.warn_bar = theme.MessageBar(self.footer_frame, textvariable=self.rate_warn_var, kind="warning")
        self.warn_bar.pack(fill=tk.X, pady=(0, 6))

        # Telemetry Estimate info bar
        self.est_bar = theme.MessageBar(self.footer_frame, textvariable=self.est_info_var, kind="info")
        self.est_bar.pack(fill=tk.X, pady=(0, 8))

        # Live Progress Area (hidden until transcribing)
        self.progress_frame = tk.Frame(self.footer_frame, bg=theme.COLOR_CARD_BG)
        self.lbl_progress = tk.Label(
            self.progress_frame,
            textvariable=self.progress_msg_var,
            font=("Segoe UI", 9, "bold"),
            bg=theme.COLOR_CARD_BG,
            fg=theme.COLOR_PRIMARY_BTN,
        )
        self.lbl_progress.pack(anchor=tk.W, pady=(0, 4))
        self.progress_bar = ttk.Progressbar(self.progress_frame, mode="determinate")
        self.progress_bar.pack(fill=tk.X)

        # Action Buttons row
        self.btn_box = tk.Frame(self.footer_frame, bg=theme.COLOR_CARD_BG)
        self.btn_box.pack(fill=tk.X, pady=(4, 0))

        self.btn_start = ttk.Button(
            self.btn_box,
            text="Start Re-transcribe",
            style="Primary.TButton",
            command=self._on_start_clicked,
        )
        self.btn_start.pack(side=tk.RIGHT, padx=4)

        self.btn_cancel = ttk.Button(self.btn_box, text="Cancel", command=self.destroy)
        self.btn_cancel.pack(side=tk.RIGHT, padx=4)

        # ------------------------------------------------------------------
        # 2. Central Scrollable Body with Universal Scrolling & Drag Pan
        # ------------------------------------------------------------------
        self.canvas = tk.Canvas(self, borderwidth=0, highlightthickness=0, bg=theme.COLOR_APP_BG)
        self.vscroll = ttk.Scrollbar(self, orient=tk.VERTICAL, command=self.canvas.yview)
        self.inner = ttk.Frame(self.canvas, padding=16)

        self.inner.bind(
            "<Configure>",
            lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")),
        )
        self.canvas_win = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.canvas.configure(yscrollcommand=self.vscroll.set)

        self._last_dialog_width = -1
        def _on_dlg_configure(e):
            if e.widget != self:
                return
            new_w = max(400, e.width - 24)
            if abs(new_w - self._last_dialog_width) > 1:
                self._last_dialog_width = new_w
                self.canvas.itemconfig(self.canvas_win, width=new_w)

        self.bind("<Configure>", _on_dlg_configure)

        self.canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.vscroll.pack(side=tk.RIGHT, fill=tk.Y)

        # Attach universal scroller: precision touchpad accumulator, Shift-wheel, and touch/mouse drag pan
        self._scroller = attach_universal_scrolling(
            canvas=self.canvas,
            content_widget=self.inner,
            toplevel=self,
            enable_drag_pan=True,
            drag_threshold=6,
        )

        # ------------------------------------------------------------------
        # 3. Content Sections inside Inner Frame
        # ------------------------------------------------------------------

        # Section 1: Selection Summary Card
        summary_card = tk.Frame(
            self.inner,
            bg=theme.COLOR_CARD_BG,
            highlightbackground=theme.COLOR_BORDER,
            highlightthickness=1,
            padx=14,
            pady=12,
        )
        summary_card.pack(fill=tk.X, pady=(0, 12))
        self._scroller.register_drag_widget(summary_card)

        tk.Label(
            summary_card,
            text="Selection Summary",
            font="SS_Section",
            bg=theme.COLOR_CARD_BG,
            fg=theme.COLOR_TEXT_PRIMARY,
        ).pack(anchor=tk.W, pady=(0, 4))

        if len(self.clips) == 1:
            c0 = self.clips[0]
            c_name = c0.get("name", "Clip")
            spk = c0.get("speaker", "Speaker")
            file_size_str = self._resolve_file_size(c0)
            dur_str = format_duration_hhmmss(self.total_dur_sec)

            tk.Label(
                summary_card,
                text=f"{c_name} · Speaker: {spk}",
                font=("Segoe UI", 10, "bold"),
                bg=theme.COLOR_CARD_BG,
                fg=theme.COLOR_TEXT_PRIMARY,
            ).pack(anchor=tk.W)

            tk.Label(
                summary_card,
                text=f"Duration: {dur_str} ({self.total_dur_sec:.1f}s)   |   File Size on Disk: {file_size_str}",
                font=("Segoe UI", 9),
                bg=theme.COLOR_CARD_BG,
                fg=theme.COLOR_TEXT_SECONDARY,
            ).pack(anchor=tk.W, pady=(2, 0))
        else:
            dur_str = format_duration_hhmmss(self.total_dur_sec)
            c_names = ", ".join(c.get("name", "") for c in self.clips[:3])
            if len(self.clips) > 3:
                c_names += f" and {len(self.clips) - 3} more"

            tk.Label(
                summary_card,
                text=f"{len(self.clips)} clips selected: {c_names}",
                font=("Segoe UI", 10, "bold"),
                bg=theme.COLOR_CARD_BG,
                fg=theme.COLOR_TEXT_PRIMARY,
                wraplength=620,
            ).pack(anchor=tk.W)

            tk.Label(
                summary_card,
                text=f"Total Combined Duration: {dur_str} ({self.total_dur_sec:.1f}s)",
                font=("Segoe UI", 9),
                bg=theme.COLOR_CARD_BG,
                fg=theme.COLOR_TEXT_SECONDARY,
            ).pack(anchor=tk.W, pady=(2, 0))

        # Section 2: Execution Mode Segmented Control
        mode_sec = tk.Frame(
            self.inner,
            bg=theme.COLOR_CARD_BG,
            highlightbackground=theme.COLOR_BORDER,
            highlightthickness=1,
            padx=14,
            pady=12,
        )
        mode_sec.pack(fill=tk.X, pady=(0, 12))
        self._scroller.register_drag_widget(mode_sec)

        tk.Label(
            mode_sec,
            text="Execution Mode",
            font="SS_Section",
            bg=theme.COLOR_CARD_BG,
            fg=theme.COLOR_TEXT_PRIMARY,
        ).pack(anchor=tk.W, pady=(0, 8))

        modes_grid = tk.Frame(mode_sec, bg=theme.COLOR_CARD_BG)
        modes_grid.pack(fill=tk.X)
        modes_grid.columnconfigure(0, weight=1)
        modes_grid.columnconfigure(1, weight=1)

        c_card = ModeCard(
            modes_grid,
            mode_key="compare",
            title="Compare (Recommended)",
            subtitle="Keep previous runs, add comparison run, and view side-by-side diff in drawer",
            is_selected=(self.mode_var.get() == "compare"),
            on_select=self._on_mode_selected,
        )
        c_card.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        self._mode_cards["compare"] = c_card

        r_card = ModeCard(
            modes_grid,
            mode_key="replace",
            title="Replace",
            subtitle="Overwrite current transcript and active telemetry (prior version archived)",
            is_selected=(self.mode_var.get() == "replace"),
            on_select=self._on_mode_selected,
        )
        r_card.grid(row=0, column=1, sticky="nsew", padx=(6, 0))
        self._mode_cards["replace"] = r_card

        # Section 3: Model & Engine Selection
        models_container = tk.Frame(
            self.inner,
            bg=theme.COLOR_CARD_BG,
            highlightbackground=theme.COLOR_BORDER,
            highlightthickness=1,
            padx=14,
            pady=12,
        )
        models_container.pack(fill=tk.X, pady=(0, 12))
        self._scroller.register_drag_widget(models_container)

        tk.Label(
            models_container,
            text="Model & Engine Selection",
            font="SS_Section",
            bg=theme.COLOR_CARD_BG,
            fg=theme.COLOR_TEXT_PRIMARY,
        ).pack(anchor=tk.W, pady=(0, 8))

        # Group A: Local on this PC
        tk.Label(
            models_container,
            text="Local on this PC (Runs on GPU, fits 4 GB VRAM):",
            font=("Segoe UI", 9, "bold"),
            bg=theme.COLOR_CARD_BG,
            fg=theme.COLOR_TEXT_PRIMARY,
        ).pack(anchor=tk.W, pady=(4, 6))

        for m in model_registry.get_local_models(max_vram_mb=4096):
            m_card = ModelCard(
                models_container,
                model_info=m,
                is_selected=(self.model_var.get() == m.id),
                on_select=self._on_model_selected,
                on_navigate=self._on_model_keyboard_nav,
            )
            m_card.pack(fill=tk.X, pady=3)
            self._model_cards[m.id] = m_card
            self._scroller.register_drag_widget(m_card)

        # Group B: NVIDIA Cloud
        tk.Label(
            models_container,
            text="NVIDIA Cloud (Hosted Riva gRPC):",
            font=("Segoe UI", 9, "bold"),
            bg=theme.COLOR_CARD_BG,
            fg=theme.COLOR_TEXT_PRIMARY,
        ).pack(anchor=tk.W, pady=(12, 6))

        for m in model_registry.get_cloud_models(include_templates=True):
            m_card = ModelCard(
                models_container,
                model_info=m,
                is_selected=(self.model_var.get() == m.id),
                on_select=self._on_model_selected,
                on_navigate=self._on_model_keyboard_nav,
            )
            m_card.pack(fill=tk.X, pady=3)
            self._model_cards[m.id] = m_card
            self._scroller.register_drag_widget(m_card)

    def _resolve_file_size(self, clip_dict: Dict[str, Any]) -> str:
        cand = [
            clip_dict.get("path", ""),
            os.path.join(self.event_root, "data", "Videos", clip_dict.get("name", "")),
            os.path.join(self.event_root, "Videos", clip_dict.get("name", "")),
            os.path.join(self.event_root, "data", clip_dict.get("name", "")),
            os.path.join(self.event_root, clip_dict.get("name", "")),
        ]
        for p in cand:
            if p and os.path.isfile(p):
                try:
                    sz = os.path.getsize(p)
                    if sz > 1024 * 1024 * 1024:
                        return f"{sz / (1024**3):.2f} GB"
                    elif sz > 1024 * 1024:
                        return f"{sz / (1024**2):.1f} MB"
                    else:
                        return f"{sz / 1024:.1f} KB"
                except Exception:
                    pass
        return "Audio ~40 MB"

    def _on_mode_selected(self, mode_key: str):
        self.mode_var.set(mode_key)
        for k, card in self._mode_cards.items():
            card.set_selected(k == mode_key)
        self._update_estimates()

    def _on_model_selected(self, model_id: str):
        self.model_var.set(model_id)
        for mid, card in self._model_cards.items():
            card.set_selected(mid == model_id)
        self._update_estimates()

    def _on_model_keyboard_nav(self, direction: str):
        selectable_ids = [mid for mid, c in self._model_cards.items() if not c.is_disabled]
        curr = self.model_var.get()
        if curr not in selectable_ids:
            if selectable_ids:
                self._on_model_selected(selectable_ids[0])
            return
        idx = selectable_ids.index(curr)
        if direction == "up" and idx > 0:
            target = selectable_ids[idx - 1]
            self._on_model_selected(target)
            self._model_cards[target].focus_set()
        elif direction == "down" and idx < len(selectable_ids) - 1:
            target = selectable_ids[idx + 1]
            self._on_model_selected(target)
            self._model_cards[target].focus_set()

    def _update_estimates(self):
        m_id = self.model_var.get()
        m = model_registry.get_model(m_id)

        # Update visual card states if model_var changed externally
        for mid, card in self._model_cards.items():
            card.set_selected(mid == m_id)

        if not m:
            self.est_info_var.set("Please select a transcription model above.")
            self.btn_start.configure(state=tk.DISABLED)
            return

        est = model_registry.estimate_run_cost(m_id, self.total_dur_sec)
        cost_txt = f"Cost: ${est['cost_usd']:.4f} (Cloud)" if m.provider == "nvidia" else "Cost: Free (Local GPU)"
        est_time_str = format_duration_hhmmss(est["estimated_time_sec"])

        self.est_info_var.set(
            f"Estimated Time: ~{est_time_str} ({est['speedup']:.1f}x real-time) · {cost_txt} · Provider: {m.provider.upper()}"
        )

        warn = model_registry.check_rate_limit_warning(m_id, self.total_dur_sec)
        if warn:
            self.rate_warn_var.set(warn)
            self.warn_bar.pack(fill=tk.X, pady=(0, 6))
        else:
            self.rate_warn_var.set("")
            self.warn_bar.pack_forget()

        if m.is_disabled_template:
            self.btn_start.configure(state=tk.DISABLED)
        else:
            self.btn_start.configure(state=tk.NORMAL)

    def _on_start_clicked(self):
        mode = self.mode_var.get()
        model_id = self.model_var.get()
        m_info = model_registry.get_model(model_id)
        if not m_info or m_info.is_disabled_template:
            return

        self.is_transcribing = True
        self.btn_start.configure(state=tk.DISABLED)
        self.btn_cancel.configure(state=tk.DISABLED)
        self.progress_frame.pack(fill=tk.X, pady=(4, 6), before=self.btn_box)
        self.progress_msg_var.set("Preparing audio...")
        self.progress_bar.configure(value=5.0)

        t_start = time.monotonic()

        def _update_timer():
            if not getattr(self, "is_transcribing", False):
                return
            elap = int(time.monotonic() - t_start)
            h, rem = divmod(elap, 3600)
            m, s = divmod(rem, 60)
            try:
                self.btn_start.configure(text=f"⏳ Transcribing… {h:02d}:{m:02d}:{s:02d}")
                self.after(1000, _update_timer)
            except Exception:
                pass

        _update_timer()

        def _worker():
            try:
                results = []
                for c in self.clips:
                    def _prog(ev):
                        try:
                            self.after(0, lambda e=ev: self._on_worker_progress(e))
                        except Exception:
                            pass
                    res = run_comparison.execute_clip_retranscription(
                        event_root=self.event_root,
                        clip_dict=c,
                        model_id=model_id,
                        mode=mode,
                        progress_callback=_prog,
                    )
                    results.append(res)
                self.after(0, lambda: self._on_transcription_finished(results, mode, model_id))
            except Exception as e:
                self.after(0, lambda exc=e: self._on_transcription_failed(exc))

        threading.Thread(target=_worker, daemon=True).start()

    def _on_worker_progress(self, ev: Dict[str, Any]):
        msg = ev.get("message", "Processing...")
        pct = ev.get("percent", 0.0)
        self.progress_msg_var.set(msg)
        self.progress_bar.configure(value=pct)

    def _on_transcription_finished(self, results: List[Dict[str, Any]], mode: str, model_id: str):
        self.is_transcribing = False
        self.destroy()
        # Pass precomputed results to caller
        self.on_start_callback(mode, model_id, results)

    def _on_transcription_failed(self, exc: Exception):
        self.is_transcribing = False
        self.btn_start.configure(state=tk.NORMAL, text="Start Re-transcribe")
        self.btn_cancel.configure(state=tk.NORMAL)
        self.progress_frame.pack_forget()
        messagebox.showerror(
            "Re-transcription Error",
            f"An error occurred during re-transcription:\n\n{exc}",
            parent=self,
        )


# ---------------------------------------------------------------------------
# Stage 3 Tab
# ---------------------------------------------------------------------------

class Stage3Tab(ScrollableTab):
    """Stage 3 Tab UI and Event Handler."""

    def __init__(self, parent, ctx: EventContext, status_callback=None, vault: Optional[VoiceVault] = None):
        super().__init__(parent)
        self.ctx = ctx
        self.status_callback = status_callback or (lambda msg: None)
        self.vault = vault or VoiceVault()
        self.transcription_mgr = TranscriptionManager(python_executable=sys.executable)

        self.manifest_data: dict = {}
        self.clips: list = []
        self.is_running = False
        self._loaded_event_root = ""
        self._attempted_root    = ""
        self._manifest_mtime: Optional[float] = None   # mtime of last successfully loaded manifest
        self._pending_reload    = False                 # reload deferred because a run is active
        # Per-clip force-redo set (clip names); toggled by right-click menu.
        self._force_clips: set = set()
        self._active_viewer = None
        self._currently_transcribing_clip: Optional[str] = None
        self._current_running_iid: Optional[str] = None

        # Live Timer & Engine Tracking (Part 1)
        self._ui_timer_id: Optional[str] = None
        self._run_start_monotonic: float = 0.0
        self._current_clip_start_monotonic: float = 0.0
        self._last_worker_event_monotonic: float = 0.0
        self._last_manual_scroll_time: float = 0.0
        self._current_engine_name: str = "Whisper medium (GPU)"
        self._current_engine_details: Dict[str, Any] = {}
        self._failover_active: bool = False
        self._failover_start_monotonic: float = 0.0
        self._current_intra_progress_pct: Optional[float] = None
        self._current_substage: str = "Transcribing"
        self._clips_in_current_run: List[Dict[str, Any]] = []
        self._completed_clips_in_run: List[Dict[str, Any]] = []

        # Cloud Engine & Provider state
        self._privacy_confirmed = False
        self._session_cloud_minutes = 0.0
        self.engine_var = tk.StringVar(value="local")
        self.profile_var = tk.StringVar(value="nvidia_parakeet — NVIDIA Parakeet TDT 0.6B v2 (English)")
        self.checkpoint_every_clip_var = tk.BooleanVar(value=False)
        self.api_key_var = tk.StringVar(value=_cloud_engine.get_nvidia_api_key())
        self.cloud_usage_var = tk.StringVar(value="Cloud uploaded this session: 0.0 mins (Est. cost: $0.00)")
        self.test_conn_status_var = tk.StringVar(value="")
        self.hf_token_var = tk.StringVar(value=hf_preflight.get_hf_token())
        self.hf_status_var = tk.StringVar(value="")
        self.timeout_base_var = tk.DoubleVar(value=15.0)
        self.timeout_factor_var = tk.DoubleVar(value=2.0)

        if self.vault:
            self.vault.add_listener(self._on_vault_state_changed)

        theme.make_dpi_aware()
        self._build_ui()

        # Bug 1: shared-state signal from Stage 2.
        self.ctx.bind_manifest_saved(self._on_manifest_saved_signal)
        # Bug 1: StringVar changes (folder/name/date changes).
        self.ctx.bind_change(self._on_context_change)

        # Bug 1: tab-focus backstop via <Map> (fires when this frame becomes visible).
        self.bind("<Map>", self._on_tab_shown)

        self._on_context_change()

    def _on_vault_state_changed(self) -> None:
        """Invoked when the vault locks or unlocks; refreshes speaker names/aliases and gate UI."""
        self._update_combine_and_approval_ui()
        try:
            if self.winfo_exists():
                self.after_idle(self._refresh_status_column)
        except Exception:
            pass

    def _resolve_clip_speaker_display(self, raw_speaker: str) -> str:
        """
        Resolves speaker aliases (S01, S02) to real names if Voice Vault is unlocked.
        If Vault is locked or uninitialized, returns the alias/raw label.
        """
        if not raw_speaker:
            return "Speaker"
        raw_spk = raw_speaker.strip()
        if self.vault and self.vault.is_unlocked():
            paths = self._paths()
            event_id = paths.get("event_id") if paths else ""
            if not event_id and self.manifest_data:
                event_id = self.manifest_data.get("event_id", "")
            if event_id:
                try:
                    roster_mgr = EventRosterManager(self.vault)
                    alias_map = roster_mgr.get_alias_to_name_map(event_id)
                    if alias_map and raw_spk in alias_map:
                        return alias_map[raw_spk]
                except Exception:
                    pass
        return raw_spk

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self):
        # All content packed flat into self.inner (the scrollable canvas child).
        # No outer PanedWindow — every section gets its natural height; the canvas
        # scrollregion grows to fit them all, so nothing is ever squashed.

        upper = ttk.LabelFrame(self.inner, text=" 1. Event & Manifest Selection ", padding=10)
        upper.pack(fill=tk.X, padx=4, pady=(4, 0))

        # Row 1: One-line summary and compact toolbar
        row_summary = ttk.Frame(upper)
        row_summary.pack(fill=tk.X, pady=(2, 4))

        self.event_summary_lbl = ttk.Label(
            row_summary,
            text="No event loaded. Set up event in Stage 2.",
            font="SS_BodyBold",
            foreground=theme.COLOR_TEXT_PRIMARY,
        )
        self.event_summary_lbl.pack(side=tk.LEFT, padx=(2, 8))

        self.btn_go_to_stage2 = ttk.Button(
            row_summary,
            text="Go to Stage 2",
            style="Primary.TButton",
            command=self._go_to_stage2,
        )
        self.btn_go_to_stage2.pack(side=tk.LEFT, padx=4)

        # Right-aligned compact action toolbar
        tb_right = ttk.Frame(row_summary)
        tb_right.pack(side=tk.RIGHT)

        self.btn_rename_speaker = ttk.Button(
            tb_right, text="Rename Speaker…", command=self._open_rename_dialog, state=tk.DISABLED
        )
        self.btn_rename_speaker.pack(side=tk.LEFT, padx=2)

        self.btn_combine_approved = ttk.Button(
            tb_right, text="Combine approved transcripts", style="Primary.TButton", command=self._combine_approved_clicked, state=tk.DISABLED
        )
        self.btn_combine_approved.pack(side=tk.LEFT, padx=2)

        self.combine_stale_lbl = ttk.Label(
            tb_right, text="", font="SS_BodyBold", foreground=theme.MSG_THEMES["warning"]["fg"]
        )
        self.combine_stale_lbl.pack(side=tk.LEFT, padx=4)

        # Reload banner (shown during run if manifest changed on disk)
        self._reload_banner_var = tk.StringVar(value="")
        self._reload_banner_lbl = theme.MessageBar(
            upper,
            textvariable=self._reload_banner_var,
            kind="warning",
        )
        self._reload_banner_lbl.pack(fill=tk.X, pady=(0, 4))

        # Compact Output row (elevated contrast tokens >= 4.5:1)
        out_row = ttk.Frame(upper)
        out_row.pack(fill=tk.X, pady=(2, 6))

        ttk.Label(out_row, text="Transcripts:", font="SS_BodyBold").pack(side=tk.LEFT)
        self.lbl_out_transcripts = tk.Label(
            out_row,
            text="—",
            font="SS_Code",
            bg=theme.COLOR_INPUT_BG,
            fg=theme.COLOR_TEXT_PRIMARY,
            relief="solid",
            bd=1,
            highlightthickness=0,
            padx=6,
            pady=2,
        )
        self.lbl_out_transcripts.pack(side=tk.LEFT, padx=(4, 8))
        self.btn_open_transcripts = ttk.Button(out_row, text="Open folder", command=lambda: self._open_dir("transcripts"))
        self.btn_open_transcripts.pack(side=tk.LEFT, padx=(0, 16))

        ttk.Label(out_row, text="Combined:", font="SS_BodyBold").pack(side=tk.LEFT)
        self.lbl_out_combined = tk.Label(
            out_row,
            text="—",
            font="SS_Code",
            bg=theme.COLOR_INPUT_BG,
            fg=theme.COLOR_TEXT_PRIMARY,
            relief="solid",
            bd=1,
            highlightthickness=0,
            padx=6,
            pady=2,
        )
        self.lbl_out_combined.pack(side=tk.LEFT, padx=(4, 8))
        self.btn_open_combined = ttk.Button(out_row, text="Open folder", command=lambda: self._open_dir("combined"))
        self.btn_open_combined.pack(side=tk.LEFT)

        # Controls row: narrow language, segmented engine selector, save progress switch, quiet upload counter
        ctrl_row = ttk.Frame(upper)
        ctrl_row.pack(fill=tk.X, pady=(2, 6))

        ttk.Label(ctrl_row, text="Language:", font="SS_BodyBold").pack(side=tk.LEFT)
        self.lang_var = tk.StringVar(value="en")
        self.lang_cb = ttk.Combobox(
            ctrl_row, textvariable=self.lang_var, values=["en", "auto", "hi"], width=6, state="readonly"
        )
        self.lang_cb.pack(side=tk.LEFT, padx=(4, 16))

        ttk.Label(ctrl_row, text="Engine:", font="SS_BodyBold").pack(side=tk.LEFT, padx=(0, 4))
        self.seg_engine = theme.SegmentedControl(
            ctrl_row,
            variable=self.engine_var,
            options=[("Local (Whisper medium)", "local"), ("Cloud (NVIDIA)", "cloud")],
            command=self._on_engine_changed,
        )
        self.seg_engine.pack(side=tk.LEFT, padx=(0, 16))

        self.sw_chkpt = theme.ToggleSwitch(
            ctrl_row,
            variable=self.checkpoint_every_clip_var,
            text="Save progress every 2 minutes",
        )
        self.sw_chkpt.pack(side=tk.LEFT, padx=(0, 16))

        self.cloud_usage_lbl = ttk.Label(
            ctrl_row, textvariable=self.cloud_usage_var, font="SS_BodyBold", foreground=theme.COLOR_PRIMARY_BTN
        )
        self.cloud_usage_lbl.pack(side=tk.RIGHT)

        # Cloud details row (visible when engine == "cloud")
        self.cloud_details_frame = ttk.Frame(upper)
        cloud_row = ttk.Frame(self.cloud_details_frame)
        cloud_row.pack(fill=tk.X, pady=(2, 4))

        ttk.Label(cloud_row, text="Cloud Model:", font="SS_BodyBold").pack(side=tk.LEFT)
        prof_values = [
            f"{p.id} — {p.name}" for p in _cloud_engine.get_enabled_profiles()
        ]
        self.profile_combo = ttk.Combobox(
            cloud_row, textvariable=self.profile_var, values=prof_values, width=38, state="readonly"
        )
        self.profile_combo.pack(side=tk.LEFT, padx=(6, 12))
        self.profile_combo.bind("<<ComboboxSelected>>", self._on_profile_selected)

        ttk.Label(cloud_row, text="Request Timeout:", font="SS_Body").pack(side=tk.LEFT, padx=(6, 2))
        ttk.Label(cloud_row, text="Base:").pack(side=tk.LEFT, padx=(2, 2))
        ttk.Spinbox(cloud_row, textvariable=self.timeout_base_var, from_=5.0, to=120.0, increment=5.0, width=5).pack(side=tk.LEFT, padx=2)
        ttk.Label(cloud_row, text="s | Factor:").pack(side=tk.LEFT, padx=(4, 2))
        ttk.Spinbox(cloud_row, textvariable=self.timeout_factor_var, from_=1.0, to=10.0, increment=0.5, width=4).pack(side=tk.LEFT, padx=2)
        ttk.Label(cloud_row, text="×", font="SS_Body").pack(side=tk.LEFT, padx=(2, 2))

        if self.engine_var.get() == "cloud":
            self.cloud_details_frame.pack(fill=tk.X, pady=(2, 4))

        # Collapsed Advanced Section (Glossary & Notes)
        self.adv_section = theme.CollapsibleSection(upper, "Advanced (Glossary & Notes)", is_open=False)
        self.adv_section.pack(fill=tk.X, pady=(3, 2))

        adv_content = self.adv_section.content
        adv_row = ttk.Frame(adv_content)
        adv_row.pack(fill=tk.X, pady=2)

        ttk.Button(adv_row, text="Glossary…", command=self._open_glossary_dialog).pack(side=tk.LEFT, padx=(0, 8))
        self.lbl_glossary_preview = ttk.Label(
            adv_row,
            textvariable=self.ctx.glossary_var,
            font="SS_Caption",
            foreground=theme.COLOR_TEXT_SECONDARY,
        )
        self.lbl_glossary_preview.pack(side=tk.LEFT, fill=tk.X, expand=True)

        note_lbl = ttk.Label(
            adv_content,
            text="Engine Note: Local Whisper Medium runs on GPU. NVIDIA Riva gRPC calls cloud models. Optimized for English and Hindi audio.",
            font="SS_Caption",
            foreground=theme.COLOR_TEXT_MUTED,
        )
        note_lbl.pack(anchor=tk.W, pady=(2, 0))

        # Collapsed Connections Section (NVIDIA Key & HF Token)
        self.conn_section = theme.CollapsibleSection(upper, "Connections (NVIDIA Key & HF Token)", is_open=False)
        self.conn_section.pack(fill=tk.X, pady=(2, 4))

        conn_content = self.conn_section.content

        # Row 1: NVIDIA Key
        row_nv = ttk.Frame(conn_content)
        row_nv.pack(fill=tk.X, pady=2)
        ttk.Label(row_nv, text="NVIDIA API Key:", width=18, font="SS_BodyBold").pack(side=tk.LEFT)
        self.api_key_entry = ttk.Entry(row_nv, textvariable=self.api_key_var, show="*", width=24)
        self.api_key_entry.pack(side=tk.LEFT, padx=(0, 4))
        self.btn_toggle_key = ttk.Button(row_nv, text="👁", width=3, command=self._toggle_show_key)
        self.btn_toggle_key.pack(side=tk.LEFT, padx=2)
        ttk.Button(row_nv, text="Save Key", command=self._save_api_key_clicked).pack(side=tk.LEFT, padx=2)
        ttk.Button(row_nv, text="Test Key", command=self._test_connection_clicked).pack(side=tk.LEFT, padx=3)
        ttk.Button(row_nv, text="Test transcription", command=self._test_transcription_clicked).pack(side=tk.LEFT, padx=3)

        self.chip_nv_status = tk.Label(
            row_nv,
            textvariable=self.test_conn_status_var,
            font="SS_Caption",
            padx=8,
            pady=2,
            relief="solid",
            borderwidth=1,
            bg="#E5E7EB",
            fg=theme.COLOR_TEXT_MUTED,
        )
        self.chip_nv_status.pack(side=tk.LEFT, padx=6)
        nv_disp = _cloud_engine.get_nvidia_key_display_status()
        self.test_conn_status_var.set(nv_disp)
        if "yes" in nv_disp:
            self.chip_nv_status.configure(bg="#EFF6FF", fg=theme.COLOR_PRIMARY_BTN)

        # Row 2: Hugging Face Token
        row_hf = ttk.Frame(conn_content)
        row_hf.pack(fill=tk.X, pady=2)
        ttk.Label(row_hf, text="Hugging Face Token:", width=18, font="SS_BodyBold").pack(side=tk.LEFT)
        self.hf_token_entry = ttk.Entry(row_hf, textvariable=self.hf_token_var, show="*", width=24)
        self.hf_token_entry.pack(side=tk.LEFT, padx=(0, 4))
        self.btn_toggle_hf = ttk.Button(row_hf, text="👁", width=3, command=self._toggle_show_hf_token)
        self.btn_toggle_hf.pack(side=tk.LEFT, padx=2)
        ttk.Button(row_hf, text="Save Token", command=self._save_hf_token_clicked).pack(side=tk.LEFT, padx=2)
        ttk.Button(row_hf, text="Test", command=self._test_hf_token_clicked).pack(side=tk.LEFT, padx=4)

        self.chip_hf_status = tk.Label(
            row_hf,
            textvariable=self.hf_status_var,
            font="SS_Caption",
            padx=8,
            pady=2,
            relief="solid",
            borderwidth=1,
            bg="#E5E7EB",
            fg=theme.COLOR_TEXT_MUTED,
        )
        self.chip_hf_status.pack(side=tk.LEFT, padx=6)
        if self.hf_token_var.get().strip():
            self.hf_status_var.set("Configured")
            self.chip_hf_status.configure(bg="#EFF6FF", fg=theme.COLOR_PRIMARY_BTN)

        middle = ttk.LabelFrame(self.inner, text=" 2. Test Gate (Mandatory Pre-Run Verification) ", padding=10)
        middle.pack(fill=tk.X, padx=4, pady=(4, 0))

        gate_ctrl = ttk.Frame(middle)
        gate_ctrl.pack(fill=tk.X, pady=4)

        self.gate_status_var = tk.StringVar(value="[LOCKED] Fill Event Folder, Name and Date in Stage 2.")
        self.gate_indicator = theme.MessageBar(
            gate_ctrl,
            textvariable=self.gate_status_var,
        )
        self.gate_indicator.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 10))

        self.btn_run_test = ttk.Button(gate_ctrl, text="Transcribe 1-2 Test Clips", style="Primary.TButton", command=self._run_test_transcription)
        self.btn_run_test.pack(side=tk.LEFT, padx=4)

        self.btn_approve_test = ttk.Button(
            gate_ctrl, text="Approve Test & Unlock Full Run", style="Primary.TButton", command=self._approve_test_gate
        )
        self.btn_approve_test.pack(side=tk.LEFT, padx=4)

        self.btn_run_full = ttk.Button(
            gate_ctrl,
            text="Run All Clips (Full Run)",
            style="Primary.TButton",
            command=self._run_full_transcription,
            state=tk.DISABLED,
        )
        self.btn_run_full.pack(side=tk.RIGHT, padx=5)

        self.btn_cancel = ttk.Button(gate_ctrl, text="Cancel / Stop", command=self._cancel_run, state=tk.DISABLED)
        self.btn_cancel.pack(side=tk.RIGHT, padx=5)

        table_container = ttk.Frame(middle)
        table_container.pack(fill=tk.X, pady=5)

        # Persistent Failover Warning Banner (visible during run after any cloud failure)
        err_theme = theme.MSG_THEMES.get("error", {"bg": "#FEF2F2", "fg": "#991B1B"})
        self.failover_banner = tk.Frame(table_container, bg=err_theme["bg"], relief="solid", borderwidth=1, padx=10, pady=6)
        self.failover_banner_lbl = tk.Label(
            self.failover_banner,
            text="",
            font="SS_BodyBold",
            bg=err_theme["bg"],
            fg=err_theme["fg"],
            anchor=tk.W,
        )
        self.failover_banner_lbl.pack(side=tk.LEFT, fill=tk.X, expand=True)
        tk.Button(
            self.failover_banner,
            text="✕",
            font="SS_CaptionBold",
            bg=err_theme["bg"],
            fg=err_theme["fg"],
            relief="flat",
            command=lambda: self.failover_banner.pack_forget(),
        ).pack(side=tk.RIGHT)

        tree_frame = ttk.Frame(table_container)
        tree_frame.pack(fill=tk.X)

        self.clip_tree = ttk.Treeview(
            tree_frame,
            columns=("check", "idx", "clip", "speaker", "duration", "start", "failover", "status", "approved", "engine"),
            show="headings",
            selectmode="extended",
            height=8,
        )
        self.clip_tree.heading("check",    text="Test", anchor=tk.CENTER)
        self.clip_tree.heading("idx",      text="#", anchor=tk.CENTER)
        self.clip_tree.heading("clip",     text="Clip Filename", anchor=tk.W)
        self.clip_tree.heading("speaker",  text="Speaker", anchor=tk.W)
        self.clip_tree.heading("duration", text="Duration", anchor=tk.CENTER)
        self.clip_tree.heading("start",    text="Time taken for\ntranscription", anchor=tk.CENTER)
        self.clip_tree.heading("failover", text="Failover\ntime", anchor=tk.CENTER)
        self.clip_tree.heading("status",   text="Status", anchor=tk.W)
        self.clip_tree.heading("approved", text="Approval", anchor=tk.CENTER)
        self.clip_tree.heading("engine",   text="Engine", anchor=tk.CENTER)

        self.clip_tree.column("check",    width=52,  minwidth=46,  anchor=tk.CENTER, stretch=False)
        self.clip_tree.column("idx",      width=38,  minwidth=32,  anchor=tk.CENTER, stretch=False)
        self.clip_tree.column("clip",     width=240, minwidth=160, anchor=tk.W,      stretch=False)
        self.clip_tree.column("speaker",  width=160, minwidth=130, anchor=tk.W,      stretch=False)
        self.clip_tree.column("duration", width=75,  minwidth=68,  anchor=tk.CENTER, stretch=False)
        self.clip_tree.column("start",    width=115, minwidth=105, anchor=tk.CENTER, stretch=False)
        self.clip_tree.column("failover", width=75,  minwidth=68,  anchor=tk.CENTER, stretch=False)
        self.clip_tree.column("status",   width=115, minwidth=105, anchor=tk.W,      stretch=False)
        self.clip_tree.column("approved", width=92,  minwidth=80,  anchor=tk.CENTER, stretch=False)
        self.clip_tree.column("engine",   width=110, minwidth=95,  anchor=tk.CENTER, stretch=False)

        self._resize_after_id = None
        self._col_widths_before_drag: Dict[str, int] = {}

        theme.configure_treeview_status_tags(self.clip_tree)

        self.tree_scroll_y = ttk.Scrollbar(tree_frame, orient=tk.VERTICAL, command=self.clip_tree.yview)
        self.clip_tree.configure(yscrollcommand=self.tree_scroll_y.set)
        self.clip_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.tree_scroll_y.pack(side=tk.RIGHT, fill=tk.Y)
        self.register_wheel(self.clip_tree)

        # Locked Totals Row (Task C)
        totals_outer = ttk.Frame(table_container)
        totals_outer.pack(fill=tk.X)

        sep = tk.Frame(totals_outer, height=1, bg=theme.COLOR_BORDER)
        sep.pack(fill=tk.X)

        totals_row_frame = ttk.Frame(totals_outer)
        totals_row_frame.pack(fill=tk.X)

        self.totals_tree = ttk.Treeview(
            totals_row_frame,
            columns=("check", "idx", "clip", "speaker", "duration", "start", "failover", "status", "approved", "engine"),
            show="",
            selectmode="none",
            height=1,
            style="Totals.Treeview",
        )
        for col_name in ("check", "idx", "clip", "speaker", "duration", "start", "failover", "status", "approved", "engine"):
            anchor = self.clip_tree.column(col_name, "anchor")
            self.totals_tree.column(col_name, anchor=anchor, stretch=False)

        theme.configure_treeview_status_tags(self.totals_tree)

        self.totals_tree.insert(
            "",
            tk.END,
            iid="totals",
            values=("", "", "Total (0 clips)", "", "00:00:00", "—", "—", "0 of 0 clips timed", "", ""),
        )
        self.totals_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self.totals_spacer = ttk.Frame(totals_row_frame, width=17)
        self.totals_spacer.pack(side=tk.RIGHT, fill=tk.Y)

        # Lock totals row: ignore selection and clicks
        self.totals_tree.bind("<Button-1>", lambda e: "break")
        self.totals_tree.bind("<Double-1>", lambda e: "break")
        self.totals_tree.bind("<Button-3>", lambda e: "break")

        # Synchronize column widths on configure / resize / drag
        self.clip_tree.bind("<Configure>", self._on_table_configure)
        self.clip_tree.bind("<ButtonPress-1>", self._on_tree_button_press, add="+")
        self.clip_tree.bind("<ButtonRelease-1>", self._on_tree_button_release, add="+")
        self.clip_tree.bind("<B1-Motion>", self._sync_totals_columns, add="+")
        self.bind("<Map>", self._on_tab_mapped, add="+")

        # Review step: double-clicking opens read-only viewer
        self.clip_tree.bind("<Double-1>", self._on_clip_double_click)

        # Cell hover tooltip on treeview
        self.clip_tree.bind("<Motion>", self._on_tree_motion)
        self.clip_tree.bind("<Leave>", self._hide_tree_tooltip)

        # Manual scroll tracking (10-second auto-scroll freeze)
        self.clip_tree.bind("<MouseWheel>", self._on_user_scroll, add="+")
        self.clip_tree.bind("<Button-4>", self._on_user_scroll, add="+")
        self.clip_tree.bind("<Button-5>", self._on_user_scroll, add="+")
        self.tree_scroll_y.bind("<ButtonPress-1>", self._on_user_scroll, add="+")
        self.tree_scroll_y.bind("<B1-Motion>", self._on_user_scroll, add="+")

        # Bug 2: right-click context menu.
        self._ctx_menu = tk.Menu(self, tearoff=0)
        self._ctx_menu.add_command(label="Force re-transcribe (toggle)", command=self._toggle_force_relist)
        self._ctx_menu.add_command(label="Re-transcribe with Model Choice…", command=self._open_retranscribe_dialog)
        self.clip_tree.bind("<Button-3>", self._on_tree_right_click)
        self.clip_tree.bind("<<TreeviewSelect>>", self._on_tree_select, add="+")

        # Review & Approval Action Bar
        review_bar = ttk.Frame(middle)
        review_bar.pack(fill=tk.X, pady=(4, 2))

        self.btn_approve_selected = ttk.Button(
            review_bar, text="Approve selected", command=self._approve_selected_clicked, state=tk.DISABLED
        )
        self.btn_approve_selected.pack(side=tk.LEFT, padx=(0, 4))

        self.btn_approve_all = ttk.Button(
            review_bar, text="Approve all completed", command=self._approve_all_completed_clicked, state=tk.DISABLED
        )
        self.btn_approve_all.pack(side=tk.LEFT, padx=4)

        self.btn_clear_approval = ttk.Button(
            review_bar, text="Clear approval", command=self._clear_approval_clicked, state=tk.DISABLED
        )
        self.btn_clear_approval.pack(side=tk.LEFT, padx=4)

        self.btn_retranscribe = ttk.Button(
            review_bar, text="Re-transcribe…", command=self._open_retranscribe_dialog, state=tk.DISABLED
        )
        self.btn_retranscribe.pack(side=tk.LEFT, padx=4)

        self.btn_export_comparison = ttk.Button(
            review_bar, text="Export comparison…", command=self._export_comparison_clicked
        )
        self.btn_export_comparison.pack(side=tk.LEFT, padx=4)

        self.btn_finalize_names = ttk.Button(
            review_bar, text="Finalise names", command=self._finalize_names_clicked, state=tk.DISABLED
        )
        self.btn_finalize_names.pack(side=tk.LEFT, padx=4)

        self.btn_reidentify = ttk.Button(
            review_bar, text="Re-Identify…", command=self._reidentify_clicked, state=tk.DISABLED
        )
        self.btn_reidentify.pack(side=tk.LEFT, padx=4)

        self.btn_undo_reidentify = ttk.Button(
            review_bar, text="Undo Re-Identify", command=self._undo_reidentify_clicked, state=tk.DISABLED
        )
        self.btn_undo_reidentify.pack(side=tk.LEFT, padx=4)

        self.vault_chip = VaultStatusChip(review_bar, self.vault, on_state_changed=self._on_vault_state_changed)
        self.vault_chip.pack(side=tk.RIGHT, padx=5)

        self.approval_counter_var = tk.StringVar(value="0 of 0 approved")
        self.lbl_approval_counter = ttk.Label(
            review_bar, textvariable=self.approval_counter_var, font=("Segoe UI", 9, "bold")
        )
        self.lbl_approval_counter.pack(side=tk.LEFT, padx=10)

        self.combine_gate_var = tk.StringVar(value="")
        self.lbl_combine_gate = theme.MessageBar(
            review_bar, textvariable=self.combine_gate_var,
        )
        self.lbl_combine_gate.pack(side=tk.RIGHT, padx=5)

        # Comparison Detail Drawer (Part D)
        self.comp_drawer = theme.CollapsibleSection(self.inner, "Transcription Comparison (Side-by-Side Diff)", is_open=False)
        self.comp_drawer.pack(fill=tk.X, padx=4, pady=(2, 4))

        comp_box = self.comp_drawer.content
        self.comp_metrics_var = tk.StringVar(value="Select a clip with multiple runs to view comparison metrics.")
        self.comp_banner = theme.MessageBar(comp_box, textvariable=self.comp_metrics_var, kind="info")
        self.comp_banner.pack(fill=tk.X, pady=(0, 4))

        comp_action_bar = ttk.Frame(comp_box)
        comp_action_bar.pack(fill=tk.X, pady=(2, 4))

        self.btn_promote_primary = ttk.Button(
            comp_action_bar,
            text="Make Comparison Run Primary",
            style="Primary.TButton",
            command=self._promote_comparison_to_primary,
        )
        self.btn_promote_primary.pack(side=tk.LEFT, padx=4)

        self.btn_export_single_comp = ttk.Button(
            comp_action_bar,
            text="Export Comparison Report (Excel/CSV)…",
            command=self._export_comparison_clicked,
        )
        self.btn_export_single_comp.pack(side=tk.LEFT, padx=4)

        # Dual Text Area (PanedWindow)
        comp_panes = ttk.PanedWindow(comp_box, orient=tk.HORIZONTAL)
        comp_panes.pack(fill=tk.BOTH, expand=True, pady=4)

        frame_left = ttk.Frame(comp_panes)
        self.lbl_comp_run1 = ttk.Label(frame_left, text="Run 1 (Baseline)", font=("Segoe UI", 9, "bold"))
        self.lbl_comp_run1.pack(anchor=tk.W, pady=(0, 2))
        self.txt_comp_run1 = scrolledtext.ScrolledText(
            frame_left,
            height=6,
            font="SS_Code",
            wrap=tk.WORD,
            bg=theme.COLOR_INPUT_BG,
            fg=theme.COLOR_TEXT_PRIMARY,
            insertbackground=theme.COLOR_TEXT_PRIMARY,
            highlightthickness=1,
            highlightbackground=theme.COLOR_BORDER,
            relief="flat",
            padx=8,
            pady=6,
        )
        self.txt_comp_run1.pack(fill=tk.BOTH, expand=True)
        comp_panes.add(frame_left, weight=1)

        frame_right = ttk.Frame(comp_panes)
        self.lbl_comp_run2 = ttk.Label(frame_right, text="Run 2 (Comparison)", font=("Segoe UI", 9, "bold"))
        self.lbl_comp_run2.pack(anchor=tk.W, pady=(0, 2))
        self.txt_comp_run2 = scrolledtext.ScrolledText(
            frame_right,
            height=6,
            font="SS_Code",
            wrap=tk.WORD,
            bg=theme.COLOR_INPUT_BG,
            fg=theme.COLOR_TEXT_PRIMARY,
            insertbackground=theme.COLOR_TEXT_PRIMARY,
            highlightthickness=1,
            highlightbackground=theme.COLOR_BORDER,
            relief="flat",
            padx=8,
            pady=6,
        )
        self.txt_comp_run2.pack(fill=tk.BOTH, expand=True)
        comp_panes.add(frame_right, weight=1)

        self._comp_target: Optional[Tuple[str, int]] = None

        lower = ttk.LabelFrame(self.inner, text=" 3. Progress, Live Logs & Transcript Preview ", padding=10)
        lower.pack(fill=tk.X, padx=4, pady=(4, 4))

        metrics_row = ttk.Frame(lower)
        metrics_row.pack(fill=tk.X, pady=2)

        self.prog_bar = ttk.Progressbar(metrics_row, orient=tk.HORIZONTAL, mode="determinate")
        self.prog_bar.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)

        self.speed_lbl_var = tk.StringVar(value="Speed: --x real-time")
        self.speed_lbl = ttk.Label(
            metrics_row, textvariable=self.speed_lbl_var, font=("Segoe UI", 9, "bold"), foreground=theme.COLOR_INFO_FG
        )
        self.speed_lbl.pack(side=tk.RIGHT, padx=5)

        self.log_text = scrolledtext.ScrolledText(
            lower,
            height=8,
            font="SS_Code",
            wrap=tk.WORD,
            bg=theme.COLOR_INPUT_BG,
            fg=theme.COLOR_TEXT_PRIMARY,
            insertbackground=theme.COLOR_TEXT_PRIMARY,
            highlightthickness=1,
            highlightbackground=theme.COLOR_BORDER,
            relief="flat",
            padx=8,
            pady=6,
        )
        self.log_text.pack(fill=tk.X, pady=4)
        self.register_wheel(self.log_text)

    # ------------------------------------------------------------------
    # Engine & Provider UI Handlers
    # ------------------------------------------------------------------

    def _on_engine_changed(self):
        if self.engine_var.get() == "cloud":
            self.cloud_details_frame.pack(fill=tk.X, pady=(4, 0))
        else:
            self.cloud_details_frame.pack_forget()

    def _on_profile_selected(self, _event=None):
        prof_val = self.profile_var.get().lower()
        if "parakeet" in prof_val:
            self.lang_var.set("en")

    def _toggle_show_key(self):
        if self.api_key_entry.cget("show") == "*":
            self.api_key_entry.configure(show="")
            self.btn_toggle_key.configure(text="🔒 Hide")
        else:
            self.api_key_entry.configure(show="*")
            self.btn_toggle_key.configure(text="👁 Show")

    def _save_api_key_clicked(self):
        key = self.api_key_var.get().strip()
        if not key:
            messagebox.showwarning("Save Key", "Please enter an API key.")
            return
        _cloud_engine.save_nvidia_api_key(key)
        self.test_conn_status_var.set("Key saved to .env")

    def _test_connection_clicked(self):
        key = self.api_key_var.get().strip() or _cloud_engine.get_nvidia_api_key()
        if not key:
            self.chip_nv_status.configure(bg="#E5E7EB", fg=theme.COLOR_TEXT_MUTED)
            self.test_conn_status_var.set("Not set")
            return

        prof_id = self.profile_var.get().split(" — ")[0].strip()
        profile = _cloud_engine.get_profile_by_id(prof_id)
        if not profile:
            self.chip_nv_status.configure(bg="#FEF2F2", fg="#7F1D1D")
            self.test_conn_status_var.set(f"Invalid profile")
            return

        self.chip_nv_status.configure(bg="#FFFBEB", fg="#78350F")
        self.test_conn_status_var.set("Testing...")

        def run_test():
            ok, msg = _cloud_engine.test_riva_connection(key, profile)
            def on_done():
                if ok:
                    self.chip_nv_status.configure(bg="#F0FDF4", fg="#14532D")
                    self.test_conn_status_var.set("Valid")
                    self._log(f"[CLOUD] Connection test passed: {msg}")
                else:
                    self.chip_nv_status.configure(bg="#FEF2F2", fg="#7F1D1D")
                    self.test_conn_status_var.set("Invalid")
                    self._log(f"[CLOUD ERROR] Connection test failed: {msg}")
            self.after(0, on_done)

        threading.Thread(target=run_test, daemon=True).start()

    def _test_transcription_clicked(self):
        key = self.api_key_var.get().strip() or _cloud_engine.get_nvidia_api_key()
        prof_id = self.profile_var.get().split(" — ")[0].strip()
        profile = _cloud_engine.get_profile_by_id(prof_id)
        if not profile:
            messagebox.showwarning("Test Transcription", "Please select a valid Cloud profile.")
            return

        self._log(f"[CLOUD TEST] Starting Real Test Transcription with bundled 5s sample on {profile.name}...")
        self.chip_nv_status.configure(bg="#FFFBEB", fg="#78350F")
        self.test_conn_status_var.set("Testing 5s...")

        def run_preflight():
            res = _cloud_engine.run_test_transcription(profile, api_key=key)
            def on_done():
                if res.success:
                    self.chip_nv_status.configure(bg="#F0FDF4", fg="#14532D")
                    self.test_conn_status_var.set("Pass (5s)")
                    self._log(f"[CLOUD TEST SUCCESS] Real cloud test transcription passed in {res.total_duration_s:.2f}s!")
                else:
                    self.chip_nv_status.configure(bg="#FEF2F2", fg="#7F1D1D")
                    self.test_conn_status_var.set(f"Fail ({res.error_class})")
                    self._log(f"[CLOUD TEST FAILED] Real cloud test transcription failed: {res.error_class} — {res.error_message}")
                self._show_test_transcription_dialog(res, profile)
            self.after(0, on_done)

        threading.Thread(target=run_preflight, daemon=True).start()

    def _show_test_transcription_dialog(self, res: _cloud_engine.PreflightTestResult, profile: _cloud_engine.ProviderProfile):
        dlg = tk.Toplevel(self)
        dlg.title("Cloud Test Transcription (Bundled 5s Sample)")
        theme.center_dialog(dlg, self, width=640, height=420, min_width=540, min_height=340)
        dlg.resizable(True, True)
        dlg.transient(self)
        dlg.grab_set()
        dlg.bind("<Escape>", lambda _e: dlg.destroy())
        dlg.bind("<Return>", lambda _e: dlg.destroy())

        btn_row = ttk.Frame(dlg, padding=10)
        btn_row.pack(fill=tk.X, side=tk.BOTTOM)
        ttk.Button(btn_row, text="Close", command=dlg.destroy).pack(side=tk.RIGHT)

        header = tk.Frame(dlg, bg="#0B2545", padx=14, pady=10)
        header.pack(fill=tk.X)
        status_text = "PASS — Cloud Transcription Ready" if res.success else f"FAIL — {res.error_class}"
        status_color = "#4ADE80" if res.success else "#F87171"
        lbl_status = tk.Label(header, text=status_text, font="SS_Heading", fg=status_color, bg="#0B2545", wraplength=600, justify=tk.LEFT)
        lbl_status.pack(anchor=tk.W)
        lbl_meta = tk.Label(header, text=f"Profile: {profile.name} | Total time: {res.total_duration_s:.2f}s (Bundled audio only)", font="SS_Caption", fg="#E2E8F0", bg="#0B2545", wraplength=600, justify=tk.LEFT)
        lbl_meta.pack(anchor=tk.W)

        body = ttk.Frame(dlg, padding=12)
        body.pack(fill=tk.BOTH, expand=True)

        stage_labels = []
        for stg in res.stages:
            stg_frame = ttk.Frame(body)
            stg_frame.pack(fill=tk.X, pady=4)
            badge_bg = "#DCFCE7" if stg.status == "PASS" else "#FEE2E2"
            badge_fg = "#15803D" if stg.status == "PASS" else "#B91C1C"
            tk.Label(stg_frame, text=stg.status, font="SS_CaptionBold", bg=badge_bg, fg=badge_fg, padx=6, pady=1, relief="solid", borderwidth=1).pack(side=tk.LEFT)
            ttk.Label(stg_frame, text=f" {stg.stage_name} ({stg.duration_s:.3f}s):", font="SS_BodyBold").pack(side=tk.LEFT, padx=4)
            s_lbl = ttk.Label(stg_frame, text=stg.message, font="SS_Body", wraplength=380, justify=tk.LEFT)
            s_lbl.pack(side=tk.LEFT, fill=tk.X, expand=True)
            stage_labels.append(s_lbl)

        def _on_dlg_cfg(e):
            if e.widget == dlg:
                avail = max(200, e.width - 40)
                if lbl_status.winfo_exists():
                    lbl_status.configure(wraplength=avail)
                if lbl_meta.winfo_exists():
                    lbl_meta.configure(wraplength=avail)
                for sl in stage_labels:
                    if sl.winfo_exists():
                        sl.configure(wraplength=max(150, e.width - 240))
        dlg.bind("<Configure>", _on_dlg_cfg)

    def show_toast(self, message: str, duration_ms: int = 5000):
        """Display non-blocking toast notification."""
        try:
            toast = tk.Toplevel(self)
            toast.wm_overrideredirect(True)
            toast.attributes("-topmost", True)
            frm = tk.Frame(toast, bg="#1E293B", padx=12, pady=8, relief="solid", borderwidth=1)
            frm.pack()
            tk.Label(frm, text=message, font="SS_Body", fg="#F8FAFC", bg="#1E293B").pack()
            x = self.winfo_rootx() + max(20, self.winfo_width() - 520)
            y = self.winfo_rooty() + max(20, self.winfo_height() - 90)
            toast.geometry(f"+{x}+{y}")
            self.after(duration_ms, lambda: toast.destroy() if toast.winfo_exists() else None)
        except Exception:
            pass

    def _check_nvidia_models_clicked(self):
        key = self.api_key_var.get().strip() or _cloud_engine.get_nvidia_api_key()
        if not key:
            messagebox.showwarning("Check Models", "Please enter an NVIDIA API key first.")
            return

        self.test_conn_status_var.set("Querying NVCF...")

        def run_check():
            models = _cloud_engine.fetch_nvidia_speech_models(key)
            self.after(0, lambda: self._show_models_dialog(models))

        threading.Thread(target=run_check, daemon=True).start()

    def _show_models_dialog(self, models: list):
        self.test_conn_status_var.set("")
        dlg = tk.Toplevel(self)
        dlg.title("NVIDIA Hosted Speech Models")
        theme.center_dialog(dlg, self, width=680, height=520, min_width=560, min_height=380)
        dlg.resizable(True, True)
        dlg.transient(self)
        dlg.grab_set()
        dlg.bind("<Escape>", lambda _e: dlg.destroy())
        dlg.bind("<Return>", lambda _e: dlg.destroy())

        btn_row = ttk.Frame(dlg, padding=(14, 10))
        btn_row.pack(fill=tk.X, side=tk.BOTTOM)
        ttk.Button(btn_row, text="Close", command=dlg.destroy).pack(side=tk.RIGHT)

        has_nemotron = any(m.get("is_nemotron_transcribe") for m in models)

        header_frame = ttk.Frame(dlg)
        header_frame.pack(fill=tk.X, padx=14, pady=(12, 6))

        if has_nemotron:
            lbl_title = ttk.Label(
                header_frame,
                text="★ Nemotron 3.5 Transcribe is AVAILABLE on build.nvidia.com!",
                font=("Segoe UI", 10, "bold"),
                foreground="#28a745",
                wraplength=620,
            )
        else:
            lbl_title = ttk.Label(
                header_frame,
                text="Nemotron 3.5 Transcribe is not yet hosted on build.nvidia.com.",
                font=("Segoe UI", 10, "bold"),
                foreground="#d9534f",
                wraplength=620,
            )
        lbl_title.pack(anchor=tk.W)

        def _on_models_cfg(e):
            if e.widget == dlg and lbl_title.winfo_exists():
                lbl_title.configure(wraplength=max(200, e.width - 40))
        dlg.bind("<Configure>", _on_models_cfg)

        ttk.Label(
            header_frame,
            text=f"Queried NVCF functions catalog ({len(models)} speech models found):",
            font=("Segoe UI", 9),
        ).pack(anchor=tk.W, pady=(4, 0))

        txt = scrolledtext.ScrolledText(dlg, font=("Consolas", 9), wrap=tk.WORD)
        txt.pack(fill=tk.BOTH, expand=True, padx=14, pady=8)

        for m in models:
            badge = "[NEMOTRON TRANSCRIBE] " if m.get("is_nemotron_transcribe") else ""
            txt.insert(tk.END, f"{badge}Name: {m.get('name')}\n")
            if m.get('id'):
                txt.insert(tk.END, f"  ID: {m.get('id')}\n")
            if m.get('status'):
                txt.insert(tk.END, f"  Status: {m.get('status')}\n")
            txt.insert(tk.END, "-" * 50 + "\n")
        txt.configure(state="disabled")

    # ------------------------------------------------------------------
    # Path helpers
    # ------------------------------------------------------------------

    def _paths(self):
        root = self.ctx.event_dir_var.get().strip()
        if not root:
            return None
        return derive_event_paths(root)

    def _current_ghash(self) -> str:
        return _glossary_hash(self.ctx.glossary_var.get().strip())

    # ------------------------------------------------------------------
    # Bug 1: manifest refresh triggers
    # ------------------------------------------------------------------

    def _on_manifest_saved_signal(self):
        """Called by EventContext when Stage 2 writes manifest.json."""
        if self.is_running:
            self._pending_reload = True
            self._reload_banner_var.set(
                "⚠ Manifest changed — clip list will reload after this run ends."
            )
            return
        self._load_manifest(silent=True)

    def _on_tab_shown(self, _event=None):
        """Fired by <Map> when this tab becomes visible. Re-read if mtime changed."""
        if self.is_running or not self.ctx.required_ready():
            return
        paths = self._paths()
        if not paths:
            return
        mp = paths["manifest"]
        try:
            mtime = os.path.getmtime(mp)
        except OSError:
            return
        if mtime != self._manifest_mtime:
            self._load_manifest(silent=True)

    def _reload_manifest_clicked(self):
        """Reload button: always re-read from disk."""
        if self.is_running:
            messagebox.showinfo(
                "Reload blocked",
                "A transcription run is active. The clip list will reload when it finishes.",
            )
            return
        err = self._load_manifest(silent=False)
        if err:
            messagebox.showerror("Cannot load manifest", err)

    def _go_to_stage2(self):
        """Navigate to Stage 2 tab."""
        try:
            self.master.select(0)
        except Exception:
            pass

    def _open_dir(self, key: str):
        """Open output directory in Windows File Explorer."""
        paths = self._paths()
        if not paths:
            messagebox.showinfo("No Event", "No event folder selected yet.", parent=self)
            return
        target = paths.get(key)
        if target and os.path.isdir(target):
            try:
                os.startfile(target)
            except Exception as exc:
                messagebox.showerror("Error", f"Failed to open directory:\n{exc}", parent=self)
        else:
            messagebox.showinfo("Folder Not Found", f"The {key} folder does not exist yet:\n{target}", parent=self)

    def _update_output_paths(self):
        """Update output path labels in compact Output row."""
        paths = self._paths()
        if paths:
            t_dir = paths.get("transcripts", "")
            c_dir = paths.get("combined", "")
            self.lbl_out_transcripts.configure(text=t_dir or "—")
            self.lbl_out_combined.configure(text=c_dir or "—")
        else:
            self.lbl_out_transcripts.configure(text="—")
            self.lbl_out_combined.configure(text="—")

    def _open_glossary_dialog(self):
        """Open dialog to edit domain glossary / initial prompt."""
        d = tk.Toplevel(self)
        d.title("Domain Glossary & Technical Terms")
        theme.center_dialog(d, self, width=480, height=280, min_width=400, min_height=200)
        d.configure(bg=theme.COLOR_APP_BG)
        d.transient(self)
        d.grab_set()
        d.resizable(True, True)
        d.bind("<Escape>", lambda _e: d.destroy())

        f = tk.Frame(d, bg=theme.COLOR_CARD_BG, padx=16, pady=12, highlightbackground=theme.COLOR_BORDER, highlightthickness=1)
        f.pack(fill=tk.BOTH, expand=True, padx=12, pady=12)

        def save_and_close():
            new_val = txt.get("1.0", tk.END).strip()
            self.ctx.glossary_var.set(new_val)
            self._on_glossary_changed()
            d.destroy()

        d.bind("<Control-Return>", lambda _e: (save_and_close(), "break")[1])

        btn_box = ttk.Frame(f)
        btn_box.pack(fill=tk.X, side=tk.BOTTOM, pady=(10, 0))
        ttk.Button(btn_box, text="Cancel", command=d.destroy).pack(side=tk.LEFT)
        ttk.Button(btn_box, text="Save Glossary", style="Primary.TButton", command=save_and_close).pack(side=tk.RIGHT)

        tk.Label(f, text="Domain Glossary / Initial Prompt", font="SS_Section", bg=theme.COLOR_CARD_BG, fg=theme.COLOR_HEADER_NAVY).pack(anchor=tk.W)
        tk.Label(
            f,
            text="Provide technical keywords, speaker names, and acronyms to prime transcription accuracy.",
            font="SS_Caption",
            bg=theme.COLOR_CARD_BG,
            fg=theme.COLOR_TEXT_SECONDARY,
            wraplength=420,
            justify=tk.LEFT,
        ).pack(anchor=tk.W, pady=(2, 8))

        txt = tk.Text(f, height=5, font="SS_Code", wrap=tk.WORD, bg=theme.COLOR_ROW_ODD, fg=theme.COLOR_TEXT_PRIMARY)
        txt.pack(fill=tk.BOTH, expand=True)
        txt.insert("1.0", self.ctx.glossary_var.get())

    def _toggle_show_hf_token(self):
        current = self.hf_token_entry.cget("show")
        if current == "*":
            self.hf_token_entry.configure(show="")
            self.btn_toggle_hf.configure(text="🔒")
        else:
            self.hf_token_entry.configure(show="*")
            self.btn_toggle_hf.configure(text="👁")

    def _save_hf_token_clicked(self):
        val = self.hf_token_var.get().strip()
        hf_preflight.save_hf_token_to_env(val)
        messagebox.showinfo("Saved", "Hugging Face token saved to local git-ignored .env file.", parent=self)
        self._test_hf_token_clicked()

    def _test_hf_token_clicked(self):
        val = self.hf_token_var.get().strip()
        if not val:
            self.hf_status_var.set("Not set")
            self.chip_hf_status.configure(bg="#E5E7EB", fg=theme.COLOR_TEXT_MUTED)
            return

        is_ok, msg, details = hf_preflight.run_hf_preflight_check(val)
        if is_ok:
            user = details.get("username", "Valid")
            self.hf_status_var.set(f"Valid ({user})")
            self.chip_hf_status.configure(bg="#F0FDF4", fg="#14532D")
            messagebox.showinfo("Hugging Face Preflight Passed", msg, parent=self)
        else:
            status_kind = details.get("status", "invalid")
            if status_kind == "no_access":
                self.hf_status_var.set("No access")
                self.chip_hf_status.configure(bg="#FFFBEB", fg="#78350F")
            else:
                self.hf_status_var.set("Invalid")
                self.chip_hf_status.configure(bg="#FEF2F2", fg="#7F1D1D")
            messagebox.showerror("Hugging Face Preflight Failed", msg, parent=self)

    def _on_context_change(self):
        if self.is_running:
            return
        ready = self.ctx.required_ready()
        action_state = tk.NORMAL if ready else tk.DISABLED
        self.btn_run_test.configure(state=action_state)
        self.btn_approve_test.configure(state=action_state)

        current_root = self.ctx.event_dir_var.get().strip()
        if not ready:
            self._clear_clips()
            self._loaded_event_root = ""
            self._attempted_root    = ""
            self._manifest_mtime    = None
            self.event_summary_lbl.configure(
                text="No event loaded. Set up event in Stage 2.",
                foreground=theme.COLOR_TEXT_SECONDARY,
            )
            self.btn_go_to_stage2.pack(side=tk.LEFT, padx=4)
            self._update_output_paths()
            self.gate_status_var.set("[LOCKED] Fill Event Folder, Name and Date in Stage 2.")
            self.gate_indicator.configure(foreground="#d9534f")
            self.btn_run_full.configure(state=tk.DISABLED)
            self.btn_rename_speaker.configure(state=tk.DISABLED)
            self.btn_combine_approved.configure(state=tk.DISABLED)
            self.btn_approve_selected.configure(state=tk.DISABLED)
            self.btn_approve_all.configure(state=tk.DISABLED)
            self.btn_clear_approval.configure(state=tk.DISABLED)
            if hasattr(self, "btn_retranscribe"):
                self.btn_retranscribe.configure(state=tk.DISABLED)
            if hasattr(self, "btn_export_comparison"):
                self.btn_export_comparison.configure(state=tk.DISABLED)
            self.approval_counter_var.set("0 of 0 approved")
            self.combine_gate_var.set("")
            self.combine_stale_lbl.configure(text="")
            return

        if current_root != self._attempted_root or self._loaded_event_root != current_root:
            self._attempted_root = current_root
            self._load_manifest(silent=True)

        self._update_output_paths()
        has_sessions = bool(self.manifest_data.get("sessions")) if self.manifest_data else False
        self.btn_rename_speaker.configure(state=tk.NORMAL if has_sessions else tk.DISABLED)
        self._update_gate_ui()
        self._update_combine_and_approval_ui()

    def _on_glossary_changed(self, _event=None):
        """Save glossary and refresh the status column (settings may now match/mismatch)."""
        self._save_glossary()
        self._refresh_status_column()

    # ------------------------------------------------------------------
    # Bug 2: per-clip status helpers
    # ------------------------------------------------------------------

    def _refresh_status_column(self):
        """Recompute disk-based status and approval for every row in the clip list."""
        paths = self._paths()
        if not paths or not self.clips:
            self._update_combine_and_approval_ui()
            return

        state_path = paths["state"]
        transcripts_dir = paths["transcripts"]
        ghash = self._current_ghash()

        # Automatically clear approval for clips whose transcript changed or was removed
        all_stems = [os.path.splitext(c.get("name", ""))[0] for c in self.clips]
        revoked = validate_and_reconcile_approvals(state_path, transcripts_dir, all_stems, test_dir=paths.get("test"))
        if revoked:
            self._log(f"[APPROVAL] Approval cleared for {len(revoked)} clip(s) whose transcript file changed on disk: {', '.join(revoked)}")
            try:
                messagebox.showwarning(
                    "Approval Cleared",
                    f"The transcript file on disk changed for {len(revoked)} clip(s);\n"
                    f"their approval was automatically cleared:\n\n"
                    + "\n".join(f"• {s}.txt" for s in revoked[:8])
                    + ("\n..." if len(revoked) > 8 else "")
                    + "\n\nPlease review and re-approve.",
                )
            except Exception:
                pass

        for idx, c in enumerate(self.clips, 1):
            iid = str(idx)
            if not self.clip_tree.exists(iid):
                continue
            stem, _ = os.path.splitext(c.get("name", ""))
            forced = c.get("name") in self._force_clips
            if forced:
                status = "Pending (forced)"
                tag    = "forced"
            else:
                status = _clip_disk_status(c, paths, ghash)
                tag    = self._status_tag(status)

            is_appr = get_clip_approval(state_path, stem)
            # Only completed clips (Done, Done (test), Edited, Completed) can remain approved
            if status not in ("Done", "Done (test)", "Edited", "Completed") and is_appr:
                clear_multiple_clip_approvals(state_path, [stem])
                is_appr = False

            appr_text = format_approval_display(is_appr)
            appr_tag  = "approved_tag" if is_appr else "unapproved_tag"

            vals = list(self.clip_tree.item(iid, "values"))
            while len(vals) < 10:
                vals.append("")
            rec = _state_read_clip(state_path, stem) if state_path else {}
            engine_val = rec.get("engine", "")
            if engine_val == "cloud":
                prov = rec.get("provider", "")
                eng_str = "Cloud: " + ("Parakeet" if "parakeet" in prov.lower() else "Whisper")
            elif engine_val == "local (fallback)":
                eng_str = "Fallback (Local)"
            elif engine_val == "local":
                eng_str = "Local"
            else:
                eng_str = engine_val or "—"

            fo_sec, _ = _get_clip_failover_data(c.get("name", ""), paths)
            vals[3] = self._resolve_clip_speaker_display(c.get("speaker", ""))
            vals[4] = format_duration_hhmmss(c.get("duration", 0))
            vals[5] = _get_clip_time_taken(c.get("name", ""), paths)
            vals[6] = format_failover_time(fo_sec)
            vals[7] = format_status_display(status)
            vals[8] = appr_text
            vals[9] = eng_str
            row_tag = "even_row" if idx % 2 == 0 else "odd_row"
            row_tags = (row_tag, tag, appr_tag) if tag else (row_tag, appr_tag)
            self.clip_tree.item(iid, values=vals, tags=row_tags)

        self._update_totals_row()
        self._recompute_column_widths()
        self._update_combine_and_approval_ui()

    @staticmethod
    def _status_tag(status: str) -> str:
        s = status.replace("✓ ", "").replace("● ", "").replace("✕ ", "").replace("! ", "").replace("— ", "").strip()
        if s.startswith("Resuming at"):
            return "resuming"
        return {
            "Done":            "done",
            "Done (test)":     "done_test",
            "Completed":       "done",
            "Edited":          "edited",
            "Diff settings":   "diff",
            "Different settings": "diff",
            "File missing":    "missing",
            "Failed":          "missing",
            "Running":         "running",
        }.get(s, "")

    # ------------------------------------------------------------------
    # Clip list management
    # ------------------------------------------------------------------

    def _clear_clips(self):
        if self._active_viewer and self._active_viewer.winfo_exists():
            try:
                self._active_viewer.destroy()
            except Exception:
                pass
            self._active_viewer = None
        self.manifest_data = {}
        self.clips = []
        self._force_clips = set()
        for item in self.clip_tree.get_children():
            self.clip_tree.delete(item)
        self.approval_counter_var.set("0 of 0 approved")
        self.combine_gate_var.set("")
        self.btn_combine_approved.configure(state=tk.DISABLED)
        self.btn_approve_selected.configure(state=tk.DISABLED)
        self.btn_approve_all.configure(state=tk.DISABLED)
        self.btn_clear_approval.configure(state=tk.DISABLED)
        self.combine_stale_lbl.configure(text="")
        self._update_totals_row()
        self._recompute_column_widths()

    def _save_glossary(self, _event=None):
        paths = self._paths()
        if not paths:
            return
        save_glossary(paths["scribe"], self.ctx.glossary_var.get())

    # ------------------------------------------------------------------
    # Manifest loading
    # ------------------------------------------------------------------

    def _load_manifest_clicked(self):
        err = self._load_manifest(silent=False)
        if err:
            messagebox.showerror("Cannot load manifest", err)

    def _load_manifest(self, silent: bool = False) -> str:
        """Load the selected event's manifest. Returns error message or '' on success."""
        if not self.ctx.required_ready():
            msg = "Fill Event Folder, Event Name and Date in Stage 2 first."
            if not silent:
                return msg
            self.event_summary_lbl.configure(text=msg, foreground="#d9534f")
            return msg

        paths         = self._paths()
        manifest_path = paths["manifest"]
        event_root    = paths["event_root"]

        if not os.path.isfile(manifest_path):
            self._clear_clips()
            self._loaded_event_root = ""
            self._manifest_mtime    = None
            msg = (
                f"{format_paths_log_header(paths)}\n\n"
                "No manifest.json for this event. Run Stage 2 and save the manifest first.\n"
                "Stage 3 will not use another event's folder."
            )
            self.event_summary_lbl.configure(
                text=f"No manifest.json in {paths['scribe']}. Run Stage 2 first.",
                foreground="#d9534f",
            )
            return msg

        try:
            mtime = os.path.getmtime(manifest_path)
            with open(manifest_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            self._clear_clips()
            self._loaded_event_root = ""
            self._manifest_mtime    = None
            return f"Could not read manifest:\n{manifest_path}\n{e}"

        if not manifest_belongs_to_event(data, manifest_path, event_root):
            self._clear_clips()
            self._loaded_event_root = ""
            self._manifest_mtime    = None
            stored = data.get("event_root", "(none recorded)")
            msg = (
                f"{format_paths_log_header(paths)}\n\n"
                "The manifest does not belong to the selected event.\n"
                f"Selected event root: {event_root}\n"
                f"Manifest event_root: {stored}\n"
                "Stage 3 will not fall back to another event."
            )
            self.event_summary_lbl.configure(
                text="Manifest does not match the selected event.", foreground="#d9534f"
            )
            return msg

        clips = []
        for s in data.get("sessions", []):
            spk = s.get("speaker", "Speaker")
            spk_mode = s.get("speaker_mode", "single")
            roster = s.get("roster", [])
            for c in s.get("clips", []):
                c = dict(c)
                c["speaker"] = spk
                c["speaker_mode"] = spk_mode
                c["roster"] = roster
                clips.append(c)

        clip_errors = validate_clips_for_event(clips, event_root)
        fatal_errors = [e for e in clip_errors if not e.startswith("Clip file is missing:")]
        if fatal_errors:
            self._clear_clips()
            self._loaded_event_root = ""
            self._manifest_mtime    = None
            msg = (
                f"{format_paths_log_header(paths)}\n\n"
                "Manifest clips are invalid or outside the selected event root:\n\n"
                + "\n".join(f"• {e}" for e in fatal_errors[:20])
            )
            if len(fatal_errors) > 20:
                msg += f"\n• ... and {len(fatal_errors) - 20} more."
            self.event_summary_lbl.configure(
                text="Clip paths in the manifest are invalid or outside this event.",
                foreground="#d9534f",
            )
            return msg

        # Success — populate state and tree.
        self.manifest_data      = data
        self.clips              = clips
        self._loaded_event_root = event_root
        self._manifest_mtime    = mtime
        # Preserve force-redo flags for clips still in the new list.
        old_force = self._force_clips
        self._force_clips = {n for n in old_force if any(c.get("name") == n for c in clips)}

        ghash = self._current_ghash()
        state_path = paths["state"]
        transcripts_dir = paths["transcripts"]

        # Reconcile approvals against files on disk
        all_stems = [os.path.splitext(c.get("name", ""))[0] for c in clips]
        validate_and_reconcile_approvals(state_path, transcripts_dir, all_stems, test_dir=paths.get("test"))

        for item in self.clip_tree.get_children():
            self.clip_tree.delete(item)

        for idx, c in enumerate(clips, 1):
            name    = c.get("name", "")
            stem, _ = os.path.splitext(name)
            forced  = name in self._force_clips
            if forced:
                status = "Pending (forced)"
                tag    = "forced"
            else:
                status = _clip_disk_status(c, paths, ghash)
                tag    = self._status_tag(status)

            rec = _state_read_clip(state_path, stem) if state_path else {}
            engine_val = rec.get("engine", "")
            if engine_val == "cloud":
                prov = rec.get("provider", "")
                eng_str = "Cloud: " + ("Parakeet" if "parakeet" in prov.lower() else "Whisper")
            elif engine_val == "local (fallback)":
                eng_str = "Fallback (Local)"
            elif engine_val == "local":
                eng_str = "Local"
            else:
                eng_str = engine_val or "—"

            is_appr = get_clip_approval(state_path, stem) if status in ("Done", "Done (test)", "Edited") else False
            appr_text = format_approval_display(is_appr)
            appr_tag  = "approved_tag" if is_appr else "unapproved_tag"

            test_mark = "[TEST]" if idx in (1, 2) else ""
            dur_display = format_duration_hhmmss(c.get("duration", 0))
            time_taken = _get_clip_time_taken(name, paths)
            fo_sec, _ = _get_clip_failover_data(name, paths)
            fo_str = format_failover_time(fo_sec)
            row_tag = "even_row" if idx % 2 == 0 else "odd_row"
            row_tags = (row_tag, tag, appr_tag) if tag else (row_tag, appr_tag)
            self.clip_tree.insert(
                "",
                tk.END,
                iid=str(idx),
                values=(
                    test_mark,
                    idx,
                    name,
                    self._resolve_clip_speaker_display(c.get("speaker", "")),
                    dur_display,
                    time_taken,
                    fo_str,
                    format_status_display(status),
                    appr_text,
                    eng_str,
                ),
                tags=row_tags,
            )

            # Child run rows for multi-run compare clips (Part D)
            runs = run_comparison.load_clip_runs(paths["event_root"], name)
            if len(runs) > 1:
                self.clip_tree.set(str(idx), "clip", f"▼ {name} ({len(runs)} runs)")
                for r in runs:
                    r_id = r.get("run_id", 1)
                    is_p = r.get("is_primary", False)
                    p_badge = "★ " if is_p else "  "
                    child_iid = f"run_{idx}_{r_id}"
                    wer_val = r.get("wer_vs_baseline", 0.0)
                    wer_disp = f"WER: {wer_val*100:.1f}%" if r_id > 1 else "Baseline"
                    dur_r = format_duration_hhmmss(r.get("duration_sec", 0))
                    tt_r = f"{r.get('time_taken_sec', 0.0):.1f}s ({r.get('speedup_ratio', 0.0):.1f}x)"
                    appr_disp = "✓ Primary" if is_p else "[Make Primary]"
                    self.clip_tree.insert(
                        str(idx),
                        tk.END,
                        iid=child_iid,
                        values=(
                            "",
                            f"↳ #{r_id}",
                            f"{p_badge}{r.get('model_name', r.get('model_id', ''))}",
                            "",
                            dur_r,
                            tt_r,
                            "—",
                            wer_disp,
                            appr_disp,
                            r.get("provider", "local"),
                        ),
                        tags=("child_run_row",),
                    )

        self._update_totals_row()
        self._recompute_column_widths()

        ev = data.get("event", {})
        ev_name = ev.get("name") or self.ctx.event_name_var.get() or "Unnamed"
        ev_date = ev.get("date") or self.ctx.event_date_var.get() or "No date"
        n_sessions = len(data.get("sessions", []))
        n_clips = sum(len(s.get("clips", [])) for s in data.get("sessions", []))
        self.event_summary_lbl.configure(
            text=f"Event: {ev_name} | Date: {ev_date} | {n_sessions} sessions, {n_clips} clips",
            foreground=theme.COLOR_TEXT_PRIMARY,
        )
        self.btn_go_to_stage2.pack_forget()
        self._update_output_paths()
        # Clear any pending-reload banner.
        self._reload_banner_var.set("")
        self._pending_reload = False
        self._update_gate_ui()

        # Enable rename button when sessions exist; update combine and approval controls
        has_sess = bool(self.manifest_data.get("sessions"))
        self.btn_rename_speaker.configure(state=tk.NORMAL if has_sess else tk.DISABLED)
        self._update_combine_and_approval_ui()
        return ""

    def _require_valid_manifest(self) -> bool:
        err = self._load_manifest(silent=False)
        if err:
            messagebox.showerror("Stage 3 stopped", err)
            return False
        return True

    # ------------------------------------------------------------------
    # Locked Totals Row (Task C)
    # ------------------------------------------------------------------

    def _on_table_configure(self, _event=None):
        """Debounced table resize handler to recompute column widths and sync totals."""
        if hasattr(self, "_resize_after_id") and self._resize_after_id:
            try:
                self.after_cancel(self._resize_after_id)
            except Exception:
                pass
        self._resize_after_id = self.after(35, self._recompute_column_widths)

    def destroy(self):
        """Clean up pending timer callbacks before destruction."""
        if hasattr(self, "_resize_after_id") and self._resize_after_id:
            try:
                self.after_cancel(self._resize_after_id)
            except Exception:
                pass
            self._resize_after_id = None
        super().destroy()

    def _on_tab_mapped(self, event=None):
        """Recompute column widths when Stage 3 tab becomes visible."""
        self.after(20, self._recompute_column_widths)

    def _on_tree_button_press(self, event=None):
        """Record column widths before user starts dragging separator."""
        if hasattr(self, "clip_tree"):
            cols = list(self.clip_tree["columns"])
            self._col_widths_before_drag = {c: self.clip_tree.column(c, "width") for c in cols}

    def _on_tree_button_release(self, event=None):
        """
        Redistribute width between neighbouring columns only when user drags a column separator,
        keeping the total width fitted to the viewport with zero horizontal scrollbar.
        """
        if not hasattr(self, "clip_tree"):
            return
        cols = list(self.clip_tree["columns"])
        if not getattr(self, "_col_widths_before_drag", None):
            self._sync_totals_columns()
            return

        curr_widths = {c: self.clip_tree.column(c, "width") for c in cols}
        changed_idx = None
        delta = 0
        for idx, c in enumerate(cols):
            diff = curr_widths[c] - self._col_widths_before_drag.get(c, curr_widths[c])
            if diff != 0:
                changed_idx = idx
                delta = diff
                break

        if changed_idx is not None and delta != 0:
            c_curr = cols[changed_idx]
            neigh_idx = changed_idx + 1 if changed_idx + 1 < len(cols) else changed_idx - 1
            c_neigh = cols[neigh_idx]

            min_c = self.clip_tree.column(c_curr, "minwidth") or 30
            min_n = self.clip_tree.column(c_neigh, "minwidth") or 30

            w_curr_orig = self._col_widths_before_drag.get(c_curr, curr_widths[c_curr])
            w_neigh_orig = self._col_widths_before_drag.get(c_neigh, curr_widths[c_neigh])

            max_delta = w_neigh_orig - min_n
            min_delta = min_c - w_curr_orig

            actual_delta = max(min_delta, min(max_delta, delta))
            self.clip_tree.column(c_curr, width=w_curr_orig + actual_delta)
            self.clip_tree.column(c_neigh, width=w_neigh_orig - actual_delta)

        self._col_widths_before_drag = {c: self.clip_tree.column(c, "width") for c in cols}
        self._sync_totals_columns()

    def refresh_theme(self) -> None:
        """Update colors and treeview status tags for active theme."""
        super().refresh_theme()
        if hasattr(self, "event_summary_lbl"):
            self.event_summary_lbl.configure(foreground=theme.COLOR_TEXT_PRIMARY)
        if hasattr(self, "lbl_out_transcripts"):
            self.lbl_out_transcripts.configure(bg=theme.COLOR_INPUT_BG, fg=theme.COLOR_TEXT_PRIMARY)
        if hasattr(self, "lbl_out_combined"):
            self.lbl_out_combined.configure(bg=theme.COLOR_INPUT_BG, fg=theme.COLOR_TEXT_PRIMARY)
        if hasattr(self, "clip_tree"):
            theme.configure_treeview_status_tags(self.clip_tree)
            theme.configure_treeview_status_tags(self.totals_tree)
            self._recompute_column_widths()
        if hasattr(self, "txt_transcript"):
            theme.style_code_panel(self.txt_transcript)
        if hasattr(self, "log_text"):
            theme.style_code_panel(self.log_text)
        if hasattr(self, "txt_log"):
            theme.style_code_panel(self.txt_log)
        if hasattr(self, "txt_comp_run1"):
            theme.style_code_panel(self.txt_comp_run1)
        if hasattr(self, "txt_comp_run2"):
            theme.style_code_panel(self.txt_comp_run2)

    def _recompute_column_widths(self, viewport_width: Optional[int] = None):
        """
        Dynamically calculate column widths based on font metrics:
        - Content-fit columns: check, idx, duration, start, failover, status, approved, engine
          Width = max(header longest word/line in bold, widest cell in cell font, totals text) + padding
        - Flexible columns: clip and speaker share remaining width in proportion to their longest text.
        - Font stepping: step down table font (never below 9pt) if content-fit columns alone exceed width.
        - Zero horizontal scrollbar: sum(column widths) exactly equals viewport width.
        """
        if not hasattr(self, "clip_tree"):
            return

        cols = list(self.clip_tree["columns"])
        if not cols:
            return

        vw = viewport_width
        if vw is None:
            vw = self.clip_tree.winfo_width()
        if vw is None or vw <= 20:
            vw = 1100

        try:
            f_bold = font.nametofont("SS_BodyBold")
            f_body = font.nametofont("SS_Body")
        except Exception:
            f_bold = font.Font(family="Segoe UI", size=10, weight="bold")
            f_body = font.Font(family="Segoe UI", size=10, weight="normal")

        scale = getattr(theme, "_CURRENT_SCALE", 1.0)
        pad = int(round(16 * scale))

        min_status_w = max(int(round(124 * scale)), f_bold.measure("21 of 21 timed") + pad)
        min_widths = {
            "check": int(round(50 * scale)),
            "idx": int(round(32 * scale)),
            "clip": int(round(145 * scale)),
            "speaker": int(round(115 * scale)),
            "duration": int(round(75 * scale)),
            "start": int(round(120 * scale)),
            "failover": int(round(75 * scale)),
            "status": min_status_w,
            "approved": int(round(90 * scale)),
            "engine": int(round(120 * scale)),
        }

        content_cols = [c for c in cols if c not in ("clip", "speaker")]
        computed: Dict[str, int] = {}
        actual_cell_w_unstepped: Dict[str, int] = {}
        actual_h_w_unstepped: Dict[str, int] = {}

        for c in content_cols:
            h_text = self.clip_tree.heading(c, "text") or ""
            h_lines = str(h_text).split("\n")
            max_h_w = max((f_bold.measure(line) for line in h_lines), default=0)
            actual_h_w_unstepped[c] = max_h_w

            max_cell_w = 0
            idx_in_cols = cols.index(c)
            for item in self.clip_tree.get_children():
                vals = self.clip_tree.item(item, "values")
                if idx_in_cols < len(vals):
                    w_val = f_body.measure(str(vals[idx_in_cols]))
                    if w_val > max_cell_w:
                        max_cell_w = w_val

            actual_cell_w_unstepped[c] = max_cell_w

            # Minimum sample measurements for empty or partially filled tables
            if c == "check":
                max_cell_w = max(max_cell_w, f_body.measure("[TEST]"))
            elif c == "duration":
                max_cell_w = max(max_cell_w, f_body.measure("00:00:00"))
            elif c == "start":
                max_cell_w = max(max_cell_w, f_body.measure("00:00:00"))
            elif c == "failover":
                max_cell_w = max(max_cell_w, f_body.measure("00:00:00.0"))
            elif c == "approved":
                max_cell_w = max(max_cell_w, f_body.measure("✓ Approved"))
            elif c == "status":
                max_cell_w = max(
                    max_cell_w,
                    f_body.measure("✓ Done (test)"),
                    f_bold.measure(f"{len(self.clips)} of {len(self.clips)} timed"),
                    f_bold.measure("21 of 21 timed"),
                )
            elif c == "engine":
                max_cell_w = max(max_cell_w, f_body.measure("Cloud: Parakeet"))

            needed_w = max(max_h_w, max_cell_w) + pad
            computed[c] = max(needed_w, min_widths.get(c, 40))

        total_cf = sum(computed.values())
        min_clip = min_widths["clip"]
        min_spk = min_widths["speaker"]
        eff_min_widths = dict(min_widths)

        # Step down table font if content-fit columns alone exceed available width
        eff_body_sz = f_body.cget("size")
        if total_cf + min_clip + min_spk > vw and eff_body_sz > 9:
            stepped_body = font.Font(family=f_body.cget("family"), size=9, weight="normal")
            stepped_bold = font.Font(family=f_bold.cget("family"), size=9, weight="bold")
            pad_stepped = int(round(12 * scale))
            for c in content_cols:
                h_text = self.clip_tree.heading(c, "text") or ""
                h_lines = str(h_text).split("\n")
                max_h_w = max((stepped_bold.measure(line) for line in h_lines), default=0)
                max_cell_w = 0
                idx_in_cols = cols.index(c)
                for item in self.clip_tree.get_children():
                    vals = self.clip_tree.item(item, "values")
                    if idx_in_cols < len(vals):
                        w_val = stepped_body.measure(str(vals[idx_in_cols]))
                        if w_val > max_cell_w:
                            max_cell_w = w_val
                if c == "status":
                    max_cell_w = max(
                        max_cell_w,
                        stepped_body.measure("✓ Done (test)"),
                        stepped_bold.measure(f"{len(self.clips)} of {len(self.clips)} timed"),
                        stepped_bold.measure("21 of 21 timed"),
                    )
                eff_min_widths[c] = max(max_h_w, int(round(min_widths.get(c, 30) * 0.9)))
                computed[c] = max(
                    max(max_h_w, max_cell_w) + pad_stepped,
                    eff_min_widths[c],
                    actual_cell_w_unstepped.get(c, 0),
                    actual_h_w_unstepped.get(c, 0),
                )
            total_cf = sum(computed.values())

        remainder = max(0, vw - total_cf)

        # Proportional sharing between clip and speaker based on longest text
        longest_clip_text = max((str(c.get("name", "")) for c in self.clips), key=len, default="Clip Filename Sample.mp4")
        longest_spk_text = max((str(c.get("speaker", "")) for c in self.clips), key=len, default="Speaker Name Sample")

        clip_text_w = max(f_body.measure(longest_clip_text), f_bold.measure("Clip Filename"))
        spk_text_w = max(f_body.measure(longest_spk_text), f_bold.measure("Speaker"))
        total_flex_weight = clip_text_w + spk_text_w
        if total_flex_weight <= 0:
            total_flex_weight = 1

        clip_ratio = clip_text_w / total_flex_weight
        spk_ratio = spk_text_w / total_flex_weight

        if remainder >= (min_clip + min_spk):
            clip_w = int(round(remainder * clip_ratio))
            spk_w = remainder - clip_w
            if clip_w < min_clip:
                clip_w = min_clip
                spk_w = remainder - clip_w
            elif spk_w < min_spk:
                spk_w = min_spk
                clip_w = remainder - spk_w
        else:
            clip_w = min_clip
            spk_w = min_spk

        computed["clip"] = clip_w
        computed["speaker"] = spk_w

        # Ensure exact pixel alignment with viewport (zero horizontal scrollbar)
        diff = vw - sum(computed.values())
        computed["clip"] += diff

        for c in cols:
            self.clip_tree.column(c, width=computed[c], minwidth=eff_min_widths.get(c, 30), stretch=False)

        self._col_widths_before_drag = {c: computed[c] for c in cols}
        self._sync_totals_columns()

    def _sync_totals_columns(self, _event=None):
        """Keep locked totals row columns exactly aligned with clip_tree columns."""
        if not hasattr(self, "totals_tree") or not hasattr(self, "clip_tree"):
            return
        cols = self.clip_tree["columns"]
        for c in cols:
            w = self.clip_tree.column(c, "width")
            self.totals_tree.column(c, width=w)
        if hasattr(self, "tree_scroll_y") and hasattr(self, "totals_spacer"):
            sw = self.tree_scroll_y.winfo_width()
            if sw > 1:
                self.totals_spacer.configure(width=sw)

    def _update_totals_row(self):
        """Recalculate summary metrics and update the locked totals row."""
        if not hasattr(self, "totals_tree") or not self.totals_tree.exists("totals"):
            return

        total_clips = len(self.clips)
        if total_clips == 0:
            self.totals_tree.item(
                "totals",
                values=("", "", "Total (0 clips)", "", "00:00:00", "—", "—", "0 of 0 timed", "", ""),
            )
            self._sync_totals_columns()
            return

        total_dur_sec = sum(c.get("duration", 0) for c in self.clips)
        paths = self._paths()

        total_time_taken_sec = 0.0
        timed_count = 0

        total_failover_sec = 0.0
        failover_count = 0

        for c in self.clips:
            name = c.get("name", "")
            # Time taken
            tt = _get_clip_time_taken(name, paths)
            sec = _parse_time_to_seconds(tt)
            if sec is not None:
                total_time_taken_sec += sec
                timed_count += 1

            # Failover time
            fo_sec, _ = _get_clip_failover_data(name, paths)
            if fo_sec is not None and fo_sec > 0:
                total_failover_sec += fo_sec
                failover_count += 1

        failover_str = format_failover_time(total_failover_sec) if failover_count > 0 else "—"

        if self.is_running:
            run_elapsed = max(0.0, time.monotonic() - self._run_start_monotonic)
            done_count = len(self._completed_clips_in_run)
            total_in_run = len(self._clips_in_current_run) if self._clips_in_current_run else total_clips
            remaining_count = max(0, total_in_run - done_count)

            # Live time taken total = finished clip times + running clip live time
            live_running_sec = 0.0
            if self._currently_transcribing_clip and self._current_clip_start_monotonic > 0:
                live_running_sec = max(0.0, time.monotonic() - self._current_clip_start_monotonic)

            eff_time_taken = total_time_taken_sec + live_running_sec
            time_taken_str = format_duration_hhmmss(eff_time_taken)

            # ETA calculation (hidden until there is enough data: >= 1 completed clip)
            if done_count >= 1 and remaining_count > 0:
                sum_done_times = sum(item.get("time", 0.0) for item in self._completed_clips_in_run)
                avg_time = sum_done_times / done_count
                eta_sec = avg_time * remaining_count
                eta_str = f"ETA ~{format_duration_hhmmss(eta_sec)} (est.)"
                clip_label = f"Total ({total_clips} clips) · Run {format_duration_hhmmss(run_elapsed)} · {done_count} of {total_in_run} done · {eta_str}"
            else:
                clip_label = f"Total ({total_clips} clips) · Run {format_duration_hhmmss(run_elapsed)} · {done_count} of {total_in_run} done"

            note_str = f"{done_count} of {total_in_run} done"
        else:
            time_taken_str = format_duration_hhmmss(total_time_taken_sec) if timed_count > 0 else "—"
            note_str = f"{timed_count} of {total_clips} timed"
            clip_label = f"Total ({total_clips} clip{'s' if total_clips != 1 else ''})"

        self.totals_tree.item(
            "totals",
            values=(
                "",
                "",
                clip_label,
                "",
                format_duration_hhmmss(total_dur_sec),
                time_taken_str,
                failover_str,
                note_str,
                "",
                "",
            ),
        )
        self._sync_totals_columns()

    # ------------------------------------------------------------------
    # Tree tooltips & hover
    # ------------------------------------------------------------------

    def _on_tree_motion(self, event):
        iid = self.clip_tree.identify_row(event.y)
        col = self.clip_tree.identify_column(event.x)
        if not iid or not col:
            self._hide_tree_tooltip()
            return

        try:
            col_idx = int(col.replace("#", "")) - 1
        except ValueError:
            self._hide_tree_tooltip()
            return

        item_vals = self.clip_tree.item(iid, "values")
        if not item_vals or col_idx >= len(item_vals):
            self._hide_tree_tooltip()
            return

        cell_text = str(item_vals[col_idx]).strip()
        if not cell_text or cell_text == "—":
            self._hide_tree_tooltip()
            return

        tooltip_text = cell_text
        if col_idx == 6:
            try:
                idx = int(iid) - 1
                if 0 <= idx < len(self.clips):
                    c = self.clips[idx]
                    paths = self._paths()
                    fo_sec, fo_info = _get_clip_failover_data(c.get("name", ""), paths)
                    if fo_info and fo_sec is not None:
                        reason = fo_info.get("fallback_reason", "unknown")
                        retries = fo_info.get("retries", 0)
                        retry_wait = fo_info.get("retry_wait_seconds", 0.0)
                        model_load = fo_info.get("local_model_load_seconds", 0.0)
                        resume_off = fo_info.get("resume_offset", 0.0)
                        tooltip_text = (
                            f"Failover Breakdown:\n"
                            f"• Reason: {reason}\n"
                            f"• Retries: {retries} (waited {retry_wait:.1f}s)\n"
                            f"• Local model load: {model_load:.2f}s\n"
                            f"• Resume offset: {resume_off:.1f}s\n"
                            f"• Handover time: {fo_sec:.1f}s"
                        )
            except Exception:
                pass
        elif col_idx == 9:
            try:
                idx = int(iid) - 1
                if 0 <= idx < len(self.clips):
                    clip_name = self.clips[idx].get("name", "")
                    paths = self._paths()
                    fo_sec, fo_info = _get_clip_failover_data(clip_name, paths)
                    if fo_info:
                        reason = fo_info.get("fallback_reason") or fo_info.get("error_class", "unknown")
                        short_err = fo_info.get("message") or fo_info.get("error", "")
                        resume_off = fo_info.get("resume_offset_str") or f"{fo_info.get('resume_offset', 0.0):.1f}s"
                        tooltip_text = (
                            f"Engine: Fallback (Local)\n"
                            f"• Cloud failed: {reason}\n"
                            f"• Message: {short_err[:80]}\n"
                            f"• Resumed locally from: {resume_off}\n"
                            f"• Failover handover: {fo_sec:.1f}s"
                        )
                    else:
                        details = self._get_engine_tooltip_details(clip_name, iid)
                        tooltip_text = (
                            f"Engine & Model Details:\n"
                            f"• Provider: {details.get('provider', 'NVIDIA Riva (Cloud)')}\n"
                            f"• Model: {details.get('model', 'nvidia/parakeet-ctc-1.1b')}\n"
                            f"• Hardware: {details.get('device', 'GPU')}\n"
                            f"• Resume offset: {details.get('resume_offset', '0.0s')}"
                        )
            except Exception:
                pass

        cell_id = (iid, col_idx)
        if getattr(self, "_tree_tooltip_cell", None) == cell_id:
            return

        self._hide_tree_tooltip()
        self._tree_tooltip_cell = cell_id

        x = event.x_root + 15
        y = event.y_root + 12
        tw = tk.Toplevel(self)
        tw.wm_overrideredirect(True)
        tw.wm_geometry(f"+{x}+{y}")
        try:
            tw.attributes("-topmost", True)
        except Exception:
            pass
        self._tree_tooltip = tw

        label = tk.Label(
            tw,
            text=tooltip_text,
            background="#ffffea",
            foreground="#222222",
            relief=tk.SOLID,
            borderwidth=1,
            font=("Segoe UI", 8),
            justify=tk.LEFT,
            padx=6,
            pady=4,
        )
        label.pack()

    def _hide_tree_tooltip(self, _event=None):
        self._tree_tooltip_cell = None
        tw = getattr(self, "_tree_tooltip", None)
        if tw:
            try:
                tw.destroy()
            except Exception:
                pass
            self._tree_tooltip = None

    def _on_user_scroll(self, _event=None):
        """User scrolled manually; freeze automatic table scrolling for 10 seconds."""
        self._last_manual_scroll_time = time.monotonic()

    def _schedule_ui_tick(self):
        if self._ui_timer_id is not None:
            try:
                self.after_cancel(self._ui_timer_id)
            except Exception:
                pass
            self._ui_timer_id = None
        if self.is_running:
            self._ui_timer_id = self.after(500, self._on_ui_tick)

    def _on_ui_tick(self):
        self._ui_timer_id = None
        if not self.is_running:
            return

        now = time.monotonic()

        # Update running row
        if self._currently_transcribing_clip and self._current_running_iid:
            iid = self._current_running_iid
            if self.clip_tree.exists(iid):
                vals = list(self.clip_tree.item(iid, "values"))
                while len(vals) < 10:
                    vals.append("")

                clip_elapsed = max(0.0, now - self._current_clip_start_monotonic)
                vals[5] = format_duration_hhmmss(clip_elapsed)

                if self._current_intra_progress_pct is not None:
                    st = f"● {self._current_substage} {int(self._current_intra_progress_pct)}%"
                else:
                    st = f"● {self._current_substage}"

                if self._last_worker_event_monotonic > 0 and (now - self._last_worker_event_monotonic > 60.0):
                    st += " (still working…)"

                vals[7] = st
                vals[9] = self._current_engine_name

                if self._failover_active:
                    vals[6] = format_failover_time(now - self._failover_start_monotonic)

                self.clip_tree.item(iid, values=vals)

                if (now - self._last_manual_scroll_time) > 10.0:
                    self.clip_tree.see(iid)

        # Update totals row
        self._update_totals_row()

        # Schedule next tick (~2 Hz)
        self._schedule_ui_tick()

    def _get_engine_tooltip_details(self, clip_name: str, iid: str) -> Dict[str, str]:
        if self.is_running and self._currently_transcribing_clip == clip_name:
            is_cloud = "Parakeet" in self._current_engine_name or self.engine_var.get() == "cloud"
            provider = "NVIDIA Hosted Riva gRPC (build.nvidia.com)" if is_cloud else "Local openai-whisper"
            model = "nvidia/parakeet-ctc-1.1b" if is_cloud else "openai/whisper-medium"
            device = "Cloud Hosted" if is_cloud else ("GPU (CUDA)" if torch.cuda.is_available() else "CPU")
            resume = f"{self._current_engine_details.get('resume_offset', 0.0):.1f}s"
            return {"provider": provider, "model": model, "device": device, "resume_offset": resume}

        paths = self._paths()
        stem, _ = os.path.splitext(clip_name)
        rec = _state_read_clip(paths.get("state", ""), stem) if paths.get("state") else {}
        prov = rec.get("provider", "Local" if not rec.get("engine") or "local" in rec.get("engine") else "Cloud")
        model = rec.get("model", "whisper-medium")
        device = rec.get("device", "GPU" if torch.cuda.is_available() else "CPU")
        resume = f"{rec.get('resume_offset', 0.0):.1f}s"
        return {"provider": prov, "model": model, "device": device, "resume_offset": resume}

    def _finalize_names_clicked(self):
        if not self.vault.is_unlocked():
            ok = ensure_vault_unlocked_preflight(self.winfo_toplevel(), self.vault)
            if not ok:
                return
        self.vault_chip.refresh()
        self._update_combine_and_approval_ui()

    def _reidentify_clicked(self):
        """
        Re-runs matching on existing transcripts without re-transcribing.
        Opens 'Who is this?' ReidentifyDialog for reviewing unknown speech clusters.
        """
        paths = self._paths()
        if not paths or not self.manifest_data:
            return

        # 1. If vault is initialized but locked, offer unlock
        if self.vault.is_initialized() and not self.vault.is_unlocked():
            dlg = UnlockVaultDialog(self.winfo_toplevel(), self.vault)
            self.winfo_toplevel().wait_window(dlg)
            self.vault_chip.refresh()

        event_id = self.manifest_data.get("event_id", "")
        event_root = paths["event_root"]
        transcripts_dir = paths.get("transcripts", os.path.join(event_root, "scribe", "transcripts"))

        if not os.path.isdir(transcripts_dir):
            messagebox.showinfo("Re-Identify", "No transcripts directory found.", parent=self)
            return

        # Load event roster if unlocked
        roster_items = []
        if self.vault.is_unlocked() and event_id:
            try:
                roster_mgr = EventRosterManager(self.vault)
                roster_items = roster_mgr.load_event_roster(event_id)
            except Exception as e:
                self._log(f"[RE-IDENTIFY WARNING] Could not load event roster: {e}")

        # Check if in-memory cached clusters exist, or load from scribe/state/clusters/, or discover from transcripts
        clusters = list(self._cached_clusters.values()) if hasattr(self, "_cached_clusters") and self._cached_clusters else []
        has_acoustic_data = bool(clusters)

        if not clusters:
            persisted_clusters = load_all_event_clusters(event_root)
            if persisted_clusters:
                clusters = list(persisted_clusters.values())
                has_acoustic_data = True
                self._cached_clusters = persisted_clusters

        if not clusters:
            clusters = self._discover_unidentified_clusters_from_transcripts(transcripts_dir)
            has_acoustic_data = False

        if not clusters:
            messagebox.showinfo(
                "Re-Identify",
                "All speech turns in completed transcripts are already attributed to the session roster.",
                parent=self,
            )
            return

        # If vault is unlocked and acoustic centroids exist, run automatic matching!
        if self.vault.is_unlocked() and roster_items and has_acoustic_data:
            try:
                storage = VaultV2StorageEngine(self.vault)
                ident_engine = IdentificationEngine(vault_storage=storage)
                ident_engine.match_roster(clusters, roster_items, is_solo=False)
                self._log(f"[RE-IDENTIFY] Automatically matched {len(clusters)} voice cluster(s) with unlocked vault.")
            except Exception as match_err:
                self._log(f"[RE-IDENTIFY WARNING] Automatic acoustic matching error: {match_err}")

        def _on_apply(assignments: Dict[str, str]):
            cnt, backup_dir = self._apply_cluster_reassignments_to_transcripts(transcripts_dir, assignments)
            self._refresh_status_column()
            self._update_combine_and_approval_ui()
            self._log(f"[RE-IDENTIFY] Successfully updated {cnt} turn(s) across transcripts with zero re-transcription. Backup: {backup_dir}")
            if messagebox.askyesno(
                "Re-Identification Complete",
                f"Updated speaker attributions across {cnt} turn(s) with zero re-transcription.\n\n"
                f"Previous transcript version archived to:\n{backup_dir}\n\n"
                "Would you like to undo this re-identification now?",
                parent=self,
            ):
                self._undo_reidentify_clicked()

        def _on_enroll(snippet_bytes: bytes):
            if not self.vault.is_unlocked():
                dlg = UnlockVaultDialog(self.winfo_toplevel(), self.vault)
                self.winfo_toplevel().wait_window(dlg)
                self.vault_chip.refresh()
                if not self.vault.is_unlocked():
                    return
            try:
                storage = VaultV2StorageEngine(self.vault)
                embedding_engine = LocalEmbeddingEngine(prefer_gpu=False)
                enrollment_mgr = EnrollmentManager(storage, embedding_engine)
                dlg = VoiceProfileEnrollmentDialog(self.winfo_toplevel(), enrollment_mgr)
                self.winfo_toplevel().wait_window(dlg)
            except Exception as err:
                messagebox.showerror("Enrollment Error", f"Could not launch enrollment: {err}", parent=self)

        dlg = ReidentifyDialog(
            parent=self.winfo_toplevel(),
            clusters=clusters,
            event_roster=roster_items,
            on_apply_callback=_on_apply,
            on_enroll_callback=_on_enroll,
            has_acoustic_data=has_acoustic_data,
        )
        self.winfo_toplevel().wait_window(dlg)

    def _discover_unidentified_clusters_from_transcripts(self, transcripts_dir: str) -> List[SpeakerCluster]:
        """Scans transcript files for unidentified or generic speaker labels ([Unidentified], [Speaker 1], etc.)."""
        clusters_map: Dict[str, SpeakerCluster] = {}
        tag_re = re.compile(r"^\[([^\]]+)\]\s*\[\d\d:\d\d:\d\d(?:\.\d+)?\s*-\s*\d\d:\d\d:\d\d(?:\.\d+)?\]\s*(.*)$")

        for fname in os.listdir(transcripts_dir):
            if not fname.endswith(".txt") or not is_completed_transcript_file(os.path.join(transcripts_dir, fname)):
                continue
            path = os.path.join(transcripts_dir, fname)
            try:
                with open(path, "r", encoding="utf-8", errors="ignore") as fh:
                    for line in fh:
                        m = tag_re.match(line.strip())
                        if m:
                            tag = m.group(1).strip()
                            if tag in ("[Unidentified]", "Unidentified") or tag.startswith("Speaker ") or tag.startswith("Cluster "):
                                c_id = f"[{tag}]" if tag in ("[Unidentified]", "Unidentified") else tag
                                if c_id not in clusters_map:
                                    clusters_map[c_id] = SpeakerCluster(
                                        cluster_id=c_id,
                                        matched_alias="[Unidentified]",
                                        is_unidentified=True,
                                        clip_names=[fname],
                                        turn_intervals=[(0.0, 5.0)],
                                        sample_count=1,
                                    )
                                else:
                                    clusters_map[c_id].sample_count += 1
                                    clusters_map[c_id].turn_intervals.append((0.0, 5.0))
            except Exception:
                pass

        return list(clusters_map.values())

    def _apply_cluster_reassignments_to_transcripts(self, transcripts_dir: str, assignments: Dict[str, str]) -> Tuple[int, str]:
        """
        Atomically archives transcripts and replaces speaker cluster tags at line-starts with newly assigned aliases.
        Returns: (total_replacements, backup_directory_path)
        """
        if not assignments:
            return 0, ""

        # 1. Prepare timestamped backup directory under scribe/state/reidentify_backups/
        ts_str = datetime.now().strftime("%Y%m%d_%H%M%S")
        event_root = getattr(self, "current_event_dir", "") or ""
        if not event_root and transcripts_dir:
            event_root = os.path.abspath(os.path.join(transcripts_dir, "..", ".."))

        backup_base = os.path.join(event_root, "scribe", "state", "reidentify_backups")
        backup_dir = os.path.join(backup_base, ts_str)
        os.makedirs(backup_dir, exist_ok=True)

        backed_up_files = []
        for fname in os.listdir(transcripts_dir):
            if fname.endswith(".txt"):
                src_f = os.path.join(transcripts_dir, fname)
                dst_f = os.path.join(backup_dir, fname)
                shutil.copy2(src_f, dst_f)
                backed_up_files.append(fname)

        meta_path = os.path.join(backup_dir, "metadata.json")
        try:
            with open(meta_path, "w", encoding="utf-8") as mfh:
                json.dump({
                    "timestamp": ts_str,
                    "assignments": assignments,
                    "backed_up_files": backed_up_files,
                }, mfh, indent=2)
        except Exception as e:
            self._log(f"[RE-IDENTIFY WARNING] Could not write metadata.json: {e}")

        self._latest_reidentify_backup = backup_dir

        # 2. Perform line-start speaker tag replacements
        total_replacements = 0
        for fname in backed_up_files:
            path = os.path.join(transcripts_dir, fname)
            try:
                with open(path, "r", encoding="utf-8", errors="ignore") as fh:
                    content = fh.read()

                modified, cnt = substitute_line_start_speaker_tags(content, assignments)
                if cnt > 0 and modified != content:
                    total_replacements += cnt
                    tmp_p = path + ".tmp"
                    with open(tmp_p, "w", encoding="utf-8") as fh:
                        fh.write(modified)
                    os.replace(tmp_p, path)
            except Exception as e:
                self._log(f"[RE-IDENTIFY ERROR] Failed updating {fname}: {e}")

        return total_replacements, backup_dir

    def _undo_reidentify_clicked(self):
        """Restores transcript files from the most recent Re-Identify backup."""
        event_root = getattr(self, "current_event_dir", "") or ""
        paths = getattr(self, "paths", {}) or {}
        transcripts_dir = paths.get("transcripts", "")
        if not transcripts_dir and event_root:
            transcripts_dir = os.path.join(event_root, "scribe", "transcripts")

        if not transcripts_dir or not os.path.isdir(transcripts_dir):
            messagebox.showwarning("Undo Re-Identify", "No transcripts directory found.", parent=self)
            return

        # Locate latest backup directory
        backup_dir = getattr(self, "_latest_reidentify_backup", None)
        if not backup_dir or not os.path.isdir(backup_dir):
            backup_base = os.path.join(event_root, "scribe", "state", "reidentify_backups")
            if os.path.isdir(backup_base):
                subdirs = sorted(
                    [os.path.join(backup_base, d) for d in os.listdir(backup_base) if os.path.isdir(os.path.join(backup_base, d))],
                    reverse=True,
                )
                if subdirs:
                    backup_dir = subdirs[0]

        if not backup_dir or not os.path.isdir(backup_dir):
            messagebox.showinfo("Undo Re-Identify", "No previous Re-Identify backup found to restore.", parent=self)
            return

        if not messagebox.askyesno(
            "Undo Re-Identify",
            f"Restore transcripts from backup?\n\n{backup_dir}\n\nThis will revert all speaker reassignments to their state before this Re-Identify operation.",
            parent=self,
        ):
            return

        restored_count = 0
        for fname in os.listdir(backup_dir):
            if fname.endswith(".txt"):
                src_f = os.path.join(backup_dir, fname)
                dst_f = os.path.join(transcripts_dir, fname)
                tmp_f = dst_f + ".tmp"
                shutil.copy2(src_f, tmp_f)
                os.replace(tmp_f, dst_f)
                restored_count += 1

        self._log(f"[RE-IDENTIFY UNDO] Restored {restored_count} transcript file(s) from {backup_dir}")
        self._refresh_status_column()
        self._update_combine_and_approval_ui()
        messagebox.showinfo(
            "Undo Re-Identify",
            f"Successfully restored {restored_count} transcript file(s) from backup.",
            parent=self,
        )

    # ------------------------------------------------------------------
    # Right-click: force re-transcribe
    # ------------------------------------------------------------------

    def _on_tree_right_click(self, event):
        iid = self.clip_tree.identify_row(event.y)
        if not iid:
            return
        # Select the right-clicked row so the menu applies to it visually.
        self.clip_tree.selection_set(iid)
        self._ctx_menu.tk_popup(event.x_root, event.y_root)

    def _toggle_force_relist(self):
        """Toggle Force re-transcribe on all currently selected rows."""
        selected = self.clip_tree.selection()
        if not selected:
            return
        paths = self._paths()
        ghash = self._current_ghash()
        for iid in selected:
            idx  = int(iid) - 1
            if idx < 0 or idx >= len(self.clips):
                continue
            name = self.clips[idx].get("name", "")
            stem, _ = os.path.splitext(name)
            if name in self._force_clips:
                self._force_clips.discard(name)
                # Recompute real disk status.
                status = _clip_disk_status(self.clips[idx], paths, ghash) if paths else "Pending"
                tag    = self._status_tag(status)
                is_appr = get_clip_approval(paths["state"], stem) if (paths and status in ("Done", "Done (test)", "Edited", "Completed")) else False
            else:
                # If clip has manual edits, warn user and make backup to scribe\state\edited_backup\
                if paths:
                    rec = _state_read_clip(paths["state"], stem)
                    if rec and rec.get("edited"):
                        confirm = messagebox.askyesno(
                            "Overwrite Manual Edits?",
                            f"Clip '{name}' has manual edits.\n\n"
                            "Force re-transcribing will overwrite your manual edits.\n"
                            "A backup will be saved to scribe\\state\\edited_backup\\.\n\n"
                            "Do you want to proceed?",
                        )
                        if not confirm:
                            continue

                        backup_dir = os.path.join(paths["state"], "edited_backup")
                        os.makedirs(backup_dir, exist_ok=True)
                        src_txt = os.path.join(paths["transcripts"], f"{stem}.txt")
                        if not os.path.isfile(src_txt):
                            src_txt = os.path.join(paths["test"], f"{stem}.txt")
                        if os.path.isfile(src_txt):
                            shutil.copy2(src_txt, os.path.join(backup_dir, f"{stem}.txt"))
                            self._log(f"[BACKUP] Saved backup of edited transcript to {os.path.join(backup_dir, f'{stem}.txt')}")

                self._force_clips.add(name)
                status = "Pending (forced)"
                tag    = "forced"
                is_appr = False
                if paths:
                    clear_multiple_clip_approvals(paths["state"], [stem])
                    _session_tracker.reset_video_state(self.clips[idx].get("path", ""), paths["state"])

            appr_text = format_approval_display(is_appr)
            appr_tag  = "approved_tag" if is_appr else "unapproved_tag"
            vals    = list(self.clip_tree.item(iid, "values"))
            while len(vals) < 10:
                vals.append("")
            vals[7] = format_status_display(status)
            vals[8] = appr_text
            row_tag = "even_row" if idx % 2 == 0 else "odd_row"
            row_tags = (row_tag, tag, appr_tag) if tag else (row_tag, appr_tag)
            self.clip_tree.item(iid, values=vals, tags=row_tags)
        self._update_totals_row()
        self._recompute_column_widths()
        self._update_combine_and_approval_ui()

    # ------------------------------------------------------------------
    # Gate UI
    # ------------------------------------------------------------------

    def _update_gate_ui(self):
        if not self.ctx.required_ready() or self.is_running:
            if not self.is_running:
                self.btn_run_full.configure(state=tk.DISABLED)
            return
        if not self._loaded_event_root or not self.clips:
            self.btn_run_full.configure(state=tk.DISABLED)
            return
        paths    = self._paths()
        approved = TranscriptionManager.is_test_approved(paths["scribe"]) if paths else False
        if approved:
            self.gate_status_var.set("[APPROVED] Test Gate Passed. Full Run Unlocked.")
            self.gate_indicator.configure(foreground="#28a745")
            self.btn_run_full.configure(state=tk.NORMAL)
        else:
            self.gate_status_var.set("[LOCKED] Test Gate Required. Run and approve 1-2 test clips first.")
            self.gate_indicator.configure(foreground="#d9534f")
            self.btn_run_full.configure(state=tk.DISABLED)

    def _approve_test_gate(self):
        with self.preserve_scroll():
            if not self._require_valid_manifest():
                return
            paths    = self._paths()
            test_dir = paths["test"]
            if not os.path.exists(test_dir) or not os.listdir(test_dir):
                messagebox.showwarning(
                    "Test Gate",
                    f"{format_paths_log_header(paths)}\n\nNo test transcripts found in scribe\\test\\. "
                    "Please run test transcription first.",
                )
                return

            confirm = messagebox.askyesno(
                "Approve Test Transcripts",
                f"{format_paths_log_header(paths)}\n\n"
                "Are the test transcripts accurate and verified?\nApproving will unlock the Full Run button.",
            )
            if confirm:
                TranscriptionManager.set_test_approved(paths["scribe"], approved=True, notes="Approved by user in UI.")
                self._update_gate_ui()
                messagebox.showinfo("Approved", "Test Gate Approved! 'Run All Clips (Full Run)' is now unlocked.")

    # ------------------------------------------------------------------
    # Task A: Pre-run guard for placeholder speaker names
    # ------------------------------------------------------------------

    def _check_placeholder_names(self, clips_to_run: list) -> bool:
        """
        Return True if safe to proceed.
        If any clip still has a placeholder "Speaker N" name, show a warning
        dialog with 'Go Back' (returns False) and 'Continue Anyway' (True).
        """
        with self.preserve_scroll():
            placeholder_speakers = sorted({
                c.get("speaker", "")
                for c in clips_to_run
                if _is_placeholder_name(c.get("speaker", ""))
            })
            if not placeholder_speakers:
                return True

            names_str = "\n".join(f"  • {s}" for s in placeholder_speakers)
            win = tk.Toplevel(self)
            win.title("Speaker Names Not Set")
            theme.center_dialog(win, self, width=480, height=280, min_width=400, min_height=220)
            win.transient(self)
            win.grab_set()
            win.resizable(True, True)

            result = tk.BooleanVar(value=False)

            def go_back():
                result.set(False)
                win.destroy()

            def continue_anyway():
                result.set(True)
                win.destroy()

            win.bind("<Escape>", lambda _e: go_back())
            win.bind("<Return>", lambda _e: continue_anyway())

            btn_row = ttk.Frame(win)
            btn_row.pack(fill=tk.X, padx=14, pady=10, side=tk.BOTTOM)
            ttk.Button(btn_row, text="Continue Anyway", command=continue_anyway).pack(side=tk.RIGHT, padx=4)
            ttk.Button(btn_row, text="Go Back", command=go_back).pack(side=tk.RIGHT)

            ttk.Label(
                win,
                text="⚠  Placeholder speaker names detected",
                font=("Segoe UI", 10, "bold"),
                foreground="#d9534f",
            ).pack(pady=(14, 4), padx=14, anchor=tk.W)
            lbl_placeholder_msg = ttk.Label(
                win,
                text=(
                    f"The following sessions still have auto-generated names:\n\n"
                    f"{names_str}\n\n"
                    "These names will appear verbatim in transcript files.\n"
                    "Go back to Stage 2 to assign real names (recommended)."
                ),
                font=("Segoe UI", 9),
                justify=tk.LEFT,
                wraplength=440,
            )
            lbl_placeholder_msg.pack(padx=14, anchor=tk.W, fill=tk.BOTH, expand=True)

            def _on_names_cfg(e):
                if e.widget == win and lbl_placeholder_msg.winfo_exists():
                    lbl_placeholder_msg.configure(wraplength=max(200, e.width - 28))
            win.bind("<Configure>", _on_names_cfg)

            self.wait_window(win)
            return result.get()

    # ------------------------------------------------------------------
    # Task A: Rename existing transcripts dialog
    # ------------------------------------------------------------------

    def _open_rename_dialog(self):
        """Open the Rename Existing Transcripts dialog."""
        with self.preserve_scroll():
            if not self.manifest_data or not self.manifest_data.get("sessions"):
                messagebox.showwarning("No Manifest", "Load a manifest with sessions first.")
                return
            if self.is_running:
                messagebox.showwarning("Rename blocked", "Cannot rename while a transcription run is active.")
                return

            paths = self._paths()
            if not paths:
                return

            sessions = self.manifest_data.get("sessions", [])

            dialog = tk.Toplevel(self)
            dialog.title("Rename Existing Transcripts")
            theme.center_dialog(dialog, self, width=600, height=520, min_width=500, min_height=420)
            dialog.transient(self)
            dialog.grab_set()
            dialog.resizable(True, True)
            dialog.bind("<Escape>", lambda _e: dialog.destroy())

        btn_row = ttk.Frame(dialog)
        btn_row.pack(fill=tk.X, padx=12, pady=8, side=tk.BOTTOM)

        btn_apply = ttk.Button(btn_row, text="Apply Rename", state=tk.DISABLED)
        btn_preview = ttk.Button(btn_row, text="Preview Changes")
        btn_close = ttk.Button(btn_row, text="Close", command=dialog.destroy)

        btn_close.pack(side=tk.RIGHT, padx=4)
        btn_apply.pack(side=tk.RIGHT, padx=4)
        btn_preview.pack(side=tk.RIGHT, padx=4)

        # ---- Session selector ----
        ttk.Label(dialog, text="Session to rename:", font=("Segoe UI", 9, "bold")).pack(
            anchor=tk.W, padx=12, pady=(12, 2)
        )
        session_labels = [f"Session {s['session_id']}: {s['speaker']}" for s in sessions]
        session_var = tk.StringVar()
        session_combo = ttk.Combobox(
            dialog, textvariable=session_var, values=session_labels, state="readonly", width=45
        )
        session_combo.pack(anchor=tk.W, padx=12, pady=2)
        if session_labels:
            session_combo.current(0)

        # ---- New name ----
        ttk.Label(dialog, text="New speaker name:", font=("Segoe UI", 9, "bold")).pack(
            anchor=tk.W, padx=12, pady=(8, 2)
        )
        name_var = tk.StringVar()
        name_entry = ttk.Entry(dialog, textvariable=name_var, width=45)
        name_entry.pack(anchor=tk.W, padx=12, pady=2)

        # ---- Preview / result area ----
        ttk.Label(dialog, text="Dry-run preview:", font=("Segoe UI", 9, "bold")).pack(
            anchor=tk.W, padx=12, pady=(10, 2)
        )
        preview_text = scrolledtext.ScrolledText(
            dialog, height=12, font=("Consolas", 8), state="disabled", wrap=tk.WORD
        )
        preview_text.pack(fill=tk.BOTH, expand=True, padx=12, pady=4)

        def _set_preview(txt: str):
            preview_text.configure(state="normal")
            preview_text.delete("1.0", tk.END)
            preview_text.insert(tk.END, txt)
            preview_text.configure(state="disabled")

        # Internal state for the Apply step
        _state = {"session": None, "old_name": None, "new_name": None, "files": []}

        def _get_selected_session():
            sel = session_var.get()
            for s in sessions:
                label = f"Session {s['session_id']}: {s['speaker']}"
                if label == sel:
                    return s
            return None

        def do_preview():
            btn_apply.configure(state=tk.DISABLED)
            _state["files"] = []

            sess = _get_selected_session()
            if not sess:
                _set_preview("✗ No session selected.")
                return

            new_name = name_var.get().strip()
            if not new_name:
                _set_preview("✗ New name is empty.")
                return

            old_name = sess["speaker"]
            if old_name == new_name:
                _set_preview("✗ New name is the same as the current name.")
                return

            # Collect .txt files for this session from both output dirs.
            clip_stems = [os.path.splitext(c.get("name", ""))[0] for c in sess.get("clips", [])]
            search_dirs = [paths["transcripts"], paths["test"]]
            files_to_update = []
            for stem in clip_stems:
                for d in search_dirs:
                    p = os.path.join(d, f"{stem}.txt")
                    if _is_completed_transcript(p):
                        files_to_update.append((p, stem))
                        break

            if not files_to_update:
                _set_preview(
                    f"No completed transcript files found for session '{old_name}'.\n"
                    "Nothing to rename."
                )
                return

            lines = [
                f"Renaming '{old_name}'  →  '{new_name}'",
                f"Session {sess['session_id']} | {len(files_to_update)} transcript file(s)\n",
            ]
            total_changes = 0
            for fpath, stem in files_to_update:
                changes = _dry_run_rename(fpath, old_name, new_name)
                total_changes += len(changes)
                rel = os.path.relpath(fpath, paths["scribe"])
                lines.append(f"  {rel}  ({len(changes)} line change(s))")
                for lineno, old_l, new_l in changes[:4]:
                    lines.append(f"    L{lineno}: {old_l!r}")
                    lines.append(f"         → {new_l!r}")
                if len(changes) > 4:
                    lines.append(f"    … and {len(changes) - 4} more segment lines")

            lines.append(f"\nTotal lines to change: {total_changes}")
            lines.append("Manifest will also be updated.")
            lines.append("\nSpoken text is NOT modified.")

            _set_preview("\n".join(lines))

            if total_changes > 0:
                _state["session"]  = sess
                _state["old_name"] = old_name
                _state["new_name"] = new_name
                _state["files"]    = files_to_update
                btn_apply.configure(state=tk.NORMAL)

        def do_apply():
            sess     = _state["session"]
            old_name = _state["old_name"]
            new_name = _state["new_name"]
            files    = _state["files"]

            if not sess or not files:
                return

            changed_files = 0
            total_lines   = 0
            errors        = []
            for fpath, _stem in files:
                try:
                    n = _apply_rename_transcript(fpath, old_name, new_name)
                    if n:
                        changed_files += 1
                        total_lines   += n
                except OSError as exc:
                    errors.append(f"  ✗ {fpath}: {exc}")

            # Update manifest
            try:
                _update_manifest_speaker(paths["manifest"], sess["session_id"], new_name)
            except Exception as exc:
                errors.append(f"  ✗ Manifest update failed: {exc}")

            # Refresh in-memory manifest_data and clip list
            self._load_manifest(silent=True)

            # Clear approval for all clips belonging to this renamed session
            session_stems = [stem for _fpath, stem in files]
            clear_multiple_clip_approvals(paths["state"], session_stems)

            # Mark existing combined files as stale; DO NOT REBUILD AUTOMATICALLY!
            set_combined_stale(paths["state"], True)

            # Refresh in-memory manifest_data and clip list
            self._load_manifest(silent=True)

            result_lines = [
                f"✓ Renamed '{old_name}' → '{new_name}'",
                f"  Files updated: {changed_files}",
                f"  Lines changed: {total_lines}",
                f"  Approvals cleared: {', '.join(session_stems)}",
                "  Combined files marked STALE — review & re-approve before combining.",
            ]

            if errors:
                result_lines.append("\nErrors:")
                result_lines.extend(errors)

            _set_preview("\n".join(result_lines))
            btn_apply.configure(state=tk.DISABLED)

            # Refresh the combo to show the new name
            updated_labels = [
                f"Session {s['session_id']}: {s['speaker']}"
                for s in self.manifest_data.get("sessions", sessions)
            ]
            session_combo["values"] = updated_labels
            for i, lbl in enumerate(updated_labels):
                if f"Session {sess['session_id']}:" in lbl:
                    session_combo.current(i)
                    break

            self.status_callback(f"Renamed '{old_name}' → '{new_name}' in {changed_files} file(s).")
            self._update_combine_and_approval_ui()

            # Notify user that approval was cleared for renamed clips
            messagebox.showinfo(
                "Speaker Renamed — Approvals Cleared",
                f"Renamed '{old_name}' → '{new_name}' in {changed_files} file(s).\n\n"
                f"Approval has been cleared for {len(session_stems)} clip(s):\n"
                + ", ".join(session_stems) + "\n\n"
                "Existing combined files are marked stale. Please review and re-approve clips, then click 'Combine approved transcripts'.",
            )

        btn_preview.configure(command=do_preview)
        btn_apply.configure(command=do_apply)
        dialog.wait_window()

    def check_unsaved_changes(self) -> bool:
        """Return True if safe to proceed (no unsaved edits or user resolved them)."""
        if self._active_viewer and self._active_viewer.winfo_exists():
            ok = self._active_viewer.prompt_unsaved_if_dirty()
            if ok and self._active_viewer and self._active_viewer.winfo_exists():
                self._active_viewer.destroy()
                self._active_viewer = None
            return ok
        return True

    def _on_clip_double_click(self, event):
        """Double-clicking a clip opens its transcript in the in-app viewer/editor."""
        iid = self.clip_tree.identify_row(event.y)
        if not iid or not self.clip_tree.exists(iid):
            return

        # If double-clicking a child run row (Part D)
        if iid.startswith("run_"):
            parts = iid.split("_")
            clip_idx = int(parts[1]) - 1
            run_id = int(parts[2])
            if 0 <= clip_idx < len(self.clips):
                clip = self.clips[clip_idx]
                c_name = clip.get("name", "")
                col = self.clip_tree.identify_column(event.x)
                if col == "#9":  # Clicked "[Make Primary]"
                    run_comparison.set_primary_run(self._paths()["event_root"], c_name, run_id)
                    self._load_manifest(silent=True)
                    self._show_comparison_drawer(c_name, target_run_id=run_id)
                    return
                self._show_comparison_drawer(c_name, target_run_id=run_id)
                return

        try:
            idx = int(iid) - 1
            clip = self.clips[idx]
        except (ValueError, IndexError):
            return

        paths = self._paths()
        if not paths:
            return

        stem, _ = os.path.splitext(clip.get("name", ""))
        transcripts_txt = os.path.join(paths["transcripts"], f"{stem}.txt")
        test_txt = os.path.join(paths["test"], f"{stem}.txt")

        target_txt = None
        loc_desc = ""
        if os.path.isfile(transcripts_txt):
            target_txt = transcripts_txt
            loc_desc = "scribe\\transcripts"
        elif os.path.isfile(test_txt):
            target_txt = test_txt
            loc_desc = "scribe\\test"

        if not target_txt:
            vals = self.clip_tree.item(iid, "values")
            status = vals[7] if len(vals) > 7 else "Unknown"
            messagebox.showinfo(
                "Transcript Viewer",
                f"No transcript file found on disk for '{clip.get('name')}'.\nStatus: {status}",
            )
            return

        # Check existing active viewer if open
        if self._active_viewer and self._active_viewer.winfo_exists():
            if not self._active_viewer.prompt_unsaved_if_dirty():
                return
            self._active_viewer.destroy()
            self._active_viewer = None

        with self.preserve_scroll():
            viewer = TranscriptEditorDialog(
                parent=self.winfo_toplevel(),
                stage3_tab=self,
                clip=clip,
                target_txt=target_txt,
                loc_desc=loc_desc,
                paths=paths,
            )
            self._active_viewer = viewer

    # ------------------------------------------------------------------
    # Part D: Model Choice, Comparison Drawer & Multi-Run Telemetry
    # ------------------------------------------------------------------

    def _on_tree_select(self, _event=None):
        """When a clip row is selected, populate and reveal comparison drawer if multiple runs exist."""
        selected = self.clip_tree.selection()
        if not selected:
            return
        first_id = selected[0]
        clip_idx = None
        target_run_id = None
        if first_id.startswith("run_"):
            parts = first_id.split("_")
            clip_idx = int(parts[1]) - 1
            if len(parts) >= 3 and parts[2].isdigit():
                target_run_id = int(parts[2])
        elif first_id.isdigit():
            clip_idx = int(first_id) - 1

        if clip_idx is not None and 0 <= clip_idx < len(self.clips):
            clip = self.clips[clip_idx]
            c_name = clip.get("name", "")
            self._show_comparison_drawer(c_name, target_run_id=target_run_id)

    def _show_comparison_drawer(self, clip_name: str, target_run_id: Optional[int] = None):
        """Populate the bottom comparison drawer for the target clip."""
        paths = self._paths()
        if not paths or not hasattr(self, "comp_drawer"):
            return
        runs = run_comparison.load_clip_runs(paths["event_root"], clip_name)
        if len(runs) >= 2:
            primary_run = next((r for r in runs if r.get("is_primary")), runs[0])
            r1 = primary_run

            if target_run_id is not None:
                r2 = next((r for r in runs if r.get("run_id") == target_run_id), None)
            else:
                r2 = None

            if r2 is None or r2.get("run_id") == r1.get("run_id"):
                other_runs = [r for r in reversed(runs) if r.get("run_id") != r1.get("run_id")]
                r2 = other_runs[0] if other_runs else runs[-1]

            diff_res = run_comparison.diff_transcripts(r1.get("transcript_text", ""), r2.get("transcript_text", ""))
            lat_delta = r2.get("time_taken_sec", 0.0) - r1.get("time_taken_sec", 0.0)

            self.comp_metrics_var.set(
                f"{clip_name} · Run #{r1['run_id']} ({r1['model_name']}, {r1['time_taken_sec']:.1f}s, {r1['speedup_ratio']:.1f}x) vs "
                f"Run #{r2['run_id']} ({r2['model_name']}, {r2['time_taken_sec']:.1f}s, {r2['speedup_ratio']:.1f}x) | "
                f"Latency Δ: {lat_delta:+.1f}s | WER: {diff_res['wer_pct']:.1f}% | Char Diff: {diff_res['char_diff_pct']:.1f}%"
            )

            self.lbl_comp_run1.configure(text=f"Run #{r1['run_id']} (Baseline: {r1['model_name']})")
            self.lbl_comp_run2.configure(text=f"Run #{r2['run_id']} (Comparison: {r2['model_name']})")

            self.txt_comp_run1.delete("1.0", tk.END)
            self.txt_comp_run1.insert(tk.END, r1.get("transcript_text", ""))

            self.txt_comp_run2.delete("1.0", tk.END)
            self.txt_comp_run2.insert(tk.END, r2.get("transcript_text", ""))

            self._comp_target = (clip_name, r2["run_id"])
            self.comp_drawer.open()
        elif len(runs) == 1:
            r1 = runs[0]
            self.comp_metrics_var.set(
                f"{clip_name} has 1 recorded run ({r1['model_name']}). Use 'Re-transcribe…' with Compare mode to evaluate another model."
            )
            self.lbl_comp_run1.configure(text=f"Run #1 ({r1['model_name']})")
            self.lbl_comp_run2.configure(text="Run #2 (Not yet executed)")
            self.txt_comp_run1.delete("1.0", tk.END)
            self.txt_comp_run1.insert(tk.END, r1.get("transcript_text", ""))
            self.txt_comp_run2.delete("1.0", tk.END)
            self.txt_comp_run2.insert(tk.END, "[No comparison run executed yet for this clip]")
            self._comp_target = None
            self.comp_drawer.open()

    def _promote_comparison_to_primary(self):
        """Promote the comparison run in the drawer to primary."""
        if not getattr(self, "_comp_target", None):
            messagebox.showinfo("Comparison", "No comparison run selected to promote.", parent=self)
            return
        clip_name, run_id = self._comp_target
        paths = self._paths()
        success = run_comparison.set_primary_run(paths["event_root"], clip_name, run_id)
        if success:
            self._load_manifest(silent=True)
            self._show_comparison_drawer(clip_name, target_run_id=run_id)
            messagebox.showinfo(
                "Primary Run Updated",
                f"Promoted Run #{run_id} to primary for '{clip_name}'.\n\n"
                "The active transcript and downstream combine files have been updated.",
                parent=self,
            )

    def _open_retranscribe_dialog(self):
        """Open the Re-transcribe with Model Choice dialog."""
        selected = self.clip_tree.selection()
        if not selected:
            if self.clips:
                selected = ["1"]
            else:
                messagebox.showinfo("Re-transcribe", "Load an event manifest first.", parent=self)
                return

        target_clips = []
        for iid in selected:
            if iid.startswith("run_"):
                idx = int(iid.split("_")[1]) - 1
            elif iid.isdigit():
                idx = int(iid) - 1
            else:
                continue
            if 0 <= idx < len(self.clips):
                c = self.clips[idx]
                if c not in target_clips:
                    target_clips.append(c)

        if not target_clips:
            messagebox.showinfo("Re-transcribe", "Select at least one clip.", parent=self)
            return

        paths = self._paths()
        ReTranscribeDialog(
            parent=self,
            clips=target_clips,
            on_start_callback=lambda mode, model_id, res=None: self._execute_retranscription(
                target_clips, mode, model_id, precomputed_results=res
            ),
            event_root=paths["event_root"],
        )

    def _execute_retranscription(
        self,
        target_clips: List[Dict[str, Any]],
        mode: str,
        model_id: str,
        precomputed_results: Optional[List[Dict[str, Any]]] = None,
    ):
        """Execute re-transcription with the selected model and mode."""
        paths = self._paths()
        m_info = model_registry.get_model(model_id)
        model_name = m_info.name if m_info else model_id
        provider = m_info.provider if m_info else "local"

        self._log(f"[RE-TRANSCRIBE] Starting re-transcription of {len(target_clips)} clip(s) with '{model_name}' (Mode: {mode.upper()})...")

        results = precomputed_results
        if results is None:
            results = []
            for c in target_clips:
                res = run_comparison.execute_clip_retranscription(
                    event_root=paths["event_root"],
                    clip_dict=c,
                    model_id=model_id,
                    mode=mode,
                )
                results.append(res)

        last_run_id = None
        for res in results:
            run_rec = res.get("run_rec", {})
            dur = res.get("audio_dur_sec", 0.0)
            elapsed = res.get("elapsed_sec", 0.0)
            c_name = res.get("clip_name") or target_clips[0].get("name", "")
            last_run_id = run_rec.get("run_id")

            if provider == "nvidia":
                self._session_cloud_minutes += dur / 60.0
                est_cost = self._session_cloud_minutes * 0.003
                self.cloud_usage_var.set(
                    f"Cloud uploaded this session: {self._session_cloud_minutes:.1f} mins (Est. cost: ${est_cost:.2f})"
                )

            self._log(
                f"  ✓ {c_name}: Run #{run_rec.get('run_id')} finished in {elapsed}s "
                f"({run_rec.get('speedup_ratio')}x RT, WER: {run_rec.get('wer_vs_baseline', 0)*100:.1f}%)"
            )

        self._load_manifest(silent=True)
        if target_clips and last_run_id is not None:
            self._show_comparison_drawer(target_clips[0].get("name", ""), target_run_id=last_run_id)

        messagebox.showinfo(
            "Re-transcription Complete",
            f"Successfully re-transcribed {len(target_clips)} clip(s) with '{model_name}'.\n\n"
            f"Execution mode: {mode.upper()}\n"
            "Review comparison metrics in the table and Comparison Drawer below.",
            parent=self,
        )

    def _export_comparison_clicked(self):
        """Export session/event comparison report to Excel (.xlsx) and CSV."""
        paths = self._paths()
        if not self.clips:
            messagebox.showinfo("Export Comparison", "No clips available for comparison report.", parent=self)
            return
        alias_to_name = None
        if self.vault and self.vault.is_unlocked():
            event_id = paths.get("event_id") if paths else ""
            if not event_id and self.manifest_data:
                event_id = self.manifest_data.get("event_id", "")
            if event_id:
                try:
                    roster_mgr = EventRosterManager(self.vault)
                    alias_to_name = roster_mgr.get_alias_to_name_map(event_id)
                except Exception:
                    pass
        try:
            out_xlsx = run_comparison.export_comparison_report(paths["event_root"], alias_to_name=alias_to_name)
            csv_path = os.path.splitext(out_xlsx)[0] + ".csv"
            messagebox.showinfo(
                "Comparison Report Exported",
                f"Successfully exported comparison report to:\n\n"
                f"Excel (.xlsx):\n{out_xlsx}\n\n"
                f"Companion CSV:\n{csv_path}",
                parent=self,
            )
        except Exception as e:
            messagebox.showerror("Export Failed", f"Could not export comparison report:\n{e}", parent=self)

    def _approve_selected_clicked(self):
        """Approve selected clips if their transcript is completed."""
        paths = self._paths()
        if not paths or not self.clips:
            return
        selected = self.clip_tree.selection()
        if not selected:
            messagebox.showinfo("Approve Selected", "Please select one or more clips in the list first.")
            return

        state_path = paths["state"]
        transcripts_dir = paths["transcripts"]
        test_dir = paths["test"]
        ghash = self._current_ghash()
        now_str = datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S IST")

        approved_stems = []
        unapprovable_reasons = []

        for iid in selected:
            try:
                idx = int(iid) - 1
                clip = self.clips[idx]
            except (ValueError, IndexError):
                continue

            clip_name = clip.get("name", "")
            stem, _ = os.path.splitext(clip_name)
            forced = clip_name in self._force_clips
            if forced:
                status = "Pending (forced)"
            else:
                status = _clip_disk_status(clip, paths, ghash)

            if status not in ("Done", "Done (test)", "Edited", "Completed"):
                if status.startswith("Pending"):
                    reason = "transcription pending"
                elif status == "File missing":
                    reason = "media file missing on disk"
                elif status == "Diff settings":
                    reason = "settings differ (re-transcribe or edit)"
                else:
                    reason = f"status '{status}' is not complete"
                unapprovable_reasons.append((clip_name, reason))
                continue

            # Look for completed transcript file (transcripts first, then test)
            txt_path = os.path.join(transcripts_dir, f"{stem}.txt")
            if not os.path.isfile(txt_path):
                txt_path = os.path.join(test_dir, f"{stem}.txt")

            if not os.path.isfile(txt_path):
                unapprovable_reasons.append((clip_name, "transcript file missing on disk"))
                continue

            h = compute_transcript_hash(txt_path)
            if h:
                set_clip_approval(state_path, stem, approved=True, transcript_hash=h, approved_at=now_str)
                approved_stems.append(stem)
            else:
                unapprovable_reasons.append((clip_name, "could not read transcript file"))

        if unapprovable_reasons:
            lines = [f"• {name} — {reason}" for name, reason in unapprovable_reasons]
            msg = (
                f"{len(unapprovable_reasons)} selected clip(s) cannot be approved:\n\n"
                + "\n".join(lines[:10])
                + ("\n..." if len(lines) > 10 else "")
                + "\n\nOnly clips with a completed transcript ('Done', 'Done (test)', or 'Edited') can be approved."
            )
            if not approved_stems:
                messagebox.showwarning("Cannot Approve", msg)
            else:
                messagebox.showinfo("Approval Notice", f"Approved {len(approved_stems)} clip(s).\n\n" + msg)

        self._refresh_status_column()

    def _approve_all_completed_clicked(self):
        """Approve all clips in the manifest whose status is complete (Done, Done (test), Edited)."""
        paths = self._paths()
        if not paths or not self.clips:
            return

        state_path = paths["state"]
        transcripts_dir = paths["transcripts"]
        test_dir = paths["test"]
        ghash = self._current_ghash()
        now_str = datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S IST")

        batch = {}
        for clip in self.clips:
            name = clip.get("name", "")
            if name in self._force_clips:
                continue
            stem, _ = os.path.splitext(name)
            status = _clip_disk_status(clip, paths, ghash)
            if status in ("Done", "Done (test)", "Edited", "Completed"):
                txt_path = os.path.join(transcripts_dir, f"{stem}.txt")
                if not os.path.isfile(txt_path):
                    txt_path = os.path.join(test_dir, f"{stem}.txt")
                if os.path.isfile(txt_path):
                    h = compute_transcript_hash(txt_path)
                    if h:
                        batch[stem] = {
                            "approved": True,
                            "transcript_hash": h,
                            "approved_at": now_str,
                        }

        if batch:
            set_multiple_clip_approvals(state_path, batch)
            self._log(f"[APPROVAL] Approved {len(batch)} completed clip(s).")
        else:
            messagebox.showinfo("Approve All Completed", "No completed transcripts found to approve.")

        self._refresh_status_column()

    def _clear_approval_clicked(self):
        """Clear approval for selected clips."""
        paths = self._paths()
        if not paths or not self.clips:
            return
        selected = self.clip_tree.selection()
        if not selected:
            messagebox.showinfo("Clear Approval", "Please select one or more clips in the list first.")
            return

        state_path = paths["state"]
        stems_to_clear = []
        for iid in selected:
            try:
                idx = int(iid) - 1
                clip = self.clips[idx]
                stem, _ = os.path.splitext(clip.get("name", ""))
                stems_to_clear.append(stem)
            except (ValueError, IndexError):
                continue

        if stems_to_clear:
            clear_multiple_clip_approvals(state_path, stems_to_clear)

        self._refresh_status_column()

    def _update_combine_and_approval_ui(self):
        """Update approval counter, review buttons, combine gate, and stale label."""
        paths = self._paths()
        if not paths or not self.clips:
            self.approval_counter_var.set("0 of 0 approved")
            self.combine_gate_var.set("")
            self.btn_combine_approved.configure(state=tk.DISABLED)
            self.btn_approve_selected.configure(state=tk.DISABLED)
            self.btn_approve_all.configure(state=tk.DISABLED)
            self.btn_clear_approval.configure(state=tk.DISABLED)
            if hasattr(self, "btn_retranscribe"):
                self.btn_retranscribe.configure(state=tk.DISABLED)
            if hasattr(self, "btn_export_comparison"):
                self.btn_export_comparison.configure(state=tk.DISABLED)
            self.combine_stale_lbl.configure(text="")
            return

        state_path = paths["state"]
        total = len(self.clips)
        ghash = self._current_ghash()

        approved_count = 0
        completed_count = 0
        unapproved_count = 0
        failed_count = 0
        pending_count = 0
        missing_count = 0

        for c in self.clips:
            name = c.get("name", "")
            stem, _ = os.path.splitext(name)
            forced = name in self._force_clips
            if forced:
                status = "Pending (forced)"
            else:
                status = _clip_disk_status(c, paths, ghash)

            is_appr = get_clip_approval(state_path, stem)

            if status in ("Done", "Done (test)", "Edited", "Completed"):
                completed_count += 1
                if is_appr:
                    approved_count += 1
                else:
                    unapproved_count += 1
            elif status == "File missing":
                missing_count += 1
            elif status in ("Diff settings", "Failed", "Error"):
                failed_count += 1
            else:  # Pending, Pending (forced), etc.
                pending_count += 1

        self.approval_counter_var.set(f"{approved_count} of {total} approved")

        # Enable review buttons if manifest loaded
        self.btn_approve_selected.configure(state=tk.NORMAL)
        self.btn_approve_all.configure(state=tk.NORMAL if completed_count > 0 else tk.DISABLED)
        self.btn_clear_approval.configure(state=tk.NORMAL if approved_count > 0 else tk.DISABLED)
        if hasattr(self, "btn_retranscribe"):
            self.btn_retranscribe.configure(state=tk.NORMAL if total > 0 else tk.DISABLED)
        if hasattr(self, "btn_export_comparison"):
            self.btn_export_comparison.configure(state=tk.NORMAL if total > 0 else tk.DISABLED)

        # Check combine gate reasons (combine gated strictly on user approval of all clips)
        reasons = []
        if unapproved_count > 0:
            reasons.append(f"{unapproved_count} clip(s) not approved")
        if failed_count > 0:
            reasons.append(f"{failed_count} clip(s) failed")
        if pending_count > 0:
            reasons.append(f"{pending_count} clip(s) pending")
        if missing_count > 0:
            reasons.append(f"{missing_count} clip(s) missing")

        # Vault is optional: finalize names button available if vault is locked, but never blocks Combine
        if hasattr(self, "btn_finalize_names"):
            if self.vault.is_initialized() and not self.vault.is_unlocked():
                self.btn_finalize_names.configure(state=tk.NORMAL)
            else:
                self.btn_finalize_names.configure(state=tk.DISABLED)

        if hasattr(self, "btn_reidentify"):
            self.btn_reidentify.configure(state=tk.NORMAL if completed_count > 0 else tk.DISABLED)

        if hasattr(self, "btn_undo_reidentify"):
            has_backup = False
            state_dir = os.path.join(paths.get("event_root", ""), "scribe", "state", "reidentify_backups")
            if os.path.isdir(state_dir):
                subdirs = [d for d in os.listdir(state_dir) if os.path.isdir(os.path.join(state_dir, d))]
                has_backup = bool(subdirs)
            self.btn_undo_reidentify.configure(state=tk.NORMAL if has_backup else tk.DISABLED)

        unlocked = (len(reasons) == 0 and total > 0 and approved_count == total)

        if unlocked:
            self.btn_combine_approved.configure(state=tk.NORMAL)
            self.combine_gate_var.set("Ready to combine (all clips approved)")
            self.lbl_combine_gate.configure(foreground="#28a745")
        else:
            self.btn_combine_approved.configure(state=tk.DISABLED)
            self.combine_gate_var.set(f"LOCKED: {', '.join(reasons)}")
            self.lbl_combine_gate.configure(foreground="#d9534f")

        # Check stale status
        is_stale = get_combined_stale(state_path)
        combined_dir = paths.get("combined", os.path.join(paths["event_root"], "scribe", "combined"))
        has_combined_files = False
        if os.path.isdir(combined_dir):
            has_combined_files = any(f.endswith(".txt") for f in os.listdir(combined_dir) if f != "old")

        if is_stale and has_combined_files:
            self.combine_stale_lbl.configure(text="⚠ Stale combined files (speaker renamed — re-approval & Combine required)")
        else:
            self.combine_stale_lbl.configure(text="")

    def _combine_approved_clicked(self):
        """Gate check, confirmation dialog, and combination execution."""
        paths = self._paths()
        if not paths or not self.manifest_data:
            return

        state_path = paths["state"]
        event_root = paths["event_root"]

        # Strict gate check: must be unlocked
        self._refresh_status_column()
        gate_reason = self.combine_gate_var.get()
        if "LOCKED" in gate_reason:
            messagebox.showerror(
                "Combine Blocked",
                f"Cannot combine transcripts:\n\n{gate_reason}\n\n"
                "All clips in the manifest must be 'Done' and 'Approved' before combining.",
            )
            return

        # Prepare confirmation file list
        sessions = self.manifest_data.get("sessions", [])
        if not sessions:
            messagebox.showerror("Combine Blocked", "No sessions found in manifest.")
            return

        # Late-bind personal names if vault is unlocked; else show aliases
        event_id = self.manifest_data.get("event_id", "")
        alias_to_name = None
        if self.vault.is_unlocked() and event_id:
            try:
                roster_mgr = EventRosterManager(self.vault)
                alias_to_name = roster_mgr.get_alias_to_name_map(event_id)
            except Exception:
                alias_to_name = None

        file_lines = []
        for sess in sessions:
            spk = (sess.get("speaker") or "Speaker").strip()
            if alias_to_name and spk in alias_to_name:
                spk = alias_to_name[spk]
            fname = sanitize_speaker_filename(spk)
            cnt = len(sess.get("clips", []))
            file_lines.append(f"  • {fname} ({cnt} clips)")

        confirm_msg = (
            "Combine Approved Transcripts\n\n"
            "The following combined files will be created in scribe\\combined\\:\n\n"
            + "\n".join(file_lines) + "\n\n"
            "Existing combined files will be moved to scribe\\combined\\old\\ before being replaced.\n\n"
            "Do you want to proceed?"
        )

        with self.preserve_scroll():
            if not messagebox.askyesno("Confirm Combine", confirm_msg):
                return  # User cancelled: nothing changes!

            # Execute combining
            res = build_combined_transcripts(event_root, self.manifest_data, alias_to_name=alias_to_name)
            set_combined_stale(state_path, False)
            self._update_combine_and_approval_ui()

            summary = res.get("summary", "")
            warnings = res.get("warnings", [])

            self._log("\n[Combined Transcripts per Speaker]")
            if summary:
                self._log(f"  Files created: {summary}")
            for w in warnings:
                self._log(f"  [WARN] {w}")

            info_msg = f"Combined transcripts created successfully!\n\n{summary}"
            if warnings:
                info_msg += "\n\nWarnings:\n" + "\n".join(f"• {w}" for w in warnings)

            messagebox.showinfo("Combine Transcripts Complete", info_msg)

    # ------------------------------------------------------------------
    # Logging
    # ------------------------------------------------------------------

    def _log(self, text: str):
        self.log_text.insert(tk.END, text + "\n")
        self.log_text.see(tk.END)

    def _log_paths(self, paths):
        self._log(format_paths_log_header(paths))

    # ------------------------------------------------------------------
    # Run helpers
    # ------------------------------------------------------------------

    def _run_test_transcription(self):
        if self.is_running:
            return
        if not self._require_valid_manifest():
            return

        selected_ids = self.clip_tree.selection()
        if selected_ids:
            test_clips = [self.clips[int(i) - 1] for i in selected_ids[:2]]
        else:
            test_clips = self.clips[:2]

        if not test_clips:
            messagebox.showwarning("No Clips", "The selected event's manifest has no video clips.")
            return

        paths = self._paths()
        self._start_transcription_job(
            clips_to_run=test_clips,
            output_dir=paths["test"],
            work_dir=paths["work"],
            log_dir=paths["logs"],
            job_name="Test Gate Run",
            paths=paths,
        )

    def _run_full_transcription(self):
        if self.is_running:
            return
        if not self._require_valid_manifest():
            return

        # Voice Vault is optional: if unlocked, names are resolved; otherwise aliases are used
        if self.vault.is_unlocked():
            self._log("[VAULT] Voice Vault is unlocked: speaker identification active.")
        else:
            self._log("[VAULT] Voice Vault is locked or uninitialized: transcription proceeding with aliases/speaker labels.")

        paths = self._paths()
        if not TranscriptionManager.is_test_approved(paths["scribe"]):
            messagebox.showwarning("Locked", "Test gate is locked. Run and approve test clips first.")
            return

        self._start_transcription_job(
            clips_to_run=self.clips,
            output_dir=paths["transcripts"],
            work_dir=paths["work"],
            log_dir=paths["logs"],
            job_name="Full Event Run",
            paths=paths,
        )

    def _build_run_summary(self, clips_to_run: list, paths: dict) -> str:
        """Compute and return the pre-run summary line for the log."""
        ghash = self._current_ghash()
        counts = {"done": 0, "done_test": 0, "diff": 0, "missing": 0, "pending": 0, "forced": 0}
        for c in clips_to_run:
            name = c.get("name", "")
            if name in self._force_clips:
                counts["forced"] += 1
                continue
            st = _clip_disk_status(c, paths, ghash)
            if st == "Done":
                counts["done"] += 1
            elif st == "Done (test)":
                counts["done_test"] += 1
            elif st == "Diff settings":
                counts["diff"] += 1
            elif st == "File missing":
                counts["missing"] += 1
            else:
                counts["pending"] += 1

        to_run = counts["pending"] + counts["forced"]
        parts = [f"{len(clips_to_run)} clips"]
        if counts["done"]:
            parts.append(f"{counts['done']} done")
        if counts["done_test"]:
            parts.append(f"{counts['done_test']} done (test — will copy)")
        if counts["diff"]:
            parts.append(f"{counts['diff']} diff settings (will skip)")
        if counts["forced"]:
            parts.append(f"{counts['forced']} forced redo")
        if counts["missing"]:
            parts.append(f"{counts['missing']} file missing")
        parts.append(f"{to_run} to transcribe")
        return " | ".join(parts)

    def _start_transcription_job(self, clips_to_run, output_dir, work_dir, log_dir, job_name, paths):
        extra = validate_clips_for_event(
            [c for c in clips_to_run if c.get("name") not in self._force_clips or os.path.isfile(c.get("path", ""))],
            paths["event_root"]
        )
        if extra:
            messagebox.showerror(
                "Stage 3 stopped",
                f"{format_paths_log_header(paths)}\n\n"
                + "\n".join(f"• {e}" for e in extra[:20]),
            )
            return

        # Task A: pre-run guard for placeholder names.
        if not self._check_placeholder_names(clips_to_run):
            return

        # Multi-speaker diarization preflight check (Task D)
        has_multi_speaker = any(
            c.get("speaker_mode") == "panel" or len(c.get("roster", [])) > 1
            for c in clips_to_run
        )
        if has_multi_speaker:
            hf_tok = self.hf_token_var.get().strip() or hf_preflight.get_hf_token()
            is_ok, msg, details = hf_preflight.run_hf_preflight_check(hf_tok)
            if not is_ok:
                messagebox.showerror(
                    "Multi-Speaker Diarization Blocked",
                    f"Multi-speaker sessions require local Pyannote diarization.\n\n"
                    f"{msg}\n\n"
                    f"Please configure a valid Hugging Face token under 'Connections' and accept the gated model terms.",
                    parent=self,
                )
                return

        # Preflight & Privacy check for Cloud engine
        selected_engine = self.engine_var.get()
        api_key = self.api_key_var.get().strip() or _cloud_engine.get_nvidia_api_key()

        if selected_engine == "cloud":
            if not api_key:
                messagebox.showerror(
                    "API Key Missing",
                    "NVIDIA API Key is required for Cloud transcription.\n\n"
                    "Please enter your key in Engine & Provider Settings or choose Local engine.",
                    parent=self,
                )
                return

            if not self._privacy_confirmed:
                confirm = messagebox.askyesno(
                    "Cloud Privacy Notice",
                    "Audio data will be transmitted to NVIDIA-hosted endpoints (grpc.nvcf.nvidia.com)\n"
                    "for cloud speech transcription using your NVIDIA API key.\n\n"
                    "Do you want to proceed with cloud transcription?",
                    parent=self,
                )
                if not confirm:
                    return
                self._privacy_confirmed = True

            # Preflight check: ensure local Whisper fallback is ready
            try:
                import torch
                import whisper
                from scribe_core.transcribe_worker import resolve_ffmpeg
                resolve_ffmpeg()
            except Exception as pref_err:
                messagebox.showerror(
                    "Local Engine Preflight Failed",
                    f"Local Whisper fallback preflight check failed:\n{pref_err}\n\n"
                    "Local Whisper medium must be ready as an automatic fallback.",
                    parent=self,
                )
                return

            # Automatic cloud preflight check (cached 10 minutes)
            prof_id = self.profile_var.get().split(" — ")[0].strip()
            profile = _cloud_engine.get_profile_by_id(prof_id)
            if profile:
                preflight_res = _cloud_engine.run_cloud_preflight_check(profile, api_key=api_key, use_cache=True)
                if not preflight_res.success:
                    reason_msg = f"{preflight_res.error_class} — {preflight_res.error_message}"
                    self._log(f"[PREFLIGHT FAILED] Cloud preflight check failed ({reason_msg}). Starting run deliberately on Local Whisper medium.")
                    messagebox.showinfo(
                        "Cloud Preflight Check Failed",
                        f"Cloud preflight test failed ({preflight_res.error_class}):\n{preflight_res.error_message}\n\n"
                        "Starting this run deliberately on Local Whisper medium.",
                        parent=self,
                    )
                    selected_engine = "local"
                    self.engine_var.set("local")

        self._save_glossary()
        self.is_running = True
        self.vault.begin_run()
        self._pending_reload = False
        self._reload_banner_var.set("")
        self.btn_run_test.configure(state=tk.DISABLED)
        self.btn_run_full.configure(state=tk.DISABLED)
        self.btn_approve_test.configure(state=tk.DISABLED)
        self.btn_rename_speaker.configure(state=tk.DISABLED)
        self.btn_cancel.configure(state=tk.NORMAL)

        # Initialize Live Timer and Engine metrics (Part 1)
        self._run_start_monotonic = time.monotonic()
        self._last_manual_scroll_time = 0.0
        self._completed_clips_in_run = []
        self._clips_in_current_run = list(clips_to_run)
        self._failover_active = False
        self.failover_banner.pack_forget()

        if selected_engine == "cloud":
            prof_name = self.profile_var.get().split(" — ")[0].strip()
            self._current_engine_name = "Whisper Large (NVIDIA)" if "whisper" in prof_name.lower() else "Parakeet (NVIDIA)"
        else:
            self._current_engine_name = "Whisper medium (GPU)" if torch.cuda.is_available() else "Whisper medium (CPU)"

        self._log(f"[ENGINE] Active engine initialized: {self._current_engine_name}")

        # Mark all queued clips in the table
        for c in clips_to_run:
            for idx, row_clip in enumerate(self.clips, 1):
                if row_clip.get("name") == c.get("name"):
                    iid = str(idx)
                    if self.clip_tree.exists(iid):
                        vals = list(self.clip_tree.item(iid, "values"))
                        while len(vals) < 10:
                            vals.append("")
                        vals[7] = "Queued"
                        vals[9] = self._current_engine_name
                        self.clip_tree.item(iid, values=vals)
                    break

        self._schedule_ui_tick()

        self.prog_bar["value"]   = 0
        self.prog_bar["maximum"] = len(clips_to_run)

        summary_line = self._build_run_summary(clips_to_run, paths)
        self._log(f"\n{'='*60}\nStarting {job_name} ({summary_line}) [Engine: {selected_engine}]")
        self._log_paths(paths)
        self._log(f"Output folder: {output_dir}\n{'='*60}")

        lang   = None if self.lang_var.get() == "auto" else self.lang_var.get()
        prompt = self.ctx.glossary_var.get().strip() or None

        prof_id = self.profile_var.get().split(" — ")[0].strip()
        profile = _cloud_engine.get_profile_by_id(prof_id)
        profile_dict = profile.to_dict() if profile else None

        config_path = self.transcription_mgr.prepare_job_config(
            event_dict=self.manifest_data.get("event", {}),
            clips_to_run=clips_to_run,
            output_dir=output_dir,
            work_dir=work_dir,
            log_dir=log_dir,
            language=lang or "en",
            initial_prompt=prompt,
            model="medium",
            resolved_paths=paths,
            # Bug 2: cross-dir context
            test_dir=paths["test"],
            transcripts_dir=paths["transcripts"],
            force_clips=list(self._force_clips),
            # Task B: state directory
            state_dir=paths["state"],
            # Cloud Engine parameters
            engine=selected_engine,
            cloud_profile=profile_dict,
            single_request_limit_sec=600.0,
            chunk_duration_sec=120.0,
            checkpoint_every_clip=self.checkpoint_every_clip_var.get(),
            timeout_base=float(self.timeout_base_var.get()),
            timeout_factor=float(self.timeout_factor_var.get()),
        )

        threading.Thread(
            target=self._worker_thread,
            args=(config_path, clips_to_run, output_dir),
            daemon=True,
        ).start()

    def _worker_thread(self, config_path, clips_to_run, output_dir):
        def on_event(ev):
            self.after(0, self._handle_worker_event, ev)

        try:
            worker_env = None
            key = self.api_key_var.get().strip() or _cloud_engine.get_nvidia_api_key()
            if key:
                worker_env = {"NVIDIA_API_KEY": key}
            summary = self.transcription_mgr.run_worker_blocking(
                config_path, progress_callback=on_event, env=worker_env
            )
            self.after(0, self._on_job_finished, summary, output_dir)
        except Exception as e:
            self.after(0, lambda err=e: self._on_job_error(str(err)))

    def _handle_worker_event(self, ev: dict):
        t = ev.get("type")
        if t == "init":
            rp = ev.get("resolved_paths") or {}
            if rp:
                self._log(
                    "Resolved paths:\n"
                    f"  Event root: {rp.get('event_root', '')}\n"
                    f"  data: {rp.get('data', '')}\n"
                    f"  scribe: {rp.get('scribe', '')}\n"
                    f"  manifest: {rp.get('manifest', '')}"
                )
        elif t == "run_summary":
            self._log(f"[SUMMARY] {ev.get('message', '')}")
        elif t == "model_loaded":
            self._log(f"[INIT] Model '{ev.get('model')}' loaded on {ev.get('device')} in {ev.get('load_time')}s.")
        elif t == "clip_start":
            forced_flag = " [FORCED]" if ev.get("forced") else ""
            clip_name = ev.get("clip")
            self._currently_transcribing_clip = clip_name
            self._current_clip_start_monotonic = time.monotonic()
            self._last_worker_event_monotonic = time.monotonic()
            self._current_intra_progress_pct = None
            self._current_substage = "Transcribing"
            self._failover_active = False

            for idx, c in enumerate(self.clips, 1):
                if c.get("name") == clip_name:
                    self._current_running_iid = str(idx)
                    break

            self._log(f"[{ev.get('index')}/{ev.get('total')}] Transcribing '{clip_name}' ({ev.get('speaker')}){forced_flag}...")

            if self._current_running_iid and self.clip_tree.exists(self._current_running_iid):
                vals = list(self.clip_tree.item(self._current_running_iid, "values"))
                while len(vals) < 10:
                    vals.append("")
                vals[5] = "00:00:00"
                vals[7] = "● Transcribing"
                vals[9] = self._current_engine_name
                row_tag = "even_row" if int(self._current_running_iid) % 2 == 0 else "odd_row"
                self.clip_tree.item(self._current_running_iid, values=vals, tags=(row_tag, "running"))
                if (time.monotonic() - self._last_manual_scroll_time) > 10.0:
                    self.clip_tree.see(self._current_running_iid)

        elif t == "progress":
            self._last_worker_event_monotonic = time.monotonic()
            if ev.get("percent") is not None:
                self._current_intra_progress_pct = float(ev["percent"])
            elif ev.get("progress_pct") is not None:
                self._current_intra_progress_pct = float(ev["progress_pct"])
            if ev.get("substage"):
                self._current_substage = str(ev["substage"])

        elif t == "clip_complete":
            self._last_worker_event_monotonic = time.monotonic()
            self.prog_bar["value"] = ev.get("index", 0)
            dur     = ev.get("audio_duration", 0)
            elapsed = ev.get("transcribe_time", 0)
            speed   = ev.get("speed_factor", 0)
            eng_used = ev.get("engine", "")

            self._completed_clips_in_run.append({"clip": ev.get("clip"), "time": float(elapsed)})

            if "cloud" in eng_used.lower():
                self._session_cloud_minutes += dur / 60.0
                est_cost = self._session_cloud_minutes * 0.003
                self.cloud_usage_var.set(
                    f"Cloud uploaded this session: {self._session_cloud_minutes:.1f} mins (Est. cost: ${est_cost:.2f})"
                )

            self.speed_lbl_var.set(f"Speed: {speed}x real-time")
            self._log(
                f"[DONE] {ev.get('clip')} ({eng_used}): Audio {dur:.1f}s transcribed in {elapsed:.2f}s "
                f"({speed}x real-time) → {ev.get('segments_count')} segments"
            )
            if "cloud" in eng_used.lower():
                eng_display = "Parakeet (NVIDIA)"
            elif "fallback" in eng_used.lower():
                eng_display = "Parakeet → Whisper medium"
            else:
                eng_display = self._current_engine_name or "Whisper medium (GPU)"

            self._set_tree_status(ev.get("clip"), "Done", "done", engine_text=eng_display)
            self._currently_transcribing_clip = None
            self._current_running_iid = None

        elif t == "fallback":
            self._last_worker_event_monotonic = time.monotonic()
            old_eng = self._current_engine_name
            self._current_engine_name = "Parakeet → Whisper medium"
            fo_sec = ev.get("failover_time_sec")
            fo_str = format_failover_time(fo_sec)
            self._failover_active = True
            self._failover_start_monotonic = time.monotonic() - float(fo_sec or 0.0)

            msg = ev.get("message") or f"Cloud failed ({ev.get('failover_reason', 'server_error')}), continuing locally"
            self._log(f"[FALLBACK] Clip '{ev.get('clip')}': {msg} (Handover time: {fo_sec:.1f}s)")
            self._log(f"[ENGINE] Engine changed from '{old_eng}' to '{self._current_engine_name}' (Fallback to local Whisper medium)")

            # Show visible persistent banner and toast
            self.failover_banner_lbl.configure(text=f"⚠️ {msg}")
            self.failover_banner.pack(fill=tk.X, pady=(0, 6), before=self.clip_tree.master)
            self.show_toast(msg)

            for idx, c in enumerate(self.clips, 1):
                if c.get("name") == ev.get("clip"):
                    iid = str(idx)
                    if self.clip_tree.exists(iid):
                        vals = list(self.clip_tree.item(iid, "values"))
                        while len(vals) < 10:
                            vals.append("")
                        vals[6] = fo_str
                        vals[9] = self._current_engine_name
                        self.clip_tree.item(iid, values=vals)
                    break
            self._update_totals_row()

        elif t == "clip_skipped":
            self._last_worker_event_monotonic = time.monotonic()
            self._currently_transcribing_clip = None
            self._current_running_iid = None
            self.prog_bar["value"] = ev.get("index", 0)
            loc = ev.get("found_in", "")
            self._log(f"[SKIP] {ev.get('clip')} skipped, already done (found in {loc}).")
            dest_status = "Done (test)" if loc == "test" else "Done"
            self._set_tree_status(ev.get("clip"), dest_status, self._status_tag(dest_status))
        elif t == "clip_skipped_different_settings":
            self._last_worker_event_monotonic = time.monotonic()
            self._currently_transcribing_clip = None
            self._current_running_iid = None
            self.prog_bar["value"] = ev.get("index", 0)
            self._log(
                f"[SKIP/DIFF] {ev.get('clip')} skipped — different settings "
                f"(sidecar ghash={ev.get('sidecar_glossary_hash')}, "
                f"current ghash={ev.get('current_glossary_hash')}). "
                "Right-click → Force re-transcribe to redo."
            )
            self._set_tree_status(ev.get("clip"), "Diff settings", "diff")
        elif t == "clip_error":
            self._last_worker_event_monotonic = time.monotonic()
            self._currently_transcribing_clip = None
            self._current_running_iid = None
            self._log(f"[ERROR] Failed {ev.get('clip')}: {ev.get('error')}")
            self._set_tree_status(ev.get("clip"), "Failed", "missing")

    def _set_tree_status(self, clip_name: str, status: str, tag: str, engine_text: str = ""):
        """Update the Status, Time Taken, Failover time, and Engine cells for the row matching *clip_name*."""
        paths = self._paths()
        for idx, c in enumerate(self.clips, 1):
            if c.get("name") == clip_name:
                iid  = str(idx)
                if self.clip_tree.exists(iid):
                    vals    = list(self.clip_tree.item(iid, "values"))
                    while len(vals) < 10:
                        vals.append("")
                    fo_sec, _ = _get_clip_failover_data(clip_name, paths)
                    vals[5] = _get_clip_time_taken(clip_name, paths)
                    vals[6] = format_failover_time(fo_sec)
                    vals[7] = format_status_display(status)
                    if engine_text:
                        vals[9] = engine_text
                    row_tag = "even_row" if idx % 2 == 0 else "odd_row"
                    row_tags = (row_tag, tag) if tag else (row_tag,)
                    self.clip_tree.item(iid, values=vals, tags=row_tags)
                break
        self._update_totals_row()

    # ------------------------------------------------------------------
    # Job completion / error / cancel
    # ------------------------------------------------------------------

    def _on_job_finished(self, summary, output_dir):
        self.is_running = False
        if self._ui_timer_id is not None:
            try:
                self.after_cancel(self._ui_timer_id)
            except Exception:
                pass
            self._ui_timer_id = None

        self._currently_transcribing_clip = None
        self._current_running_iid = None
        self.vault.end_run()
        if hasattr(self, "vault_chip"):
            self.vault_chip.refresh()
        self.btn_cancel.configure(state=tk.DISABLED)

        # Bug 1: apply deferred reload if manifest changed during the run.
        if self._pending_reload:
            self._load_manifest(silent=True)
        else:
            self._refresh_status_column()
            self._on_context_change()

        self._update_totals_row()
        self._recompute_column_widths()

        # Re-enable rename button
        if self.manifest_data.get("sessions"):
            self.btn_rename_speaker.configure(state=tk.NORMAL)

        paths  = self._paths()
        banner = format_paths_log_header(paths) + "\n\n" if paths else ""
        msg = (
            f"{banner}"
            f"Run Completed Successfully!\n\n"
            f"• Processed: {summary.get('processed_count', 0)}\n"
            f"• Skipped (already done): {summary.get('skipped_count', 0)}\n"
            f"• Total Run Time: {summary.get('total_run_time_str')}\n"
            f"• Total Audio: {summary.get('total_audio_duration_sec', 0):.1f}s\n"
            f"• Speed Factor: {summary.get('overall_speed_factor', 0)}x real-time\n"
            f"• Flagged Low-Confidence Lines: {summary.get('low_confidence_flags_count', 0)}\n"
            f"• Output Folder: {output_dir}"
        )

        self._log(f"\n{msg}\n")
        messagebox.showinfo("Transcription Run Complete", msg)

    def _on_job_error(self, err_msg):
        self.is_running = False
        if self._ui_timer_id is not None:
            try:
                self.after_cancel(self._ui_timer_id)
            except Exception:
                pass
            self._ui_timer_id = None

        self._currently_transcribing_clip = None
        self._current_running_iid = None
        self.vault.end_run()
        if hasattr(self, "vault_chip"):
            self.vault_chip.refresh()
        self.btn_cancel.configure(state=tk.DISABLED)

        # Bug 1: apply deferred reload on error/cancel too.
        if self._pending_reload:
            self._load_manifest(silent=True)
        else:
            self._refresh_status_column()
            self._on_context_change()

        if self.manifest_data.get("sessions"):
            self.btn_rename_speaker.configure(state=tk.NORMAL)

        paths = self._paths()
        if paths:
            self._log(format_paths_log_header(paths))
        self._log(f"\n[JOB FAILURE]: {err_msg}\n")
        messagebox.showerror("Transcription Job Failed", err_msg)

    def _cancel_run(self):
        if self.is_running:
            self.is_running = False
            if self._ui_timer_id is not None:
                try:
                    self.after_cancel(self._ui_timer_id)
                except Exception:
                    pass
                self._ui_timer_id = None
            self.vault.end_run()
            if hasattr(self, "vault_chip"):
                self.vault_chip.refresh()
            self.transcription_mgr.cancel()
            self._log("[CANCEL] Sent termination signal to worker process. Timers stopped; preserving current metrics.")
            self._update_totals_row()
