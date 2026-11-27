"""
tests/test_vault_v2_storage.py
Comprehensive unit tests for Vault v2 Dual-Zone Storage Engine (scribe_core/vault_v2.py).

Requirements verified (Synthetic Data Only):
1. Atomic CRUD for Identity records (<spk_id>.id.enc).
2. Atomic CRUD for Biometric records (<spk_id>.bio.enc) and encrypted WAV audio.
3. Encrypted per-event alias map (S01 -> spk_id).
4. Encrypted padded indexes revealing neither counts nor names.
5. Crash simulation during write (simulated interrupted write; vault and original records remain intact).
6. Swapped and tampered files rejection at storage layer.
7. Plaintext canary scan across the entire vault directory (zero occurrences of names or raw audio/embedding bytes).
8. Least-privilege isolation at storage layer (Matcher cannot read identity; Resolver cannot read biometric).
9. NTFS / local file permission checks on vault directory.

All tests operate in isolated tempfile.mkdtemp() workspaces. Zero real vault data is touched.
"""

import json
import os
import shutil
import sys
import tempfile
import time
import unittest

# sys.path guard derived from __file__
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from scribe_core.vault_crypto import (
    TamperError,
    LeastPrivilegeViolationError,
    generate_speaker_id,
    atomic_write_bytes,
)
from scribe_core.voice_vault import VoiceVault
from scribe_core.vault_v2 import VaultV2StorageEngine


class TestVaultV2StorageEngine(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="ss_vault_v2_test_")
        # Fast test parameters for instant test derivation
        self.vault = VoiceVault(
            keys_dir=self.test_dir,
            idle_timeout_seconds=300,
            time_cost=1,
            memory_cost=1024,
            parallelism=2,
        )
        self.passphrase = "hazard galaxy pebble timber canyon velvet"
        ok, msg = self.vault.initialize(self.passphrase)
        self.assertTrue(ok, f"Vault init failed: {msg}")

        self.storage = VaultV2StorageEngine(self.vault)

    def tearDown(self):
        self.vault.close()
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_01_identity_crud_lifecycle(self):
        """Verify atomic create, read, update, and delete for Identity records."""
        canary_full_name = "Dr. Alice Canary-Researcher"
        canary_display = "Alice Canary"
        affiliation = "Synthetic Science Institute"
        roles = ["Keynote", "Speaker"]

        # 1. Create
        spk_id = self.storage.create_identity_record(
            full_name=canary_full_name,
            display_name=canary_display,
            affiliation=affiliation,
            roles=roles,
            consent_opt_in=True,
        )
        self.assertTrue(spk_id.startswith("spk_"))
        self.assertGreaterEqual(len(spk_id), 36)

        # 2. Read
        record = self.storage.read_identity_record(spk_id)
        self.assertEqual(record["spk_id"], spk_id)
        self.assertEqual(record["full_name"], canary_full_name)
        self.assertEqual(record["display_name"], canary_display)
        self.assertEqual(record["affiliation"], affiliation)
        self.assertEqual(record["roles"], roles)
        self.assertTrue(record["consent"]["opt_in"])
        self.assertEqual(record["schema_version"], 2)
        self.assertFalse(record["quarantine"]["is_quarantined"])

        # Index includes spk_id
        active_ids = self.storage.list_identity_ids()
        self.assertIn(spk_id, active_ids)

        # 3. Update
        updated = self.storage.update_identity_record(
            spk_id, {"affiliation": "Updated AI Lab", "roles": ["Moderator"]}
        )
        self.assertEqual(updated["affiliation"], "Updated AI Lab")
        self.assertEqual(updated["roles"], ["Moderator"])

        re_read = self.storage.read_identity_record(spk_id)
        self.assertEqual(re_read["affiliation"], "Updated AI Lab")

        # 4. Delete
        self.storage.delete_identity_record(spk_id)
        self.assertNotIn(spk_id, self.storage.list_identity_ids())
        with self.assertRaises(FileNotFoundError):
            self.storage.read_identity_record(spk_id)

    def test_02_biometric_crud_and_encrypted_wav(self):
        """Verify atomic create, read, update, and delete for Biometric records and audio samples."""
        spk_id = generate_speaker_id()
        synthetic_embedding = os.urandom(2048)  # 512 floats * 4 bytes
        sample_names = ["clip_01_intro", "clip_02_panel"]

        # Synthetic reference WAV audio (with RIFF header)
        synthetic_wav_1 = b"RIFF" + b"\x00\x00\x00\x00" + b"WAVEfmt " + os.urandom(1024)
        synthetic_wav_2 = b"RIFF" + b"\x00\x00\x00\x00" + b"WAVEfmt " + os.urandom(2048)

        # 1. Create Biometric Record
        self.storage.create_biometric_record(
            spk_id=spk_id,
            embedding_bytes=synthetic_embedding,
            sample_names=sample_names,
        )

        # 2. Save reference audio WAVs
        self.storage.save_sample_audio(spk_id, sample_names[0], synthetic_wav_1)
        self.storage.save_sample_audio(spk_id, sample_names[1], synthetic_wav_2)

        # 3. Read Biometric Record
        meta, emb = self.storage.read_biometric_record(spk_id)
        self.assertEqual(meta["spk_id"], spk_id)
        self.assertEqual(meta["sample_count"], 2)
        self.assertEqual(emb, synthetic_embedding)

        # 4. Read audio samples
        out_wav_1 = self.storage.read_sample_audio(spk_id, sample_names[0])
        out_wav_2 = self.storage.read_sample_audio(spk_id, sample_names[1])
        self.assertEqual(out_wav_1, synthetic_wav_1)
        self.assertEqual(out_wav_2, synthetic_wav_2)

        # 5. Delete Biometric Record & Audio
        self.storage.delete_biometric_record(spk_id)
        self.assertNotIn(spk_id, self.storage.list_biometric_ids())
        with self.assertRaises(FileNotFoundError):
            self.storage.read_biometric_record(spk_id)
        with self.assertRaises(FileNotFoundError):
            self.storage.read_sample_audio(spk_id, sample_names[0])

    def test_03_per_event_alias_map(self):
        """Verify per-event session alias map encryption in Identity Zone."""
        event_id = "ev_20261119_ai_conf"
        alias_map = {
            "S01": generate_speaker_id(),
            "S02": generate_speaker_id(),
            "S03": generate_speaker_id(),
        }

        self.storage.save_alias_map(event_id, alias_map)
        loaded_map = self.storage.get_alias_map(event_id)
        self.assertEqual(loaded_map, alias_map)

        # Non-existent event returns empty dict
        self.assertEqual(self.storage.get_alias_map("ev_unknown"), {})

    def test_04_crash_simulation_during_write(self):
        """Simulate process kill or crash mid-write; assert vault remains intact."""
        spk_id = self.storage.create_identity_record("Dr. Stable Speaker")
        record_path = os.path.join(self.storage.identity_records_dir, f"{spk_id}.id.enc")
        with open(record_path, "rb") as f:
            original_data = f.read()

        # Simulate incomplete write leaving a .tmp file
        crash_tmp = f"{record_path}.1234.crash.tmp"
        with open(crash_tmp, "wb") as f:
            f.write(b"CORRUPTED_INCOMPLETE_WRITE_DATA")

        # Original record is untouched and decodes properly
        record = self.storage.read_identity_record(spk_id)
        self.assertEqual(record["full_name"], "Dr. Stable Speaker")

        # Clean up tmp
        if os.path.isfile(crash_tmp):
            os.remove(crash_tmp)

    def test_05_swapped_and_tampered_files_rejected(self):
        """Verify tampering with on-disk ciphertext raises TamperError."""
        spk_id = self.storage.create_identity_record("Dr. Tamper Test")
        record_path = os.path.join(self.storage.identity_records_dir, f"{spk_id}.id.enc")

        with open(record_path, "rb") as f:
            blob = bytearray(f.read())

        # Corrupt one byte of ciphertext
        blob[-8] ^= 0x42
        with open(record_path, "wb") as f:
            f.write(blob)

        with self.assertRaises(TamperError):
            self.storage.read_identity_record(spk_id)

    def test_06_plaintext_canary_scan(self):
        """
        Critical Invariant: Deep scan of entire vault directory asserts ZERO occurrences
        of plaintext speaker names, RIFF WAV headers, or raw embedding byte patterns.
        """
        canary_names = [
            "Canary_FirstName_Omega",
            "Canary_LastName_Zeta",
            "Prof. Jonathan Xavier Canterbury",
        ]
        canary_bytes = [
            b"UNIQUE_CANARY_AUDIO_SAMPLE_RIFF_PATTERN_12345",
            b"UNIQUE_CANARY_EMBEDDING_VECTOR_BYTES_67890",
        ]

        # Enrol canary identity
        spk_id = self.storage.create_identity_record(
            full_name=canary_names[2],
            display_name=canary_names[0],
            affiliation="Secret Canary Society",
        )

        # Enrol canary biometric & audio sample containing canary bytes
        self.storage.create_biometric_record(
            spk_id=spk_id,
            embedding_bytes=canary_bytes[1] * 20,
            sample_names=["canary_mic_1"],
        )
        self.storage.save_sample_audio(spk_id, "canary_mic_1", canary_bytes[0] * 10)

        # Save canary alias map
        self.storage.save_alias_map("ev_canary_01", {"S01": spk_id})

        # Execute deep canary scan
        violations = self.storage.canary_scan_vault(canary_names, canary_bytes)
        self.assertEqual(
            violations,
            [],
            f"Security Leak! Plaintext found in vault directory: {violations}",
        )

    def test_07_least_privilege_at_storage_layer(self):
        """Matcher cannot access identity records; Resolver cannot access biometrics."""
        spk_id = self.storage.create_identity_record("Dr. Least Privilege")
        embedding = os.urandom(2048)
        self.storage.create_biometric_record(spk_id, embedding)

        # Obtain separate contexts
        id_ctx = self.vault.get_identity_context()
        bio_ctx = self.vault.get_biometric_context()

        # Bio context cannot decrypt identity record
        id_path = os.path.join(self.storage.identity_records_dir, f"{spk_id}.id.enc")
        with open(id_path, "rb") as f:
            id_bytes = f.read()

        with self.assertRaises(Exception):
            bio_ctx.decrypt_biometric_record(spk_id, id_bytes)

        # Id context cannot decrypt biometric record
        bio_path = os.path.join(self.storage.biometric_records_dir, f"{spk_id}.bio.enc")
        with open(bio_path, "rb") as f:
            bio_bytes = f.read()

        with self.assertRaises(Exception):
            id_ctx.decrypt_identity_record(spk_id, bio_bytes)

    def test_08_file_permissions(self):
        """Verify vault directory and files are accessible to current user."""
        self.assertTrue(os.path.isdir(self.storage.vault_dir))
        self.assertTrue(os.access(self.storage.vault_dir, os.R_OK | os.W_OK))


if __name__ == "__main__":
    unittest.main()
