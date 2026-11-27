# Feature: Scrollable Tab Canvases and Application-Wide Text Editing Shortcuts

> **Note:** Reconstructed from records and git history.

- **Date:** 2026-10-28
- **Type:** feature
- **Status:** Complete

---

## 1. What was asked
1. Make notebook tabs scrollable vertically so tables and control boxes do not squash or disappear on smaller screens (e.g. 1366x768).
2. Fix text entry boxes so Ctrl+Backspace deletes previous words without inserting stray unprintable characters, and add standard word-movement shortcuts (Ctrl+Left, Ctrl+Right, Ctrl+A) across all entries and text widgets.

## 2. Why
On laptop screens without scaling adjustments, multi-section panels were squeezed to unreadable strips. Furthermore, Windows Tkinter entries lacked native modern word deletion and navigation shortcuts, causing typing friction and insertion of ASCII DEL (`\x7f`) characters.

## 3. What was built (user-visible behaviour)
- `ScrollableTab`: Generic canvas wrapper with vertical scrollbar, mouse wheel routing, and dynamic interior frame resizing.
- `text_shortcuts.py`: Class-level event bindings (`bind_class`) for `TEntry`, `Entry`, and `Text` supporting Ctrl+Backspace, Ctrl+Delete, Ctrl+Left, Ctrl+Right, and Ctrl+A.
- Unified mouse wheel routing ensures wheel events over buttons or nested frames scroll the scrollable canvas smoothly.

## 4. How to use it
- Mouse wheel: Scroll anywhere over a tab to view top-to-bottom contents.
- Shortcuts: Use Ctrl+Backspace, Ctrl+Left/Right, and Ctrl+A in any text field or text area across the app.

## 5. Settings and defaults
- Scrollbar auto-binds to mouse wheel.
- Word boundaries recognize punctuation (`_`, `-`, `.`) as delimiters.

## 6. Files changed
- `gui/scrollable_tab.py`
- `gui/text_shortcuts.py`
- `gui/stage2_tab.py`
- `gui/stage3_tab.py`
- `tests/test_scrolling_and_shortcuts.py`

## 7. Limitations and known issues
- Disabled and read-only entries ignore mutation shortcuts while preserving navigation and selection shortcuts.

## 8. How it was verified
- 36 unit and integration tests in `tests/test_scrolling_and_shortcuts.py`.

## 9. How to roll it back
- Revert inheritance from `ScrollableTab` to plain `ttk.Frame`, and remove `bind_text_shortcuts` initialization in `gui/app.py`.

