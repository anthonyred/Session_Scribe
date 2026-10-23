# Stage 3: Transcription Build Notes

**Date:** 2026-10-23  
**Workspace:** `D:\Session_Scribe\`  
**Target Event:** `D:\Events\17.ElevateUai_cybersecurity\`  
**Architecture:** Multi-process GUI & Transcription Worker  

---

## 1. Overview & Architectural Improvements

In earlier per-event scripts (e.g. `transcribe_elevate_full.py`), transcription suffered from several known bugs and inefficiencies:
1. **Per-clip Model Reloading:** Whisper was reloaded for every individual clip, wasting 5-10s per clip and fragmenting GPU memory.
2. **Unnecessary Diarization in Single-Speaker Mode:** Pyannote was loaded even when each clip had only one speaker, consuming extra VRAM and requiring a Hugging Face token.
3. **Hardcoded Lists & Token in Code:** HF token and video lists were hardcoded directly in script files.
4. **No Resume Gate or Progress Stream:** If interrupted, transcripts were often re-run from scratch, and GUI windows would freeze during model execution.

### Stage 3 Solution:
- **Independent Subprocess Worker (`transcribe_worker.py`):** Runs outside the GUI thread. When the run finishes or is cancelled, process termination guarantees 100% GPU VRAM release back to idle.
- **Model Loaded Once:** Whisper medium is loaded once on `cuda:0` at run start and reused across all clips.
- **Pure Single-Speaker Labeling:** No Pyannote, no HF token needed for single-speaker sessions. Speaker names are retrieved directly from `manifest.json`.
- **Dynamic ffmpeg Audio Extraction:** Extracts mono 16 kHz WAV into `scribe\work\` and deletes the temporary file in a `finally` block immediately after each clip finishes or errors.
- **Confidence Flagging:** Segments with `avg_logprob < -1.0`, `no_speech_prob > 0.6`, or `compression_ratio > 2.4` are flagged in `scribe\transcripts\low_confidence_report.txt` rather than polluting the clean transcript.
- **Test Gate Enforcement:** Full runs are disabled until 1-2 test clips have been run into `scribe\test\` and approved.
- **Atomic Resume Support:** Completed transcript files (>100 bytes) in the destination folder are automatically skipped on resume.

---

## 2. Transcript Layout Format

The output layout preserves the exact structure expected by the user:

```
=== EVENT DETAILS ===
Event Name: CYBER & BEYOND 2026, organised by elevateU.ai in collaboration with Quality Thought
Date: 5 September 2026
Organiser: elevateU.ai in collaboration with Quality Thought
Venue: N-Heights building 5th floor, Hyderabad

=== TRANSCRIPT ===
Video File: VID20260905151747.mp4
Start Time: 2026-09-05 15:17:47
Speaker: Speaker 3
Transcription Duration: 00:00:04
Transcription Started: 2026-10-23 20:12:12 IST
Transcription Completed: 2026-10-23 20:12:16 IST

[Speaker 3] [15:17:47.000 - 15:17:59.000] and then it closes by SOCing. We will get to know how SOC operation center works and what is the process followed there to investigate the alert.
```

---

## 3. Acceptance Check Results

All Stage 3 acceptance criteria were verified via `test_stage3_acceptance.py`:

| Check | Result | Details |
| :--- | :--- | :--- |
| **Test Gate Locking** | **PASSED** | `TranscriptionManager.is_test_approved()` initially returns `False`. The GUI disables the "Run All" button until approval is recorded in `test_approval.json`. |
| **Elevate Test Run (2 clips)** | **PASSED** | Transcribed `VID20260905114130.mp4` and `VID20260905114405.mp4` into `scribe\test\`. Layout, absolute timestamps (`11:41:30.000`), and speaker names (`Speaker 1`) matched previous format. Run finished in 7s (1.6x real-time speed). |
| **Temp Audio Cleanup** | **PASSED** | Verified zero `.wav` files remain in `scribe\work\`. |
| **Test Gate Approval** | **PASSED** | Approved test gate; `is_test_approved()` updated to `True` and unlocked full run. |
| **Resume Check** | **PASSED** | Pre-populated 2 existing transcripts into `scribe\transcripts\`. Full run detected both existing files and skipped them immediately without re-transcribing. |
| **Full Elevate Run (16 clips)** | **PASSED** | All 16 clips completed with valid transcripts in `scribe\transcripts\`. Total audio transcribed: 976.49s in 6m 32s (overall **2.5x real-time speed** on RTX 3050 Laptop GPU). |
| **Low-Confidence Report** | **PASSED** | Isolated 37 flagged segments across the 16 clips in `low_confidence_report.txt` (not in transcripts). |
| **VRAM Release to Idle** | **PASSED** | Worker process exited; GPU VRAM immediately returned to idle with **3305.7 MB free** (total 4095.5 MB). |
