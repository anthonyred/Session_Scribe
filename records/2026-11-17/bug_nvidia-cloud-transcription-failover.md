# Bug Record — NVIDIA Cloud Transcription Immediate Fallback to Local Whisper

- **Date:** 2026-11-17
- **What was asked / symptom:**
  In Stage 3, NVIDIA "Test connection" succeeded, but real cloud transcription on event clips immediately failed and fell back to local Whisper medium.
- **Root cause:**
  Two distinct code-level defects were uncovered through evidence-based analysis:
  1. **Primary Defect (`scribe_core/transcribe_worker.py:577`):**
     In `transcription_manager.py`, the configuration dictionary was named `job_data`, while inside `transcribe_worker.py` the argument passed to `run_transcription` is named `job`. Lines 577-578 referenced `job_data.get("timeout_base")` and `job_data.get("timeout_factor")`, immediately raising `NameError: name 'job_data' is not defined` before any gRPC call was made. The exception handler caught this `NameError`, misclassified it as `"server error"`, and triggered fallback to local Whisper medium.
  2. **Secondary Defect (`scribe_core/transcribe_worker.py:874`):**
     If a clip yielded 0 speech segments (e.g. silence or resuming past checkpoint), the segment formatting loop `for seg in segments:` never executed, leaving `seg` unassigned and crashing with `UnboundLocalError: cannot access local variable 'seg' where it is not associated with a value`.
- **What was changed / fix:**
  1. Replaced `job_data.get(...)` with `job.get(...)` in `scribe_core/transcribe_worker.py:577`.
  2. Added an explicit `if segments:` guard before extracting `avg_logprob`, `no_speech_prob`, and `compression_ratio` from `segments[-1]`.
- **Files touched:**
  - `scribe_core/transcribe_worker.py`
- **How it was verified:**
  1. Unit tests in `tests/test_cloud_failover_visibility.py` verifying scaled timeout extraction from `job` dictionary and empty segments confidence guard.
  2. Step 3 live verification using real NVIDIA API key on 5s, 2m, and 9m dummy clips in `scripts/verify_step3.py`: all clips transcribed completely on Cloud without triggering fallbacks.

