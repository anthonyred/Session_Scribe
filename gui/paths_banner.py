"""Resolved-path banner shown on Stage 2 and Stage 3 before any run.

Uses a fixed-height (4 lines) read-only tk.Text widget so the banner never
grows beyond its allocated space and never pushes buttons off-screen.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from scribe_core.event_paths import derive_event_paths, format_paths_banner

from .event_context import EventContext
from . import theme


class PathsBanner(ttk.LabelFrame):
    """Compact 4-line read-only display of the derived event paths styled as an elevated monospace code panel."""

    def __init__(self, parent, ctx: EventContext):
        super().__init__(parent, text=" Resolved paths ", padding=(8, 4))
        self.ctx = ctx

        # Fixed height=4 lines — never grows, never clips buttons below it.
        # Styled as a modern elevated code panel with subtle 1px border.
        self.body = tk.Text(
            self,
            height=4,
            font="SS_Code",
            wrap=tk.NONE,          # paths are single lines; no word-wrap
            state=tk.DISABLED,
            relief=tk.FLAT,
            borderwidth=0,
            highlightthickness=1,
            highlightbackground=theme.COLOR_BORDER,
            foreground=theme.COLOR_TEXT_PRIMARY,
            background=theme.COLOR_INPUT_BG,
            padx=6,
            pady=4,
            cursor="arrow",        # no text-cursor pointer
            takefocus=False,
        )
        self.body.pack(fill=tk.X, expand=True)

        ctx.bind_change(self.refresh)
        self.refresh()

    def refresh_theme(self) -> None:
        """Update colors to match the active design tokens."""
        if hasattr(self, "body"):
            self.body.configure(
                background=theme.COLOR_INPUT_BG,
                foreground=theme.COLOR_TEXT_PRIMARY,
                highlightbackground=theme.COLOR_BORDER,
                font="SS_Code",
            )

    def _set_text(self, text: str) -> None:
        self.body.configure(state=tk.NORMAL)
        self.body.delete("1.0", tk.END)
        self.body.insert("1.0", text)
        self.body.configure(state=tk.DISABLED)

    def refresh(self) -> None:
        root = self.ctx.event_dir_var.get().strip()
        if not root:
            self._set_text("Select an event folder to resolve paths.")
            return
        self._set_text(format_paths_banner(derive_event_paths(root)))
