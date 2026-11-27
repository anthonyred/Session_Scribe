"""
gui/session_config_dialog.py
Dialog for configuring Session Structure (Solo vs Panel) and Speaker Assignment.

Security & Architectural Invariants:
1. Solo Sessions have exactly ONE expected speaker from the Event Roster.
2. Panel Sessions have exactly ONE host and one or more panelists from the Event Roster.
3. Roster members reference per-event aliases (S01, S02, ...) mapped in Vault v2.
4. Names are visible only while unlocked; while locked, only aliases are displayed.
5. Invariant: Dialog builds once, uses theme tokens only, and supports light/dark themes.
"""

from __future__ import annotations

import os
import tkinter as tk
from tkinter import ttk, messagebox
from typing import Any, Callable, Dict, List, Optional

from scribe_core.session_grouper import SessionInfo
from . import theme
from .scrollable_tab import attach_universal_scrolling


class SessionConfigDialog(tk.Toplevel):
    """
    Dialog to configure an individual session's model:
    - Solo (single expected speaker)
    - Panel (1 host + 1 or more panelists)
    """

    def __init__(
        self,
        parent: tk.Misc,
        session: SessionInfo,
        event_roster: List[Dict[str, Any]],
        on_save_callback: Optional[Callable[[SessionInfo], None]] = None,
    ):
        super().__init__(parent)
        self.session = session
        self.event_roster = list(event_roster)
        self.on_save_callback = on_save_callback

        self.title(f"Configure Session {session.session_id} — Session Model")
        self.minsize(560, 480)
        self.resizable(True, True)
        self.configure(bg=theme.COLOR_APP_BG)
        self.transient(parent)
        self.grab_set()

        # State
        self.session_type_var = tk.StringVar(
            value=session.session_type if session.session_type in ("solo", "panel") else "solo"
        )
        self.solo_speaker_var = tk.StringVar()
        self.panel_host_var = tk.StringVar()
        self.panelist_vars: Dict[str, tk.BooleanVar] = {}

        # Prepopulate selections
        if session.session_type == "solo" and session.expected_speaker:
            self.solo_speaker_var.set(session.expected_speaker.get("alias", ""))
        elif session.session_type == "panel":
            if session.host:
                self.panel_host_var.set(session.host.get("alias", ""))
            selected_panelists = {p.get("alias") for p in session.panelists}
            for r in self.event_roster:
                alias = r.get("alias", "")
                self.panelist_vars[alias] = tk.BooleanVar(value=alias in selected_panelists)
        else:
            if self.event_roster:
                self.solo_speaker_var.set(self.event_roster[0].get("alias", ""))
                self.panel_host_var.set(self.event_roster[0].get("alias", ""))

        for r in self.event_roster:
            alias = r.get("alias", "")
            if alias not in self.panelist_vars:
                self.panelist_vars[alias] = tk.BooleanVar(value=False)

        self._build_ui()
        self._on_type_changed()
        self.bind("<Configure>", self._on_configure)
        self.bind("<Escape>", lambda _e: self.destroy())
        theme.center_dialog(self, parent, width=660, height=600, min_width=560, min_height=480)

    def _on_configure(self, event) -> None:
        if event.widget == self:
            avail = max(200, event.width - 48)
            if hasattr(self, "lbl_info") and self.lbl_info.winfo_exists():
                self.lbl_info.configure(wraplength=avail)

    def _build_ui(self) -> None:
        container = ttk.Frame(self, padding=14)
        container.pack(fill=tk.BOTH, expand=True)

        # Footer Buttons (packed first at bottom to ensure visibility)
        footer = ttk.Frame(container)
        footer.pack(fill=tk.X, side="bottom", pady=(10, 0))

        btn_box = ttk.Frame(footer)
        btn_box.pack(side=tk.RIGHT)

        ttk.Button(btn_box, text="Cancel", command=self.destroy).pack(side=tk.RIGHT, padx=4)
        ttk.Button(btn_box, text="Apply Configuration", style="Accent.TButton", command=self._apply_config).pack(
            side=tk.RIGHT, padx=4
        )

        # Header
        lbl_title = ttk.Label(
            container,
            text=f"Session {self.session.session_id} Structure",
            font=("Segoe UI", 12, "bold"),
            foreground=theme.COLOR_HEADER_NAVY,
        )
        lbl_title.pack(anchor=tk.W)

        self.lbl_info = ttk.Label(
            container,
            text=(
                "Select whether this session is a Solo Presentation or a Panel Discussion. "
                "Speakers are picked from the Event Roster and referenced by per-event aliases."
            ),
            font=("Segoe UI", 9),
            wraplength=600,
        )
        self.lbl_info.pack(anchor=tk.W, fill=tk.X, pady=(2, 10))

        # Session Type Selection Card
        type_card = ttk.LabelFrame(container, text=" Session Type ", padding=10)
        type_card.pack(fill=tk.X, pady=(0, 10))

        row_type = ttk.Frame(type_card)
        row_type.pack(fill=tk.X)

        rb_solo = ttk.Radiobutton(
            row_type,
            text="Solo Presentation (Single Expected Speaker)",
            variable=self.session_type_var,
            value="solo",
            command=self._on_type_changed,
        )
        rb_solo.pack(side=tk.LEFT, padx=(0, 20))

        rb_panel = ttk.Radiobutton(
            row_type,
            text="Panel Discussion (Host + Multiple Panelists)",
            variable=self.session_type_var,
            value="panel",
            command=self._on_type_changed,
        )
        rb_panel.pack(side=tk.LEFT)

        # Dynamic Options Area
        self.dynamic_frame = ttk.Frame(container)
        self.dynamic_frame.pack(fill=tk.BOTH, expand=True, pady=4)

        # 1. Solo Frame
        self.frame_solo = ttk.LabelFrame(self.dynamic_frame, text=" Expected Speaker ", padding=10)

        row_solo_picker = ttk.Frame(self.frame_solo)
        row_solo_picker.pack(fill=tk.X, pady=4)

        ttk.Label(row_solo_picker, text="Speaker:").pack(side=tk.LEFT, padx=(0, 8))
        self.combo_solo = ttk.Combobox(row_solo_picker, textvariable=self.solo_speaker_var, state="readonly", width=40)
        self.combo_solo.pack(side=tk.LEFT, fill=tk.X, expand=True)

        # 2. Panel Frame
        self.frame_panel = ttk.LabelFrame(self.dynamic_frame, text=" Host & Panelists Selection ", padding=10)

        row_host = ttk.Frame(self.frame_panel)
        row_host.pack(fill=tk.X, pady=(0, 8))
        ttk.Label(row_host, text="Session Host:", width=14).pack(side=tk.LEFT)
        self.combo_host = ttk.Combobox(row_host, textvariable=self.panel_host_var, state="readonly", width=36)
        self.combo_host.pack(side=tk.LEFT, fill=tk.X, expand=True)

        lbl_panelists = ttk.Label(self.frame_panel, text="Panelists (check all who participate):")
        lbl_panelists.pack(anchor=tk.W, pady=(4, 2))

        # Checklist inside scrollable frame
        panelist_card = ttk.Frame(self.frame_panel, relief=tk.SOLID, borderwidth=1)
        panelist_card.pack(fill=tk.BOTH, expand=True, pady=(2, 6))

        canvas = tk.Canvas(panelist_card, highlightthickness=0, bg=theme.COLOR_APP_BG)
        vscroll = ttk.Scrollbar(panelist_card, orient=tk.VERTICAL, command=canvas.yview)
        canvas.configure(yscrollcommand=vscroll.set)
        vscroll.pack(side=tk.RIGHT, fill=tk.Y)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        check_box_frame = ttk.Frame(canvas)
        win_id = canvas.create_window((0, 0), window=check_box_frame, anchor="nw")

        def _sync_scroll(_event=None):
            canvas.configure(scrollregion=canvas.bbox("all"))
        check_box_frame.bind("<Configure>", _sync_scroll)

        # Populate options
        roster_labels = []
        for r in self.event_roster:
            alias = r.get("alias", "")
            name = r.get("name", "")
            lbl = f"{alias} — {name}" if name else alias
            roster_labels.append(lbl)

            cb = ttk.Checkbutton(
                check_box_frame,
                text=lbl,
                variable=self.panelist_vars[alias],
            )
            cb.pack(anchor=tk.W, padx=8, pady=3)

        self.combo_solo["values"] = roster_labels
        self.combo_host["values"] = roster_labels

        # Match initial selection to label
        for r in self.event_roster:
            alias = r.get("alias", "")
            name = r.get("name", "")
            lbl = f"{alias} — {name}" if name else alias
            if alias == self.solo_speaker_var.get():
                self.combo_solo.set(lbl)
            if alias == self.panel_host_var.get():
                self.combo_host.set(lbl)

    def _on_type_changed(self) -> None:
        stype = self.session_type_var.get()
        if stype == "solo":
            self.frame_panel.pack_forget()
            self.frame_solo.pack(fill=tk.BOTH, expand=True)
        else:
            self.frame_solo.pack_forget()
            self.frame_panel.pack(fill=tk.BOTH, expand=True)

    def _apply_config(self) -> None:
        stype = self.session_type_var.get()

        def extract_alias(raw_val: str) -> str:
            clean = raw_val.strip()
            if " — " in clean:
                return clean.split(" — ")[0].strip()
            return clean

        if stype == "solo":
            raw_sel = self.combo_solo.get()
            alias = extract_alias(raw_sel)
            if not alias:
                if not os.environ.get("SESSION_SCRIBE_TEST_MODE"):
                    messagebox.showwarning("Validation Error", "Please select an expected speaker for this solo session.", parent=self)
                return

            person = next((r for r in self.event_roster if r.get("alias") == alias), None)
            role = person.get("role", "Speaker") if person else "Speaker"
            name = person.get("name") if person else None

            self.session.session_type = "solo"
            self.session.speaker_mode = "single"
            self.session.expected_speaker = {
                "alias": alias,
                "role": role,
                "name": name,
            }
            self.session.host = None
            self.session.panelists = []
            self.session.speaker_name = alias
            self.session.sync_roster_fields()

        else: # panel
            raw_host = self.combo_host.get()
            host_alias = extract_alias(raw_host)
            if not host_alias:
                if not os.environ.get("SESSION_SCRIBE_TEST_MODE"):
                    messagebox.showwarning("Validation Error", "Please select a host for this panel session.", parent=self)
                return

            panelist_aliases = [
                alias for alias, var in self.panelist_vars.items() if var.get() and alias != host_alias
            ]
            if not panelist_aliases:
                if not os.environ.get("SESSION_SCRIBE_TEST_MODE"):
                    messagebox.showwarning("Validation Error", "Please select at least one panelist for this panel session.", parent=self)
                return

            host_person = next((r for r in self.event_roster if r.get("alias") == host_alias), None)
            self.session.session_type = "panel"
            self.session.speaker_mode = "panel"
            self.session.host = {
                "alias": host_alias,
                "role": "Host",
                "name": host_person.get("name") if host_person else None,
            }

            self.session.panelists = []
            for p_alias in panelist_aliases:
                p_person = next((r for r in self.event_roster if r.get("alias") == p_alias), None)
                self.session.panelists.append({
                    "alias": p_alias,
                    "role": p_person.get("role", "Panelist") if p_person else "Panelist",
                    "name": p_person.get("name") if p_person else None,
                })

            self.session.expected_speaker = None
            self.session.speaker_name = f"Panel Session {self.session.session_id}"
            self.session.sync_roster_fields()

        if self.on_save_callback:
            self.on_save_callback(self.session)

        self.destroy()
