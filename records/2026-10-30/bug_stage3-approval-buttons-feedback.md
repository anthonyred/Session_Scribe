# Bug: Stage 3 Approval Buttons Ineffective and Missing Feedback

**Date:** 2026-10-30  
**Type:** bug  

---

## What was asked / requested

Investigate and fix why "Approve selected" and "Approve all completed" do nothing in Stage 3:
1. Identify root cause:
   - Button command bindings.
   - Disabled state flags.
   - Selection retrieval issues (focus loss vs tree selection).
   - Silent rejection of clips with status `"Done (test)"` or `"Diff settings"`.
   - State file write failures / silent exceptions on Windows.
   - UI and approval counter refresh failures.
2. Fix both buttons to provide immediate feedback:
   - Update the status and approval columns in the clip list.
   - Update the "N of M approved" counter immediately.
   - Persist approval in `<event root>\scribe\state\clip_state.json`.
3. Informative, non-silent feedback:
   - When clips cannot be approved (not completed, failed, pending, file missing), show a clear message naming the affected clips and the explicit reasons. Never ignore clicks silently.
4. Selection and Batch Handling:
   - "Approve selected" supports multi-select (Ctrl+click, Shift+click).
   - "Approve all completed" approves every clip that has a completed transcript.
5. Consistent handling of `scribe\test`:
   - Clips whose completed transcript is in `scribe\test` (`"Done (test)"`) count as completed, can be approved, and the combine step reads the same approved files (`scribe\transcripts\` first, falling back to `scribe\test\`).
6. State persistence and isolation:
   - Approvals survive restart and never leak to other events.

---

## Root cause

1. **State File Write Target Misconfiguration (`PermissionError: [WinError 5] Access is denied`):**
   `derive_event_paths()` sets `paths["state"]` to `<event root>\scribe\state` (a directory). In `_approve_selected_clicked` and `_approve_all_completed_clicked`, `paths["state"]` was passed directly to `set_clip_approval()` and `set_multiple_clip_approvals()`. Inside `_save_raw()`, the atomic rename `os.replace(tmp, state_path)` attempted to replace a file onto a directory path, raising an uncaught Windows `PermissionError`. The Tkinter button callback failed silently without updating state or UI.
2. **Silent Rejection of Valid Transcripts and Incomplete Clips:**
   - Both approval handlers checked strictly `if status == "Done"`. Clips in `scribe\test` (`"Done (test)"`) or clips marked `"Edited"` were silently skipped without any message to the user.
   - When users clicked "Approve selected" on pending or unapprovable clips, no feedback was shown, making the button appear broken.
   - `btn_approve_all` was disabled if `done_count == 0`, even when completed test transcripts were present in `scribe\test`.
3. **Settings Difference False-Positive When Loading State Records:**
   In `_clip_disk_status`, clips with valid completed transcripts on disk were compared with `rec.get("glossary_hash") != current_ghash`. When clips were approved without having a full transcription settings record in `clip_state.json`, `rec.get("glossary_hash")` was `None`, causing `_clip_disk_status` to evaluate to `"Diff settings"`, which in turn caused `_refresh_status_column()` to automatically revoke the approval immediately after saving.

---

## What was changed

1. **State Path Normalization (`scribe_core/clip_state.py`):**
   - Added `_resolve_state_path()` helper to `_load_raw()` and `_save_raw()`. If a directory path (e.g. `paths["state"]`) is passed, it automatically appends `clip_state.json`, preventing Windows `PermissionError` and guaranteeing atomic state updates.
   - Updated `validate_and_reconcile_approvals()` to accept `test_dir: Optional[str] = None` and check both `scribe\transcripts` and `scribe\test` for transcript hashes.
2. **Combine Fallback for Test Transcripts (`scribe_core/combined_transcripts.py`):**
   - Updated `build_combined_transcripts()` to look for completed transcripts in `scribe\transcripts\` first and fall back to `scribe\test\`.
3. **Stage 3 Approval Handlers & Feedback UI (`gui/stage3_tab.py`):**
   - Rewrote `_approve_selected_clicked()`:
     - Supports multi-selection via `clip_tree.selection()`.
     - Approves clips whose status is `"Done"`, `"Done (test)"`, or `"Edited"`.
     - Inspects `transcripts\` then `test\`, computes content hash, and saves approval.
     - For any unapprovable clips (`Pending`, `File missing`, `Diff settings`), displays a clear message dialog naming the clips and their explicit blocking reasons.
   - Rewrote `_approve_all_completed_clicked()`:
     - Identifies all clips with completed transcripts across `transcripts\` and `test\`.
     - Writes batch approvals in a single atomic transaction.
     - Provides informational feedback if no completed transcripts exist.
   - Updated `_update_combine_and_approval_ui()`:
     - Counts `Done`, `Done (test)`, and `Edited` clips as completed.
     - Enables `btn_approve_all` whenever completed transcripts exist.
     - Updates approval counter immediately.
   - Updated `_clip_disk_status()`:
     - Only flags `"Diff settings"` if a glossary hash was recorded in state and differs from the current glossary hash (`if rec and rec.get("glossary_hash") and rec.get("glossary_hash") != current_ghash`).
     - Ignores settings differences for manual edits (`rec.get("edited")`).

---

## Files touched

- `scribe_core/clip_state.py`
- `scribe_core/combined_transcripts.py`
- `gui/stage3_tab.py`
- `tests/test_stage2_stage3_fixes.py`

---

## How it was verified

1. **Automated Unit & Acceptance Tests (`tests/test_stage2_stage3_fixes.py`):**
   - `test_approve_selected_multi_select_with_feedback`: Verified multi-selection approval, approval counter updates, and informative feedback dialog naming unapprovable clips and reasons.
   - `test_approve_all_completed_handles_test_directory_and_combines`: Verified approval of clips whose completed transcripts reside in `scribe\test\`, verified atomic persistence in `clip_state.json`, and verified combining outputs from both `transcripts\` and `test\`.
   - `test_approval_survives_reopening_and_event_switch_isolation`: Verified approval state survives closing and reopening Stage 3, and remains strictly isolated between different event folders.
2. **Full Regression Suite:**
   - Ran `pytest` across all test suites (`test_mapping_and_rename.py`, `test_scrolling_and_shortcuts.py`, `test_stage2_acceptance.py`, `test_stage2_stage3_fixes.py`, `test_stage3_acceptance.py`): 92 passed, 1 skipped (per AGENTS.md §4).

