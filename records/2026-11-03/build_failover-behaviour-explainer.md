# Build Record: Failover Behaviour Explainer & Specification Comparison

**Date:** 2026-11-03  
**File:** `records/2026-11-03/build_failover-behaviour-explainer.md`  
**Author:** AI Systems Engineer  

---

## Part 1: How Failover Works in Code Today (Plain-Language Explainer)

This section explains in plain language exactly how Session Scribe detects failures, manages retries, falls back to the local engine, stitches transcripts, and measures handover time based on the active codebase.

---

### 1. How the App Notices a Failure (Error Detection & Classification)

When transcribing with the hosted cloud engine (`NVIDIA Hosted Riva` via gRPC), every remote call to `offline_recognize` is monitored inside a structured error-handling wrapper in `scribe_core/cloud_engine.py` and `scribe_core/transcribe_worker.py`.

The system inspects incoming gRPC status codes, HTTP status codes, and exception string patterns to categorize failures into six distinct reasons:

1. **Authentication (`auth`):**
   - **Trigger:** gRPC status `UNAUTHENTICATED`, HTTP status `401`, or error details containing `"unauthenticated"`, `"invalid api key"`, or `"unauthorized"`.
   - **Meaning:** The API key is missing, invalid, or revoked.
2. **Credits / Quota Exhaustion (`credits`):**
   - **Trigger:** gRPC status `RESOURCE_EXHAUSTED` (without rate-limit keywords), HTTP status `402`, or details containing `"credits"`, `"quota"`, `"balance"`, or `"payment required"`.
   - **Meaning:** The account balance has run out or the billing limit was reached.
3. **Rate Limits / Concurrency (`rate limit`):**
   - **Trigger:** gRPC status `RESOURCE_EXHAUSTED` with `"rate limit"`, `"too many requests"`, or HTTP status `429`.
   - **Meaning:** Concurrency limit reached or requests per second exceeded.
4. **Timeouts (`timeout`):**
   - **Trigger:** gRPC status `DEADLINE_EXCEEDED`, socket timeouts, or exception details containing `"timed out"`, `"deadline exceeded"`.
   - **Meaning:** Triton/Riva took longer to process the request than the scaled timeout deadline.
5. **Connection Failures (`connection`):**
   - **Trigger:** gRPC status `UNAVAILABLE`, dns resolution failures, network socket resets, or details containing `"unavailable"`, `"connection reset"`, `"handshake failed"`.
   - **Meaning:** Remote endpoint unreachable or internet connection dropped.
6. **Server / Triton Errors (`server error`):**
   - **Trigger:** gRPC status `INTERNAL`, `INVALID_ARGUMENT` (e.g. Triton audio decoder exception), `UNKNOWN`, or HTTP status `500`/`503`.
   - **Meaning:** Remote Triton server threw an unrecoverable decoding exception or internal crash.

---

### 2. Retries vs. Immediate Fallback

Not all errors are retried. Retrying permanent errors wastes time and user credits, whereas transient network hiccups benefit from exponential backoff:

| Failure Type | Retried? | Schedule & Backoff | Action on Exhaustion |
| :--- | :--- | :--- | :--- |
| **Auth (`auth`)** | **No (Immediate)** | 0 retries; gives up instantly | Immediately falls back to local Whisper; sticky local set |
| **Credits (`credits`)** | **No (Immediate)** | 0 retries; gives up instantly | Immediately falls back to local Whisper; sticky local set |
| **Rate Limit (`rate limit`)** | **Yes** | Up to 3 attempts (backoffs: 2.0s, 4.0s, 8.0s) | If still failing after 3 attempts, falls back to local Whisper |
| **Timeout (`timeout`)** | **Yes** | Up to 3 attempts (backoffs: 2.0s, 4.0s, 8.0s) | If still failing after 3 attempts, falls back to local Whisper |
| **Connection (`connection`)** | **Yes** | Up to 3 attempts (backoffs: 2.0s, 4.0s, 8.0s) | If still failing after 3 attempts, falls back to local Whisper |
| **Server Error (`server error`)** | **No** | 0 retries; gives up instantly | Immediately falls back to local Whisper |

When a retry occurs, the worker pauses (`time.sleep`) for the prescribed backoff duration and increments `retry_wait_seconds`. If all retries fail or if an immediate fallback error is raised, the function marks `fallback_decided_at` and initiates local failover.

---

### 3. Scaled Per-Request Timeout

To prevent the application from hanging indefinitely if a remote Triton server drops a stream, requests use a dynamic scaled timeout rather than a static timeout:

- **Formula:**
  $$\text{timeout\_sec} = \max(\text{Base Timeout}, \text{Audio Duration} \times \text{Duration Factor})$$
- **Default Values:**
  - **Base Timeout:** `15.0` seconds (minimum floor).
  - **Duration Factor:** `2.0` (accounts for network transmission and transcription speed).
  - **Example 1:** A 30s chunk $\rightarrow \max(15.0, 30 \times 2.0) = 60.0$ seconds.
  - **Example 2:** A 5s chunk $\rightarrow \max(15.0, 5 \times 2.0) = 15.0$ seconds.
- **UI Settings Exposure:**
  - Located in Stage 3 under **Transcription Engine & Provider Settings** $\rightarrow$ **Request Timeout**.
  - Exposes interactive spinboxes for **Base (s)** (5s–120s) and **Duration Factor** ($1.0\times$–$10.0\times$).

---

### 4. What Happens to Already-Transcribed Text (Seam Stitching & Resumption)

When a clip fails partway through:

1. **Saved Progress:**
   - Any audio slices processed prior to the failure are stored in `all_segments` and checkpointed in `scribe\state\session_state.json` (and `clip_state.json`).
   - The timestamp `completed_up_to` marks the last successfully transcribed second (e.g. `60.0s`).
2. **Local Resume:**
   - The local engine (`faster-whisper` medium on GPU or CPU) takes over from `cur_pos` (the beginning of the failed chunk).
   - In `stitch_transcript_segments`:
     - Any new local segment generated with local relative timestamps $[0.0, T]$ is shifted by the offset:
       $$\text{absolute\_start} = \text{local\_start} + \text{cut\_offset}$$
       $$\text{absolute\_end} = \text{local\_end} + \text{cut\_offset}$$
     - Any segment that starts prior to `resume_offset` is skipped or clamped, preventing duplicate words at the boundary.
3. **Word-Boundary Preservation:**
   - Segments are assembled using silence-gap splitting ($\ge 0.5$s silence gap) and sentence terminators (`.`, `?`, `!`) to preserve natural phrasing without clipped words.

---

### 5. What Happens When a Clip Has Multiple Failures

If a clip encounters multiple errors:
- If cloud retries fail, failover transitions to local Whisper medium.
- If local Whisper medium encounters an error (e.g. corrupted audio or out-of-memory), the clip status is marked as `Failed` in the state file and the UI clip table.
- Mid-clip checkpoints remain preserved in `session_state.json`. When the user restarts or re-runs, the system detects `completed_up_to` and resumes rather than starting from 0.0s.

---

### 6. How Failover Time is Measured and Reported

Failover time measures the total elapsed duration from when the first cloud error occurred on that clip, through all retries/backoffs and local model loading, until local Whisper decoding begins:

1. **Three Measured Timestamps:**
   - `first_error_at`: When the first cloud attempt failed.
   - `fallback_decided_at`: When all retries were exhausted (or immediate failure occurred) and fallback was decided.
   - `local_started_at`: The exact moment before local decoding begins (including any model loading time if local Whisper wasn't already loaded in VRAM).
2. **Handover Formula (Task B Definition):**
   $$\text{Headline Failover Time} = \text{local\_started\_at} - \text{first\_error\_at}$$
   *(Note: The handover breakdown tooltip and log preserve the full timeline, including `fallback_decided_at`, retries, and retry sleep wait time).*
3. **Persisted Metrics (in `scribe\state\clip_state.json`):**
   - `fallback_reason`: (`auth`, `credits`, `rate limit`, `timeout`, `connection`, `server error`)
   - `retries`: Number of retries attempted (0 for auth/credits/server error; up to 3 for timeout/rate limit).
   - `retry_wait_seconds`: Total seconds spent waiting in retry backoff sleep.
   - `local_model_load_seconds`: Time spent loading Whisper medium into GPU memory.
   - `resume_offset`: Audio timestamp in seconds where local decoding picked up.
   - `failover_time_sec`: Handover time in seconds.
4. **UI Display:**
   - **Clip Table Column:** **Failover time** formatted as `hh:mm:ss.s`. Clips with pure local or successful cloud display `"—”` (em-dash), never `0.0s`.
   - **Hover Tooltip:** Shows detailed breakdown: reason, retries attempted, retry wait duration, local model load time, resume offset, and total handover time.
   - **End-of-Run Summary Log:** When fallovers occur, prints count of affected clips, average handover time, and longest handover time.

---

### 7. What Happens on the Next Clip After a Failover (Sticky Fallback)

- **Sticky Mechanism:** Once cloud fails on any clip (whether auth, credits, or exhausted retries), `sticky_local_fallback = True` is activated in the transcribe worker.
- **Subsequent Clips:** All subsequent clips in the batch automatically bypass cloud and run directly on local Whisper medium.
- **Failover Time for Subsequent Clips:** Because subsequent clips never attempt cloud and never experience a cloud failure, their failover time is `None` and displays `"—”` in the table.
- **Sticky Reset:** Sticky local is scoped to the run. It resets when:
  - The job completes or is cancelled.
  - The user manually selects Cloud engine and clicks "Run All Clips" or "Transcribe 1-2 Test Clips" for a new run.

---

## Part 2: Specification Comparison Table

This section audits the current implementation against the original specification item by item:

| # | Specification Item | Current Code Status | Detailed Findings & Differences |
| :-: | :--- | :--- | :--- |
| **1** | **Whole-clip requests by default, with ~120s checkpointed pieces as a selectable checkbox option.** | **DIFFERENT** | **Code behavior:** The code currently slices all audio into discrete chunk windows (`window_size`, 30s or 120s if checkpoint box is checked), even when checkbox is off. It does not send entire 5–9 minute files in a single gRPC payload.<br>**Difference:** Slices audio in chunks regardless of checkbox state, whereas spec asked for whole-clip by default. |
| **2** | **2–3 retries with exponential backoff on transient errors before giving up on cloud.** | **MATCHES SPEC** | **Code behavior:** Up to 3 attempts with exponential backoff (`[2.0s, 4.0s, 8.0s]`) for `timeout`, `rate limit`, and `connection` failures before giving up. |
| **3** | **Immediate fallback on auth or credit errors without retries.** | **MATCHES SPEC** | **Code behavior:** `auth` (401) and `credits` (402 / resource exhausted) immediately break retry loop with 0 retries and trigger instant local fallback. |
| **4** | **Sticky local fallback: once cloud fails on any clip, subsequent clips use local Whisper medium automatically.** | **MATCHES SPEC** | **Code behavior:** `sticky_local_fallback = True` is set on first failure; subsequent clips bypass cloud and transcribe via local Whisper medium. Resets on new job run. |
| **5** | **Checkpoints saved in per-event state file after each piece, storing accumulated text and timestamps so far.** | **MATCHES SPEC** | **Code behavior:** `scribe_core/session_tracker.py` writes to `session_state.json` / `clip_state.json` after every chunk with `completed_up_to`, `chunks`, and `full_transcript`. |
| **6** | **Resume decoding on local Whisper medium from last complete segment (minus 0.5s for audio context), with timestamps shifted to maintain continuity.** | **DIFFERENT** | **Code behavior:** The code currently resumes from `cur_pos` (the start of the failed audio chunk window) rather than inspecting the sub-second end of the last discrete Whisper segment minus 0.5s.<br>**Difference:** Resumes at chunk window boundary rather than last segment timestamp minus 0.5s. |
| **7** | **In-progress text appended atomically to the clip's final .txt file as checkpoints pass, so a crash leaves partial work on disk.** | **DIFFERENT** | **Code behavior:** Currently, in-progress checkpoints are written atomically to `session_state.json` (state file) during execution. The final `.txt` file in `scribe\transcripts\` or `scribe\test\` is written atomically at the end of the clip completion via `write_transcript`.<br>**Difference:** Intermediate text is persisted in JSON state file, but not yet appended incrementally to the `.txt` transcript file on disk prior to clip completion. |
| **8** | **Per-clip state tracking so a crashed or interrupted run resumes from the last completed clip (or last checkpoint if chunked).** | **MATCHES SPEC** | **Code behavior:** `session_tracker.py` and `clip_disk_status` check both completed `.txt` files and mid-clip checkpoints (`completed_up_to`). An interrupted run skips finished clips and resumes partially transcribed clips at `completed_up_to`. |

---

## Part 3: Summary of Identified Differences

As required by user instructions, the following differences exist between earlier architectural prompts and current implementation:
1. **Whole-clip vs. chunked default:** Audio is currently divided into window slices (30s or 120s) rather than attempting a single whole-file gRPC request by default.
2. **Resume boundary:** Local failover resumes from the start of the failed chunk (`cur_pos`) rather than extracting from `last_segment_end - 0.5s`.
3. **Intermediate disk .txt writes:** Checkpoint progress is saved in `session_state.json`, but partial `.txt` files are only written upon clip completion.

*Per user rules, none of these differences are modified without explicit user confirmation.*

