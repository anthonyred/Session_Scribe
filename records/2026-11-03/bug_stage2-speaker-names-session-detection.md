# Bug Record: Stage 2 Speaker Names & Authoritative Mapping During Session Detection

- **Date**: 2026-11-03
- **What was asked / symptom**:
  - The user pasted a mapping with five named speakers (*Jehosheba Amruthaluri*, *Surender Tanwar*, *Madhav Poosarla*, *Kishore Katta*, *Subhankar Dey*).
  - After clicking **Detect Sessions & Assign Photos**, the Sessions table displayed 8 rows named `Speaker 1` to `Speaker 8` (time-gap clusters with default names), and the status line reported *"Detected 8 sessions across 21 clips"*.
  - The applied mapping was discarded, and clips from different speakers that were filmed with a short gap were incorrectly merged into the same session (e.g. the 3rd speaker's last clip and 4th speaker's first clip merged into one gap group).
- **Root cause**:
  1. `_detect_thread_worker` in `gui/stage2_tab.py` called `grouper.group_clips(clips)` without passing any mapping or speaker data, completely ignoring both the mapping box and any previously assigned clip speakers.
  2. `group_clips` in `scribe_core/session_grouper.py` grouped clips purely by time gap and assigned default names `Speaker 1..N`. Furthermore, it calculated gap duration as `clip.start_time - prev_clip.start_time` (difference in start times) rather than idle time `clip.start_time - prev_clip.end_time`, causing clips with short intervals to be lumped into single sessions regardless of speaker identity.
  3. `_on_detection_complete` overwrote `self.sessions` with these generic `Speaker 1..8` sessions.
  4. In `assign_photos`, photos falling into overlapping session grace windows were assigned using the session midpoint `(start + end) / 2` rather than computing the true distance to the nearest clip in each session.
  5. In `_on_session_double_click`, renaming a speaker only mutated that single session in memory without renaming the person across their other sessions, without updating the mapping box, and without updating `manifest.json`.
- **What was changed / fix**:
  1. **Authoritative Mapping**:
     - `ClipInfo` now carries `speaker_name` and `person_id`.
     - When Detect Sessions runs, `_get_current_mapping_dict` extracts mapping from validated blocks, pasted mapping text, or existing clip assignments.
     - `SessionGrouper.group_clips` respects `mapping_dict`. Each session represents a contiguous run of clips of the *same person*. It splits whenever the speaker changes, or when the idle gap `clip.start_time - prev_clip.end_time` exceeds `gap_threshold_seconds`. In One speaker mode, a session never contains two people.
     - Unmapped clips form their own individual sessions named `Unnamed 1`, `Unnamed 2`, etc. (never `Speaker N`), and are highlighted in the Sessions table with the `unnamed_session` warning tag.
  2. **Separate Reset Mapping Action**:
     - Added a `Reset Mapping` button with a confirmation dialog that clears the mapping and marks all clips as Unnamed.
  3. **Photo Overlaps to Nearest Clip**:
     - Updated `SessionGrouper.assign_photos` so that when a photo falls within multiple session windows, it is assigned to the session whose individual clips are closest in absolute time difference. Photos outside all windows stay `Unassigned`.
  4. **Global Rename on Double-Click**:
     - Double-clicking a speaker name in the Sessions table renames the person across all of their sessions, updates all clips and photos of that speaker, updates the mapping text box, and silently updates `manifest.json` on disk.
  5. **Manifest Roundtrip**:
     - Manifest serialization stores `speaker_name` and `person_id` per clip. Loading restores the exact saved sessions with zero automatic re-detection. Legacy manifests with `Speaker N` load gracefully with backwards compatibility.
- **Files touched**:
  - `scribe_core/session_grouper.py`
  - `gui/stage2_tab.py`
- **How it was verified**:
  - Created unit test suite `tests/test_speaker_mapping_authoritative.py`:
    * Verified 5 named speakers generate 5 separate sessions.
    * Verified back-to-back clips of different people (5s gap) never merge.
    * Verified same person after a long gap (> 10m) forms two sessions with the same speaker name.
    * Verified unmapped clips form individual `Unnamed N` sessions.
    * Verified photo overlap assignment selects the nearest clip.
    * Verified manifest roundtrip serialization.
  - All unit tests pass.

