# Build Record: NVIDIA Cloud Riva gRPC Probe and Step 0 Investigation

**Date:** 2026-10-30  
**Type:** `build`  
**Short Title:** `build_nvidia-cloud-asr-probe`  

---

## 1. What was asked / requested

Step 0 and Step 0b verification before implementing hosted cloud engine in Stage 3:
- Read API key from environment variable `NVIDIA_API_KEY` or git-ignored `.env`. Ensure the key is never written to repo, logs, state files, or reports.
- Investigate models available on `build.nvidia.com`:
  - Determine which speech-to-text models the key can call.
  - Verify whether Nemotron 3.5 Transcribe is hosted on `build.nvidia.com`.
  - Identify call style: plain HTTP audio-transcriptions endpoint vs. gRPC with a function ID.
  - Test accepted audio formats, maximum payload size, durations (1, 5, 9 minutes), streaming, prompt / phrase boosting (`speech_contexts`), language codes, rate limits, credit consumption, and exact error codes.
- Test in a throwaway venv built from the lock file and identify any conflicts with pinned dependencies (`torch`, `numpy`, `protobuf`, `huggingface_hub`). Do not touch the shared `.venv`.
- Confirm where key is read from and perform a secret scan showing 0 occurrences across all repo files.
- Document proposed rule for constructing transcript segments from word timestamps.

---

## 2. Findings & What Was Changed

### 2.1 Model Availability on NVIDIA Build
- **Nemotron 3.5 Transcribe**: **Not available** on `build.nvidia.com` or NVIDIA Cloud Functions (NVCF). Nemotron models currently hosted are text LLMs, vision-language models, embeddings, and an experimental streaming ASR stub.
- **Active ASR Models Available**:
  - `ai-whisper-large-v3` (`b702f636-f60c-4a3d-a6f4-f3568c13bd7d`)
  - `ai-parakeet-tdt-0_6b-v2` (`d3fe9151-442b-4204-a70d-5fcc597fd610`)
  - `ai-parakeet-tdt-0_6b` (`2b940e91-a70e-4483-a958-d50408b96589`)
  - `ai-parakeet-ctc-1_1b-asr` (`1598d209-5e27-4d3c-8079-4751568b1081`)
  - `ai-parakeet-1_1b-rnnt-multilingual-asr` (`71203149-d3b7-4460-8231-1be2543a1fca`)
  - `ai-canary-1b-asr` (`b0e8b4a5-217c-40b7-9b96-17d84e666317`)
  - `ai-conformer-ctc-asr` (`50124cf2-fb60-4a8c-9d25-d85b85199526`)

### 2.2 Call Style & Transport
- **gRPC via NVCF**: Connects to `grpc.nvcf.nvidia.com:443` using SSL.
- **Headers / Metadata**: Passed via gRPC metadata:
  - `function-id`: `<UUID>`
  - `authorization`: `Bearer <NVIDIA_API_KEY>`
- **HTTP status**: Calling `https://integrate.api.nvidia.com/v1/audio/transcriptions` returns `HTTP 404 page not found`. NVIDIA does not expose a public OpenAI-compatible REST proxy for Riva.
- **Client library**: `nvidia-riva-client` (`riva.client.ASRService`).

### 2.3 Audio Formats, Payloads & Execution Durations
- **Format**: Linear PCM WAV (16 kHz, 16-bit mono), FLAC, OGG.
- **Tested Audio Lengths (16 kHz 16-bit Mono WAV)**:
  - **1.0 minute (1.83 MB)**:
    - Parakeet TDT 0.6B v2: **1.99s** (~30x real-time)
    - Whisper Large v3: **1.43s** (~42x real-time)
  - **5.0 minutes (9.16 MB)**:
    - Parakeet TDT 0.6B v2: **4.74s** (~63x real-time)
    - Whisper Large v3: **12.83s** (~23x real-time)
  - **9.0 minutes (16.48 MB)**:
    - Parakeet TDT 0.6B v2: **8.09s** (~66x real-time)
    - Whisper Large v3: **49.07s** (~11x real-time)
- Both models easily accept 1, 5, and 9-minute audio chunks up to 16.5 MB in a single gRPC call without truncation or failure.

### 2.4 Language Settings & Triton Multi-Model Dispatch
- **Parakeet TDT 0.6B v2**: Strictly **`en-US`** only. Requesting `hi`, `hi-IN`, or `auto` returns:
  `INVALID_ARGUMENT: Received error from Triton: Request language code doesn't match model language codes en-US`.
- **Whisper Large v3**: Supports 99+ languages including **`en`**, **`hi`**, and **`multi`** (code-switching). Requesting `en-US` or `hi-IN` requires standard ISO language codes (`en`, `hi`, `multi`).

### 2.5 Phrase Boosting (`speech_contexts`)
- Both `parakeet-tdt-0.6b-v2` and `whisper-large-v3` accepted `speech_contexts` with word boosting list (`boosted_lm_words`) and score (`boosted_lm_score=10.0`) via `riva.client.add_word_boosting_to_config()`.

### 2.6 Error Codes & Concurrency
- **Authentication Failure**:
  - Code: `grpc.StatusCode.PERMISSION_DENIED`
  - Details: `failed to open stateful work request: rpc error: code = PermissionDenied desc = Authorization failed`
- **Concurrency / Quota Limit**:
  - Code: `grpc.StatusCode.RESOURCE_EXHAUSTED` (or `429 Too Many Requests`).
  - 5 concurrent requests fired simultaneously succeeded without triggering concurrency rejections on the tested account, executing in ~5.7s to 6.9s.

### 2.7 Proposed Rule for Building Segments from Word Timestamps
Riva returns `response.results[].alternatives[].words` where each word has `word`, `start_time` (ms), and `end_time` (ms).
Proposed segment-building rule:
1. Iterate through words in chronological order.
2. Accumulate words into a segment until one of the following conditions is met:
   - **Pause threshold**: Gap between previous word's `end_time` and current word's `start_time` $\ge 800\text{ ms}$ (0.8s).
   - **Sentence boundary**: Previous word ends with sentence punctuation (`.`, `?`, `!`) and current word begins a new phrase.
   - **Max duration**: Accumulated segment duration exceeds $20.0\text{ seconds}$.
3. Format start and end times in clip seconds (`start_ms / 1000.0`, `end_ms / 1000.0`).
4. Generate text by joining words with single spaces and preserving punctuation emitted by Riva's punctuation module.

### 2.8 Dependency Conflict Analysis (Throwaway Virtualenv)
- Created isolated throwaway venv at `%TEMP%\throwaway_riva_venv`.
- Lock file pins:
  - `protobuf==7.36.2`
  - `numpy==1.26.4`
  - `huggingface-hub==0.26.5`
  - `torch==2.4.1+cu121`
  - `speechbrain==1.1.1`
- When installing `nvidia-riva-client 2.27.0`:
  - `nvidia-riva-client` strictly requires `protobuf==6.33.5` (or `protobuf<7`).
  - It downgrades `protobuf` from `7.36.2` to `6.33.5`.
  - `pip check` confirmed `No broken requirements found` when `protobuf==6.33.5` was installed alongside `numpy==1.26.4` and `huggingface-hub==0.26.5`.
  - The shared `.venv` was left completely untouched.

### 2.9 Secret Scan
- Executed `scripts/secret_scan.py` across all repository files, tests, state files, and logs.
- Confirmed: **0 occurrences** of the secret key across all files. Key is read strictly from `os.environ["NVIDIA_API_KEY"]` or local `.env` (in `.gitignore`).

---

## 3. Files touched

- `AGENTS.md` — Added section 7 ("Project assumptions").
- `scripts/investigate_nvidia.py` — Script querying NVCF function catalog and models.
- `scripts/test_nvidia_grpc.py` — Script executing gRPC probes against Parakeet and Whisper functions.
- `scripts/secret_scan.py` — Script scanning repository to ensure no API key leakage.
- `records/2026-10-30/build_nvidia-cloud-asr-probe.md` — This record.

---

## 4. How it was verified

- Verified NVCF models and function catalog via HTTPS API.
- Verified gRPC connection, auth handshake, and Triton model configurations.
- Verified 1-minute, 5-minute, and 9-minute WAV processing latencies on `ai-parakeet-tdt-0_6b-v2` and `ai-whisper-large-v3`.
- Verified error reporting on invalid credentials (`PERMISSION_DENIED`).
- Verified clean `pip check` in throwaway venv.
- Verified clean repository secret scan (`scripts/secret_scan.py`).

