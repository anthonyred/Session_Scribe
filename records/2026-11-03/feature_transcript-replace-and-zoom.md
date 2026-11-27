# Feature: In-App Transcript Replace, Replace All, and Zoom

## Date
2026-11-03

## What Was Asked
Enhance the Stage 3 transcript viewer and editor (`TranscriptEditorDialog`) with comprehensive Find & Replace functionality and independent text zooming:
1. **Find & Replace**:
   - `Ctrl+H` or expand toggle (`▼ Replace` / `▲ Replace`) opens the Replace row.
   - Replace field, Replace button, Replace All button, "Match case" and "Whole word" checkboxes.
   - Empty Find query disables both Replace and Replace All buttons.
   - Read mode disables Replace controls and displays guidance: `"Switch to Edit Mode to replace"`.
   - Single Replace replaces the current match and advances to the next match.
   - Replace All confirms with match counts (e.g., `"Replace 12 occurrences of 'X' with 'Y'?"` or `"Delete 12 occurrences of 'X'?"`), executes as a single undo step (`Ctrl+Z` restores all edits at once), and marks the transcript dirty.
   - Protected regions: Event and clip metadata headers and line timestamp prefixes `[Speaker] [timestamp]` are protected from replacement by default. Skipped matches are reported. A checkbox `"Also change headers and timestamps"` (default off) lifts this protection.
2. **Text Zoom**:
   - Independent text zoom (50% to 300% in 10% steps, default 100%) that scales only the viewer text font without zooming the outer application window or tab.
   - Toolbar controls: `−`, `+`, `Reset`, and percentage label (`100%`).
   - Shortcuts & Gestures: `Ctrl+Plus`, `Ctrl+Equal`, `Ctrl+Minus`, `Ctrl+0`, `Ctrl+Wheel`, and precision touchpad pinch (with delta accumulation so high-resolution touchpads are smooth).
   - `Ctrl+Wheel` over viewer returns `"break"` to never scroll the parent container.
   - Preserves visible line position anchored at `@0,0` across zoom steps and re-wraps lines.

## Why
1. Reviewers and editors frequently need to correct recurring transcript terms, misheard names, or remove unwanted filler across a clip's transcript quickly and safely.
2. Without protection, global replacements could corrupt critical headers (`Speaker:`, `Transcription Completed:`) or segment timestamps, breaking downstream parsers, speaker tracking, and manifest reconciliation.
3. Users working on laptops or high-DPI displays require adjustable text magnification inside the transcript reader without distorting the outer layout or causing accidental scrolling.

## What Was Built (User-Visible Behaviour)
- **Replace Row in Find Bar**:
  - `Ctrl+H` or clicking `▼ Replace` expands the Replace row directly beneath the Find controls.
  - When switching between Read and Edit modes via the toolbar button, Replace controls automatically update their active/disabled state and show informative status text.
  - "Whole word" checkbox next to "Match case" restricts searches to whole words (e.g. matching `test` but skipping `testing` or `my_test`).
  - "Also change headers and timestamps" checkbox lets editors intentionally update metadata headers or timestamps when required, while protecting them by default.
  - "Replace All" prompts with a clear confirmation dialog and executes all replacements as an atomic transaction that can be reverted with a single `Ctrl+Z`.
- **Zoom Controls**:
  - Zoom sub-toolbar (`Zoom: [−] [100%] [+] [Reset]`) is integrated directly into the viewer toolbar.
  - Smooth zooming from 50% to 300% via buttons, keyboard shortcuts (`Ctrl+=`, `Ctrl+-`, `Ctrl+0`), and mouse wheel / pinch gestures.
  - Viewport remains anchored to the currently visible text line on zoom adjustments.

## How to Use It
1. Open any completed transcript in Stage 3 by double-clicking its row.
2. Press `Ctrl+H` to open the Replace toolbar.
3. Click "Switch to Edit Mode" to enable editing.
4. Enter target text in Find and replacement text in Replace.
5. Toggle "Whole word" or "Match case" as desired.
6. Click "Replace" to step through occurrences, or "Replace All" to replace all eligible occurrences at once.
7. Use `Zoom: [−] / [+]` or hold `Ctrl` while scrolling the mouse wheel / pinching on the touchpad to resize text.

## Settings and Defaults
- Default Zoom: 100% (range 50% to 300%, step 10%).
- Match case: Off (`False`).
- Whole word: Off (`False`).
- Also change headers and timestamps: Off (`False`).
- Mode on open: Read mode (safe read-only).

## Files Changed
- `gui/zoom_helper.py` — Reusable `TextViewerZoomer` helper class.
- `gui/stage3_tab.py` — Integrated `TextViewerZoomer`, added Replace bar UI, whole word matching, protected range evaluator, atomic multi-replace, and mode state bindings.
- `tests/test_transcript_replace_and_zoom.py` — Unit and integration tests covering zooming, find & replace, whole word matching, protected regions, and single-undo.

## Limitations and Known Issues
- Precision touchpad pinch gestures in Tkinter on Windows fire `<Control-MouseWheel>` events with varying delta values depending on OEM touchpad drivers; the delta accumulator smooths these deltas to avoid erratic jumpiness, but physical pinch sensitivity may still vary slightly between laptop models. Two-finger scrolling without Ctrl continues to scroll text normally.

## How It Was Verified
1. Unit tests in `tests/test_transcript_replace_and_zoom.py`:
   - Zoom stepping, clamping (50% to 300%), and delta accumulation.
   - Whole word boundary verification on punctuation and whitespace.
   - Protected area detection for headers and timestamps vs. spoken text.
   - Single undo step restoring text on `edit_undo()`.
2. Integration suite: `pytest tests/test_stage2_stage3_fixes.py` (23 passed), full test suite (123 passed).
3. Secret scan: `python scripts/secret_scan.py` (clean).

## How to Roll It Back
Revert `gui/stage3_tab.py` and delete `gui/zoom_helper.py` and `tests/test_transcript_replace_and_zoom.py`.

