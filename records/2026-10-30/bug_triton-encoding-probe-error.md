# Bug: Triton Audio Decoder Exception During Connection / Health Test

**Date:** 2026-10-30  
**Type:** bug  
**Record:** `records/2026-10-30/bug_triton-encoding-probe-error.md`

---

## 1. What was asked / requested

Perform Root Cause Analysis (RCA) and resolve the following runtime error thrown during the Stage 3 cloud engine connection/health test:
```text
<_InactiveRpcError of RPC that terminated with:
    status = StatusCode.INVALID_ARGUMENT
    details = "Received error from Triton: Audio decoder exception: Request config encoding not specified and could not detect encoding from audio content."
    debug_error_string = "INVALID_ARGUMENT:Received error from Triton: Audio decoder exception: Request config encoding not specified and could not detect encoding from audio content."
>
```

---

## 2. Root Cause Analysis (RCA)

1. **Unspecified Encoding in `RecognitionConfig`**:
   In `scribe_core/cloud_engine.py`'s `test_riva_connection()`, the `riva.client.RecognitionConfig` was constructed without setting the `encoding` parameter:
   ```python
   cfg = rcli.RecognitionConfig(language_code=profile.language, max_alternatives=1)
   ```
   When `encoding` is omitted, `riva.client.RecognitionConfig` defaults to `AudioEncoding.ENCODING_UNSPECIFIED` (enum value `0`).

2. **Empty Data Chunk in In-Memory Test Header**:
   The probe audio passed to `client.offline_recognize(audio, cfg)` was a hardcoded 44-byte string:
   ```python
   audio = b"RIFF\x24\x00\x00\x00WAVEfmt \x10\x00\x00\x00\x01\x00\x01\x00\x80>\x00\x00\x00}\x00\x00\x02\x00\x10\x00data\x00\x00\x00\x00"
   ```
   Notice `data\x00\x00\x00\x00`: the subchunk size was 0 bytes, meaning there were zero audio frames in the payload.

3. **Triton Failure Sequence**:
   When Triton received the request on `grpc.nvcf.nvidia.com:443`:
   - It saw `Request config encoding not specified` (`ENCODING_UNSPECIFIED`).
   - It attempted auto-detect on the audio payload via its internal audio decoder.
   - Because the data chunk had 0 sample bytes, the audio decoder could not extract or detect encoding from the audio content.
   - Triton aborted the RPC with `StatusCode.INVALID_ARGUMENT`.

---

## 3. What was changed

1. **`scribe_core/cloud_engine.py`**:
   - Implemented `make_silence_wav(duration_sec=0.5, framerate=16000)` using `io.BytesIO` and Python's `wave` module to generate a fully compliant 16kHz mono WAV file with 8,000 samples (16,000 bytes) of PCM silence audio.
   - Updated `test_riva_connection()` to explicitly provide:
     ```python
     encoding=rcli.AudioEncoding.LINEAR_PCM,
     sample_rate_hertz=16000,
     audio_channel_count=1,
     ```
     and pass the valid 0.5s silence WAV payload.
   - Updated `transcribe_audio_chunk_riva()` to also explicitly specify `encoding=riva_client.AudioEncoding.LINEAR_PCM`, `sample_rate_hertz=16000`, and `audio_channel_count=1` in `RecognitionConfig` to prevent decoder ambiguity on any silence chunk.

---

## 4. Files touched

- `scribe_core/cloud_engine.py`
- `records/2026-10-30/bug_triton-encoding-probe-error.md` (This document)
- `CHANGELOG.md` (Updated)

---

## 5. How it was verified

1. **Direct Endpoint Probe**:
   Executed standalone reproduction probe against NVIDIA Riva endpoints with live API key (`grpc.nvcf.nvidia.com:443`):
   - **Before Fix**:
     - `test_riva_connection(key, parakeet)`: Failed with `StatusCode.INVALID_ARGUMENT: Received error from Triton: Audio decoder exception: Request config encoding not specified and could not detect encoding from audio content.`
     - `test_riva_connection(key, whisper)`: Failed with identical Triton error.
   - **After Fix**:
     - `test_riva_connection(key, parakeet)`: Returned `True` (`Successfully connected to NVIDIA Parakeet TDT 0.6B v2 (English) via gRPC.`)
     - `test_riva_connection(key, whisper)`: Returned `True` (`Successfully connected to NVIDIA Whisper Large v3 (Multilingual) via gRPC.`)

2. **Automated Unit & Regression Tests**:
   - `pytest tests/test_cloud_engine.py`: 12 passed.
   - `pytest tests/test_stage2_stage3_fixes.py`: 23 passed.
   - `python scripts/secret_scan.py`: 0 secret occurrences found across all files.

