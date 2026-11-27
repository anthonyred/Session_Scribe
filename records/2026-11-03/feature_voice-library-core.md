# Feature Record: People Model & Voice Library Core

- **Date**: 2026-11-03
- **What was asked**:
  - Implement a persistent, local-only people model and voice library (`D:\Session_Scribe_Data\voice_library\`, configurable, default pending confirmation).
  - Storage must be strictly outside the git repository and outside `D:\Events`, refusing any path inside either boundary. Nothing in it is ever uploaded or committed.
  - Structure: `library.json` index file, `people\<person_id>\<sample_id>.wav` (16 kHz mono), and embeddings cache under `cache\<model_version>\<sample_id>.npy`.
  - All writes to index and cache must be atomic.
  - Event rosters reference person IDs; roles are per session (`Host`, `Speaker`, `Panelist`, `Moderator`, `Guest`). One person can have different roles in different sessions of the same event.
  - Transcript line format: `[Name]` in One speaker sessions; `[Name (Role)]` in Multiple speakers sessions.
  - Audio extraction via `ffmpeg` (explicit argument list), trimming, and speech presence check.
  - Detailed verification report:
    * Quality rating: speech duration (minimum 10s, recommended 20-30s), noise floor (dB), clipping ratio, spectral flatness. Rates `Good`, `Fair`, or `Poor`.
    * Single-voice check: sub-window embedding similarities to detect mixed speakers.
    * Reference match: similarity to confirmed library samples (`Strong`, `Possible`, `No match`).
    * Cross-person check: similarity against all other roster members, warning when closer to someone else.
    * Context: transcript text snippet if available.
  - Default status is `Unconfirmed`; requires explicit user confirmation.
  - CRUD operations: add person, add sample (max 5 samples per person limit enforced), list, get path for playback, delete sample, delete person (with cascade artifact cleanup), and search by name/alias (case-insensitive and fuzzy).
  - Model: `pyannote/wespeaker-voxceleb-resnet34-LM` (ResNet-34, 256-dim embeddings, ~25 MB VRAM), local processing only, lazy loading, and CPU fallback.
- **Why**:
  - Provides a centralized, reusable voice library to power multi-speaker diarization and speaker identification without coupling biometric voice samples to event folders or external clouds.
- **What was built (user-visible behaviour)**:
  - Core engine in `scribe_core/voice_library.py`:
    * Safe directory structure and path validator `validate_library_path`.
    * Complete Person and VoiceSample lifecycle with atomic index and cache writes.
    * Multi-speaker transcript line formatter `format_transcript_line`.
    * Automated audio analysis producing `VerificationReport`.
    * Maximum 5 samples per person limit strictly enforced.
- **How to use it**:
  - In code: instantiate `VoiceLibrary(library_dir=...)`.
  - Add people: `person = lib.add_person("Name", aliases=["Alias"])`.
  - Add samples: `sample, report = lib.add_sample(person.id, audio_path, range_s=(start, end))`.
  - Review report and confirm: `lib.confirm_sample(sample.id)`.
  - Format transcript lines: `format_transcript_line(speaker_name, role, is_multi_speaker, start_ts, end_ts, text)`.
- **Settings and defaults**:
  - Default library directory: `D:\Session_Scribe_Data\voice_library`
  - Max samples per person: `5`
  - Minimum speech duration: `10.0s`
  - Recommended speech duration: `20.0s` to `30.0s`
  - Embedding model: `pyannote/wespeaker-voxceleb-resnet34-LM` (~25 MB VRAM, 256-dim)
  - Match thresholds: `Strong` ($\ge 0.75$), `Possible` ($0.50 \le s < 0.75$), `No match` ($< 0.50$).
- **Files changed**:
  - `scribe_core/voice_library.py`: Core library implementation.
  - `tests/test_voice_library.py`: Unit test suite (14 tests).
  - `AGENTS.md`: Updated project assumptions.
- **Limitations and known issues**:
  - Pure synthetic sine tones produce compressed cosine similarity ranges (0.70 to 0.85) in the CNN embedding space because they lack natural human vocal tract acoustics (formants, fricatives). Natural speech provides wider separation.
- **How it was verified**:
  - 14 tests in `tests/test_voice_library.py` covering path isolation, CRUD, constraints, audio quality metrics, single-voice check, reference match, and transcript line formatting.
  - All 108 tests passing across the full test suite.
  - Secret scan confirmed 0 secrets.
- **How to roll it back**:
  - Delete `scribe_core/voice_library.py` and `tests/test_voice_library.py`. Revert changes to `AGENTS.md`.

