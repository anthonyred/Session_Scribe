# Build Record — Vault v2 Cryptographic Core & Least-Privilege Isolation (Phase V2)

- **Date:** 2026-11-19
- **What was asked:**
  - Implement Phase V2 of the Session Scribe security architecture overhaul:
    1. Apply Phase V1 amendments to `docs/security/threat_model.md` and `docs/security/vault_format.md` (Argon2id calibrated to ~0.5–1s with 256 MiB RAM, store parameters in header, strength evaluator for 5–6 word passphrases, plain-language shredding explanation on SSD/NVMe, BitLocker recommendations, least-privilege matcher vs resolver key isolation, keyed MAC audit log with external mirroring, machine/user binding).
    2. Pin `argon2-cffi==25.1.0` and `cryptography==50.0.2` in `constraints.txt` and `requirements-lock.txt`.
    3. Implement `scribe_core/vault_crypto.py` and `scribe_core/key_hierarchy.py`.
    4. Refactor `scribe_core/voice_vault.py` to use the 5-tier key hierarchy, random 128-bit `spk_` IDs, and padded index concealing record counts and lengths.
    5. Eradicate legacy home-made XOR keystream cipher, pure-Python Argon2, and recovery export.
    6. Eliminate contradictory vault status chips across Tkinter and Qt with a unified 4-state observable source (`uninitialized`, `locked`, `unlocked`, `locking_soon`).
    7. Provide comprehensive unit tests covering wrong passphrase, throttling, tampered file, swapped records (AAD), truncated file, nonce uniqueness, key rotation, crash during write (atomic writes), lock clears keys, and least-privilege enforcement using synthetic test data only.
- **Why:**
  - Biometric voice templates and speaker identities require hardware-anchored envelope encryption, provable zero-leakage invariants, and rigorous least-privilege key isolation so compromised components cannot reach unauthorized data.
  - Legacy implementations suffered from home-made ciphers, unencrypted embedding arrays, and desynchronized status chips.
- **What was built (user-visible behaviour):**
  - **Hardware-Anchored Envelope Cryptography:** Master keys derived via C-accelerated Argon2id (calibrated to ~0.58s with 256 MiB RAM in worker thread) and wrapped via Windows DPAPI, bound to current Windows user and machine.
  - **Least-Privilege Key Isolation:** The acoustic matcher receives only `BiometricZoneKeyContext` (embeddings and reference audio); the transcript resolver receives only `IdentityZoneKeyContext` (speaker names and alias maps); neither can access the other zone's keys.
  - **Unified Status Chips:** Both Tkinter (`VaultStatusChip`) and Qt (`QtVaultChip`) subscribe to `VoiceVault` state notifications, displaying synchronized states:
    - `🔒 Setup Vault` (Uninitialized)
    - `🔒 Vault Locked` (Locked)
    - `🔓 Vault Unlocked` (Unlocked)
    - `⏳ Locking soon (Xm)` (Approaching 30-minute idle timeout)
  - **Multi-Word Passphrase Guidance:** Interactive strength meter at vault setup evaluates word entropy, rewarding 5–6 random word passphrases with clear actionable feedback.
  - **Zero Backdoor Recovery:** Recovery exports are removed by design; lost passphrases mean permanent data loss.
- **How to use it:**
  - Launch Session Scribe and click the header vault chip to initialize with a 5–6 word passphrase.
  - The vault unlocks for 30 minutes of idle time. Active transcription runs suppress auto-lock. Workstation lock (`Win+L`) or screensaver activation immediately locks the vault.
- **Settings and defaults:**
  - `DEFAULT_ARGON2_TIME_COST = 10`
  - `DEFAULT_ARGON2_MEMORY_COST_KIB = 262144` (256 MiB)
  - `DEFAULT_ARGON2_PARALLELISM = 4`
  - `DEFAULT_IDLE_TIMEOUT_SECONDS = 1800` (30 minutes)
  - `LOCKING_SOON_THRESHOLD_SECONDS = 120` (2 minutes)
- **Files changed:**
  - `scribe_core/vault_crypto.py` (New: core AEAD, KDF, DPAPI, keyed MAC, zeroing, strength evaluator)
  - `scribe_core/key_hierarchy.py` (New: 5-tier key hierarchy, least privilege contexts, padded index)
  - `scribe_core/voice_vault.py` (Updated: refactored to Vault v2, 4-state observable machine, auto-lock loop)
  - `gui/vault_dialog.py` (Updated: 4-state chip refresh, multi-word strength evaluator, zero-recovery notice)
  - `gui/qt/components.py` (Updated: 4-state Qt chip refresh and multi-word strength evaluator)
  - `constraints.txt` (Updated: pinned `argon2-cffi==25.1.0` and `cryptography==50.0.2`)
  - `requirements-lock.txt` (Updated: pinned `argon2-cffi`, `argon2-cffi-bindings`, and `cryptography`)
  - `docs/security/threat_model.md` (Updated: Phase V1 amendments applied)
  - `docs/security/vault_format.md` (Updated: Phase V1 amendments applied)
  - `tests/test_vault_crypto.py` (New: 12 comprehensive security invariant unit tests)
  - `tests/test_voice_vault.py` (Updated: updated for Vault v2 and zero-recovery verification)
- **Limitations and known issues:**
  - The Qt vault chip and dialog changes in `gui/qt/components.py` are untested because PySide6 is not installed in the environment (8 Qt unit tests skipped).
  - In-place file overwriting on modern SSD/NVMe drives is unreliable due to Flash Translation Layers (FTL); protection relies on key eradication (crypto-shredding). BitLocker on `C:` and `D:` is recommended.
  - The vault is strictly bound to this Windows user and machine; a Windows reinstallation means the vault cannot be recovered.
- **How it was verified:**
  - Executed `tests/test_vault_crypto.py` (12/12 passed in 8.3s).
  - Executed `tests/test_voice_vault.py` (8/8 passed in 6.4s).
  - Executed full repository test suite `python -m unittest discover -s tests -p "test_*.py"` (204 tests passed, 0 failures, 8 skipped).
  - Executed `scripts/secret_scan.py` (0 secrets found across all files).
- **How to roll it back:**
  - Restore previous version of `voice_vault.py`, remove `vault_crypto.py` and `key_hierarchy.py`, and revert dependency lock files.
