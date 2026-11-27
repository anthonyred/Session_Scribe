"""
tests/test_session_tracker.py

Comprehensive tests for:
  1. scribe_core/session_tracker.py:
     - Initialization, persistence, and atomic updates.
     - Mid-point checkpointing: completed_up_to, status, chunks, accumulated full_transcript.
     - State recovery, failure recording, reset, and format_resume_status.
  2. Slicing with Mid-Point Resume:
     - Query get_resume_time(video_path) prior to processing.
     - Interruption simulation at an exact second (e.g., 70.0s).
     - ffmpeg slicing starts at -ss <completed_up_to> instead of 0:00.
     - Seamless stitching of final transcript text.
  3. UI status reflection:
     - Stage 3 disk status reporting "Resuming at 01:10" and "Completed".
"""

import os
import sys
import json
import shutil
import tempfile
import unittest
from unittest.mock import patch, MagicMock

# sys.path guard derived from __file__ per AGENTS.md rule 4
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from scribe_core import session_tracker
from scribe_core import transcribe_worker


class TestSessionTracker(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="scribe_tracker_test_")
        self.state_dir = os.path.join(self.temp_dir, "scribe", "state")
        os.makedirs(self.state_dir, exist_ok=True)
        self.video_path = os.path.join(self.temp_dir, "data", "sample_clip_01.mp4")
        os.makedirs(os.path.dirname(self.video_path), exist_ok=True)
        with open(self.video_path, "wb") as f:
            f.write(b"\x00" * 1024)

    def tearDown(self):
        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_init_video_state(self):
        rec = session_tracker.init_video_state(
            video_path=self.video_path,
            total_duration=120.0,
            state_dir=self.state_dir,
        )
        self.assertEqual(rec["status"], "pending")
        self.assertEqual(rec["total_duration"], 120.0)
        self.assertEqual(rec["completed_up_to"], 0.0)
        self.assertEqual(rec["chunks"], [])
        self.assertEqual(rec["full_transcript"], "")

        # State file exists on disk
        state_file = os.path.join(self.state_dir, "session_state.json")
        self.assertTrue(os.path.isfile(state_file))

    def test_get_resume_time_new_and_existing(self):
        # New video should return 0.0
        self.assertEqual(session_tracker.get_resume_time(self.video_path, self.state_dir), 0.0)

        # Save progress at 45.5s
        chunk1 = {"start": 0.0, "end": 45.5, "text": "Hello world.", "provider": "nvidia_parakeet"}
        session_tracker.save_chunk_progress(
            video_path=self.video_path,
            chunk_data=chunk1,
            new_timestamp=45.5,
            state_dir=self.state_dir,
            total_duration=180.0,
        )

        # Query get_resume_time returns exact timestamp
        self.assertEqual(session_tracker.get_resume_time(self.video_path, self.state_dir), 45.5)

    def test_save_chunk_progress_accumulates_chunks_and_text(self):
        # First chunk 0.0 to 30.0
        chunk1 = {"start": 0.0, "end": 30.0, "text": "Welcome to Session Scribe.", "provider": "nvidia_parakeet"}
        session_tracker.save_chunk_progress(
            video_path=self.video_path,
            chunk_data=chunk1,
            new_timestamp=30.0,
            state_dir=self.state_dir,
            total_duration=90.0,
        )

        state = session_tracker.get_video_state(self.video_path, self.state_dir)
        self.assertEqual(state["completed_up_to"], 30.0)
        self.assertEqual(state["status"], "in_progress")
        self.assertEqual(len(state["chunks"]), 1)
        self.assertEqual(state["full_transcript"], "Welcome to Session Scribe.")

        # Second chunk 30.0 to 60.0
        chunk2 = {"start": 30.0, "end": 60.0, "text": "This is discrete mid-point checkpointing.", "provider": "local"}
        session_tracker.save_chunk_progress(
            video_path=self.video_path,
            chunk_data=chunk2,
            new_timestamp=60.0,
            state_dir=self.state_dir,
            total_duration=90.0,
        )

        state = session_tracker.get_video_state(self.video_path, self.state_dir)
        self.assertEqual(state["completed_up_to"], 60.0)
        self.assertEqual(len(state["chunks"]), 2)
        expected_full = "Welcome to Session Scribe.\nThis is discrete mid-point checkpointing."
        self.assertEqual(state["full_transcript"], expected_full)

    def test_finalize_transcription(self):
        chunk1 = {"start": 0.0, "end": 30.0, "text": "Part one.", "provider": "local"}
        chunk2 = {"start": 30.0, "end": 60.0, "text": "Part two.", "provider": "local"}
        session_tracker.save_chunk_progress(self.video_path, chunk1, 30.0, self.state_dir, 60.0)
        session_tracker.save_chunk_progress(self.video_path, chunk2, 60.0, self.state_dir, 60.0)

        session_tracker.finalize_transcription(self.video_path, self.state_dir)

        state = session_tracker.get_video_state(self.video_path, self.state_dir)
        self.assertEqual(state["status"], "completed")
        self.assertEqual(state["completed_up_to"], 60.0)
        self.assertIn("Part one.", state["full_transcript"])
        self.assertIn("Part two.", state["full_transcript"])

    def test_mark_video_failed_and_reset(self):
        session_tracker.save_chunk_progress(
            self.video_path,
            {"start": 0.0, "end": 20.0, "text": "Started.", "provider": "cloud"},
            20.0,
            self.state_dir,
            60.0,
        )

        session_tracker.mark_video_failed(self.video_path, "CUDA out of memory", self.state_dir)
        state = session_tracker.get_video_state(self.video_path, self.state_dir)
        self.assertEqual(state["status"], "failed")
        self.assertEqual(state["error"], "CUDA out of memory")
        self.assertEqual(state["completed_up_to"], 20.0)

        # User forces re-transcribe -> reset_video_state
        session_tracker.reset_video_state(self.video_path, self.state_dir)
        self.assertIsNone(session_tracker.get_video_state(self.video_path, self.state_dir))
        self.assertEqual(session_tracker.get_resume_time(self.video_path, self.state_dir), 0.0)

    def test_format_resume_status(self):
        self.assertEqual(session_tracker.format_resume_status(0.0, "pending"), "Pending")
        self.assertEqual(session_tracker.format_resume_status(70.0, "in_progress"), "Resuming at 01:10")
        self.assertEqual(session_tracker.format_resume_status(125.0, "failed"), "Resuming at 02:05")
        self.assertEqual(session_tracker.format_resume_status(90.0, "completed"), "Completed")


class TestSlicingAndMidPointResume(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="scribe_slicing_test_")
        self.state_dir = os.path.join(self.temp_dir, "scribe", "state")
        os.makedirs(self.state_dir, exist_ok=True)
        self.video_path = os.path.join(self.temp_dir, "data", "test_video.mp4")
        os.makedirs(os.path.dirname(self.video_path), exist_ok=True)
        with open(self.video_path, "wb") as f:
            f.write(b"\x00" * 4096)

    def tearDown(self):
        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_extract_audio_slice_passes_ss_before_i(self):
        """Verify ffmpeg receives -ss <completed_up_to> before -i for mid-point seeking."""
        captured_cmds = []

        def mock_subprocess_run(cmd, check=True, capture_output=True):
            captured_cmds.append(cmd)
            # Create a mock 0.5s valid WAV file so get_wav_duration_seconds returns > 0
            out_file = cmd[-1]
            from scribe_core.cloud_engine import make_silence_wav
            with open(out_file, "wb") as wf:
                wf.write(make_silence_wav(30.0))
            return MagicMock(returncode=0)

        with patch("subprocess.run", side_effect=mock_subprocess_run):
            out_wav = os.path.join(self.temp_dir, "slice.wav")
            dur = transcribe_worker.extract_audio_slice(
                ffmpeg_bin="ffmpeg",
                media_path=self.video_path,
                output_wav=out_wav,
                start_sec=70.0,
                duration_sec=30.0,
            )
            self.assertGreater(dur, 0.0)
            self.assertEqual(len(captured_cmds), 1)
            cmd = captured_cmds[0]
            # Check -ss 70.000 is present and occurs before -i
            self.assertIn("-ss", cmd)
            ss_idx = cmd.index("-ss")
            self.assertEqual(cmd[ss_idx + 1], "70.000")
            i_idx = cmd.index("-i")
            self.assertLess(ss_idx, i_idx, "Expected -ss to precede -i for fast input seeking")

    def test_simulated_interruption_and_seamless_resumption(self):
        """
        Simulate a 90s video processed in 30s discrete windows:
          Phase 1: Processes 0-30s and 30-60s. Crashes/interrupts at 60s.
                   Checkpoint records exact second 60.0s.
          Phase 2: Resumption queries get_resume_time -> 60.0s.
                   Extracts slice starting from 60.0s to 90.0s.
                   Stitches seamlessly into complete 90s transcript.
        """
        # --- Phase 1: Run windows 1 and 2, then simulate interruption ---
        session_tracker.init_video_state(self.video_path, total_duration=90.0, state_dir=self.state_dir)

        # Slice 1 (0-30s)
        session_tracker.save_chunk_progress(
            video_path=self.video_path,
            chunk_data={"start": 0.0, "end": 30.0, "text": "Opening remarks and keynote introduction.", "provider": "nvidia_parakeet"},
            new_timestamp=30.0,
            state_dir=self.state_dir,
            total_duration=90.0,
        )

        # Slice 2 (30-60s)
        session_tracker.save_chunk_progress(
            video_path=self.video_path,
            chunk_data={"start": 30.0, "end": 60.0, "text": "Deep dive into architecture and models.", "provider": "nvidia_parakeet"},
            new_timestamp=60.0,
            state_dir=self.state_dir,
            total_duration=90.0,
        )

        # Simulate process crash / power cut: state must be intact on disk
        rec_interrupted = session_tracker.get_video_state(self.video_path, self.state_dir)
        self.assertEqual(rec_interrupted["completed_up_to"], 60.0)
        self.assertEqual(rec_interrupted["status"], "in_progress")
        self.assertEqual(len(rec_interrupted["chunks"]), 2)

        # UI formatting test during interruption
        ui_status = session_tracker.format_resume_status(rec_interrupted["completed_up_to"], rec_interrupted["status"])
        self.assertEqual(ui_status, "Resuming at 01:00")

        # --- Phase 2: Resume ---
        resume_t = session_tracker.get_resume_time(self.video_path, self.state_dir)
        self.assertEqual(resume_t, 60.0, "Resume time must match exact checkpoint timestamp")

        # Slice 3 (60-90s) processed after resume
        session_tracker.save_chunk_progress(
            video_path=self.video_path,
            chunk_data={"start": 60.0, "end": 90.0, "text": "Conclusion and audience Q&A session.", "provider": "local"},
            new_timestamp=90.0,
            state_dir=self.state_dir,
            total_duration=90.0,
        )

        session_tracker.finalize_transcription(self.video_path, self.state_dir)

        final_rec = session_tracker.get_video_state(self.video_path, self.state_dir)
        self.assertEqual(final_rec["status"], "completed")
        self.assertEqual(final_rec["completed_up_to"], 90.0)
        self.assertEqual(len(final_rec["chunks"]), 3)

        # Verify seamless stitching of accumulated transcript
        full_text = final_rec["full_transcript"]
        self.assertIn("Opening remarks and keynote introduction.", full_text)
        self.assertIn("Deep dive into architecture and models.", full_text)
        self.assertIn("Conclusion and audience Q&A session.", full_text)


class TestUIStage3Integration(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="scribe_stage3_ui_test_")
        self.event_dir = os.path.join(self.temp_dir, "Event_2026")
        self.state_dir = os.path.join(self.event_dir, "scribe", "state")
        self.transcripts_dir = os.path.join(self.event_dir, "scribe", "transcripts")
        self.test_dir = os.path.join(self.event_dir, "scribe", "test")
        os.makedirs(self.state_dir, exist_ok=True)
        os.makedirs(self.transcripts_dir, exist_ok=True)
        os.makedirs(self.test_dir, exist_ok=True)

        self.video_path = os.path.join(self.event_dir, "data", "clip_01.mp4")
        os.makedirs(os.path.dirname(self.video_path), exist_ok=True)
        with open(self.video_path, "wb") as f:
            f.write(b"\x00" * 2048)

        self.paths = {
            "root": self.event_dir,
            "state": self.state_dir,
            "transcripts": self.transcripts_dir,
            "test": self.test_dir,
        }

    def tearDown(self):
        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_clip_disk_status_shows_resuming_and_completed(self):
        from gui.stage3_tab import _clip_disk_status

        clip = {"name": "clip_01.mp4", "path": self.video_path}

        # 1. Fresh clip: Pending
        st = _clip_disk_status(clip, self.paths, "somehash")
        self.assertEqual(st, "Pending")

        # 2. Interrupted at 70s: Resuming at 01:10
        session_tracker.save_chunk_progress(
            self.video_path,
            {"start": 0.0, "end": 70.0, "text": "Halfway through.", "provider": "cloud"},
            70.0,
            self.state_dir,
            150.0,
        )
        st = _clip_disk_status(clip, self.paths, "somehash")
        self.assertEqual(st, "Resuming at 01:10")

        # 3. Finalized in session_tracker: Completed
        session_tracker.finalize_transcription(self.video_path, self.state_dir)
        st = _clip_disk_status(clip, self.paths, "somehash")
        self.assertEqual(st, "Completed")


if __name__ == "__main__":
    unittest.main()

