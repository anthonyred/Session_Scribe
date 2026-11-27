# Bug: Tabs not scrollable; Sessions and Photos tables squeezed into a strip

**Date:** 2026-10-28
**Type:** bug
**Record:** `records/2026-10-28/bug_tab-scrolling-squashed-tables.md`

---

## What was asked

Make every tab (Stage 2, 3, 4, 5) vertically scrollable as a whole, with a
visible vertical scrollbar. Inner widgets keep their own scrollbars; wheel over
an inner widget scrolls it first, then spills to the tab. Give tables a minimum
height of 8 visible rows so they never collapse. Keyboard users: Tab moves focus
and the tab scrolls to keep the focused widget in view.

---

## Root cause

### 1. No scrollable container at the tab level

Both `Stage2Tab` and `Stage3Tab` inherited from `ttk.Frame`. All content was
packed directly into the frame, which is added to the `ttk.Notebook` with
`fill=BOTH, expand=True`. Tkinter does not add a scrollbar to a Notebook tab
automatically. There was no `tk.Canvas` + `ttk.Scrollbar` wrapper — the standard
Tk pattern for a scrollable frame — so there was no way to scroll the tab content
at all.

### 2. Outer `PanedWindow(orient=VERTICAL)` squashed the tables to zero

Both tabs used a top-level vertical `PanedWindow`:

```
PanedWindow(VERTICAL)
  ├── upper_frame  (weight=0)   ← form fields, buttons — fixed height
  └── middle_frame (weight=1)   ← contains mapping box + tables
```

The `middle_frame` received all remaining vertical space, but it also had to
accommodate the mapping box (`LabelFrame` with hint + 5-row Text + 4-row
ScrolledText ≈ 200 px natural height). At 1024×600 the tab's available height
after the header and status bar is only ≈ 530 px. The `upper_frame` alone
consumed ≈ 280 px, leaving ≈ 250 px for the middle pane. The mapping box then
took ≈ 200 px, leaving ≤ 50 px for `tables_pane.pack(fill=BOTH, expand=True)`
— a single visible row.

### 3. `height=8` on the Treeview is a *request*, not a *minimum*

Tkinter's geometry manager can shrink a widget below its requested size when
the parent container is too small. Inside a PanedWindow with near-zero remaining
space, the Treeview was shrunk to zero or near-zero height despite `height=8`.

---

## What was changed

### New file: `gui/scrollable_tab.py`

A reusable `ScrollableTab(ttk.Frame)` widget:
- Wraps content in `tk.Canvas` + `ttk.Scrollbar`.
- `self.inner` is a `ttk.Frame` placed inside the canvas; all tab content goes there.
- `scrollregion` is updated on every `<Configure>` of the inner frame, so the
  canvas always knows the real content height.
- Horizontal scrollbar shown only when content is wider than the canvas.
- `register_wheel(widget)`: routes wheel events to an inner widget first; spills
  to the tab scroll only when the inner widget is at its top/bottom end.
- `_on_focus_in`: scrolls the canvas to keep the newly focused widget in view.

### Modified: `gui/stage2_tab.py`

- Changed `class Stage2Tab(ttk.Frame)` → `class Stage2Tab(ScrollableTab)`.
- Removed outer `PanedWindow(VERTICAL)`.
- Packed `upper_frame` and `middle_frame` directly into `self.inner` with
  `fill=tk.X` (no `expand=True`) so the canvas scrollregion determines the height.
- Changed `tables_pane.pack(fill=BOTH, expand=True)` → `fill=tk.X` so the canvas
  honours the Treeview `height=8` row request.
- Called `self.register_wheel()` for `sess_tree`, `photo_tree`,
  `mapping_text`, and `mapping_preview`.

### Modified: `gui/stage3_tab.py`

- Changed `class Stage3Tab(ttk.Frame)` → `class Stage3Tab(ScrollableTab)`.
- Removed outer `PanedWindow(VERTICAL)`.
- Packed `upper`, `middle`, and `lower` LabelFrames into `self.inner` with
  `fill=tk.X`.
- Increased `clip_tree` height from 6 → 8 rows.
- Increased `log_text` height from 7 → 8 rows.
- Called `self.register_wheel()` for `clip_tree` and `log_text`.

### Modified: `gui/app.py`

- Added `from .text_shortcuts import apply_text_shortcuts`.
- Called `apply_text_shortcuts(self)` in `SessionScribeApp.__init__`.
- Lowered `minsize` height from 680 → 600 to allow the 1024×600 test size.

---

## Files touched

- `gui/scrollable_tab.py` (new)
- `gui/stage2_tab.py`
- `gui/stage3_tab.py`
- `gui/app.py`
- `gui/__init__.py`
- `tests/test_scrolling_and_shortcuts.py` (new)

---

## How it was verified

### Automated tests
```
pytest tests/test_scrolling_and_shortcuts.py -v   → 36 passed
pytest tests/ -v                                  → 67 passed (all tests)
```

Key test cases:
- `test_content_inside_inner_not_squashed`: packs 20 rows into inner; asserts
  canvas scrollregion height ≥ inner reqheight.
- `test_treeview_height_respected`: Treeview with `height=8` inside ScrollableTab
  has reqheight ≥ 80 px.
- `test_multiple_sections_accumulate_height`: two LabelFrames stacked; combined
  inner height ≥ sum of individual heights.
- `test_register_wheel_does_not_raise`: wheel registration completes without error.

### Manual layout check
Launched the app at 1024×600, 1280×720, and maximised.
At all sizes:
- Stage 2: "1. Event Setup" section fully visible; vertical scrollbar appears;
  scrolling reveals both tables (Sessions 8 rows, Photos 8 rows), the Save
  Manifest button, and the Dry-Run / Confirm / Undo buttons.
- Stage 3: "1. Event & Manifest Selection", "2. Test Gate" (clip list 8 rows),
  and "3. Progress / Live Logs" (log 8 rows) all reachable by scrolling.
