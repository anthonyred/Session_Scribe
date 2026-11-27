# Build: Qt Stage 3 Live Telemetry, Usage Tab Integration, and Preflight Visibility

## Date
2026-11-17

## What was asked
1. Carry over cloud preflight, 5-stage test transcription, and failover visibility from the Tk hotfix into the PySide6 Qt GUI (`gui/qt/`).
2. Integrate the **Usage** tab into `SessionScribeMainWindow` top navigation bar (`Stage 2`, `Stage 3`, `Usage`, `Stage 4 (Soon)`, `Stage 5 (Soon)`).
3. Add `QtVaultChip` to the application header for one-click Voice Vault status display (`🔒 Setup Vault`, `🔒 Vault Locked`, `🔓 Vault Unlocked`) and masked unlocking/initialization dialogs.
4. Implement live table ticking telemetry and persistent failover visibility directly in the Qt Stage 3 view (`gui/qt/stage3_view.py`):
   - "Test transcription (5s)" button running bundled sample across 5 stages and opening `QtTestTranscriptionDialog`.
   - "Test connection" button and live `Key found: yes/no (source)` status pill.
   - Automatic 10-minute cached preflight check on transcription runs.
   - Persistent `failover_banner` displayed directly above the table with warning text and dismiss button.
   - Live ~2 Hz monotonic table ticking for active clip rows (`● Transcribing {pct}%`, ticking `hh:mm:ss`, engine fallback indication, failover timer, diagnostic tooltips).
   - Dynamic pinned totals row (`Total (21 clips) · Run hh:mm:ss · N of M done · ETA ~hh:mm:ss (est.)`).
   - Auto-scroll to running row with 10-second manual scroll override freeze.
5. Verify zero horizontal scrollbars across screen sizes (1100×680, 1280×720, 1920×1080) and text scales (85%, 100%, 150%).

## Why
To ensure the modern PySide6 Qt desktop migration achieves complete feature parity with the production Tkinter application, providing real-time transcription monitoring, failover diagnostics, biometric voice vault controls, and multi-event usage tracking.

## What was built (user-visible behaviour)
1. **Top Tab Bar Navigation:** The Qt main window now features 5 tabs: **Stage 2**, **Stage 3**, **Usage**, **Stage 4 (Soon)**, and **Stage 5 (Soon)**. Switching to **Usage** displays daily audio minutes, cloud requests bar charts, run history, and archived transcript versions.
2. **Voice Vault Header Chip (`QtVaultChip`):** Placed in the top-right header next to Appearance and Settings. Shows `🔒 Setup Vault` if uninitialized, `🔒 Vault Locked` when locked, or `🔓 Vault Unlocked` when open. Clicking opens clean masked passphrase dialogs with live strength evaluation.
3. **Connection & Preflight Controls:**
   - `Key found: yes/no (source)` chip showing key presence and origin (`vault`, `env`, or `.env`).
   - `Test transcription (5s)` button executing a 5-stage check with the bundled 16kHz WAV sample and opening `QtTestTranscriptionDialog` showing exact stage timings and PASS/FAIL badges.
   - `Test connection` button testing gRPC connectivity with `grpc.nvcf.nvidia.com:443`.
4. **Persistent Failover Banner:** When a cloud error occurs (or during fallback), a persistent warning banner appears above the clips table displaying the error class and fallback message with a dismiss (`✕`) button.
5. **Live In-Table Telemetry & Totals:** Running clip rows tick live in `hh:mm:ss` with `● Transcribing {pct}%`. The locked totals bar dynamically updates elapsed run time and remaining clip ETA without layout shift or jitter.
6. **Zero Horizontal Scrollbar Guarantee:** Verified across 1100×680, 1280×720, and 1920×1080 at 85%, 100%, and 150% scales with automated screenshot captures saved to `scratch/ui_review/`.

## How to use it
1. Run the Qt application:
   ```powershell
   scratch\qt_venv\Scripts\python.exe gui\qt\main_window.py
   ```
2. In Stage 3, click **Test transcription (5s)** to run the 5-stage preflight check.
3. Click **Run Test Clips** or **Transcribe All** to start live transcription and observe the ticking table rows and totals bar.
4. Switch to the **Usage** tab in the top tab bar to review cloud requests, daily audio minutes, and attempt history.

## Settings and defaults
- **Rate Limit Default:** 40 requests/min (NVIDIA free tier pace).
- **Preflight Cache:** 10 minutes TTL (`PREFLIGHT_CACHE_TTL_SEC = 600.0`).
- **Auto-Scroll Override:** 10 seconds freeze after user scrolls table.
- **Window Minimum Geometry:** 1100×680, defaults to ~85% available display.

## Files changed
- `gui/qt/components.py`: Added `QtTestTranscriptionDialog`, `QtUnlockVaultDialog`, `QtInitializeVaultDialog`, and `QtVaultChip`. Enhanced `TotalsBar.update_totals` with `run_status_text`.
- `gui/qt/main_window.py`: Added **Usage** tab to `tab_bar` and `stack`, integrated `QtVaultChip` in header bar, wired theme switching to `UsageView`.
- `gui/qt/stage3_view.py`: Added `Key found: yes/no` chip, `Test transcription (5s)` and `Test connection` buttons, `failover_banner` above table, live ~2 Hz monotonic timer, ETA calculations, and preflight check gate.
- `gui/qt/usage_view.py`: Added `account_ledger_path` parameter to `UsageView.__init__` for isolated testability.
- `scripts/capture_qt_screenshots.py`: Updated table theme styling and column adaptation calls.
- `tests/test_qt_prototype.py`: Updated tab count assertion to 5 tabs, added tests for failover banner, live table ticking, and `QtTestTranscriptionDialog`.

## Limitations and known issues
- PySide6 remains installed in `scratch/qt_venv/` as an isolated review prototype until approved for addition to the main `.venv/`.
- Real gRPC network calls require network connectivity and outbound access to `grpc.nvcf.nvidia.com:443`.

## How it was verified
1. **Qt Prototype Automated Tests (11/11 Passed):**
   ```powershell
   scratch\qt_venv\Scripts\python.exe tests/test_qt_prototype.py
   ```
   *Result:* 11 tests ran in 5.52s, 0 failures, 0 errors.
2. **Design System & Contrast Automated Tests (11/11 Passed):**
   ```powershell
   scratch\qt_venv\Scripts\python.exe tests/test_design_system_v2.py
   scratch\qt_venv\Scripts\python.exe tests/test_theme_contrast.py
   ```
   *Result:* 100% passed, all color pairs meet WCAG AA $\ge 4.5:1$.
3. **Automated Visual QA & Screenshot Matrix (13 Screenshots):**
   ```powershell
   scratch\qt_venv\Scripts\python.exe scripts/capture_qt_screenshots.py
   ```
   *Result:* 0 horizontal scrollbar violations across 1100×680, 1280×720, and 1920×1080 at 85%, 100%, and 150% scales.
4. **Secret Scan Verification:**
   ```powershell
   D:\Session_Scribe\.venv\Scripts\python.exe scripts/secret_scan.py
   ```
   *Result:* 0 secrets detected across all repository files.

## How to roll it back
Revert changes to `gui/qt/` and `tests/test_qt_prototype.py` using git checkout.

