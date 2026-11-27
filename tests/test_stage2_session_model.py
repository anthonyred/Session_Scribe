"""
tests/test_stage2_session_model.py
Unit test suite for Phase V5 Session Model in Stage 2.

Invariants Tested:
1. Session Model: Solo (1 expected speaker) vs Panel (1 host + 1 or more panelists).
2. Event Roster Selection:
   - Enrolled profiles picked from Vault v2 into per-event roster.
   - Quarantined profiles NEVER appear in roster selection.
   - Real names visible ONLY while Vault v2 is unlocked; while locked, aliases/masked tokens appear.
3. Persistent Identity Invariant: One person = one persistent spk_ ID across all sessions and events.
4. Per-event aliases (S01, S02, ...) stored in Vault v2 Identity Zone alias map keyed by random event_id (evt_...).
5. Compute-Zone Isolation: manifest.json carries aliases only, NEVER spk_ IDs or real personal names.
6. Manual Merge & Split of sessions:
   - merge_sessions combines clips and photos, recalculates bounds, preserves primary ID.
   - split_session splits at clip boundary, partitions clips and photos, generates new session ID.
7. Multi-Speaker Alignment: Removal of order-of-appearance assignment (roster[idx]) in multi_speaker.py.
8. UI Dialog Stability: EventRosterDialog and SessionConfigDialog under light/dark themes and resize.
"""

from __future__ import annotations

import io
import json
import os
import shutil
import sys
import tempfile
import time
import tkinter as tk
import unittest
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Any

import numpy as np
import soundfile as sf

# sys.path guard derived from __file__
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from gui import theme
from gui.event_roster_dialog import EventRosterDialog
from gui.session_config_dialog import SessionConfigDialog
from scribe_core.event_roster_manager import EventRosterManager, generate_event_id
from scribe_core.manifest import ManifestManager, EventDetails
from scribe_core.multi_speaker import assign_diarization_to_words
from scribe_core.session_grouper import (
    SessionInfo,
    ClipInfo,
    PhotoInfo,
    SessionGrouper,
    merge_sessions,
    split_session,
)
from scribe_core.vault_v2 import VaultV2StorageEngine
from scribe_core.voice_vault import VoiceVault


def create_dummy_clip(filename: str, start_sec: float, duration_sec: float) -> ClipInfo:
    base_time = datetime(2026, 9, 5, 10, 0, 0)
    st = base_time + timedelta(seconds=start_sec)
    et = st + timedelta(seconds=duration_sec)
    return ClipInfo(
        filename=filename,
        filepath=f"D:/Events/TestEvent/data/Videos/{filename}",
        start_time=st,
        duration=duration_sec,
        end_time=et,
    )


def create_dummy_photo(filename: str, offset_sec: float) -> PhotoInfo:
    base_time = datetime(2026, 9, 5, 10, 0, 0)
    ts = base_time + timedelta(seconds=offset_sec)
    return PhotoInfo(
        filename=filename,
        filepath=f"D:/Events/TestEvent/data/Photos/{filename}",
        timestamp=ts,
    )


class TestStage2SessionModel(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            cls.root = tk.Tk()
            cls.root.withdraw()
            theme.apply_global_theme(cls.root, theme_mode="dark")
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
        self.temp_dir = tempfile.mkdtemp(prefix="ss_test_v5_")
        self.vault = VoiceVault(
            keys_dir=self.temp_dir,
            time_cost=1,
            memory_cost=1024,
            parallelism=2,
        )
        self.passphrase = "whisper canyon timber hazard galaxy velvet"
        self.vault.initialize(self.passphrase)
        self.storage = VaultV2StorageEngine(self.vault)
        self.roster_mgr = EventRosterManager(self.storage)

        # Enroll two synthetic profiles into Vault v2
        # Profile 1: Dr. Katherine Synthetic
        self.spk1 = "spk_1111222233334444"
        self.storage.create_identity_record(
            spk_id=self.spk1,
            full_name="Dr. Katherine Synthetic",
            display_name="Dr. Katherine Synthetic",
            roles=["Speaker", "Panelist"],
            consent_opt_in=True,
            notes="Keynote on Voice Privacy",
        )
        self.storage.create_biometric_record(
            spk_id=self.spk1,
            embedding_bytes=np.ones(256, dtype=np.float32).tobytes(),
            sample_names=["sample_1"],
            model_version="1.0.0",
            model_revision="837717ddb9ff5507820346191109dc79c958d614",
        )

        # Profile 2: Prof. Alan Turing
        self.spk2 = "spk_5555666677778888"
        self.storage.create_identity_record(
            spk_id=self.spk2,
            full_name="Prof. Alan Turing",
            display_name="Prof. Alan Turing",
            roles=["Host", "Speaker"],
            consent_opt_in=True,
            notes="Session Host",
        )
        self.storage.create_biometric_record(
            spk_id=self.spk2,
            embedding_bytes=(np.ones(256, dtype=np.float32) * 0.5).tobytes(),
            sample_names=["sample_1"],
            model_version="1.0.0",
            model_revision="837717ddb9ff5507820346191109dc79c958d614",
        )

        # Profile 3: Quarantined Profile (should NEVER appear in roster selection)
        self.spk3_quarantined = "spk_9999aaaabbbbcccc"
        self.storage.create_identity_record(
            spk_id=self.spk3_quarantined,
            full_name="Quarantined Impostor",
            display_name="Quarantined Impostor",
            roles=["Guest"],
            consent_opt_in=False,
        )
        self.storage.create_biometric_record(
            spk_id=self.spk3_quarantined,
            embedding_bytes=np.zeros(256, dtype=np.float32).tobytes(),
            sample_names=["bad_sample"],
        )
        self.storage.update_biometric_record(
            self.spk3_quarantined,
            {"quarantine": {"is_quarantined": True, "reason": "Unconsented sample"}},
        )
        self.storage.update_identity_record(
            self.spk3_quarantined,
            {"quarantine": {"is_quarantined": True, "reason": "Unconsented sample"}},
        )

    def tearDown(self):
        self.vault.close()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_01_session_model_solo_structure_and_serialization(self):
        """Invariant: Solo session has exactly 1 expected speaker; compute-zone dict carries alias only."""
        sess = SessionInfo(
            session_id=1,
            speaker_name="Solo Presentation",
            clips=[create_dummy_clip("clip_01.mp4", 0.0, 300.0)],
            session_type="solo",
            expected_speaker={
                "alias": "S01",
                "role": "Speaker",
                "name": "Dr. Katherine Synthetic",
                "spk_id": self.spk1,
            },
        )
        sess.calculate_bounds()
        sess.sync_roster_fields()

        self.assertEqual(sess.session_type, "solo")
        self.assertEqual(sess.speaker_mode, "single")
        self.assertEqual(len(sess.roster), 1)
        self.assertEqual(sess.roster[0]["alias"], "S01")

        # In-memory dict with full metadata (unlocked)
        full_dict = sess.to_dict(compute_zone_safe=False)
        self.assertEqual(full_dict["expected_speaker"]["name"], "Dr. Katherine Synthetic")
        self.assertEqual(full_dict["expected_speaker"]["spk_id"], self.spk1)

        # Compute-zone serialized dict (for manifest.json)
        safe_dict = sess.to_dict(compute_zone_safe=True)
        self.assertEqual(safe_dict["speaker"], "S01")
        self.assertEqual(safe_dict["session_type"], "solo")
        self.assertEqual(safe_dict["expected_speaker"]["alias"], "S01")
        self.assertEqual(safe_dict["expected_speaker"]["role"], "Speaker")
        # Invariant: Zero plaintext names or spk_ IDs in compute-zone dict!
        self.assertNotIn("name", safe_dict["expected_speaker"])
        self.assertNotIn("spk_id", safe_dict["expected_speaker"])
        self.assertNotIn("name", safe_dict["roster"][0])
        self.assertNotIn("spk_id", safe_dict["roster"][0])

    def test_02_session_model_panel_structure_and_serialization(self):
        """Invariant: Panel session has 1 host + panelists; compute-zone dict carries aliases only."""
        sess = SessionInfo(
            session_id=2,
            speaker_name="Panel Discussion",
            clips=[create_dummy_clip("clip_02.mp4", 360.0, 600.0)],
            session_type="panel",
            host={"alias": "S02", "role": "Host", "name": "Prof. Alan Turing", "spk_id": self.spk2},
            panelists=[
                {"alias": "S01", "role": "Panelist", "name": "Dr. Katherine Synthetic", "spk_id": self.spk1}
            ],
        )
        sess.calculate_bounds()
        sess.sync_roster_fields()

        self.assertEqual(sess.session_type, "panel")
        self.assertEqual(sess.speaker_mode, "panel")
        self.assertEqual(len(sess.roster), 2)
        self.assertEqual(sess.roster[0]["alias"], "S02")
        self.assertEqual(sess.roster[1]["alias"], "S01")

        safe_dict = sess.to_dict(compute_zone_safe=True)
        self.assertEqual(safe_dict["session_type"], "panel")
        self.assertEqual(safe_dict["host"]["alias"], "S02")
        self.assertEqual(safe_dict["host"]["role"], "Host")
        self.assertEqual(len(safe_dict["panelists"]), 1)
        self.assertEqual(safe_dict["panelists"][0]["alias"], "S01")
        self.assertEqual(safe_dict["panelists"][0]["role"], "Panelist")
        # Zero plaintext names or spk_ IDs!
        self.assertNotIn("name", safe_dict["host"])
        self.assertNotIn("spk_id", safe_dict["host"])
        self.assertNotIn("name", safe_dict["panelists"][0])
        self.assertNotIn("spk_id", safe_dict["panelists"][0])

    def test_03_quarantined_profiles_never_appear_in_roster_selection(self):
        """Invariant: Quarantined profiles are strictly excluded from event roster selection."""
        available = self.roster_mgr.get_available_vault_people()
        available_ids = [p["spk_id"] for p in available]

        self.assertIn(self.spk1, available_ids)
        self.assertIn(self.spk2, available_ids)
        # Quarantined profile must NEVER be present!
        self.assertNotIn(self.spk3_quarantined, available_ids)

    def test_04_vault_locked_vs_unlocked_names_visibility(self):
        """Invariant: Real names visible ONLY while unlocked; while locked, aliases/masked labels appear."""
        event_id = generate_event_id()
        self.roster_mgr.save_event_roster(
            event_id,
            [
                {"alias": "S01", "spk_id": self.spk1},
                {"alias": "S02", "spk_id": self.spk2},
            ],
        )

        # 1. While unlocked: Real names resolved from Zone 1 Identity
        unlocked_roster = self.roster_mgr.load_event_roster(event_id)
        self.assertEqual(len(unlocked_roster), 2)
        self.assertEqual(unlocked_roster[0]["name"], "Dr. Katherine Synthetic")
        self.assertEqual(unlocked_roster[1]["name"], "Prof. Alan Turing")

        # 2. Lock the vault
        self.vault.lock()
        self.assertFalse(self.vault.is_unlocked())

        # Available vault profiles list returns empty while locked (gated)
        locked_people = self.roster_mgr.get_available_vault_people()
        self.assertEqual(len(locked_people), 0)

        # Event roster cannot decrypt vault alias map while locked
        locked_roster = self.roster_mgr.load_event_roster(event_id)
        self.assertEqual(len(locked_roster), 0)

        # In compute zone (manifest.json), sessions carry aliases only
        sess = SessionInfo(
            session_id=1,
            speaker_name="Opening Session",
            session_type="solo",
            expected_speaker={"alias": "S01", "role": "Speaker"},
        )
        safe_dict = sess.to_dict(compute_zone_safe=True)
        self.assertEqual(safe_dict["speaker"], "S01")
        self.assertNotIn("Katherine", str(safe_dict))

    def test_05_persistent_identity_invariant_one_person_one_spk_id(self):
        """
        Invariant: One person = one persistent spk_ ID across multiple events and sessions.
        Even if assigned different per-event aliases (S01 in Event A, S03 in Event B).
        """
        event_A = "evt_aaaaaaaaaaaaaaaa"
        event_B = "evt_bbbbbbbbbbbbbbbb"

        # Event A: Katherine is S01
        self.roster_mgr.save_event_roster(event_A, [{"alias": "S01", "spk_id": self.spk1}])

        # Event B: Katherine is S03 (different alias in different event)
        self.roster_mgr.save_event_roster(
            event_B,
            [
                {"alias": "S01", "spk_id": self.spk2},
                {"alias": "S03", "spk_id": self.spk1},
            ],
        )

        map_A = self.storage.get_alias_map(event_A)
        map_B = self.storage.get_alias_map(event_B)

        self.assertEqual(map_A["S01"], self.spk1)
        self.assertEqual(map_B["S03"], self.spk1)

        # Underlying biometric and identity records are identical
        name_A = self.storage.resolve_speaker_name(map_A["S01"])
        name_B = self.storage.resolve_speaker_name(map_B["S03"])
        self.assertEqual(name_A, name_B)
        self.assertEqual(name_A, "Dr. Katherine Synthetic")

    def test_06_manual_merge_sessions(self):
        """Invariant: merge_sessions combines clips and photos, recalculates bounds, and preserves primary ID."""
        s1 = SessionInfo(
            session_id=1,
            speaker_name="Opening Session",
            clips=[create_dummy_clip("clip_01.mp4", 0.0, 180.0)],
            photos=[create_dummy_photo("photo_01.jpg", 30.0)],
            session_type="solo",
            expected_speaker={"alias": "S01", "role": "Speaker"},
        )
        s1.calculate_bounds()

        s2 = SessionInfo(
            session_id=2,
            speaker_name="Keynote Remarks",
            clips=[create_dummy_clip("clip_02.mp4", 200.0, 240.0)],
            photos=[create_dummy_photo("photo_02.jpg", 220.0)],
            session_type="solo",
            expected_speaker={"alias": "S01", "role": "Speaker"},
        )
        s2.calculate_bounds()

        s3 = SessionInfo(
            session_id=3,
            speaker_name="Afternoon Talk",
            clips=[create_dummy_clip("clip_03.mp4", 600.0, 300.0)],
            session_type="solo",
            expected_speaker={"alias": "S02", "role": "Speaker"},
        )
        s3.calculate_bounds()

        sessions = [s1, s2, s3]
        merged_list = merge_sessions(sessions, session_ids=[1, 2])

        self.assertEqual(len(merged_list), 2)
        merged = merged_list[0]
        self.assertEqual(merged.session_id, 1)
        self.assertEqual(len(merged.clips), 2)
        self.assertEqual(len(merged.photos), 2)
        self.assertEqual(merged.clips[0].filename, "clip_01.mp4")
        self.assertEqual(merged.clips[1].filename, "clip_02.mp4")
        self.assertEqual(merged.start_time, s1.start_time)
        self.assertEqual(merged.end_time, s2.end_time)

        # Third session untouched
        self.assertEqual(merged_list[1].session_id, 3)

    def test_07_manual_split_session(self):
        """Invariant: split_session splits at clip boundary, partitions clips and photos, and updates bounds."""
        s1 = SessionInfo(
            session_id=1,
            speaker_name="Continuous Recording",
            clips=[
                create_dummy_clip("clip_01.mp4", 0.0, 120.0),
                create_dummy_clip("clip_02.mp4", 130.0, 150.0),
                create_dummy_clip("clip_03.mp4", 300.0, 200.0),
            ],
            photos=[
                create_dummy_photo("photo_01.jpg", 50.0),
                create_dummy_photo("photo_03.jpg", 350.0),
            ],
            session_type="solo",
            expected_speaker={"alias": "S01", "role": "Speaker"},
        )
        s1.calculate_bounds()

        sessions = [s1]
        # Split at clip index 2 (clip_01 and clip_02 in part 1; clip_03 in part 2)
        split_list = split_session(sessions, session_id=1, split_at_clip_index=2)

        self.assertEqual(len(split_list), 2)
        part1 = split_list[0]
        part2 = split_list[1]

        self.assertEqual(part1.session_id, 1)
        self.assertEqual(len(part1.clips), 2)
        self.assertEqual(part1.clips[0].filename, "clip_01.mp4")
        self.assertEqual(part1.clips[1].filename, "clip_02.mp4")
        self.assertEqual(len(part1.photos), 1)
        self.assertEqual(part1.photos[0].filename, "photo_01.jpg")

        self.assertEqual(part2.session_id, 2)
        self.assertEqual(len(part2.clips), 1)
        self.assertEqual(part2.clips[0].filename, "clip_03.mp4")
        self.assertEqual(len(part2.photos), 1)
        self.assertEqual(part2.photos[0].filename, "photo_03.jpg")

    def test_08_order_of_appearance_assignment_removed_in_multi_speaker(self):
        """
        Invariant: Removal of order-of-appearance assignment (roster[idx]) in multi_speaker.py.
        Unmatched turns without similarity map must receive anonymous labels, NEVER false attribution.
        """
        roster = [
            {"alias": "S01", "role": "Host"},
            {"alias": "S02", "role": "Panelist"},
        ]
        words = [
            {"start": 0.0, "end": 1.0, "word": "Unknown"},
            {"start": 1.1, "end": 2.0, "word": "voice."},
        ]
        turns = [
            {"start": 0.0, "end": 2.5, "speaker": "SPEAKER_00"},
        ]

        # Case 1: NO similarity map provided
        # Previous bug attributed SPEAKER_00 to roster[0] ("S01", "Host") based on order of appearance!
        # Now must NOT attribute to roster!
        segments_unmapped = assign_diarization_to_words(words, turns, roster, similarity_map=None)
        self.assertEqual(len(segments_unmapped), 1)
        self.assertEqual(segments_unmapped[0]["speaker"], "[Unidentified]")
        self.assertEqual(segments_unmapped[0]["role"], "")

        # Case 2: Verified acoustic similarity map provided
        sim_map = {"SPEAKER_00": "S01"}
        segments_verified = assign_diarization_to_words(words, turns, roster, similarity_map=sim_map)
        self.assertEqual(len(segments_verified), 1)
        self.assertEqual(segments_verified[0]["speaker"], "S01")
        self.assertEqual(segments_verified[0]["role"], "Host")

    def test_09_compute_zone_canary_scan_manifest_isolation(self):
        """
        Invariant: Canary scan verifies zero plaintext personal names and zero spk_ IDs in manifest.json.
        Only aliases (S01, S02) and random event_id appear in compute zone.
        """
        event_id = "evt_canary12345678"
        canary_name = "SecretCanaryKeynoteSpeakerXYZ"

        # Enroll canary person into Vault v2
        spk_canary = "spk_canary88889999"
        self.storage.create_identity_record(
            spk_id=spk_canary,
            full_name=canary_name,
            display_name=canary_name,
            roles=["Speaker"],
            consent_opt_in=True,
        )
        self.storage.create_biometric_record(
            spk_id=spk_canary,
            embedding_bytes=np.ones(256, dtype=np.float32).tobytes(),
            sample_names=["sample_1"],
        )

        # Save encrypted alias map in Vault v2
        self.roster_mgr.save_event_roster(event_id, [{"alias": "S01", "spk_id": spk_canary}])

        # Build Stage 2 session referencing alias S01
        sess = SessionInfo(
            session_id=1,
            speaker_name="Canary Keynote",
            clips=[create_dummy_clip("canary_clip.mp4", 0.0, 180.0)],
            session_type="solo",
            expected_speaker={"alias": "S01", "role": "Speaker", "name": canary_name, "spk_id": spk_canary},
        )
        sess.calculate_bounds()

        event = EventDetails(
            event_name="Canary Technology Summit",
            date="2026-09-05",
            organiser="Canary Org",
            venue="Hall A",
            event_id=event_id,
        )

        scribe_dir = os.path.join(self.temp_dir, "event_scribe")
        manifest_path, warns = ManifestManager.save_manifest(
            scribe_dir=scribe_dir,
            event=event,
            sessions=[sess],
            unassigned_photos=[],
            settings_used={"gap_seconds": 600.0},
        )

        self.assertTrue(os.path.isfile(manifest_path))

        with open(manifest_path, "r", encoding="utf-8") as f:
            manifest_content = f.read()

        # Invariant Assertions:
        self.assertIn("evt_canary12345678", manifest_content)
        self.assertIn('"alias": "S01"', manifest_content)

        # ZERO plaintext real names allowed in manifest.json!
        self.assertNotIn(canary_name, manifest_content, "CRITICAL: Plaintext personal name leaked into manifest.json!")
        # ZERO spk_ IDs allowed in manifest.json!
        self.assertNotIn(spk_canary, manifest_content, "CRITICAL: Persistent spk_ ID leaked into manifest.json!")
        self.assertNotIn("spk_", manifest_content, "CRITICAL: Any spk_ pattern leaked into manifest.json!")

    def test_10_dialog_stability_event_roster_and_session_config(self):
        """Invariant: EventRosterDialog and SessionConfigDialog are stable under light/dark theme and resize."""
        if not self.root:
            self.skipTest("Headless environment: Tk root not available")

        event_id = generate_event_id()
        self.roster_mgr.save_event_roster(event_id, [{"alias": "S01", "spk_id": self.spk1}])

        for mode in ("dark", "light"):
            theme.apply_global_theme(self.root, theme_mode=mode)

            # Test 1: EventRosterDialog
            dlg_roster = EventRosterDialog(self.root, event_id, self.roster_mgr)
            dlg_roster.update_idletasks()
            init_w_r = len(dlg_roster.winfo_children())

            # Resize to 150%
            dlg_roster.geometry("900x700")
            dlg_roster.event_generate("<Configure>", width=900, height=700)
            dlg_roster.update_idletasks()
            final_w_r = len(dlg_roster.winfo_children())
            self.assertEqual(init_w_r, final_w_r, "EventRosterDialog must not duplicate widgets on resize!")
            dlg_roster.destroy()

            # Test 2: SessionConfigDialog
            sess = SessionInfo(
                session_id=1,
                speaker_name="Solo Session",
                clips=[create_dummy_clip("clip_01.mp4", 0.0, 100.0)],
                session_type="solo",
                expected_speaker={"alias": "S01", "role": "Speaker"},
            )
            roster_items = self.roster_mgr.load_event_roster(event_id)
            dlg_cfg = SessionConfigDialog(self.root, sess, roster_items)
            dlg_cfg.update_idletasks()
            init_w_c = len(dlg_cfg.winfo_children())

            # Resize to 150%
            dlg_cfg.geometry("800x680")
            dlg_cfg.event_generate("<Configure>", width=800, height=680)
            dlg_cfg.update_idletasks()
            final_w_c = len(dlg_cfg.winfo_children())
            self.assertEqual(init_w_c, final_w_c, "SessionConfigDialog must not duplicate widgets on resize!")
            dlg_cfg.destroy()


if __name__ == "__main__":
    unittest.main()
