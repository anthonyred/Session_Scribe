# Build Record — Cloud Preflight Diagnostics, Test Transcription, and Visible Failovers

- **Date:** 2026-11-17
- **What was asked:**
  Build a dedicated "Test transcription" button using a bundled 5-second sample, automatic cloud preflight check (cached 10m), standardized 12-class failover classification (`auth`, `key_not_found`, `function_not_found`, `invalid_argument`, `timeout`, `rate_limit`, `quota`, `network_tls`, `egress_guard`, `consent`, `payload_too_large`, `server_error`), immediate fallback on configuration errors and retries on transient errors, visible failover banners, toasts, and Engine cell tooltips, unified key loader displaying `Key found: yes/no (source)`, mocked gRPC unit tests, `LICENSE` file, and `README.md` amendments.
- **Why:**
  Prevent silent or opaque cloud failures, eliminate discrepancies between simple ping tests and full RPC calls, and provide full operational visibility across logs, UI banners, tooltips, and `attempts.csv`.
- **What was built (user-visible behaviour):**
  1. **"Test transcription" Button:** Added to Stage 3 Connections alongside "Test Key". Executes the real cloud path stage-by-stage (Sample Audio Prep, Request Build, Connection & Auth, Cloud Transcription Call, Response Parsing) with a bundled 5s dummy sample WAV without uploading event media. Displays a modal dialog with individual stage statuses and timings.
  2. **Automatic Preflight Check:** Before any cloud run, Stage 3 runs the preflight test automatically with 10-minute caching. If preflight fails, it notifies the user before clip 1, logs the reason, and starts the run deliberately on Local Whisper medium.
  3. **Standardized 12 Failover Classes:**
     `auth`, `key_not_found`, `function_not_found`, `invalid_argument`, `timeout`, `rate_limit`, `quota`, `network_tls`, `egress_guard`, `consent`, `payload_too_large`, `server_error`.
  4. **Immediate Fallback vs Retries:**
     - Configuration errors (`auth`, `key_not_found`, `function_not_found`, `invalid_argument`, `egress_guard`, `consent`) fall back immediately with `retries=0` and disable cloud for the remainder of the run.
     - Transient errors (`timeout`, `network_tls`, `server_error`, `rate_limit`, `payload_too_large`) retry up to 3 times with exponential backoff (`[2s, 4s, 8s]`) before falling back.
  5. **Visibility Across UI:**
     - Persistent warning banner above the clip table for the duration of the run.
     - Toast notification popup on fallback handover.
     - Engine column cell tooltip detailing error class, message, and local resume offset timestamp (`hh:mm:ss.mmm`).
     - Structured logging in `transcription.log` and `attempts.csv`.
  6. **Unified Key Loader:** Shared `get_nvidia_api_key_info()` function used by both GUI and worker subprocess. Displays `Key found: yes (environment)`, `Key found: yes (.env)`, or `Key found: no` in Connections without exposing the key.
  7. **Legal & Documentation:** Created `LICENSE` file ("All rights reserved © 2026 Anthony Reddy"), updated `README.md` troubleshooting table with all 12 classes, documented 30-minute auto-lock default, and updated `.env.example`.
- **How to use it:**
  - In Stage 3, expand Connections to check `Key found: yes (.env)`. Click "Test transcription" to verify full pipeline readiness.
  - When starting a Cloud run, preflight validates credentials and functions automatically.
  - If a network drop or provider error occurs, watch the persistent red/amber banner and check the Engine cell tooltip for handover diagnostics.
- **Settings and defaults:**
  - `timeout_base`: 15.0s
  - `timeout_factor`: 2.0x audio duration
  - `single_request_limit_sec`: 600.0s
  - `chunk_duration_sec`: 120.0s
  - Preflight cache TTL: 600.0s (10 minutes)
- **Files changed:**
  - `scribe_core/cloud_engine.py`
  - `scribe_core/transcribe_worker.py`
  - `scribe_core/resources/sample_5s.wav`
  - `gui/stage3_tab.py`
  - `tests/test_cloud_failover_visibility.py`
  - `scripts/verify_step3.py`
  - `LICENSE`
  - `README.md`
  - `.env.example`
- **Limitations and known issues:**
  - Network-dependent: Cloud transcription requires active internet connectivity to `grpc.nvcf.nvidia.com:443`.
- **How it was verified:**
  - Comprehensive unit test suite `tests/test_cloud_failover_visibility.py` with mocked gRPC servers for all 12 error classes (100% pass).
  - Live verification harness `scripts/verify_step3.py` with real NVIDIA API key: 5s, 2m, and 9m dummy clips all completed on Cloud at 55x-58x real-time speed with zero fallbacks.
- **How to roll it back:**
  - Revert changes to `scribe_core/cloud_engine.py`, `scribe_core/transcribe_worker.py`, and `gui/stage3_tab.py` via git.

