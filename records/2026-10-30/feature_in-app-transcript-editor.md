# Feature: In-App Transcript Editor and Reviewer

**Date:** 2026-10-30  
**Type:** feature  

---

## What was asked / requested

Provide in-app viewing and editing of transcripts directly inside Session Scribe (Stage 3):
1. **In-App Viewer/Editor Window (`TranscriptEditorDialog`):**
   - Double-clicking a clip opens its transcript in Read mode.
   - Edit button switches to Edit mode; Read button switches back.
   - Mode, filename, and unsaved status clearly displayed in the window title bar:
     `Transcript Viewer — [Read] <filename>` / `Transcript Editor — [Edit] <filename> [modified]`.
2. **Text Editor Capabilities:**
   - Full keyboard shortcuts: typing, backspace/delete, paste, undo/redo (Ctrl+Z, Ctrl+Y), word-level navigation shortcuts.
   - Ctrl+S saves transcript.
   - Ctrl+F opens an in-dialog find bar with search term input, Next, Prev, and match highlighting.
   - Preserves original UTF-8 encoding and original line ending style (`\r\n` vs `\n`).
3. **Unsaved Changes Guard:**
   - Warns and prompts with Save, Discard, Cancel on:
     - Closing the viewer.
     - Double-clicking another clip.
     - Switching tabs in the notebook.
     - Switching events in Stage 2.
     - Closing the application window.
   - Cancel keeps the user in place without discarding edits.
4. **Original Backup and Atomic Write:**
   - Before the first save of a clip, backs up the original transcript to `<event root>\scribe\state\originals\<stem>.txt` (once only, never overwritten by subsequent saves).
   - Writes atomically via `.tmp` swap. The transcripts directory continues to hold only `.txt` files.
5. **Disk Conflict & Header Protections:**
   - Warns if the disk file changed externally while the viewer was open (offers Reload or Save Anyway).
   - Protects header lines (`Transcription Completed:`, `Speaker:`, `Video File:`). Warns before saving if critical header elements are damaged or removed.
6. **Edit Consequences:**
   - Manual edits clear approval for that clip and display notification.
   - Clip status in the list displays `"Edited"`.
   - Existing combined files are marked stale (requiring re-approval and manual combination).
   - Skip logic and settings checks treat edited transcripts as complete and ignore settings mismatches.
   - Force re-transcribe on an edited clip warns that edits will be overwritten, and backs up the edited transcript to `<event root>\scribe\state\edited_backup\<stem>.txt`.
7. **Read-Only Lock During Transcription:**
   - Disables editing if that clip is currently being transcribed by an active job, displaying an explanation.

---

## What was changed

1. **Transcript Editor Dialog (`gui/stage3_tab.py`):**
   - Created `TranscriptEditorDialog(tk.Toplevel)`:
     - Mode toggle between Read mode (disabled text widget) and Edit mode (interactive ScrolledText).
     - Title bar updates dynamically with mode and `[modified]` indicator.
     - Undo/redo integration and Ctrl keyboard shortcuts (Ctrl+S, Ctrl+F, Ctrl+Z, Ctrl+Y, Escape).
     - Embedded Find bar with regex-safe case-insensitive text search and match navigation.
     - Detects original line endings (`\r\n` vs `\n`) on open and writes in binary mode to guarantee exact preservation of newline style and UTF-8 encoding.
     - First-save backup to `<event root>\scribe\state\originals\<stem>.txt` using `shutil.copy2()`.
     - External disk conflict detection comparing SHA-256 hash at open vs save time.
     - Header preservation validation checking for `Transcription Completed:`, `Speaker:`, and clip identifier.
     - Unsaved changes confirmation dialog (`prompt_unsaved_if_dirty()`) returning boolean status to abort navigation on Cancel.
2. **Consequence Handling & State Updates (`gui/stage3_tab.py`, `scribe_core/clip_state.py`):**
   - Added `set_clip_edited()` to `scribe_core/clip_state.py`: records `"edited": True` in `clip_state.json`.
   - Updated `_clip_disk_status()`: returns `"Edited"` for edited complete transcripts and bypasses glossary mismatch checks.
   - Added `edited` tag styling (cyan/teal foreground) in the clip tree.
   - On save: clears approval, sets combined files stale, and refreshes the Stage 3 UI.
   - Updated `_toggle_force_relist()`: warns user when forcing re-transcription of edited clips and backs up current edited content to `<event root>\scribe\state\edited_backup\<stem>.txt`.
   - Tracked `self._currently_transcribing_clip` in `Stage3Tab` during worker runs to block editing during active writes.
3. **Global Navigation Interception (`gui/app.py`, `gui/stage2_tab.py`):**
   - In `SessionScribeApp`:
     - Intercepted notebook tab changes (`<Button-1>`) to check `stage3_tab.check_unsaved_changes()`. Blocks tab switch on Cancel.
     - Intercepted window close (`WM_DELETE_WINDOW`) to check `stage3_tab.check_unsaved_changes()`. Blocks close on Cancel.
   - In `Stage2Tab`:
     - Connected `_stage3_ref` via `set_stage3_tab()`.
     - In `_apply_event_folder()`, queries `_stage3_ref.check_unsaved_changes()` before changing events. Blocks event switch on Cancel.

---

## Files touched

- `gui/stage3_tab.py`
- `gui/app.py`
- `gui/stage2_tab.py`
- `scribe_core/clip_state.py`
- `tests/test_stage2_stage3_fixes.py`

---

## How it was verified

1. **Automated Unit & Acceptance Tests (`tests/test_stage2_stage3_fixes.py`):**
   - `test_viewer_modes_and_editing_with_backup_and_state`: Verified Read/Edit mode toggling, title bar indicator, atomic saving, CRLF line ending preservation, one-time original backup creation, approval clearing, stale flag marking, and `"Edited"` status column update.
   - `test_header_protection_and_conflict_check`: Verified warning dialog on damaged headers and conflict detection on modified disk files.
   - `test_unsaved_changes_prompt_navigation`: Verified Save, Discard, and Cancel responses on `prompt_unsaved_if_dirty()`.
   - `test_force_retranscribe_warns_and_backups_edited_clip`: Verified confirmation warning and backup to `scribe\state\edited_backup\<stem>.txt` when force re-transcribing edited clips.
   - `test_read_only_protection_during_transcription`: Verified editing is blocked and kept read-only while a clip is being actively transcribed.
2. **Full Regression Suite:**
   - Ran `pytest` across all test suites: 92 passed, 1 skipped.

