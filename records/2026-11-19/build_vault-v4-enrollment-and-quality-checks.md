# Build Record: Vault v4 Voice Profile Enrollment Engine & Acoustic Quality Checks

- **Date:** 2026-11-19
- **Type:** `build`
- **What was asked:**
  Implement Phase V4: Voice Profile Enrollment Engine into Vault v2 (with Phase V3 amendments applied first):
  1. **Phase V3 Amendments:**
     - TOTP policy adjustment: Daily unlock requires passphrase ONLY (plus Windows login/DPAPI binding); TOTP prompt removed from daily unlock.
     - TOTP strictly required for destructive actions: delete, replace, modify, bulk purge, passphrase change, and TOTP reset.
     - TOTP reset procedure with 24-hour mandatory security delay.
     - In setup enrollment, offer to display the TOTP setup key once so user can store it offline (QR code never written to disk).
     - Audit head mirror written outside the vault directory (`%LOCALAPPDATA%\SessionScribe\audit_mirror\audit_head.mirror`).
     - Honest documentation in threat model and README: TOTP is an access-control layer (not an offline cryptographic factor); crypto-shredding is final only if no external backup/shadow copy exists; future FIDO2 `hmac-secret` option noted.
     - Passphrase check: default to generated 6 or 7 words; manual entry bar requires $\ge 6$ words or length $\ge 20$, and $\ge 10^{20}$ guesses; blacklist famous weak passphrases (`correct horse battery staple`).
     - Quarantined profile exclusion: quarantined profiles strictly excluded from acoustic matching and name resolution.
     - UI stability tests and screenshot captures for all dialogs (build once, stable widget counts, light/dark, 100%/150%).
  2. **Phase V4 Implementation:**
     - In-memory audio extraction from media files (audio or video) with optional `(start_sec, end_sec)` time range via FFmpeg pipes (16 kHz mono WAV, zero plaintext on disk).
     - Comprehensive acoustic quality checks before acceptance:
       * Speech-only duration: minimum 10.0s enforced (30-60s recommended).
       * Noise level / SNR: minimum 12.0 dB SNR (rejects muted/silent or loud background noise).
       * Background music detection: sustained tonal band stationarity analysis ($\ge 1.2$s) rejecting musical accompaniment or beds.
       * Single speaker & overlap rejection: harmonic sieve analysis rejecting polyphonic/overlapping talkers.
       * Audio distortion & clipping rejection ($> 1.0\%$ clipped).
     - Target per person: 3 to 5 reference samples (~2-3 minutes total) with user-approved staging.
     - Dual-zone storage commitment:
       * Zone 1 (Identity): Full name, session roles, auditable consent record (`consent_noted=True`, `consent_date_utc`), and notes.
       * Zone 2 (Biometric): Combined centroid profile embedding, sample embeddings, encrypted reference WAV samples.
       * Keyed-MAC Audit Log entry (`PROFILE_ENROLLED`) with anonymous `spk_` ID only.
     - Hardware & VRAM isolation: Local embedding model runs strictly isolated from Whisper in GPU VRAM (`_VRAM_EXECUTION_LOCK`, `release_vram()`, and CUDA cache clearance); Hugging Face offline mode once cached.
     - UI Dialog: `VoiceProfileEnrollmentDialog` built once with Design System v2 tokens, identity & consent checkboxes, sample import & quality cards, sample approval list, and commitment action.
- **Why:**
  Biometric enrollment requires clean, single-speaker reference audio without background music, overlap, or excessive noise so that downstream speaker identification is accurate and reliable. Zero plaintext audio or personal identities must ever touch persistent storage unencrypted.
- **What was built (user-visible behaviour):**
  - `VoiceProfileEnrollmentDialog`: A complete enrollment interface allowing users to import reference clips, view acoustic quality metrics (duration, SNR, music, overlap), approve/reject individual samples, note consent, and commit into Vault v2.
  - Transparent diagnostic feedback: if a sample fails a quality check, an actionable reason is displayed (e.g. "Speech length too short (6.2s)", "Background music detected", "Excessive background noise (SNR 7.5 dB)", "Multiple speakers or overlapping speech detected").
  - Unified Vault Chip updates reflecting new profile counts and state.
- **How to use it:**
  - Launch from Vault Manager or Stage 2 Voice Profile actions: "Enroll New Voice Profile".
  - Enter full name, optional affiliation and roles. Check "Consent noted from speaker".
  - Click "Import Audio / Video Sample" to select reference files. Review quality check cards.
  - Approve at least 3 verified samples (3-5 recommended).
  - Click "Commit Profile to Vault".
- **Settings and defaults:**
  - `MIN_SPEECH_DURATION_SECONDS`: 10.0s (recommended 30.0s – 60.0s).
  - `MIN_SNR_DB`: 12.0 dB.
  - `MIN_ENROLLMENT_SAMPLES`: 3 samples (maximum 5).
  - `FRAME_DURATION_SEC`: 0.03s (30 ms).
  - `SPEECH_ENERGY_THRESHOLD`: 0.015 RMS.
  - Default Passphrase Generation: 6 words (optional 7 words) from EFF-derived wordlist.
  - TOTP Security Delay: 24.0 hours for reset.
- **Files changed:**
  - `scribe_core/vault_crypto.py` (blacklists, manual threshold, 6/7 words).
  - `scribe_core/wordlist.py` (wordlist generator).
  - `scribe_core/voice_vault.py` (daily unlock without TOTP, TOTP reset state machine).
  - `scribe_core/audit_log.py` (audit head mirror outside vault dir).
  - `scribe_core/vault_v2.py` (quarantine exclusions for matching & resolution, notes support).
  - `scribe_core/audio_extractor.py` (in-memory FFmpeg extraction).
  - `scribe_core/voice_quality_checker.py` (acoustic duration, SNR, music stationarity, harmonic sieve overlap).
  - `scribe_core/embedding_engine.py` (hardware-isolated embedding engine with VRAM lock).
  - `scribe_core/enrollment_manager.py` (multi-sample enrollment manager, centroid calculation, dual-zone commitment).
  - `gui/vault_dialog.py` (unlock dialog passphrase-only, setup offline key button).
  - `gui/enrollment_dialog.py` (VoiceProfileEnrollmentDialog UI).
  - `docs/security/threat_model.md` & `docs/security/vault_format.md` & `README.md` (honest security docs).
  - `scripts/capture_vault_dialog_screenshots.py` (automated light/dark UI capture).
  - `tests/test_vault_crypto.py` (14 unit tests).
  - `tests/test_vault_v3_quarantine.py` (15 unit tests).
  - `tests/test_vault_dialog_stability.py` (5 UI stability tests).
  - `tests/test_vault_v4_enrollment.py` (8 enrollment & quality tests).
- **Limitations and known issues:**
  - Audio extraction requires `ffmpeg` available on system PATH.
  - Local Pyannote embedding model requires initial download and cache before offline mode functions.
  - PySide6 UI tests remain skipped pending PySide6 installation in the virtual environment.
- **How it was verified:**
  - Executed `test_vault_v4_enrollment.py`: 8/8 tests passed in 15.75s (synthetic audio only).
  - Executed full repository test suite: 242 tests passed in 125.95s (8 skipped).
  - Executed `secret_scan.py`: 0 secret occurrences found across entire codebase.
  - Captured 12 UI stability screenshots across light and dark modes at `scratch/ui_stability/`.
- **How to roll it back:**
  - Revert `scribe_core/audio_extractor.py`, `scribe_core/voice_quality_checker.py`, `scribe_core/embedding_engine.py`, `scribe_core/enrollment_manager.py`, `gui/enrollment_dialog.py`, and `tests/test_vault_v4_enrollment.py`.
