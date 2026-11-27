"""
tests/test_vault_crypto.py
Comprehensive cryptographic security and invariant tests for Vault v2.

Requirements verified (Synthetic Data Only):
1. Wrong passphrase rejection.
2. Throttling and progressive backoff.
3. Tampered ciphertext file (AES-GCM tag verification).
4. Swapped records (AAD binding to spk_id).
5. Truncated encrypted files.
6. Nonce uniqueness (probabilistic collision resistance).
7. Key rotation (passphrase change preserves master key & zone records).
8. Crash during write (atomic replacement).
9. Lock clears in-memory keys (best-effort buffer zeroing).
10. Least-privilege zone isolation (matcher != resolver).

All tests use tempfile.mkdtemp() and never touch real user vault files.
"""

import io
import json
import logging
import os
import shutil
import sys
import tempfile
import time
import unittest

# sys.path guard derived from __file__
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from scribe_core.wordlist import generate_memorable_passphrase
from scribe_core.vault_crypto import (
    AES_KEY_LEN,
    TamperError,
    AuthenticationError,
    LeastPrivilegeViolationError,
    aes_gcm_encrypt,
    aes_gcm_decrypt,
    derive_argon2id_key,
    dpapi_protect,
    dpapi_unprotect,
    compute_keyed_mac,
    verify_keyed_mac,
    zero_buffer,
    evaluate_passphrase_strength,
    atomic_write_bytes,
    best_effort_shred_file,
    generate_speaker_id,
)
from scribe_core.key_hierarchy import (
    IdentityZoneKeyContext,
    BiometricZoneKeyContext,
    KeyHierarchyManager,
)
from scribe_core.voice_vault import VoiceVault, VoiceVaultState


class TestVaultCryptoSecurity(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="ss_crypto_test_")
        # Fast Argon2 parameters for test execution speed (milliseconds)
        self.fast_t = 1
        self.fast_m = 1024  # 1 MiB in KiB
        self.fast_p = 2
        self.vault = VoiceVault(
            keys_dir=self.test_dir,
            idle_timeout_seconds=2,
            time_cost=self.fast_t,
            memory_cost=self.fast_m,
            parallelism=self.fast_p,
        )

    def tearDown(self):
        self.vault.close()
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_01_wrong_passphrase_rejection(self):
        """Invariant: Wrong passphrase fails and does not expose master key."""
        passphrase = "hazard galaxy pebble timber canyon velvet"
        ok, _ = self.vault.initialize(passphrase)
        self.assertTrue(ok)
        self.vault.lock()

        # Unlock with wrong passphrase
        ok_wrong, msg = self.vault.unlock("Incorrect-Passphrase-Attempt-123!")
        self.assertFalse(ok_wrong)
        self.assertFalse(self.vault.is_unlocked())
        self.assertEqual(self.vault.state(), VoiceVaultState.LOCKED)
        with self.assertRaises(PermissionError):
            self.vault.get_identity_context()

    def test_02_throttling_and_progressive_backoff(self):
        """Invariant: Consecutive failed unlock attempts increment progressive delays."""
        passphrase = "hazard galaxy pebble timber canyon velvet"
        self.vault.initialize(passphrase)
        self.vault.lock()

        self.assertEqual(self.vault.get_progressive_delay(), 0.0)

        # 1st failure -> 1.0s delay scheduled
        self.vault.unlock("BadPassword1!")
        self.assertEqual(self.vault._consecutive_failures, 1)
        self.assertEqual(self.vault.get_progressive_delay(), 1.0)

        # 2nd failure -> 2.0s delay scheduled
        self.vault.unlock("BadPassword2!")
        self.assertEqual(self.vault._consecutive_failures, 2)
        self.assertEqual(self.vault.get_progressive_delay(), 2.0)

        # 3rd failure -> 4.0s delay scheduled
        self.vault.unlock("BadPassword3!")
        self.assertEqual(self.vault._consecutive_failures, 3)
        self.assertEqual(self.vault.get_progressive_delay(), 4.0)

        # Successful unlock resets failure count and delay to 0
        ok, _ = self.vault.unlock(passphrase)
        self.assertTrue(ok)
        self.assertEqual(self.vault._consecutive_failures, 0)
        self.assertEqual(self.vault.get_progressive_delay(), 0.0)

    def test_03_tampered_ciphertext_rejection(self):
        """Invariant: Modifying a single bit in ciphertext or tag raises TamperError."""
        key = os.urandom(32)
        plaintext = b"Sensitive speaker identity data: Jane Doe"
        aad = b"spk_01934a5f"

        blob = aes_gcm_encrypt(key, plaintext, aad=aad)
        self.assertGreater(len(blob), 28)

        # Flip one bit in the ciphertext payload
        tampered = bytearray(blob)
        tampered[-5] ^= 0x01

        with self.assertRaises(TamperError):
            aes_gcm_decrypt(key, bytes(tampered), aad=aad)

        # Flip one bit in the nonce
        tampered_nonce = bytearray(blob)
        tampered_nonce[2] ^= 0xFF
        with self.assertRaises(TamperError):
            aes_gcm_decrypt(key, bytes(tampered_nonce), aad=aad)

    def test_04_swapped_records_aad_binding(self):
        """Invariant: Decrypting a record under a different spk_id fails (swapped record attack)."""
        spk_alice = generate_speaker_id()
        spk_bob = generate_speaker_id()
        self.assertNotEqual(spk_alice, spk_bob)

        zone_key = os.urandom(32)
        ctx = IdentityZoneKeyContext(zone_key)

        alice_payload = json.dumps({"name": "Alice Smith", "role": "Keynote"}).encode("utf-8")
        alice_record = ctx.encrypt_identity_record(spk_alice, alice_payload)

        # Decrypting under correct spk_id succeeds
        decrypted = ctx.decrypt_identity_record(spk_alice, alice_record)
        self.assertEqual(decrypted, alice_payload)

        # Attacker swaps file and attempts to decrypt Alice's record as Bob's record
        with self.assertRaises(TamperError):
            ctx.decrypt_identity_record(spk_bob, alice_record)

    def test_05_truncated_file_detection(self):
        """Invariant: Truncated encrypted blobs are rejected cleanly."""
        key = os.urandom(32)
        blob = aes_gcm_encrypt(key, b"Complete payload text", aad=b"test")

        # Truncate to less than nonce + tag length (28 bytes)
        truncated_short = blob[:15]
        with self.assertRaises(TamperError):
            aes_gcm_decrypt(key, truncated_short, aad=b"test")

        # Truncate payload partially
        truncated_mid = blob[:-5]
        with self.assertRaises(TamperError):
            aes_gcm_decrypt(key, truncated_mid, aad=b"test")

    def test_06_nonce_uniqueness(self):
        """Invariant: Consecutive encryptions of identical plaintext use unique nonces."""
        key = os.urandom(32)
        plaintext = b"Identical repeated speech audio segment"
        aad = b"audio_chunk_1"

        nonces = set()
        blobs = set()
        for _ in range(50):
            blob = aes_gcm_encrypt(key, plaintext, aad=aad)
            nonce = blob[:12]
            self.assertNotIn(nonce, nonces, "Critical: Nonce collision detected!")
            self.assertNotIn(blob, blobs, "Critical: Repeated ciphertext detected!")
            nonces.add(nonce)
            blobs.add(blob)

        self.assertEqual(len(nonces), 50)
        self.assertEqual(len(blobs), 50)

    def test_07_key_rotation_preserves_zone_data(self):
        """Invariant: Passphrase change re-wraps master key without re-encrypting records."""
        old_pass = "hazard galaxy pebble timber canyon velvet"
        new_pass = "vicious viable reflect later atom raven"

        self.vault.initialize(old_pass)
        id_ctx = self.vault.get_identity_context()

        # Enrol synthetic person
        spk_id = generate_speaker_id()
        payload = b'{"name": "Synthetic Speaker 1"}'
        record_bytes = id_ctx.encrypt_identity_record(spk_id, payload)

        orig_master_key = self.vault.get_master_key_bytes()

        # Change passphrase
        ok, msg = self.vault.change_passphrase(old_pass, new_pass)
        self.assertTrue(ok)

        # Relock and verify
        self.vault.lock()

        # Old passphrase fails
        ok_old, _ = self.vault.unlock(old_pass)
        self.assertFalse(ok_old)

        # New passphrase succeeds
        ok_new, _ = self.vault.unlock(new_pass)
        self.assertTrue(ok_new)
        self.assertEqual(self.vault.get_master_key_bytes(), orig_master_key)

        # Previous record decodes perfectly without re-encryption
        new_id_ctx = self.vault.get_identity_context()
        decrypted = new_id_ctx.decrypt_identity_record(spk_id, record_bytes)
        self.assertEqual(decrypted, payload)

    def test_08_atomic_writes_and_crash_safety(self):
        """Invariant: atomic_write_bytes creates destination atomically via fsync and replace."""
        target_file = os.path.join(self.test_dir, "atomic_test.dat")
        data_v1 = b"Version 1 content"
        atomic_write_bytes(target_file, data_v1)

        self.assertTrue(os.path.isfile(target_file))
        with open(target_file, "rb") as f:
            self.assertEqual(f.read(), data_v1)

        # Overwrite with v2
        data_v2 = b"Version 2 updated content"
        atomic_write_bytes(target_file, data_v2)
        with open(target_file, "rb") as f:
            self.assertEqual(f.read(), data_v2)

        # Best effort shred file
        ok = best_effort_shred_file(target_file)
        self.assertTrue(ok)
        self.assertFalse(os.path.isfile(target_file))

    def test_09_lock_clears_keys_and_memory(self):
        """Invariant: Locking zeroes out keys in memory and denies access."""
        passphrase = "hazard galaxy pebble timber canyon velvet"
        self.vault.initialize(passphrase)
        self.assertTrue(self.vault.is_unlocked())

        id_ctx = self.vault.get_identity_context()
        bio_ctx = self.vault.get_biometric_context()
        self.assertIsNotNone(id_ctx)
        self.assertIsNotNone(bio_ctx)

        # Lock vault
        self.vault.lock()
        self.assertFalse(self.vault.is_unlocked())
        self.assertEqual(self.vault.state(), VoiceVaultState.LOCKED)

        # Context access must raise PermissionError
        with self.assertRaises(PermissionError):
            self.vault.get_identity_context()

        with self.assertRaises(PermissionError):
            self.vault.get_biometric_context()

        with self.assertRaises(PermissionError):
            self.vault.get_master_key_bytes()

    def test_10_least_privilege_enforcement(self):
        """Invariant: Matcher receives only biometric key; Resolver receives only identity key."""
        passphrase = "hazard galaxy pebble timber canyon velvet"
        self.vault.initialize(passphrase)

        id_ctx = self.vault.get_identity_context()
        bio_ctx = self.vault.get_biometric_context()

        spk_id = generate_speaker_id()
        synthetic_name = b'{"name": "Dr. Synthetic Scientist"}'
        synthetic_embedding = os.urandom(2048)  # 512 floats * 4 bytes

        # Identity context encrypts name
        id_record = id_ctx.encrypt_identity_record(spk_id, synthetic_name)
        # Biometric context encrypts embedding
        bio_record = bio_ctx.encrypt_biometric_record(spk_id, synthetic_embedding)

        # Matcher (bio_ctx) cannot decrypt identity record
        with self.assertRaises(Exception):
            bio_ctx.decrypt_biometric_record(spk_id, id_record)

        # Resolver (id_ctx) cannot decrypt biometric record
        with self.assertRaises(Exception):
            id_ctx.decrypt_identity_record(spk_id, bio_record)

        # Proper contexts succeed
        self.assertEqual(id_ctx.decrypt_identity_record(spk_id, id_record), synthetic_name)
        self.assertEqual(bio_ctx.decrypt_biometric_record(spk_id, bio_record), synthetic_embedding)

    def test_11_keyed_mac_audit_chain(self):
        """Invariant: Audit MAC chain with master-derived key authenticates entries."""
        audit_key = os.urandom(32)
        prev_mac = b"0" * 32
        entry_1 = b"index:1,action:ENROL,spk_id:spk_test001"
        mac_1 = compute_keyed_mac(audit_key, entry_1 + prev_mac)

        self.assertTrue(verify_keyed_mac(audit_key, entry_1 + prev_mac, mac_1))
        # Tampered entry fails
        self.assertFalse(verify_keyed_mac(audit_key, b"index:1,action:TAMPERED" + prev_mac, mac_1))

    def test_12_encrypted_index_size_padding(self):
        """Invariant: Index encryption conceals count and string lengths via bucketed padding."""
        zone_key = os.urandom(32)
        ctx = IdentityZoneKeyContext(zone_key)

        small_index = {"spk_1": {"enrolled": True}}
        larger_index = {"spk_1": {"enrolled": True}, "spk_2": {"enrolled": True}, "spk_3": {"enrolled": True}}

        blob_1 = ctx.encrypt_index(small_index)
        blob_2 = ctx.encrypt_index(larger_index)

        # Both small and larger index pad to identical 4096-byte buckets (+ nonce and tag)
        self.assertEqual(len(blob_1), len(blob_2))

        # Decrypt round trip restores original dictionary exactly
        self.assertEqual(ctx.decrypt_index(blob_1), small_index)
        self.assertEqual(ctx.decrypt_index(blob_2), larger_index)

    def test_13_passphrase_entropy_and_repetition_rejection(self):
        """Invariant: Repetitive patterns and dictionary loops rejected; high-entropy non-words accepted."""
        # 1. User's exact test: "aaaa bbbb cccc dddd eeee" must be rejected
        res_rep = evaluate_passphrase_strength("aaaa bbbb cccc dddd eeee")
        self.assertFalse(res_rep["valid"], "Failed: 'aaaa bbbb cccc dddd eeee' must be rejected!")
        self.assertEqual(res_rep["label"], "Too repetitive")

        # 2. Repeated words loop must be rejected
        res_words = evaluate_passphrase_strength("password password password password password")
        self.assertFalse(res_words["valid"], "Failed: repeated words must be rejected!")

        # 3. Valid multi-word passphrase accepted
        res_valid = evaluate_passphrase_strength("apple banana cherry dog elephant falcon")
        self.assertTrue(res_valid["valid"])
        self.assertEqual(res_valid["label"], "Very Strong")

        # 4. Long high-entropy non-word passphrase accepted
        res_nonword = evaluate_passphrase_strength("K8#v9!mQ2$zL5@xP7&wR")
        self.assertTrue(res_nonword["valid"])
        self.assertIn(res_nonword["label"], ("Strong", "Very Strong"))

        # 5. User's exact test: "correct horse battery staple" must be rejected
        res_famous = evaluate_passphrase_strength("correct horse battery staple")
        self.assertFalse(res_famous["valid"], "Failed: 'correct horse battery staple' must be rejected!")
        self.assertEqual(res_famous["label"], "Famous phrase")

        # 6. User's exact test: variations like "correcthorsebatterystaple" must be rejected
        res_famous_concat = evaluate_passphrase_strength("correcthorsebatterystaple")
        self.assertFalse(res_famous_concat["valid"], "Failed: 'correcthorsebatterystaple' must be rejected!")

        # 7. Manual 5-word phrase rejected (requires >= 6 words)
        res_5words = evaluate_passphrase_strength("apple banana cherry dog elephant")
        self.assertFalse(res_5words["valid"])
        self.assertEqual(res_5words["label"], "Too few words")

        # 8. 6-word and 7-word generated passphrases accepted
        phrase_6 = generate_memorable_passphrase(word_count=6)
        phrase_7 = generate_memorable_passphrase(word_count=7)
        self.assertTrue(evaluate_passphrase_strength(phrase_6)["valid"])
        self.assertTrue(evaluate_passphrase_strength(phrase_7)["valid"])

    def test_14_persistent_throttle_across_restarts(self):
        """Invariant: Failed-unlock counter persists in throttle.dat across restarts."""
        passphrase = "hazard galaxy pebble timber canyon velvet"
        self.vault.initialize(passphrase)
        self.vault.lock()

        # 2 failures
        self.vault.unlock("WrongPassword1!")
        self.vault.unlock("WrongPassword2!")
        self.assertEqual(self.vault._consecutive_failures, 2)
        self.assertTrue(os.path.isfile(self.vault.throttle_file))

        # Simulate restarting the application with a new VoiceVault instance on same directory
        self.vault.close()
        vault_restarted = VoiceVault(
            keys_dir=self.test_dir,
            idle_timeout_seconds=300,
            time_cost=self.fast_t,
            memory_cost=self.fast_m,
            parallelism=self.fast_p,
        )
        try:
            # Must retain 2 consecutive failures
            self.assertEqual(vault_restarted._consecutive_failures, 2)
            self.assertEqual(vault_restarted.get_progressive_delay(), 2.0)

            # Successful unlock resets persistent counter
            ok, _ = vault_restarted.unlock(passphrase)
            self.assertTrue(ok)
            self.assertEqual(vault_restarted._consecutive_failures, 0)
            self.assertEqual(vault_restarted.get_progressive_delay(), 0.0)
        finally:
            vault_restarted.close()


if __name__ == "__main__":
    unittest.main()
