"""
Tests for Task A1: Authoritative Speaker Mapping, Gap Splitting, Unnamed Sessions,
and Overlap Photo Assignment.
"""

import os
import sys
import unittest
from datetime import datetime, timedelta

# sys.path guard derived from __file__
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from scribe_core.session_grouper import SessionGrouper, ClipInfo, PhotoInfo, SessionInfo
from scribe_core.manifest import ManifestManager, EventDetails


class TestAuthoritativeSpeakerMapping(unittest.TestCase):
    """Test suite for authoritative mapping, gap grouping, and photo assignment."""

    def setUp(self):
        self.grouper = SessionGrouper(gap_threshold_seconds=600.0, photo_grace_seconds=180.0)
        self.base_time = datetime(2026, 11, 3, 10, 0, 0)

    def test_five_named_people_authoritative_mapping(self):
        """
        Five named people:
        Jehosheba Amruthaluri, Surender Tanwar, Madhav Poosarla, Kishore Katta, Subhankar Dey.
        Each clip mapped to its person's name.
        Consecutive clips of the same person form sessions.
        """
        people = [
            "Jehosheba Amruthaluri",
            "Surender Tanwar",
            "Madhav Poosarla",
            "Kishore Katta",
            "Subhankar Dey",
        ]
        clips = []
        cur = self.base_time
        # Create 2 clips per person, spaced by 30 seconds
        for i, person in enumerate(people):
            for clip_idx in range(2):
                c = ClipInfo(
                    filename=f"clip_{i}_{clip_idx}.mp4",
                    filepath=f"/dummy/clip_{i}_{clip_idx}.mp4",
                    start_time=cur,
                    duration=120.0,
                    end_time=cur + timedelta(seconds=120),
                    speaker_name=person,
                )
                clips.append(c)
                cur = c.end_time + timedelta(seconds=30)

        sessions = self.grouper.group_clips(clips)
        self.assertEqual(len(sessions), 5)
        for i, sess in enumerate(sessions):
            self.assertEqual(sess.speaker_name, people[i])
            self.assertEqual(len(sess.clips), 2)
            self.assertEqual(sess.session_id, i + 1)

    def test_back_to_back_clips_of_different_people_never_merge(self):
        """
        Back-to-back clips of different people:
        Speaker A ends at 10:05:00.
        Speaker B starts at 10:05:05 (gap 5s < 600s).
        Must NEVER merge into the same session in One speaker mode.
        """
        c1 = ClipInfo(
            filename="vid1.mp4",
            filepath="/dummy/vid1.mp4",
            start_time=self.base_time,
            duration=300.0,
            end_time=self.base_time + timedelta(seconds=300),
            speaker_name="Speaker A",
        )
        c2 = ClipInfo(
            filename="vid2.mp4",
            filepath="/dummy/vid2.mp4",
            start_time=c1.end_time + timedelta(seconds=5),  # 5 seconds idle gap
            duration=300.0,
            end_time=c1.end_time + timedelta(seconds=305),
            speaker_name="Speaker B",
        )

        sessions = self.grouper.group_clips([c1, c2])
        self.assertEqual(len(sessions), 2)
        self.assertEqual(sessions[0].speaker_name, "Speaker A")
        self.assertEqual(sessions[1].speaker_name, "Speaker B")
        self.assertEqual(sessions[0].clips, [c1])
        self.assertEqual(sessions[1].clips, [c2])

    def test_same_person_after_long_gap_creates_two_sessions(self):
        """
        Same person speaks in the morning and later after a long gap (> 10 min):
        Two sessions, both with the same speaker name.
        """
        c1 = ClipInfo(
            filename="vid1.mp4",
            filepath="/dummy/vid1.mp4",
            start_time=self.base_time,
            duration=300.0,
            end_time=self.base_time + timedelta(seconds=300),
            speaker_name="Surender Tanwar",
        )
        # Gap of 30 minutes (1800s > 600s threshold)
        c2 = ClipInfo(
            filename="vid2.mp4",
            filepath="/dummy/vid2.mp4",
            start_time=c1.end_time + timedelta(seconds=1800),
            duration=300.0,
            end_time=c1.end_time + timedelta(seconds=2100),
            speaker_name="Surender Tanwar",
        )

        sessions = self.grouper.group_clips([c1, c2])
        self.assertEqual(len(sessions), 2)
        self.assertEqual(sessions[0].speaker_name, "Surender Tanwar")
        self.assertEqual(sessions[1].speaker_name, "Surender Tanwar")
        self.assertEqual(sessions[0].session_id, 1)
        self.assertEqual(sessions[1].session_id, 2)

    def test_unmapped_clips_form_individual_unnamed_sessions(self):
        """
        Unmapped clips form their own sessions named 'Unnamed 1', 'Unnamed 2'...
        Never 'Speaker N'.
        """
        c1 = ClipInfo(
            filename="vid1.mp4",
            filepath="/dummy/vid1.mp4",
            start_time=self.base_time,
            duration=120.0,
            end_time=self.base_time + timedelta(seconds=120),
            speaker_name="Kishore Katta",
        )
        c2 = ClipInfo(
            filename="vid2.mp4",
            filepath="/dummy/vid2.mp4",
            start_time=c1.end_time + timedelta(seconds=10),
            duration=120.0,
            end_time=c1.end_time + timedelta(seconds=130),
            speaker_name=None,  # Unmapped clip!
        )
        c3 = ClipInfo(
            filename="vid3.mp4",
            filepath="/dummy/vid3.mp4",
            start_time=c2.end_time + timedelta(seconds=10),
            duration=120.0,
            end_time=c2.end_time + timedelta(seconds=130),
            speaker_name="Subhankar Dey",
        )

        sessions = self.grouper.group_clips([c1, c2, c3])
        self.assertEqual(len(sessions), 3)
        self.assertEqual(sessions[0].speaker_name, "Kishore Katta")
        self.assertEqual(sessions[1].speaker_name, "Unnamed 1")
        self.assertEqual(sessions[2].speaker_name, "Subhankar Dey")

    def test_mapping_dict_argument_applied_to_unmapped_clips(self):
        """Mapping dictionary is applied by filename or stem."""
        c1 = ClipInfo(
            filename="VID001.mp4",
            filepath="/dummy/VID001.mp4",
            start_time=self.base_time,
            duration=100.0,
            end_time=self.base_time + timedelta(seconds=100),
        )
        mapping = {"VID001": "Madhav Poosarla"}
        sessions = self.grouper.group_clips([c1], mapping_dict=mapping)
        self.assertEqual(len(sessions), 1)
        self.assertEqual(sessions[0].speaker_name, "Madhav Poosarla")
        self.assertEqual(c1.speaker_name, "Madhav Poosarla")

    def test_photo_assignment_overlaps_go_to_nearest_clip(self):
        """
        Photos go to the session whose window [start - grace, end + grace] contains them.
        Overlaps go to the nearest clip.
        Photos outside every window stay Unassigned.
        """
        # Session 1: 10:00:00 to 10:10:00. Grace window: 09:57:00 to 10:13:00.
        s1_clip = ClipInfo(
            filename="s1.mp4",
            filepath="/dummy/s1.mp4",
            start_time=self.base_time,
            duration=600.0,
            end_time=self.base_time + timedelta(seconds=600),
            speaker_name="Speaker 1",
        )
        sess1 = SessionInfo(session_id=1, speaker_name="Speaker 1", clips=[s1_clip])
        sess1.calculate_bounds()

        # Session 2: 10:12:00 to 10:22:00. Grace window: 10:09:00 to 10:25:00.
        # Overlap window: [10:09:00, 10:13:00].
        s2_clip = ClipInfo(
            filename="s2.mp4",
            filepath="/dummy/s2.mp4",
            start_time=self.base_time + timedelta(seconds=720),
            duration=600.0,
            end_time=self.base_time + timedelta(seconds=1320),
            speaker_name="Speaker 2",
        )
        sess2 = SessionInfo(session_id=2, speaker_name="Speaker 2", clips=[s2_clip])
        sess2.calculate_bounds()

        # Photo 1 at 10:10:30 (30s after s1 ends, 90s before s2 starts) -> closer to s1
        p1 = PhotoInfo(
            filename="photo1.jpg",
            filepath="/dummy/photo1.jpg",
            timestamp=self.base_time + timedelta(seconds=630),
        )
        # Photo 2 at 10:11:40 (100s after s1 ends, 20s before s2 starts) -> closer to s2
        p2 = PhotoInfo(
            filename="photo2.jpg",
            filepath="/dummy/photo2.jpg",
            timestamp=self.base_time + timedelta(seconds=700),
        )
        # Photo 3 at 11:30:00 (far away) -> Unassigned
        p3 = PhotoInfo(
            filename="photo3.jpg",
            filepath="/dummy/photo3.jpg",
            timestamp=self.base_time + timedelta(seconds=5400),
        )

        sessions, unassigned = self.grouper.assign_photos([p1, p2, p3], [sess1, sess2])
        self.assertEqual(p1.assigned_session_id, 1)
        self.assertEqual(p1.assignment_status, "Assigned")
        self.assertEqual(p2.assigned_session_id, 2)
        self.assertEqual(p2.assignment_status, "Assigned")
        self.assertIn(p3, unassigned)
        self.assertEqual(p3.assignment_status, "Unassigned")

    def test_manifest_roundtrip_preserves_clip_speaker_names(self):
        """Manifest preserves clip-level speaker_name and restores without re-detection."""
        c = ClipInfo(
            filename="clip1.mp4",
            filepath="/dummy/clip1.mp4",
            start_time=self.base_time,
            duration=120.0,
            end_time=self.base_time + timedelta(seconds=120),
            speaker_name="Jehosheba Amruthaluri",
            person_id="person_123",
        )
        sess = SessionInfo(session_id=1, speaker_name="Jehosheba Amruthaluri", clips=[c])
        event = EventDetails(
            event_name="Test Event",
            date="3 Nov 2026",
            organiser="Test Org",
            venue="Test Hall",
        )

        manifest_data = ManifestManager.build_manifest(
            event=event,
            sessions=[sess],
            unassigned_photos=[],
            settings_used={"session_gap_threshold_seconds": 600.0, "photo_grace_seconds": 180.0},
        )

        # Check serialization
        serialized_clip = manifest_data["sessions"][0]["clips"][0]
        self.assertEqual(serialized_clip["speaker_name"], "Jehosheba Amruthaluri")
        self.assertEqual(serialized_clip["person_id"], "person_123")


if __name__ == "__main__":
    unittest.main()

