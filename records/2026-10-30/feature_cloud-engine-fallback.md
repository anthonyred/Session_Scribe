# Feature: Hosted Cloud Engine with Automatic Fallback to Local Whisper

**Date:** 2026-10-30  
**Type:** feature  
**Record:** `records/2026-10-30/feature_cloud-engine-fallback.md`

---

## 1. What was asked / requested

Add a hosted cloud speech-to-text engine as a selectable option before each Stage 3 run, with automatic, seamless fallback to the local Whisper medium model that continues from the exact last saved checkpoint offset.

Key constraints & requirements:
1. **Free options only**: Cloud provider is NVIDIA hosted Riva gRPC via `build.nvidia.com` with the user's existing key.
2. **Provider profiles in settings**: Profile dataclass supporting protocol type (`riva_grpc` or `openai_http`), endpoint, function or model ID, language, boosting supported. Configured for Parakeet TDT 0.6B v2 (English) and Whisper Large v3 (Multilingual), plus disabled template profile for Nemotron 3.5 Transcribe.
3. **Stage 3 Engine UI**:
   - Engine selector (Local Whisper Medium vs Cloud NVIDIA Hosted Riva).
   - Profile selector, masked API key entry with Show/Hide toggle, "Save Key" button (to git-ignored `.env`).
   - "Test connection" button with feedback.
   - "Check NVIDIA speech models" button opening modal dialog querying NVCF catalog and highlighting Nemotron 3.5 Transcribe if present.
   - Session cloud uploaded minutes display: `Cloud uploaded this session: X.X mins (Est. cost: $0.00)`.
   - Privacy confirmation dialog before first cloud run in a session.
4. **Hybrid request strategy**:
   - Single request for clips $\le 10$ minutes (`single_request_limit_sec = 600.0`).
   - Pieces of ~120s with ~1s overlap for longer clips, or when user checks "Checkpoint every clip".
   - Silence-aware chunking (splits cleanly at pauses $\ge 0.5$s in search window when possible).
   - Checkpoint saved in `scribe\state\clip_state.json` after each piece.
   - Discard checkpoint if audio file mtime or size changed.
5. **Exact local fallback & resume**:
   - Retries on transient errors (timeouts, 429, 5xx): 2-3 retries with backoff.
   - Immediate fallback on auth failure (401 / PermissionDenied) or quota/credit exhaustion (`"NVIDIA free credits or limit reached, continuing locally"`).
   - Once cloud fails, switch to local Whisper medium for the rest of the run.
   - Exact resume:
     - Failure before any output: runs locally from 0.0s.
     - Failure after N segments / mid-clip: cut audio at `max(0.0, resume_offset - 0.5)`, transcribe remainder, drop local segments before resume offset, stitch with saved cloud segments.
     - Log resume point: `"resumed locally from HH:MM:SS"` or `"from clip start, no partial output received"`.
   - Lazy model loading: Local Whisper medium is not loaded into GPU VRAM upfront if Cloud is selected; only loaded lazily if fallback occurs.
6. **Output format & secrecy**:
   - Output format in `.txt` files identical to local runs.
   - Transcripts folder holds only `.txt` files.
   - Engine recorded in state file (`engine`, `provider`, `model_id`), logs, and new "Engine" column in Stage 3 clip list.
   - API key never stored in repo, logs, state files, or reports.

---

## 2. What was changed

### Core Architecture & Modules

1. **`scribe_core/cloud_engine.py`**:
   - Implemented `ProviderProfile` dataclass and `DEFAULT_PROFILES` (`nvidia_parakeet`, `nvidia_whisper`, `nemotron_http_template`).
   - Implemented `WordInfo` and `build_segments_from_words()` for converting word timestamps into sentence-like segments with silence-pause (0.8s), punctuation, and max duration rules.
   - Implemented `find_silence_split_points()` for silence-aware chunking (scanning 16kHz PCM audio for pauses $\ge 0.5$s near 120s boundaries, with 1.0s overlap fallback).
   - Implemented `extract_audio_chunk()` and `cut_audio_for_resume()` using `ffmpeg` argument lists.
   - Implemented `stitch_segments()`: offsets local segments by cut start, drops duplicates occurring before `resume_offset`, clamps start boundary, and joins with saved cloud segments.
   - Implemented `transcribe_audio_chunk_riva()`: gRPC client caller with phrase boosting (`speech_contexts`), retries on transient errors with backoff, and typed exceptions (`CloudAuthError`, `CloudCreditExhaustedError`, `CloudRetryableError`).
   - Implemented `test_riva_connection()` and `fetch_nvidia_speech_models()`.
   - Implemented secure API key storage (`get_nvidia_api_key()`, `save_nvidia_api_key()`).

2. **`scribe_core/clip_state.py`**:
   - Added `save_checkpoint()`, `get_checkpoint()`, `clear_checkpoint()`.
   - Updated `write_clip()` to accept `engine`, `provider`, and `model_id`, and automatically clear mid-clip checkpoints upon successful completion.
   - Updated `settings_differ()` to treat engine differences as settings differences (so changing between Local and Cloud flags clip appropriately unless forced).

3. **`scribe_core/transcription_manager.py`**:
   - Added `engine`, `cloud_profile`, `single_request_limit_sec`, `chunk_duration_sec`, and `checkpoint_every_clip` parameters to `prepare_job_config()`.
   - Added `env` injection parameter to `run_worker_blocking()` so the API key is passed securely in memory to the subprocess environment.

4. **`scribe_core/transcribe_worker.py`**:
   - Wired cloud execution and hybrid chunking strategy.
   - Implemented lazy loading for local Whisper model (`get_local_whisper_model()` loaded on demand).
   - Added checkpoint checking with audio file `mtime` and `size` validation.
   - Implemented error interception: on credit exhaustion or auth error, logs notice, switches `current_engine = "local"` for remainder of run, cuts audio at `resume_offset - 0.5`, transcribes remainder with local Whisper, and stitches segments seamlessly.
   - Maintained atomic write (`.tmp` $\to$ `.txt`) only upon full completion.
   - Emits `clip_complete` with `engine` and `fallback` notification events.

5. **`gui/stage3_tab.py`**:
   - Added "Transcription Engine & Provider Settings" frame:
     - Radiobuttons for Local (Whisper Medium) vs Cloud (NVIDIA Hosted Riva).
     - Checkbox for "Checkpoint every clip (~120s pieces)".
     - Cloud session uploaded minutes tracker (`Cloud uploaded this session: X.X mins (Est. cost: $0.00)`).
     - Cloud Profile combobox (`nvidia_parakeet`, `nvidia_whisper`).
     - Masked API key entry with Show/Hide toggle and "Save Key" button.
     - "Test Connection" button calling `test_riva_connection()`.
     - "Check NVIDIA Models" button calling `fetch_nvidia_speech_models()` and displaying dialog.
   - Added "Engine" column (index 8) to `clip_tree` displaying `Local`, `Cloud: Parakeet`, `Cloud: Whisper`, `Fallback (Local)`, or `—`.
   - Added privacy confirmation modal dialog before first cloud run in a session.
   - Added local Whisper fallback preflight verification prior to starting a cloud run.

---

## 3. Files touched

- `AGENTS.md` (Assumptions section added)
- `constraints.txt` (Pinned `nvidia-riva-client==2.27.0` and `protobuf==6.33.5`)
- `requirements-lock.txt` (Updated lock file)
- `scribe_core/cloud_engine.py` (Created)
- `scribe_core/clip_state.py` (Modified)
- `scribe_core/transcription_manager.py` (Modified)
- `scribe_core/transcribe_worker.py` (Modified)
- `gui/stage3_tab.py` (Modified)
- `tests/test_cloud_engine.py` (Created)
- `scripts/secret_scan.py` (Created)
- `records/2026-10-30/build_nvidia-cloud-asr-probe.md` (Created Step 0 & 0b probe record)
- `records/2026-10-30/feature_cloud-engine-fallback.md` (This document)
- `CHANGELOG.md` (Updated)

---

## 4. How it was verified

1. **Unit & Acceptance Test Suite**:
   Ran `pytest tests/test_cloud_engine.py`:
   - 12 passed in 0.55s.
   - Validated provider profiles, word segment builder, silence-aware chunking, audio cutting, seam-stitching without duplicates, checkpoint roundtrip/clear, permission-denied immediate fallback, quota/credit exhaustion immediate fallback, retry with backoff on transient errors, and secret safety in job configs.

2. **Full Regression Suite**:
   Ran `pytest tests/test_stage2_stage3_fixes.py`:
   - 23 passed in 12.49s.
   - Validated that existing test gate, approval buttons, in-app viewer/editor, and combined transcripts remain 100% operational with the new Engine column and cloud engine integration.
   Ran `pytest tests/test_scrolling_and_shortcuts.py`:
   - 36 passed in 3.88s.

3. **Secret Scan Verification**:
   Executed `python scripts/secret_scan.py`:
   - Scanned all repository files.
   - `[PASS] Secret scan clean! 0 occurrences of secret found across all repository files.`

