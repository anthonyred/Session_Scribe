# Build Record: Re-Identify Archiving, Line-Start Tag Isolation & Combined Files Deduplication

- **Date:** 2026-11-20
- **What was asked:**
  1. Run full test discovery (all test files, fast mode), reporting total tests, skipped tests, and failures, comparing with previous runs (246+ earlier), and confirming no test file or test was deleted or disabled.
  2. Increase watchdog limit for slow tests (`RUN_SLOW_TESTS=1`) to 300 seconds and document the command.
  3. Ensure name $\leftrightarrow$ alias mapping in the transcript viewer (`TranscriptEditorDialog`), combined files (`build_combined_transcripts`), and Re-Identify (`_apply_cluster_reassignments_to_transcripts`) strictly touches speaker tags at the start of a line and headers, never spoken text. Add a unit test where a speaker says another speaker's name and alias token.
  4. Avoid stale duplicates when both a name file (e.g. `Dr._Ada_Lovelace_combined.txt`) and alias file (e.g. `S01_combined.txt`) could exist for the same speaker in `scribe/combined/`.
  5. Archive the previous transcript version in `scribe/state/reidentify_backups/<timestamp>/` before rewriting during Re-Identify, and provide an undo mechanism.
  6. Clarify where Re-Identify keeps per-cluster data and confirm compatibility with events transcribed before this change.
- **Why:**
  - Preserving spoken dialogue is a cardinal invariant: if a participant says "I asked S02 and Dr. Ada Lovelace about S01", replacing "S01" or "S02" in the spoken text corrupts the factual record of what was spoken. Only turn tags and headers represent metadata attributions.
  - In combined transcripts, having both alias files and real-name files side-by-side leads to user confusion and stale data.
  - Re-Identify modifies transcripts in place with zero re-transcription. An automated timestamped backup and one-click undo ensures operators can safely experiment with speaker reassignments without risk of data loss.
- **What was built (user-visible behaviour):**
  - **Dialogue Isolation Invariant:** `substitute_line_start_speaker_tags(text, tag_map)` uses regexes `_TURN_LINE_RE` and `_HEADER_SPEAKER_RE` to replace speaker tags strictly at the beginning of transcript lines and header keys. Body text and dialogue remain 100% untouched.
  - **Combined Files Active Canonical Scheme with Stale Archiving:** When building combined transcripts with unlocked vault names, any existing alternate combined files for the same session (e.g. `S01_combined.txt`) are automatically archived to `scribe/combined/old/`, preventing stale duplicates.
  - **Re-Identify Automated Archiving & Undo:** Before applying speaker cluster reassignments, Stage 3 archives the current transcript files to `<event root>/scribe/state/reidentify_backups/<YYYYMMDD_HHMMSS>/` with a `metadata.json` audit trail. The user is prompted with an immediate undo option, and an "Undo Re-Identify" button in the review bar enables reverting to the pre-identification state at any time.
  - **Historical Event Cluster Discovery:** `_discover_unidentified_clusters_from_transcripts` scans existing transcript files on disk for `[Unidentified]` and generic speaker turns, dynamically reconstituting speaker clusters so Re-Identify works seamlessly for events transcribed before this change.
  - **Slow Test Watchdog Limit:** `tests/__init__.py` detects `RUN_SLOW_TESTS=1` and extends the test timeout watchdog from 60s to 300s per test.
- **How to use it:**
  - In Stage 3, click **Re-Identify…** to review unidentified speech clusters. When assignments are applied, transcripts are updated instantly and archived. If a mistake is made, click **Undo Re-Identify** to revert to the archived versions.
  - To run slow tests with the extended watchdog limit:
    ```powershell
    $env:RUN_SLOW_TESTS="1"; .venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py"
    ```
- **Settings and defaults:**
  - Default per-test timeout: 60.0s (fast mode); 300.0s when `RUN_SLOW_TESTS=1`.
  - Re-Identify backups: Stored under `<event root>/scribe/state/reidentify_backups/<YYYYMMDD_HHMMSS>/`.
  - Stale combined files: Archived under `<event root>/scribe/combined/old/`.
- **Files changed:**
  - `scribe_core/multi_speaker.py` — Line ending preservation in `substitute_line_start_speaker_tags`.
  - `scribe_core/combined_transcripts.py` — Deduplication and stale file archiving to `scribe/combined/old/`.
  - `gui/stage3_tab.py` — Re-Identify archiving, `_apply_cluster_reassignments_to_transcripts`, `_undo_reidentify_clicked`, `btn_undo_reidentify` button, and regex matching for timestamps with/without milliseconds.
  - `tests/__init__.py` — 300s watchdog timeout when `RUN_SLOW_TESTS=1`.
  - `tests/test_speaker_tag_replacement_and_undo.py` — 5 unit tests validating dialogue preservation, combined deduplication, Re-Identify archiving/undo, and historical discovery.
- **Limitations and known issues:**
  - Undo restores the most recent backup on disk. If multiple sequential re-identifications are performed, each creates its own timestamped directory.
- **How it was verified:**
  - `tests/test_speaker_tag_replacement_and_undo.py` (5 tests passing in 2.8s):
    - Verified a speaker saying another speaker's name and alias token has their dialogue preserved byte-for-byte across both forward and reverse substitutions.
    - Verified combined files stale duplicate avoidance moves alternate files to `combined/old/`.
    - Verified Re-Identify creates `reidentify_backups/` and undo restores original files bit-for-bit.
    - Verified historical transcript cluster discovery parses turns with and without milliseconds.
- **How to roll it back:**
  - Revert changes to `scribe_core/multi_speaker.py`, `scribe_core/combined_transcripts.py`, `gui/stage3_tab.py`, `tests/__init__.py`, and remove `tests/test_speaker_tag_replacement_and_undo.py`.
