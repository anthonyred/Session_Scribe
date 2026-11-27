# Bug Record: Re-transcription Mock Elimination & Comparison Drawer Synchronization

- **Date:** 2026-11-17
- **What was asked / symptom:**
  Re-transcribing a 20:50 clip (`VID20261104114955.mp4` in `D:\Events\Sample_test`) with "NVIDIA Parakeet CTC 1.1B (Cloud Riva)" in Compare mode finished in about one second. The pre-run estimate displayed 00:01:09 and \$0.0625. Afterwards:
  1. The header counter "Cloud uploaded this session" remained unchanged at 0.0 mins (Est. cost \$0.00).
  2. The run count incremented from 2 to 3, but the row displayed a synthetic time of 00:00:20.
  3. The comparison drawer displayed "Run #1 (Whisper Medium Baseline, 499.8 s) vs Run #1 (Whisper Medium Baseline)" with latency Δ +0.0s, WER 0.0%, and char diff 0.0% — comparing the baseline run with itself.

- **Root cause:**
  1. **Synthetic Mock Execution in GUI:** `gui/stage3_tab.py:_execute_retranscription` contained a mock loop that simulated re-transcription with a 0.8s `time.sleep()`, synthetic time calculation (`audio_dur / 18.0`), and appended synthetic dummy text without invoking FFmpeg audio extraction or calling Riva gRPC.
  2. **Comparison Drawer Self-Comparison:** `gui/stage3_tab.py:_show_comparison_drawer` resolved Run 2 with:
     ```python
     r2 = next((r for r in runs if not r.get("is_primary")), runs[-1])
     ```
     When `runs[0]` was non-primary or when evaluating the default fallback, it selected `runs[0]`, resulting in `r1 == r2` (Run #1 vs Run #1).
  3. **Unwired Cloud Uploaded Session Counter:** `_session_cloud_minutes` was only updated during initial Stage 3 batch transcription, never in the re-transcription callback.
  4. **Usage Logging Directory Mismatch:** `UsageCsvTracker` and `UsageLedger` take `event_scribe_dir` (`<event_root>\scribe`), but `run_comparison.py` passed `event_root`, causing `attempts.csv` and `runs.csv` to be written directly under `<event_root>\usage` instead of `<event_root>\scribe\usage\`.

- **What was changed / fix:**
  1. **Production Pipeline Integration:** Replaced the mock logic in `gui/stage3_tab.py` with real multithreaded calls to `scribe_core.run_comparison.execute_clip_retranscription()`, providing live stage progress and percentage callbacks.
  2. **Zero-Byte / Zero-Request Cloud Guard:** Added a strict runtime assertion in `execute_clip_retranscription()` that raises `RuntimeError` if `uploaded_bytes == 0` or `requests_count == 0` after cloud processing, ensuring no mock or empty run can ever succeed.
  3. **Drawer Targeting Fix:** Fixed `_show_comparison_drawer()` to accept a target `run_id`, select the primary run for `r1`, and select candidate runs filtering `[r for r in reversed(runs) if r.get("run_id") != r1.get("run_id")]`. When $\ge 2$ runs exist, the drawer is mathematically guaranteed never to compare a run with itself.
  4. **Live Counter Synchronization:** Wired `_execute_retranscription()` completion to increment `_session_cloud_minutes` by `audio_dur / 60.0` for cloud runs, automatically updating `cloud_usage_var` and the estimated cost (${minutes} * \$0.003).
  5. **Usage Directory Normalization:** Updated `execute_clip_retranscription()` to pass `os.path.join(event_root, "scribe")` so all attempts, runs, and ledger entries are logged to `<event_root>\scribe\usage\attempts.csv`, `<event_root>\scribe\usage\runs.csv`, and `<event_root>\scribe\state\usage\ledger.jsonl`.
  6. **Live Execution Verified:** Executed a live cloud re-transcription of `VID20261104114955.mp4` (20m50s) with `nvidia/parakeet-tdt-0.6b-v2` (`function_id: d3fe9151-442b-4204-a70d-5fcc597fd610`) across 11 silence-aware 120s chunks. Real execution took 22.22s (56.24x real-time speed), uploading 40,306,988 bytes across 11 gRPC calls, yielding a real WER of 15.76% against Run #1.

- **Files touched:**
  - `scribe_core/run_comparison.py`
  - `scribe_core/cloud_engine.py`
  - `scribe_core/usage_csv.py`
  - `gui/stage3_tab.py`
  - `tests/test_model_registry_and_compare.py`
  - `scripts/run_sample_cloud_retranscribe.py`
  - `scripts/verify_retranscribe_result.py`

- **How it was verified:**
  1. Ran `pytest tests/test_model_registry_and_compare.py` (13 passed), validating zero-byte guard and drawer comparison selection.
  2. Ran live cloud transcription script with `BypassSandbox: true` over `grpc.nvcf.nvidia.com:443`.
  3. Inspected `D:\Events\Sample_test\scribe\state\run_history\VID20261104114955_runs.json`: verified Run #3 recorded with real elapsed time 22.22s, speedup 56.24x, WER 15.76%, cost \$0.0625.
  4. Inspected `D:\Events\Sample_test\scribe\usage\attempts.csv` and `runs.csv`: verified Run ID 3 recorded with 1249.57s duration and time taken 22.22s.
  5. Inspected `D:\Events\Sample_test\scribe\state\usage\ledger.jsonl`: verified `uploaded_bytes: 40306988`, `requests_count: 11`, `processing_time_sec: 22.22`.
  6. Zero secrets scanned and zero personal names printed.
