# Feature Record: Design System v2 & Multi-Speaker Diarization Pipeline

- **Date**: 2026-11-03
- **What was asked**:
  1. **Task A: Design System v2 (All Tabs)**: Evaluate Windows 11-style ttk theme (`sv-ttk`) in a throwaway venv; adopt a calm, modern design system with Segoe UI Variable fonts (fallback Segoe UI), Consolas for paths/logs, strict type scale, 8px grid spacing, high contrast WCAG AA compliance, user-controlled UI text size slider (85% to 150%) with View menu shortcuts and header `A−`/`A+` buttons, table density toggle (Compact 24px / Comfortable 30px), persistent settings in `%LOCALAPPDATA%\SessionScribe\ui_settings.json`, remembered column widths, modern `SegmentedControl` and `ToggleSwitch` widgets, improved transcript viewer layout, and capture theme screenshots across all tabs.
  2. **Task B: Stage 2 Changes**: Replace speaker mode radiobuttons with segmented control (`One speaker | Multiple speakers`) per event with session overrides in the Sessions table (`Mode` column), and add a `Roster…` button opening a multi-speaker roster configuration dialog.
  3. **Task C: Stage 3 Cleanup**: Report root cause for why Event Folder box, Load Manifest, and Reload buttons failed and remove them; replace with a clean one-line event summary and "Go to Stage 2" navigation button; compact toolbar for Rename Speaker and Combine transcripts; compact Output row with `Transcripts:` and `Combined:` folder buttons; narrow language selector; engine segmented control and quiet progress toggle; collapsible Advanced and Connections sections; proposed Windows Credential Manager option; clip table styling with 10pt text, 2-line wrapped headers, and aligned totals row.
  4. **Task D: Multi-Speaker Mode (Host + Speakers per Video)**: Role-based attribution (`Host`, `Speaker`, `Panelist`, `Moderator`, `Guest`), `[Name (Role)] [timestamp] text` line format, roster configuration dialog with voice sample extraction/playback and biometric privacy notice, pyannote diarization with 4GB VRAM guard (unload Whisper before loading Pyannote), word-level alignment, overlap handling (`[Overlapping: ...]`), unidentified speaker fallback (`[Unidentified]`), session-level combined transcripts (`<session>_combined.txt`), and Hugging Face token preflight check for gated model access.
- **Why**:
  - The previous UI had inconsistent type sizes, unscaled controls, low contrast elements, and clutter in Stage 3 with disconnected manual buttons.
  - Multi-speaker panels and sessions require precise speaker attribution with role labels and diarization while respecting GPU memory limits (RTX 3050 Laptop GPU with 4GB VRAM) and user privacy.
- **What was built (user-visible behaviour)**:
  - **Design System v2**:
    - High-contrast, clean light theme based on an extended `clam` engine.
    - Standardized typography scale: App Title 18pt bold, Section 12pt bold, Body 10pt, Table 10pt, Caption 9pt, Code 10pt Consolas.
    - `View` menu with Text Size dialog (slider 85%-150% with live preview and reset), Zoom In (Ctrl++), Zoom Out (Ctrl+-), Normal Size (Ctrl+0), Table Density (Compact 24px / Comfortable 30px), and Reset Layout.
    - Quick-access `A−` and `A+` buttons in the header band.
    - Settings saved automatically to `%LOCALAPPDATA%\SessionScribe\ui_settings.json` (strictly isolated from repo and event folders).
    - Modern pill-shaped `SegmentedControl` for mode and engine selections.
    - Smooth `ToggleSwitch` replacing checkboxes.
    - Floating `ToolTip` on truncated headers and labels.
    - Transcript Viewer window minsize (680, 480) with a clean two-row toolbar (Save, Find, Mode, Zoom, Replace) and header tooltip.
  - **Stage 2 Enhancements**:
    - Segmented control for default event speaker mode: `One speaker | Multiple speakers`.
    - Sessions table displays `Mode` column. Double-clicking toggles mode or opens the Roster dialog.
    - `Roster…` button opens the session speaker roster configuration.
  - **Stage 3 Streamlined Interface**:
    - Removed disconnected Event Folder entry, Load Manifest, and Reload buttons.
    - Added one-line summary: `Event: <name> | Date: <date> | N sessions, M clips` with a "Go to Stage 2" navigation button when unconfigured.
    - Compact action toolbar containing `Rename Speaker…` and `Combine approved transcripts`, with a stale combination warning banner when edits occur.
    - Compact Output row with `Transcripts:` and `Combined:` directory paths and `[Open folder]` buttons.
    - Narrow language selector (`width=6`).
    - Collapsible `Advanced (Glossary & Notes)` and `Connections (NVIDIA Key & HF Token)` sections with status chips (`Not set`, `Valid`, `Invalid`, `No access`).
    - Clip table with 2-line wrapped headers (`Time taken\nfor transcription`, `Failover\ntime`) and locked totals row.
  - **Multi-Speaker Diarization Pipeline**:
    - Interactive Roster Dialog allowing adding participants with roles (`Host`, `Speaker`, `Panelist`, `Moderator`, `Guest`), extracting audio samples from event clips via ffmpeg, or uploading WAV samples.
    - Prominent biometric privacy notice confirming audio samples never leave the local machine.
    - Preflight check for Hugging Face token: validates token format and checks access to gated models `pyannote/speaker-diarization-3.1` and `pyannote/segmentation-3.0`.
    - Sequential GPU execution: Whisper medium is unloaded from VRAM and `torch.cuda.empty_cache()` is called before pyannote is loaded, preventing CUDA OOM on 4GB GPUs.
    - Attribution line format: `[Name (Role)] [HH:MM:SS.mmm - HH:MM:SS.mmm] text`.
    - Overlap lines: `[Overlapping: Host (Host), Speaker A (Speaker)] [HH:MM:SS.mmm - HH:MM:SS.mmm] text`.
    - Combined transcripts produced as `<session>_combined.txt`.
- **How to use it**:
  1. *Change UI size*: Use `View > Text size...`, press `Ctrl++` / `Ctrl+-`, or click `A−` / `A+` in the header band. Use `View > Table density` to switch row spacing.
  2. *Set Multi-Speaker Mode*: In Stage 2, select `Multiple speakers` in the segmented control, or click `Roster…` on any session in the Sessions table. Assign roles and optionally extract voice samples.
  3. *Configure HF Token for Diarization*: Enter your Hugging Face token in Stage 3 under `Connections` and click `Save` and `Test`. The preflight verifies model permissions without exposing the token.
  4. *Transcribe*: Select engine (Local or Cloud) and click `Start Transcription`. The worker automatically runs Whisper transcription, unloads it, runs Pyannote diarization, aligns word timestamps, and generates multi-speaker transcripts.
- **Settings and defaults**:
  - `text_scale`: `1.0` (range: `0.85` to `1.50`).
  - `table_density`: `comfortable` (30px row height; compact is 24px).
  - UI preferences stored in: `%LOCALAPPDATA%\SessionScribe\ui_settings.json`.
  - Roles: `Host`, `Speaker`, `Panelist`, `Moderator`, `Guest` (default: `Speaker`).
  - Voice samples directory: `<event root>\scribe\voice_samples\<name>.wav`.
  - Preflight checks: blocks execution if multi-speaker mode is enabled and HF token/models are inaccessible.
- **Files changed**:
  - `gui/theme.py`: Added type scale, named Tcl fonts, DPI scaling, `SegmentedControl`, `ToggleSwitch`, `TextSizeDialog`, `CollapsibleSection`, `ToolTip`.
  - `gui/ui_settings.py`: Created zero-dependency persistent UI preferences manager.
  - `gui/app.py`: Added View menu, header font buttons, column resize tracking, and DPI awareness.
  - `gui/stage2_tab.py`: Added segmented speaker mode, Sessions table `Mode` column, and `Roster…` action.
  - `gui/stage3_tab.py`: Removed dead folder/load buttons, added one-line summary, compact toolbar, collapsible sections, HF preflight integration, wrapped headers.
  - `gui/roster_dialog.py`: Created multi-speaker roster configuration dialog with privacy notice and ffmpeg extraction.
  - `scribe_core/multi_speaker.py`: Added role data structures, word alignment, turn splitting, ffmpeg sample extractor, and line formatters.
  - `scribe_core/hf_preflight.py`: Added Hugging Face token and gated model access preflight validator.
  - `scribe_core/transcription_manager.py`: Added HF token forwarding to worker subprocess.
  - `scribe_core/transcribe_worker.py`: Added sequential 4GB VRAM guard, Pyannote diarization pipeline, and multi-speaker transcript output formatting.
  - `scribe_core/combined_transcripts.py`: Updated combined output to support `<session>_combined.txt`.
  - `tests/test_design_system_v2.py`: Added 7 tests for type scale, segmented controls, toggle switch, and settings persistence.
  - `tests/test_multi_speaker.py`: Added 10 tests for multi-speaker formatting, roster validation, word alignment, and preflight.
  - `scripts/capture_theme_screenshots.py`: Tool to generate theme review screenshots.
- **Limitations and known issues**:
  - `sv-ttk` is incompatible with Tkinter Treeview row background tinting (tags are ignored by sv-ttk's custom bitmap engine); extended `clam` was retained to preserve status colors and zero third-party dependencies.
  - Diarization requires Pyannote models with accepted user agreements on Hugging Face; unaccepted agreements trigger an explicit guidance dialog.
- **How it was verified**:
  - Verified `sv-ttk` in isolated virtual environment `scratch/test_env/` and documented failure to support Treeview tag row background tinting.
  - All 87 unit tests passing via `python -m unittest discover tests`.
  - Secret scan script (`scripts/secret_scan.py`) executed: 0 secrets found.
  - 24 UI screenshots captured across all tabs, scale levels (100%, 150%), and window sizes into `scratch\theme_review\`.
- **How to roll it back**:
  - Revert changes to `gui/` and `scribe_core/` using git checkout/restore. Remove `gui/ui_settings.py`, `gui/roster_dialog.py`, `scribe_core/multi_speaker.py`, and `scribe_core/hf_preflight.py`.

