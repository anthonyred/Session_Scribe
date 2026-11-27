"""
tests/test_event_folder_canary.py

Canary test verifying that personal identities and biometric IDs (spk_...)
never leak into the compute zone (event folder: manifest.json, clip_state.json,
logs, state, reports).

Workflow:
1. Enrolls two canary identities in Vault v2.
2. Selects them into Event Roster (assigns S01, S02).
3. Configures Solo and Panel sessions.
4. Executes Split and Merge session operations.
5. Serializes manifest and state to event folder.
6. Exhaustively scans every single file in the event folder.
7. Asserts 0 occurrences of canary names and 0 occurrences of spk_ IDs.
8. Confirms host, panelists, expected_speaker hold aliases in manifest.json.
"""

import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from scribe_core.voice_vault import VoiceVault
from scribe_core.vault_v2 import VaultV2StorageEngine
from scribe_core.quarantine_manager import QuarantineManager
from scribe_core.event_roster_manager import EventRosterManager
from scribe_core.session_grouper import (
    ClipInfo,
    SessionGrouper,
    SessionInfo,
    merge_sessions,
    split_session,
)
from scribe_core.manifest import EventDetails, ManifestManager
from scribe_core.clip_state import write_clip


class TestEventFolderCanary(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="ss_canary_test_")
        self.vault_dir = os.path.join(self.temp_dir, "vault")
        self.event_dir = os.path.join(self.temp_dir, "Canary_Event_2026")
        os.makedirs(self.vault_dir, exist_ok=True)
        os.makedirs(self.event_dir, exist_ok=True)

        self.vault = VoiceVault(keys_dir=self.vault_dir, time_cost=1, memory_cost=1024, parallelism=2)
        self.vault.initialize("canary velvet timber galaxy pebble hazard")
        self.storage = VaultV2StorageEngine(self.vault)
        self.quarantine_mgr = QuarantineManager(self.storage)

        # Enrol two canary people with distinct unique canary markers
        self.canary_name_1 = "CANARY_ALICE_SECRET_99182"
        self.canary_name_2 = "CANARY_BOB_CONFIDENTIAL_77301"

        self.spk_id_1 = self.storage.create_identity_record(
            full_name=self.canary_name_1,
            roles=["Speaker", "Host"],
            consent_opt_in=True,
        )
        self.spk_id_2 = self.storage.create_identity_record(
            full_name=self.canary_name_2,
            roles=["Speaker", "Panelist"],
            consent_opt_in=True,
        )

        dummy_emb = b"\x00" * 2048
        self.storage.create_biometric_record(self.spk_id_1, dummy_emb)
        self.storage.create_biometric_record(self.spk_id_2, dummy_emb)

    def tearDown(self):
        self.vault.close()
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_full_stage2_canary_isolation_scan(self):
        """
        Verify end-to-end that canary names and spk_ IDs never appear
        in any file within the event directory.
        """
        event_id = "evt_canary_test_8820"
        roster_mgr = EventRosterManager(storage=self.storage)

        # 1. Event Roster selection
        available = roster_mgr.get_available_vault_people()
        self.assertEqual(len(available), 2)

        # Add both to event roster
        alias1 = EventRosterManager.assign_next_alias([])
        alias2 = EventRosterManager.assign_next_alias([alias1])
        self.assertEqual(alias1, "S01")
        self.assertEqual(alias2, "S02")

        roster_items = [
            {"alias": alias1, "spk_id": self.spk_id_1, "name": self.canary_name_1, "role": "Host"},
            {"alias": alias2, "spk_id": self.spk_id_2, "name": self.canary_name_2, "role": "Panelist"},
        ]
        roster_mgr.save_event_roster(event_id, roster_items)

        event_roster = roster_mgr.load_event_roster(event_id)
        self.assertEqual(len(event_roster), 2)

        # 2. Build initial sessions
        now = datetime(2026, 11, 19, 10, 0, 0)
        c1 = ClipInfo("clip_01.mp4", os.path.join(self.event_dir, "clip_01.mp4"), now, 100.0, now + timedelta(seconds=100))
        c2 = ClipInfo("clip_02.mp4", os.path.join(self.event_dir, "clip_02.mp4"), now + timedelta(seconds=110), 120.0, now + timedelta(seconds=230))
        c3 = ClipInfo("clip_03.mp4", os.path.join(self.event_dir, "clip_03.mp4"), now + timedelta(seconds=240), 90.0, now + timedelta(seconds=330))

        s1 = SessionInfo(session_id=1, speaker_name="S01", clips=[c1, c2])
        s2 = SessionInfo(session_id=2, speaker_name="Panel Session 2", clips=[c3])
        sessions = [s1, s2]

        # 3. Configure Session 1 as Solo (expected_speaker = S01)
        s1.session_type = "solo"
        s1.expected_speaker = {"alias": "S01", "role": "Speaker"}
        s1.sync_roster_fields()

        # Configure Session 2 as Panel (host = S01, panelist = S02)
        s2.session_type = "panel"
        s2.host = {"alias": "S01", "role": "Host"}
        s2.panelists = [{"alias": "S02", "role": "Panelist"}]
        s2.sync_roster_fields()

        # 4. Perform Split on Session 1 at clip index 1 (c2 moves to new session)
        split_sessions = split_session(sessions, session_id=1, split_at_clip_index=1)
        self.assertEqual(len(split_sessions), 3)

        # 5. Perform Merge of Session 2 and the newly split session (Session 3)
        merged_sessions = merge_sessions(split_sessions, session_ids=[2, 3], target_session_type="panel")
        self.assertEqual(len(merged_sessions), 2)

        # Ensure merged session carries panel configuration
        merged_panel = [s for s in merged_sessions if s.session_id == 2][0]
        merged_panel.host = {"alias": "S01", "role": "Host"}
        merged_panel.panelists = [{"alias": "S02", "role": "Panelist"}]
        merged_panel.sync_roster_fields()

        # 6. Save Manifest to event folder
        scribe_dir = os.path.join(self.event_dir, "scribe")
        os.makedirs(scribe_dir, exist_ok=True)

        event_details = EventDetails(
            event_name="Canary Isolation Conference",
            date="2026-11-19",
            organiser="Canary Testing Bureau",
            venue="Vault Secure Room",
            event_id=event_id,
        )

        manifest_path, warnings = ManifestManager.save_manifest(
            scribe_dir=scribe_dir,
            event=event_details,
            sessions=merged_sessions,
            unassigned_photos=[],
            settings_used={"test": True},
            event_root=self.event_dir,
        )

        # 7. Save clip state
        state_path = os.path.join(scribe_dir, "state", "clip_state.json")
        write_clip(state_path, "clip_01", model="medium", transcribed_at="2026-11-19 10:00:00")
        write_clip(state_path, "clip_02", model="medium", transcribed_at="2026-11-19 10:05:00")
        write_clip(state_path, "clip_03", model="medium", transcribed_at="2026-11-19 10:10:00")

        # Also write a mock log and state file in the event folder
        with open(os.path.join(scribe_dir, "state", "test_run.log"), "w", encoding="utf-8") as f:
            f.write("2026-11-19 INFO Ingested clips for S01 and S02 successfully.\n")

        # 8. Exhaustive Canary Scan of the entire event folder
        forbidden_strings = [
            self.canary_name_1,
            self.canary_name_2,
            self.spk_id_1,
            self.spk_id_2,
            "spk_",
        ]

        scanned_files = 0
        for root_dir, _, filenames in os.walk(self.event_dir):
            for fname in filenames:
                fpath = os.path.join(root_dir, fname)
                scanned_files += 1
                with open(fpath, "rb") as fh:
                    content = fh.read()
                    for forbidden in forbidden_strings:
                        forbidden_bytes = forbidden.encode("utf-8")
                        self.assertNotIn(
                            forbidden_bytes,
                            content,
                            f"LEAK DETECTED in file '{fpath}': found forbidden string '{forbidden}'",
                        )

        self.assertGreater(scanned_files, 2, "Expected at least manifest.json and clip_state.json to be scanned")

        # 9. Verify that manifest.json fields strictly hold aliases
        with open(manifest_path, "r", encoding="utf-8") as f:
            manifest_json = json.load(f)

        for s in manifest_json.get("sessions", []):
            stype = s.get("session_type")
            if stype == "solo":
                expected = s.get("expected_speaker")
                self.assertIsNotNone(expected)
                self.assertIn("alias", expected)
                self.assertEqual(expected["alias"], "S01")
                self.assertNotIn("name", expected)
                self.assertNotIn("spk_id", expected)
            elif stype == "panel":
                host = s.get("host")
                self.assertIsNotNone(host)
                self.assertIn("alias", host)
                self.assertEqual(host["alias"], "S01")
                self.assertNotIn("name", host)
                self.assertNotIn("spk_id", host)

                panelists = s.get("panelists", [])
                self.assertTrue(len(panelists) >= 1)
                for p in panelists:
                    self.assertIn("alias", p)
                    self.assertNotIn("name", p)
                    self.assertNotIn("spk_id", p)

            # Roster array must hold aliases only
            for r in s.get("roster", []):
                self.assertIn("alias", r)
                self.assertNotIn("name", r)
                self.assertNotIn("spk_id", r)


if __name__ == "__main__":
    unittest.main()
