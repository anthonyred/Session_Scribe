# Build Record: Qt Stage 3 Redesign, Appearance Control, and CSV Usage Logging

- **Date:** 2026-11-07
- **What was asked:**
  - Provide Step 0 status report on `ui-qt-migration`, throwaway venv, and Tk build state.
  - Part A: Confirm root cause of Tk table cut-offs (headers, columns, dragging).
  - Part B: Stage 3 table and panes redesign in Qt:
    - Column roles: Fixed (`#` with "T" badge), Content-fit (`Duration`, `Time taken for transcription`, `Failover time`, `Status`, `Approval`, `Engine`), Flexible (`Clip`, `Speaker`).
    - Width algorithm: Content-fit width from `QFontMetrics` (max cell text, longest header word + padding); flexible columns share remainder; Clip gets full filename need first, then Speaker; dynamic text reduction before eliding; zero horizontal scrollbar.
    - Multi-line wrapping headers up to 3 lines with computed header height.
    - Cell formatting: Status and Approval pills ("✓ Done", "✓ Approved"), Engine icon + short name, Totals row pinned directly under table and aligned to columns.
    - Column dragging moving width between neighbouring columns preserving total viewport width; double-click auto-fit; "⋯" menu (Fit columns, Reset widths); A−/A+ table text zoom (85% to 130%).
    - Panes: Top header with Appearance control, compact controls card (Engine, Model, Language, Save progress switch, Cloud today caption, Settings gear), test-gate stepper with one primary action, clips table, actions bar, compact outputs row.
    - Bottom drawer: Collapsed by default; summary on handle when row selected ("Attempt 3 · 00:00:07 · Parakeet · 2 requests"); expanded takes at most 40% height (table keeps at least 6 rows); tabs: "Selected clip" (attempts table with "Open this version", Open CSV, Open folder), "Log", and "Transcript preview".
  - Part C: Appearance (Light, Dark, System) in header bar, settings sheet, and View menu; live switching; System follows Windows mode via Qt styleHints `colorSchemeChanged` / Windows registry; dual-palette WCAG AA >= 4.5:1 contrast tests.
  - Part D: CSV-based usage logging replacing earlier usage tab plan:
    - Park Usage tab, credits card, and power/energy sampling into `records/registers/IDEAS.md`.
    - CSV files: `<event>\scribe\usage\runs.csv`, `attempts.csv`, and `D:\Events\_usage\account_attempts.csv`.
    - UTF-8 with BOM (`utf-8-sig`) for Excel; fixed schema; IST timestamps with offset; both seconds and `hh:mm:ss`.
    - Excel lock resilience with local spool queue in `usage\.pending\`.
    - Rolling 60s window rate limit tracking (default 40 rpm, 80% pace warning, 429 count).
    - Transcript version archiving to `scribe\state\history\<clip>\<attempt_id>.txt`.
    - Stage 3 display: "Selected clip" tab showing attempts, combined multi-selection totals, main table showing latest attempt and "N attempts" tooltip.
  - Part E: Root `README.md` user guide for non-technical users, screenshots in `docs/images/`, and update `AGENTS.md`.
- **Why:**
  - Eliminate all layout squashing and cut-offs in the transcription view.
  - Provide a clean, Apple- and Windows 11 Fluent-inspired desktop interface with zero horizontal scrollbars.
  - Deliver reliable, Excel-compatible usage logging that never loses rows or blocks transcription runs when files are open in external programs.

---

## What was built (user-visible behaviour)

### 1. Step 0 Status & Part A Root Cause
- **Git Branch Status:** Ref lock restrictions on `.git/refs/heads/` in the sandbox keep the repository on `fix-event-defaults`. The PySide6 migration codebase resides cleanly in `gui/qt/` without commits.
- **Throwaway Environment:** PySide6 6.12.0 is installed and verified in `scratch/qt_venv/`.
- **Tk Build Differences:** The legacy Tk app still has the old layout ("Rename Speaker...", A−/A+ text buttons, and "Cloud uploaded this session").
- **Part A Root Cause Analysis:**
  In `gui/stage3_tab.py`:
  1. Column widths sum to 1080 px (`check: 45`, `idx: 35`, `clip: 220`, `speaker: 130`, `duration: 80`, `start: 150`, `failover: 100`, `status: 110`, `approved: 90`, `engine: 120`). Inside the tab container margins at 1100 px or 1280 px, the available space is smaller than 1080 px, but the horizontal scrollbar was removed, pushing rightmost columns out of view.
  2. The multi-line header strings (`Time taken\nfor transcription`, `Failover\ntime`) are placed in `ttk.Treeview` headings, which default to a fixed single-line height (~22 px) in the clam theme. The second line is cut off vertically.
  3. Column dragging in ttk Treeview fails to sync totals row widths (only synced on manual method call) and dragging dividers against a fixed-width container crushes rightmost columns.

### 2. Stage 3 Redesigned Table & Panes (`gui/qt/stage3_view.py`)
- **Column Roles & Adaptive Layout:**
  - Fixed `#`: Column 0 renders index with a styled blue "T" badge for test clips.
  - Content-fit: `Duration`, `Time taken for transcription`, `Failover time`, `Status`, `Approval`, `Engine`.
  - Flexible: `Clip Filename` (9.5 pt font), `Speaker` (10 pt font).
- **Width Algorithm (`AdaptiveTable.adapt_columns_to_width`):**
  - Content-fit widths are calculated from `QFontMetrics` based on longest header word and widest cell text + padding.
  - Flexible columns share the remaining viewport. `Clip` receives what full filenames need first, then `Speaker`.
  - Both elide with "…" and tooltip when window space is constrained.
  - Font size dynamically steps down (never below 9 pt) if minimum window size is tight.
  - All columns always fit within the viewport with zero horizontal scrollbars.
- **Multi-Line Wrapping Header (`MultiLineHeaderView`):**
  - Wraps up to 3 lines with dynamic height computation (`needed_height = 3 * line_height + padding`). Headers like "Time taken for transcription" and "Failover time" are fully visible.
- **Locked Column-Aligned Totals Row (`TotalsBar`):**
  - Synchronizes cell widths to match `table.columnWidth(c)` exactly.
  - Concise labels: `Total`, `21 clips`, `5 speakers`, `07:42`, `21 of 21 timed`, `0s`, `21 Done`, `21 Approved`.
- **Resizing & Drag Redistribution:**
  - Dragging a column divider moves width between the neighbouring columns, keeping the total viewport sum constant.
  - Minimum widths are strictly enforced.
  - Double-clicking a divider auto-fits the column.
  - Small "⋯" menu provides "Fit columns" and "Reset widths".
  - Small `A−` / `A+` buttons beside the table zoom table text from 85% to 130%.
- **Top Panes:**
  - Header: Event name, date, speakers count, clips count, and compact `AppearanceControl` (`Light`, `Dark`, `System`).
  - Compact Controls Card: `Engine` segmented control, `Model` dropdown, `Lang` dropdown, `Save progress (2m)` switch, `Cloud today` caption, `⚙ Settings` button.
  - Test-Gate Stepper: 3-step indicator (`Run Test Clips` -> `Review & Approve` -> `Transcribe All`) with a single dynamic primary button.
  - Actions Bar: `Approve selected`, `Approve all completed`, `Clear approval`, counter, `Combine approved transcripts`.
  - Compact Outputs Row: Middle-elided paths for `Transcripts` and `Combined` with `Open folder` buttons.
- **Bottom Activity Drawer:**
  - Collapsed by default with handle arrow `▶ Activity Drawer`.
  - When collapsed and a row is selected: shows one-line summary on the handle (e.g. `Attempt 1 · 00:00:12 · Parakeet · 2 requests`).
  - When expanded (`▼`): takes at most 40% height, keeping at least 6 table rows visible.
  - Tabs:
    - `Selected clip`: Shows all historical attempts for the selected clip (newest first) in an adaptive table with `Attempt`, `Started`, `Time taken`, `Engine`, `Outcome`, `Requests`, `Retries`, `429s`, `Tokens`, `Failover`, and an `Open version` button. With multiple clips selected, displays a combined totals summary. Top buttons: `Open usage CSV`, `Open usage folder`.
    - `Log`: Worker logging output.
    - `Transcript preview`: Read-only view of the clip's transcript text.

### 3. Appearance & Theme Engine (`gui/qt/tokens.py`, `main_window.py`)
- Three modes: `Light`, `Dark`, and `System` (default).
- `AppearanceControl` segmented control placed in the header bar and wired to live stylesheet reloads without restarting the app.
- System mode checks `QGuiApplication.styleHints().colorScheme()` and connects to `colorSchemeChanged` signal, with fallback to Windows registry `AppsUseLightTheme`.
- All UI components (pills, cards, tables, drawer, buttons) transition seamlessly between palettes.
- Automated WCAG AA contrast tests confirm all 13 token pairs $\ge 4.5:1$ in both Light and Dark themes.

### 4. CSV Usage Logging & Spooling Resilience (`scribe_core/usage_csv.py`)
- Parked the standalone Usage tab, credits card, and in-app GPU power/energy sampling into `records/registers/IDEAS.md`.
- Output Files:
  - `<event>\scribe\usage\runs.csv`
  - `<event>\scribe\usage\attempts.csv`
  - `D:\Events\_usage\account_attempts.csv` (cloud attempts only)
- Format: UTF-8 with BOM (`utf-8-sig`) for Excel compatibility.
- Standard ISO-8601 timestamps in IST with `+05:30` offset.
- Both seconds (`time_taken_s`) and `hh:mm:ss` (`time_taken_hms`) recorded.
- **Excel Locking Resilience:**
  - When an append fails because Excel holds an exclusive file lock, rows are written to `.pending/<target>_<uuid>.json`.
  - Emits one-time notice callback: *"Usage CSV is open elsewhere. N rows waiting"*.
  - Automatically flushes pending spool when the file is freed or at application startup.
- **Rate Limit Tracking:**
  - Rolling 60-second window tracking request timestamps against 40 req/min limit.
  - Pacing check alerts when pace reaches 80% (32 rpm) or when a 429 status is hit.
- **Transcript Archiving:**
  - When a transcript is replaced, the prior version is archived to `scribe/state/history/<clip>/<attempt_id>.txt` and linked in `archived_transcript_path`.

### 5. Documentation & README
- Comprehensive root `README.md` created in plain language for non-technical users.
- Sections: Overview, Requirements & Preflight, Starting, Quick Start, Folder Map, Local vs Cloud, API Keys, Status & CSVs, Shortcuts, Troubleshooting, Glossary, Privacy, and Last Verified.
- Reference screenshots generated with dummy data in `docs/images/` and full test matrix (48 screenshots) in `scratch/theme_review/`.
- Updated `AGENTS.md` with rule requiring `README.md` maintenance and "Last verified" updates.

---

## How to use it

1. Launch the Qt application:
   ```powershell
   .\scratch\qt_venv\Scripts\python.exe -m gui.qt.main_window
   ```
2. Navigate via the top tab bar: `Stage 2`, `Stage 3`, `Stage 4 (Soon)`, `Stage 5 (Soon)`.
3. Switch theme live via the `☀ Light` / `🌙 Dark` / `💻 System` segmented control in the top-right header.
4. Drag column dividers in Stage 3 to redistribute space; double-click dividers to auto-fit; use `A−` / `A+` to scale text.
5. Click `▶ Activity Drawer` to expand the drawer and inspect attempt history in the `Selected clip` tab. Click `Open usage CSV` to review `attempts.csv` in Excel.

---

## Files changed

- `gui/qt/components.py`: MultiLineHeaderView, Stage3TableDelegate, AdaptiveTable width algorithm, TotalsBar column synchronization, AppearanceControl.
- `gui/qt/stage3_view.py`: Complete Stage 3 redesign, compact controls, test stepper, drawer with Selected clip attempts table, CSV integration.
- `gui/qt/main_window.py`: Top tab bar (4 tabs), top header appearance selector, View menu, system scheme change listener.
- `gui/qt/tokens.py`: Qt styleHints system mode detection, default theme to `system`.
- `scribe_core/usage_csv.py`: CSV tracker, UTF-8 BOM, spool queue, rate limit rolling tracker, transcript archiving.
- `README.md`: Non-technical user guide.
- `AGENTS.md`: Added section 8 for user documentation maintenance.
- `records/registers/IDEAS.md`: Parked standalone Usage tab, credits card, and in-app GPU power sampling.
- `tests/test_usage_csv.py`: Unit tests for CSV writing, Excel locking, attempt numbering, and archiving.
- `tests/test_qt_prototype.py`: Tests for width algorithm, simulated drag, appearance switching, and contrast.
- `scripts/capture_stage3_screenshots.py`: Automated screenshot generator with dummy data.

---

## How it was verified

1. **Unit Tests (Standard .venv):**
   ```powershell
   .\.venv\Scripts\python.exe -m pytest tests/test_usage_csv.py tests/test_usage_ledger.py tests/test_voice_library.py
   ```
   *Result:* 27 passed in 31.13s.
2. **Qt Prototype Tests (qt_venv):**
   ```powershell
   .\scratch\qt_venv\Scripts\python.exe -m pytest tests/test_qt_prototype.py tests/test_usage_csv.py
   ```
   *Result:* 12 passed in 10.69s.
3. **Visual QA & Screenshots:**
   48 screenshots captured in `scratch/theme_review/` and 4 reference images in `docs/images/` across 1100×680, 1280×720, and 1920×1080 at 100% and 150% scales in Light and Dark modes.
4. **Excel Lock & Spooling:**
   Simulated file lock with active write, verified spooling to `.pending/`, verified notice callback, and confirmed clean flush upon unlock with zero data loss.
5. **Security Scan:**
   Verified 0 API keys, bearer tokens, or audio bytes in any CSV, log, or screenshot.

---

## How to roll it back

- Revert changes to `gui/qt/`, `scribe_core/usage_csv.py`, and `README.md`.
- Tkinter application (`gui/stage3_tab.py` and `gui/app.py`) was preserved and remains runnable without modification.

