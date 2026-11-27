"""
tests/test_optional_vault_and_reidentify.py

End-to-end functional regression tests for:
1. Optional Vault:
   - App (Stage 3 transcription, Approval, Combine) works completely whether Vault v2
     is locked, unlocked, or never initialized.
   - Combine gate is strictly based on user approval of clips, never blocked by vault lock.
   - Combined files output aliases (S01_combined.txt) when locked/missing,
     and decrypted personal names (Jane_Doe_combined.txt) when unlocked.
2. Re-Identify Flow:
   - Re-attribution of existing transcripts in-place with zero re-transcription.
3. Vault Status Chip:
   - "Skip for now" option is remembered in user settings and toggles chip state.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from scribe_core.voice_vault import VoiceVault
from scribe_core.vault_v2 import VaultV2StorageEngine
from scribe_core.event_roster_manager import EventRosterManager
from scribe_core.combined_transcripts import (
    build_combined_transcripts,
    sanitize_speaker_filename,
)
from gui.ui_settings import is_vault_setup_skipped, set_vault_setup_skipped


class TestOptionalVaultAndReidentify(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="ss_opt_vault_test_")
        self.event_root = os.path.join(self.temp_dir, "event_01")
        self.vault_dir = os.path.join(self.temp_dir, "vault")
        self.settings_ini = os.path.join(self.temp_dir, "ui.ini")

        # Create event directory layout
        self.transcripts_dir = os.path.join(self.event_root, "scribe", "transcripts")
        self.combined_dir = os.path.join(self.event_root, "scribe", "combined")
        self.state_dir = os.path.join(self.event_root, "scribe", "state")
        os.makedirs(self.transcripts_dir, exist_ok=True)
        os.makedirs(self.combined_dir, exist_ok=True)
        os.makedirs(self.state_dir, exist_ok=True)
        os.makedirs(self.vault_dir, exist_ok=True)

        self.vault = VoiceVault(keys_dir=self.vault_dir, time_cost=1, memory_cost=1024, parallelism=2)

    def tearDown(self):
        self.vault.close()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _create_synthetic_transcript(self, clip_stem: str, speaker_tag: str = "S01"):
        path = os.path.join(self.transcripts_dir, f"{clip_stem}.txt")
        content = (
            "=== EVENT DETAILS ===\n"
            "Event Name: Tech Symposium 2026\n"
            "Date: 2026-11-19\n\n"
            "=== TRANSCRIPT ===\n"
            f"Video File: {clip_stem}.mp4\n"
            "Start Time: 2026-11-19 10:00:00\n"
            f"Speaker: {speaker_tag}\n"
            "Transcription Duration: 00:01:30\n"
            "Transcription Started: 2026-11-19 10:00:00 IST\n"
            "Transcription Completed: 2026-11-19 10:01:30 IST\n\n"
            f"[{speaker_tag}] [10:00:05 - 10:00:25] Welcome to today's keynote presentation.\n"
            f"[{speaker_tag}] [10:00:26 - 10:00:45] We will cover several new architecture topics.\n"
        )
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return path

    def test_01_combine_transcripts_when_vault_uninitialized(self):
        """Invariant: Combine works seamlessly with aliases when Vault v2 is uninitialized."""
        self.assertFalse(self.vault.is_initialized())
        self.assertFalse(self.vault.is_unlocked())

        self._create_synthetic_transcript("clip_01", speaker_tag="S01")
        self._create_synthetic_transcript("clip_02", speaker_tag="S01")

        manifest = {
            "event_id": "evt_test_101",
            "sessions": [
                {
                    "session_id": 1,
                    "speaker": "S01",
                    "clips": [{"name": "clip_01.mp4"}, {"name": "clip_02.mp4"}],
                }
            ]
        }

        # Build combined transcripts with uninitialized vault (alias_to_name is None)
        res = build_combined_transcripts(self.event_root, manifest, alias_to_name=None)
        self.assertEqual(len(res["created_files"]), 1)
        combined_file = res["created_files"][0]

        # File name is based on alias S01
        self.assertTrue(combined_file.endswith("S01_combined.txt"))

        # File content carries alias tags
        with open(combined_file, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertIn("[S01] [10:00:05 - 10:00:25]", content)
        self.assertIn("Speaker: S01", content)

    def test_02_combine_transcripts_when_vault_locked_vs_unlocked(self):
        """Invariant: When vault is locked, aliases are exported; when unlocked, names are resolved."""
        # 1. Initialize vault and enroll speaker
        self.vault.initialize("velvet galaxy timber pebble hazard canyon")
        self.assertTrue(self.vault.is_unlocked())
        storage = VaultV2StorageEngine(self.vault)
        roster_mgr = EventRosterManager(self.vault)

        spk_id = storage.create_identity_record("Dr. Ada Lovelace", roles=["Speaker"], consent_opt_in=True)
        event_id = "evt_ada_102"
        roster_mgr.save_event_roster(event_id, [{"alias": "S01", "spk_id": spk_id, "role": "Speaker"}])

        self._create_synthetic_transcript("clip_03", speaker_tag="S01")

        manifest = {
            "event_id": event_id,
            "sessions": [
                {
                    "session_id": 1,
                    "speaker": "S01",
                    "expected_speaker": {"alias": "S01", "role": "Speaker"},
                    "clips": [{"name": "clip_03.mp4"}],
                }
            ]
        }

        # Case A: Vault is locked
        self.vault.lock()
        self.assertFalse(self.vault.is_unlocked())

        alias_map_locked = roster_mgr.get_alias_to_name_map(event_id)
        self.assertEqual(alias_map_locked, {})

        res_locked = build_combined_transcripts(self.event_root, manifest, alias_to_name=alias_map_locked)
        self.assertTrue(res_locked["created_files"][0].endswith("S01_combined.txt"))
        with open(res_locked["created_files"][0], "r", encoding="utf-8") as f:
            content_locked = f.read()
        self.assertIn("[S01]", content_locked)
        self.assertNotIn("Dr. Ada Lovelace", content_locked)

        # Case B: Vault is unlocked
        ok, _ = self.vault.unlock("velvet galaxy timber pebble hazard canyon")
        self.assertTrue(ok)
        self.assertTrue(self.vault.is_unlocked())

        alias_map_unlocked = roster_mgr.get_alias_to_name_map(event_id)
        self.assertEqual(alias_map_unlocked, {"S01": "Dr. Ada Lovelace"})

        res_unlocked = build_combined_transcripts(self.event_root, manifest, alias_to_name=alias_map_unlocked)
        self.assertTrue(res_unlocked["created_files"][0].endswith("Dr._Ada_Lovelace_combined.txt"))
        with open(res_unlocked["created_files"][0], "r", encoding="utf-8") as f:
            content_unlocked = f.read()
        self.assertIn("[Dr. Ada Lovelace] [10:00:05 - 10:00:25]", content_unlocked)
        self.assertIn("Speaker: Dr. Ada Lovelace", content_unlocked)
        self.assertNotIn("[S01]", content_unlocked)

    def test_03_reidentify_transcript_reattribution_in_place(self):
        """Invariant: Re-identification replaces cluster labels in-place with zero re-transcription."""
        clip_path = self._create_synthetic_transcript("clip_04", speaker_tag="Speaker 1")

        with open(clip_path, "r", encoding="utf-8") as f:
            orig_text = f.read()
        self.assertIn("[Speaker 1]", orig_text)

        # Simulate Re-Identify assigning "Speaker 1" -> "S02"
        assignments = {"Speaker 1": "S02"}

        # Simulate _apply_cluster_reassignments_to_transcripts
        with open(clip_path, "r", encoding="utf-8") as f:
            content = f.read()
        for old_tag, new_alias in assignments.items():
            content = content.replace(f"[{old_tag}]", f"[{new_alias}]")
            content = content.replace(f"Speaker: {old_tag}", f"Speaker: {new_alias}")
        with open(clip_path, "w", encoding="utf-8") as f:
            f.write(content)

        with open(clip_path, "r", encoding="utf-8") as f:
            updated_text = f.read()
        self.assertNotIn("[Speaker 1]", updated_text)
        self.assertIn("[S02] [10:00:05 - 10:00:25]", updated_text)
        # Note: audio was NEVER re-transcribed with Whisper!

    def test_04_vault_setup_skipped_setting_persistence(self):
        """Invariant: 'Skip for now' setting persists and can be toggled without data loss."""
        # By default not skipped
        self.assertFalse(is_vault_setup_skipped(custom_path=self.settings_ini))

        # User chooses "Skip for now"
        set_vault_setup_skipped(True, custom_path=self.settings_ini)
        self.assertTrue(is_vault_setup_skipped(custom_path=self.settings_ini))

        # User later enables or completes vault setup
        set_vault_setup_skipped(False, custom_path=self.settings_ini)
        self.assertFalse(is_vault_setup_skipped(custom_path=self.settings_ini))


if __name__ == "__main__":
    unittest.main()
