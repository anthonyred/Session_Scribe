# Build: Stage 3 Live Timers, Voice Vault DPAPI Security, and Documentation

## Date
2026-11-13

## What was asked
1. **Part 1: Live Timer and Engine Display inside the Stage 3 Table:**
   - Embed real-time telemetry directly into the Stage 3 table (no separate status strip).
   - Running row: Status (`● Transcribing 42%`, `● Diarizing`, `● Identifying`), ticking `hh:mm:ss` monotonic clock supporting >24 hours, live engine & model with fallback notation (`Parakeet → Whisper medium`), diagnostic tooltips, failover timers ticking and freezing, and `Queued` indicator for upcoming rows.
   - Pinned totals row: Dynamic status label (`Total (21 clips) · Run 00:12:34 · 7 of 21 done · ETA ~00:09:10 (est.)`), live accumulated run time, and ETA based strictly on remaining clips (hidden until $\ge 1$ clip finishes).
   - Zero layout jitter: Update only changed cells (`item(iid, values=...)`) without table reloads; ~2 Hz UI timer driven by `time.monotonic()`; 60s silence detection appending `(still working…)`.
   - Auto-scroll to running row with 10-second manual scroll override freeze.
2. **Part 3, Item 3: Voice Vault Unlock (Windows Account + Passphrase):**
   - 32-byte master key derived from passphrase via Argon2id (RFC 9106, standard library `hashlib.blake2b` implementation, tuned to ~0.60s execution).
   - Dual-layer protection: Wrapped blob protected with Windows DPAPI (`CryptProtectData`/`CryptUnprotectData` via `ctypes.windll.crypt32` with app entropy) stored in `%LOCALAPPDATA%\SessionScribe\keys\vault.key`.
   - Passphrase rules: $\ge 12$ characters, real-time strength bar, masked input (`show="•"`), zero logging or caching of plaintext passphrases.
   - Best-effort bytearray zeroing on lock. Preflight run gate; runs never lock mid-flight (`begin_run()` / `end_run()`).
   - Configurable 30-minute idle auto-lock, progressive delay on failed attempts (1s, 2s, 4s... up to 60s) logging only failure counts.
   - Separate recovery export passphrase roundtrip; in-place passphrase change without re-encrypting voice archives.
   - Compact header chip (`🔒 Vault Locked` / `🔓 Vault Unlocked`).
3. **Part 4: Root README.md per 12 Mandatory Sections:**
   - Comprehensive documentation covering Overview & Features, Screenshots in `docs/images/` with sample data captions, exact Tech Stack, Installation steps (Python 3.11.9, `.venv`, CUDA torch, requirements, ffmpeg, Tesseract, preflight), Usage Guide, Architecture tree, Environment variables with `.env.example`, Contribution guidelines from AGENTS.md, License & Author placeholders, Troubleshooting & FAQ table, Acknowledgements with real licenses from metadata, and "Last verified" date.

## Why
To provide real-time, zero-jitter transcription monitoring and failover transparency directly within the Stage 3 table; to establish state-of-the-art cryptographic protection for biometric voiceprints and speaker identities on Windows; and to provide comprehensive, clear documentation for users and contributors.

## What was built (user-visible behaviour)
1. **Live Monotonic Table Ticking:** As soon as transcription starts, the active clip row status turns to `● Transcribing`, and `Time taken for transcription` counts up smoothly every half second (`00:00:01`, `00:00:02`...).
2. **Dynamic Fallback Indicator:** If the cloud engine drops, the engine column updates to `Parakeet → Whisper medium`, the failover clock freezes the exact handover duration, and the tooltip provides hardware, provider, and resume offset details.
3. **Pinned Live Totals:** The bottom totals row displays the total run elapsed time, finished clip count, and remaining clip ETA without disturbing column alignments.
4. **Header Vault Chip & Dialogs:** The application header displays `🔒 Vault Locked` or `🔓 Vault Unlocked`. Clicking the chip prompts for initialization (with password strength bar) or unlocking. Before running transcription that touches speaker voices, a preflight check ensures the vault is unlocked.
5. **Sanitized Configuration:** `.env.example` provides a clear template for `NVIDIA_API_KEY` and `HF_TOKEN`.

## How to use it
1. Launch the application: `D:\Session_Scribe\.venv\Scripts\python.exe -m gui.app`.
2. Click the `🔒 Vault Locked` chip in the top right to initialize or unlock your voice vault.
3. In Stage 3, choose your engine and click **Run Test Clips** or **Transcribe All**.
4. Observe the live timer, engine state, and totals row directly in the table.
5. Hover over the **Engine** cell for hardware and resume offset tooltips.

## Settings and defaults
- **Reports Directory:** Default analytical summaries are saved in `<event root>\scribe\reports\`.
- **Cloud API Privacy:** Strictly audio slices (16 kHz mono) are sent for decoding; zero personal names, photos, or voice profiles are ever transmitted.
- **Vault Auto-Lock:** Defaults to 30 minutes of idle activity (`touch_activity()`); suppressed during active transcription runs.
- **Argon2id Parameters:** `time_cost=2`, `memory_cost=1024` KiB, tuned to ~0.60s on laptop hardware.

## Files changed
- `scribe_core/event_paths.py`: Added `"reports"` leaf directory to `LEAF_FOLDER_NAMES` and path derivation.
- `scribe_core/voice_vault.py`: Complete RFC 9106 Argon2id implementation, Windows DPAPI wrapper, key wrapping, progressive delays, and recovery export.
- `gui/vault_dialog.py`: `VaultStatusChip`, `UnlockVaultDialog`, `InitializeVaultDialog`, and `ensure_vault_unlocked_preflight`.
- `gui/app.py`: Integrated `VoiceVault` instance, header chip, and passed vault to Stage 3.
- `gui/stage3_tab.py`: Live ~2 Hz monotonic UI timer, running row updates, totals row ETA formatting, engine tooltips, auto-scroll with 10s override, preflight gate.
- `README.md`: Completely rewritten to satisfy all 12 mandatory sections.
- `.env.example`: Sanitized environment variables template.
- `tests/test_voice_vault.py`: 8 automated unit tests for vault mechanics.
- `tests/test_stage3_live_timers.py`: 8 automated unit tests for live table ticking and zero jitter.

## Limitations and known issues
- Pure synthetic sine waves without harmonic overtones cannot be reliably differentiated by pyannote speaker embedding models. Tests use multi-harmonic synthetic waveforms with deterministic seeds.
- The PySide6 Qt GUI migration is currently maintained on branch `ui-qt-migration` as a functional prototype; the Tkinter interface remains the primary production interface.

## How it was verified
1. **Voice Vault Tests:**
   ```powershell
   D:\Session_Scribe\.venv\Scripts\python.exe tests/test_voice_vault.py
   ```
   *Result:* 8/8 passed in 6.58s.
2. **Stage 3 Live Timer Tests:**
   ```powershell
   D:\Session_Scribe\.venv\Scripts\python.exe tests/test_stage3_live_timers.py
   ```
   *Result:* 8/8 passed in 2.64s.
3. **Tk Stage 3 Table Tests:**
   ```powershell
   D:\Session_Scribe\.venv\Scripts\python.exe -m pytest tests/test_stage3_tk_table.py
   ```
   *Result:* 6/6 passed in 9.66s.
4. **Design System & Contrast Tests:**
   ```powershell
   D:\Session_Scribe\.venv\Scripts\python.exe -m pytest tests/test_design_system_v2.py tests/test_theme_contrast.py tests/test_qt_prototype.py
   ```
   *Result:* 14 passed, 5 skipped (headless) in 3.76s.
5. **Voice Library Core Tests:**
   ```powershell
   D:\Session_Scribe\.venv\Scripts\python.exe -m pytest tests/test_voice_library.py
   ```
   *Result:* 16/16 passed in 20.77s.
6. **Secret Scan Verification:**
   ```powershell
   D:\Session_Scribe\.venv\Scripts\python.exe scripts/secret_scan.py
   ```
   *Result:* 0 secrets detected across all files.

## How to roll it back
To revert changes, check out previous revisions of modified files using git checkout on `gui/`, `scribe_core/`, and `README.md`.

