# Bug: Saved Speaker Mapping and Session Tables Not Loaded on Event Selection

**Date:** 2026-10-30  
**Type:** bug  

---

## What was asked / requested

Task 2:
1. When selecting an event folder that has a saved `scribe\manifest.json`, load everything:
   - Event metadata fields (name, date, organiser, venue, sponsors, speaker mode).
   - Algorithm parameters: session gap threshold and photo grace margin.
   - Sessions table (speaker name, clip count, photo count, start time, end time).
   - Photos table (filename, timestamp, assigned speaker, status).
   - Speaker Mapping text box, rebuilt in the standard paste format (`clip1.mp4, clip2.mp4 - Speaker Name`).
2. Disk verification against manifest:
   - Check clips in manifest against disk. Flag missing clips and new unmanifested files in an inline status message and table row tags.
   - Do not alter the saved mapping automatically; prompt for new files: "run Detect Sessions to include them".
3. If the user has already typed into the mapping box, ask before replacing it.
4. When switching to a different event, completely clear previous event tables and mapping box so no stale data carries over.
5. Stage 3 must display the same clips and speaker names immediately upon event selection without application restart.
6. If the manifest is missing, corrupt, or belongs to another event, display an inline message and leave tables empty without crashing.

---

## Root cause

1. **Stage 2 Incomplete Manifest Ingest:**
   - In `gui/stage2_tab.py`, `_apply_event_folder` only called `_maybe_fill_from_manifest(manifest_path)`, which populated 5 string fields in `EventContext` (name, date, organiser, venue, sponsors).
   - It completely omitted restoring session objects (`self.sessions`), photo objects (`self.photos`), the Treeview tables (`sess_tree`, `photo_tree`), algorithm thresholds (`gap_min_var`, `grace_min_var`), and `mapping_text`. As a result, the tables and mapping box remained empty until the user re-ran "Detect Sessions".
2. **Incomplete Event Switching Teardown:**
   - When switching to a folder without a manifest, `self.sessions` and `self.photos` were not reset, leaving residual data in memory and treeviews.
3. **Stage 3 Fatal Guard on Missing Files:**
   - In `gui/stage3_tab.py`, `_load_manifest` called `validate_clips_for_event(manifest_path, videos_dir)`. If any video file listed in the manifest was missing from disk, `validate_clips_for_event` reported fatal errors and Stage 3 aborted loading, clearing all clips instead of displaying the clips with status "File missing".

---

## What was changed

1. **Comprehensive Manifest Loader (`gui/stage2_tab.py`):**
   - Implemented `_load_from_manifest(manifest_path, ask_parent=True)`:
     - Verifies manifest validity and ensures `event_root` matches the selected event directory.
     - Loads metadata into `EventContext`.
     - Restores `gap_min_var` and `grace_min_var` from `manifest["settings"]`.
     - Deserializes `SessionGroup` and `PhotoItem` models and populates `sess_tree` and `photo_tree`.
     - Rebuilds paste-format speaker mapping in `mapping_text`: groups clips by speaker in start-time order and formats each block as `<clip1>, <clip2> - <Speaker Name>`.
     - Prompts user confirmation via `messagebox.askyesno` before overwriting `mapping_text` if it already contains user text.
2. **Disk Verification and Status Messaging (`gui/stage2_tab.py`):**
   - Compares manifest clips and photos against actual files on disk.
   - Highlights missing clip rows in `sess_tree` with a `missing` warning tag (yellow background).
   - Displays clear inline status messages detailing missing clips and untracked files on disk, with instructions to run "Detect Sessions" to incorporate new files.
3. **Clean Teardown on Event Switch (`gui/stage2_tab.py`):**
   - Implemented `_clear_event_data()`: purges all tree rows, clears `mapping_text`, resets `self.sessions` and `self.photos`, and resets separation summary state before loading a new event folder.
4. **Resilient Stage 3 Loading (`gui/stage3_tab.py`):**
   - Updated `_load_manifest` to distinguish between corrupted/foreign manifest schema errors and missing clip files on disk. If only clip files are missing from disk, Stage 3 still loads the manifest data, logs a warning, and flags missing clips with status "File missing" in the UI.

---

## Files touched

- `gui/stage2_tab.py`
- `gui/stage3_tab.py`
- `tests/test_stage2_stage3_fixes.py`

---

## How it was verified

1. **Automated Unit & Acceptance Tests (`tests/test_stage2_stage3_fixes.py`):**
   - `test_load_event_with_manifest_fills_all_data`: Verified event metadata, settings spinboxes, Sessions table, Photos table, and mapping text box are populated correctly.
   - `test_disk_verification_missing_clip_and_new_clip`: Verified deleting a clip and adding a new file flags both in status and tags the row as `missing`.
   - `test_switch_event_clears_previous_completely`: Verified switching to an event without a manifest completely empties all tables and the mapping text box.
   - `test_stage3_loads_same_clips_without_restart`: Verified Stage 3 displays the same clips and speaker names immediately upon event selection.
2. **Integration Verification (`scratch/verify_all.py`):**
   - Verified end-to-end manifest auto-load and paste-mapping formatting (`CLIP_A.mp4, CLIP_B.mp4 - Kalyani Patra`).
   - Verified disk check flags missing clips and new untracked files with recommendation to re-run Detect Sessions.
   - Verified event switching thoroughly clears all UI controls.
3. **Full Test Suite:**
   - All 79 tests in `tests/` passing cleanly.

