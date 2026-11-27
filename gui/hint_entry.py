"""
gui/hint_entry.py

Modern Entry widget that displays an accessible placeholder while bound StringVar is empty.
Styled as a flat input card (one step lighter than card in dark mode), 1px border,
accent-colored focus ring, visible caret, readable placeholder (>= 4.5:1), and ~8x6 inner padding.
"""

from __future__ import annotations

import tkinter as tk
from typing import Optional

from . import theme


class HintEntry(tk.Entry):
    def __init__(self, master, textvariable: tk.StringVar, hint: str, **kwargs):
        self._real_var = textvariable
        self._hint = hint
        self._display = tk.StringVar()
        self._hinting = False
        self._syncing = False

        kwargs.setdefault("font", "SS_Body")
        kwargs.setdefault("relief", "flat")
        kwargs.setdefault("highlightthickness", 1)
        kwargs.setdefault("highlightbackground", theme.COLOR_INPUT_BORDER)
        kwargs.setdefault("highlightcolor", theme.COLOR_BORDER_FOCUS)
        kwargs.setdefault("bg", theme.COLOR_INPUT_BG)
        kwargs.setdefault("fg", theme.COLOR_TEXT_PRIMARY)
        kwargs.setdefault("insertbackground", theme.COLOR_TEXT_PRIMARY)

        super().__init__(master, textvariable=self._display, **kwargs)

        self.bind("<FocusIn>", self._on_focus_in)
        self.bind("<FocusOut>", self._on_focus_out)
        self._display.trace_add("write", self._on_display_write)
        self._real_var.trace_add("write", self._on_real_write)
        self._refresh_hint()

    def pack(self, *args, **kwargs):
        kwargs.setdefault("ipady", 4)
        return super().pack(*args, **kwargs)

    def grid(self, *args, **kwargs):
        kwargs.setdefault("ipady", 4)
        return super().grid(*args, **kwargs)

    def refresh_theme(self) -> None:
        """Update colors, borders, and fonts to match the active design tokens."""
        state = str(self.cget("state"))
        if state == "disabled":
            bg = theme.COLOR_DISABLED_BG
            fg = theme.COLOR_DISABLED_FG
            border = theme.COLOR_BORDER
        elif state == "readonly":
            bg = theme.COLOR_READONLY_BG
            fg = theme.COLOR_TEXT_PRIMARY
            border = theme.COLOR_BORDER
        else:
            bg = theme.COLOR_INPUT_BG
            fg = theme.COLOR_PLACEHOLDER if self._hinting else theme.COLOR_TEXT_PRIMARY
            border = theme.COLOR_INPUT_BORDER

        self.configure(
            bg=bg,
            fg=fg,
            insertbackground=theme.COLOR_TEXT_PRIMARY,
            highlightbackground=border,
            highlightcolor=theme.COLOR_BORDER_FOCUS,
            font="SS_Body",
        )

    def _on_focus_in(self, _event=None):
        if self._hinting:
            self._hinting = False
            self._syncing = True
            self._display.set("")
            self._syncing = False
        self.configure(fg=theme.COLOR_TEXT_PRIMARY)

    def _on_focus_out(self, _event=None):
        typed = self._display.get().strip()
        if self._hinting:
            return
        self._real_var.set(typed)
        self._refresh_hint()

    def _on_display_write(self, *_args):
        if self._syncing or self._hinting:
            return
        self._real_var.set(self._display.get())

    def _on_real_write(self, *_args):
        if self._syncing:
            return
        real = self._real_var.get()
        focused = self.focus_get() is self
        if real.strip():
            self._hinting = False
            if self._display.get() != real:
                self._syncing = True
                self._display.set(real)
                self._syncing = False
            self.configure(fg=theme.COLOR_TEXT_PRIMARY)
        elif not focused:
            self._refresh_hint()

    def _refresh_hint(self):
        if self._real_var.get().strip():
            self._hinting = False
            self._syncing = True
            self._display.set(self._real_var.get())
            self._syncing = False
            self.configure(fg=theme.COLOR_TEXT_PRIMARY)
            return
        if self.focus_get() is self:
            self._hinting = False
            self.configure(fg=theme.COLOR_TEXT_PRIMARY)
            return
        self._hinting = True
        self._syncing = True
        self._display.set(self._hint)
        self._syncing = False
        self.configure(fg=theme.COLOR_PLACEHOLDER)
        self.configure(fg=theme.COLOR_PLACEHOLDER)
