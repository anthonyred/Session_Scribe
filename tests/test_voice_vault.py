"""
Unit tests for Voice Vault (Argon2id + Windows DPAPI + Passphrase).
Uses tempfile for isolated storage (never touching real keys or events).
Ensures zero logging of passphrases and full round-trip recovery.
"""

import io
import logging
import os
import shutil
import sys
import tempfile
import time
import unittest

# sys.path guard derived from __file__
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from scribe_core.voice_vault import (
    VoiceVault,
    evaluate_passphrase_strength,
    MIN_PASSPHRASE_LENGTH,
)


class TestVoiceVault(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="ss_vault_test_")
        # Use fast parameters for tests so tests run in milliseconds
        self.vault = VoiceVault(
            keys_dir=self.test_dir,
            idle_timeout_seconds=2,  # 2 second timeout for auto-lock test
            time_cost=1,
            memory_cost=64,  # small memory for test speed
        )

    def tearDown(self):
        self.vault.lock()
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_passphrase_strength_evaluator(self):
        # Too short
        res = evaluate_passphrase_strength("Short1!")
        self.assertFalse(res["valid"])
        self.assertEqual(res["label"], "Too short")

        # Weak (12 chars, single case, 1 word)
        res = evaluate_passphrase_strength("alllowercase")
        self.assertFalse(res["valid"])  # Requires >= 14 chars or >= 5 words
        self.assertEqual(res["label"], "Weak")

        # Strong (multi-word passphrase from wordlist)
        res = evaluate_passphrase_strength("hazard galaxy pebble timber canyon velvet")
        self.assertTrue(res["valid"])
        self.assertIn(res["label"], ("Strong", "Very Strong"))
        self.assertGreaterEqual(res["word_count"], 4)

    def test_initialize_and_unlock_flow(self):
        passphrase = "hazard galaxy pebble timber canyon velvet"
        self.assertFalse(self.vault.is_initialized())
        self.assertFalse(self.vault.is_unlocked())

        # Initialize
        ok, msg = self.vault.initialize(passphrase)
        self.assertTrue(ok)
        self.assertTrue(self.vault.is_initialized())
        self.assertTrue(self.vault.is_unlocked())

        # Master key accessible
        key = self.vault.get_master_key_bytes()
        self.assertEqual(len(key), 32)

        # Lock
        self.vault.lock()
        self.assertFalse(self.vault.is_unlocked())
        with self.assertRaises(PermissionError):
            self.vault.get_master_key_bytes()

        # Wrong passphrase fails
        ok_wrong, _ = self.vault.unlock("WrongPassword123!")
        self.assertFalse(ok_wrong)
        self.assertFalse(self.vault.is_unlocked())

        # Right passphrase unlocks
        ok_right, _ = self.vault.unlock(passphrase)
        self.assertTrue(ok_right)
        self.assertTrue(self.vault.is_unlocked())
        self.assertEqual(self.vault.get_master_key_bytes(), key)

    def test_change_passphrase_preserves_master_key(self):
        old_pass = "hazard galaxy pebble timber canyon velvet"
        new_pass = "vicious viable reflect later atom raven"

        self.vault.initialize(old_pass)
        orig_key = self.vault.get_master_key_bytes()

        # Change passphrase
        ok, msg = self.vault.change_passphrase(old_pass, new_pass)
        self.assertTrue(ok)

        # Relock and verify old fails, new works
        self.vault.lock()
        ok_old, _ = self.vault.unlock(old_pass)
        self.assertFalse(ok_old)

        ok_new, _ = self.vault.unlock(new_pass)
        self.assertTrue(ok_new)
        # Master key must be IDENTICAL so files don't need re-encryption
        self.assertEqual(self.vault.get_master_key_bytes(), orig_key)

    def test_auto_lock_after_idle_timeout(self):
        passphrase = "hazard galaxy pebble timber canyon velvet"
        self.vault.initialize(passphrase)
        self.assertTrue(self.vault.is_unlocked())

        # Simulate idle by backdating last active time
        self.vault._last_active_time = time.monotonic() - 10.0
        self.assertFalse(self.vault.is_unlocked())

    def test_auto_lock_suppressed_during_active_run(self):
        passphrase = "hazard galaxy pebble timber canyon velvet"
        self.vault.initialize(passphrase)
        self.vault.begin_run()

        # Backdate last active time
        self.vault._last_active_time = time.monotonic() - 10.0
        # Active run suppresses auto-lock
        self.assertTrue(self.vault.is_unlocked())

        self.vault.end_run()
        # After run ends and idle time past, lock engages
        self.vault._last_active_time = time.monotonic() - 10.0
        self.assertFalse(self.vault.is_unlocked())

    def test_progressive_delay_on_failures(self):
        passphrase = "hazard galaxy pebble timber canyon velvet"
        self.vault.initialize(passphrase)
        self.vault.lock()

        self.assertEqual(self.vault.get_progressive_delay(), 0.0)

        # 1 failure -> 1s delay
        self.vault.unlock("WrongPass12345!")
        self.assertEqual(self.vault._consecutive_failures, 1)
        self.assertEqual(self.vault.get_progressive_delay(), 1.0)

        # 2 failures -> 2s delay
        self.vault.unlock("WrongPass12345!")
        self.assertEqual(self.vault._consecutive_failures, 2)
        self.assertEqual(self.vault.get_progressive_delay(), 2.0)

        # Success resets failures
        self.vault.unlock(passphrase)
        self.assertEqual(self.vault._consecutive_failures, 0)
        self.assertEqual(self.vault.get_progressive_delay(), 0.0)

    def test_passphrase_never_logged(self):
        log_stream = io.StringIO()
        handler = logging.StreamHandler(log_stream)
        vault_logger = logging.getLogger("scribe_core.voice_vault")
        vault_logger.addHandler(handler)
        vault_logger.setLevel(logging.DEBUG)

        secret_passphrase = "hazard galaxy pebble timber canyon velvet"
        self.vault.initialize(secret_passphrase)
        self.vault.lock()
        self.vault.unlock("FailedAttemptPassphraseSecret!")
        self.vault.unlock(secret_passphrase)

        logs = log_stream.getvalue()
        self.assertNotIn(secret_passphrase, logs)
        self.assertNotIn("FailedAttemptPassphraseSecret", logs)
        vault_logger.removeHandler(handler)

    def test_zero_recovery_by_design_and_state_consistency(self):
        """Invariant: Recovery exports are completely removed (zero recovery by design)."""
        passphrase = "hazard galaxy pebble timber canyon velvet"
        self.vault.initialize(passphrase)
        self.assertTrue(self.vault.is_unlocked())

        # Assert recovery export methods do not exist
        self.assertFalse(hasattr(self.vault, "create_recovery_export"))
        self.assertFalse(hasattr(self.vault, "restore_from_recovery"))

        # Assert consistent single state source
        # Note: With idle_timeout_seconds=2 in test setup, state accurately reflects "locking_soon" (<= 120s)
        self.assertIn(self.vault.state(), ("unlocked", "locking_soon"))
        self.assertTrue(self.vault.state_label().startswith("🔓 Vault Unlocked") or "Locking soon" in self.vault.state_label())

        self.vault.lock()
        self.assertEqual(self.vault.state(), "locked")
        self.assertEqual(self.vault.state_label(), "🔒 Vault Locked")


if __name__ == "__main__":
    unittest.main()

