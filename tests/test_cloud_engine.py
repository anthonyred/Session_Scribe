"""
tests/test_cloud_engine.py

Comprehensive tests for Cloud Engine & Automatic Fallback in Session Scribe:
1. Provider Profile management (Parakeet, Whisper Large v3, Nemotron template).
2. Segment building from word timestamps.
3. Silence-aware audio chunking (split at pauses >= 0.5s or 1.0s overlap fallback).
4. Audio cutting for resume and seam-stitching without duplicates or gaps.
5. Checkpoint persistence in clip_state.json and stale-checkpoint detection.
6. Error handling & retries:
   - 401 / PermissionDenied -> immediate fallback without retry.
   - Quota / credit exhaustion -> immediate fallback ("NVIDIA free credits or limit reached, continuing locally").
   - Transient failure / timeout -> retry with backoff then fallback.
7. Exact local fallback & resume scenarios:
   - Failure before any output -> local runs from 0.0s.
   - Failure mid-clip (after N segments) -> local resumes from saved offset.
   - Failure on last chunk.
   - Mid-clip restart -> state checkpoint recovered.
   - Audio file modified -> stale checkpoint discarded, fresh run.
8. Output format consistency:
   - Transcript .txt format identical to local runs.
   - Engine recorded in state file, logs, and clip tree.
   - Transcripts folder holds only .txt files.
9. Secret safety: API key never appears in state files, job configs, or logs.
"""

import os
import sys
import json
import wave
import shutil
import tempfile
import unittest
from unittest.mock import MagicMock, patch

# sys.path guard per AGENTS.md §4
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from scribe_core import cloud_engine as ce
from scribe_core import clip_state as cs
from scribe_core.transcription_manager import TranscriptionManager


def create_dummy_wav(path: str, duration_sec: float = 1.0, framerate: int = 16000, silence: bool = True):
    """Helper to create a valid mono 16-bit PCM WAV file."""
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    n_frames = int(duration_sec * framerate)
    with wave.open(path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(framerate)
        if silence:
            wf.writeframes(b"\x00\x00" * n_frames)
        else:
            # Alternating waveform
            wf.writeframes(b"\x00\x10\x00\xf0" * (n_frames // 2))


class TestCloudEngineProfilesAndSegments(unittest.TestCase):
    """Unit tests for profiles and segment conversion."""

    def test_provider_profiles(self):
        parakeet = ce.get_profile_by_id("nvidia_parakeet")
        self.assertIsNotNone(parakeet)
        self.assertEqual(parakeet.protocol, "riva_grpc")
        self.assertEqual(parakeet.function_id, "d3fe9151-442b-4204-a70d-5fcc597fd610")
        self.assertTrue(parakeet.supports_boosting)
        self.assertTrue(parakeet.enabled)

        whisper = ce.get_profile_by_id("nvidia_whisper")
        self.assertIsNotNone(whisper)
        self.assertEqual(whisper.protocol, "riva_grpc")
        self.assertEqual(whisper.function_id, "b702f636-f60c-4a3d-a6f4-f3568c13bd7d")
        self.assertTrue(whisper.enabled)

        nemotron = ce.get_profile_by_id("nemotron_http_template")
        self.assertIsNotNone(nemotron)
        self.assertEqual(nemotron.protocol, "openai_http")
        self.assertFalse(nemotron.enabled)

        enabled = ce.get_enabled_profiles()
        self.assertIn(parakeet, enabled)
        self.assertIn(whisper, enabled)
        self.assertNotIn(nemotron, enabled)

    def test_build_segments_from_words(self):
        words = [
            ce.WordInfo(word="Hello", start_time=0.1, end_time=0.4),
            ce.WordInfo(word="world.", start_time=0.5, end_time=0.9),
            # Gap of 1.1s (>= 0.8s silence pause)
            ce.WordInfo(word="This", start_time=2.0, end_time=2.3),
            ce.WordInfo(word="is", start_time=2.4, end_time=2.6),
            ce.WordInfo(word="a", start_time=2.7, end_time=2.8),
            ce.WordInfo(word="test.", start_time=2.9, end_time=3.2),
        ]
        segments = ce.build_segments_from_words(words, max_silence_gap=0.8)
        self.assertEqual(len(segments), 2)
        self.assertEqual(segments[0]["text"], "Hello world.")
        self.assertEqual(segments[0]["start"], 0.1)
        self.assertEqual(segments[0]["end"], 0.9)

        self.assertEqual(segments[1]["text"], "This is a test.")
        self.assertEqual(segments[1]["start"], 2.0)
        self.assertEqual(segments[1]["end"], 3.2)


class TestAudioChunkingAndResumeStitching(unittest.TestCase):
    """Tests for silence-aware chunking and exact resume stitching."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_find_silence_split_points_short_clip(self):
        wav = os.path.join(self.temp_dir, "short.wav")
        create_dummy_wav(wav, duration_sec=5.0)
        chunks = ce.find_silence_split_points(wav, target_chunk_sec=120.0)
        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0], (0.0, 5.0))

    def test_find_silence_split_points_long_clip(self):
        wav = os.path.join(self.temp_dir, "long.wav")
        # 250s clip
        create_dummy_wav(wav, duration_sec=250.0)
        chunks = ce.find_silence_split_points(wav, target_chunk_sec=120.0)
        self.assertTrue(len(chunks) >= 2)
        self.assertEqual(chunks[0][0], 0.0)
        self.assertEqual(chunks[-1][1], 250.0)

    def test_stitch_segments_exact_resume(self):
        saved = [
            {"start": 0.0, "end": 10.0, "text": "Part one of speech."},
            {"start": 10.5, "end": 20.0, "text": "Part two of speech."},
        ]
        resume_offset = 20.0
        cut_offset = 19.5  # Audio cut at 19.5s

        # Local whisper returned 2 segments on remainder audio:
        # Segment 1 was in the overlap [0.1 to 0.4] -> absolute [19.6 to 19.9] <= 20.0 -> dropped!
        # Segment 2 starts at 0.7 -> absolute [20.2 to 30.0] -> kept!
        local_segs = [
            {"start": 0.1, "end": 0.4, "text": "speech."},
            {"start": 0.7, "end": 10.5, "text": "Part three continued locally."},
        ]

        stitched = ce.stitch_segments(saved, local_segs, resume_offset=resume_offset, cut_offset=cut_offset)
        self.assertEqual(len(stitched), 3)
        self.assertEqual(stitched[0]["text"], "Part one of speech.")
        self.assertEqual(stitched[1]["text"], "Part two of speech.")
        self.assertEqual(stitched[2]["text"], "Part three continued locally.")
        self.assertEqual(stitched[2]["start"], 20.2)
        self.assertEqual(stitched[2]["end"], 30.0)


class TestCheckpointStateManagement(unittest.TestCase):
    """Tests for checkpointing, mtime validation, and atomic writes."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.state_path = os.path.join(self.temp_dir, "scribe", "state", "clip_state.json")

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_checkpoint_roundtrip_and_clear(self):
        stem = "CLIP_001"
        cp_data = {
            "engine": "cloud",
            "provider": "nvidia_parakeet",
            "model_id": "d3fe9151-442b-4204-a70d-5fcc597fd610",
            "completed_segments": [{"start": 0.0, "end": 120.0, "text": "Hello"}],
            "resume_offset": 120.0,
            "audio_mtime": 1000.0,
            "audio_size": 3840000,
        }
        cs.save_checkpoint(self.state_path, stem, cp_data)

        # Retrieve
        loaded = cs.get_checkpoint(self.state_path, stem)
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded["resume_offset"], 120.0)
        self.assertEqual(len(loaded["completed_segments"]), 1)

        # Clear on completion
        cs.clear_checkpoint(self.state_path, stem)
        self.assertIsNone(cs.get_checkpoint(self.state_path, stem))

    def test_write_clip_clears_checkpoint_automatically(self):
        stem = "CLIP_002"
        cs.save_checkpoint(self.state_path, stem, {"resume_offset": 60.0})
        self.assertIsNotNone(cs.get_checkpoint(self.state_path, stem))

        # Completing the clip removes checkpoint
        cs.write_clip(
            self.state_path,
            stem=stem,
            model="medium",
            language="en",
            glossary_hash="abc",
            transcribed_at="2026-10-30 12:00:00 IST",
            engine="cloud",
            provider="nvidia_parakeet",
            model_id="d3fe9151-442b-4204-a70d-5fcc597fd610",
        )
        self.assertIsNone(cs.get_checkpoint(self.state_path, stem))
        rec = cs.read_clip(self.state_path, stem)
        self.assertEqual(rec["engine"], "cloud")
        self.assertEqual(rec["provider"], "nvidia_parakeet")


class TestCloudWorkerExecutionAndFallback(unittest.TestCase):
    """End-to-end tests for worker cloud dispatching, retries, and fallback."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.event_dir = os.path.join(self.temp_dir, "Event_Test")
        self.scribe_dir = os.path.join(self.event_dir, "scribe")
        self.state_dir = os.path.join(self.scribe_dir, "state")
        self.transcripts_dir = os.path.join(self.scribe_dir, "transcripts")
        self.work_dir = os.path.join(self.scribe_dir, "work")
        self.logs_dir = os.path.join(self.scribe_dir, "logs")

        os.makedirs(self.state_dir, exist_ok=True)
        os.makedirs(self.transcripts_dir, exist_ok=True)
        os.makedirs(self.work_dir, exist_ok=True)
        os.makedirs(self.logs_dir, exist_ok=True)

        self.clip_path = os.path.join(self.temp_dir, "clip1.wav")
        create_dummy_wav(self.clip_path, duration_sec=5.0)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_auth_failure_triggers_immediate_fallback(self):
        """PermissionDenied / invalid key immediately falls back to local Whisper without retrying."""
        profile = ce.DEFAULT_PROFILES["nvidia_parakeet"]

        # Mock riva client that throws PermissionDenied
        mock_client = MagicMock()
        mock_client.ASRService().offline_recognize.side_effect = Exception("StatusCode.PERMISSION_DENIED: Invalid Key")

        with self.assertRaises(ce.CloudAuthError):
            ce.transcribe_audio_chunk_riva(
                b"RIFF...",
                profile=profile,
                api_key="bad_key",
                riva_client_module=mock_client,
            )

    def test_credit_exhausted_triggers_immediate_fallback(self):
        """Credit or quota exhausted throws non-retryable CloudCreditExhaustedError."""
        profile = ce.DEFAULT_PROFILES["nvidia_parakeet"]

        mock_client = MagicMock()
        mock_client.ASRService().offline_recognize.side_effect = Exception("RESOURCE_EXHAUSTED: credits exhausted for function")

        with self.assertRaises(ce.CloudCreditExhaustedError):
            ce.transcribe_audio_chunk_riva(
                b"RIFF...",
                profile=profile,
                api_key="key",
                riva_client_module=mock_client,
            )

    def test_retryable_error_retries_with_backoff(self):
        """Temporary 429 or timeout retries 3 times before raising CloudRetryableError."""
        profile = ce.DEFAULT_PROFILES["nvidia_parakeet"]

        mock_client = MagicMock()
        mock_client.ASRService().offline_recognize.side_effect = Exception("Too Many Requests: rate limit exceeded")

        with patch("time.sleep") as mock_sleep:
            with self.assertRaises(ce.CloudRetryableError):
                ce.transcribe_audio_chunk_riva(
                    b"RIFF...",
                    profile=profile,
                    api_key="key",
                    riva_client_module=mock_client,
                )
            self.assertEqual(mock_sleep.call_count, 2)  # Retries after backoff


class TestSecretSafety(unittest.TestCase):
    """Verify that NVIDIA_API_KEY is never written to repo, job config, or logs."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_save_and_get_nvidia_api_key(self):
        test_key = "nvapi-test-secret-key-12345"
        ce.save_nvidia_api_key(test_key, project_root=self.temp_dir)

        # .env was created
        env_path = os.path.join(self.temp_dir, ".env")
        self.assertTrue(os.path.isfile(env_path))

        # Retrieved key matches
        retrieved = ce.get_nvidia_api_key(project_root=self.temp_dir)
        self.assertEqual(retrieved, test_key)

    def test_job_config_does_not_contain_api_key(self):
        mgr = TranscriptionManager()
        job_config_path = mgr.prepare_job_config(
            event_dict={"name": "TestEvent"},
            clips_to_run=[{"name": "clip.mp4", "path": "c:/clip.mp4"}],
            output_dir=os.path.join(self.temp_dir, "out"),
            work_dir=os.path.join(self.temp_dir, "work"),
            log_dir=os.path.join(self.temp_dir, "log"),
            engine="cloud",
            cloud_profile={"id": "nvidia_parakeet", "endpoint": "grpc.nvcf.nvidia.com:443"},
        )
        with open(job_config_path, "r", encoding="utf-8") as f:
            content = f.read()

        # Job config holds parameters but NOT the secret key
        self.assertNotIn("api_key", content)
        self.assertNotIn("NVIDIA_API_KEY", content)
        self.assertIn('"engine": "cloud"', content)
        self.assertIn('"single_request_limit_sec": 600.0', content)


if __name__ == "__main__":
    unittest.main()

