# Bug Record: Stage 3 Tk Table Cutoffs, Theme Manager & Qt Stage 3 Refinements

- **Date**: 2026-11-07
- **What was asked / symptom**:
  - **Tk Stage 3 Table**:
    * Headers cut off to one line when an event is loaded, cutting off the second lines of "Time taken for transcription" and "Failover time" (without an event, it showed two lines).
    * "Test" and "Duration" headers were clipped in both states ("Tes", "Duratio").
    * Cells were cut too ("✓ Approve" instead of "✓ Approved", "[TES" instead of "[TEST]").
    * Wide empty gaps around Failover and Status while narrower columns were cramped.
    * Totals row clipped: `"21 of 21 clips timed"` truncated to `"21 of 21 clips t"`.
    * Table failed to adapt to window or monitor size automatically (although manual dragging worked).
  - **Tk Appearance (Part C)**:
    * System, Light, and Dark mode theme manager (default `system`), remembered in `ui.ini`.
    * Compact `[ System | Light | Dark ]` segmented control in header bar.
    * Live switching by reading `AppsUseLightTheme` registry key polled every 2s.
    * Full dark palette (`#1C1C1E` app bg, `#2C2C2E` cards, `#F5F5F7` text, `#A1A1A6` secondary text, `#0A84FF` accent, WCAG AA >= 4.5:1).
    * Windows DWM dark title bar via `DwmSetWindowAttribute(parent_hwnd, 20)`.
    * In-app themed `View ▾` header menu replacing native OS menu bar.
  - **Qt Stage 3 Carryover (Part D)**:
    * Invariant header height across 0, 3, 21, 60 rows and model changes.
    * Exact font-metrics badge measurement and strict role space distribution.
    * Zero horizontal scrollbars and drag redistribution carried over to Qt tests.

---

## Part A: Root Cause Analysis (7 Hypotheses Evaluated with Empirical Measurements)

1. **Hypothesis 1: Content width tied to window width (ScrollableTab canvas vs inner frame vs Treeview)**
   - *Status*: **CONFIRMED**.
   - *Evidence*: `ScrollableTab._on_canvas_configure` previously contained:
     `new_width = max(event.width, self.inner.winfo_reqwidth())`.
     Tkinter's `Canvas.create_window` keeps the inner frame at its natural requested width (`winfo_reqwidth()`) unless explicitly overridden with `self.canvas.itemconfigure(self._inner_id, width=event.width)`. When measured at 1366px window width, the canvas was 1331px, but the inner frame remained stuck at 1420px (Treeview at 1379px), overflowing the viewport by 89px.
   - *Fix*: Replaced with strict width tying: `self.canvas.itemconfigure(self._inner_id, width=event.width)` and updated scrollregion to `(0, 0, event.width, inner_h)`.

2. **Hypothesis 2: Fit execution timing and mapped/unmapped width visibility**
   - *Status*: **CONFIRMED**.
   - *Evidence*: When the application launches, Stage 2 is active and Stage 3 is unmapped. In Tkinter, unmapped widgets return `winfo_width() == 1`. Stage 3 had a fallback default of 1100px. Furthermore, `<<NotebookTabChanged>>` was never bound on the notebook, so when the user switched to Stage 3, `_recompute_column_widths()` was never triggered with the real mapped viewport width.
   - *Fix*: Bound `<<NotebookTabChanged>>` on `self.notebook` to immediately re-fit active tab columns with real mapped viewport width, and added `<Map>` handler on `Stage3Tab` (`after(20, self._recompute_column_widths)`).

3. **Hypothesis 3: DPI awareness initialization order and scaling**
   - *Status*: **CONFIRMED**.
   - *Evidence*: Windows Win32 API strictly requires process DPI awareness to be set before any top-level Win32 window is created. In `gui/app.py`, `theme.make_dpi_aware()` was called inside `SessionScribeApp.__init__()` after `super().__init__()` had already initialized `tk.Tk()`. Calling `SetProcessDpiAwareness` after Tk initialization returned `E_ACCESSDENIED` (0x80070005) and Windows left the process in `DPI Unaware (mode 0)`.
   - *Fix*: Called `theme.make_dpi_aware()` at module level and before `super().__init__()`, invoking `SetProcessDpiAwarenessContext(-4)` (Per-Monitor V2) first, with fallback to `SetProcessDpiAwareness(2)` and `SetProcessDPIAware()`. Achieved mode 2 (Per-Monitor DPI Aware V2).

4. **Hypothesis 4: Initial window size selection vs monitor usable work area**
   - *Status*: **CONFIRMED**.
   - *Evidence*: Window geometry was hardcoded to `"1100x750"`. On 1366×768 displays (where usable work area height is ~720px after taskbar), 750px height exceeded the usable monitor screen, pushing the status bar and bottom table rows off-screen. On 1920×1080 (usable work area ~1032px), 1100×750 left massive unused space.
   - *Fix*: Implemented dynamic geometry calculation targeting ~85% of usable monitor work area via Win32 `SPI_GETWORKAREA` / `GetMonitorInfoW(rcWork)`. Clamped between 1000×640 and `work_w × work_h`, centered, and persisted in `ui.ini`.

5. **Hypothesis 5: Treeview heading height multiline behavior and row overlap**
   - *Status*: **CONFIRMED**.
   - *Evidence*: Standard ttk Treeview uses a single-line heading layout. When `gui/theme.py` configured `Treeview.Heading` padding with only 5px vertical padding, the heading container allocated ~27px (`linespace 17px + 10px pad`). When empty, no row was drawn underneath, so the second line was partially visible; but upon populating rows, Row 1 rendered starting at y=33, completely obscuring the second line ("for transcription", "time").
   - *Fix*: Configured dynamic linespace-aware heading padding: `extra_y = (max_hdr_lines - 1) * linespace` with balanced top and bottom padding, allocating ~50px heading height consistently across 0, 3, 21, and 60 rows.

6. **Hypothesis 6: Totals row alignment, status column width, and cutoff**
   - *Status*: **CONFIRMED**.
   - *Evidence*: In `_update_totals_row()`, the totals status string `"21 of 21 clips timed"` at 100% scale in `SS_BodyBold` measures 148px. The status column width was calculated as 123px (a 25px overflow deficit), causing the rightmost letters to be truncated to `"21 of 21 clips t"`.
   - *Fix*: Switched to short text: `"21 of 21 timed"` (measures 108px) and `"0 of 0 timed"`. Enforced status column `minwidth >= f_bold.measure("21 of 21 timed") + pad` (at least 126px).

7. **Hypothesis 7: Previous hotfix test passing vs screenshot clipping**
   - *Status*: **CONFIRMED**.
   - *Evidence*: Previous tests ran in a detached, single-widget root without `Notebook`, `ScrollableTab`, or unmapped tab states, passing mock viewport width arguments directly to `_recompute_column_widths()`. They did not test the full integration where `ScrollableTab` canvas inner frame failed to track window width, nor did they test unmapped tab switching.

---

## Part B: What Was Changed / Tk Hotfix

1. **Inner frame width strictly follows canvas width (`gui/scrollable_tab.py`)**:
   - `_on_canvas_configure` strictly sets `canvas.itemconfigure(window, width=event.width)`.
   - Verified: at 1366px window width, canvas is 1331px, inner is 1331px, treeview is 1290px (< 1331px), zero overflow.

2. **Window sizing from monitor work area (`gui/app.py`, `gui/theme.py`)**:
   - Window opens at ~85% of usable monitor work area (excluding taskbar), centered, minimum 1000×640.
   - Clamped to work area at launch and remembered in `ui.ini` (`window_width`, `window_height`, `window_x`, `window_y`).
   - Window `<Configure>` debounced handler updates `ui.ini` and detects monitor moves via `get_window_dpi(hwnd)`.

3. **Column roles & proportional distribution (`gui/stage3_tab.py`)**:
   - Content-fit columns (`check`, `idx`, `duration`, `start`, `failover`, `status`, `approved`, `engine`): width = `max(header longest line in bold, widest cell text, totals text) + padding`.
   - Remainder shared between `clip` and `speaker` in proportion to their longest text.
   - Font stepping: steps down table font (never below 9pt) if content-fit columns alone exceed width.
   - Exact sum alignment: `sum(col_widths) == viewport_width` with zero horizontal scrollbar.

4. **Totals row short text & fit guarantee (`gui/stage3_tab.py`)**:
   - Displays `"21 of 21 timed"` and `"0 of 0 timed"`.
   - Status column minimum width guaranteed to fit `"21 of 21 timed"` without clipping.

5. **Column edge drag redistribution (`gui/stage3_tab.py`)**:
   - Dragging a column separator redistributes width between neighbouring columns only (`clip_tree.column(c_curr)` and `clip_tree.column(c_neigh)`).
   - Clamps to both columns' minimum widths and preserves total viewport width with zero horizontal scrollbar.

6. **Tab mapping & synchronization (`gui/stage3_tab.py`, `gui/app.py`)**:
   - Bound `<Map>` on `Stage3Tab` and `<<NotebookTabChanged>>` on `self.notebook` to re-fit columns immediately when tab is shown.

---

## Part C: Tk Appearance (System, Light, Dark)

1. **Theme manager with 3 modes (`gui/theme.py`, `gui/app.py`)**:
   - Modes: `"system"` (default), `"light"`, `"dark"`.
   - Persisted in `ui.ini` under `[Display] theme = system`.

2. **Live registry polling (`gui/app.py`)**:
   - Reads `HKCU\Software\Microsoft\Windows\CurrentVersion\Themes\Personalize\AppsUseLightTheme`.
   - Polled every 2000ms (`_poll_system_theme()`). If mode is `"system"` and Windows switches, the app updates live.

3. **In-app themed `View ▾` header dropdown (`gui/app.py`)**:
   - Replaces native OS menu bar with an in-app themed dropdown button in the header bar.
   - Contains: Text size..., Zoom in (Ctrl++), Zoom out (Ctrl+-), Normal size (Ctrl+0), Table density (Compact / Comfortable), Reset layout.

4. **Segmented control in header (`gui/app.py`, `gui/theme.py`)**:
   - Compact `[ System | Light | Dark ]` control in header bar.
   - Fully theme-aware: adapts pill backgrounds, active color, and borders dynamically.

5. **Full dark palette & DWM dark title bar (`gui/theme.py`)**:
   - `#1C1C1E` app bg, `#2C2C2E` cards, `#F5F5F7` text, `#A1A1A6` secondary text, `#98989D` muted text, `#0078D4` primary button.
   - Every color pair verified WCAG AA >= 4.5:1.
   - Top-level HWND resolved via `user32.GetParent(root.winfo_id())` and configured via `DwmSetWindowAttribute(hwnd, 20, byref(c_int(1)), 4)`.

---

## Part D: Qt Carryover

- Added acceptance checks matching Tk hotfix across simulated screen matrix:
  * 1366×768 @ 125%, 1536×864 @ 125%, 1920×1080 @ 100% and 150%, 2560×1440 @ 150%.
  * Invariant header height across 0, 3, 21, 60 rows.
  * Totals row short text `"21 of 21 timed"` and `"0 of 0 timed"`.
  * Status column fit without clipping.
  * Zero horizontal scrollbar.
  * Verified 8/8 tests pass in `tests/test_qt_prototype.py`.

---

## Files Changed

- `gui/theme.py`: Per-Monitor V2 DPI awareness, Win32 work area & DPI helpers, DWM dark title bar, registry theme reader, Light and Dark complete palettes, dynamic theme switching, theme-aware SegmentedControl and ToggleSwitch.
- `gui/ui_settings.py`: Atomic `ui.ini` persistence (`[Display]` and `[ColumnWidths]`), `ui_settings.json` fallback, stripped disallowed keys.
- `gui/scrollable_tab.py`: Canvas inner frame width strictly tied to canvas width (`canvas.itemconfigure(window, width=event.width)`).
- `gui/app.py`: Window geometry from usable monitor work area (~85%, centered, min 1000×640), in-app themed `View ▾` dropdown button, `[ System | Light | Dark ]` segmented control, live 2s system theme polling, `<<NotebookTabChanged>>` re-fit trigger.
- `gui/stage3_tab.py`: Role-based column width fitting, proportional clip/speaker distribution, font stepping down, totals short text ("21 of 21 timed"), column drag redistribution between neighbours, `<Map>` recompute, `refresh_theme()`.
- `gui/stage2_tab.py`: Added `refresh_theme()` for theme changes.
- `gui/qt/components.py`: AdaptiveTable scrollbar guard for high resolutions.
- `tests/test_stage3_tk_table.py`: Parameterized simulated screen matrix tests across 0, 3, 21, 60 rows in light & dark, totals text fit, drag redistribution, window sizing from work area.
- `tests/test_qt_prototype.py`: Expanded screen matrix tests.
- `scripts/capture_tk_stage3_fix.py`: Screenshot capture script for Light and Dark modes.

---

## How It Was Verified

1. **Unit & Integration Tests**:
   - `python -m unittest tests/test_stage3_tk_table.py`: 6/6 tests passed.
   - `python -m unittest tests/test_design_system_v2.py`: 7/7 tests passed.
   - `python -m unittest tests/test_theme_contrast.py`: 4/4 tests passed (all pairs >= 4.5:1).
   - `.\scratch\qt_venv\Scripts\python.exe -m unittest discover -s tests -p "test_qt_prototype.py"`: 8/8 tests passed.
2. **Visual Verification (Screenshots in `scratch/theme_review/`)**:
   - `tk_stage3_light.png`: Light theme with populated event and dummy speaker "Bhatti Vikramarka Mallu", showing complete 2-line headers, no elision, totals row "21 of 21 timed", in-app `View ▾` dropdown, `[ System | Light | Dark ]` segmented control, A-/A+ buttons.
   - `tk_stage3_dark.png`: Full dark theme with dark header, dark title bar, high contrast text, and dark table with status indicators.
   - `tk_stage2_dark.png`: Stage 2 in dark mode.
   - `tk_stage3_0_rows.png`: Stage 3 with 0 rows, showing identical header height and "0 of 0 timed".
3. **Secret Scan**:
   - `python scripts/secret_scan.py`: 0 secrets, tokens, or keys found across all repository files.
