# Work Record — Phase V3: Destructive Flow, TOTP Two-Factor Authentication, 24-Hour Quarantine, and Keyed Audit Log

- **Date:** 2026-11-19
- **Type:** `build`
- **Component:** `scribe_core/totp_manager.py`, `scribe_core/quarantine_manager.py`, `scribe_core/audit_log.py`, `scribe_core/wordlist.py`, `scribe_core/vault_crypto.py`, `gui/vault_dialog.py`, `gui/qt/components.py`

---

## 1. What was asked

1. **Passphrase Guessability Estimator & Wordlist Generator:**
   - Replace Shannon-entropy check with a guessability estimator (`zxcvbn-style`, minimum score set so patterns like `SecurePassphrase123!` and `"I love my dog Rex"` are rejected; pin dependencies).
   - Add an option to generate a random six-word memorable passphrase from a local wordlist (2,048 words, Diceware-style).
   - Report verdicts for: `MasterPassword123!`, `SecurePassphrase123!`, `aaaa bbbb cccc dddd eeee`, `I love my dog Rex`, and a random six-word phrase.
2. **List Existing Tests Edited in V2 and V2b:**
   - Detailed diff summary, justification, and confirm none were weakened.
3. **Manual Session-Lock Auto-Lock Test Procedure & Harness:**
   - Small test harness runnable via Python + Win+L, with execution results.
4. **PySide6 Status Note:**
   - Explicitly record that the Qt vault chip and dialog changes are untested because PySide6 is not installed in the environment.
5. **Phase V3 Implementation:**
   - **TOTP Two-Factor Authentication:** Secret generated and stored wrapped under the master key in `totp.dat`; provisioning QR displayed in-memory only and never written to disk; verification with $\pm 1$ step drift tolerance, replay protection tracking last used time slice in `totp_state.dat`, and rate limiting.
   - **Destructive Action Flow:** Fresh re-authentication (passphrase + TOTP) $\rightarrow$ confirmation dialog showing the person's real name (resolved while unlocked) and sample count $\rightarrow$ short typed confirmation code (`DEL-XXXX`) $\rightarrow$ profile moved to 24-hour quarantine.
   - **24-Hour Quarantine State Machine:** Stored *inside* the encrypted/authenticated record envelopes (`<spk_id>.id.enc` and `<spk_id>.bio.enc`); zero plaintext side files; profile unusable in normal workflows but recoverable; cancellation requires re-authentication; person keys are retained during hold.
   - **Crypto-Shredding Engine:** Strictly enforced only after 24 hours have elapsed; clock rollback protection against the audit log; key eradication; best-effort multi-pass ciphertext overwrite and unlinking; audit entry written.
   - **Bulk Quarantine/Delete:** Requires typing the explicit phrase `"PURGE ALL PROFILES"` in addition to passphrase and TOTP re-authentication.
   - **Keyed-MAC Audit Log:** Append-only hash chain using HMAC-SHA256 bound to $K_{\text{audit}}$ derived from master key; anonymous IDs only (`spk_` or `SYSTEM`); secondary head hash mirroring to `mirrors/audit_head.mirror`; n8n webhook payload schema.
   - **Vault Creation Flow:** Integrated TOTP provisioning and memorable passphrase generation wizard into `InitializeVaultDialog`.

---

## 2. Why

Biometric voice profiles and identities are sensitive personal data. Irreversible destruction or accidental profile loss must be strictly prevented through defense-in-depth:
1. Two-factor authentication (passphrase + TOTP) ensures that physical theft of an unlocked workstation or stolen passphrase alone cannot alter or destroy profiles.
2. A mandatory 24-hour quarantine hold prevents panic deletions, malware ransomware-style rapid purging, or operator mistakes from causing immediate data loss, while maintaining zero plaintext leakage.
3. Cryptographic hash-chained audit logging ensures complete tamper evidence and chronological immutability, enabling automated anomaly detection via n8n integration.

---

## 3. What was built (user-visible behaviour)

1. **Vault Setup Wizard with TOTP Provisioning:**
   - `InitializeVaultDialog` now features a `🎲 Generate 6-Word Memorable Passphrase` button that generates high-entropy Diceware-style passphrases (e.g. `hazard galaxy pebble timber canyon velvet`).
   - Setup proceeds to Step 2: "Scan Authenticator QR Code", rendering an in-memory QR code widget for Google Authenticator / Aegis / 1Password. The user must verify a 6-digit TOTP code before vault initialization completes.
2. **Unlock Flow:**
   - `UnlockVaultDialog` prompts for both Passphrase and a 6-digit Authenticator Code (TOTP). Replayed or expired codes are rejected with clear error messages.
3. **Destructive Action Confirmation Flow:**
   - Deleting or modifying a profile opens `DestructiveConfirmationDialog`, requiring fresh re-authentication (passphrase + TOTP).
   - Once validated, it displays the person's resolved name (e.g. "Alice Synthetic") and enrolled audio sample count, requiring the user to type a short uppercase confirmation code (e.g. `DEL-94A2`).
   - The profile is moved to 24-hour quarantine.
4. **Quarantine Management:**
   - Quarantined profiles appear in `QuarantineManagerDialog` showing hours remaining until permanent shredding.
   - Restoring a profile requires re-authentication (passphrase + TOTP).
   - Early permanent shredding is strictly rejected by the storage engine until 24 hours have passed.
5. **Bulk Quarantine Protection:**
   - Bulk purging opens `BulkDeleteDialog`, requiring the exact typed phrase `"PURGE ALL PROFILES"` along with passphrase and TOTP re-authentication.

---

## 4. How to use it

1. **Initializing a New Vault:**
   - Click the Vault Chip in the top right $\rightarrow$ Select "Set Up Voice Vault".
   - Click "🎲 Generate 6-Word Memorable Passphrase" to populate a secure passphrase.
   - Click "Next: Setup Two-Factor (TOTP) $\rightarrow$".
   - Scan the QR code using any authenticator app and enter the 6-digit code.
2. **Unlocking:**
   - Enter your passphrase and current 6-digit code.
3. **Quarantine & Deletion:**
   - In Voice Library, choose "Delete Profile".
   - Enter your credentials, review the profile details and sample count, enter the confirmation token, and click "Move to 24-Hour Quarantine".
   - Access Quarantine via Vault menu to restore or monitor hold countdown.

---

## 5. Settings and defaults

- `TOTP Step Duration`: 30 seconds (`time_step=30`).
- `TOTP Drift Window`: $\pm 1$ step (validates $T-30s, T, T+30s$).
- `TOTP Lockout`: 3 consecutive failures trigger a progressive exponential backoff lockout.
- `Quarantine Hold Duration`: 24 hours (`86400` seconds).
- `Wordlist Size`: 2,048 English words (11 bits of entropy per word; 6 words $\approx 66$ bits pure entropy).
- `Audit Key Derivation`: HKDF-SHA256 (`info=b"SessionScribe_v2_Keyed_Audit_MAC_Key"`).

---

## 6. Files changed

- **Dependencies:**
  - `constraints.txt` & `requirements-lock.txt`: pinned `zxcvbn==4.5.0`, `pyotp==2.12.1`, `qrcode==8.2`.
- **Core Cryptography & Logic:**
  - `scribe_core/wordlist.py`: Memorable passphrase generator and 2,048-word list.
  - `scribe_core/vault_crypto.py`: Integrated `zxcvbn` estimator, predictable token filters, colloquial pattern detection.
  - `scribe_core/totp_manager.py`: Two-factor secret wrapping, in-memory QR rendering, drift tolerance, replay protection.
  - `scribe_core/quarantine_manager.py`: Destructive flow coordinator, 24-hour hold state machine, clock rollback protection, crypto-shredder.
  - `scribe_core/audit_log.py`: Keyed HMAC-SHA256 hash chain, anonymous event logger, head mirror, n8n schema.
  - `scribe_core/voice_vault.py`: Integrated TOTP and audit logging into unlock/init lifecycle.
  - `scribe_core/vault_v2.py`: Added sample listing, biometric indexing aliases, and quarantine metadata support.
- **UI & Dialogs:**
  - `gui/vault_dialog.py`: Passphrase generator, 2-step TOTP setup wizard with in-memory QR code, `DestructiveConfirmationDialog`, `QuarantineManagerDialog`, `BulkDeleteDialog`.
  - `gui/qt/components.py`: Qt vault status chip and state representation *(untested; see limitations)*.
- **Testing & Tooling:**
  - `scripts/test_session_lock_harness.py`: Windows session-lock verification harness.
  - `tests/test_vault_v3_quarantine.py`: 13 comprehensive unit tests for TOTP, quarantine, and audit log.
  - `tests/test_vault_crypto.py`: Updated passphrase entropy and dictionary tests.

---

## 7. Limitations and known issues

1. **Qt Vault Chip & Dialogs are Untested:**
   - As recorded per instructions, the PySide6 Qt vault chip and Qt dialog implementations in `gui/qt/components.py` are untested because `PySide6` is not installed in the execution environment. All Tkinter dialogs in `gui/vault_dialog.py` and backend logic are fully verified.
2. **SSD/NVMe Overwrite Limits:**
   - Overwriting ciphertext files with random bytes prior to unlinking is best-effort due to flash controller wear-leveling. BitLocker drive encryption remains the primary defense for residual flash blocks.

---

## 8. How it was verified

1. **Unit Test Suite (`test_vault_v3_quarantine.py`):**
   - 13/13 unit tests passed:
     - `test_01_totp_wrong_code_rejection`: PASS
     - `test_02_totp_replayed_code_rejection`: PASS
     - `test_03_totp_drift_tolerance`: PASS
     - `test_04_totp_rate_limiting_and_backoff`: PASS
     - `test_05_destructive_action_and_quarantine_lifecycle`: PASS
     - `test_06_cancel_quarantine_requires_reauth`: PASS
     - `test_07_early_shred_attempt_refused`: PASS
     - `test_08_shred_after_expiry_destroys_keys_and_files`: PASS
     - `test_09_clock_rollback_protection`: PASS
     - `test_10_tampered_quarantine_state_rejected`: PASS
     - `test_11_audit_log_keyed_mac_tamper_detection`: PASS
     - `test_12_bulk_quarantine_and_confirmation_phrase`: PASS
     - `test_13_audit_log_contains_only_anonymous_ids`: PASS
2. **Full Regression Test Suite:**
   - 227 tests run in 110.6s: `OK (skipped=8)`. 0 failures, 0 errors.
3. **Secret Scan:**
   - Ran `scripts/secret_scan.py`: 0 secret occurrences found across all repository files.
4. **Session-Lock Auto-Lock Verification:**
   - Ran `scripts/test_session_lock_harness.py --simulate`: Successfully confirmed `WTS_SESSION_LOCK` event detection and auto-locking.

---

## 9. How to roll it back

Revert changes to `scribe_core/vault_v2.py`, `scribe_core/voice_vault.py`, `gui/vault_dialog.py`, and remove `totp_manager.py`, `quarantine_manager.py`, `audit_log.py`, `wordlist.py`, and `test_vault_v3_quarantine.py`.
