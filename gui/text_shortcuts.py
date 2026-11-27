"""
gui/text_shortcuts.py

Application-wide Ctrl word-editing keyboard shortcuts for all text input widgets.

Problem
-------
On Windows, Tkinter's default Entry/Text class bindings do not support:
  - Ctrl+Backspace → delete previous word
  - Ctrl+Delete    → delete next word
  - Ctrl+Left / Ctrl+Right → move by word
  - Ctrl+Shift+Left / Ctrl+Shift+Right → extend selection by word
  - Ctrl+A → select all

Worse, <Control-BackSpace> may insert a stray BS control character (rendered
as a small box) before deleting one character.

Solution
--------
``apply_text_shortcuts(root)`` is called **once** in SessionScribeApp.__init__.
It installs class-level bindings on:
    Entry, TEntry (ttk.Entry), Text, TSpinbox (ttk.Spinbox), TCombobox (ttk.Combobox)

Class-level bindings apply to every instance, current and future, without
per-widget code.

Word definition
---------------
A "word" is a maximal run of ``[A-Za-z0-9]`` characters.  Punctuation (. - _ \\ ,
/ : etc.) acts as a boundary.  This matches user expectation for filenames, paths,
and the mapping paste-box.

Protection
----------
Handlers check the widget's state attribute.  Read-only ("readonly") and
disabled ("disabled") widgets are silently ignored — their content is never changed.

Compatibility
-------------
Ctrl+C / Ctrl+V / Ctrl+X / Ctrl+Z are already handled correctly by Tk on Windows
(they are bound at the class level by Tk itself).  The handlers below return
``"break"`` only for the shortcuts they manage, so those existing bindings are
not affected.
"""

from __future__ import annotations

import re
import tkinter as tk
from tkinter import ttk

# Regex patterns for word-boundary navigation.
# A "word" is a maximal run of alphanumeric characters.
_WORD_RE        = re.compile(r"[A-Za-z0-9]+")
_NON_WORD_RE    = re.compile(r"[^A-Za-z0-9]+")


# ---------------------------------------------------------------------------
# Helpers shared by Entry-like and Text-like widgets
# ---------------------------------------------------------------------------

def _is_editable(widget: tk.Widget) -> bool:
    """Return True iff the widget is in a state that allows text changes."""
    try:
        state = str(widget.cget("state"))
        return state not in ("readonly", "disabled")
    except tk.TclError:
        return True


def _entry_get_pos(widget) -> int:
    """Return the current insertion cursor index for an Entry-like widget."""
    return widget.index(tk.INSERT)


def _entry_has_sel(widget) -> bool:
    """True if the Entry has an active selection."""
    try:
        widget.index(tk.SEL_FIRST)
        return True
    except tk.TclError:
        return False


def _prev_word_start(text: str, pos: int) -> int:
    """
    Return the index of the start of the previous word, moving left from *pos*.
    Skips any non-word characters first (e.g. trailing spaces / punctuation).
    """
    if pos <= 0:
        return 0
    t = text[:pos]
    # Skip non-word chars (punctuation, spaces) to the left.
    m = _NON_WORD_RE.search(t[::-1])
    skip = m.end() if m and m.start() == 0 else 0
    t2 = t[:pos - skip]
    if not t2:
        return 0
    # Find the start of the preceding word.
    m2 = _WORD_RE.search(t2[::-1])
    if not m2:
        return 0
    return pos - skip - m2.end()


def _next_word_end(text: str, pos: int) -> int:
    """
    Return the index just past the end of the next word, moving right from *pos*.
    Skips non-word characters first.
    """
    length = len(text)
    if pos >= length:
        return length
    t = text[pos:]
    # Skip non-word chars.
    m = _NON_WORD_RE.match(t)
    skip = m.end() if m else 0
    t2 = t[skip:]
    if not t2:
        return length
    m2 = _WORD_RE.match(t2)
    advance = m2.end() if m2 else len(t2)
    return pos + skip + advance


# ---------------------------------------------------------------------------
# Entry-like handlers (Entry, ttk.Entry, ttk.Spinbox, editable ttk.Combobox)
# ---------------------------------------------------------------------------

def _entry_ctrl_backspace(event: tk.Event) -> str:
    w = event.widget
    if not _is_editable(w):
        return "break"
    if _entry_has_sel(w):
        w.delete(tk.SEL_FIRST, tk.SEL_LAST)
        return "break"
    pos  = _entry_get_pos(w)
    text = w.get()
    new_pos = _prev_word_start(text, pos)
    if new_pos < pos:
        w.delete(new_pos, pos)
    return "break"


def _entry_ctrl_delete(event: tk.Event) -> str:
    w = event.widget
    if not _is_editable(w):
        return "break"
    if _entry_has_sel(w):
        w.delete(tk.SEL_FIRST, tk.SEL_LAST)
        return "break"
    pos  = _entry_get_pos(w)
    text = w.get()
    new_pos = _next_word_end(text, pos)
    if new_pos > pos:
        w.delete(pos, new_pos)
    return "break"


def _entry_ctrl_left(event: tk.Event) -> str:
    w = event.widget
    pos  = _entry_get_pos(w)
    text = w.get()
    new_pos = _prev_word_start(text, pos)
    w.icursor(new_pos)
    # Clear selection (plain Ctrl+Left = move only)
    try:
        w.selection_clear()
    except tk.TclError:
        pass
    return "break"


def _entry_ctrl_right(event: tk.Event) -> str:
    w = event.widget
    pos  = _entry_get_pos(w)
    text = w.get()
    new_pos = _next_word_end(text, pos)
    w.icursor(new_pos)
    try:
        w.selection_clear()
    except tk.TclError:
        pass
    return "break"


def _entry_ctrl_shift_left(event: tk.Event) -> str:
    w = event.widget
    pos  = _entry_get_pos(w)
    text = w.get()
    new_pos = _prev_word_start(text, pos)
    # Extend or create selection from anchor to new_pos.
    try:
        anchor = w.index(tk.SEL_FIRST) if _entry_has_sel(w) else pos
    except tk.TclError:
        anchor = pos
    # If we are moving left, select from new_pos to anchor (or further).
    try:
        # Determine existing anchor: the end that is NOT moving.
        sel_first = w.index(tk.SEL_FIRST)
        sel_last  = w.index(tk.SEL_LAST)
        anchor_pos = sel_last if pos == sel_first else sel_first
    except tk.TclError:
        anchor_pos = pos
    lo, hi = (new_pos, anchor_pos) if new_pos < anchor_pos else (anchor_pos, new_pos)
    w.selection_range(lo, hi)
    w.icursor(new_pos)
    return "break"


def _entry_ctrl_shift_right(event: tk.Event) -> str:
    w = event.widget
    pos  = _entry_get_pos(w)
    text = w.get()
    new_pos = _next_word_end(text, pos)
    try:
        sel_first = w.index(tk.SEL_FIRST)
        sel_last  = w.index(tk.SEL_LAST)
        anchor_pos = sel_last if pos == sel_first else sel_first
    except tk.TclError:
        anchor_pos = pos
    lo, hi = (anchor_pos, new_pos) if anchor_pos < new_pos else (new_pos, anchor_pos)
    w.selection_range(lo, hi)
    w.icursor(new_pos)
    return "break"


def _entry_ctrl_a(event: tk.Event) -> str:
    w = event.widget
    w.selection_range(0, tk.END)
    w.icursor(tk.END)
    return "break"


# ---------------------------------------------------------------------------
# Text-like handlers (tk.Text, ScrolledText)
# ---------------------------------------------------------------------------

def _text_index_to_int(widget, index) -> int:
    """Convert a Tk Text index (row.col string) to a flat character offset."""
    return int(widget.count("1.0", index, "chars")[0])


def _text_int_to_index(widget, offset: int) -> str:
    return f"1.0 + {offset} chars"


def _text_ctrl_backspace(event: tk.Event) -> str:
    w = event.widget
    if not _is_editable(w):
        return "break"
    if w.tag_ranges(tk.SEL):
        w.delete(tk.SEL_FIRST, tk.SEL_LAST)
        return "break"
    total_chars = int(w.count("1.0", tk.END, "chars")[0])
    pos = _text_index_to_int(w, tk.INSERT)
    text = w.get("1.0", tk.END)
    new_pos = _prev_word_start(text, pos)
    if new_pos < pos:
        w.delete(_text_int_to_index(w, new_pos), _text_int_to_index(w, pos))
    return "break"


def _text_ctrl_delete(event: tk.Event) -> str:
    w = event.widget
    if not _is_editable(w):
        return "break"
    if w.tag_ranges(tk.SEL):
        w.delete(tk.SEL_FIRST, tk.SEL_LAST)
        return "break"
    pos  = _text_index_to_int(w, tk.INSERT)
    text = w.get("1.0", tk.END)
    new_pos = _next_word_end(text, pos)
    if new_pos > pos:
        w.delete(_text_int_to_index(w, pos), _text_int_to_index(w, new_pos))
    return "break"


def _text_ctrl_left(event: tk.Event) -> str:
    w = event.widget
    pos  = _text_index_to_int(w, tk.INSERT)
    text = w.get("1.0", tk.END)
    new_pos = _prev_word_start(text, pos)
    w.mark_set(tk.INSERT, _text_int_to_index(w, new_pos))
    w.tag_remove(tk.SEL, "1.0", tk.END)
    return "break"


def _text_ctrl_right(event: tk.Event) -> str:
    w = event.widget
    pos  = _text_index_to_int(w, tk.INSERT)
    text = w.get("1.0", tk.END)
    new_pos = _next_word_end(text, pos)
    w.mark_set(tk.INSERT, _text_int_to_index(w, new_pos))
    w.tag_remove(tk.SEL, "1.0", tk.END)
    return "break"


def _text_ctrl_shift_left(event: tk.Event) -> str:
    w = event.widget
    pos  = _text_index_to_int(w, tk.INSERT)
    text = w.get("1.0", tk.END)
    new_pos = _prev_word_start(text, pos)
    _text_extend_sel(w, pos, new_pos)
    return "break"


def _text_ctrl_shift_right(event: tk.Event) -> str:
    w = event.widget
    pos  = _text_index_to_int(w, tk.INSERT)
    text = w.get("1.0", tk.END)
    new_pos = _next_word_end(text, pos)
    _text_extend_sel(w, pos, new_pos)
    return "break"


def _text_extend_sel(w, from_pos: int, to_pos: int):
    """Extend the Text selection from *from_pos* to *to_pos*."""
    ranges = w.tag_ranges(tk.SEL)
    if ranges:
        anchor_idx = _text_index_to_int(w, str(ranges[1])) if _text_index_to_int(w, str(ranges[0])) == from_pos else _text_index_to_int(w, str(ranges[0]))
    else:
        anchor_idx = from_pos
    lo, hi = sorted([anchor_idx, to_pos])
    w.tag_remove(tk.SEL, "1.0", tk.END)
    w.tag_add(tk.SEL, _text_int_to_index(w, lo), _text_int_to_index(w, hi))
    w.mark_set(tk.INSERT, _text_int_to_index(w, to_pos))


def _text_ctrl_a(event: tk.Event) -> str:
    w = event.widget
    w.tag_add(tk.SEL, "1.0", tk.END)
    w.mark_set(tk.INSERT, tk.END)
    return "break"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def apply_text_shortcuts(root: tk.Tk) -> None:
    """
    Install Ctrl word-editing bindings application-wide.

    Call **once** in SessionScribeApp.__init__ after the Tk root is created.
    Uses ``bind_class`` so every current and future widget instance inherits
    the bindings without per-widget code.
    """
    # Widget class names as Tk knows them.
    entry_classes = ["Entry", "TEntry", "TSpinbox", "TCombobox"]
    text_classes  = ["Text"]

    entry_bindings = {
        "<Control-BackSpace>":   _entry_ctrl_backspace,
        "<Control-Delete>":      _entry_ctrl_delete,
        "<Control-Left>":        _entry_ctrl_left,
        "<Control-Right>":       _entry_ctrl_right,
        "<Control-Shift-Left>":  _entry_ctrl_shift_left,
        "<Control-Shift-Right>": _entry_ctrl_shift_right,
        "<Control-a>":           _entry_ctrl_a,
        "<Control-A>":           _entry_ctrl_a,
    }

    text_bindings = {
        "<Control-BackSpace>":   _text_ctrl_backspace,
        "<Control-Delete>":      _text_ctrl_delete,
        "<Control-Left>":        _text_ctrl_left,
        "<Control-Right>":       _text_ctrl_right,
        "<Control-Shift-Left>":  _text_ctrl_shift_left,
        "<Control-Shift-Right>": _text_ctrl_shift_right,
        "<Control-a>":           _text_ctrl_a,
        "<Control-A>":           _text_ctrl_a,
    }

    for cls in entry_classes:
        for seq, handler in entry_bindings.items():
            root.bind_class(cls, seq, handler, add=False)

    for cls in text_classes:
        for seq, handler in text_bindings.items():
            root.bind_class(cls, seq, handler, add=False)
