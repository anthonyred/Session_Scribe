"""
gui/roster_dialog.py

Session Roster and Voice Sample Management Dialog for Multi-Speaker Sessions.
Allows configuring participants, their roles (Host, Speaker, Panelist, Moderator, Guest),
and optional voice samples extracted from event clips or audio files.

STRICT PRIVACY GUARANTEE:
Voice samples and biometric data are stored strictly locally in <event root>\\scribe\\voice_samples\\.
They are NEVER uploaded or transmitted to any external server or cloud service.
"""

from __future__ import annotations

import os
import shutil
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from typing import Any, Callable, Dict, List, Optional

from scribe_core.multi_speaker import (
    ALLOWED_ROLES,
    BIOMETRIC_PRIVACY_NOTICE,
    RosterMember,
    extract_voice_sample,
    play_voice_sample,
    stop_voice_sample,
    sanitize_filename,
)
from . import theme


class RosterDialog(tk.Toplevel):
    """Dialog to manage session speaker roster and voice samples."""

    def __init__(
        self,
        parent: tk.Misc,
        session_id: int,
        speaker_name: str,
        current_roster: List[Dict[str, Any]],
        event_root: str,
        available_clips: List[Dict[str, Any]],
        on_save_callback: Callable[[List[Dict[str, Any]]], None],
    ):
        super().__init__(parent)
        self.session_id = session_id
        self.speaker_name = speaker_name
        self.event_root = event_root
        self.available_clips = available_clips
        self.on_save_callback = on_save_callback

        self.roster_members: List[Dict[str, Any]] = [
            dict(m) for m in current_roster
        ]
        # If roster is empty, pre-populate with the session speaker name
        if not self.roster_members and speaker_name.strip():
            self.roster_members.append({
                "name": speaker_name.strip(),
                "role": "Host" if "panel" in speaker_name.lower() else "Speaker",
                "sample_path": None,
            })

        self.title(f"Speaker Roster — Session {session_id} ({speaker_name})")
        self.minsize(620, 520)
        self.resizable(True, True)
        self.configure(bg=theme.COLOR_APP_BG)
        self.transient(parent)
        self.grab_set()

        self._build_ui()
        self._refresh_table()
        self.bind("<Configure>", self._on_dialog_configure)
        self.bind("<Escape>", lambda _e: self.destroy())
        theme.center_dialog(self, parent, width=760, height=640, min_width=620, min_height=520)

    def _on_dialog_configure(self, event) -> None:
        if event.widget == self:
            avail = max(200, event.width - 64)
            if hasattr(self, "lbl_explanation") and self.lbl_explanation.winfo_exists():
                self.lbl_explanation.configure(wraplength=avail)
            if hasattr(self, "_canvas") and hasattr(self, "_win_id"):
                self._canvas.itemconfig(self._win_id, width=max(200, event.width - 24))

    def _build_ui(self):
        # Bottom Dialog buttons (pinned to bottom outside canvas so they never clip or scroll away)
        dlg_btn_row = ttk.Frame(self, padding=(16, 8, 16, 12))
        dlg_btn_row.pack(side=tk.BOTTOM, fill=tk.X)

        ttk.Button(dlg_btn_row, text="Cancel", command=self.destroy).pack(side=tk.LEFT)
        ttk.Button(dlg_btn_row, text="Save Roster", style="Primary.TButton", command=self._save_and_close).pack(side=tk.RIGHT)

        # Canvas and scrollbar wrapper to guarantee small monitors never clip
        self._canvas = tk.Canvas(self, bg=theme.COLOR_APP_BG, highlightthickness=0)
        self._vscroll = ttk.Scrollbar(self, orient=tk.VERTICAL, command=self._canvas.yview)
        self._canvas.configure(yscrollcommand=self._vscroll.set)
        self._vscroll.pack(side=tk.RIGHT, fill=tk.Y)
        self._canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        container = tk.Frame(self._canvas, bg=theme.COLOR_APP_BG, padx=16, pady=12)
        self._win_id = self._canvas.create_window((0, 0), window=container, anchor="nw")

        def _on_canvas_resize(event):
            self._canvas.itemconfig(self._win_id, width=event.width)
        self._canvas.bind("<Configure>", _on_canvas_resize)

        def _on_container_resize(_event):
            self._canvas.configure(scrollregion=self._canvas.bbox("all"))
        container.bind("<Configure>", _on_container_resize)

        # Attach universal scroller (smooth touchpad deltas, Shift-wheel, keyboard nav, touch/drag pan)
        from gui.scrollable_tab import attach_universal_scrolling
        self._scroller = attach_universal_scrolling(
            canvas=self._canvas,
            content_widget=container,
            toplevel=self,
            enable_drag_pan=True,
        )

        # Header section
        header_card = tk.Frame(
            container,
            bg=theme.COLOR_CARD_BG,
            highlightbackground=theme.COLOR_BORDER,
            highlightthickness=1,
            padx=14,
            pady=10,
        )
        header_card.pack(fill=tk.X, pady=(0, 8))

        tk.Label(
            header_card,
            text=f"Session {self.session_id} Speaker Roster",
            font="SS_Section",
            bg=theme.COLOR_CARD_BG,
            fg=theme.COLOR_HEADER_NAVY,
        ).pack(anchor=tk.W)

        self.lbl_explanation = tk.Label(
            header_card,
            text="Assign names and roles for multi-speaker attribution. Optional voice samples enable local speaker matching.",
            font="SS_Body",
            bg=theme.COLOR_CARD_BG,
            fg=theme.COLOR_TEXT_SECONDARY,
            wraplength=640,
            justify=tk.LEFT,
        )
        self.lbl_explanation.pack(anchor=tk.W, fill=tk.X, expand=True, pady=(2, 0))

        # Main table card
        table_card = tk.Frame(
            container,
            bg=theme.COLOR_CARD_BG,
            highlightbackground=theme.COLOR_BORDER,
            highlightthickness=1,
            padx=12,
            pady=10,
        )
        table_card.pack(fill=tk.BOTH, expand=True, pady=(0, 8))

        # Table and scrollbar
        tree_frame = ttk.Frame(table_card)
        tree_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 8))

        self.tree = ttk.Treeview(
            tree_frame,
            columns=("name", "role", "sample"),
            show="headings",
            selectmode="browse",
            height=6,
        )
        self.tree.heading("name", text="Speaker Name", anchor=tk.W)
        self.tree.heading("role", text="Role", anchor=tk.W)
        self.tree.heading("sample", text="Voice Sample Status", anchor=tk.W)

        self.tree.column("name", width=200, minwidth=140)
        self.tree.column("role", width=120, minwidth=100)
        self.tree.column("sample", width=220, minwidth=150)

        tree_scroll = ttk.Scrollbar(tree_frame, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=tree_scroll.set)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        tree_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree.bind("<<TreeviewSelect>>", self._on_select_row)

        # Action toolbar for members
        btn_row = ttk.Frame(table_card)
        btn_row.pack(fill=tk.X, pady=(0, 8))

        ttk.Button(btn_row, text="Add Speaker", command=self._add_speaker_prompt).pack(side=tk.LEFT, padx=(0, 4))
        self.btn_edit_role = ttk.Button(btn_row, text="Change Role", command=self._edit_role_prompt, state=tk.DISABLED)
        self.btn_edit_role.pack(side=tk.LEFT, padx=4)
        self.btn_remove = ttk.Button(btn_row, text="Remove", command=self._remove_speaker, state=tk.DISABLED)
        self.btn_remove.pack(side=tk.LEFT, padx=4)

        # Voice sample management row
        sample_card = ttk.LabelFrame(table_card, text=" Voice Sample Tools ", padding=8)
        sample_card.pack(fill=tk.X, pady=(4, 0))

        sample_btn_row = ttk.Frame(sample_card)
        sample_btn_row.pack(fill=tk.X, pady=2)

        self.btn_import_sample = ttk.Button(
            sample_btn_row, text="Upload Audio / WAV...", command=self._upload_sample_file, state=tk.DISABLED
        )
        self.btn_import_sample.pack(side=tk.LEFT, padx=(0, 4))

        self.btn_extract_clip = ttk.Button(
            sample_btn_row, text="Extract from Event Clip...", command=self._open_extract_dialog, state=tk.DISABLED
        )
        self.btn_extract_clip.pack(side=tk.LEFT, padx=4)

        self.btn_play_sample = ttk.Button(
            sample_btn_row, text="▶ Play Sample", command=self._play_sample, state=tk.DISABLED
        )
        self.btn_play_sample.pack(side=tk.LEFT, padx=4)

        self.btn_stop_sample = ttk.Button(
            sample_btn_row, text="■ Stop", command=stop_voice_sample
        )
        self.btn_stop_sample.pack(side=tk.LEFT, padx=2)

        # Privacy Notice Banner
        privacy_bar = theme.MessageBar(
            container,
            text=BIOMETRIC_PRIVACY_NOTICE,
            kind="info",
        )
        privacy_bar.pack(fill=tk.X, pady=(0, 8))

    def _refresh_table(self):
        self.tree.delete(*self.tree.get_children())
        for idx, m in enumerate(self.roster_members):
            sample_disp = "None"
            spath = m.get("sample_path")
            if spath and os.path.isfile(spath):
                sample_disp = f"✓ {os.path.basename(spath)}"
            self.tree.insert("", tk.END, iid=str(idx), values=(m["name"], m.get("role", "Speaker"), sample_disp))

        self._on_select_row(None)

    def _on_select_row(self, _event):
        sel = self.tree.selection()
        if sel:
            idx = int(sel[0])
            m = self.roster_members[idx]
            self.btn_edit_role.configure(state=tk.NORMAL)
            self.btn_remove.configure(state=tk.NORMAL)
            self.btn_import_sample.configure(state=tk.NORMAL)
            self.btn_extract_clip.configure(state=tk.NORMAL)

            spath = m.get("sample_path")
            if spath and os.path.isfile(spath):
                self.btn_play_sample.configure(state=tk.NORMAL)
            else:
                self.btn_play_sample.configure(state=tk.DISABLED)
        else:
            self.btn_edit_role.configure(state=tk.DISABLED)
            self.btn_remove.configure(state=tk.DISABLED)
            self.btn_import_sample.configure(state=tk.DISABLED)
            self.btn_extract_clip.configure(state=tk.DISABLED)
            self.btn_play_sample.configure(state=tk.DISABLED)

    def _add_speaker_prompt(self):
        d = tk.Toplevel(self)
        d.title("Add Speaker")
        d.geometry("420x220")
        d.minsize(360, 200)
        d.resizable(True, True)
        d.configure(bg=theme.COLOR_APP_BG)
        d.transient(self)
        d.grab_set()

        f = tk.Frame(d, bg=theme.COLOR_CARD_BG, padx=16, pady=14, highlightbackground=theme.COLOR_BORDER, highlightthickness=1)
        f.pack(fill=tk.BOTH, expand=True, padx=12, pady=12)

        tk.Label(f, text="Speaker Name:", font="SS_Body", bg=theme.COLOR_CARD_BG).pack(anchor=tk.W)
        name_var = tk.StringVar()
        name_entry = ttk.Entry(f, textvariable=name_var, width=32)
        name_entry.pack(fill=tk.X, pady=(2, 8))
        name_entry.focus_set()

        tk.Label(f, text="Role:", font="SS_Body", bg=theme.COLOR_CARD_BG).pack(anchor=tk.W)
        role_var = tk.StringVar(value="Speaker")
        role_cb = ttk.Combobox(f, textvariable=role_var, values=ALLOWED_ROLES, state="readonly", width=30)
        role_cb.pack(fill=tk.X, pady=(2, 10))

        def on_add():
            nm = name_var.get().strip()
            if not nm:
                messagebox.showerror("Error", "Speaker name cannot be empty.", parent=d)
                return
            self.roster_members.append({"name": nm, "role": role_var.get(), "sample_path": None})
            d.destroy()
            self._refresh_table()

        b_row = ttk.Frame(f)
        b_row.pack(fill=tk.X)
        ttk.Button(b_row, text="Cancel", command=d.destroy).pack(side=tk.LEFT)
        ttk.Button(b_row, text="Add", style="Primary.TButton", command=on_add).pack(side=tk.RIGHT)

    def _edit_role_prompt(self):
        sel = self.tree.selection()
        if not sel:
            return
        idx = int(sel[0])
        m = self.roster_members[idx]

        d = tk.Toplevel(self)
        d.title(f"Change Role — {m['name']}")
        d.geometry("380x180")
        d.minsize(340, 160)
        d.resizable(True, True)
        d.configure(bg=theme.COLOR_APP_BG)
        d.transient(self)
        d.grab_set()

        f = tk.Frame(d, bg=theme.COLOR_CARD_BG, padx=16, pady=14, highlightbackground=theme.COLOR_BORDER, highlightthickness=1)
        f.pack(fill=tk.BOTH, expand=True, padx=12, pady=12)

        tk.Label(f, text="Select Role:", font="SS_Body", bg=theme.COLOR_CARD_BG).pack(anchor=tk.W)
        role_var = tk.StringVar(value=m.get("role", "Speaker"))
        role_cb = ttk.Combobox(f, textvariable=role_var, values=ALLOWED_ROLES, state="readonly", width=28)
        role_cb.pack(fill=tk.X, pady=(4, 10))

        def on_save():
            m["role"] = role_var.get()
            d.destroy()
            self._refresh_table()

        b_row = ttk.Frame(f)
        b_row.pack(fill=tk.X)
        ttk.Button(b_row, text="Cancel", command=d.destroy).pack(side=tk.LEFT)
        ttk.Button(b_row, text="Save", style="Primary.TButton", command=on_save).pack(side=tk.RIGHT)

    def _remove_speaker(self):
        sel = self.tree.selection()
        if not sel:
            return
        idx = int(sel[0])
        del self.roster_members[idx]
        self._refresh_table()

    def _upload_sample_file(self):
        sel = self.tree.selection()
        if not sel:
            return
        idx = int(sel[0])
        m = self.roster_members[idx]

        file_path = filedialog.askopenfilename(
            parent=self,
            title=f"Select Voice Sample for {m['name']}",
            filetypes=[("Audio / Video Files", "*.wav;*.mp3;*.m4a;*.mp4;*.mkv"), ("All Files", "*.*")],
        )
        if not file_path:
            return

        samples_dir = os.path.join(self.event_root, "scribe", "voice_samples")
        os.makedirs(samples_dir, exist_ok=True)
        out_wav = os.path.join(samples_dir, f"{sanitize_filename(m['name'])}.wav")

        try:
            extract_voice_sample(file_path, out_wav, start_sec=0.0, duration_sec=15.0)
            m["sample_path"] = out_wav
            messagebox.showinfo("Success", f"Voice sample for '{m['name']}' saved locally to:\n{out_wav}", parent=self)
            self._refresh_table()
        except Exception as exc:
            messagebox.showerror("Extraction Error", f"Failed to extract sample:\n{exc}", parent=self)

    def _open_extract_dialog(self):
        sel = self.tree.selection()
        if not sel:
            return
        idx = int(sel[0])
        m = self.roster_members[idx]

        if not self.available_clips:
            messagebox.showwarning("No Clips", "No media clips found in this session to extract from.", parent=self)
            return

        d = tk.Toplevel(self)
        d.title(f"Extract Sample from Clip — {m['name']}")
        d.geometry("480x320")
        d.minsize(420, 280)
        d.resizable(True, True)
        d.configure(bg=theme.COLOR_APP_BG)
        d.transient(self)
        d.grab_set()

        f = tk.Frame(d, bg=theme.COLOR_CARD_BG, padx=16, pady=14, highlightbackground=theme.COLOR_BORDER, highlightthickness=1)
        f.pack(fill=tk.BOTH, expand=True, padx=12, pady=12)

        tk.Label(f, text="Select Video Clip:", font="SS_Body", bg=theme.COLOR_CARD_BG).pack(anchor=tk.W)
        clip_names = [c.get("name", "") for c in self.available_clips]
        clip_var = tk.StringVar(value=clip_names[0] if clip_names else "")
        clip_cb = ttk.Combobox(f, textvariable=clip_var, values=clip_names, state="readonly", width=36)
        clip_cb.pack(fill=tk.X, pady=(2, 8))

        tk.Label(f, text="Start Offset (seconds):", font="SS_Body", bg=theme.COLOR_CARD_BG).pack(anchor=tk.W)
        start_var = tk.DoubleVar(value=5.0)
        start_sb = ttk.Spinbox(f, from_=0.0, to=3600.0, increment=1.0, textvariable=start_var, width=10)
        start_sb.pack(anchor=tk.W, pady=(2, 8))

        lbl_dur = tk.Label(f, text="Duration (seconds, 5-15s recommended):", font="SS_Body", bg=theme.COLOR_CARD_BG, wraplength=420, justify=tk.LEFT)
        lbl_dur.pack(anchor=tk.W, pady=(2, 4))
        dur_var = tk.DoubleVar(value=10.0)
        dur_sb = ttk.Spinbox(f, from_=3.0, to=30.0, increment=1.0, textvariable=dur_var, width=10)
        dur_sb.pack(anchor=tk.W, pady=(2, 12))

        def _on_extract_cfg(e):
            if e.widget == d:
                lbl_dur.configure(wraplength=max(200, e.width - 40))
        d.bind("<Configure>", _on_extract_cfg)

        def on_extract():
            chosen_clip = clip_var.get()
            target_path = ""
            for c in self.available_clips:
                if c.get("name") == chosen_clip:
                    target_path = c.get("path", "")
                    break
            if not target_path or not os.path.isfile(target_path):
                messagebox.showerror("Error", f"Clip file not found: {target_path}", parent=d)
                return

            samples_dir = os.path.join(self.event_root, "scribe", "voice_samples")
            os.makedirs(samples_dir, exist_ok=True)
            out_wav = os.path.join(samples_dir, f"{sanitize_filename(m['name'])}.wav")

            try:
                extract_voice_sample(target_path, out_wav, start_sec=start_var.get(), duration_sec=dur_var.get())
                m["sample_path"] = out_wav
                d.destroy()
                messagebox.showinfo("Success", f"Voice sample for '{m['name']}' extracted successfully:\n{out_wav}", parent=self)
                self._refresh_table()
            except Exception as exc:
                messagebox.showerror("Extraction Error", f"Failed to extract sample:\n{exc}", parent=d)

        b_row = ttk.Frame(f)
        b_row.pack(fill=tk.X)
        ttk.Button(b_row, text="Cancel", command=d.destroy).pack(side=tk.LEFT)
        ttk.Button(b_row, text="Extract Sample", style="Primary.TButton", command=on_extract).pack(side=tk.RIGHT)

    def _play_sample(self):
        sel = self.tree.selection()
        if not sel:
            return
        idx = int(sel[0])
        m = self.roster_members[idx]
        spath = m.get("sample_path")
        if spath and os.path.isfile(spath):
            try:
                play_voice_sample(spath)
            except Exception as exc:
                messagebox.showerror("Playback Error", f"Could not play audio:\n{exc}", parent=self)

    def _save_and_close(self):
        stop_voice_sample()
        self.on_save_callback(self.roster_members)
        self.destroy()

