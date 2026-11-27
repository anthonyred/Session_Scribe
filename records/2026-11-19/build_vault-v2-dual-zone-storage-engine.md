# Build Record — Vault v2 Dual-Zone Storage Engine (Phase V2b)

- **Date:** 2026-11-19
- **What was asked:**
  - Execute Phase V2b: Dual-Zone Storage Engine (`scribe_core/vault_v2.py`).
  - Answer user questions regarding:
    1. The 8 skipped tests in test suite (evidence and details).
    2. Windows session-lock auto-lock hook implementation.
    3. Startup behavior with legacy `vault.key` (offer setup, archive key safely, leave plaintext library untouched).
    4. Passphrase strength check measuring real entropy (rejecting repetitive strings like `"aaaa bbbb cccc dddd eeee"`, accepting high-entropy non-word passphrases).
    5. Capping run-active lock suppression at 4 hours max with a 2-minute post-run grace period.
    6. Persisting failed-unlock counter across process restarts.
  - Implement `scribe_core/vault_v2.py`:
    - Atomic CRUD for Identity records (`<spk_id>.id.enc`) and Biometric records (`<spk_id>.bio.enc`).
    - Encrypted reference WAV samples (`sample_*.wav.enc`).
    - Padded encrypted indexes (padded to 4096-byte buckets concealing counts and lengths).
    - Per-event session alias maps (`alias_map.dat`).
    - Record versioning (schema 2) and quarantine state fields.
    - Records addressed strictly by random 128-bit `spk_` IDs (names exist only inside encrypted identity records).
  - Add comprehensive unit tests covering CRUD, crash simulation during write, swapped/tampered files, plaintext canary scan (zero names/bytes on disk), file permissions, and least-privilege storage access using synthetic data only.
  - Do not create or touch the real vault.
- **Why:**
  - Physical and logical segregation of identity and biometric data prevents cross-zone compromise.
  - Padded indexes ensure adversary file snooping cannot deduce speaker counts or name lengths from disk block sizes.
  - Deep canary scanning guarantees zero unencrypted biometric samples or identities ever hit the storage layer.
- **What was built (user-visible behaviour):**
  - **Dual-Zone Storage Engine (`VaultV2StorageEngine`):**
    - Zone 1 (`zone_identity/`): Encrypted person identity records (`<spk_id>.id.enc`), consent metadata, affiliation, roles, and per-event alias maps (`alias_maps/<event_id>.alias.enc`).
    - Zone 2 (`zone_biometric/`): Encrypted acoustic embeddings (`<spk_id>.bio.enc`) and reference audio clips (`<spk_id>_samples/<sample>.wav.enc`).
  - **Zero Plaintext on Disk:** All identities, voiceprints, and audio clips exist strictly as AES-256-GCM ciphertext bound with record IDs in AAD.
  - **Real Shannon Entropy Passphrase Evaluator:** Rejects repetitive dummy words (`"aaaa bbbb cccc dddd eeee"`), repeated words, and keyboard sequences, while properly accepting long high-entropy non-word passphrases (`K8#v9!mQ2$zL5@xP7&wR`).
  - **Windows Workstation Lock Hook:** Background message-only window listener registers `WTSRegisterSessionNotification(NOTIFY_FOR_THIS_SESSION)` and instantly locks the vault upon receiving `WTS_SESSION_LOCK`.
  - **Run Suppression Hard Cap:** Active transcription runs suppress auto-lock for a hard maximum of 4 hours, transitioning to a 2-minute grace period upon run completion.
  - **Persistent Anti-Brute-Force Throttling:** Failed unlock attempts persist in `throttle.dat`, enforcing exponential backoff across process restarts.
  - **Safe Legacy Key Archiving:** Detects legacy `vault.key`, archives it to `vault.key.legacy_v1_backup` without silent deletion, and leaves `D:\Events\_voice_library\` untouched.
- **How to use it:**
  - Storage engine is invoked programmatically by transcription workflows and the late-binding resolver.
  - The UI operates exclusively through the `VoiceVault` controller.
- **Settings and defaults:**
  - `FORMAT_VERSION_V2 = 2`
  - `MAX_RUN_SUPPRESSION_SECONDS = 14400` (4 hours)
  - `RUN_END_GRACE_PERIOD_SECONDS = 120` (2 minutes)
  - Index padding: multiples of 4096 bytes with random padding.
- **Files changed:**
  - `scribe_core/vault_v2.py` (New: Dual-zone storage engine, atomic CRUD, padded index, canary scanner)
  - `scribe_core/vault_crypto.py` (Updated: real Shannon entropy evaluator rejecting `"aaaa bbbb cccc dddd eeee"`)
  - `scribe_core/voice_vault.py` (Updated: persistent throttle counter, Windows session lock hook, run cap)
  - `tests/test_vault_v2_storage.py` (New: 8 comprehensive storage layer unit tests)
  - `tests/test_vault_crypto.py` (Updated: added tests for entropy, repetition rejection, and persistent throttling)
- **Limitations and known issues:**
  - The Qt vault chip and dialog changes in `gui/qt/components.py` are untested because PySide6 is not installed in the environment (8 Qt unit tests skipped).
  - Full TOTP destructive workflow and quarantine scheduler deferred to Phase V3 as directed.
  - Flash wear leveling on SSD/NVMe means crypto-shredding relies on key eradication.
- **How it was verified:**
  - `tests/test_vault_v2_storage.py`: 8/8 tests passed in 0.57s (including crash simulation and plaintext canary scan).
  - `tests/test_vault_crypto.py`: 14/14 tests passed in 11.4s.
  - `tests/test_voice_vault.py`: 8/8 tests passed in 6.4s.
  - Full test suite: 204 tests passed, 0 failures, 8 skipped.
  - `scripts/secret_scan.py`: 0 secrets found.
- **How to roll it back:**
  - Revert `scribe_core/vault_v2.py`, `tests/test_vault_v2_storage.py`, and restore previous versions of `vault_crypto.py` and `voice_vault.py`.
