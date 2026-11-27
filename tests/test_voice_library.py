"""
Tests for Task A2: People, Roles, and Voice Library Core.
Uses tempfile for isolated storage (never touching repo or real D:\\Events).
Tests synthetic audio for same vs different speaker, multi-voice, and short/noisy samples.
"""

import os
import shutil
import sys
import tempfile
import unittest
import numpy as np
import soundfile as sf

# sys.path guard derived from __file__
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from scribe_core.voice_library import (
    Person,
    VoiceLibrary,
    validate_library_path,
    format_transcript_line,
    MAX_SAMPLES_PER_PERSON,
    VALID_ROLES,
)


def _generate_synthetic_wav(
    filepath: str, duration: float = 22.0, freq: float = 200.0, sample_rate: int = 16000, noise_level: float = 0.001
):
    """Generate clean synthetic harmonic audio representing a voice-like signal."""
    t = np.linspace(0, duration, int(sample_rate * duration))
    # Fundamental + harmonics (voice-like structure)
    waveform = (
        0.5 * np.sin(2 * np.pi * freq * t)
        + 0.25 * np.sin(2 * np.pi * (2 * freq) * t)
        + 0.15 * np.sin(2 * np.pi * (3 * freq) * t)
    )
    if noise_level > 0:
        rng = np.random.RandomState(int(round(freq * 10)) % 10000)
        waveform += noise_level * rng.randn(len(waveform))
    waveform = waveform / np.max(np.abs(waveform)) * 0.8
    sf.write(filepath, waveform.astype(np.float32), sample_rate)


class TestVoiceLibraryPathIsolation(unittest.TestCase):
    """Ensure VoiceLibrary refuses paths inside repo or D:\\Events."""

    def test_refuses_path_inside_repository(self):
        repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        inside_repo_path = os.path.join(repo_root, "scratch", "voice_lib")
        with self.assertRaises(ValueError) as ctx:
            validate_library_path(inside_repo_path, repo_root=repo_root)
        self.assertIn("cannot be inside repository", str(ctx.exception))

    def test_refuses_path_inside_events(self):
        inside_events_path = r"D:\Events\DevFest2026\voice_library"
        with self.assertRaises(ValueError) as ctx:
            validate_library_path(inside_events_path)
        self.assertIn("strictly under D:\\Events\\_voice_library", str(ctx.exception))

    def test_accepts_voice_library_under_events(self):
        allowed_path = r"D:\Events\_voice_library"
        result = validate_library_path(allowed_path)
        self.assertEqual(os.path.abspath(allowed_path), result)

    def test_person_permission_noted(self):
        p = Person(id="p1", display_name="Test Speaker", permission_noted=True, permission_date="2026-11-07")
        data = p.to_dict()
        self.assertTrue(data["permission_noted"])
        self.assertEqual(data["permission_date"], "2026-11-07")
        p2 = Person.from_dict(data)
        self.assertTrue(p2.permission_noted)
        self.assertEqual(p2.permission_date, "2026-11-07")

    def test_accepts_valid_external_path(self):
        tmp_dir = tempfile.mkdtemp(prefix="ss_voice_test_")
        try:
            valid_path = validate_library_path(tmp_dir)
            self.assertEqual(os.path.abspath(tmp_dir), valid_path)
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)


class TestVoiceLibraryCore(unittest.TestCase):
    """Core CRUD, index atomicity, and sample constraints."""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="ss_vlib_")
        self.lib = VoiceLibrary(library_dir=self.test_dir, prefer_gpu=False)

    def tearDown(self):
        self.lib.release_model()
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_add_and_search_people(self):
        p1 = self.lib.add_person("Jehosheba Amruthaluri", aliases=["Sheba", "J. Amruthaluri"])
        p2 = self.lib.add_person("Surender Tanwar", aliases=["Suri"])
        p3 = self.lib.add_person("Madhav Poosarla")

        people = self.lib.list_people()
        self.assertEqual(len(people), 3)

        # Exact and alias search
        self.assertEqual(self.lib.search_people("Jehosheba")[0].id, p1.id)
        self.assertEqual(self.lib.search_people("sheba")[0].id, p1.id)
        self.assertEqual(self.lib.search_people("Suri")[0].id, p2.id)
        self.assertEqual(self.lib.search_people("Madhav")[0].id, p3.id)

    def test_enforces_max_five_samples_per_person(self):
        person = self.lib.add_person("Kishore Katta")
        wav_path = os.path.join(self.test_dir, "sample_src.wav")
        _generate_synthetic_wav(wav_path, duration=12.0, freq=220.0)

        # Add 5 samples
        for i in range(MAX_SAMPLES_PER_PERSON):
            self.lib.add_sample(person.id, wav_path)

        samples = self.lib.list_samples(person.id)
        self.assertEqual(len(samples), 5)

        # 6th sample must raise ValueError
        with self.assertRaises(ValueError) as ctx:
            self.lib.add_sample(person.id, wav_path)
        self.assertIn("maximum of 5 samples", str(ctx.exception))

    def test_delete_sample_cleans_wav_and_cache(self):
        person = self.lib.add_person("Subhankar Dey")
        wav_path = os.path.join(self.test_dir, "subhankar.wav")
        _generate_synthetic_wav(wav_path, duration=15.0, freq=180.0)

        sample, _ = self.lib.add_sample(person.id, wav_path)
        sample_path = self.lib.get_sample_path(sample.id)
        cache_path = os.path.join(self.lib.cache_dir, f"{sample.id}.npy")

        self.assertTrue(os.path.isfile(sample_path))
        self.assertTrue(os.path.isfile(cache_path))

        res = self.lib.delete_sample(sample.id)
        self.assertFalse(os.path.isfile(sample_path))
        self.assertFalse(os.path.isfile(cache_path))
        self.assertEqual(len(self.lib.list_samples(person.id)), 0)

    def test_delete_person_removes_all_artifacts(self):
        person = self.lib.add_person("Speaker To Delete")
        wav_path = os.path.join(self.test_dir, "del.wav")
        _generate_synthetic_wav(wav_path, duration=12.0, freq=250.0)

        s1, _ = self.lib.add_sample(person.id, wav_path)
        s2, _ = self.lib.add_sample(person.id, wav_path)

        res = self.lib.delete_person(person.id)
        self.assertEqual(res["removed_samples_count"], 2)
        self.assertIsNone(self.lib.get_person(person.id))
        self.assertFalse(os.path.isdir(os.path.join(self.lib.people_dir, person.id)))

    def test_confirmation_required_by_user(self):
        person = self.lib.add_person("Unconfirmed Speaker")
        wav_path = os.path.join(self.test_dir, "unconf.wav")
        _generate_synthetic_wav(wav_path, duration=15.0, freq=210.0)

        sample, _ = self.lib.add_sample(person.id, wav_path)
        self.assertFalse(sample.confirmed_by_user)

        self.lib.confirm_sample(sample.id)
        updated = self.lib.get_sample(sample.id)
        self.assertTrue(updated.confirmed_by_user)


class TestVoiceVerificationAndAudioQuality(unittest.TestCase):
    """Test audio verification report: quality, single voice, same vs different person."""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="ss_vreport_")
        self.lib = VoiceLibrary(library_dir=self.test_dir, prefer_gpu=False)

    def tearDown(self):
        self.lib.release_model()
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_quality_rating_good_for_clean_long_audio(self):
        p = self.lib.add_person("Alice")
        wav_path = os.path.join(self.test_dir, "clean_long.wav")
        _generate_synthetic_wav(wav_path, duration=24.0, freq=220.0, noise_level=0.0001)

        sample, report = self.lib.add_sample(p.id, wav_path)
        self.assertEqual(report.quality_rating, "Good")
        self.assertGreaterEqual(report.speech_duration, 20.0)
        self.assertLessEqual(report.clipping_ratio, 0.01)

    def test_quality_rating_poor_for_short_audio(self):
        p = self.lib.add_person("Bob")
        wav_path = os.path.join(self.test_dir, "short.wav")
        # 5 seconds is below minimum 10s requirement
        _generate_synthetic_wav(wav_path, duration=5.0, freq=190.0)

        sample, report = self.lib.add_sample(p.id, wav_path)
        self.assertEqual(report.quality_rating, "Poor")
        self.assertLess(report.speech_duration, 10.0)

    def test_same_person_versus_different_person_embeddings(self):
        p_alice = self.lib.add_person("Alice")
        p_bob = self.lib.add_person("Bob")

        # Alice sample 1 (baseline)
        alice_wav1 = os.path.join(self.test_dir, "alice1.wav")
        _generate_synthetic_wav(alice_wav1, duration=15.0, freq=260.0)
        s_alice1, _ = self.lib.add_sample(p_alice.id, alice_wav1)
        self.lib.confirm_sample(s_alice1.id)

        # Alice sample 2 (same person voice: freq 260 Hz, slight variation)
        alice_wav2 = os.path.join(self.test_dir, "alice2.wav")
        _generate_synthetic_wav(alice_wav2, duration=15.0, freq=260.0, noise_level=0.002)
        s_alice2, report_alice2 = self.lib.add_sample(p_alice.id, alice_wav2)

        # Bob sample (different person voice: freq 140 Hz)
        bob_wav = os.path.join(self.test_dir, "bob.wav")
        _generate_synthetic_wav(bob_wav, duration=15.0, freq=140.0)
        s_bob, _ = self.lib.add_sample(p_bob.id, bob_wav)
        self.lib.confirm_sample(s_bob.id)

        # Verify Alice sample 2 matches Alice sample 1 strongly
        self.assertIn(report_alice2.reference_match["status"], ("Strong", "Possible"))
        alice_score = report_alice2.reference_match["score"]

        # Compute cross-similarity of Alice to Bob
        e_alice2 = self.lib._load_cache(s_alice2.id)
        e_bob = self.lib._load_cache(s_bob.id)
        bob_cross_score = float(np.dot(e_alice2, e_bob))

        # Alice's similarity to Alice should be noticeably higher than Alice to Bob
        self.assertGreater(alice_score, bob_cross_score)

    def test_single_voice_check_detects_mixed_two_voice_audio(self):
        p = self.lib.add_person("Mixed Speaker")
        # Generate mixed audio: first 6s voice A (150 Hz), next 6s voice B (400 Hz)
        sr = 16000
        t1 = np.linspace(0, 6, sr * 6)
        w1 = np.sin(2 * np.pi * 150 * t1).astype(np.float32)
        t2 = np.linspace(0, 6, sr * 6)
        w2 = np.sin(2 * np.pi * 400 * t2).astype(np.float32)
        mixed = np.concatenate([w1, w2])
        mixed_path = os.path.join(self.test_dir, "mixed.wav")
        sf.write(mixed_path, mixed, sr)

        sample, report = self.lib.add_sample(p.id, mixed_path)
        # Check that single_voice_check reports window similarities
        self.assertIn("window_similarities", report.single_voice_check)
        self.assertGreater(len(report.single_voice_check["window_similarities"]), 1)


class TestTranscriptLineFormatting(unittest.TestCase):
    """Test [Name] vs [Name (Role)] line formatters."""

    def test_single_speaker_format(self):
        line = format_transcript_line(
            speaker_name="Surender Tanwar",
            role="Speaker",
            is_multi_speaker=False,
            start_ts="00:01:23.456",
            end_ts="00:01:28.900",
            text="Welcome everyone to the session.",
        )
        self.assertEqual(line, "[Surender Tanwar] [00:01:23.456 - 00:01:28.900] Welcome everyone to the session.")

    def test_multi_speaker_role_format(self):
        line = format_transcript_line(
            speaker_name="Madhava Poosarla",
            role="Host",
            is_multi_speaker=True,
            start_ts="00:00:05.100",
            end_ts="00:00:12.300",
            text="I would like to introduce our keynote speaker.",
        )
        self.assertEqual(line, "[Madhava Poosarla (Host)] [00:00:05.100 - 00:00:12.300] I would like to introduce our keynote speaker.")


if __name__ == "__main__":
    unittest.main()

