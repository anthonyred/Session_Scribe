# Change: Stage 3 Clip Table Alignment, Durations, and Transcription Time Taken

## Date
2026-11-03

## What Was Asked
Inspect and correct the Stage 3 clip table (`self.clip_tree`):
1. **Misaligned Columns Root Cause & Fix**:
   - Determine why columns looked misplaced (headers centered while values left-aligned; empty Test column).
   - Match header alignment to cell value alignment:
     - Text columns (`clip`, `speaker`, `status`): left-aligned (`anchor=tk.W`) for both heading and cell values.
     - Numeric/time columns (`check`, `idx`, `duration`, `start`, `approved`, `engine`): centered (`anchor=tk.CENTER`) for both heading and cell values.
2. **Column Widths & Content Fit**:
   - Adjust widths to fit content comfortably (e.g. full `"Cloud: Parakeet"` visible without clipping).
   - Allow columns to stretch with window resizing while maintaining sensible `minwidth` thresholds.
   - Add hover tooltips displaying full cell contents when hovering over treeview rows.
3. **Format Duration as `hh:mm:ss`**:
   - Format clip duration in `hh:mm:ss` rounded to nearest second (e.g. 306.9s -> `00:05:07`, 4.4s -> `00:00:04`).
   - Manifest file on disk retains numeric seconds unchanged.
4. **Replace "Start Time (IST)" with "Time taken for transcription"**:
   - Re-title column 5 to `"Time taken for transcription"`.
   - Populate from `processing_time_sec` stored in `scribe\state\clip_state.json`, falling back to `"Transcription Duration"` header in completed transcript files, or `"—"` if pending/missing.
   - Never re-time skipped clips. Preserve chronological row ordering by clip start time.

## Why
1. In `ttk.Treeview`, headings default to `tk.CENTER` unless explicitly anchored, whereas column data defaults to `tk.W`. This caused text columns to look disconnected from their headers, and numeric columns to have inconsistent margins.
2. Reviewers need to see the actual processing time taken for transcription to evaluate engine speeds and track long runs, rather than the scheduled start timestamp of the recording clip.
3. Displaying raw float seconds (e.g. `306.9s`) was hard to read compared to standard `hh:mm:ss` media timing notation.

## What Was Built (User-Visible Behaviour)
- **Consistent Header & Cell Alignment**:
  - Clip Filename, Speaker, and Status columns are consistently left-aligned.
  - Test marker (`[TEST]`), Index (`#`), Duration (`hh:mm:ss`), Time Taken (`hh:mm:ss`), Approval (`Approved` / `Not approved`), and Engine (`Local`, `Cloud: Parakeet`, etc.) are cleanly centered.
  - Column 5 header is now `"Time taken for transcription"`.
- **Dynamic Content & Sizing**:
  - The Engine column width (140px, minwidth 115px) displays `"Cloud: Parakeet"` and `"Fallback (Local)"` in full.
  - Text and time columns stretch dynamically as the application window is resized.
  - Hovering over any cell displays a transient tooltip showing the full value.
- **Timing Resolution**:
  - Durations display formatted as `hh:mm:ss`.
  - Elapsed transcription times are pulled from the clip state database or transcript headers, showing `"00:05:07"` or `"00:00:42"`, or `"—"` if pending.

## How to Use It
1. Open Stage 3 and load any event manifest.
2. Inspect the clip table: durations appear in `hh:mm:ss` format and the "Time taken for transcription" column shows the actual duration required to transcribe each completed clip.
3. Hover mouse cursor over any clip filename, engine label, or status to view full cell text.

## Settings and Defaults
- Column Alignments:
  - Text columns: `tk.W`
  - Numeric/Time columns: `tk.CENTER`
- Tooltip hover delay: instant on mouse movement with hover cache.

## Files Changed
- `gui/stage3_tab.py`:
  - Added `format_duration_hhmmss` and `_get_clip_time_taken` helpers.
  - Reconfigured `self.clip_tree` headings, column anchors, widths, and stretch options.
  - Added `_on_tree_motion` and `_hide_tree_tooltip` for hover tooltips.
  - Updated `_load_manifest`, `_refresh_status_column`, and `_set_tree_status` to populate formatted duration and time taken.
- `scribe_core/clip_state.py`: Enhanced `write_clip` to accept `processing_time_sec`.
- `scribe_core/session_tracker.py`: Enhanced `finalize_transcription` to accept `processing_time_sec`.
- `scribe_core/transcribe_worker.py`: Passes `transcribe_dur_sec` to state files upon clip completion.
- `tests/test_transcript_replace_and_zoom.py`: Added tests verifying table alignment, duration formatting, and time taken resolution.

## Limitations and Known Issues
- Clips transcribed prior to this update that lack `processing_time_sec` in `clip_state.json` rely on the `"Transcription Duration"` line in their `.txt` header; if that header is absent, the column displays `"—"`.

## How It Was Verified
1. Unit tests in `tests/test_transcript_replace_and_zoom.py` (`TestStage3ClipTable`):
   - Verified alignment for text (`W`) and numeric/time (`CENTER`) columns.
   - Verified duration formatting for 306.9s, 4.4s, 0s, 3661s.
   - Verified resolution of time taken from state file, transcript header fallback, and pending fallback.
2. Full test suite: 123 passed.
3. Secret scan: clean.

## How to Roll It Back
Revert modifications in `gui/stage3_tab.py`, `scribe_core/clip_state.py`, `scribe_core/session_tracker.py`, and `scribe_core/transcribe_worker.py`.

