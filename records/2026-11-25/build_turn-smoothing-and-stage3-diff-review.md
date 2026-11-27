# Work Record: Speaker Turn Smoothing & Stage 3 Diff Review

- **Date:** 2026-11-25
- **Type:** `build`
- **What was asked:**
  1. **Review Stage 3 Final Diff:** Review the final diff of `gui/stage3_tab.py` to confirm there are no stray or half-finished edits from earlier revert attempts and report findings.
  2. **Add Turn Smoothing to Speaker Assignment:** Merge speaker islands shorter than a configurable minimum (default 0.7 s) into the surrounding turn when both neighbours are the same speaker. Never smooth across or into an `[Overlapping]` line. Add unit tests covering jittery word-level input.
  3. Run fast tests, add the work record, and report briefly.
- **Why:**
  - Jittery acoustic predictions and boundary artifacts in diarization can produce brief sub-second misclassifications (e.g. 0.2s glitch to another speaker) during a single speaker's continuous turn. Merging these short islands into the surrounding speaker eliminates noisy turn fragmentation.
  - Overlapping speech segments (`[Overlapping: ...]`) reflect actual dual-speaker dialogue or cross-talk and must never be smoothed away or merged across.
  - Code hygiene requires confirming that `gui/stage3_tab.py` contains zero stray definitions, duplicated methods, or incomplete edits.
- **What was built (user-visible behaviour):**
  - **Speaker Turn Smoothing (`smooth_speaker_turns`):**
    - Identifies speaker islands where `(end - start) < min_island_sec` (default 0.7s) whose preceding and following neighbours are identical in speaker identity and role (`prev.speaker == next.speaker` and `prev.role == next.role`).
    - Smooths the island into the surrounding turn by reassigning its speaker and collapsing adjacent segments.
    - Strictly preserves `[Overlapping]` lines: never smooths if `prev`, `curr`, or `next` is overlapping.
    - Preserves short interjections between different speakers (e.g. Alice -> Bob ("Yes.") -> Carol).
    - Preserves turns $\ge min\_island\_sec$.
  - **Audited `gui/stage3_tab.py`:**
    - Full AST analysis confirmed 0 syntax errors, 0 duplicate class methods, 0 duplicate module functions, and 0 merge conflict markers.
    - All recent integrations (`_resolve_clip_speaker_display`, `_on_vault_state_changed`, `_refresh_status_column`, `_export_comparison_clicked`, `_get_clip_time_taken`) are intact, syntactically clean, and fully operational.
- **How to use it:**
  - Turn smoothing runs automatically inside `assign_diarization_to_words(words, turns, roster, min_island_sec=0.7)`.
  - Pass `min_island_sec=0.0` to disable smoothing or supply a custom threshold in seconds.
- **Settings and defaults:**
  - `DEFAULT_MIN_ISLAND_DURATION_SEC = 0.7`
- **Files changed:**
  - `scribe_core/multi_speaker.py`: Added `DEFAULT_MIN_ISLAND_DURATION_SEC`, `smooth_speaker_turns()`, and integrated smoothing into `assign_diarization_to_words()`.
  - `tests/test_multi_speaker.py`: Added 4 unit tests covering jittery word-level input, overlap preservation, interjections between different speakers, and turns above threshold.
- **How it was verified:**
  - Fast test suite:
    - `tests/test_multi_speaker.py`: 14 tests, all passing in 5.8s.
    - `tests/test_vault_usability_and_pipeline.py`: 7 tests, all passing in 7.5s.
  - Total: 21 tests passing cleanly.
- **How to roll it back:**
  - Revert changes to `scribe_core/multi_speaker.py` and `tests/test_multi_speaker.py`.
