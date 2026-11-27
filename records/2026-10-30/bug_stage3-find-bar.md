# Bug Record: Stage 3 Transcript Viewer Find Bar and Keyboard Shortcuts Fix

**Date:** 2026-10-30  
**Type:** `bug`  
**Short Title:** `bug_stage3-find-bar`  

---

## 1. What was asked / requested

In the Stage 3 transcript viewer (`TranscriptEditorDialog`):
- Find button and Ctrl+F did nothing in either Read or Edit mode.
- An empty band between the toolbar and the text area appeared to be an undrawn find bar.
- Perform root cause analysis first across geometry, focus, event bindings, read-only search capability, and highlight tag rendering.
- Fix requirements:
  1. Ctrl+F and Find button open a visible find bar between the toolbar and the text area, focus the search field, and pre-fill it with any selected text. Ctrl+F works wherever focus is inside the viewer.
  2. The bar has a search field, Next button, Previous button, a "Match case" checkbox (off by default), match counter such as "3 of 12" (or "No matches"), and a close button (`✕`).
  3. Key navigation: Enter or F3 goes to the next match, Shift+Enter or Shift+F3 to the previous. Esc closes the bar and clears highlights.
  4. Highlighting: All matches highlighted in soft yellow (`#ffe082`), the active match in a stronger orange (`#ff9800`), and the text scrolls so the active match is visible (`see()`). Search wraps around with a short message `(wrapped)`.
  5. Non-destructive: Finding never changes the transcript, never marks it modified, and never triggers the unsaved-changes prompt. It works in Read and Edit modes and keeps word-level shortcuts functioning in the search field.
  6. Verification on a dummy transcript with repeated words, mixed case, no matches, and in both Read and Edit modes. Add unit test under `tests/`, bug record in dated folder, update `CHANGELOG.md`, and do not commit.

---

## 2. Root Cause

1. **Pack Parenting Mismatch in `_toggle_find_bar()`**:
   - In `TranscriptEditorDialog._build_ui()`, `self.find_frame` was instantiated with parent `self` (`Toplevel`).
   - However, `_toggle_find_bar()` invoked:
     `self.find_frame.pack(fill=tk.X, padx=8, pady=(0, 4), before=self.txt_widget.master)`
   - `self.txt_widget` is a `scrolledtext.ScrolledText`, whose `.master` is an internal child frame of `text_frame` (a grandchild of `self`), not a direct sibling of `find_frame`.
   - When Tkinter evaluated `pack(..., before=non_sibling)`, the geometry manager placed `find_frame` at coordinates `y=39` with height `33`, while `text_frame` remained at `y=39` with height `544`. `text_frame` completely overlapped and obscured `find_frame`.
2. **Empty Band Origin**:
   - The empty band between the toolbar and text area was the outer padding `pady=(2, 4)` of `text_frame` against the toolbar, appearing as an undrawn space.
3. **Missing Match Case & Wrap-Around Controls**:
   - The initial prototype find bar lacked a "Match case" toggle, bidirectional cycling with wrap-around indicators, and cursor-relative search resumption.
4. **Missing Key Bindings on Focused Children**:
   - `<Control-f>`, `<Escape>`, `<F3>`, and `<Shift-F3>` were not explicitly bound on `txt_widget` and `find_entry`, which could prevent navigation or find-bar invocation when those widgets captured focus.
5. **Selection Tag Priority**:
   - Active selections (`tk.SEL`) had default tag priority over search highlight tags, hiding matches under text selections unless explicitly raised.

---

## 3. What was changed

### 3.1 Layout & Hierarchy Fix (`gui/stage3_tab.py`)
- Stored `self.text_frame = ttk.Frame(self)` as an explicit attribute on `self`.
- Updated `_open_find_bar()` to pack with `before=self.text_frame`. Both `self.find_frame` and `self.text_frame` are direct children of `self`, allowing `find_frame` to pack cleanly directly below `toolbar` and push `text_frame` downwards to `y=70`.
- Configured search highlight tags and raised priorities:
  - `find_match`: background `#ffe082`, foreground `#000000`
  - `find_active`: background `#ff9800`, foreground `#ffffff`
  - Raised tags above `tk.SEL` so highlighted matches are visible through active selections.

### 3.2 Find Bar UI Elements & Keyboard Bindings (`gui/stage3_tab.py`)
- Added:
  - Search entry `self.find_entry`
  - `Next` button (`self._find_next`)
  - `Previous` button (`self._find_prev`)
  - `Match case` checkbox (`self.case_var = tk.BooleanVar(value=False)`)
  - Status counter `self.lbl_find_status` ("X of Y", "X of Y (wrapped)", "No matches")
  - Close button `✕` (`self._hide_find_bar`)
- Added comprehensive event bindings across `self`, `self.txt_widget`, and `self.find_entry`:
  - `<Control-f>` and `<Control-F>`: open find bar, pre-fill from selection, focus search field
  - `<F3>` and `<Shift-F3>`: Next and Previous match navigation
  - `<Return>` and `<Shift-Return>`: Next and Previous match navigation from `self.find_entry`
  - `<KeyRelease>` on `self.find_entry`: live search updates without disrupting navigation
  - `<Escape>`: closes find bar, clears all highlight tags, and restores focus to `txt_widget`

### 3.3 Search Algorithm & Navigation Logic (`gui/stage3_tab.py`)
- Implemented `_find_match(self, forward=True, is_nav=False)`:
  - Case-sensitivity driven by `nocase = not self.case_var.get()`.
  - Tags all occurrences with `find_match`.
  - On navigation (`is_nav=True`), checks whether insertion cursor was manually moved by the user in the text editor to resume searching seamlessly from the cursor position.
  - Detects wrap-around on both forward and backward cycles and appends `(wrapped)` to the status counter.
  - Non-destructive: Text widget `edit_modified(False)` is preserved; finding never alters text, never marks the file dirty, and never triggers unsaved changes prompts.
  - Cleared `self.txt_widget.edit_modified(False)` after initial transcript population in `_build_ui()`.

---

## 4. Files touched

- `gui/stage3_tab.py` — Fixed find bar pack hierarchy, added Match case and Next/Previous UI controls, bound Ctrl+F / F3 / Esc across widgets, implemented wrap-around search and tag highlighting.
- `tests/test_stage2_stage3_fixes.py` — Added unit and acceptance test `test_find_bar_visibility_and_search_features`.
- `CHANGELOG.md` — Added change record entry for `2026-10-30`.

---

## 5. How it was verified

1. **Automated Unit & Acceptance Tests**:
   - `pytest tests/test_stage2_stage3_fixes.py -k test_find_bar_visibility_and_search_features`:
     - Verified find bar mapping and geometry (`find_frame.winfo_y() < text_frame.winfo_y()`, height > 15px).
     - Verified pre-filling search query from active selection.
     - Verified case-insensitive matching (3 matches on dummy transcript with "Apple", "apple", "APPLE").
     - Verified Next navigation cycling to match 2 and 3, then wrapping to match 1 with `(wrapped)`.
     - Verified Previous navigation wrapping backward to match 3 with `(wrapped)` then to match 2.
     - Verified Match case filtering to 1 match for "Apple".
     - Verified "No matches" behavior and highlight clearing.
     - Verified Esc key closes find bar and removes tags.
     - Verified search in both Read and Edit modes without setting `_is_dirty` or `edit_modified()`.
     - Result: `PASSED` (1 passed).
2. **Full Regression Suite**:
   - Ran all test suites:
     `pytest tests/test_mapping_and_rename.py tests/test_scrolling_and_shortcuts.py tests/test_stage2_acceptance.py tests/test_stage2_stage3_fixes.py tests/test_stage3_acceptance.py`
     - Result: `93 passed, 1 skipped` in 14.64s.

