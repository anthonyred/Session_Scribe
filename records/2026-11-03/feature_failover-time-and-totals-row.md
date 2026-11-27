# Feature Record: Failover Time Measurement & Locked Totals Row

- **Date:** 2026-11-03
- **What was asked:**
  1. Measure failover handover time when the cloud engine fails and the local engine takes over a clip. Record `first_error_at`, `fallback_decided_at`, `local_started_at`, `fallback_reason`, `retries`, `retry_wait_seconds`, `local_model_load_seconds`, and `resume_offset`. Display failover time in a new "Failover time" column in the Stage 3 clip table formatted as `hh:mm:ss.s`. Clips with no fallback or pure local show `"—”`. Provide a detailed breakdown on hover. Print an end-of-run log summary with count, average, and longest failover times.
  2. Add a locked totals row below the Stage 3 clip table that stays visible outside the scrolling table. Columns and text alignments must match the table above and stay synchronized on resize and horizontal scroll. Display "Total (N clips)", total duration (`hh:mm:ss`), total time taken (`hh:mm:ss`), total failover time (`hh:mm:ss.s`), and a summary note such as "17 of 21 clips timed".
- **Why:**
  Users need transparent operational visibility into cloud-to-local failovers, understanding exactly how much time was lost to network retries and model loading during fallback. Furthermore, event managers need an immediate summary of overall audio duration, total processing time, and failover overhead across the entire session without manual calculation.
- **What was built (user-visible behaviour):**
  - **Failover time column:** Added at index 6 in the Stage 3 clip table, centered, displaying `hh:mm:ss.s` when failover occurs and `"—”` for pure local or successful cloud runs.
  - **Failover breakdown tooltip:** Hovering over any failover cell shows reason, retry count and wait time, local model load duration, resume offset, and total handover time.
  - **End-of-run log summary:** Emits `[FAILOVER SUMMARY] Fallback occurred on N clip(s). Avg handover: X.Xs, Longest: Y.Ys (clip_name)`.
  - **Locked totals row:** Positioned directly below the clip table, separated by a distinct top border, with dark text on `#E5E7EB` gray background. Updates dynamically as clips complete or manifest reloads.
- **How to use it:**
  - Run Stage 3 with Cloud engine enabled. If a failure occurs (e.g. timeout or credits), the table automatically populates handover duration.
  - Hover over the failover timestamp to view the diagnostic tooltip.
  - Inspect the totals row at the bottom of the table to see session-wide clip count, duration, processing time, and total failover overhead.
- **Settings and defaults:**
  - "Failover time" column width: 105px (minwidth 90px, center-aligned).
  - Scaled timeout formula: $\max(\text{Base}, \text{Audio Duration} \times \text{Factor})$, default base `15.0s`, factor `2.0\times`.
- **Files changed:**
  - `scribe_core/cloud_engine.py`: Structured `CloudTranscriptionError` with diagnostics and timeout parameter.
  - `scribe_core/clip_state.py`: Added `failover_info` and `failover_time_sec` fields to `write_clip`.
  - `scribe_core/session_tracker.py`: Added failover metadata storage.
  - `scribe_core/transcribe_worker.py`: Implemented three-timestamp handover timing, local model load duration, and end-of-run summary log.
  - `scribe_core/transcription_manager.py`: Added `timeout_base` and `timeout_factor` to `prepare_job_config`.
  - `gui/stage3_tab.py`: Added column to `clip_tree`, failover formatting, tooltip breakdown, and built `self.totals_tree` with column sync and live update handlers.
  - `tests/test_failover_and_totals.py`: Comprehensive test suite for failover metrics and totals row.
  - `tests/test_stage2_stage3_fixes.py`: Updated column value indices to match new table structure.
  - `tests/test_transcript_replace_and_zoom.py`: Updated column alignment tests.
- **Limitations and known issues:**
  - When sticky fallback is active, subsequent clips run directly on local Whisper without experiencing cloud failures, so their failover time is `"—”`.
- **How it was verified:**
  - Automated tests in `tests/test_failover_and_totals.py` (12 tests passed).
  - Automated tests in `tests/test_stage2_stage3_fixes.py` (23 tests passed).
  - Column alignment tests in `tests/test_transcript_replace_and_zoom.py` (9 tests passed).
- **How to roll it back:**
  - Revert changes to `gui/stage3_tab.py`, `scribe_core/transcribe_worker.py`, and `scribe_core/clip_state.py`.

