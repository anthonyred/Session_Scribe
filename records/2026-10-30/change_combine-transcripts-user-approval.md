# Change: Transcript Combining Gated on Explicit User Approval

**Date:** 2026-10-30  
**Type:** change  

---

## What was asked / requested

Change combining of transcripts per speaker from automatic execution to an explicit, gated user-approval workflow:
1. **Remove every automatic trigger of combine:**
   - Remove combine at the end of a full run or resumed run.
   - Remove automatic rebuild when using the rename-speaker tool.
   - Ensure no startup, tab-open, or reload triggers combine.
   - End-of-run summary displays transcription results only.
2. **Approval status per clip in Stage 3 clip list:**
   - Display approval status (`Not approved`, `Approved`) per clip in the Stage 3 clip list.
   - Persist approval in the per-event state file under `scribe\state\clip_state.json` (not in `scribe\transcripts\`).
   - Only clips with completed transcripts (`status == "Done"`) can be approved.
3. **Review step in Stage 3:**
   - Double-clicking a clip opens its transcript in an in-app read-only viewer modal.
   - Buttons: "Approve selected", "Approve all completed", and "Clear approval" for selected clips.
   - Real-time progress counter showing approval progress (e.g., `21 of 23 approved`).
4. **Automatic Approval Invalidation:**
   - Automatically revoke approval for a clip when its transcript changes: re-transcribed (Force re-transcribe), speaker label renamed by the rename tool, or edited on disk (compared via SHA-256 content hash stored with approval).
   - Display a notification message naming the affected clips.
5. **Strict Gate on "Combine approved transcripts" Button:**
   - Replaces the old "Rebuild combined files" button.
   - Disabled until every clip in the manifest is Done and Approved.
   - When disabled, display the explicit blocking reason (e.g., `LOCKED: 2 clips not approved, 1 clip failed`). Failed, pending, or missing clips block combination with zero override.
6. **Pre-Combine Confirmation Modal:**
   - When clicked, display a confirmation dialog listing the target combined files to be created and their clip counts, with Yes or Cancel options. Cancelling leaves all files unchanged.
7. **Atomic Operations & Archiving:**
   - Files written to `<event root>\scribe\combined\<speaker_name>_combined.txt` in clip start-time order separated by single blank lines.
   - Existing combined files moved to `<event root>\scribe\combined\old\` before being replaced.
8. **Rename Speaker Stale Handling:**
   - After speaker renaming, existing combined files are marked stale (showing a persistent `⚠ Stale combined files` notice in the window) and are not rebuilt automatically.
   - User must review, re-approve affected clips, and explicitly click Combine.
9. **Event Isolation & App Restart:**
   - Approval state survives closing and reopening the application.
   - Switching events loads only the new event's approvals and never leaks approvals from other events.

---

## Root cause analysis (previous architecture)

Combined files were previously triggered automatically in three locations in `gui/stage3_tab.py`:
1. `_on_job_finished`: when `output_dir == paths["transcripts"]`, `_rebuild_combined_files()` was called automatically at the end of every full or resumed transcription run, producing files before user review.
2. `do_apply` inside `_open_rename_transcripts_dialog`: immediately archived the old file and regenerated new combined files without awaiting review.
3. `btn_rebuild_combined`: ungated button enabled whenever sessions existed, with no check for failed/pending clips, no approval tracking, and no confirmation dialog.

---

## What was changed

1. **State Store & Approval Invalidation (`scribe_core/clip_state.py`):**
   - Added `compute_transcript_hash(txt_path)` to calculate binary SHA-256 content digests.
   - Added `get_clip_approval`, `set_clip_approval`, `set_multiple_clip_approvals`, and `clear_multiple_clip_approvals`.
   - Updated `write_clip` to reset `approved: False`, `transcript_hash: ""`, and `approved_at: ""` upon new or re-transcription.
   - Implemented `validate_and_reconcile_approvals(state_path, transcripts_dir, clip_stems)`: compares transcript files on disk against stored SHA-256 hashes, revoking approval if modified or missing.
   - Added `get_combined_stale` and `set_combined_stale` to track stale output state in `clip_state.json`.
2. **Combined File Archiving (`scribe_core/combined_transcripts.py`):**
   - In `build_combined_transcripts`, automatically moves any existing target file to `scribe\combined\old\` before atomically replacing it.
3. **Stage 3 UI & Review Workflow (`gui/stage3_tab.py`):**
   - Replaced `btn_rebuild_combined` with `btn_combine_approved` ("Combine approved transcripts") and added `combine_stale_lbl`.
   - Added `"approved"` column to `clip_tree` displaying `Approved` or `Not approved` with colored tags (`approved_tag`, `unapproved_tag`).
   - Bound `<Double-1>` on `clip_tree` to `_on_clip_double_click`, opening an in-app read-only transcript viewer modal with ScrolledText (`state="disabled"`).
   - Added a review action bar with buttons: "Approve selected", "Approve all completed", "Clear approval", and progress counter label `approval_counter_var` (`X of Y approved`).
   - Implemented `_update_combine_and_approval_ui()`: enforces strict gating with detailed locking reasons (`LOCKED: X clip(s) not approved, Y clip(s) failed/pending/missing`) and updates stale notices.
   - Added confirmation dialog in `_combine_approved_clicked` listing target files and clip counts with Yes/Cancel.
   - In `_on_job_finished`: removed automatic combination trigger; end-of-run message displays transcription statistics only.
   - In `_open_rename_transcripts_dialog` (`do_apply`): removed automatic combination rebuild, marked combined files stale, cleared approval for renamed clips, and displayed an informational alert naming affected clips.
4. **Context Listener Safety (`gui/event_context.py`):**
   - Added dead-listener removal in `_notify` to gracefully drop callbacks from destroyed widgets.

---

## Files touched

- `scribe_core/clip_state.py`
- `scribe_core/combined_transcripts.py`
- `gui/stage3_tab.py`
- `gui/event_context.py`
- `tests/test_stage2_stage3_fixes.py`

---

## How it was verified

All tests use temporary directories (`tempfile.mkdtemp()`) without touching real event folders:
1. **End-of-run isolation:** Finished dummy transcription run; verified no combined file is created and the completion dialog shows transcription metrics only (`test_end_of_run_does_not_trigger_combine_automatically`).
2. **Gate locking reasons:** Confirmed Combine button is disabled with explicit reasons when clips are unapproved (`2 clip(s) not approved`), pending (`1 clip(s) pending`), failed, or missing (`1 clip(s) missing`) (`test_combine_button_locked_with_reasons`).
3. **Approval actions & confirmation dialog:** Approved clips; verified button unlocks. Tested Cancel (no files written) and Yes (files created with correct names, order, and verbatim contents) (`test_approve_unlocks_combine_cancel_and_confirm`).
4. **Approval invalidation:** Re-transcribed with force or edited transcript on disk; confirmed approval revoked automatically and button re-locked (`test_force_retranscribe_and_file_edit_clears_approval`).
5. **Rename tool stale handling:** Renamed speaker; confirmed existing combined file flagged stale, session approval cleared, and no auto-rebuild performed (`test_rename_speaker_marks_stale_and_does_not_rebuild`).
6. **Persistence & event isolation:** Confirmed approval state survives destroying and re-instantiating Stage 3 tab, and switching to another event does not leak approvals (`test_approval_survives_reopening_and_event_switch_isolation`).
7. **Automated test suite:** Full suite passed with 85 passed, 1 skipped.

