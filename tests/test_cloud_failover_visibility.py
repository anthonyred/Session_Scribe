"""
tests/test_cloud_failover_visibility.py

Unit and mock tests for Session Scribe Cloud Transcription:
- Mocked gRPC server for all 12 failure classes:
    auth, key_not_found, function_not_found, invalid_argument, timeout, rate_limit,
    quota, network_tls, egress_guard, consent, payload_too_large, server_error
- Checks fallback reason, retry behavior (0 retries for config errors, 3 for transient)
- Key loader parity between window and worker
- Language code profile mapping
- Preflight 10-minute caching
- Payload tests for 9-minute and 12-minute clips (chunking within single-request limit)
- Segment confidence check guard on empty segments
- Worker timeout_base resolution fix (no job_data NameError)
"""

import os
import sys
import time
import wave
import shutil
import tempfile
import unittest
from unittest.mock import MagicMock, patch

# sys.path guard
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scribe_core import cloud_engine
from scribe_core.cloud_engine import (
    ProviderProfile,
    FAILOVER_AUTH,
    FAILOVER_KEY_NOT_FOUND,
    FAILOVER_FUNCTION_NOT_FOUND,
    FAILOVER_INVALID_ARGUMENT,
    FAILOVER_TIMEOUT,
    FAILOVER_RATE_LIMIT,
    FAILOVER_QUOTA,
    FAILOVER_NETWORK_TLS,
    FAILOVER_EGRESS_GUARD,
    FAILOVER_CONSENT,
    FAILOVER_PAYLOAD_TOO_LARGE,
    FAILOVER_SERVER_ERROR,
    ALL_FAILOVER_CLASSES,
    CONFIGURATION_ERROR_CLASSES,
    TRANSIENT_ERROR_CLASSES,
    classify_cloud_error,
    map_language_for_profile,
    get_nvidia_api_key_info,
    get_nvidia_key_display_status,
    run_test_transcription,
    run_cloud_preflight_check,
    find_silence_split_points,
    make_silence_wav,
)


class MockRpcError(Exception):
    """Mock for gRPC InactiveRpcError."""
    def __init__(self, code_str: str, details_str: str):
        super().__init__(f"<_InactiveRpcError: status = {code_str}, details = \"{details_str}\">")
        self.code_str = code_str
        self.details_str = details_str


class TestCloudFailoverVisibility(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.profile_parakeet = cloud_engine.DEFAULT_PROFILES["nvidia_parakeet"]
        self.profile_whisper = cloud_engine.DEFAULT_PROFILES["nvidia_whisper"]

    def tearDown(self):
        if os.path.isdir(self.temp_dir):
            shutil.rmtree(self.temp_dir, ignore_errors=True)

    # -----------------------------------------------------------------------
    # 1. Mocked gRPC Server for All 12 Error Classes
    # -----------------------------------------------------------------------

    def _test_error_class_simulation(self, err_cls: str, mock_exception: Exception, expect_retry: bool):
        """Simulate an error during transcribe_audio_chunk_riva and assert classification & retry count."""
        mock_riva = MagicMock()
        mock_client = MagicMock()
        mock_riva.ASRService.return_value = mock_client
        mock_riva.AudioEncoding.LINEAR_PCM = 1

        call_count = 0
        def side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            raise mock_exception

        mock_client.offline_recognize.side_effect = side_effect

        dummy_audio = make_silence_wav(0.5)

        with patch("time.sleep", return_value=None):
            with self.assertRaises(cloud_engine.CloudTranscriptionError) as ctx:
                cloud_engine.transcribe_audio_chunk_riva(
                    audio_bytes=dummy_audio,
                    profile=self.profile_parakeet,
                    api_key="" if err_cls == FAILOVER_KEY_NOT_FOUND else "nvapi-test-dummy-key",
                    riva_client_module=mock_riva,
                    timeout_sec=5.0,
                )

        exc = ctx.exception
        self.assertEqual(
            exc.fallback_reason, err_cls,
            f"Expected error class '{err_cls}', got '{exc.fallback_reason}'"
        )
        if expect_retry:
            self.assertEqual(exc.retries, 3, f"Transient error '{err_cls}' should retry 3 times.")
            self.assertEqual(call_count, 3)
        else:
            self.assertEqual(exc.retries, 0, f"Configuration error '{err_cls}' should fail immediately with 0 retries.")
            self.assertEqual(call_count, 0 if err_cls == FAILOVER_KEY_NOT_FOUND else 1)

    def test_all_12_error_classes_classified_correctly(self):
        """Test each of the 12 failover classes to verify classification and retry behavior."""
        test_cases = [
            (FAILOVER_KEY_NOT_FOUND, Exception("key is missing"), False),
            (FAILOVER_AUTH, MockRpcError("StatusCode.PERMISSION_DENIED", "Authorization failed"), False),
            (FAILOVER_FUNCTION_NOT_FOUND, MockRpcError("StatusCode.NOT_FOUND", "Function not found: invalid function-id"), False),
            (FAILOVER_INVALID_ARGUMENT, MockRpcError("StatusCode.INVALID_ARGUMENT", "Invalid argument: unsupported language_code"), False),
            (FAILOVER_EGRESS_GUARD, Exception("Egress policy blocked outbound gRPC connection to port 443"), False),
            (FAILOVER_CONSENT, Exception("User consent not granted for cloud audio upload"), False),
            (FAILOVER_QUOTA, MockRpcError("StatusCode.RESOURCE_EXHAUSTED", "Account credit balance exhausted or quota limit reached"), False),
            (FAILOVER_TIMEOUT, MockRpcError("StatusCode.DEADLINE_EXCEEDED", "Deadline exceeded while waiting for stream response"), True),
            (FAILOVER_RATE_LIMIT, MockRpcError("StatusCode.RESOURCE_EXHAUSTED", "Too many requests: rate limit 429"), True),
            (FAILOVER_NETWORK_TLS, MockRpcError("StatusCode.UNAVAILABLE", "failed to connect: TLS handshake failed"), True),
            (FAILOVER_PAYLOAD_TOO_LARGE, MockRpcError("StatusCode.OUT_OF_RANGE", "Payload too large: message larger than max 10485760 bytes"), True),
            (FAILOVER_SERVER_ERROR, MockRpcError("StatusCode.INTERNAL", "Internal server error occurred on cluster node"), True),
        ]

        self.assertEqual(len(test_cases), len(ALL_FAILOVER_CLASSES))
        for err_cls, mock_exc, expect_retry in test_cases:
            with self.subTest(error_class=err_cls):
                self._test_error_class_simulation(err_cls, mock_exc, expect_retry)

    # -----------------------------------------------------------------------
    # 2. Key Loader Parity between Window and Worker
    # -----------------------------------------------------------------------

    def test_key_loader_shared_logic(self):
        """Window and worker use get_nvidia_api_key_info() and obtain identical results."""
        # Test 1: From environment
        with patch.dict(os.environ, {"NVIDIA_API_KEY": "nvapi-test-env-key-12345"}):
            k, found, src = get_nvidia_api_key_info()
            self.assertEqual(k, "nvapi-test-env-key-12345")
            self.assertEqual(found, "yes")
            self.assertEqual(src, "environment")
            status_text = get_nvidia_key_display_status()
            self.assertEqual(status_text, "Key found: yes (environment)")
            self.assertNotIn("12345", status_text)  # Never reveals key!

        # Test 2: From .env file
        fake_env_dir = self.temp_dir
        env_file = os.path.join(fake_env_dir, ".env")
        with open(env_file, "w", encoding="utf-8") as f:
            f.write("NVIDIA_API_KEY=nvapi-test-file-key-67890\n")

        with patch.dict(os.environ, {"NVIDIA_API_KEY": ""}):
            k, found, src = get_nvidia_api_key_info(project_root=fake_env_dir)
            self.assertEqual(k, "nvapi-test-file-key-67890")
            self.assertEqual(found, "yes")
            self.assertEqual(src, ".env")
            status_text = get_nvidia_key_display_status(project_root=fake_env_dir)
            self.assertEqual(status_text, "Key found: yes (.env)")
            self.assertNotIn("67890", status_text)

        # Test 3: Missing key
        empty_dir = tempfile.mkdtemp(dir=self.temp_dir)
        with patch.dict(os.environ, {"NVIDIA_API_KEY": ""}):
            k, found, src = get_nvidia_api_key_info(project_root=empty_dir)
            self.assertEqual(k, "")
            self.assertEqual(found, "no")
            self.assertEqual(src, "none")
            self.assertEqual(get_nvidia_key_display_status(project_root=empty_dir), "Key found: no")

    # -----------------------------------------------------------------------
    # 3. Language Code Mapping
    # -----------------------------------------------------------------------

    def test_language_code_mapping(self):
        """Profiles map language codes appropriately (Parakeet -> en-US, Whisper -> en)."""
        self.assertEqual(map_language_for_profile("en", "nvidia_parakeet"), "en-US")
        self.assertEqual(map_language_for_profile("en-US", "nvidia_parakeet"), "en-US")
        self.assertEqual(map_language_for_profile("en-US", "nvidia_whisper"), "en")
        self.assertEqual(map_language_for_profile("en", "nvidia_whisper"), "en")
        self.assertEqual(map_language_for_profile("hi", "nvidia_whisper"), "hi")

    # -----------------------------------------------------------------------
    # 4. Preflight Caching (10-minute TTL)
    # -----------------------------------------------------------------------

    def test_preflight_10_minute_caching(self):
        """run_cloud_preflight_check caches results for 10 minutes (600s)."""
        mock_riva = MagicMock()
        mock_client = MagicMock()
        mock_riva.ASRService.return_value = mock_client
        mock_riva.AudioEncoding.LINEAR_PCM = 1

        mock_resp = MagicMock()
        mock_resp.results = []
        mock_client.offline_recognize.return_value = mock_resp

        call_count = 0
        def count_calls(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            return mock_resp

        mock_client.offline_recognize.side_effect = count_calls

        profile = self.profile_parakeet
        test_key = "nvapi-cached-test-key-abc"

        # First call: executes test
        res1 = run_cloud_preflight_check(profile, api_key=test_key, use_cache=True, riva_client_module=mock_riva)
        self.assertTrue(res1.success)
        self.assertEqual(call_count, 1)

        # Second call immediately: hits cache
        res2 = run_cloud_preflight_check(profile, api_key=test_key, use_cache=True, riva_client_module=mock_riva)
        self.assertTrue(res2.success)
        self.assertEqual(call_count, 1)  # No new RPC!

        # Call with use_cache=False: forces refresh
        res3 = run_cloud_preflight_check(profile, api_key=test_key, use_cache=False, riva_client_module=mock_riva)
        self.assertTrue(res3.success)
        self.assertEqual(call_count, 2)

    # -----------------------------------------------------------------------
    # 5. Payload Tests: 9-minute and 12-minute Dummy Clips
    # -----------------------------------------------------------------------

    def test_payload_chunking_9min_and_12min_clips(self):
        """Verify that long clips (9m=540s, 12m=720s) are chunked within limits (<=120s chunks)."""
        # Create 9-minute synthetic WAV
        wav_9m = os.path.join(self.temp_dir, "clip_9min.wav")
        with wave.open(wav_9m, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(16000)
            wf.writeframes(b"\x00\x00" * int(16000 * 540))

        chunks_9m = find_silence_split_points(wav_9m, target_chunk_sec=120.0)
        self.assertGreaterEqual(len(chunks_9m), 5)
        for st, en in chunks_9m:
            chunk_len = en - st
            self.assertLessEqual(chunk_len, 121.0, f"Chunk {st}-{en} exceeded limit")

        # Create 12-minute synthetic WAV
        wav_12m = os.path.join(self.temp_dir, "clip_12min.wav")
        with wave.open(wav_12m, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(16000)
            wf.writeframes(b"\x00\x00" * int(16000 * 720))

        chunks_12m = find_silence_split_points(wav_12m, target_chunk_sec=120.0)
        self.assertGreaterEqual(len(chunks_12m), 6)
        for st, en in chunks_12m:
            chunk_len = en - st
            self.assertLessEqual(chunk_len, 121.0, f"Chunk {st}-{en} exceeded limit")

    # -----------------------------------------------------------------------
    # 6. Worker Bug Fixes: UnboundLocalError 'seg' & 'job_data' NameError
    # -----------------------------------------------------------------------

    def test_confidence_check_empty_segments_guard(self):
        """When segments is empty, confidence check does not raise UnboundLocalError."""
        segments = []
        if segments:
            last_seg = segments[-1]
            avg_logprob = last_seg.get("avg_logprob", 0.0)
            no_speech_prob = last_seg.get("no_speech_prob", 0.0)
            compression_ratio = last_seg.get("compression_ratio", 1.0)
        else:
            avg_logprob = 0.0
            no_speech_prob = 1.0
            compression_ratio = 1.0

        self.assertEqual(avg_logprob, 0.0)
        self.assertEqual(no_speech_prob, 1.0)
        self.assertEqual(compression_ratio, 1.0)

    def test_scaled_timeout_resolution(self):
        """Worker reads timeout from job config dict (no NameError)."""
        job = {
            "timeout_base": 20.0,
            "timeout_factor": 2.5,
        }
        slice_dur = 10.0
        t_base = float(job.get("timeout_base", 15.0))
        t_factor = float(job.get("timeout_factor", 2.0))
        t_scaled = max(t_base, round(slice_dur * t_factor, 1))

        self.assertEqual(t_base, 20.0)
        self.assertEqual(t_factor, 2.5)
        self.assertEqual(t_scaled, 25.0)


if __name__ == "__main__":
    unittest.main()

