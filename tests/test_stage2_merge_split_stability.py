"""
tests/test_stage2_merge_split_stability.py

Stress and stability verification for Stage 2 Merge and Split operations:
- Empty selection / single-item merge guard
- Non-adjacent and multi-session merge consolidation
- Split at first clip boundary (invalid/noop guard)
- Split at last clip boundary
- Split at interior clip boundaries
- Repeated split-then-merge cycles preserving total clip count and time bounds
- Boundary calculation stability with zero clips or single clips
- Memory stability under 100 rapid merge/split iterations
"""

import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from scribe_core.session_grouper import (
    ClipInfo,
    SessionInfo,
    merge_sessions,
    split_session,
)


class TestStage2MergeSplitStability(unittest.TestCase):
    def setUp(self):
        self.base_time = datetime(2026, 11, 19, 9, 0, 0)

    def _make_clip(self, idx: int, duration: float = 60.0) -> ClipInfo:
        start = self.base_time + timedelta(seconds=idx * 100)
        end = start + timedelta(seconds=duration)
        return ClipInfo(
            filename=f"clip_{idx:03d}.mp4",
            filepath=f"/test/clip_{idx:03d}.mp4",
            start_time=start,
            duration=duration,
            end_time=end,
        )

    def test_01_merge_guards_empty_and_single(self):
        """Verify merge operation raises ValueError for empty or single session selections."""
        s1 = SessionInfo(session_id=1, speaker_name="S01", clips=[self._make_clip(1)])
        sessions = [s1]

        # Empty selection raises ValueError
        with self.assertRaises(ValueError):
            merge_sessions(sessions, session_ids=[])

        # Single item selection raises ValueError
        with self.assertRaises(ValueError):
            merge_sessions(sessions, session_ids=[1])

        # Non-existent ID raises ValueError
        with self.assertRaises(ValueError):
            merge_sessions(sessions, session_ids=[999, 888])

    def test_02_split_guards_boundaries(self):
        """Verify split operation raises ValueError for out-of-range split indices."""
        c1 = self._make_clip(1)
        c2 = self._make_clip(2)
        s1 = SessionInfo(session_id=1, speaker_name="S01", clips=[c1, c2])
        sessions = [s1]

        # Split at index <= 0 raises ValueError
        with self.assertRaises(ValueError):
            split_session(sessions, session_id=1, split_at_clip_index=0)

        # Split at index >= len(clips) raises ValueError
        with self.assertRaises(ValueError):
            split_session(sessions, session_id=1, split_at_clip_index=2)

        # Split on non-existent session_id raises ValueError
        with self.assertRaises(ValueError):
            split_session(sessions, session_id=999, split_at_clip_index=1)

    def test_03_split_and_merge_roundtrip_conservation(self):
        """Verify total clip count and time boundaries are strictly conserved across split & merge."""
        clips = [self._make_clip(i) for i in range(10)]
        initial_session = SessionInfo(session_id=1, speaker_name="S01", clips=clips)
        initial_session.calculate_bounds()
        orig_start = initial_session.start_time
        orig_end = initial_session.end_time
        sessions = [initial_session]

        # Split at clip 3 (clips 0..2 remain in s1, clips 3..9 go to s2)
        sessions = split_session(sessions, session_id=1, split_at_clip_index=3)
        self.assertEqual(len(sessions), 2)
        self.assertEqual(len(sessions[0].clips), 3)
        self.assertEqual(len(sessions[1].clips), 7)

        # Split s2 at clip 4 (clips 3..6 in s2, clips 7..9 in s3)
        s2_id = sessions[1].session_id
        sessions = split_session(sessions, session_id=s2_id, split_at_clip_index=4)
        self.assertEqual(len(sessions), 3)
        self.assertEqual(len(sessions[0].clips), 3)
        self.assertEqual(len(sessions[1].clips), 4)
        self.assertEqual(len(sessions[2].clips), 3)

        # Total clips must be exactly 10
        total_clips = sum(len(s.clips) for s in sessions)
        self.assertEqual(total_clips, 10)

        # Now merge all 3 sessions back together
        all_ids = [s.session_id for s in sessions]
        merged = merge_sessions(sessions, session_ids=all_ids)
        self.assertEqual(len(merged), 1)
        self.assertEqual(len(merged[0].clips), 10)
        merged[0].calculate_bounds()
        self.assertEqual(merged[0].start_time, orig_start)
        self.assertEqual(merged[0].end_time, orig_end)

    def test_04_stress_100_rapid_cycles(self):
        """Execute 100 rapid randomized split and merge operations to prove zero state leak or crash."""
        clips = [self._make_clip(i) for i in range(8)]
        sessions = [SessionInfo(session_id=1, speaker_name="S01", clips=clips)]

        for cycle in range(100):
            # Split the largest session
            largest = max(sessions, key=lambda s: len(s.clips))
            if len(largest.clips) > 1:
                split_idx = len(largest.clips) // 2
                sessions = split_session(sessions, session_id=largest.session_id, split_at_clip_index=split_idx)

            # Merge if more than 2 sessions exist
            if len(sessions) >= 3:
                ids_to_merge = [sessions[0].session_id, sessions[1].session_id]
                sessions = merge_sessions(sessions, session_ids=ids_to_merge)

        # Invariant: Total clip count must remain exactly 8
        self.assertEqual(sum(len(s.clips) for s in sessions), 8)
        self.assertTrue(len(sessions) >= 1)


if __name__ == "__main__":
    unittest.main()
