# Build Record: Re-transcribe Card Dialog Redesign & Universal Smooth Touchpad/Drag Scrolling

- **Date:** 2026-11-17
- **What was asked:**
  1. Fix dark-mode contrast gaps on Stage 3 header labels ("Event: … | Date …", "Transcripts:", "Combined:") so all text meets WCAG AA contrast ($\ge 4.5:1$).
  2. Implement universal smooth touchpad and touch drag scrolling across all scrollable containers (tabs, dialogs, drawers, viewer) with sub-notch delta accumulation, Shift+wheel horizontal scrolling, keyboard navigation, and mouse/touch drag-to-scroll.
  3. Redesign `ReTranscribeDialog` into a professional card-based modal window (~720x640, centered, resizable, minsize set) with a top clip summary card, segmented execution mode cards, rich selectable model cards with custom vector engine icons and metadata chips, and a sticky footer with telemetry estimates, warning bar, live progress bar, and spinning timer.
  4. Capture documentation screenshots in Light and Dark modes at 100% and 150% scales using dummy data only.

- **Why:**
  - Previous Stage 3 header text (`event_summary_lbl`) used `theme.COLOR_HEADER_NAVY` which dynamically evaluated to pitch black (`#090D13`) on `#161B22` dark cards (~1.05:1 contrast), making event details illegible in dark mode.
  - Windows Precision Touchpads send fractional `<MouseWheel>` delta ticks (e.g. $\pm 30$ units instead of $\pm 120$ detent clicks). Standard Tkinter handlers divided by 120, truncating sub-notch touchpad gestures to zero or causing jumpy scrolling.
  - Standard Tkinter widgets lack native touch panning support on Windows.
  - The previous re-transcription dialog was a basic form with text radio buttons, lacking visual hierarchy, disk file size information, visual differentiation between local GPU and cloud providers, and sticky footer controls.

- **What was built (user-visible behaviour):**
  1. **High-Contrast Stage 3 Summary Tokens:**
     - Replaced `COLOR_HEADER_NAVY` with `COLOR_TEXT_PRIMARY` (`#F0F6FC` dark, `#111827` light) on `event_summary_lbl` ($\ge 12:1$ contrast).
     - Styled `lbl_out_transcripts` and `lbl_out_combined` as elevated monospace pill boxes with `COLOR_INPUT_BG` and `COLOR_TEXT_PRIMARY`, meeting WCAG AA requirements.
  2. **Universal Scrolling Engine (`UniversalScroller`):**
     - Sub-notch floating point delta accumulator with threshold (30.0 units) to guarantee smooth scrolling on high-precision touchpads without divide-by-zero or gesture deadzones.
     - Horizontal Shift-wheel routing (`<Shift-MouseWheel>` / `<Shift-Button-4/5>`) directing scrolls along the X-axis.
     - Keyboard navigation bindings: `<Prior>` (Page Up), `<Next>` (Page Down), `<Home>`, `<End>`, `<Up>`, `<Down>`.
     - Pointer drag-to-scroll gesture pan: smooth direct manipulation pan via `<ButtonPress-1>`, `<B1-Motion>`, and `<ButtonRelease-1>` with a 6px deadzone threshold to differentiate deliberate clicks from scroll drags.
     - Integrated across `ScrollableTab`, `ReTranscribeDialog`, `RosterDialog`, and app-wide mouse wheel router.
  3. **Professional `ReTranscribeDialog` Redesign:**
     - **Window Geometry:** 720x640 default dimensions, centered over parent, resizable with a minimum size of 680x560.
     - **Top Selection Summary Card:** Displays selected clip filename, formatted duration (`hh:mm:ss`), and file size on disk (`MB`/`GB`) resolved from disk metadata.
     - **Segmented Execution Mode Cards (`ModeCard`):** Distinct selectable cards for "Compare (Side-by-side)" and "Replace (Overwrite)" with selection rings and descriptive subtext.
     - **Rich Selectable Model Cards (`ModelCard`):** Grouped into "Local on this PC" and "NVIDIA Cloud". Features a 22px radio indicator (accent circle with checkmark vs border ring), crisp 28x28 vector icons (silicon GPU chip with circuit pins vs Cloud with broadcast dot), semibold typography, description subtext, and badges for VRAM footprint, speedup factor, and cost. Disabled "Coming Soon" chip for Nemotron Transcribe (non-selectable).
     - **Sticky Footer:** Stays anchored at the bottom with real-time audio duration and cost estimates, rate-limit / VRAM warning bar, live indeterminate/percentage progress bar, and a Start button that transforms into a spinning timer (`⏳ Transcribing… hh:mm:ss`) while executing.

- **How to use it:**
  1. On Stage 3, select any clip row and click **Re-transcribe…** (or double-click a child comparison run).
  2. In the dialog, review the clip summary card (duration and file size).
  3. Choose **Compare** mode to generate an alternative run without overwriting, or **Replace** mode to update the primary transcript.
  4. Click any model card (e.g. *NVIDIA Parakeet TDT 0.6B v2* or *Whisper Medium*).
  5. Click **Start Re-transcription**. The button shows a live elapsed timer and the progress bar animates.
  6. Scroll anywhere in the dialog using touchpad gestures, mouse wheel, keyboard arrows, or clicking and dragging with the pointer.

- **Settings and defaults:**
  - Dialog dimensions: `720x640` (minimum `680x560`).
  - Mode default: `compare`.
  - Model default: `whisper-medium` (or active clip model).
  - Drag-to-scroll deadzone threshold: `6px`.
  - Touchpad accumulator threshold: `30.0 units`.

- **Files changed:**
  - `gui/scrollable_tab.py`: implemented `UniversalScroller` and `attach_universal_scrolling()`.
  - `gui/roster_dialog.py`: integrated `UniversalScroller`.
  - `gui/stage3_tab.py`: fixed dark-on-dark labels, implemented `ModeCard`, `ModelCard`, `ReTranscribeDialog` layout, and drawer selection logic.
  - `tests/test_scrolling_and_shortcuts.py`: added test suite `TestUniversalScroller`.
  - `scripts/capture_retranscribe_screenshots.py`: automated dummy screenshot generation.
  - `docs/images/`: generated 4 dialog screenshots.

- **Limitations and known issues:**
  - **Tkinter Native Touch Limitation:** Tkinter on Windows does not receive native `WM_GESTURE` or `WM_TOUCH` messages from the operating system. Universal smooth touch scrolling is implemented via touchpad delta accumulation for Windows Precision Touchpads, complemented by pointer drag-to-scroll tracking (`<ButtonPress-1>` / `<B1-Motion>`) with a 6px deadzone to simulate native touch pan.

- **How it was verified:**
  1. Executed `pytest tests/test_scrolling_and_shortcuts.py` (40 passed), validating delta accumulation, Shift-wheel, and drag pan.
  2. Executed `pytest tests/test_model_registry_and_compare.py` (13 passed), validating model selection and dialog state.
  3. Generated and reviewed screenshots in `docs/images/retranscribe_dialog_dark.png`, `retranscribe_dialog_light.png`, `retranscribe_dialog_scale150_dark.png`, and `retranscribe_dialog_scale150_light.png`.

- **How to roll it back:**
  Revert `gui/stage3_tab.py` and `gui/scrollable_tab.py` to their previous versions.
