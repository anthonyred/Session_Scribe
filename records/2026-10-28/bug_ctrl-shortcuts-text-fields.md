# Bug: Ctrl+Backspace inserts stray character; no word-movement shortcuts

**Date:** 2026-10-28
**Type:** bug
**Record:** `records/2026-10-28/bug_ctrl-shortcuts-text-fields.md`

---

## What was asked

Apply Ctrl word-edit shortcuts application-wide (class-level bindings on Entry,
ttk.Entry, Text, ScrolledText, Spinbox, editable Combobox):

- `Ctrl+Backspace` — delete previous word (or selection if one exists)
- `Ctrl+Delete` — delete next word
- `Ctrl+Left` / `Ctrl+Right` — move by word
- `Ctrl+Shift+Left` / `Ctrl+Shift+Right` — extend selection by word
- `Ctrl+A` — select all

Word boundary = maximal run of `[A-Za-z0-9]`, so `.`, `-`, `_`, `\`, `,` all act
as separators. `Ctrl+C/V/X/Z` must continue to work.

Read-only and disabled fields must not be changed by the shortcuts.

---

## Root cause

### 1. Tk's default `<Control-BackSpace>` binding on Windows is wrong

On Windows, Tk binds `<Control-BackSpace>` at the class level to
`delete-char-backward` — which deletes exactly **one character** rather than a
word. In some versions of Tk on Windows the byte `\x08` (ASCII BS) is first
inserted into the widget (visible as a small box glyph) and then one character
is removed, producing a net insertion of a control character or the deletion
of only one normal character. This is the "stray character" the user sees.

### 2. No word-movement bindings exist in Tk on Windows

Tk's default Text/Entry class bindings on Windows do not include:
- `<Control-Left>` / `<Control-Right>` moving by word
- `<Control-Shift-Left>` / `<Control-Shift-Right>` extending selection by word
- `<Control-Delete>` deleting a word forward

(These are platform-specific; they exist on X11/Linux but not on Win32.)

### 3. No application-level override

No code in `gui/` called `bind_class` to install correct handlers. Every
`Entry`, `ttk.Entry`, `HintEntry`, `tk.Text`, `ScrolledText`, `ttk.Spinbox`,
and `ttk.Combobox` was affected.

### 4. `HintEntry` inherits the bug

`HintEntry(tk.Entry)` does not override any keyboard bindings, so the broken
default was inherited silently.

---

## What was changed

### New file: `gui/text_shortcuts.py`

- Pure-Python helpers `_prev_word_start(text, pos)` and `_next_word_end(text, pos)`:
  locate word boundaries using `[A-Za-z0-9]+` as the word pattern.
- Handler functions for Entry-like widgets:
  `_entry_ctrl_backspace`, `_entry_ctrl_delete`,
  `_entry_ctrl_left`, `_entry_ctrl_right`,
  `_entry_ctrl_shift_left`, `_entry_ctrl_shift_right`,
  `_entry_ctrl_a`.
- Handler functions for Text-like widgets:
  `_text_ctrl_backspace`, `_text_ctrl_delete`,
  `_text_ctrl_left`, `_text_ctrl_right`,
  `_text_ctrl_shift_left`, `_text_ctrl_shift_right`,
  `_text_ctrl_a`.
- `_is_editable(widget)` guard: returns `False` for `state="readonly"` or
  `state="disabled"`, so handlers silently skip protected widgets.
- `apply_text_shortcuts(root)`: installs all bindings via `root.bind_class(cls,
  seq, handler, add=False)` on widget classes `Entry`, `TEntry`, `TSpinbox`,
  `TCombobox`, and `Text`. The `add=False` flag **replaces** the broken Tk
  default for `<Control-BackSpace>` instead of adding a second handler.

### Modified: `gui/app.py`

- Imports `apply_text_shortcuts`.
- Calls `apply_text_shortcuts(self)` in `SessionScribeApp.__init__` after the Tk
  root is created. Called exactly once; every current and future widget instance
  inherits the bindings.

---

## Files touched

- `gui/text_shortcuts.py` (new)
- `gui/app.py`
- `gui/__init__.py`
- `tests/test_scrolling_and_shortcuts.py` (new)

---

## How it was verified

### Automated tests — `tests/test_scrolling_and_shortcuts.py`

```
pytest tests/test_scrolling_and_shortcuts.py -v   → 36 passed
pytest tests/ -v                                  → 67 passed (all tests)
```

Key cases:
- `test_entry_ctrl_backspace_deletes_word`: "hello world" → cursor at end →
  Ctrl+Backspace → "hello ".
- `test_entry_ctrl_backspace_no_stray_char`: result contains no char with
  `ord(c) < 32`.
- `test_entry_ctrl_backspace_with_selection_deletes_sel`: selection "world"
  deleted first; cursor left intact.
- `test_entry_ctrl_backspace_multiple_words`: three consecutive presses on
  "alpha beta gamma" → empty string.
- `test_readonly_entry_not_modified`, `test_disabled_entry_not_modified`:
  `var.get()` unchanged after handler call.
- `test_entry_filename_word_deletion`: "VID001.mp4" → Ctrl+Backspace ×2 → "".
- `test_mapping_box_ctrl_backspace_filename`: "VID001.mp4, VID002.mp4" →
  two presses → "VID001.mp4, " (correct boundary at `.`).
- Word-boundary round-trip: traversing right then left returns to position 0.

### Manual testing

Launched the app and tested:
- **Event Name field** (HintEntry): Ctrl+Backspace deleted previous word, not one char; no box glyph.
- **Organiser / Venue fields**: same.
- **Event Folder field** (ttk.Entry, read-only): no change.
- **Speaker-mapping box** (tk.Text): Ctrl+Backspace deleted "mp4" from "VID001.mp4", then "VID001", stopping at "."; no stray character appeared.
- **Ctrl+C / Ctrl+V / Ctrl+Z**: continued to work in all fields.
