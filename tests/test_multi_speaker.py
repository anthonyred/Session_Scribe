"""
tests/test_multi_speaker.py

Tests for Multi-Speaker Mode (Task D):
- Roles and validation (Host, Speaker, Panelist, Moderator, Guest).
- Line format:
    [Name (Role)] [HH:MM:SS.mmm - HH:MM:SS.mmm] text
    [Overlapping: Name A (Role), Name B (Role)] [HH:MM:SS.mmm - HH:MM:SS.mmm] text
    [Unidentified] [HH:MM:SS.mmm - HH:MM:SS.mmm] text
    Single speaker fallback: [Name] [HH:MM:SS.mmm - HH:MM:SS.mmm] text
- Header format: 'Speakers: Name (Role), ...'
- Diarization turn-to-word alignment with segment splitting at speaker boundaries.
- Hugging Face token preflight check and gated model access verification.
- Combined transcript generation: <session label>_combined.txt for multi-speaker sessions.
"""

import json
import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

_TEST_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_TEST_DIR)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from scribe_core.multi_speaker import (
    ALLOWED_ROLES,
    BIOMETRIC_PRIVACY_NOTICE,
    RosterMember,
    format_speaker_line,
    format_speakers_header,
    assign_diarization_to_words,
    smooth_speaker_turns,
    DEFAULT_MIN_ISLAND_DURATION_SEC,
    format_hhmmss_mmm,
)
from scribe_core import hf_preflight
from scribe_core.combined_transcripts import build_combined_transcripts, sanitize_speaker_filename


class TestMultiSpeakerFormatting(unittest.TestCase):
    """Test speaker roles, headers, line formats, overlaps, and unidentified speech."""

    def test_allowed_roles(self):
        for role in ["Host", "Speaker", "Panelist", "Moderator", "Guest"]:
            self.assertIn(role, ALLOWED_ROLES)
            m = RosterMember(name="Alice", role=role)
            self.assertEqual(m.role, role)

        # Disallowed role defaults to Speaker
        m_bad = RosterMember(name="Bob", role="Actor")
        self.assertEqual(m_bad.role, "Speaker")

    def test_format_speakers_header(self):
        roster = [
            {"name": "Dr. Sarah", "role": "Host"},
            {"name": "Prof. Alan", "role": "Speaker"},
            {"name": "Elena Rostova", "role": "Panelist"},
        ]
        hdr = format_speakers_header(roster)
        self.assertEqual(hdr, "Speakers: Dr. Sarah (Host), Prof. Alan (Speaker), Elena Rostova (Panelist)")

    def test_single_speaker_line_format(self):
        line = format_speaker_line(
            speaker_name="Kalyani Patra",
            role=None,
            start_sec=12.345,
            end_sec=18.678,
            text="Welcome to the event.",
            is_multi_speaker=False,
        )
        self.assertEqual(line, "[Kalyani Patra] [00:00:12.345 - 00:00:18.678] Welcome to the event.")

    def test_multi_speaker_line_format(self):
        line = format_speaker_line(
            speaker_name="Dr. Sarah",
            role="Host",
            start_sec=0.0,
            end_sec=5.250,
            text="Let's open the discussion.",
            is_multi_speaker=True,
        )
        self.assertEqual(line, "[Dr. Sarah (Host)] [00:00:00.000 - 00:00:05.250] Let's open the discussion.")

    def test_multi_speaker_overlap_format(self):
        line = format_speaker_line(
            speaker_name="Overlapping",
            role="",
            start_sec=10.0,
            end_sec=14.0,
            text="I agree with that point.",
            is_multi_speaker=True,
            is_overlapping=True,
            overlapping_speakers=[("Dr. Sarah", "Host"), ("Prof. Alan", "Speaker")],
        )
        self.assertEqual(
            line,
            "[Overlapping: Dr. Sarah (Host), Prof. Alan (Speaker)] [00:00:10.000 - 00:00:14.000] I agree with that point.",
        )

    def test_multi_speaker_unidentified_format(self):
        line = format_speaker_line(
            speaker_name="[Unidentified]",
            role="",
            start_sec=20.0,
            end_sec=22.5,
            text="Unclear audience remark.",
            is_multi_speaker=True,
        )
        self.assertEqual(line, "[Unidentified] [00:00:20.000 - 00:00:22.500] Unclear audience remark.")


class TestDiarizationAlignment(unittest.TestCase):
    """Test alignment of transcribed words with speaker turns and segment splitting."""

    def test_assign_diarization_to_words(self):
        roster = [
            {"name": "Alice", "role": "Host"},
            {"name": "Bob", "role": "Panelist"},
        ]
        words = [
            {"start": 0.0, "end": 1.0, "word": "Hello"},
            {"start": 1.1, "end": 2.0, "word": "everyone,"},
            {"start": 2.1, "end": 3.0, "word": "thank"},
            {"start": 3.1, "end": 4.0, "word": "you."},
            {"start": 4.5, "end": 5.5, "word": "Glad"},
            {"start": 5.6, "end": 6.5, "word": "to"},
            {"start": 6.6, "end": 7.5, "word": "be"},
            {"start": 7.6, "end": 8.5, "word": "here."},
        ]
        turns = [
            {"start": 0.0, "end": 4.2, "speaker": "SPEAKER_00"},
            {"start": 4.3, "end": 9.0, "speaker": "SPEAKER_01"},
        ]
        sim_map = {"SPEAKER_00": "Alice", "SPEAKER_01": "Bob"}

        segments = assign_diarization_to_words(words, turns, roster, similarity_map=sim_map)
        self.assertEqual(len(segments), 2)

        # Turn 1: Alice (Host)
        self.assertEqual(segments[0]["speaker"], "Alice")
        self.assertEqual(segments[0]["role"], "Host")
        self.assertEqual(segments[0]["text"], "Hello everyone, thank you.")

        # Turn 2: Bob (Panelist)
        self.assertEqual(segments[1]["speaker"], "Bob")
        self.assertEqual(segments[1]["role"], "Panelist")
        self.assertEqual(segments[1]["text"], "Glad to be here.")

    def test_turn_smoothing_jittery_word_level_input(self):
        """Verify turn smoothing merges short speaker islands (< 0.7s) between the same speaker."""
        roster = [
            {"name": "Alice", "role": "Host"},
            {"name": "Bob", "role": "Panelist"},
        ]
        # Jittery input:
        # Alice speaks 0.0 - 2.0s
        # 0.2s glitch to Bob (2.1 - 2.3s)
        # Alice continues 2.4 - 4.5s
        # 0.25s glitch to [Unidentified] (4.6 - 4.85s)
        # Alice finishes 5.0 - 6.5s
        words = [
            {"start": 0.0, "end": 0.8, "word": "Good"},
            {"start": 0.9, "end": 2.0, "word": "morning."},
            {"start": 2.1, "end": 2.3, "word": "uh"},
            {"start": 2.4, "end": 3.4, "word": "Welcome"},
            {"start": 3.5, "end": 4.5, "word": "everyone."},
            {"start": 4.6, "end": 4.85, "word": "hmm"},
            {"start": 5.0, "end": 5.8, "word": "Let's"},
            {"start": 5.9, "end": 6.5, "word": "begin."},
        ]
        turns = [
            {"start": 0.0, "end": 2.05, "speaker": "SPK_00"},
            {"start": 2.06, "end": 2.35, "speaker": "SPK_01"},
            {"start": 2.36, "end": 4.55, "speaker": "SPK_00"},
            {"start": 4.56, "end": 4.95, "speaker": "SPK_99"},  # Unmapped turn
            {"start": 4.96, "end": 7.0, "speaker": "SPK_00"},
        ]
        sim_map = {"SPK_00": "Alice", "SPK_01": "Bob"}

        # With default smoothing (0.7s): both jittery glitches (< 0.7s) should merge into Alice
        smoothed_segments = assign_diarization_to_words(
            words, turns, roster, similarity_map=sim_map, min_island_sec=0.7
        )
        self.assertEqual(len(smoothed_segments), 1)
        self.assertEqual(smoothed_segments[0]["speaker"], "Alice")
        self.assertEqual(smoothed_segments[0]["role"], "Host")
        self.assertEqual(
            smoothed_segments[0]["text"],
            "Good morning. uh Welcome everyone. hmm Let's begin.",
        )
        self.assertEqual(smoothed_segments[0]["start"], 0.0)
        self.assertEqual(smoothed_segments[0]["end"], 6.5)

        # Without smoothing (min_island_sec=0.0): all 5 jittery segments must be preserved
        raw_segments = assign_diarization_to_words(
            words, turns, roster, similarity_map=sim_map, min_island_sec=0.0
        )
        self.assertEqual(len(raw_segments), 5)
        self.assertEqual(raw_segments[0]["speaker"], "Alice")
        self.assertEqual(raw_segments[1]["speaker"], "Bob")
        self.assertEqual(raw_segments[2]["speaker"], "Alice")
        self.assertEqual(raw_segments[3]["speaker"], "[Unidentified]")
        self.assertEqual(raw_segments[4]["speaker"], "Alice")

    def test_turn_smoothing_never_across_overlapping_line(self):
        """Verify turn smoothing NEVER smooths across or into an [Overlapping] line."""
        roster = [
            {"name": "Alice", "role": "Host"},
            {"name": "Bob", "role": "Panelist"},
        ]
        # Overlapping turn in the middle (0.3s):
        # Alice speaks 0.0 - 2.0s
        # 0.3s Overlapping turn (2.1 - 2.4s)
        # Alice speaks 2.5 - 4.5s
        words = [
            {"start": 0.0, "end": 2.0, "word": "I agree with that."},
            {"start": 2.1, "end": 2.4, "word": "Both speaking here."},
            {"start": 2.5, "end": 4.5, "word": "Continuing my point."},
        ]
        turns = [
            {"start": 0.0, "end": 2.05, "speaker": "SPK_00"},
            {"start": 2.06, "end": 2.45, "speaker": "SPK_00"},
            {"start": 2.06, "end": 2.45, "speaker": "SPK_01"},  # Dual speaker -> overlap
            {"start": 2.46, "end": 5.0, "speaker": "SPK_00"},
        ]
        sim_map = {"SPK_00": "Alice", "SPK_01": "Bob"}

        segments = assign_diarization_to_words(
            words, turns, roster, similarity_map=sim_map, min_island_sec=0.7
        )
        # Must retain 3 distinct segments; overlap must NEVER be merged away
        self.assertEqual(len(segments), 3)
        self.assertEqual(segments[0]["speaker"], "Alice")
        self.assertFalse(segments[0]["is_overlapping"])

        self.assertTrue(segments[1]["is_overlapping"])
        self.assertEqual(segments[1]["text"], "Both speaking here.")

        self.assertEqual(segments[2]["speaker"], "Alice")
        self.assertFalse(segments[2]["is_overlapping"])

    def test_turn_smoothing_short_interjection_between_different_speakers(self):
        """Verify short remarks (< 0.7s) between DIFFERENT speakers are NOT smoothed."""
        roster = [
            {"name": "Alice", "role": "Host"},
            {"name": "Bob", "role": "Panelist"},
            {"name": "Carol", "role": "Panelist"},
        ]
        # Alice -> Bob (0.35s) -> Carol
        words = [
            {"start": 0.0, "end": 2.0, "word": "Do you agree?"},
            {"start": 2.1, "end": 2.45, "word": "Yes."},
            {"start": 2.55, "end": 5.0, "word": "I see it differently."},
        ]
        turns = [
            {"start": 0.0, "end": 2.05, "speaker": "SPK_ALICE"},
            {"start": 2.08, "end": 2.50, "speaker": "SPK_BOB"},
            {"start": 2.52, "end": 5.0, "speaker": "SPK_CAROL"},
        ]
        sim_map = {"SPK_ALICE": "Alice", "SPK_BOB": "Bob", "SPK_CAROL": "Carol"}

        segments = assign_diarization_to_words(
            words, turns, roster, similarity_map=sim_map, min_island_sec=0.7
        )
        self.assertEqual(len(segments), 3)
        self.assertEqual(segments[0]["speaker"], "Alice")
        self.assertEqual(segments[1]["speaker"], "Bob")
        self.assertEqual(segments[1]["text"], "Yes.")
        self.assertEqual(segments[2]["speaker"], "Carol")

    def test_turn_smoothing_island_above_threshold_retained(self):
        """Verify speaker turns >= min_island_sec (e.g. 0.85s >= 0.7s) are NOT smoothed."""
        roster = [
            {"name": "Alice", "role": "Host"},
            {"name": "Bob", "role": "Panelist"},
        ]
        words = [
            {"start": 0.0, "end": 2.0, "word": "Opening question."},
            {"start": 2.1, "end": 2.95, "word": "Substantial answer."},
            {"start": 3.05, "end": 5.0, "word": "Follow-up question."},
        ]
        turns = [
            {"start": 0.0, "end": 2.05, "speaker": "SPK_00"},
            {"start": 2.08, "end": 3.0, "speaker": "SPK_01"},  # 0.85s duration >= 0.7s
            {"start": 3.02, "end": 5.0, "speaker": "SPK_00"},
        ]
        sim_map = {"SPK_00": "Alice", "SPK_01": "Bob"}

        segments = assign_diarization_to_words(
            words, turns, roster, similarity_map=sim_map, min_island_sec=0.7
        )
        self.assertEqual(len(segments), 3)
        self.assertEqual(segments[0]["speaker"], "Alice")
        self.assertEqual(segments[1]["speaker"], "Bob")
        self.assertEqual(segments[1]["text"], "Substantial answer.")
        self.assertEqual(segments[2]["speaker"], "Alice")


class TestCombinedMultiSpeakerTranscripts(unittest.TestCase):
    """Verify <session label>_combined.txt output for multiple speakers sessions."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.event_root = self.temp_dir
        self.scribe_dir = os.path.join(self.event_root, "scribe")
        self.transcripts_dir = os.path.join(self.scribe_dir, "transcripts")
        os.makedirs(self.transcripts_dir, exist_ok=True)

    def tearDown(self):
        try:
            import shutil
            shutil.rmtree(self.temp_dir, ignore_errors=True)
        except Exception:
            pass

    def test_multi_speaker_session_combined_filename(self):
        manifest_data = {
            "sessions": [
                {
                    "session_id": 1,
                    "speaker": "Panel Discussion - AI Future",
                    "speaker_mode": "panel",
                    "roster": [{"name": "Alice", "role": "Host"}, {"name": "Bob", "role": "Panelist"}],
                    "clips": [{"name": "clip_01.mp4", "start_time": "2026-11-03T10:00:00"}],
                }
            ]
        }
        # Write dummy transcript
        txt_path = os.path.join(self.transcripts_dir, "clip_01.txt")
        with open(txt_path, "w", encoding="utf-8") as f:
            f.write("Transcription Completed: 2026-11-03 10:05:00 IST\nSpeakers: Alice (Host), Bob (Panelist)\n\n[Alice (Host)] [00:00:01 - 00:00:05] Welcome.\n")

        res = build_combined_transcripts(self.event_root, manifest_data)
        combined_file = os.path.join(self.scribe_dir, "combined", "Panel_Discussion_-_AI_Future_combined.txt")
        self.assertTrue(os.path.isfile(combined_file))


class TestHuggingFacePreflight(unittest.TestCase):
    """Test HF token preflight check and gated model access."""

    def test_missing_token_fails_gracefully(self):
        is_ok, msg, details = hf_preflight.run_hf_preflight_check(token="")
        self.assertFalse(is_ok)
        self.assertEqual(details["status"], "not_set")
        self.assertIn("token is not set", msg.lower())

    @patch("huggingface_hub.whoami")
    @patch("huggingface_hub.model_info")
    def test_valid_token_and_gated_models_access(self, mock_model_info, mock_whoami):
        mock_whoami.return_value = {"name": "test_engineer", "fullname": "Test Engineer"}
        mock_model_info.return_value = MagicMock()

        is_ok, msg, details = hf_preflight.run_hf_preflight_check(token="hf_mock_token_123")
        self.assertTrue(is_ok)
        self.assertEqual(details["status"], "valid")
        self.assertEqual(details["username"], "test_engineer")
        self.assertIn("test_engineer", msg)
        # Token must never be printed or logged in output message
        self.assertNotIn("hf_mock_token_123", msg)


if __name__ == "__main__":
    unittest.main()

