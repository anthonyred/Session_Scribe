"""
gui/reidentify_dialog.py

Modal dialog for inspecting unidentified speaker clusters ("Who is this?" flow):
- Displays unidentified clusters from the session.
- Allows listening to the clean in-memory representative snippet (zero disk write).
- Allows assigning a roster member or enrolling a new voice profile with consent.
- Re-identifies the session in place with ZERO re-transcription.

Strictly styled with gui.theme tokens.
"""

from __future__ import annotations

import io
import os
import sys
import tempfile
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Any, Callable, Dict, List, Optional

from gui import theme
from scribe_core.identification_engine import SpeakerCluster, AlignedSegment


class ReidentifyDialog(tk.Toplevel):
    """
    Modal interface for reviewing unidentified clusters and triggering re-identification.
    """

    def __init__(
        self,
        parent: tk.Misc,
        clusters: List[SpeakerCluster],
        event_roster: List[Dict[str, Any]],
        on_apply_callback: Optional[Callable[[Dict[str, str]], None]] = None,
        on_enroll_callback: Optional[Callable[[bytes], None]] = None,
        has_acoustic_data: bool = True,
    ):
        super().__init__(parent)
        self.title("Speaker Identification Review — \"Who is this?\"")
        self.clusters = [c for c in clusters if c.is_unidentified]
        self.event_roster = event_roster
        self.on_apply_callback = on_apply_callback
        self.on_enroll_callback = on_enroll_callback
        self.has_acoustic_data = has_acoustic_data

        self.assignments: Dict[str, tk.StringVar] = {}
        self._playing = False

        self.withdraw()
        theme.style_toplevel(self)
        self.minsize(600, 440)
        self.resizable(True, True)

        self._build_ui()
        self.deiconify()
        self.transient(parent)
        self.grab_set()
        self.bind("<Escape>", lambda _e: self.destroy())
        theme.center_dialog(self, parent=parent, width=720, height=520, min_width=600, min_height=440)

    def _build_ui(self) -> None:
        self.configure(bg=theme.COLOR_APP_BG)
        container = ttk.Frame(self, padding=16)
        container.pack(fill=tk.BOTH, expand=True)

        # Footer actions (packed first at bottom to ensure visibility)
        footer = ttk.Frame(container)
        footer.pack(fill=tk.X, side="bottom", pady=(10, 0))

        btn_cancel = ttk.Button(footer, text="Cancel", command=self.destroy)
        btn_cancel.pack(side=tk.RIGHT, padx=(6, 0))

        btn_apply = ttk.Button(
            footer,
            text="Apply & Re-Identify",
            style="Accent.TButton",
            command=self._apply_reidentification,
        )
        btn_apply.pack(side=tk.RIGHT)

        header_lbl = ttk.Label(
            container,
            text="Unidentified Speaker Review",
            font="SS_Title",
            foreground=theme.COLOR_TEXT_PRIMARY,
        )
        header_lbl.pack(anchor=tk.W, pady=(0, 4))

        desc_lbl = ttk.Label(
            container,
            text=(
                "The following speaker clusters could not be confidently attributed to the roster.\n"
                "Listen to the in-memory sample snippet and assign an alias, or enroll the speaker as a new profile."
            ),
            font="SS_Caption",
            foreground=theme.COLOR_TEXT_SECONDARY,
            wraplength=680,
        )
        desc_lbl.pack(anchor=tk.W, pady=(0, 10))

        if not self.has_acoustic_data:
            notice_bg = "#fff4ce" if theme.CURRENT_THEME == "light" else "#3b2e1e"
            notice_fg = "#795548" if theme.CURRENT_THEME == "light" else "#ffd599"
            notice_frame = tk.Frame(container, bg=notice_bg, padx=10, pady=8)
            notice_frame.pack(fill=tk.X, pady=(0, 10))
            tk.Label(
                notice_frame,
                text="Notice: Automatic matching is not available for older events without saved voice cluster data. You can manually assign speaker aliases below.",
                font="SS_Caption",
                fg=notice_fg,
                bg=notice_bg,
                wraplength=660,
                justify=tk.LEFT,
            ).pack(anchor=tk.W)

        # Clusters frame
        list_card = ttk.Frame(container, style="Card.TFrame", padding=12)
        list_card.pack(fill=tk.BOTH, expand=True, pady=(0, 12))

        if not self.clusters:
            empty_lbl = ttk.Label(
                list_card,
                text="✓ All speech clusters in this session are confidently identified.",
                font="SS_Body",
                foreground=theme.COLOR_SUCCESS_GREEN if hasattr(theme, "COLOR_SUCCESS_GREEN") else "#107c10",
            )
            empty_lbl.pack(pady=24)
        else:
            # Table-like list of unidentified clusters
            canvas = tk.Canvas(list_card, highlightthickness=0, bg=theme.COLOR_CARD_BG)
            scrollbar = ttk.Scrollbar(list_card, orient=tk.VERTICAL, command=canvas.yview)
            inner_frame = ttk.Frame(canvas, style="Card.TFrame")

            inner_frame.bind(
                "<Configure>",
                lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
            )
            canvas.create_window((0, 0), window=inner_frame, anchor="nw")
            canvas.configure(xscrollcommand=scrollbar.set)

            canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
            scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

            roster_choices = ["[Unidentified]", "[Audience]"]
            for m in self.event_roster:
                alias = m.get("alias", "")
                role = m.get("role", "Speaker")
                if alias:
                    roster_choices.append(f"{alias} ({role})")

            for idx, c in enumerate(self.clusters):
                row = ttk.Frame(inner_frame, padding=8)
                row.pack(fill=tk.X, expand=True, pady=4)

                info_text = f"Cluster {c.cluster_id}: {len(c.turn_intervals)} turn(s)"
                if c.confidence > 0:
                    info_text += f" (Best candidate sim: {c.confidence:.2f})"
                lbl = ttk.Label(
                    row,
                    text=info_text,
                    font="SS_BodyBold",
                    foreground=theme.COLOR_TEXT_PRIMARY,
                )
                lbl.pack(side=tk.LEFT, padx=(0, 12))

                # Play button
                has_snippet = c.representative_snippet is not None
                btn_play = ttk.Button(
                    row,
                    text="▶ Listen to Snippet",
                    command=lambda cl=c: self._play_snippet(cl),
                    state=tk.NORMAL if has_snippet else tk.DISABLED,
                )
                btn_play.pack(side=tk.LEFT, padx=(0, 8))

                # Enroll button
                if self.on_enroll_callback:
                    btn_enrol = ttk.Button(
                        row,
                        text="Enroll Profile…",
                        command=lambda cl=c: self._enroll_snippet(cl),
                        state=tk.NORMAL if has_snippet else tk.DISABLED,
                    )
                    btn_enrol.pack(side=tk.LEFT, padx=(0, 12))

                # Assign dropdown
                var = tk.StringVar(value=c.matched_alias)
                self.assignments[c.cluster_id] = var
                cb = ttk.Combobox(row, textvariable=var, values=roster_choices, state="readonly", width=18)
                cb.pack(side=tk.RIGHT)
                ttk.Label(row, text="Attribution:", font="SS_Caption").pack(side=tk.RIGHT, padx=4)

    def _play_snippet(self, cluster: SpeakerCluster) -> None:
        """Play WAV snippet using Windows winsound asynchronously."""
        if not cluster.representative_snippet:
            return
        if sys.platform == "win32":
            import winsound
            # Winsound SND_MEMORY requires bytes directly in Python 3
            try:
                winsound.PlaySound(cluster.representative_snippet, winsound.SND_MEMORY | winsound.SND_ASYNC)
            except Exception as e:
                messagebox.showwarning("Audio Playback", f"Could not play snippet: {e}", parent=self)

    def _enroll_snippet(self, cluster: SpeakerCluster) -> None:
        """Trigger enrollment callback with the representative snippet."""
        if self.on_enroll_callback and cluster.representative_snippet:
            self.on_enroll_callback(cluster.representative_snippet)

    def _apply_reidentification(self) -> None:
        """Collect cluster attributions and invoke callback."""
        resolved: Dict[str, str] = {}
        for c_id, var in self.assignments.items():
            val = var.get().strip()
            # Extract alias from "S01 (Host)" -> "S01"
            alias = val.split(" ")[0].strip("()")
            resolved[c_id] = alias

        if self.on_apply_callback:
            self.on_apply_callback(resolved)

        self.destroy()
