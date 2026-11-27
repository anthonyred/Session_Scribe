"""
Stage 2 Tab: Event Setup, Media Ingest & Separation, Session Grouping & Manifest
Tkinter / ttk implementation with non-blocking threaded execution.

Speaker Mapping box (Task A):
  After 'Detect Sessions' the user may paste a block-structured mapping:

      VID001.mp4, VID002.mp4 - Kalyani Patra
      VID003.mp4
      VID004.mp4 - Ravi Kumar

  'Validate' parses and reports:
    ✗ filenames not found in the manifest
    ✗ a clip listed under two speaker names (duplicate)
    ⚠ clips not covered by any block (will fall back to time-gap grouping)

  'Apply' is enabled only when there are no errors (warnings are fine).
  On Apply the in-memory sessions are rebuilt; 'Save Manifest' must then
  be clicked to persist.
"""

import json
import os
import re
import threading
from typing import Optional
from datetime import datetime, timedelta
import tkinter as tk
from tkinter import ttk, messagebox, filedialog, scrolledtext

from .scrollable_tab import ScrollableTab

from scribe_core.time_parser import TimeParser
from scribe_core.clip_probe import ClipProbe
from scribe_core.session_grouper import SessionGrouper, SessionInfo, ClipInfo, PhotoInfo
from scribe_core.media_separator import MediaSeparator, SeparationPreview
from scribe_core.manifest import ManifestManager, EventDetails
from scribe_core.event_paths import (
    derive_event_paths,
    format_paths_log_header,
    load_glossary,
    manifest_belongs_to_event,
    proposed_event_root,
)

from .event_context import EventContext
from .hint_entry import HintEntry
from .paths_banner import PathsBanner
from . import theme
from .enrollment_dialog import VoiceProfileEnrollmentDialog
from .vault_dialog import InitializeVaultDialog, UnlockVaultDialog
from scribe_core.voice_vault import VoiceVault
from scribe_core.vault_v2 import VaultV2StorageEngine
from scribe_core.enrollment_manager import EnrollmentManager

EVENTS_ROOT = r"D:\Events"


class Stage2Tab(ScrollableTab):
    """Stage 2 Tab UI and Event Handler."""

    def __init__(self, parent, ctx: EventContext, status_callback=None, vault: Optional[VoiceVault] = None):
        super().__init__(parent)
        self.ctx = ctx
        self.status_callback = status_callback or (lambda msg: None)
        self.vault = vault or VoiceVault()

        self.time_parser = TimeParser()
        self.clip_probe = ClipProbe()
        self.separator = MediaSeparator()
        self.grouper = SessionGrouper()

        # State
        self.current_preview: SeparationPreview = None
        self.sessions: list[SessionInfo] = []
        self.photos: list[PhotoInfo] = []
        self.unassigned_photos: list[PhotoInfo] = []
        self.clips: list[ClipInfo] = []
        self._applying_folder = False
        self._stage3_ref = None

        # Mapping-box parsed state
        self._parsed_mapping: list = []   # list of (list_of_stems, speaker_name)
        self._mapping_has_errors: bool = True

        self._build_ui()
        self.ctx.bind_change(self._on_context_change)
        self._on_context_change()

    def _build_ui(self):
        # All content packed into self.inner (the scrollable canvas child).
        # Three structured cards: Event details, Media separation, Sessions and photos.

        # ------------------------------------------------------------------ #
        # Card 1: Event details                                              #
        # ------------------------------------------------------------------ #
        card_event = ttk.LabelFrame(self.inner, text=" Event details ", padding=12)
        card_event.pack(fill=tk.X, padx=6, pady=(6, 4))

        folder_row = ttk.Frame(card_event)
        folder_row.pack(fill=tk.X, pady=(0, 6))
        ttk.Label(folder_row, text="Event Folder:", width=14, font="SS_BodyBold").pack(side=tk.LEFT)
        self.event_entry = HintEntry(
            folder_row,
            textvariable=self.ctx.event_dir_var,
            hint=r"D:\Events\<event>  (event root only)",
            width=42,
        )
        self.event_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=6)
        self.event_entry.bind("<FocusOut>", self._on_event_folder_typed, add="+")
        ttk.Button(folder_row, text="Browse...", command=self._browse_event_dir).pack(side=tk.LEFT, padx=3)
        ttk.Button(folder_row, text="Scan Events in D:\\Events", command=self._scan_events_dialog).pack(side=tk.LEFT, padx=3)

        self.paths_banner = PathsBanner(card_event, self.ctx)
        self.paths_banner.pack(fill=tk.X, pady=(2, 6))

        # Even two-column grid with aligned labels and 8px multiple spacing
        self.form_frame = ttk.Frame(card_event)
        self.form_frame.pack(fill=tk.X, pady=4)
        self.form_frame.columnconfigure(1, weight=1)
        self.form_frame.columnconfigure(3, weight=1)

        # Row 0: Event Name | Date
        ttk.Label(self.form_frame, text="Event Name:", font="SS_Body").grid(row=0, column=0, sticky=tk.W, padx=(4, 8), pady=4)
        HintEntry(self.form_frame, textvariable=self.ctx.event_name_var, hint="Event name", width=40).grid(
            row=0, column=1, sticky=tk.EW, padx=(0, 16), pady=4
        )

        ttk.Label(self.form_frame, text="Date:", font="SS_Body").grid(row=0, column=2, sticky=tk.W, padx=(4, 8), pady=4)
        HintEntry(self.form_frame, textvariable=self.ctx.event_date_var, hint="e.g. 5 September 2026", width=24).grid(
            row=0, column=3, sticky=tk.EW, padx=(0, 4), pady=4
        )

        # Row 1: Organiser | Venue
        ttk.Label(self.form_frame, text="Organiser:", font="SS_Body").grid(row=1, column=0, sticky=tk.W, padx=(4, 8), pady=4)
        HintEntry(self.form_frame, textvariable=self.ctx.organiser_var, hint="Organiser (optional for now)", width=40).grid(
            row=1, column=1, sticky=tk.EW, padx=(0, 16), pady=4
        )

        ttk.Label(self.form_frame, text="Venue:", font="SS_Body").grid(row=1, column=2, sticky=tk.W, padx=(4, 8), pady=4)
        HintEntry(self.form_frame, textvariable=self.ctx.venue_var, hint="Venue (optional for now)", width=40).grid(
            row=1, column=3, sticky=tk.EW, padx=(0, 4), pady=4
        )

        # Row 2: Sponsors (opt) | Speaker Mode
        ttk.Label(self.form_frame, text="Sponsors (opt):", font="SS_Body").grid(row=2, column=0, sticky=tk.W, padx=(4, 8), pady=4)
        HintEntry(self.form_frame, textvariable=self.ctx.sponsors_var, hint="Sponsors (optional)", width=40).grid(
            row=2, column=1, sticky=tk.EW, padx=(0, 16), pady=4
        )

        ttk.Label(self.form_frame, text="Speaker Mode:", font="SS_Body").grid(row=2, column=2, sticky=tk.W, padx=(4, 8), pady=4)
        mode_frame = ttk.Frame(self.form_frame)
        mode_frame.grid(row=2, column=3, sticky=tk.W, padx=(0, 4), pady=4)
        self.seg_speaker_mode = theme.SegmentedControl(
            mode_frame,
            variable=self.ctx.speaker_mode_var,
            options=[("One speaker", "single"), ("Multiple speakers", "panel")],
        )
        self.seg_speaker_mode.pack(side=tk.LEFT)

        self.warning_label = ttk.Label(card_event, text="", foreground=theme.MSG_THEMES["error"]["fg"], font="SS_Body")
        self.warning_label.pack(anchor=tk.W, pady=(4, 2))

        # ------------------------------------------------------------------ #
        # Card 2: Media separation                                           #
        # ------------------------------------------------------------------ #
        card_media = ttk.LabelFrame(self.inner, text=" Media separation ", padding=12)
        card_media.pack(fill=tk.X, padx=6, pady=4)

        self.sep_status_bar = theme.MessageBar(
            card_media,
            text="Ready. Fill Event Folder, Name and Date, then dry-run before moving.",
            kind="info",
        )
        self.sep_status_bar.pack(fill=tk.X, pady=(0, 8))

        sep_actions = ttk.Frame(card_media)
        sep_actions.pack(fill=tk.X, pady=2)

        # Primary action in Media separation is Dry-Run Media Separation
        self.btn_dry_run = ttk.Button(
            sep_actions, text="Dry-Run Media Separation", style="Primary.TButton", command=self._run_dry_run
        )
        self.btn_dry_run.pack(side=tk.LEFT, padx=(0, 6))

        self.btn_confirm_move = ttk.Button(
            sep_actions, text="Confirm & Move Files", state=tk.DISABLED, command=self._confirm_separation
        )
        self.btn_confirm_move.pack(side=tk.LEFT, padx=4)

        self.btn_undo = ttk.Button(sep_actions, text="Undo Last Separation", command=self._undo_separation)
        self.btn_undo.pack(side=tk.LEFT, padx=4)

        self.sep_status_var = tk.StringVar(value="Ready. Fill Event Folder, Name and Date, then dry-run before moving.")
        self.sep_status_var.trace_add("write", self._on_sep_status_changed)

        # ------------------------------------------------------------------ #
        # Card 3: Sessions and photos                                        #
        # ------------------------------------------------------------------ #
        card_sessions = ttk.LabelFrame(self.inner, text=" Sessions and photos ", padding=12)
        card_sessions.pack(fill=tk.X, padx=6, pady=(4, 6))

        ctrl_row = ttk.Frame(card_sessions)
        ctrl_row.pack(fill=tk.X, pady=(0, 8))

        ttk.Label(ctrl_row, text="Gap Threshold (min):", font="SS_Body").pack(side=tk.LEFT)
        self.gap_min_var = tk.DoubleVar(value=10.0)
        ttk.Spinbox(ctrl_row, from_=1.0, to=120.0, increment=1.0, textvariable=self.gap_min_var, width=6).pack(
            side=tk.LEFT, padx=(4, 16)
        )

        ttk.Label(ctrl_row, text="Photo Grace Margin (min):", font="SS_Body").pack(side=tk.LEFT)
        self.grace_min_var = tk.DoubleVar(value=3.0)
        ttk.Spinbox(ctrl_row, from_=0.0, to=60.0, increment=0.5, textvariable=self.grace_min_var, width=6).pack(
            side=tk.LEFT, padx=(4, 16)
        )

        # Primary action in Sessions and photos is Detect Sessions
        self.btn_detect = ttk.Button(
            ctrl_row, text="Detect Sessions & Assign Photos", style="Primary.TButton", command=self._run_session_detection
        )
        self.btn_detect.pack(side=tk.LEFT, padx=4)

        # Enroll Voice Profile action button
        self.btn_enroll_voice = ttk.Button(
            ctrl_row,
            text="🎙️ Enroll Voice Profile...",
            command=self._on_enroll_voice_profile,
        )
        self.btn_enroll_voice.pack(side=tk.LEFT, padx=4)

        # Secondary action is Save Manifest (manifest.json)
        self.btn_save_manifest = ttk.Button(
            ctrl_row, text="Save Manifest (manifest.json)", style="Primary.TButton", command=self._save_manifest
        )
        self.btn_save_manifest.pack(side=tk.RIGHT, padx=4)

        # ------------------------------------------------------------------ #
        # Speaker Mapping box                                                #
        # ------------------------------------------------------------------ #
        map_frame = ttk.LabelFrame(
            card_sessions,
            text=" Speaker Mapping — paste clip-to-speaker assignments (optional) ",
            padding=8,
        )
        map_frame.pack(fill=tk.X, pady=(0, 8))

        self.map_hint = ttk.Label(
            map_frame,
            text=(
                "Format: one or more filenames (comma / newline separated, .mp4 optional)"
                "  -  Speaker Name\n"
                "Example:  VID001.mp4, VID002.mp4 - Kalyani Patra\n"
                "          VID003.mp4\n"
                "          VID004.mp4 - Ravi Kumar\n"
                "A new block starts after each name.  Unlisted clips use time-gap grouping."
            ),
            font="SS_Body",
            foreground=theme.COLOR_TEXT_SECONDARY,
            justify=tk.LEFT,
        )
        self.map_hint.pack(anchor=tk.W, pady=(0, 4))

        self.mapping_text = tk.Text(
            map_frame,
            height=5,
            font="SS_Code",
            wrap=tk.WORD,
            bg=theme.COLOR_INPUT_BG,
            fg=theme.COLOR_TEXT_PRIMARY,
            insertbackground=theme.COLOR_TEXT_PRIMARY,
            highlightthickness=1,
            highlightbackground=theme.COLOR_INPUT_BORDER,
            highlightcolor=theme.COLOR_BORDER_FOCUS,
            padx=8,
            pady=6,
            relief="flat",
        )
        self.mapping_text.pack(fill=tk.X, pady=(0, 6))
        self.register_wheel(self.mapping_text)

        map_btn_row = ttk.Frame(map_frame)
        map_btn_row.pack(fill=tk.X)

        self.btn_validate_mapping = ttk.Button(
            map_btn_row, text="Validate Mapping", command=self._validate_mapping
        )
        self.btn_validate_mapping.pack(side=tk.LEFT, padx=(0, 4))

        self.btn_apply_mapping = ttk.Button(
            map_btn_row, text="Apply Mapping", command=self._apply_mapping, state=tk.DISABLED
        )
        self.btn_apply_mapping.pack(side=tk.LEFT, padx=(0, 4))

        self.btn_reset_mapping = ttk.Button(
            map_btn_row, text="Reset Mapping", command=self._reset_mapping
        )
        self.btn_reset_mapping.pack(side=tk.LEFT, padx=(0, 8))

        # Preview area (read-only ScrolledText styled as elevated monospace card)
        self.mapping_preview = scrolledtext.ScrolledText(
            map_frame,
            height=4,
            font="SS_Code",
            state="disabled",
            wrap=tk.WORD,
            background=theme.COLOR_INPUT_BG,
            foreground=theme.COLOR_TEXT_PRIMARY,
            insertbackground=theme.COLOR_TEXT_PRIMARY,
            highlightthickness=1,
            highlightbackground=theme.COLOR_BORDER,
            relief="flat",
            padx=8,
            pady=6,
        )
        self.mapping_preview.pack(fill=tk.X, pady=(4, 0))
        self.register_wheel(self.mapping_preview)
        theme.style_code_panel(self.mapping_preview)

        # ------------------------------------------------------------------ #
        # Session & photo tables                                             #
        # ------------------------------------------------------------------ #
        tables_pane = ttk.PanedWindow(card_sessions, orient=tk.HORIZONTAL)
        tables_pane.pack(fill=tk.X, pady=4)

        sess_frame = ttk.Frame(tables_pane)
        tables_pane.add(sess_frame, weight=1)
        ttk.Label(sess_frame, text="Sessions (Double-click speaker to rename, or mode to toggle):", font="SS_BodyBold").pack(
            anchor=tk.W
        )

        tree_box = ttk.Frame(sess_frame)
        tree_box.pack(fill=tk.BOTH, expand=True)

        self.sess_tree = ttk.Treeview(
            tree_box,
            columns=("id", "speaker", "mode", "clips", "photos", "start", "end"),
            show="headings",
            height=8,
            selectmode="extended",
        )
        self.sess_tree.heading("id", text="#")
        self.sess_tree.heading("speaker", text="Speaker / Aliases")
        self.sess_tree.heading("mode", text="Structure")
        self.sess_tree.heading("clips", text="Clips")
        self.sess_tree.heading("photos", text="Photos")
        self.sess_tree.heading("start", text="Start Time")
        self.sess_tree.heading("end", text="End Time")

        self.sess_tree.column("id", width=35, anchor=tk.CENTER)
        self.sess_tree.column("speaker", width=140)
        self.sess_tree.column("mode", width=110, anchor=tk.CENTER)
        self.sess_tree.column("clips", width=45, anchor=tk.CENTER)
        self.sess_tree.column("photos", width=50, anchor=tk.CENTER)
        self.sess_tree.column("start", width=75, anchor=tk.CENTER)
        self.sess_tree.column("end", width=75, anchor=tk.CENTER)

        sess_scroll = ttk.Scrollbar(tree_box, orient=tk.VERTICAL, command=self.sess_tree.yview)
        self.sess_tree.configure(yscrollcommand=sess_scroll.set)
        self.sess_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sess_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.sess_tree.bind("<Double-1>", self._on_session_double_click)
        self.register_wheel(self.sess_tree)

        # Action toolbar for sessions
        sess_btn_row = ttk.Frame(sess_frame)
        sess_btn_row.pack(fill=tk.X, pady=(4, 0))

        self.btn_event_roster = ttk.Button(
            sess_btn_row,
            text="Event Roster…",
            command=self._open_event_roster_dialog,
        )
        self.btn_event_roster.pack(side=tk.LEFT, padx=(0, 4))

        self.btn_config_session = ttk.Button(
            sess_btn_row,
            text="Configure Session…",
            command=self._open_session_config_dialog,
        )
        self.btn_config_session.pack(side=tk.LEFT, padx=4)

        self.btn_merge_sessions = ttk.Button(
            sess_btn_row,
            text="Merge Sessions",
            command=self._merge_selected_sessions,
        )
        self.btn_merge_sessions.pack(side=tk.LEFT, padx=4)

        self.btn_split_session = ttk.Button(
            sess_btn_row,
            text="Split Session…",
            command=self._split_selected_session,
        )
        self.btn_split_session.pack(side=tk.LEFT, padx=4)

        self.btn_roster = ttk.Button(
            sess_btn_row,
            text="Legacy Roster…",
            command=self._open_roster_dialog,
        )
        self.btn_roster.pack(side=tk.LEFT, padx=4)

        self.btn_toggle_mode = ttk.Button(
            sess_btn_row,
            text="Toggle Mode",
            command=self._toggle_selected_session_mode,
        )
        self.btn_toggle_mode.pack(side=tk.LEFT, padx=4)

        photo_frame = ttk.Frame(tables_pane)
        tables_pane.add(photo_frame, weight=1)
        ttk.Label(photo_frame, text="Photos & Session Assignment:", font=("Segoe UI", 9, "bold")).pack(anchor=tk.W)

        self.photo_tree = ttk.Treeview(
            photo_frame,
            columns=("name", "time", "assigned", "status"),
            show="headings",
            selectmode="browse",
            height=8,
        )
        self.photo_tree.heading("name", text="Photo Filename")
        self.photo_tree.heading("time", text="Timestamp (IST)")
        self.photo_tree.heading("assigned", text="Assigned Speaker")
        self.photo_tree.heading("status", text="Status")

        self.photo_tree.column("name", width=160)
        self.photo_tree.column("time", width=95, anchor=tk.CENTER)
        self.photo_tree.column("assigned", width=110)
        self.photo_tree.column("status", width=80, anchor=tk.CENTER)

        photo_scroll = ttk.Scrollbar(photo_frame, orient=tk.VERTICAL, command=self.photo_tree.yview)
        self.photo_tree.configure(yscrollcommand=photo_scroll.set)
        self.photo_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        photo_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.register_wheel(self.photo_tree)

        theme.configure_treeview_status_tags(self.sess_tree)
        theme.configure_treeview_status_tags(self.photo_tree)
        self.sess_tree.tag_configure("unnamed_session", foreground=theme.MSG_THEMES["warning"]["fg"])

    def refresh_theme(self) -> None:
        """Update colors, inputs, code panels, and treeview status tags for active theme."""
        super().refresh_theme()
        if hasattr(self, "event_entry") and hasattr(self.event_entry, "refresh_theme"):
            self.event_entry.refresh_theme()
        if hasattr(self, "paths_banner") and hasattr(self.paths_banner, "refresh_theme"):
            self.paths_banner.refresh_theme()
        if hasattr(self, "form_frame"):
            for child in self.form_frame.winfo_children():
                if hasattr(child, "refresh_theme"):
                    child.refresh_theme()
        if hasattr(self, "seg_speaker_mode") and hasattr(self.seg_speaker_mode, "refresh_theme"):
            self.seg_speaker_mode.refresh_theme()
        if hasattr(self, "mapping_text"):
            self.mapping_text.configure(
                bg=theme.COLOR_INPUT_BG,
                fg=theme.COLOR_TEXT_PRIMARY,
                insertbackground=theme.COLOR_TEXT_PRIMARY,
                highlightbackground=theme.COLOR_INPUT_BORDER,
                highlightcolor=theme.COLOR_BORDER_FOCUS,
                font="SS_Code",
            )
        if hasattr(self, "mapping_preview"):
            theme.style_code_panel(self.mapping_preview)
        if hasattr(self, "map_hint"):
            self.map_hint.configure(
                foreground=theme.COLOR_TEXT_SECONDARY,
            )
        if hasattr(self, "sess_tree"):
            theme.configure_treeview_status_tags(self.sess_tree)
            self.sess_tree.tag_configure("unnamed_session", foreground=theme.MSG_THEMES["warning"]["fg"])
        if hasattr(self, "photo_tree"):
            theme.configure_treeview_status_tags(self.photo_tree)
        if hasattr(self, "sep_status_bar"):
            self.sep_status_bar.configure(bg=theme.COLOR_CARD_BG)

    def _on_sep_status_changed(self, *_args):
        msg = self.sep_status_var.get()
        kind = "info"
        lower = msg.lower()
        if "error" in lower or "corrupt" in lower or "missing" in lower or "not match" in lower:
            kind = "error"
        elif "complete" in lower or "verified" in lower:
            kind = "success"
        elif "warning" in lower or "unassigned" in lower:
            kind = "warning"
        if hasattr(self, "sep_status_bar"):
            self.sep_status_bar.set_message(msg, kind=kind)

    # ------------------------------------------------------------------
    # Mapping box helpers
    # ------------------------------------------------------------------

    def _set_mapping_preview(self, text: str):
        """Write *text* into the read-only preview area."""
        self.mapping_preview.configure(state="normal")
        self.mapping_preview.delete("1.0", tk.END)
        self.mapping_preview.insert(tk.END, text)
        self.mapping_preview.configure(state="disabled")

    @staticmethod
    def _normalise_stems(raw_filenames: list) -> list:
        """Strip extension and upper-case each token for case-insensitive matching."""
        result = []
        for t in raw_filenames:
            s = t.strip()
            if not s:
                continue
            s = os.path.splitext(s)[0].upper()
            result.append(s)
        return result

    def _validate_mapping(self):
        """
        Parse the text in the mapping box, validate against self.clips, and
        update the preview area. Enable Apply only when there are no errors.
        """
        self._parsed_mapping = []
        self._mapping_has_errors = True
        self.btn_apply_mapping.configure(state=tk.DISABLED)

        raw = self.mapping_text.get("1.0", tk.END).strip()
        if not raw:
            self._set_mapping_preview("(paste your mapping above, then click Validate)")
            return

        if not self.clips:
            self._set_mapping_preview("⚠ No clips loaded — run 'Detect Sessions & Assign Photos' first.")
            return

        # Build a stem → ClipInfo lookup (case-insensitive)
        manifest_stems: dict[str, ClipInfo] = {}
        for c in self.clips:
            stem = os.path.splitext(c.filename)[0].upper()
            manifest_stems[stem] = c

        # ---- Parse --------------------------------------------------------
        # Each block = zero or more filename-only lines, then a line containing
        # "filenames - Speaker Name" (dash is the separator).
        blocks: list[tuple[list[str], str]] = []   # (stems, speaker_name)
        pending_tokens: list[str] = []

        for line in raw.splitlines():
            line_stripped = line.strip()
            if not line_stripped:
                continue

            if " - " in line_stripped:
                # Split on the LAST " - " to tolerate dashes in file names.
                idx = line_stripped.rfind(" - ")
                filenames_part = line_stripped[:idx]
                speaker_name = line_stripped[idx + 3:].strip()

                # Filenames on this line (before the dash)
                tokens_here = [t.strip() for t in re.split(r"[,]+", filenames_part) if t.strip()]
                pending_tokens.extend(tokens_here)

                stems = self._normalise_stems(pending_tokens)
                if stems and speaker_name:
                    blocks.append((stems, speaker_name))
                pending_tokens = []
            else:
                # Accumulate filenames for the next block
                tokens = [t.strip() for t in re.split(r"[,]+", line_stripped) if t.strip()]
                pending_tokens.extend(tokens)

        if not blocks:
            self._set_mapping_preview(
                "✗ No valid blocks found.\n"
                "Each block must end with:  filename(s) - Speaker Name"
            )
            return

        # ---- Validate -----------------------------------------------------
        errors: list[str] = []
        warnings: list[str] = []
        assigned_stem: dict[str, str] = {}   # stem → first speaker that claimed it

        for stems, speaker_name in blocks:
            for stem in stems:
                if stem not in manifest_stems:
                    errors.append(f"  ✗ '{stem}' not found in manifest")
                elif stem in assigned_stem:
                    errors.append(
                        f"  ✗ '{stem}' claimed by both '{assigned_stem[stem]}' and '{speaker_name}'"
                    )
                else:
                    assigned_stem[stem] = speaker_name

        uncovered = [s for s in manifest_stems if s not in assigned_stem]
        if uncovered:
            warnings.append(
                f"  ⚠ {len(uncovered)} clip(s) not covered — will fall back to time-gap grouping"
            )

        # ---- Preview lines ------------------------------------------------
        preview_lines = ["Preview:"]
        for stems, speaker_name in blocks:
            valid_count = sum(
                1 for s in stems
                if s in manifest_stems and assigned_stem.get(s) == speaker_name
            )
            preview_lines.append(f"  ✓ {speaker_name}: {valid_count} clip(s)")

        if warnings:
            preview_lines.append("")
            preview_lines.extend(warnings)

        if errors:
            preview_lines.append("")
            preview_lines.append(f"Errors ({len(errors)}) — fix before applying:")
            preview_lines.extend(errors)

        self._set_mapping_preview("\n".join(preview_lines))

        if errors:
            return  # Apply stays disabled

        # Store for apply step
        self._parsed_mapping = blocks
        self._mapping_has_errors = False
        self.btn_apply_mapping.configure(state=tk.NORMAL)

    def _get_current_mapping_dict(self) -> dict[str, str]:
        """Extract authoritative mapping dict (stem / filename -> speaker_name)."""
        mapping_dict: dict[str, str] = {}
        # 1. From validated mapping blocks if present
        if self._parsed_mapping:
            for stems, spk in self._parsed_mapping:
                for stem in stems:
                    mapping_dict[stem.upper()] = spk
        else:
            # 2. Or parse directly from mapping_text
            raw = self.mapping_text.get("1.0", tk.END).strip()
            if raw:
                pending_tokens = []
                for line in raw.splitlines():
                    line_stripped = line.strip()
                    if not line_stripped:
                        continue
                    if " - " in line_stripped:
                        idx = line_stripped.rfind(" - ")
                        filenames_part = line_stripped[:idx]
                        speaker_name = line_stripped[idx + 3:].strip()
                        tokens_here = [t.strip() for t in re.split(r"[,]+", filenames_part) if t.strip()]
                        pending_tokens.extend(tokens_here)
                        for token in pending_tokens:
                            stem = os.path.splitext(token.strip())[0].upper()
                            mapping_dict[stem] = speaker_name
                        pending_tokens = []
                    else:
                        tokens = [t.strip() for t in re.split(r"[,]+", line_stripped) if t.strip()]
                        pending_tokens.extend(tokens)

        # 3. Fall back to existing clip speaker names if not in mapping_dict
        for c in self.clips:
            if c.speaker_name and not c.speaker_name.startswith("Unnamed "):
                stem = os.path.splitext(c.filename)[0].upper()
                if stem not in mapping_dict:
                    mapping_dict[stem] = c.speaker_name
                mapping_dict[c.filename.upper()] = c.speaker_name

        return mapping_dict

    def _refresh_tables(self):
        """Refresh session and photo Treeviews from self.sessions and self.photos."""
        for item in self.sess_tree.get_children():
            self.sess_tree.delete(item)
        for item in self.photo_tree.get_children():
            self.photo_tree.delete(item)

        is_unlocked = self.vault.is_unlocked()
        for i, s in enumerate(self.sessions):
            st = s.start_time.strftime("%H:%M:%S") if s.start_time else "--"
            et = s.end_time.strftime("%H:%M:%S") if s.end_time else "--"
            row_tag = "even_row" if i % 2 == 0 else "odd_row"
            tags = [row_tag]
            is_unnamed = not s.speaker_name or s.speaker_name.startswith("Unnamed ")
            if is_unnamed:
                tags.append("unnamed_session")
            is_panel = s.session_type == "panel" or getattr(s, "speaker_mode", "single") == "panel"
            mode_lbl = "Panel Discussion" if is_panel else "Solo Speaker"

            if s.session_type == "solo" and s.expected_speaker:
                alias = s.expected_speaker.get("alias", "")
                name = s.expected_speaker.get("name") if is_unlocked else None
                speaker_lbl = f"{alias} ({name})" if name else (alias or s.speaker_name)
            elif s.session_type == "panel":
                host_alias = s.host.get("alias", "") if s.host else ""
                panelist_aliases = ", ".join(p.get("alias", "") for p in s.panelists)
                if host_alias:
                    speaker_lbl = f"Host: {host_alias} | Panelists: {panelist_aliases}" if panelist_aliases else f"Host: {host_alias}"
                else:
                    speaker_lbl = s.speaker_name
            else:
                speaker_lbl = s.speaker_name

            self.sess_tree.insert(
                "", tk.END,
                iid=str(s.session_id),
                values=(s.session_id, speaker_lbl, mode_lbl, len(s.clips), len(s.photos), st, et),
                tags=tuple(tags),
            )

        for i, p in enumerate(self.photos):
            t = p.timestamp.strftime("%H:%M:%S") if p.timestamp else "--"
            spk = p.assigned_speaker if p.assigned_speaker else "None"
            row_tag = "even_row" if i % 2 == 0 else "odd_row"
            self.photo_tree.insert("", tk.END, values=(p.filename, t, spk, p.assignment_status), tags=(row_tag,))

    def _apply_mapping(self):
        """
        Rebuild self.sessions from the validated mapping.
        An applied mapping is authoritative: each clip carries its person's name.
        Consecutive clips of the same person form sessions; splits on speaker change
        or gap > threshold. Unmapped clips form individual Unnamed sessions.
        """
        if self._mapping_has_errors or not self._parsed_mapping:
            return

        mapping_dict = self._get_current_mapping_dict()

        for c in self.clips:
            stem = os.path.splitext(c.filename)[0].upper()
            c.speaker_name = mapping_dict.get(stem) or mapping_dict.get(c.filename.upper())

        grouper = SessionGrouper(
            gap_threshold_seconds=self.gap_min_var.get() * 60.0,
            photo_grace_seconds=self.grace_min_var.get() * 60.0,
        )
        self.sessions = grouper.group_clips(
            self.clips,
            default_speaker_mode=self.ctx.speaker_mode_var.get(),
            mapping_dict=mapping_dict,
        )
        self.sessions, self.unassigned_photos = grouper.assign_photos(self.photos, self.sessions)

        self._refresh_tables()
        self.btn_apply_mapping.configure(state=tk.DISABLED)
        self._set_mapping_preview(
            f"✓ Mapping applied — {len(self.sessions)} session(s) derived.\n"
            "Click 'Save Manifest' to persist."
        )
        self.status_callback(
            f"Speaker mapping applied: {len(self.sessions)} session(s). "
            "Save the manifest to lock it in."
        )

    def _reset_mapping(self):
        """Reset speaker mapping after confirmation, marking all clips as Unnamed."""
        if not self.clips and not self.sessions:
            self.mapping_text.delete("1.0", tk.END)
            self._set_mapping_preview("")
            return

        confirm = messagebox.askyesno(
            "Reset Mapping?",
            "Are you sure you want to reset the speaker mapping?\n\n"
            "This will clear the mapping box, remove all speaker assignments, "
            "and re-derive sessions as Unnamed.",
            parent=self,
        )
        if not confirm:
            return

        self.mapping_text.delete("1.0", tk.END)
        self._parsed_mapping = []
        self._mapping_has_errors = True
        self.btn_apply_mapping.configure(state=tk.DISABLED)
        self._set_mapping_preview("(mapping reset — clips grouped as Unnamed)")

        for c in self.clips:
            c.speaker_name = None

        grouper = SessionGrouper(
            gap_threshold_seconds=self.gap_min_var.get() * 60.0,
            photo_grace_seconds=self.grace_min_var.get() * 60.0,
        )
        self.sessions = grouper.group_clips(
            self.clips, default_speaker_mode=self.ctx.speaker_mode_var.get()
        )
        self.sessions, self.unassigned_photos = grouper.assign_photos(self.photos, self.sessions)

        self._refresh_tables()
        self.status_callback(
            f"Speaker mapping reset: {len(self.sessions)} Unnamed session(s) created."
        )

    # ------------------------------------------------------------------
    # Context / folder helpers (unchanged from previous version)
    # ------------------------------------------------------------------

    def _on_context_change(self):
        ready = self.ctx.required_ready()
        folder_ok = bool(self.ctx.event_dir_var.get().strip())
        self.btn_undo.configure(state=tk.NORMAL if folder_ok else tk.DISABLED)

        if not ready:
            self.btn_dry_run.configure(state=tk.DISABLED)
            self.btn_detect.configure(state=tk.DISABLED)
            self.btn_save_manifest.configure(state=tk.DISABLED)
            self.btn_confirm_move.configure(state=tk.DISABLED)
            self.warning_label.configure(text="")
            return

        # Required fields filled — check data\ for finer-grained button states.
        paths = self._paths()
        data_dir = paths["data"] if paths else ""
        data_exists = bool(data_dir and os.path.isdir(data_dir))

        # Dry-Run: enabled when data\ directory exists.  The dry-run itself
        # reports zero-file results inline instead of blocking upfront.
        self.btn_dry_run.configure(state=tk.NORMAL if data_exists else tk.DISABLED)

        # Detect Sessions: enabled when video files are available — either in
        # data\Videos\ (post-separation) or flat in data\ (pre-separation).
        detect_ok = data_exists and self._data_dir_has_videos(data_dir)
        self.btn_detect.configure(state=tk.NORMAL if detect_ok else tk.DISABLED)

        # Save Manifest: enabled as long as required fields are filled.
        self.btn_save_manifest.configure(state=tk.NORMAL)

        # Inline hint when data\ is missing.
        if not data_exists and data_dir:
            self.sep_status_var.set(
                f"data\\ folder not found: {data_dir} — place media there first."
            )

        # Optional field warnings (Organiser / Venue).
        warnings = self.ctx.missing_optional_warnings()
        self.warning_label.configure(text=("WARNING: " + " | ".join(warnings)) if warnings else "")

    def _paths(self):
        root = self.ctx.event_dir_var.get().strip()
        if not root:
            return None
        return derive_event_paths(root)

    def _data_dir_has_videos(self, data_dir: str) -> bool:
        """True if data\\ contains video files directly or inside data\\Videos\\."""
        if not data_dir or not os.path.isdir(data_dir):
            return False
        # Flat files directly in data\
        for f in os.listdir(data_dir):
            if (
                os.path.isfile(os.path.join(data_dir, f))
                and self.separator.classify_extension(f) == "video"
            ):
                return True
        # data\Videos\ subfolder
        vdir = os.path.join(data_dir, "Videos")
        if os.path.isdir(vdir):
            for f in os.listdir(vdir):
                if (
                    os.path.isfile(os.path.join(vdir, f))
                    and self.separator.classify_extension(f) == "video"
                ):
                    return True
        return False

    def _gate_or_warn(self) -> bool:
        if self.ctx.required_ready():
            return True
        messagebox.showwarning(
            "Event details required",
            "Fill Event Folder, Event Name and Date before this action.",
        )
        return False

    def _on_event_folder_typed(self, _event=None):
        typed = self.ctx.event_dir_var.get().strip()
        if typed and os.path.isdir(typed):
            self._apply_event_folder(typed, ask_parent=True)

    def _browse_event_dir(self):
        with self.preserve_scroll():
            initial = EVENTS_ROOT if os.path.isdir(EVENTS_ROOT) else os.getcwd()
            d = filedialog.askdirectory(initialdir=initial, title="Select Event Root Folder")
            if d:
                self._apply_event_folder(d, ask_parent=True)

    def _scan_events_dialog(self):
        with self.preserve_scroll():
            if not os.path.exists(EVENTS_ROOT):
                messagebox.showerror("Error", f"Directory {EVENTS_ROOT} does not exist.")
                return

            items = [
                d
                for d in os.listdir(EVENTS_ROOT)
                if os.path.isdir(os.path.join(EVENTS_ROOT, d))
                and not d.startswith("_")
            ]
            if not items:
                messagebox.showinfo("Scan Events", f"No event directories found in {EVENTS_ROOT}.")
                return

            dialog = tk.Toplevel(self)
            dialog.title("Select Event")
            dialog.minsize(360, 280)
            dialog.resizable(True, True)
            dialog.transient(self)
            dialog.grab_set()

            def select():
                sel = lb.curselection()
                if sel:
                    event_name = lb.get(sel[0])
                    dialog.destroy()
                    self._apply_event_folder(os.path.join(EVENTS_ROOT, event_name), ask_parent=False)

            btn_box = ttk.Frame(dialog)
            btn_box.pack(fill=tk.X, side=tk.BOTTOM, padx=10, pady=8)
            ttk.Button(btn_box, text="Select", command=select).pack(side=tk.RIGHT, padx=4)
            ttk.Button(btn_box, text="Cancel", command=dialog.destroy).pack(side=tk.RIGHT)

            ttk.Label(dialog, text=f"Available Events in {EVENTS_ROOT}:", font=("Segoe UI", 10, "bold")).pack(
                anchor=tk.W, padx=10, pady=8
            )
            lb = tk.Listbox(dialog, selectmode=tk.SINGLE)
            for item in sorted(items):
                lb.insert(tk.END, item)
            lb.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)
            lb.bind("<Double-Button-1>", lambda _e: select())
            dialog.bind("<Escape>", lambda _e: dialog.destroy())

            theme.center_dialog(dialog, self, width=440, height=360, min_width=360, min_height=280)
            dialog.wait_window()

    def set_stage3_tab(self, ref):
        self._stage3_ref = ref

    def _apply_event_folder(self, selected: str, ask_parent: bool = True):
        if self._applying_folder:
            return
        if hasattr(self, "_stage3_ref") and self._stage3_ref:
            if not self._stage3_ref.check_unsaved_changes():
                return
        selected = os.path.abspath(selected.strip())
        if not os.path.isdir(selected):
            messagebox.showerror("Invalid folder", f"Not a directory:\n{selected}")
            return

        if os.path.normcase(selected) == os.path.normcase(os.path.abspath(EVENTS_ROOT)):
            messagebox.showwarning(
                "Select an event folder",
                f"Select an event root inside {EVENTS_ROOT}, not {EVENTS_ROOT} itself.",
            )
            return

        proposed = proposed_event_root(selected)
        if ask_parent and os.path.normcase(proposed) != os.path.normcase(selected):
            use_parent = messagebox.askyesno(
                "Use event root?",
                "You selected a folder inside an event, not the event root:\n"
                f"  Selected: {selected}\n"
                f"  Event root: {proposed}\n\n"
                "Use the event root instead?\n"
                "(data, data\\Photos, data\\Videos and scribe are derived from the event root.)",
            )
            if use_parent:
                selected = proposed
            else:
                return

        prev_event = self.ctx.event_dir_var.get().strip()
        is_different = (
            os.path.normcase(os.path.abspath(selected)) != os.path.normcase(os.path.abspath(prev_event))
            if prev_event else True
        )

        self._applying_folder = True
        try:
            with self.preserve_scroll():
                # Always clear previous event data completely on switch
                self._clear_event_data(clear_mapping=is_different)
                self.ctx.event_dir_var.set(selected)
                self._load_from_manifest(selected, is_different_event=is_different)
                paths = derive_event_paths(selected)
                stored = load_glossary(paths["scribe"])
                self.ctx.glossary_var.set(stored)
        finally:
            self._applying_folder = False

    def _clear_event_data(self, clear_mapping: bool = True):
        """Clear previous event tables, clips, photos, sessions, and mapping."""
        for item in self.sess_tree.get_children():
            self.sess_tree.delete(item)
        for item in self.photo_tree.get_children():
            self.photo_tree.delete(item)
        self.sessions = []
        self.photos = []
        self.unassigned_photos = []
        self.clips = []
        self._parsed_mapping = []
        self.current_preview = None
        self.btn_apply_mapping.configure(state=tk.DISABLED)
        self._set_mapping_preview("")
        if clear_mapping:
            self.mapping_text.delete("1.0", tk.END)

    def _load_from_manifest(self, event_root: str, is_different_event: bool = True):
        """
        Load complete manifest: event details, settings, sessions, photos,
        and rebuild speaker mapping. Verify against disk.
        """
        paths = derive_event_paths(event_root)
        manifest_path = paths["manifest"]

        if not os.path.isfile(manifest_path):
            self.sep_status_var.set("No manifest.json found for this event. Run media separation and Detect Sessions.")
            return

        try:
            with open(manifest_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            self.sep_status_var.set(f"Manifest corrupt or unreadable: {e}")
            return

        if not manifest_belongs_to_event(data, manifest_path, event_root):
            self.sep_status_var.set("Manifest does not match the selected event folder.")
            return

        # 1. Fill event fields
        ev = data.get("event") or {}
        if ev.get("name"):
            self.ctx.event_name_var.set(str(ev.get("name")))
        if ev.get("date"):
            self.ctx.event_date_var.set(str(ev.get("date")))
        if ev.get("organiser"):
            self.ctx.organiser_var.set(str(ev.get("organiser")))
        if ev.get("venue"):
            self.ctx.venue_var.set(str(ev.get("venue")))
        if ev.get("sponsors"):
            self.ctx.sponsors_var.set(str(ev.get("sponsors")))
        if ev.get("speaker_mode"):
            self.ctx.speaker_mode_var.set(str(ev.get("speaker_mode")))

        # 2. Fill settings (gap threshold & photo grace margin)
        settings = data.get("settings") or {}
        if "session_gap_threshold_seconds" in settings:
            try:
                self.gap_min_var.set(float(settings["session_gap_threshold_seconds"]) / 60.0)
            except (ValueError, TypeError):
                pass
        if "photo_grace_seconds" in settings:
            try:
                self.grace_min_var.set(float(settings["photo_grace_seconds"]) / 60.0)
            except (ValueError, TypeError):
                pass

        # 3. Reconstruct Sessions, Clips, Photos
        loaded_sessions: list[SessionInfo] = []
        all_clips: list[ClipInfo] = []
        all_photos: list[PhotoInfo] = []

        for s_dict in data.get("sessions", []):
            sess_clips: list[ClipInfo] = []
            for c_dict in s_dict.get("clips", []):
                st = datetime.fromisoformat(c_dict["start_time"]) if c_dict.get("start_time") else None
                et = datetime.fromisoformat(c_dict["end_time"]) if c_dict.get("end_time") else None
                c_spk = c_dict.get("speaker_name") or s_dict.get("speaker")
                c = ClipInfo(
                    filename=c_dict.get("name", ""),
                    filepath=c_dict.get("path", ""),
                    start_time=st,
                    duration=c_dict.get("duration", 0.0),
                    end_time=et,
                    time_source=c_dict.get("time_source", "filename"),
                    speaker_name=c_spk,
                    person_id=c_dict.get("person_id"),
                )
                sess_clips.append(c)
                all_clips.append(c)

            sess_photos: list[PhotoInfo] = []
            for p_dict in s_dict.get("photos", []):
                pts = datetime.fromisoformat(p_dict["timestamp"]) if p_dict.get("timestamp") else None
                p = PhotoInfo(
                    filename=p_dict.get("name", ""),
                    filepath=p_dict.get("path", ""),
                    timestamp=pts,
                    assigned_session_id=p_dict.get("assigned_session_id", s_dict.get("session_id")),
                    assigned_speaker=p_dict.get("assigned_speaker", s_dict.get("speaker")),
                    assignment_status=p_dict.get("status", "Assigned"),
                    time_source=p_dict.get("time_source", "filename")
                )
                sess_photos.append(p)
                all_photos.append(p)

            s_start = datetime.fromisoformat(s_dict["start_time"]) if s_dict.get("start_time") else None
            s_end = datetime.fromisoformat(s_dict["end_time"]) if s_dict.get("end_time") else None
            sess = SessionInfo(
                session_id=s_dict.get("session_id", len(loaded_sessions) + 1),
                speaker_name=s_dict.get("speaker", f"Speaker {len(loaded_sessions) + 1}"),
                clips=sess_clips,
                photos=sess_photos,
                start_time=s_start,
                end_time=s_end,
                speaker_mode=s_dict.get("speaker_mode", ev.get("speaker_mode", "single")),
                roster=s_dict.get("roster", []),
            )
            loaded_sessions.append(sess)

        # Unassigned photos
        loaded_unassigned: list[PhotoInfo] = []
        for p_dict in data.get("unassigned_photos", []):
            pts = datetime.fromisoformat(p_dict["timestamp"]) if p_dict.get("timestamp") else None
            p = PhotoInfo(
                filename=p_dict.get("name", ""),
                filepath=p_dict.get("path", ""),
                timestamp=pts,
                assigned_session_id=None,
                assigned_speaker=None,
                assignment_status=p_dict.get("status", "Unassigned"),
                time_source=p_dict.get("time_source", "filename")
            )
            loaded_unassigned.append(p)
            all_photos.append(p)

        self.sessions = loaded_sessions
        self.clips = all_clips
        self.photos = all_photos
        self.unassigned_photos = loaded_unassigned

        # 4. Check manifest against disk
        # (a) clips in manifest that no longer exist
        missing_clips: list[ClipInfo] = []
        for c in all_clips:
            found_path = ""
            for candidate in (
                c.filepath,
                os.path.join(paths["videos"], c.filename),
                os.path.join(paths["data"], c.filename),
            ):
                if candidate and os.path.isfile(candidate):
                    found_path = candidate
                    break
            if found_path:
                c.filepath = found_path
            else:
                missing_clips.append(c)

        missing_stems = {os.path.splitext(c.filename)[0].upper() for c in missing_clips}

        # (b) new files on disk that the manifest does not know
        manifest_filenames = {c.filename.lower() for c in all_clips}
        disk_video_files: list[str] = []
        for d in (paths["videos"], paths["data"]):
            if os.path.isdir(d):
                for f in os.listdir(d):
                    fp = os.path.join(d, f)
                    if os.path.isfile(fp) and self.separator.classify_extension(f) == "video":
                        disk_video_files.append(f)

        new_files = [f for f in disk_video_files if f.lower() not in manifest_filenames]

        # 5. Populate Treeviews with rows and missing tags
        self.sess_tree.tag_configure("missing", foreground=theme.TINT_FAILED_FG)
        self.photo_tree.tag_configure("missing", foreground=theme.TINT_FAILED_FG)

        for i, s in enumerate(self.sessions):
            st_str = s.start_time.strftime("%H:%M:%S") if s.start_time else "--"
            et_str = s.end_time.strftime("%H:%M:%S") if s.end_time else "--"
            sess_missing = any(os.path.splitext(c.filename)[0].upper() in missing_stems for c in s.clips)
            speaker_label = f"{s.speaker_name} ⚠" if sess_missing else s.speaker_name
            mode_lbl = "Multiple speakers" if getattr(s, "speaker_mode", "single") == "panel" else "One speaker"
            row_tag = "even_row" if i % 2 == 0 else "odd_row"
            tags = [row_tag]
            if sess_missing:
                tags.append("missing")
            if not s.speaker_name or s.speaker_name.startswith("Unnamed "):
                tags.append("unnamed_session")
            self.sess_tree.insert(
                "", tk.END,
                iid=str(s.session_id),
                values=(s.session_id, speaker_label, mode_lbl, len(s.clips), len(s.photos), st_str, et_str),
                tags=tuple(tags),
            )

        for i, p in enumerate(self.photos):
            t_str = p.timestamp.strftime("%H:%M:%S") if p.timestamp else "--"
            spk_str = p.assigned_speaker if p.assigned_speaker else "None"
            photo_missing = not (p.filepath and os.path.isfile(p.filepath))
            row_tag = "even_row" if i % 2 == 0 else "odd_row"
            tags = (row_tag, "missing") if photo_missing else (row_tag,)
            self.photo_tree.insert(
                "", tk.END,
                values=(p.filename, t_str, spk_str, p.assignment_status),
                tags=tags,
            )

        # 6. Rebuild Speaker Mapping text box
        rebuilt_lines = []
        for s in self.sessions:
            clip_names = ", ".join(c.filename for c in s.clips)
            rebuilt_lines.append(f"{clip_names} - {s.speaker_name}")
        rebuilt_text = "\n".join(rebuilt_lines)

        current_typed = self.mapping_text.get("1.0", tk.END).strip()
        should_replace = True
        if current_typed:
            should_replace = messagebox.askyesno(
                "Replace Speaker Mapping?",
                "Speaker mapping contains text. Replace it with the mapping from the saved manifest?"
            )

        if should_replace:
            self.mapping_text.delete("1.0", tk.END)
            self.mapping_text.insert("1.0", rebuilt_text)
            self._validate_mapping()

        # 7. Inline status message
        status_msgs = []
        if missing_clips:
            m_names = ", ".join(c.filename for c in missing_clips[:3])
            m_extra = f" (+{len(missing_clips)-3} more)" if len(missing_clips) > 3 else ""
            status_msgs.append(f"⚠ {len(missing_clips)} clip(s) missing from disk: {m_names}{m_extra}")
        if new_files:
            n_names = ", ".join(new_files[:3])
            n_extra = f" (+{len(new_files)-3} more)" if len(new_files) > 3 else ""
            status_msgs.append(f"⚠ {len(new_files)} new file(s) on disk: {n_names}{n_extra} — run Detect Sessions to include them")

        if status_msgs:
            self.sep_status_var.set(" | ".join(status_msgs))
        else:
            self.sep_status_var.set(
                f"Loaded manifest with {len(self.sessions)} session(s), {len(self.clips)} clip(s). All files verified on disk."
            )

    # ------------------------------------------------------------------
    # Separation actions
    # ------------------------------------------------------------------

    def _run_dry_run(self):
        with self.preserve_scroll():
            if not self._gate_or_warn():
                return
            paths = self._paths()
            data_dir = paths["data"]

            # Inline status — no popup — if data\ is simply not there yet.
            if not os.path.isdir(data_dir):
                self.sep_status_var.set(
                    f"data\\ folder not found: {data_dir} — place media there first."
                )
                return

            try:
                self.current_preview = self.separator.dry_run(data_dir)
                total_to_move = self.current_preview.video_count + self.current_preview.photo_count

                # Zero-file result: report inline, keep Confirm disabled.
                if total_to_move == 0:
                    self.sep_status_var.set(
                        f"Dry-run: nothing to move "
                        f"({self.current_preview.already_separated_count} already separated, "
                        f"{self.current_preview.unknown_count} unknown)."
                    )
                    self.btn_confirm_move.configure(state=tk.DISABLED)
                    messagebox.showinfo(
                        "Dry-Run — Nothing to Move",
                        f"{format_paths_log_header(paths)}\n\n"
                        f"No moveable files found in:\n  {data_dir}\n\n"
                        f"Already separated : {self.current_preview.already_separated_count} file(s)\n"
                        f"Unknown (ignored) : {self.current_preview.unknown_count} file(s)",
                    )
                    return

                report = (
                    f"{format_paths_log_header(paths)}\n"
                    f"{'-'*50}\n"
                    f"DRY-RUN PREVIEW FOR: {data_dir}\n"
                    f"• Videos to move into Videos/ : {self.current_preview.video_count}\n"
                    f"• Photos to move into Photos/ : {self.current_preview.photo_count}\n"
                    f"• Unknown files (stay in data/): {self.current_preview.unknown_count}\n"
                    f"• Potential Name Collisions   : {self.current_preview.collision_count}\n"
                    f"• Files already separated     : {self.current_preview.already_separated_count}\n"
                )
                if self.current_preview.unknown_files:
                    report += "\nUnknown files staying in place:\n  " + "\n  ".join(self.current_preview.unknown_files[:10])
                    if len(self.current_preview.unknown_files) > 10:
                        report += f"\n  ... and {len(self.current_preview.unknown_files) - 10} more."

                if self.current_preview.collisions:
                    report += "\n\nCollisions handled safely (both kept):\n"
                    for c in self.current_preview.collisions:
                        report += f"  {c['original']} -> {c['target_subfolder']}/{c['resolution']}\n"

                report += "\nNothing has moved on disk. Click 'Confirm & Move Files' to proceed."
                messagebox.showinfo("Dry-Run Separation Preview", report)

                # Only enable Confirm when there is actually something to move.
                self.btn_confirm_move.configure(state=tk.NORMAL)
                self.sep_status_var.set(
                    f"Dry-run OK: {self.current_preview.video_count} videos, "
                    f"{self.current_preview.photo_count} photos, {self.current_preview.collision_count} collisions."
                )
            except Exception as e:
                self.sep_status_var.set(f"Dry-run error: {e}")
                messagebox.showerror("Dry Run Error", str(e))

    def _confirm_separation(self):
        with self.preserve_scroll():
            if not self._gate_or_warn():
                return
            paths = self._paths()
            if not self.current_preview:
                return

            confirm = messagebox.askyesno(
                "Confirm Move",
                f"{format_paths_log_header(paths)}\n\n"
                f"Move {self.current_preview.video_count} videos and {self.current_preview.photo_count} photos?\n"
                "This is an instant same-drive move that preserves file timestamps.\n"
                f"A move log will be created in {paths['scribe']}\\move_log.json for undo.",
            )
            if not confirm:
                return

            try:
                res = self.separator.separate(paths["data"], paths["scribe"])
                if res.success:
                    messagebox.showinfo("Separation Success", res.message)
                    self.sep_status_var.set("Separation complete and verified! Move log written.")
                    self.btn_confirm_move.configure(state=tk.DISABLED)
                else:
                    messagebox.showerror("Verification Failed", res.message)
            except Exception as e:
                messagebox.showerror("Separation Error", str(e))

    def _undo_separation(self):
        with self.preserve_scroll():
            paths = self._paths()
            if not paths:
                messagebox.showwarning("Undo", "Select an event folder first.")
                return
            move_log = os.path.join(paths["scribe"], "move_log.json")
            if not os.path.exists(move_log):
                messagebox.showwarning("Undo", f"No move_log.json found in {paths['scribe']}.")
                return

            confirm = messagebox.askyesno(
                "Undo Separation",
                f"{format_paths_log_header(paths)}\n\nRestore all moved media back to their original locations?",
            )
            if not confirm:
                return

            res = self.separator.undo(paths["scribe"])
            if res.success:
                messagebox.showinfo("Undo Complete", res.message)
                self.sep_status_var.set(res.message)
                self.btn_confirm_move.configure(state=tk.DISABLED)
            else:
                messagebox.showerror("Undo Error", res.message)

    # ------------------------------------------------------------------
    # Session detection
    # ------------------------------------------------------------------

    def _run_session_detection(self):
        if not self._gate_or_warn():
            return
        paths = self._paths()
        data_dir  = paths["data"]
        videos_dir = paths["videos"]   # data\Videos\
        photos_dir = paths["photos"]   # data\Photos\

        # Prefer data\Videos\ (post-separation); fall back to flat data\ (pre-separation).
        if os.path.isdir(videos_dir):
            effective_videos_dir = videos_dir
        elif os.path.isdir(data_dir) and self._data_dir_has_videos(data_dir):
            effective_videos_dir = data_dir
        else:
            self.sep_status_var.set(
                "No video files found in data\\ or data\\Videos\\ — "
                "run Dry-Run and Confirm & Move first, or check that your event folder is correct."
            )
            return

        # Prefer data\Photos\ (post-separation); fall back to flat data\ (pre-separation).
        if os.path.isdir(photos_dir):
            effective_photos_dir = photos_dir
        elif os.path.isdir(data_dir):
            effective_photos_dir = data_dir   # photos live here too (flat layout)
        else:
            effective_photos_dir = ""

        self.btn_detect.configure(state=tk.DISABLED)
        self.status_callback("Scanning video clips and reading durations with ffprobe...")

        mapping_dict = self._get_current_mapping_dict()
        default_mode = self.ctx.speaker_mode_var.get()

        threading.Thread(
            target=self._detect_thread_worker,
            args=(effective_videos_dir, effective_photos_dir, mapping_dict, default_mode),
            daemon=True,
        ).start()

    def _detect_thread_worker(self, videos_dir: str, photos_dir: str, mapping_dict: dict, default_mode: str):
        try:
            video_files = [f for f in os.listdir(videos_dir) if self.separator.classify_extension(f) == "video"]
            clips = []
            for f in video_files:
                p = os.path.join(videos_dir, f)
                dt, src = self.time_parser.get_timestamp(p, is_photo=False)
                dur = self.clip_probe.get_duration(p)
                end_dt = dt + timedelta(seconds=dur)
                stem = os.path.splitext(f)[0].upper()
                spk = mapping_dict.get(stem) or mapping_dict.get(f.upper())
                clips.append(
                    ClipInfo(
                        filename=f,
                        filepath=p,
                        start_time=dt,
                        duration=dur,
                        end_time=end_dt,
                        time_source=src,
                        speaker_name=spk,
                    )
                )

            grouper = SessionGrouper(
                gap_threshold_seconds=self.gap_min_var.get() * 60.0,
                photo_grace_seconds=self.grace_min_var.get() * 60.0,
            )
            sessions = grouper.group_clips(
                clips,
                default_speaker_mode=default_mode,
                mapping_dict=mapping_dict,
            )

            photos = []
            if os.path.exists(photos_dir):
                photo_files = [f for f in os.listdir(photos_dir) if self.separator.classify_extension(f) == "photo"]
                for f in photo_files:
                    p = os.path.join(photos_dir, f)
                    dt, src = self.time_parser.get_timestamp(p, is_photo=True)
                    photos.append(
                        PhotoInfo(
                            filename=f,
                            filepath=p,
                            timestamp=dt,
                            time_source=src,
                        )
                    )

            sessions, unassigned = grouper.assign_photos(photos, sessions)

            self.after(0, self._on_detection_complete, sessions, photos, unassigned, clips)
        except Exception as e:
            self.after(0, lambda err=e: messagebox.showerror("Detection Failed", str(err)))
            self.after(0, self._on_context_change)

    def _on_detection_complete(self, sessions, photos, unassigned, clips):
        self.sessions = sessions
        self.photos = photos
        self.unassigned_photos = unassigned
        self.clips = clips
        self._on_context_change()

        self._refresh_tables()

        unnamed_count = sum(1 for s in self.sessions if s.speaker_name.startswith("Unnamed "))
        unnamed_msg = f" (including {unnamed_count} Unnamed)" if unnamed_count > 0 else ""
        msg = (
            f"Detected {len(self.sessions)} sessions{unnamed_msg} across {len(self.clips)} clips. "
            f"Assigned {len(self.photos) - len(self.unassigned_photos)}/{len(self.photos)} photos "
            f"({len(self.unassigned_photos)} unassigned)."
        )
        self.status_callback(msg)
        paths = self._paths()
        banner = format_paths_log_header(paths) + "\n\n" if paths else ""
        messagebox.showinfo("Session Grouping Complete", banner + msg)

    def _on_session_double_click(self, event):
        """Allows in-place editing of speaker name in session table (renaming across all sessions), or toggling Mode."""
        with self.preserve_scroll():
            item_id = self.sess_tree.focus()
            if not item_id:
                return

            col = self.sess_tree.identify_column(event.x)
            if col == "#3":
                # Double click on Mode column toggles mode
                self._toggle_selected_session_mode()
                return

            curr_values = self.sess_tree.item(item_id, "values")
            session_id = int(curr_values[0])
            old_name = curr_values[1]

            prompt_win = tk.Toplevel(self)
            prompt_win.title(f"Rename Speaker ({old_name})")
            prompt_win.minsize(320, 150)
            prompt_win.resizable(True, True)
            prompt_win.transient(self)
            prompt_win.grab_set()

            def apply_name():
                new_name = entry_var.get().strip() or old_name
                # Rename the person across all of their sessions!
                for s in self.sessions:
                    if s.speaker_name == old_name or s.session_id == session_id:
                        s.speaker_name = new_name
                        for c in s.clips:
                            c.speaker_name = new_name
                        for p in s.photos:
                            p.assigned_speaker = new_name

                # Update mapping text box
                raw_mapping = self.mapping_text.get("1.0", tk.END).strip()
                if old_name in raw_mapping:
                    updated_mapping = raw_mapping.replace(f" - {old_name}", f" - {new_name}")
                    if updated_mapping == raw_mapping:
                        updated_mapping = raw_mapping.replace(old_name, new_name)
                    self.mapping_text.delete("1.0", tk.END)
                    self.mapping_text.insert("1.0", updated_mapping)
                else:
                    # Rebuild mapping text from sessions
                    rebuilt_lines = []
                    for s in self.sessions:
                        if not s.speaker_name.startswith("Unnamed "):
                            clip_names = ", ".join(c.filename for c in s.clips)
                            rebuilt_lines.append(f"{clip_names} - {s.speaker_name}")
                    if rebuilt_lines:
                        self.mapping_text.delete("1.0", tk.END)
                        self.mapping_text.insert("1.0", "\n".join(rebuilt_lines))

                # Update manifest on disk if it already exists
                paths = self._paths()
                if paths and os.path.isfile(paths["manifest"]):
                    self._save_manifest_quiet()

                self._refresh_tables()
                prompt_win.destroy()

            btn_box = ttk.Frame(prompt_win)
            btn_box.pack(fill=tk.X, side=tk.BOTTOM, pady=8, padx=10)
            ttk.Button(btn_box, text="Save", command=apply_name).pack(side=tk.RIGHT, padx=4)
            ttk.Button(btn_box, text="Cancel", command=prompt_win.destroy).pack(side=tk.RIGHT)

            ttk.Label(prompt_win, text=f"Enter Speaker Name for '{old_name}':").pack(pady=(12, 4), padx=10)
            entry_var = tk.StringVar(value=old_name)
            entry = ttk.Entry(prompt_win, textvariable=entry_var, width=32)
            entry.pack(pady=4, padx=10, fill=tk.X)
            entry.focus()
            entry.bind("<Return>", lambda _e: apply_name())
            prompt_win.bind("<Escape>", lambda _e: prompt_win.destroy())

            theme.center_dialog(prompt_win, self, width=380, height=170, min_width=320, min_height=150)
            prompt_win.wait_window()

    def _save_manifest_quiet(self):
        """Silently save updated manifest without showing dialog."""
        paths = self._paths()
        if not paths:
            return
        event = EventDetails(
            event_name=self.ctx.event_name_var.get().strip(),
            date=self.ctx.event_date_var.get().strip(),
            organiser=self.ctx.organiser_var.get().strip(),
            venue=self.ctx.venue_var.get().strip(),
            sponsors=self.ctx.sponsors_var.get().strip(),
            speaker_mode=self.ctx.speaker_mode_var.get(),
            event_id=self.ctx.event_id_var.get().strip() or None,
        )
        settings = {
            "session_gap_threshold_seconds": self.gap_min_var.get() * 60.0,
            "photo_grace_seconds": self.grace_min_var.get() * 60.0,
        }
        try:
            ManifestManager.save_manifest(
                scribe_dir=paths["scribe"],
                event=event,
                sessions=self.sessions,
                unassigned_photos=self.unassigned_photos,
                settings_used=settings,
                event_root=paths["event_root"],
            )
            self.ctx.notify_manifest_saved()
        except Exception:
            pass

    def _open_roster_dialog(self):
        """Open the speaker roster dialog for the selected session."""
        item_id = self.sess_tree.focus()
        if not item_id:
            children = self.sess_tree.get_children()
            if children:
                item_id = children[0]
                self.sess_tree.focus(item_id)
                self.sess_tree.selection_set(item_id)
            else:
                messagebox.showwarning("No Sessions", "No sessions available. Run Detect Sessions first.", parent=self)
                return

        curr_values = self.sess_tree.item(item_id, "values")
        session_id = int(curr_values[0])
        target_sess = None
        for s in self.sessions:
            if s.session_id == session_id:
                target_sess = s
                break
        if not target_sess:
            return

        from .roster_dialog import RosterDialog
        event_root = self.ctx.event_dir_var.get().strip()
        available_clips = [c.to_dict() for c in target_sess.clips]

        def on_roster_saved(new_roster):
            target_sess.roster = new_roster
            if len(new_roster) > 1:
                target_sess.speaker_mode = "panel"
            self._refresh_session_table_item(item_id, target_sess)

        RosterDialog(
            parent=self,
            session_id=session_id,
            speaker_name=target_sess.speaker_name,
            current_roster=getattr(target_sess, "roster", []),
            event_root=event_root,
            available_clips=available_clips,
            on_save_callback=on_roster_saved,
        )

    def _toggle_selected_session_mode(self):
        """Toggle session speaker mode between 'One speaker' and 'Multiple speakers'."""
        item_id = self.sess_tree.focus()
        if not item_id:
            children = self.sess_tree.get_children()
            if children:
                item_id = children[0]
                self.sess_tree.focus(item_id)
                self.sess_tree.selection_set(item_id)
            else:
                return
        curr_values = self.sess_tree.item(item_id, "values")
        session_id = int(curr_values[0])
        for s in self.sessions:
            if s.session_id == session_id:
                curr_mode = getattr(s, "speaker_mode", "single")
                new_mode = "panel" if curr_mode == "single" else "single"
                s.speaker_mode = new_mode
                self._refresh_session_table_item(item_id, s)
                if new_mode == "panel" and not getattr(s, "roster", []):
                    self._open_roster_dialog()
                break

    def _refresh_session_table_item(self, item_id: str, s: SessionInfo):
        st = s.start_time.strftime("%H:%M:%S") if s.start_time else "--"
        et = s.end_time.strftime("%H:%M:%S") if s.end_time else "--"
        mode_label = "Panel Discussion" if (s.session_type == "panel" or getattr(s, "speaker_mode", "single") == "panel") else "Solo Speaker"
        self.sess_tree.item(
            item_id,
            values=(s.session_id, s.speaker_name, mode_label, len(s.clips), len(s.photos), st, et),
        )

    def _open_event_roster_dialog(self) -> None:
        """Opens the Event Roster Dialog to pick people from Vault v2."""
        from scribe_core.vault_v2 import VaultV2StorageEngine
        from scribe_core.event_roster_manager import EventRosterManager, generate_event_id
        from .event_roster_dialog import EventRosterDialog

        storage = VaultV2StorageEngine(self.vault)
        roster_mgr = EventRosterManager(storage)

        event_id = self.ctx.event_id_var.get().strip()
        if not event_id:
            event_id = generate_event_id()
            self.ctx.event_id_var.set(event_id)

        def on_roster_saved(saved_roster):
            self.status_callback(f"Updated event roster ({len(saved_roster)} speakers)")
            self._refresh_tables()
            self._save_manifest_quiet()

        EventRosterDialog(
            parent=self.winfo_toplevel(),
            event_id=event_id,
            roster_mgr=roster_mgr,
            on_save_callback=on_roster_saved,
        )

    def _open_session_config_dialog(self) -> None:
        """Opens SessionConfigDialog for the selected session."""
        sel = self.sess_tree.selection()
        if not sel:
            children = self.sess_tree.get_children()
            if children:
                sel = [children[0]]
            else:
                if not os.environ.get("SESSION_SCRIBE_TEST_MODE"):
                    messagebox.showwarning("No Session", "Please select a session to configure.")
                return

        sess_id = int(sel[0])
        target_sess = next((s for s in self.sessions if s.session_id == sess_id), None)
        if not target_sess:
            return

        from scribe_core.vault_v2 import VaultV2StorageEngine
        from scribe_core.event_roster_manager import EventRosterManager, generate_event_id
        from .session_config_dialog import SessionConfigDialog

        storage = VaultV2StorageEngine(self.vault)
        roster_mgr = EventRosterManager(storage)
        event_id = self.ctx.event_id_var.get().strip() or generate_event_id()
        self.ctx.event_id_var.set(event_id)
        event_roster = roster_mgr.load_event_roster(event_id)

        if not event_roster and not os.environ.get("SESSION_SCRIBE_TEST_MODE"):
            resp = messagebox.askyesno(
                "Event Roster Empty",
                "The Event Roster has no enrolled speakers yet. Would you like to configure the Event Roster first?",
            )
            if resp:
                self._open_event_roster_dialog()
                return

        def on_session_saved(updated_sess: SessionInfo):
            self._refresh_tables()
            self._save_manifest_quiet()

        SessionConfigDialog(
            parent=self.winfo_toplevel(),
            session=target_sess,
            event_roster=event_roster,
            on_save_callback=on_session_saved,
        )

    def _merge_selected_sessions(self) -> None:
        """Manually merges selected sessions into a single session."""
        from scribe_core.session_grouper import merge_sessions

        sel = self.sess_tree.selection()
        if len(sel) < 2:
            if not os.environ.get("SESSION_SCRIBE_TEST_MODE"):
                messagebox.showinfo(
                    "Select Sessions to Merge",
                    "Please select 2 or more sessions in the table (hold Ctrl or Shift to select multiple).",
                )
            return

        selected_ids = [int(i) for i in sel]
        try:
            self.sessions = merge_sessions(self.sessions, selected_ids)
            self._refresh_tables()
            self._save_manifest_quiet()
            self.status_callback(f"Successfully merged sessions {selected_ids}.")
        except Exception as e:
            if not os.environ.get("SESSION_SCRIBE_TEST_MODE"):
                messagebox.showerror("Merge Error", str(e))

    def _split_selected_session(self) -> None:
        """Manually splits the selected session at a clip boundary."""
        from scribe_core.session_grouper import split_session

        sel = self.sess_tree.selection()
        if not sel or len(sel) != 1:
            if not os.environ.get("SESSION_SCRIBE_TEST_MODE"):
                messagebox.showinfo("Select Session to Split", "Please select exactly one session to split.")
            return

        sess_id = int(sel[0])
        target_sess = next((s for s in self.sessions if s.session_id == sess_id), None)
        if not target_sess or len(target_sess.clips) < 2:
            if not os.environ.get("SESSION_SCRIBE_TEST_MODE"):
                messagebox.showwarning(
                    "Cannot Split",
                    "Selected session must contain at least 2 clips in order to split.",
                )
            return

        split_idx = len(target_sess.clips) // 2
        try:
            self.sessions = split_session(self.sessions, sess_id, split_at_clip_index=split_idx)
            self._refresh_tables()
            self._save_manifest_quiet()
            self.status_callback(f"Successfully split session {sess_id} at clip #{split_idx}.")
        except Exception as e:
            if not os.environ.get("SESSION_SCRIBE_TEST_MODE"):
                messagebox.showerror("Split Error", str(e))

    # ------------------------------------------------------------------
    # Save manifest
    # ------------------------------------------------------------------

    def _save_manifest(self):
        if not self._gate_or_warn():
            return
        paths = self._paths()

        event = EventDetails(
            event_name=self.ctx.event_name_var.get().strip(),
            date=self.ctx.event_date_var.get().strip(),
            organiser=self.ctx.organiser_var.get().strip(),
            venue=self.ctx.venue_var.get().strip(),
            sponsors=self.ctx.sponsors_var.get().strip(),
            speaker_mode=self.ctx.speaker_mode_var.get(),
            event_id=self.ctx.event_id_var.get().strip() or None,
        )

        warnings = event.validate()
        if warnings:
            self.warning_label.configure(text="WARNING: " + " | ".join(warnings))
            confirm = messagebox.askyesno(
                "Missing Fields Warning",
                "Validation Warnings:\n"
                + "\n".join(f"• {w}" for w in warnings)
                + "\n\nDo you want to proceed saving manifest anyway?",
            )
            if not confirm:
                return
        else:
            optional = self.ctx.missing_optional_warnings()
            self.warning_label.configure(text=("WARNING: " + " | ".join(optional)) if optional else "")

        if not self.sessions:
            messagebox.showwarning("No Sessions", "Please run 'Detect Sessions & Assign Photos' first.")
            return

        settings = {
            "session_gap_threshold_seconds": self.gap_min_var.get() * 60.0,
            "photo_grace_seconds": self.grace_min_var.get() * 60.0,
        }

        try:
            path, warns = ManifestManager.save_manifest(
                scribe_dir=paths["scribe"],
                event=event,
                sessions=self.sessions,
                unassigned_photos=self.unassigned_photos,
                settings_used=settings,
                event_root=paths["event_root"],
            )
            msg = (
                f"{format_paths_log_header(paths)}\n\n"
                f"Manifest successfully saved to:\n{path}\n\nSummary:\n"
                f"• Event: {event.event_name}\n"
                f"• Sessions: {len(self.sessions)}\n"
                f"• Clips: {sum(len(s.clips) for s in self.sessions)}\n"
                f"• Assigned Photos: {sum(len(s.photos) for s in self.sessions)}\n"
                f"• Unassigned Photos: {len(self.unassigned_photos)}"
            )
            messagebox.showinfo("Manifest Saved", msg)
            self.status_callback(f"Manifest saved to {path}")
            # Notify Stage 3 so it reloads the clip list without a restart.
            self.ctx.notify_manifest_saved()
        except Exception as e:
            messagebox.showerror("Error Saving Manifest", str(e))

    def _on_enroll_voice_profile(self) -> None:
        """Opens voice profile enrollment dialog, strictly checking unlocked vault state."""
        if not self.vault.is_initialized():
            dlg = InitializeVaultDialog(self.winfo_toplevel(), self.vault)
            self.winfo_toplevel().wait_window(dlg)
            if not self.vault.is_unlocked():
                return
        elif not self.vault.is_unlocked():
            messagebox.showinfo(
                "Voice Vault Locked",
                "Voice Vault is locked.\nPlease unlock the vault with your passphrase to enroll or manage speaker voice profiles.",
                parent=self.winfo_toplevel(),
            )
            dlg = UnlockVaultDialog(self.winfo_toplevel(), self.vault)
            self.winfo_toplevel().wait_window(dlg)
            if not self.vault.is_unlocked():
                return

        storage = VaultV2StorageEngine(self.vault)
        mgr = EnrollmentManager(storage)
        dlg = VoiceProfileEnrollmentDialog(self.winfo_toplevel(), mgr)
        self.winfo_toplevel().wait_window(dlg)
