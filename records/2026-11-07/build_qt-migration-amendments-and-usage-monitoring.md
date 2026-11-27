# Build Record: Qt Migration Amendments, Dark Mode, Top Tab Navigation, and Usage Ledger Monitoring

- **Date:** 2026-11-07
- **What was asked:** 
  - Execute Phase U0 audit of cloud metadata/trailers, HTTP headers, token exposure, and nvidia-smi power query.
  - Implement navigation amendment: top tab bar (Stage 2, Stage 3, Usage, Stage 4 (Soon), Stage 5 (Soon)) with top-right settings gear, no left sidebar.
  - Sizing & Adaptive Tables: Window opens at ~85% available screen (min 1100×680) and remembers size/position in `ui.ini`. Tables adapt to screen: content-based minimum widths, proportional stretch on resize, middle-elision on Clip and Speaker text with tooltips, header text wrap, 'Fit columns' action button, and zero horizontal scrollbar across 1100×680, 1280×720, and 1920×1080 at 100% and 150% scales.
  - Rename: Remove rename from Stage 3; retain inline rename in Stage 2 Sessions table with confirmation dialog: "Update N existing transcripts and mark combined files stale?".
  - Voice library & User Data: Default path to `D:\Events\_voice_library\`, add permission noted tick and date per person, update AGENTS.md (`_voice_library` and `_usage` are user data, never committed, ignored by "Scan Events").
  - Dark mode: Light, Dark, and System modes in settings sheet, switching live and persisted in `ui.ini`. Dual palette contrast validator ($\ge 4.5:1$).
  - Usage & Quota Monitoring (Phase U1 & U2):
    - Append-only JSONL ledgers flushed and fsynced on write: per-event (`<event root>\scribe\state\usage\ledger.jsonl`) and account-wide (`D:\Events\_usage\account_ledger.jsonl`).
    - Run, Attempt (with attempt numbers 1, 2, 3), and Quota snapshot records.
    - Background GPU compute sampling via `nvidia-smi` (1 sample/sec during local decoding, energy Wh = sum(power * dt) / 3600, labeled "GPU only, estimated").
    - Transcript version archiving to `scribe\state\history\<clip>\<attempt_id>.txt` on replaced transcripts.
    - Usage tab with Scope filter, Engine filter, throughput cards, quota rate-limit pace, local compute metrics, Run history table, Clip attempt history with "Open this transcript version" button, and QPainter daily bar chart.
    - Stage 3 quiet usage line ("Today: 12 requests · NVIDIA free tier") opening Usage tab, pre-run cloud estimate, right-click "Show history" context menu, fail-safe non-blocking logging.
    - Full test suite passing and screenshots in `scratch\theme_review\`.
- **Why:** 
  - Transition from sidebar navigation to top tab bar while ensuring full responsive table layout and zero horizontal scrollbars.
  - Provide complete observability of cloud rate limits, requests, retries, 429 hits, and local GPU power/energy without blocking runs or leaking credentials.
  - Deliver seamless Light, Dark, and System appearance matching Windows 11 Fluent and Apple human interface standards.

---

## What was built (user-visible behaviour)

### 1. Phase U0 Audit Findings
- **NVIDIA Riva gRPC Endpoints (`grpc.nvcf.nvidia.com:443`)**:
  - `offline_recognize` returns `RecognizeResponse` containing `results` and `id`.
  - Result contains `alternatives`, `channel_tag`, and `audio_processed` (seconds).
  - Alternative contains `transcript`, `confidence`, and `words` (parakeet) or `language_code` (whisper).
  - **Zero token counts or rate-limit/quota fields** are returned in the response payload or gRPC metadata/trailers.
- **NVIDIA NVCF HTTP Headers**:
  - Responses contain standard web headers (`Date`, `Content-Type`, `X-Request-Id`, etc.).
  - **No rate-limit headers** (`x-ratelimit-remaining`, `x-ratelimit-limit`) or balance endpoints are exposed.
  - Free tier operates strictly under a requests-per-minute rate limit. Therefore, the app defaults to **Rate-limit mode** (tracking requests/min against an editable 40 req/min limit, requests today, and 429 hits, with the note *"no credit balance on this plan"*), while supporting manual sync for accounts that have credit balances.
- **Local Whisper Token Exposure**:
  - In `openai-whisper`, each segment in `result['segments']` is a dictionary containing `tokens: List[int]`. Output token count is directly measured as `len(segment['tokens'])`.
  - For Cloud Riva: tokens are tagged `"n/a"`. For Local Whisper: tokens are tagged `"local"`.
- **GPU Power & Utilization (`nvidia-smi`)**:
  - `nvidia-smi --query-gpu=power.draw,utilization.gpu,memory.used --format=csv,noheader,nounits` executes cleanly via argument list.
  - Samples power (W), GPU utilization (%), and VRAM (MB) once per second during local decoding.

### 2. Top Tab Bar Navigation & Adaptive Shell (`gui/qt/main_window.py`)
- Replaced sidebar with clean top tab bar:
  - `Stage 2`
  - `Stage 3`
  - `Usage`
  - `Stage 4 (Soon)`
  - `Stage 5 (Soon)`
  - Top-right `⚙ Settings` button.
- Adaptive Window Opening:
  - Opens at ~85% of available screen geometry (minimum 1100×680).
  - Remembers width, height, and screen position in `%LOCALAPPDATA%\SessionScribe\ui.ini`.

### 3. Adaptive Tables & Responsive Proportions
- In `gui/qt/components.py` (`AdaptiveTable`):
  - Every column in Stage 2 and Stage 3 stays visible with **zero horizontal scrollbar**.
  - Minimum column widths defined from content.
  - Proportional column stretching when window resizes.
  - Middle-elision for Clip and Speaker names (`MiddleElideDelegate`) with hover tooltips displaying full text.
  - Header text wrapping supported.
  - "Fit columns" button in Totals bar recalculates and resets widths proportionally.

### 4. Stage 2 Inline Rename & Confirmation Dialog (`gui/qt/stage2_view.py`)
- Removed speaker rename from Stage 3.
- In Stage 2 Sessions table: double-clicking a speaker cell enables inline editing.
- Modifying a speaker name triggers `RenameSpeakerDialog`:
  *"You are renaming '<Old>' to '<New>'. Update N existing transcripts and mark combined files stale?"*
- Emits `speakerRenamed` signal to immediately synchronize Stage 3.

### 5. Voice Library & Directory Rules (`scribe_core/voice_library.py`, `AGENTS.md`)
- Changed default voice library location to `D:\Events\_voice_library\`.
- Added `permission_noted: bool` and `permission_date: Optional[str]` to `Person` model.
- Updated `AGENTS.md`: `D:\Events\_voice_library\` and `D:\Events\_usage\` are user data, never committed, and `Scan Events` ignores folders starting with an underscore (`_`).
- Updated `gui/stage2_tab.py` `_scan_events_dialog` to ignore folders starting with `_`.

### 6. Dark Mode & Live Palette Switching (`gui/qt/tokens.py`, `ui.ini`)
- Implemented dual palettes:
  - Light: Canvas `#F5F5F7`, Cards `#FFFFFF`, Dividers `#E5E5EA`, Primary `#1D1D1F`, Secondary `#515154`, Accent `#0A64D6`.
  - Dark: Canvas `#1C1C1E`, Cards `#2C2C2E`, Dividers `#3A3A3C`, Primary `#F5F5F7`, Secondary `#A1A1A6` (no darker than `#A1A1A6`), Accent `#0A84FF`.
- Dynamic Settings Sheet with Light, Dark, and System switcher.
- Live switching across all open tabs without application restart.
- Automated WCAG AA contrast check confirms $\ge 4.5:1$ across all text/background pairs in BOTH themes.

### 7. Usage Ledger & Quota Monitoring (`scribe_core/usage_ledger.py`, `gui/qt/usage_view.py`)
- Append-only JSONL ledgers flushed and fsynced on write:
  - Per-event: `<event root>\scribe\state\usage\ledger.jsonl`.
  - Account-wide: `D:\Events\_usage\account_ledger.jsonl`.
- Resilient parsing: Corrupt lines skipped with a warning; reading never fails.
- Replaced transcript archiving: Whenever a transcript is replaced, the prior version is archived to `scribe\state\history\<clip>\<attempt_id>.txt` and linked in the attempt record.
- `LocalComputeSampler`: Background thread reading `nvidia-smi` once per second during decoding, computing average power, peak VRAM, and energy Wh = $\sum (\text{power} \times \Delta t) / 3600$, labeled *"GPU only, estimated"*.
- `rebuild_account_ledger`: Utility function to collate all event ledgers into `account_ledger.jsonl`.
- Usage Tab UI:
  - Scope selector (`This Event` / `All Events`), Engine filter (`All`, `Cloud Riva`, `Local Whisper`), `Export CSV`, and `Open Ledger Folder`.
  - 3 Summary Cards: Throughput & Time, Cloud Requests & Quota, Local Compute (GPU).
  - Quota Card: Rate Limit pace (default 40 req/min, requests today, 429 count, note *"no credit balance on this plan"*), with manual sync support.
  - Run History Table (newest first).
  - Clip Attempt History with attempt numbers (e.g. #1, #2, #3), time taken, engine fallback path, and **"Open Version"** button for archived transcripts.
  - Daily Usage Bar Chart rendered cleanly with `QPainter` for Light & Dark themes.
- Stage 3 Integration:
  - Quiet usage line: *"Today: 12 requests · NVIDIA free tier"* with *"View Usage →"* link jumping to Usage tab.
  - Pre-run cloud estimate banner: shows audio minutes and rate pace or remaining credits.
  - Right-click context menu on clip table: *"Show history for '<clip>'"* jumps to Usage tab filtered to that clip.
  - Fail-safe write: warns once on permissions/disk errors and never blocks transcription.

---

## How to use it

### Launch Main Window (Qt):
```powershell
scratch\qt_venv\Scripts\python.exe gui\qt\main_window.py
```

### Launch Usage Tab standalone:
```powershell
scratch\qt_venv\Scripts\python.exe -c "import sys; from PySide6.QtWidgets import QApplication; from gui.qt.usage_view import UsageView; app=QApplication(sys.argv); v=UsageView(); v.show(); app.exec()"
```

### Run Visual QA & Theme Screenshots Capture:
```powershell
scratch\qt_venv\Scripts\python.exe scripts\capture_theme_screenshots.py
```

---

## Settings and defaults
- **Storage Location:** `%LOCALAPPDATA%\SessionScribe\ui.ini`
- **Defaults:**
  ```ini
  [Display]
  theme = light
  text_scale = 1.0
  table_density = comfortable
  window_width = 1180
  window_height = 840
  quota_mode = rate_limit
  rate_limit_per_min = 40
  ```

---

## Files changed

- **Created:**
  - `scribe_core/usage_ledger.py`: Append-only JSONL usage tracker, local compute sampler, transcript archiver, and account ledger collator.
  - `gui/qt/main_window.py`: Main window shell with top tab navigation, geometry memory, and inter-tab signal routing.
  - `gui/qt/stage2_view.py`: Stage 2 Qt view with adaptive tables, inline rename, and confirmation dialog.
  - `gui/qt/usage_view.py`: Usage and quota monitoring tab with throughput cards, history tables, and QPainter daily chart.
  - `scripts/audit_u0_metadata.py`: Phase U0 metadata/trailers/headers audit probe.
  - `scripts/capture_theme_screenshots.py`: Automated visual QA script capturing 32 screenshots across resolutions, scales, and themes.
  - `tests/test_usage_ledger.py`: Comprehensive tests for JSONL persistence, corruption resilience, attempt numbering, transcript archiving, and security sanitization.
  - `scratch/theme_review/*.png`: 32 captured PNG screenshots in Light and Dark themes.
- **Modified:**
  - `AGENTS.md`: Added rules for `D:\Events\_voice_library\`, `D:\Events\_usage\`, permission noted tick/date, and underscore folder exclusion in "Scan Events".
  - `.gitignore`: Added `_voice_library/`, `_usage/`, and `*.ini`.
  - `scribe_core/voice_library.py`: Updated default directory to `D:\Events\_voice_library`, path validator, and `Person` permission noted/date.
  - `gui/stage2_tab.py`: Updated `_scan_events_dialog` to ignore folders starting with `_`.
  - `gui/qt/tokens.py`: Implemented dual Light & Dark palettes, `get_theme_palette()`, `ui.ini` persistence, and dual-theme WCAG AA validator.
  - `gui/qt/components.py`: Updated `Card`, `SegmentedControl`, `ToggleSwitch`, buttons, pills, `AdaptiveTable`, `TotalsBar`, and `SettingsSheet` for Light and Dark themes.
  - `gui/qt/stage3_view.py`: Added quiet usage line, pre-run estimate, right-click "Show history", removed speaker rename, integrated `AdaptiveTable`, and Light/Dark support.
  - `tests/test_voice_library.py`: Added tests for `_voice_library` path validation and permission noted fields.
  - `tests/test_qt_prototype.py`: Added tests for dual-theme contrast, `ui.ini` persistence, and top-tab navigation.

---

## Limitations and known issues
- **PySide6 Isolation:** PySide6 remains isolated in `scratch\qt_venv\` pending final approval to merge into shared venv.
- **Provider Quota Header Absence:** NVIDIA Riva and NVCF APIs do not return rate limit or remaining quota headers in gRPC trailers or HTTP response headers; rate-limit pace is tracked client-side against the user-configured limit (default 40 req/min).

---

## How it was verified

1. **Phase U0 Live Network & System Probes:**
   - Probed `grpc.nvcf.nvidia.com:443` for parakeet-tdt and whisper-large-v3: confirmed response fields (`audio_processed`, `words`, `transcript`) and lack of server-side quota headers.
   - Probed NVCF HTTP endpoints: logged all header names, confirmed no rate limit/quota headers.
   - Probed local whisper: confirmed `len(segment['tokens'])` exposes exact segment token counts.
   - Probed `nvidia-smi`: confirmed power (W), GPU utilization (%), and VRAM (MB) query works via argument list.
2. **Automated WCAG AA Contrast QA:**
   - Evaluated 13 token pairs in Light mode: **100% PASS** ($\ge 4.5:1$).
   - Evaluated 13 token pairs in Dark mode: **100% PASS** ($\ge 4.5:1$).
3. **Adaptive Table & Zero Horizontal Scrollbar QA:**
   - Evaluated across resolutions: 1100×680, 1280×720, 1920×1080.
   - Evaluated across scales: 100% and 150%.
   - Evaluated in Light and Dark themes.
   - Result: **0 horizontal scrollbars** across all configurations.
4. **Screenshot Artifacts:**
   - 32 PNG images saved into `scratch\theme_review\` for Stage 2, Stage 3, Usage, Rename Dialog, and Settings Sheet.
5. **Unit Test Suite:**
   - `tests/test_usage_ledger.py`: 6 passed in 0.42s.
   - `tests/test_qt_prototype.py`: 5 passed in 3.14s (PySide6) / 3 passed, 2 skipped (standard venv).
   - `tests/test_voice_library.py`: 16 passed.
   - Overall suite: 41 passed, 2 skipped, 0 failures.

---

## How to roll it back
- Revert modifications to `AGENTS.md`, `.gitignore`, `scribe_core/voice_library.py`, `gui/stage2_tab.py`, and `gui/qt/`.
- The Tkinter application remains 100% functional and untouched.

