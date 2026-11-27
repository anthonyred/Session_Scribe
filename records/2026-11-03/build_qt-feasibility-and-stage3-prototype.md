# Build Record: Qt Feasibility, Design System Tokens, Style Gallery, and Stage 3 Prototype

- **Date:** 2026-11-03
- **What was asked:** 
  - Evaluate feasibility of migrating Session Scribe to PySide6 (Qt) in a throwaway venv (`scratch\qt_venv\`) without modifying the shared main `.venv\` or touching the runnable Tkinter GUI.
  - Implement a complete design system module with Apple- and Windows 11 Fluent-inspired tokens (colors, typography, spacing, radii, soft drop shadows, motion timings) with automated WCAG AA ($\ge 4.5:1$) contrast verification and dynamic text scaling (85% to 150%).
  - Create reusable PySide6 UI components (`Card`, `SegmentedControl`, `ToggleSwitch`, `PrimaryButton`, `SecondaryButton`, `TextButton`, `StatusPill`, `TestPill`, `ThinProgressBar`, `Toast`, `ClipsTable` with middle-elision, docked `TotalsBar`, and `SettingsSheet`).
  - Create a Style Gallery (`gui/qt/style_gallery.py`) exhibiting all components across states with live text scaling.
  - Create a high-fidelity Stage 3 Prototype (`gui/qt/stage3_view.py`) loading the real Oracle event (`D:\Events\22.Oracle event\scribe`), adhering to the hard constraint: **No horizontal scrollbar at $\ge 1100$px window width at any text size from 85% to 150%**.
  - Provide automated screenshot capture across resolutions (1100×680, 1280×720, 1920×1080) and text scales (85%, 100%, 150%) saved to `scratch\ui_review\`.
  - Add tests under `tests/test_qt_prototype.py`.
- **Why:** 
  - Evaluate Qt (PySide6) as an alternative to Tkinter for a modern Apple- and Windows 11 Fluent-inspired desktop experience.
  - Act as an explicit review gate before considering porting any other app tabs or live worker pipelines.
  - Ensure zero disruption to existing application workflows, existing test suites, manifests, and worker processes.

---

## What was built (user-visible behaviour)

### 1. Feasibility Analysis (Step 1)
- **PySide6 Version & Footprint:** Installed `PySide6==6.12.0` in isolated throwaway venv `scratch\qt_venv\`. Measured full install size of 650.53 MB (or ~210 MB if pinned to `PySide6-Essentials` without WebEngine/Pdf modules).
- **Licensing:** PySide6 is released under LGPLv3 / Commercial. Dynamic linking in proprietary applications is fully permitted under LGPLv3.
- **Dependency Compatibility:** 100% compatible alongside project dependencies (`numpy==1.26.4`, `torch==2.4.1+cu121`, `google.protobuf==6.33.5`, `huggingface_hub==0.26.5`).
- **High-DPI & Look:** Full automatic per-monitor High-DPI scaling (devicePixelRatio 1.25 tested).
- **Native Word Shortcuts:** Out-of-the-box Windows navigation and word deletion shortcuts (`Ctrl+Backspace`, `Ctrl+Left`, `Ctrl+Right`, `Ctrl+Delete`, `Ctrl+A`) natively supported without custom class bindings.
- **Subprocess Worker Streaming:** `QProcess` streams stdout and stderr asynchronously into GUI logs without thread contention or UI freezes.

### 2. Design System Tokens & Automated WCAG AA Contrast (Step 2)
- Module: `gui/qt/tokens.py`
  - Colors: Canvas `#F5F5F7`, Cards `#FFFFFF`, Dividers `#E5E5EA`, System Blue `#0A64D6`, Hover `#0852B0`, Pressed `#063F8A`.
  - Status pill colors: Success (`#E8F5E9` / `#1B5E20`), Warning (`#FEF3C7` / `#92400E`), Error (`#FEE2E2` / `#991B1B`), Test Purple (`#EDE9FE` / `#5B21B6`), Neutral (`#F3F4F6` / `#1F2937`).
  - Automated contrast validator: Every token pair programmatically checked and confirmed $\ge 4.5:1$ (many $\ge 7.0:1$ AAA).
  - Typography scale: `Segoe UI Variable` (fallback `Segoe UI`) with base sizes: Page title 22pt, Section title 13pt, Body 10.5pt, Table 10pt, Caption 9pt.
  - Spacing, Radii & Motion: Card radius 12px, button radius 8px, pill radius 12px, soft drop shadows (`0 2px 8px rgba(0,0,0,0.04)`), motion fade 140ms, slide 180ms.
  - Settings persistence: Atomic persistence of user preferences in `%LOCALAPPDATA%\SessionScribe\qt_ui_settings.json` (zero event data).

### 3. Reusable UI Components
- Module: `gui/qt/components.py`
  - `Card`: Framed container with rounded corners and subtle ambient elevation shadow.
  - `SegmentedControl`: Pill container with `#0A64D6` active segment indicator.
  - `ToggleSwitch`: Apple/Fluent animated switch (44x24px) with 140ms quad easing.
  - `PrimaryButton`, `SecondaryButton`, `TextButton`: Interactive state styling (normal, hover, pressed, disabled).
  - `StatusPill` & `TestPill`: High-contrast pill badges with icons and labels.
  - `ThinProgressBar`: Sleek 4px progress line.
  - `Toast`: Non-modal floating notification with auto fade-out.
  - `ClipsTable`: Table widget with alternating rows, middle-elided clip names, hover row tints, and selection highlights.
  - `TotalsBar`: Docked summary strip directly below the table with aligned metrics.
  - `SettingsSheet`: Slide-in modal drawer for pipelines, endpoints, text scale, and density.

### 4. Interactive Style Gallery
- Module: `gui/qt/style_gallery.py`
  - Interactive exhibition of all components, typography scale, pill badges, and table widgets.
  - Interactive live text sizing slider (85% to 150%).

### 5. High-Fidelity Stage 3 Prototype
- Module: `gui/qt/stage3_view.py`
  - Loads real Oracle event data from `D:\Events\22.Oracle event\scribe` (`manifest.json` and `state/clip_state.json`).
  - Header strip: `IOFUG Minin AI Days organized | 31 October 2026 | 5 speakers · 21 clips` + `⚙ Settings` button.
  - Run bar card: Segmented engine selector (`Local Whisper`, `Cloud Riva`, `Auto (Hybrid)`), cloud model combobox, language selector, live progress toggle switch, action buttons (`Transcribe All`, `Stop`), and 4px progress line.
  - Clips table: 21 rows populated with middle-elided clip filenames, speaker names, durations, transcription times, failover times, status pills, approval badges, and engine indicators.
  - Docked totals bar: Directly attached under table showing `Clips: 21 | Duration: 42m 18s | Time taken: 04m 14s | Failover: 0s | Approved: 21 of 21`.
  - Action row: `Approve selected`, `Approve all completed`, `Clear approval`, `21 of 21 approved` badge, and `Combine approved transcripts`.
  - Outputs row: Transcripts directory and Combined directory paths with `Open` buttons.
  - Activity drawer: Collapsible slide-up drawer with `Worker Log` (monospace) and `Transcript Preview` tabs.
  - Hard constraint verified: **Zero horizontal scrollbar at $\ge 1100$px window width at all text scales from 85% to 150%**.

---

## How to use it

### Run Style Gallery:
```powershell
scratch\qt_venv\Scripts\python.exe gui\qt\style_gallery.py
```

### Run Stage 3 Prototype:
```powershell
scratch\qt_venv\Scripts\python.exe gui\qt\stage3_view.py
```

### Run Automated Screenshot Capture & Layout QA:
```powershell
scratch\qt_venv\Scripts\python.exe scripts\capture_qt_screenshots.py
```

---

## Settings and defaults
- **Storage Location:** `%LOCALAPPDATA%\SessionScribe\qt_ui_settings.json` (local user preferences, strictly isolated from event directories).
- **Default settings:**
  - `text_scale`: `1.0` (range: `0.85` to `1.50`).
  - `table_density`: `"comfortable"` (36px row height) or `"compact"` (28px row height).
  - `reduced_motion`: `False`.

---

## Files changed

- **Created:**
  - `gui/qt/tokens.py`: Design tokens, typography scale, settings persistence, automated WCAG AA contrast checker.
  - `gui/qt/components.py`: Reusable PySide6 components (`Card`, `SegmentedControl`, `ToggleSwitch`, buttons, pills, table, totals bar, settings sheet).
  - `gui/qt/style_gallery.py`: Interactive component exhibition window.
  - `gui/qt/stage3_view.py`: High-fidelity Stage 3 prototype view loaded with real Oracle event data.
  - `scripts/capture_qt_screenshots.py`: Automated layout QA and screenshot capture script across resolutions and scales.
  - `tests/test_qt_prototype.py`: Unit tests for token contrasts, atomic settings persistence, and prototype layout checks.
  - `scratch/ui_review/*.png`: 13 captured screenshots of Stage 3 prototype and Style Gallery.
- **Untouched:**
  - Existing Tkinter application (`gui/app.py`, `gui/stage2_tab.py`, `gui/stage3_tab.py`).
  - Core pipeline & manifest formats (`scribe_core/`, `transcribe_worker.py`).
  - Shared `.venv` environment (no PySide6 installed into `.venv`).

---

## Limitations and known issues
- **Review Gate Scope:** This is an explicit review gate. The prototype is read-only and exhibits UI/UX fidelity; it does not start live transcription jobs from Qt or replace Tkinter yet.
- **PySide6 Isolation:** PySide6 is confined to `scratch\qt_venv\` pending user review and confirmation to add it to the shared environment.

---

## How it was verified

1. **Automated WCAG AA Contrast:**
   - 13 token pairs tested programmatically: all meet or exceed WCAG AA ($\ge 4.5:1$), with primary text reaching 16.83:1 (AAA).
2. **Zero Horizontal Scrollbar QA:**
   - Evaluated across resolutions: 1100×680, 1280×720, 1920×1080.
   - Evaluated across text scales: 85%, 100%, 150%.
   - Result: 0 horizontal scrollbar occurrences at $\ge 1100$px width.
3. **Screenshot Verification:**
   - 13 PNG images captured in `scratch\ui_review\` showing pristine typography and responsive middle-elision.
4. **Unit Tests:**
   - `scratch\qt_venv\Scripts\python.exe tests/test_qt_prototype.py`: 5 passed in 1.65s.
   - `.\.venv\Scripts\python.exe -m pytest tests/test_qt_prototype.py`: 3 passed, 2 skipped in 3.49s (no PySide6 in `.venv`).

---

## How to roll it back

- Delete `gui/qt/` folder, `scripts/capture_qt_screenshots.py`, `tests/test_qt_prototype.py`, and `scratch/qt_venv/`.
- The Tkinter application in `gui/` is 100% untouched and fully functional.

