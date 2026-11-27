"""
tests/test_vault_v3_quarantine.py
Unit tests for Phase V3: TOTP 2FA, Destructive Actions, 24-Hour Quarantine State Machine,
Crypto-Shredding Engine, and Keyed-MAC Audit Log.

Uses synthetic data only in tempfile.mkdtemp().
Never touches real vault or personal data.
"""

from datetime import datetime, timezone, timedelta
import os
import shutil
import sys
import tempfile
import time
import unittest
import tkinter as tk

# Ensure project root is in sys.path
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import pyotp

from scribe_core.voice_vault import VoiceVault
from scribe_core.vault_v2 import VaultV2StorageEngine
from scribe_core.totp_manager import TOTPManager
from scribe_core.audit_log import KeyedAuditLog, AUDIT_LOG_NAME
from scribe_core.quarantine_manager import QuarantineManager, BULK_CONFIRMATION_TEXT
from scribe_core.vault_crypto import (
    generate_speaker_id,
    TamperError,
    AuthenticationError,
)
from gui.vault_dialog import (
    InitializeVaultDialog,
    UnlockVaultDialog,
    DestructiveConfirmationDialog,
    QuarantineManagerDialog,
    BulkDeleteDialog,
)
from gui import theme


class TestVaultV3QuarantineAndTOTP(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            cls.root = tk.Tk()
            cls.root.withdraw()
        except Exception:
            cls.root = None

    @classmethod
    def tearDownClass(cls):
        if cls.root:
            try:
                cls.root.destroy()
            except Exception:
                pass

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="ss_test_v3_")
        self.vault_dir = os.path.join(self.temp_dir, "vault")
        os.makedirs(self.vault_dir, exist_ok=True)

        self.vault = VoiceVault(
            keys_dir=self.vault_dir,
            idle_timeout_seconds=300,
            time_cost=1,
            memory_cost=1024,
            parallelism=2,
        )
        self.passphrase = "hazard galaxy pebble timber canyon velvet"

        # Initialize vault without TOTP first
        ok, msg = self.vault.initialize(self.passphrase)
        self.assertTrue(ok, f"Init failed: {msg}")

        self.storage = VaultV2StorageEngine(self.vault)
        self.totp_mgr = TOTPManager(self.vault_dir)
        self.audit_log = KeyedAuditLog(self.vault_dir)
        self.quarantine_mgr = QuarantineManager(self.storage)

        # Enrol TOTP
        self.totp_secret = self.totp_mgr.generate_new_secret()
        self.pytotp = pyotp.TOTP(self.totp_secret)
        code = self.pytotp.now()
        master_key = self.vault.get_master_key_bytes()
        en_ok, en_msg = self.totp_mgr.enroll_secret(master_key, self.totp_secret, code)
        self.assertTrue(en_ok, f"TOTP enroll failed: {en_msg}")

    def tearDown(self):
        self.vault.lock()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_01_totp_wrong_code_rejection(self):
        """Invariant: Wrong 6-digit TOTP code is rejected."""
        master_key = self.vault.get_master_key_bytes()
        ok, msg = self.totp_mgr.verify_code(master_key, "000000")
        self.assertFalse(ok)
        self.assertIn("Invalid TOTP code", msg)

    def test_02_totp_replayed_code_rejection(self):
        """Invariant: Replaying an already-consumed TOTP code is rejected."""
        master_key = self.vault.get_master_key_bytes()
        # Initial enrollment consumed current time slice
        code = self.pytotp.now()
        ok, msg = self.totp_mgr.verify_code(master_key, code)
        self.assertFalse(ok)
        self.assertIn("already been used", msg)

    def test_03_totp_drift_tolerance(self):
        """Invariant: TOTP accepts ±1 step drift tolerance."""
        master_key = self.vault.get_master_key_bytes()
        # Test future slice (+30s)
        base_time = time.time() + 30
        code = self.pytotp.at(base_time)

        ok, msg = self.totp_mgr.verify_code(master_key, code, at_time=base_time)
        self.assertTrue(ok, f"Future drift code failed: {msg}")

    def test_04_totp_rate_limiting_and_backoff(self):
        """Invariant: Consecutive failed TOTP attempts incur progressive lockout delays."""
        master_key = self.vault.get_master_key_bytes()
        for _ in range(3):
            self.totp_mgr.verify_code(master_key, "123456")

        self.assertGreater(self.totp_mgr._consecutive_failures, 0)
        self.assertGreater(self.totp_mgr._lockout_until, time.monotonic())

        # Attempt during lockout fails immediately
        ok, msg = self.totp_mgr.verify_code(master_key, "999999")
        self.assertFalse(ok)
        self.assertIn("Too many failed TOTP attempts", msg)

    def test_05_destructive_action_and_quarantine_lifecycle(self):
        """
        Invariant: Profile moves to 24-hour quarantine inside encrypted records.
        No plaintext side files. Person keys are NOT destroyed.
        """
        spk_id = self.storage.create_identity_record("Alice Synthetic", roles=["Speaker"])
        self.storage.create_biometric_record(spk_id, b"EMB_RAW_BYTES_12345", sample_names=["clip1.wav"])

        t0 = time.time() + 30
        c1 = self.pytotp.at(t0)
        prep_ok, prep_msg, prep_data = self.quarantine_mgr.prepare_destructive_action(
            spk_id, self.passphrase, c1, at_time=t0
        )
        self.assertTrue(prep_ok, f"Prepare failed: {prep_msg}")
        self.assertEqual(prep_data["name"], "Alice Synthetic")
        self.assertTrue(prep_data["confirmation_code"].startswith("DEL-"))

        # Step 2: Move to Quarantine
        t1 = t0 + 30
        c2 = self.pytotp.at(t1)
        token = prep_data["confirmation_code"]
        q_ok, q_msg = self.quarantine_mgr.move_to_quarantine(
            spk_id=spk_id,
            passphrase=self.passphrase,
            totp_code=c2,
            expected_code=token,
            entered_code=token,
            at_time=t1,
        )
        self.assertTrue(q_ok, f"Quarantine failed: {q_msg}")

        # Assert NO plaintext side file exists
        q_dir = os.path.join(self.vault_dir, "quarantine")
        if os.path.isdir(q_dir):
            self.assertEqual(len(os.listdir(q_dir)), 0, "Quarantine directory must NOT hold plaintext side files!")

        # Verify state inside encrypted record
        id_rec = self.storage.read_identity_record(spk_id)
        self.assertTrue(id_rec["quarantine"]["is_quarantined"])
        self.assertEqual(id_rec["quarantine"]["confirmation_code"], token)

        # Verify index marks profile as quarantined
        id_index = self.storage._read_identity_index()
        self.assertTrue(id_index[spk_id]["is_quarantined"])

        # Verify person key still exists and is accessible
        bio_rec, emb = self.storage.read_biometric_record(spk_id)
        self.assertEqual(emb, b"EMB_RAW_BYTES_12345")

    def test_06_cancel_quarantine_requires_reauth(self):
        """Invariant: Cancelling quarantine restores profile and clears quarantine flags."""
        spk_id = self.storage.create_identity_record("Bob Synthetic", roles=["Host"])
        self.storage.create_biometric_record(spk_id, b"EMB_BOB_BYTES")

        t0 = time.time() + 30
        c1 = self.pytotp.at(t0)
        self.quarantine_mgr.move_to_quarantine(spk_id, self.passphrase, c1, "DEL-1", "DEL-1", at_time=t0)

        # Wrong credentials fail
        t1 = t0 + 30
        c2 = self.pytotp.at(t1)
        fail_ok, _ = self.quarantine_mgr.cancel_quarantine(spk_id, "wrong passphrase", c2, at_time=t1)
        self.assertFalse(fail_ok)

        # Valid credentials succeed
        t2 = t0 + 60
        c3 = self.pytotp.at(t2)
        res_ok, res_msg = self.quarantine_mgr.cancel_quarantine(spk_id, self.passphrase, c3, at_time=t2)
        self.assertTrue(res_ok, f"Cancel quarantine failed: {res_msg}")

        # Assert flags cleared
        id_rec = self.storage.read_identity_record(spk_id)
        self.assertFalse(id_rec["quarantine"]["is_quarantined"])

    def test_07_early_shred_attempt_refused(self):
        """Invariant: Attempting to crypto-shred before 24 hours have elapsed is strictly refused."""
        spk_id = self.storage.create_identity_record("Charlie Synthetic")
        self.storage.create_biometric_record(spk_id, b"EMB_CHARLIE")

        t0 = time.time() + 30
        c1 = self.pytotp.at(t0)
        self.quarantine_mgr.move_to_quarantine(spk_id, self.passphrase, c1, "DEL-2", "DEL-2", at_time=t0)

        t1 = t0 + 30
        c2 = self.pytotp.at(t1)
        shred_ok, shred_msg = self.quarantine_mgr.crypto_shred(spk_id, self.passphrase, c2, at_time=t1)
        self.assertFalse(shred_ok)
        self.assertIn("Early shred attempt refused", shred_msg)

    def test_08_shred_after_expiry_destroys_keys_and_files(self):
        """Invariant: After 24 hours, crypto-shred eradicates keys and ciphertext files."""
        spk_id = self.storage.create_identity_record("Dave Synthetic")
        self.storage.create_biometric_record(spk_id, b"EMB_DAVE")
        self.storage.save_sample_audio(spk_id, "sample1", b"WAV_DATA_SAMPLE")

        t0 = time.time() + 30
        c1 = self.pytotp.at(t0)
        self.quarantine_mgr.move_to_quarantine(spk_id, self.passphrase, c1, "DEL-3", "DEL-3", at_time=t0)

        id_file = os.path.join(self.storage.identity_records_dir, f"{spk_id}.id.enc")
        bio_file = os.path.join(self.storage.biometric_records_dir, f"{spk_id}.bio.enc")
        self.assertTrue(os.path.isfile(id_file))
        self.assertTrue(os.path.isfile(bio_file))

        # Shred with simulated time elapsed (>= 24h)
        t1 = t0 + 30
        c2 = self.pytotp.at(t1)
        shred_ok, shred_msg = self.quarantine_mgr.crypto_shred(
            spk_id, self.passphrase, c2, simulate_time_elapsed=True, at_time=t1
        )
        self.assertTrue(shred_ok, f"Crypto-shred failed: {shred_msg}")

        # Files must be unlinked
        self.assertFalse(os.path.isfile(id_file))
        self.assertFalse(os.path.isfile(bio_file))
        self.assertNotIn(spk_id, self.storage.list_identity_ids())

    def test_09_clock_rollback_protection(self):
        """Invariant: Crypto-shred is refused if current system time is rolled back behind audit log."""
        spk_id = self.storage.create_identity_record("Eve Synthetic")
        self.storage.create_biometric_record(spk_id, b"EMB_EVE")

        t0 = time.time() + 30
        c1 = self.pytotp.at(t0)
        self.quarantine_mgr.move_to_quarantine(spk_id, self.passphrase, c1, "DEL-4", "DEL-4", at_time=t0)

        # Fake an audit log entry in the future
        master_key = self.vault.get_master_key_bytes()
        audit_key = self.audit_log.derive_audit_key(master_key)
        future_time_str = (datetime.now(timezone.utc) + timedelta(days=5)).isoformat()
        self.audit_log.append_entry(
            audit_key, "FUTURE_EVENT", "SYSTEM", {"fake_future": future_time_str}, timestamp=future_time_str
        )

        # Now attempting shred while clock is earlier than audit entry fails
        t1 = t0 + 30
        c2 = self.pytotp.at(t1)
        shred_ok, shred_msg = self.quarantine_mgr.crypto_shred(
            spk_id, self.passphrase, c2, simulate_time_elapsed=True, at_time=t1
        )
        self.assertFalse(shred_ok)
        self.assertIn("Clock rollback detected", shred_msg)

    def test_10_tampered_quarantine_state_rejected(self):
        """Invariant: Modifying quarantined ciphertext fails decryption via TamperError."""
        spk_id = self.storage.create_identity_record("Frank Synthetic")
        self.storage.create_biometric_record(spk_id, b"EMB_FRANK")

        t0 = time.time() + 30
        c1 = self.pytotp.at(t0)
        self.quarantine_mgr.move_to_quarantine(spk_id, self.passphrase, c1, "DEL-5", "DEL-5", at_time=t0)

        id_path = os.path.join(self.storage.identity_records_dir, f"{spk_id}.id.enc")
        self.assertTrue(os.path.isfile(id_path))
        with open(id_path, "rb") as f:
            data = bytearray(f.read())
        # Tamper one byte in the middle of ciphertext
        data[45] ^= 0xFF
        with open(id_path, "wb") as f:
            f.write(data)

        with self.assertRaises(TamperError):
            self.storage.read_identity_record(spk_id)

    def test_11_audit_log_keyed_mac_tamper_detection(self):
        """Invariant: Modifying or reordering audit log entries breaks hash chain validation."""
        master_key = self.vault.get_master_key_bytes()
        audit_key = self.audit_log.derive_audit_key(master_key)

        ok, err = self.audit_log.verify_chain(audit_key)
        self.assertTrue(ok, f"Audit log verification failed: {err}")

        # Tamper with audit.log
        with open(self.audit_log.log_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        self.assertGreater(len(lines), 0)
        lines[0] = lines[0].replace("VAULT_INITIALIZED", "MALICIOUS_TAMPER")
        with open(self.audit_log.log_path, "w", encoding="utf-8") as f:
            f.writelines(lines)

        tampered_ok, tampered_err = self.audit_log.verify_chain(audit_key)
        self.assertFalse(tampered_ok)
        self.assertIn("HMAC verification failed", tampered_err)

    def test_12_bulk_quarantine_and_confirmation_phrase(self):
        """Invariant: Bulk quarantine requires exact phrase 'PURGE ALL PROFILES'."""
        spk1 = self.storage.create_identity_record("Bulk 1")
        spk2 = self.storage.create_identity_record("Bulk 2")
        self.storage.create_biometric_record(spk1, b"EMB1")
        self.storage.create_biometric_record(spk2, b"EMB2")

        t0 = time.time() + 30
        c1 = self.pytotp.at(t0)
        # Typo in phrase fails
        bad_ok, bad_msg = self.quarantine_mgr.bulk_quarantine(
            [spk1, spk2], self.passphrase, c1, "purge all", at_time=t0
        )
        self.assertFalse(bad_ok)

        t1 = t0 + 30
        c2 = self.pytotp.at(t1)
        good_ok, good_msg = self.quarantine_mgr.bulk_quarantine(
            [spk1, spk2], self.passphrase, c2, BULK_CONFIRMATION_TEXT, at_time=t1
        )
        self.assertTrue(good_ok, f"Bulk quarantine failed: {good_msg}")
        self.assertIn("Successfully quarantined 2 of 2", good_msg)

    def test_13_ui_dialogs_stability_light_and_dark(self):
        """Invariant: All new dialogs instantiate cleanly under light and dark theme without crashing."""
        if not self.root:
            self.skipTest("Headless environment: Tk root not available")

        # Test under both theme modes
        for mode in ("light", "dark"):
            theme.apply_global_theme(self.root, theme_mode=mode)

            # 1. InitializeVaultDialog
            dlg_init = InitializeVaultDialog(self.root, self.vault)
            dlg_init.update_idletasks()
            dlg_init.destroy()

            # 2. UnlockVaultDialog
            dlg_unlock = UnlockVaultDialog(self.root, self.vault)
            dlg_unlock.update_idletasks()
            dlg_unlock.destroy()

            # 3. DestructiveConfirmationDialog
            dlg_dest = DestructiveConfirmationDialog(
                self.root,
                spk_id="spk_synthetic1234",
                name="Synthetic Persona",
                sample_count=3,
                confirmation_code="DEL-99A1",
                quarantine_mgr=self.quarantine_mgr,
            )
            dlg_dest.update_idletasks()
            dlg_dest.destroy()

            # 4. QuarantineManagerDialog
            dlg_q = QuarantineManagerDialog(self.root, self.quarantine_mgr)
            dlg_q.update_idletasks()
            dlg_q.destroy()

            # 5. BulkDeleteDialog
            dlg_bulk = BulkDeleteDialog(
                self.root,
                spk_ids=["spk_1", "spk_2"],
                quarantine_mgr=self.quarantine_mgr,
            )
            dlg_bulk.update_idletasks()
            dlg_bulk.destroy()

    def test_14_quarantined_profile_excluded_from_matching_and_resolution(self):
        """
        Invariant: Quarantined profiles are strictly excluded from:
        - list_identity_ids()
        - list_biometric_ids()
        - resolve_speaker_name()
        - get_biometric_embedding_for_matching()
        """
        alice_id = self.storage.create_identity_record("Alice Persona", roles=["Speaker"])
        self.storage.create_biometric_record(alice_id, b"EMBEDDING_ALICE_BYTES_99")

        # Initially active: present in both indexes and resolvable
        self.assertIn(alice_id, self.storage.list_identity_ids())
        self.assertIn(alice_id, self.storage.list_biometric_ids())
        self.assertEqual(self.storage.resolve_speaker_name(alice_id), "Alice Persona")
        self.assertEqual(self.storage.get_biometric_embedding_for_matching(alice_id), b"EMBEDDING_ALICE_BYTES_99")

        # Move to quarantine
        t0 = time.time() + 30
        c1 = self.pytotp.at(t0)
        self.quarantine_mgr.move_to_quarantine(alice_id, self.passphrase, c1, "DEL-ALICE", "DEL-ALICE", at_time=t0)

        # Quarantined: MUST be excluded from standard listing, matching, and resolution
        self.assertNotIn(alice_id, self.storage.list_identity_ids())
        self.assertNotIn(alice_id, self.storage.list_biometric_ids())
        self.assertIsNone(self.storage.resolve_speaker_name(alice_id))
        self.assertIsNone(self.storage.get_biometric_embedding_for_matching(alice_id))

        # Explicit include_quarantined=True retains access for quarantine management
        self.assertIn(alice_id, self.storage.list_identity_ids(include_quarantined=True))
        self.assertIn(alice_id, self.storage.list_biometric_ids(include_quarantined=True))

        # Restoring profile restores matching and resolution
        t1 = t0 + 30
        c2 = self.pytotp.at(t1)
        self.quarantine_mgr.cancel_quarantine(alice_id, self.passphrase, c2, at_time=t1)
        self.assertIn(alice_id, self.storage.list_identity_ids())
        self.assertIn(alice_id, self.storage.list_biometric_ids())
        self.assertEqual(self.storage.resolve_speaker_name(alice_id), "Alice Persona")
        self.assertEqual(self.storage.get_biometric_embedding_for_matching(alice_id), b"EMBEDDING_ALICE_BYTES_99")

    def test_15_daily_unlock_without_totp_and_totp_reset_cooldown(self):
        """
        Invariant: Daily unlock requires passphrase only (no TOTP prompt).
        TOTP reset requires passphrase plus a mandatory 24-hour security delay.
        """
        self.vault.lock()
        self.assertFalse(self.vault.is_unlocked())

        # Daily unlock with passphrase only succeeds even though TOTP is enrolled!
        ok_unlock, msg_unlock = self.vault.unlock(self.passphrase)
        self.assertTrue(ok_unlock, f"Daily unlock failed: {msg_unlock}")
        self.assertTrue(self.vault.is_unlocked())

        # Schedule TOTP reset
        res_sched, msg_sched = self.vault.schedule_totp_reset(self.passphrase)
        self.assertTrue(res_sched)
        self.assertIn("mandatory 24-hour security delay", msg_sched)

        status = self.vault.get_totp_reset_status()
        self.assertTrue(status["active"])
        self.assertFalse(status["ready"])
        self.assertGreater(status["hours_remaining"], 23.0)

        # Early reset completion refused
        new_secret = self.vault.totp_manager.generate_new_secret()
        new_pytotp = pyotp.TOTP(new_secret)
        code_new = new_pytotp.now()

        res_early, msg_early = self.vault.complete_totp_reset(
            self.passphrase, new_secret, code_new, simulate_time_elapsed=False
        )
        self.assertFalse(res_early)
        self.assertIn("Early TOTP reset refused", msg_early)

        # Reset after 24h simulated delay succeeds
        res_late, msg_late = self.vault.complete_totp_reset(
            self.passphrase, new_secret, code_new, simulate_time_elapsed=True
        )
        self.assertTrue(res_late, f"Complete TOTP reset failed: {msg_late}")
        self.assertFalse(self.vault.get_totp_reset_status()["active"])


if __name__ == "__main__":
    unittest.main()
