# Feature: Combined Transcript Per Speaker After Transcription

**Date:** 2026-10-30  
**Type:** feature  

---

## What was asked / requested

Task 3:
1. When a full transcription run finishes (including a resumed run completing remaining clips), generate one combined text file per speaker in `<event root>\scribe\combined\`, named `<speaker name>_combined.txt`.
   - Sanitize filenames: replace spaces with underscores and strip invalid Windows characters (`<>:"/\|?*`).
2. File contents:
   - Verbatim copy of each completed clip's `.txt` transcript (headers included), concatenated in clip start-time order, separated by a single blank line (`\n\n`).
   - No summarisation, no re-timestamping, no text modification.
3. Incomplete / missing clip handling:
   - Only include completed transcripts (`Transcription Completed: Yes` and valid segment content).
   - If some clips for a speaker are missing, failed, or pending, still create the combined file from available completed clips and emit a clear warning in the log and UI naming the omitted clips.
   - Never generate empty files.
4. Concurrency & atomic operations:
   - Write files atomically via a unique temporary file (`<filename>.tmp.<pid>`) and atomic replacement (`os.replace`).
   - Overwrite existing combined files with the refreshed version on subsequent runs.
5. Manual and automated rebuilds:
   - Add a "Rebuild combined files" button to Stage 3 for manual execution.
   - Automatically rebuild combined files when the Rename Existing Transcripts tool renames a speaker.
   - Archive stale combined files from old speaker names by moving them to `<event root>\scribe\combined\old\` rather than deleting them.
6. Summary and logging:
   - Display a concise summary (e.g. `Kalyani_Patra_combined.txt: 11 clips, Jeevan_D_C_combined.txt: 12 clips`) upon completion in both UI and log.
7. Directory isolation:
   - `scribe\combined\` is an output-only directory. Skip logic, clip state persistence (`clip_state.json`), and worker transcript scans remain isolated and ignore the combined folder.

---

## What was changed

1. **Core Module (`scribe_core/combined_transcripts.py`):**
   - Implemented `sanitize_speaker_filename(speaker_name: str) -> str`: strips invalid Windows characters and replaces whitespace with underscores.
   - Implemented `is_completed_transcript_file(txt_path: str) -> bool`: validates that the transcript exists and contains the completed header.
   - Implemented `build_combined_transcripts(event_root: str, manifest_data: dict) -> dict`:
     - Reads completed clips from `scribe\transcripts\` in start-time order.
     - Concatenates texts with single blank lines (`\n\n`).
     - Logs warnings for any omitted clips per speaker.
     - Performs atomic writes using `.tmp.<pid>` and `os.replace`.
     - Returns summary string, counts, warnings, and created file list.
   - Implemented `archive_stale_combined_file(event_root: str, old_speaker_name: str) -> Optional[str]`:
     - Safely moves `<old_speaker_name>_combined.txt` into `scribe\combined\old\`.
2. **Path Registry (`scribe_core/event_paths.py`):**
   - Added `"combined"` to `LEAF_FOLDER_NAMES` and registered `"combined": os.path.join(scribe, "combined")` in `derive_event_paths`.
3. **Stage 3 UI Integration (`gui/stage3_tab.py`):**
   - Added "Rebuild combined files" button (`btn_rebuild_combined`) in the Stage 3 action panel.
   - Connected `_rebuild_combined_files(show_info=True)` displaying warnings and summary popups.
   - In `_on_job_finished`: automatically triggers `build_combined_transcripts` when `output_dir == paths["transcripts"]`, logging summary and warnings.
   - In `do_apply` of Rename Transcripts dialog: archives stale combined files to `scribe\combined\old\` and automatically rebuilds combined files for the event.

---

## Files touched

- `scribe_core/combined_transcripts.py` (new)
- `scribe_core/event_paths.py`
- `gui/stage3_tab.py`
- `tests/test_stage2_stage3_fixes.py`

---

## How it was verified

1. **Automated Unit & Acceptance Tests (`tests/test_stage2_stage3_fixes.py`):**
   - `test_sanitize_speaker_filename`: Verified space replacement and Windows illegal character sanitization.
   - `test_build_combined_transcripts_success_and_omission_warning`: Verified exact concatenation in order with single blank lines, and omission warning identifying incomplete clips.
   - `test_archive_stale_combined_file_on_rename`: Verified old combined files are safely moved to `scribe\combined\old\` upon rename.
2. **Integration Verification (`scratch/verify_all.py`):**
   - Verified end-to-end multi-speaker build (`Alice Smith`, `Bob Jones`).
   - Verified content match against source files.
   - Verified missing clip warning logged for `Bob Jones`.
   - Verified rename archiving (`Alice Smith` archived to `old/` and replaced by `Dr_Alice_Smith_combined.txt`).
3. **Full Test Suite:**
   - 79 passed, 1 skipped (real event test per AGENTS.md §4).

