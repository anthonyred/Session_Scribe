# Bug: Stage 2 Browse Layout Shift and Dead Mouse Wheel

**Date:** 2026-10-30  
**Type:** bug  

---

## What was asked / requested

Task 1:
1. Symptoms:
   - After clicking Browse (opening, cancelling, or selecting a folder in the folder dialog), the whole Stage 2 page and its fields shifted downward.
   - The mouse wheel no longer scrolled Stage 2 after the dialog closed.
2. Root cause analysis on why the shift occurs and why the wheel goes dead, reporting whether they share a cause.
3. Fix so that opening, cancelling, or confirming Browse (and "Scan Events", Dry-Run popups, Undo) leaves the layout and scroll position unchanged. Changing the folder updates values only and never rebuilds widgets.
4. The mouse wheel and touchpad must scroll the tab at all times, including right after any dialog closes and after switching tabs and coming back. Use one application-level wheel handler routing by the widget under the pointer (inner table or text box first, then the tab) instead of per-widget bindings that can be lost.
5. Apply the same check to every dialog-opening action across Stage 2 and Stage 3.

---

## Root cause

The layout shift and dead mouse wheel did **not** share a single cause; they were caused by two separate mechanisms:

1. **Downwards Layout Shift:**
   - In `ScrollableTab`, a global `bind_all("<FocusIn>", self._on_focus_in)` was installed to bring focused widgets into view via `_scroll_widget_into_view(event.widget)`.
   - When a native folder dialog (e.g. `filedialog.askdirectory`) or popup closes, Windows Tkinter sends `<FocusIn>` events to the parent hierarchy starting at container frames (`self.inner`), followed by the button that opened the dialog.
   - Because `self.inner` is a container whose requested height exceeds the canvas viewport height, `_scroll_widget_into_view` calculated `widget_bottom > canvas_h` and executed `self._canvas.yview_moveto(...)`, dragging the entire page to the bottom. Subsequent focus on top widgets pulled it partly back up, causing erratic jumping and resetting the user's scroll offset.

2. **Dead Mouse Wheel:**
   - Mouse wheel events were only bound to the canvas background and 4 specific leaf widgets (`_bind_wheel` on `self._canvas`, trees, and text).
   - In Tkinter on Windows, `<MouseWheel>` events fire on the widget under the cursor and **do not bubble** up to parent containers if unhandled.
   - Intermediate frames (`self.inner`, group frames, label frames, labels, entries, buttons) covered virtually the entire canvas surface without `<MouseWheel>` bindings. Hovering over any of these widgets silently dropped all mouse wheel events.
   - When dialogs closed or tabs switched, focus transitions exposed these unhandled regions, making the wheel appear dead.

---

## What was changed

1. **Application-Level Wheel Router (`gui/scrollable_tab.py` & `gui/app.py`):**
   - Implemented `install_application_wheel_handler(root)` which attaches a single application-level binding on `<MouseWheel>`, `<Button-4>`, and `<Button-5>`.
   - The handler `_app_route_wheel(event)` inspects `winfo_containing(event.x_root, event.y_root)`:
     - If the cursor is over an inner scrollable widget (`ttk.Treeview`, `tk.Text`, `tk.Listbox`, `ttk.Spinbox`), it scrolls the inner widget first until reaching its boundaries (`lo <= 0.0` or `hi >= 1.0`).
     - If hovering over any other control (buttons, entries, frames, labels, background) or when the inner widget has reached its boundary, it routes the scroll to the active `ScrollableTab` canvas via `found_tab._scroll_tab(delta)`.
     - Returns `"break"` to prevent duplicate handling.

2. **Focus-In Container & Visibility Guards (`gui/scrollable_tab.py`):**
   - Updated `_scroll_widget_into_view` to ignore container widgets (`_CONTAINER_CLASSES`: `Frame`, `Canvas`, `Labelframe`, `Notebook`, `Toplevel`, `Button`, etc.).
   - Added a visibility check: if the focused widget is already inside the visible canvas viewport (`widget_top >= 0 and widget_bottom <= canvas_h`), no scrolling occurs.

3. **Scroll Offset Preservation Context Manager (`gui/scrollable_tab.py`):**
   - Added `preserve_scroll()` context manager in `ScrollableTab`. It records `yview()[0]`, suppresses automatic focus scrolling during the block, and restores the exact scroll position upon exit.
   - Wrapped all dialog-opening actions in `gui/stage2_tab.py` and `gui/stage3_tab.py`:
     - Stage 2: `_browse_event_dir`, `_scan_events_dialog`, `_run_dry_run`, `_confirm_separation`, `_undo_separation`, `_on_session_double_click`.
     - Stage 3: `_browse_event_dir`, `_open_rename_transcripts_dialog`, `_rebuild_combined_clicked`, `_on_job_finished`.

---

## Files touched

- `gui/scrollable_tab.py`
- `gui/stage2_tab.py`
- `gui/stage3_tab.py`
- `gui/app.py`
- `tests/test_stage2_stage3_fixes.py`

---

## How it was verified

1. **Automated Unit & Acceptance Tests (`tests/test_stage2_stage3_fixes.py`):**
   - `test_application_level_wheel_scrolls_tab_over_button`: Confirmed wheel delta over non-scrollable buttons routes directly to canvas scrolling.
   - `test_preserve_scroll_context_manager`: Verified canvas offset remains identical across simulated focus events and actions.
2. **Integration Verification (`scratch/verify_all.py`):**
   - Simulated Browse cancel: scroll offset remained identical (`0.0499 -> 0.0499`).
   - Simulated Browse select: scroll offset remained identical (`0.0499 -> 0.0499`).
   - Verified wheel scrolling immediately over buttons without dead zones (`0.0000 -> 0.0816`).
   - Switched between Stage 2 and Stage 3 tabs and verified wheel scrolling works immediately upon return.
3. **Full Test Suite:**
   - Ran `pytest tests`, verifying all 79 tests passed.

