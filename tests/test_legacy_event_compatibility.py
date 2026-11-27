"""
tests/test_legacy_event_compatibility.py

Regression test verifying that legacy events (manifests with plaintext speaker names,
missing event_id, legacy speaker mapping box workflows) continue to work end-to-end
in Stage 2 and Stage 3 alongside new Vault v2 alias-based events.

Uses synthetic test data only (no real events, names, or audio).
"""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from scribe_core.manifest import EventDetails, ManifestManager
from scribe_core.session_grouper import ClipInfo, SessionGrouper, SessionInfo
from scribe_core.combined_transcripts import build_combined_transcripts


class TestLegacyEventCompatibility(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="ss_legacy_compat_")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_01_legacy_manifest_loads_and_combines_in_stage3(self):
        """
        Verify that a legacy manifest with plaintext speaker names (no event_id,
        no aliases, no session_type) loads and combines cleanly in Stage 3.
        """
        event_root = os.path.join(self.test_dir, "Legacy_Event_2026")
        scribe_dir = os.path.join(event_root, "scribe")
        transcripts_dir = os.path.join(scribe_dir, "transcripts")
        os.makedirs(transcripts_dir, exist_ok=True)

        # 1. Create a legacy manifest.json directly as written in older Session Scribe versions
        legacy_manifest_data = {
            "version": "1.0",
            "created_at": "2026-10-25T10:00:00+05:30",
            "event": {
                "name": "Legacy Conference 2026",
                "date": "2026-10-25",
                "organiser": "Legacy Org",
                "venue": "Main Hall",
                "speaker_mode": "single",
            },
            "event_root": event_root,
            "settings": {"gap_threshold_seconds": 600.0},
            "sessions": [
                {
                    "session_id": 1,
                    "speaker": "Professor Synthetic Speaker",
                    "speaker_mode": "single",
                    "clip_count": 2,
                    "photo_count": 0,
                    "clips": [
                        {"name": "clip_01.mp4", "duration": 120.0, "start_time": "2026-10-25T10:00:00"},
                        {"name": "clip_02.mp4", "duration": 150.0, "start_time": "2026-10-25T10:05:00"},
                    ],
                    "photos": [],
                    "roster": [
                        {"name": "Professor Synthetic Speaker", "role": "Speaker"}
                    ],
                }
            ],
            "unassigned_photos": [],
        }

        manifest_path = os.path.join(scribe_dir, "manifest.json")
        with open(manifest_path, "w", encoding="utf-8") as f:
            json.dump(legacy_manifest_data, f, indent=2)

        # 2. Write completed synthetic transcript files with valid header
        t1_path = os.path.join(transcripts_dir, "clip_01.txt")
        t2_path = os.path.join(transcripts_dir, "clip_02.txt")

        header = (
            "Session Scribe Transcript\n"
            "Event: Legacy Conference 2026\n"
            "Speaker: Professor Synthetic Speaker\n"
            "Transcription Completed: 2026-10-25 10:15:00\n"
            "--------------------------------------------------\n"
        )
        with open(t1_path, "w", encoding="utf-8") as f:
            f.write(header + "[Professor Synthetic Speaker] [00:00:00.000 - 00:02:00.000] Welcome everyone to the morning lecture.\n")
        with open(t2_path, "w", encoding="utf-8") as f:
            f.write(header + "[Professor Synthetic Speaker] [00:00:00.000 - 00:02:30.000] Continuing our technical discussion today.\n")

        # 3. Execute Stage 3 combined transcript generation
        result = build_combined_transcripts(event_root, legacy_manifest_data)

        # 4. Verify output file path and content
        combined_file = os.path.join(scribe_dir, "combined", "Professor_Synthetic_Speaker_combined.txt")
        self.assertTrue(os.path.isfile(combined_file), f"Expected combined file at {combined_file}")
        self.assertEqual(result["counts"].get("Professor_Synthetic_Speaker_combined.txt"), 2)

        with open(combined_file, "r", encoding="utf-8") as f:
            combined_content = f.read()

        self.assertIn("Welcome everyone to the morning lecture", combined_content)
        self.assertIn("Continuing our technical discussion today", combined_content)

    def test_02_legacy_speaker_mapping_box_flow_preserves_speaker_name(self):
        """
        Verify that using the legacy Speaker Mapping box (without Vault v2 enrollment)
        groups clips by the mapped speaker and saves that name in manifest.json.
        """
        from datetime import datetime
        now = datetime(2026, 11, 1, 10, 0, 0)
        from datetime import timedelta

        grouper = SessionGrouper(gap_threshold_seconds=600.0)

        clips = [
            ClipInfo(filename="CAM_001.MOV", filepath="/tmp/CAM_001.MOV", start_time=now, duration=60.0, end_time=now + timedelta(seconds=60)),
            ClipInfo(filename="CAM_002.MOV", filepath="/tmp/CAM_002.MOV", start_time=now + timedelta(seconds=120), duration=80.0, end_time=now + timedelta(seconds=200)),
        ]

        # User pasted a mapping in the Speaker Mapping box
        mapping_dict = {
            "CAM_001": "Dr. Legacy Guest",
            "CAM_002": "Dr. Legacy Guest",
        }

        sessions = grouper.group_clips(
            clips=clips,
            mapping_dict=mapping_dict,
            default_speaker_mode="single",
        )

        self.assertEqual(len(sessions), 1)
        self.assertEqual(sessions[0].speaker_name, "Dr. Legacy Guest")

        # In compute_zone_safe serialization when expected_speaker is None:
        serialized = sessions[0].to_dict(compute_zone_safe=True)
        # speaker should remain the mapped name since no Vault alias exists
        self.assertEqual(serialized["speaker"], "Dr. Legacy Guest")

        event = EventDetails(
            event_name="Legacy Mapped Event",
            date="2026-11-01",
            organiser="Organizer Corp",
            venue="Hall 2",
        )
        scribe_dir = os.path.join(self.test_dir, "mapped_event", "scribe")
        ManifestManager.save_manifest(
            scribe_dir=scribe_dir,
            event=event,
            sessions=sessions,
            unassigned_photos=[],
            settings_used={},
        )

        loaded = ManifestManager.load_manifest(os.path.join(scribe_dir, "manifest.json"))
        self.assertEqual(loaded["sessions"][0]["speaker"], "Dr. Legacy Guest")

    def test_03_alias_based_manifest_in_stage3(self):
        """
        Verify that an alias-based manifest from Phase V5 writes S01_combined.txt
        in Stage 3 without crashing.
        """
        event_root = os.path.join(self.test_dir, "Alias_Event_2026")
        scribe_dir = os.path.join(event_root, "scribe")
        transcripts_dir = os.path.join(scribe_dir, "transcripts")
        os.makedirs(transcripts_dir, exist_ok=True)

        alias_manifest_data = {
            "version": "1.0",
            "created_at": "2026-11-19T10:00:00+05:30",
            "event": {
                "event_id": "evt_0123456789abcdef",
                "name": "Alias Conference 2026",
                "date": "2026-11-19",
                "organiser": "Org",
                "venue": "Hall",
                "speaker_mode": "single",
            },
            "event_root": event_root,
            "settings": {},
            "sessions": [
                {
                    "session_id": 1,
                    "session_type": "solo",
                    "speaker": "S01",
                    "speaker_mode": "single",
                    "clip_count": 1,
                    "photo_count": 0,
                    "clips": [
                        {"name": "clip_01.mp4", "duration": 100.0, "start_time": "2026-11-19T10:00:00"},
                    ],
                    "photos": [],
                    "roster": [{"alias": "S01", "role": "Speaker"}],
                    "expected_speaker": {"alias": "S01", "role": "Speaker"},
                }
            ],
            "unassigned_photos": [],
        }

        t1_path = os.path.join(transcripts_dir, "clip_01.txt")
        header = (
            "Session Scribe Transcript\n"
            "Event: Alias Conference 2026\n"
            "Speaker: S01\n"
            "Transcription Completed: 2026-11-19 10:15:00\n"
            "--------------------------------------------------\n"
        )
        with open(t1_path, "w", encoding="utf-8") as f:
            f.write(header + "[S01] [00:00:00.000 - 00:01:40.000] Hello world with alias output.\n")

        result = build_combined_transcripts(event_root, alias_manifest_data)
        combined_file = os.path.join(scribe_dir, "combined", "S01_combined.txt")
        self.assertTrue(os.path.isfile(combined_file))
        self.assertEqual(result["counts"].get("S01_combined.txt"), 1)


if __name__ == "__main__":
    unittest.main()
