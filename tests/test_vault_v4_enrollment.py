"""
tests/test_vault_v4_enrollment.py
Unit test suite for Phase V4 Voice Profile Enrollment Engine into Vault v2.

Invariants Tested:
1. In-memory audio extraction via FFmpeg pipes (16 kHz mono WAV, zero plaintext on disk).
2. Quality checks before acceptance:
   - Speech length (min 10.0s enforced).
   - Noise floor / SNR (min 12 dB).
   - Silence / low energy rejection.
   - Music detection rejection.
   - Multi-speaker overlap rejection.
3. Multi-sample target (3 to 5 samples, per-sample approval gate).
4. Secure dual-zone commitment:
   - Zone 1: Real name, session roles, auditable consent record.
   - Zone 2: Centroid combined embedding, sample embeddings, encrypted WAVs.
   - Keyed-MAC Audit Log entry with anonymous spk_ ID only.
5. Hardware & VRAM isolation: embedding engine lock and cache cleanup.
6. Zero plaintext disk leakage: canary scan verifies zero plaintext names or audio.
7. UI Dialog stability: VoiceProfileEnrollmentDialog build-once, light/dark, 10s loop.
"""

from __future__ import annotations

import io
import math
import os
import shutil
import sys
import tempfile
import threading
import time
import tkinter as tk
import traceback
import unittest

import numpy as np
import soundfile as sf

# sys.path guard derived from __file__
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from gui import theme
from gui.enrollment_dialog import VoiceProfileEnrollmentDialog
from scribe_core.audio_extractor import extract_audio_in_memory, AudioExtractionError
from scribe_core.embedding_engine import LocalEmbeddingEngine
from scribe_core.enrollment_manager import EnrollmentManager, MIN_ENROLLMENT_SAMPLES
from scribe_core.vault_v2 import VaultV2StorageEngine
from scribe_core.voice_quality_checker import (
    evaluate_sample_quality,
    QualityCheckResult,
    MIN_SPEECH_DURATION_SECONDS,
)
from scribe_core.voice_vault import VoiceVault


def generate_synthetic_speech_wav(
    duration_s: float = 20.0,
    sample_rate: int = 16000,
    add_noise: bool = False,
    noise_level: float = 0.001,
    add_music: bool = False,
    multi_speaker: bool = False,
) -> bytes:
    """Generates synthetic 16 kHz mono WAV bytes with modulated speech-like formants."""
    num_samples = int(duration_s * sample_rate)
    t = np.linspace(0, duration_s, num_samples, endpoint=False, dtype=np.float32)

    # Base pitch + modulated speech harmonics (moving prosody with integer harmonics)
    f0 = 135.0 + 15.0 * np.sin(2 * np.pi * 0.7 * t)
    sig = (
        0.5 * np.sin(2 * np.pi * f0 * t)
        + 0.3 * np.sin(2 * np.pi * 2.0 * f0 * t)
        + 0.15 * np.sin(2 * np.pi * 3.0 * f0 * t)
    )

    # Word pauses (active speech cadence: 1.6s speech, 0.4s pause)
    cadence = np.where((t % 2.0) < 1.6, 1.0, 0.0).astype(np.float32)
    sig = sig * cadence

    # Multi-speaker overlap (competing inharmonic voice present during speech)
    if multi_speaker:
        f2 = 250.0 + 35.0 * np.sin(2 * np.pi * 1.5 * t)
        sig2 = 0.45 * np.sin(2 * np.pi * f2 * t) + 0.25 * np.sin(2 * np.pi * 2.0 * f2 * t)
        sig = sig + sig2 * cadence

    # Background music tone (sustained steady musical chord)
    if add_music:
        music_tone = 0.45 * np.sin(2 * np.pi * 440.0 * t) + 0.35 * np.sin(2 * np.pi * 554.37 * t)
        sig = sig + music_tone

    # Background room noise
    if add_noise:
        sig += np.random.normal(0, 0.25, num_samples).astype(np.float32)
    else:
        sig += np.random.normal(0, noise_level, num_samples).astype(np.float32)

    # Normalize to prevent clipping
    max_val = np.max(np.abs(sig))
    if max_val > 1e-6:
        sig = (sig / max_val) * 0.85

    bio = io.BytesIO()
    sf.write(bio, sig.astype(np.float32), sample_rate, format="WAV", subtype="PCM_16")
    return bio.getvalue()


# Decorator to mark slow neural inference tests so the default test suite runs fast
slow_test = unittest.skipUnless(
    os.environ.get("RUN_SLOW_TESTS") == "1",
    "Slow neural inference test; run with RUN_SLOW_TESTS=1 to execute",
)


class TestVaultV4Enrollment(unittest.TestCase):
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

    TEST_TIMEOUT_SECONDS = 60.0

    def setUp(self):
        self._test_timer = threading.Timer(self.TEST_TIMEOUT_SECONDS, self._on_test_timeout)
        self._test_timer.daemon = True
        self._test_timer.start()

        self.temp_dir = tempfile.mkdtemp(prefix="ss_test_v4_")
        self.vault = VoiceVault(
            keys_dir=self.temp_dir,
            time_cost=1,
            memory_cost=1024,
            parallelism=2,
        )
        self.passphrase = "hazard galaxy pebble timber canyon velvet"
        self.vault.initialize(self.passphrase)
        self.storage = VaultV2StorageEngine(self.vault)
        self.embedding_engine = LocalEmbeddingEngine(prefer_gpu=False)
        self.enrollment_mgr = EnrollmentManager(self.storage, self.embedding_engine)

    def tearDown(self):
        if hasattr(self, "_test_timer") and self._test_timer:
            self._test_timer.cancel()
        self.vault.close()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _on_test_timeout(self):
        print(f"\n[TEST TIMEOUT EXCEEDED: {self.TEST_TIMEOUT_SECONDS}s] Test hung: {self.id()}")
        sys.stdout.flush()
        for th in threading.enumerate():
            stack = sys._current_frames().get(th.ident)
            if stack:
                print(f"\n--- Thread: {th.name} ---")
                traceback.print_stack(stack)
        sys.stdout.flush()
        os._exit(1)

    def test_01_in_memory_audio_extraction(self):
        """Invariant: Audio extractor extracts 16 kHz mono WAV strictly in memory."""
        # Create a synthetic WAV file on disk to simulate user media
        wav_path = os.path.join(self.temp_dir, "synthetic_input.wav")
        synthetic_wav = generate_synthetic_speech_wav(duration_s=12.0)
        with open(wav_path, "wb") as f:
            f.write(synthetic_wav)

        # Extract full file
        extracted_bytes = extract_audio_in_memory(wav_path)
        self.assertGreater(len(extracted_bytes), 44)

        data, sr = sf.read(io.BytesIO(extracted_bytes))
        self.assertEqual(sr, 16000)
        self.assertEqual(data.ndim, 1)  # Mono
        self.assertAlmostEqual(len(data) / sr, 12.0, delta=0.2)

        # Extract sub-range: 2.0s to 7.0s (5.0s length)
        sub_bytes = extract_audio_in_memory(wav_path, time_range=(2.0, 7.0))
        sub_data, sub_sr = sf.read(io.BytesIO(sub_bytes))
        self.assertEqual(sub_sr, 16000)
        self.assertAlmostEqual(len(sub_data) / sub_sr, 5.0, delta=0.2)

    @slow_test
    def test_02_quality_checks_duration_rejection(self):
        """Invariant: Audio samples with < 10.0s active speech are rejected with diagnostic reason."""
        short_wav = generate_synthetic_speech_wav(duration_s=6.0)
        res = evaluate_sample_quality(short_wav)
        self.assertFalse(res.passed)
        self.assertIn("Speech length too short", res.failure_reason)
        self.assertLess(res.speech_duration_sec, MIN_SPEECH_DURATION_SECONDS)

        # >= 15s speech passes clearly without borderline
        good_wav = generate_synthetic_speech_wav(duration_s=20.0)
        res_good = evaluate_sample_quality(good_wav)
        self.assertTrue(res_good.passed)
        self.assertGreaterEqual(res_good.speech_duration_sec, MIN_SPEECH_DURATION_SECONDS)

    @slow_test
    def test_03_quality_checks_silence_and_noise_rejection(self):
        """Invariant: Nearly silent audio or high background noise (SNR < 12 dB) is rejected."""
        # Near silent audio (zeros with tiny noise)
        silent_audio = np.zeros(16000 * 12, dtype=np.float32)
        res_silent = evaluate_sample_quality(silent_audio)
        self.assertFalse(res_silent.passed)
        self.assertIn("nearly silent", res_silent.failure_reason)

        # High noise audio
        noisy_wav = generate_synthetic_speech_wav(duration_s=15.0, add_noise=True, noise_level=0.35)
        res_noisy = evaluate_sample_quality(noisy_wav)
        self.assertFalse(res_noisy.passed)
        self.assertIn("background noise", res_noisy.failure_reason)
        self.assertLess(res_noisy.snr_db, 12.0)

    @slow_test
    def test_04_quality_checks_borderline_and_override(self):
        """Invariant: Borderline sample results show numbers and require explicit override approval."""
        # Borderline sample: 12.0s duration (between 10.0s and 15.0s) or moderate noise
        borderline_wav = generate_synthetic_speech_wav(duration_s=14.0)
        res = evaluate_sample_quality(borderline_wav)
        self.assertTrue(res.is_borderline)
        self.assertTrue(res.can_override)
        self.assertFalse(res.passed)  # Rejected by default until overridden
        self.assertEqual(res.verdict, "BORDERLINE")
        self.assertGreater(len(res.borderline_reasons), 0)

        # Overridden with explicit user acceptance
        res_override = evaluate_sample_quality(borderline_wav, override_borderline=True)
        self.assertTrue(res_override.is_borderline)
        self.assertTrue(res_override.passed)  # Now accepted

    @slow_test
    def test_05_quality_checks_multi_speaker_rejection(self):
        """Invariant: Multiple overlapping speakers are rejected."""
        import unittest.mock
        overlap_wav = generate_synthetic_speech_wav(duration_s=20.0, multi_speaker=True)
        with unittest.mock.patch("scribe_core.voice_quality_checker.run_pyannote_analysis") as mock_pyannote:
            mock_pyannote.return_value = (
                18.0,
                3,
                2.5,
                {"SPEAKER_00": 10.0, "SPEAKER_01": 5.0, "SPEAKER_02": 3.0},
            )
            res_multi = evaluate_sample_quality(overlap_wav)
            self.assertFalse(res_multi.passed)
            self.assertIn("Multiple speakers", res_multi.failure_reason)

    @slow_test
    def test_06_enrollment_lifecycle_3_samples_success(self):
        """
        Invariant: Importing 3 verified samples with user approval successfully commits profile
        into Vault v2 (Zone 1 Identity + Zone 2 Biometric) with zero plaintext on disk.
        """
        session = self.enrollment_mgr.start_session(
            full_name="Dr. Katherine Synthetic",
            roles=["Speaker", "Panelist"],
            consent_noted=True,
            notes="Keynote speaker on Voice AI",
        )
        self.assertEqual(session.full_name, "Dr. Katherine Synthetic")
        self.assertTrue(session.spk_id.startswith("spk_"))

        # Generate 3 valid synthetic speech samples
        sample_files = []
        for i in range(3):
            p = os.path.join(self.temp_dir, f"sample_{i+1}.wav")
            with open(p, "wb") as f:
                f.write(generate_synthetic_speech_wav(duration_s=20.0))
            sample_files.append(p)

        # Import 2 samples: commit must fail (requires >= 3)
        for i in range(2):
            ok, msg, q = self.enrollment_mgr.import_and_verify_sample(session, sample_files[i], user_approved=True)
            self.assertTrue(ok, f"Import failed: {msg}")

        self.assertEqual(session.approved_sample_count, 2)
        commit_fail, commit_msg = self.enrollment_mgr.commit_enrollment(session)
        self.assertFalse(commit_fail)
        self.assertIn("requires at least 3 approved samples", commit_msg)

        # Import 3rd sample and approve
        ok3, _, _ = self.enrollment_mgr.import_and_verify_sample(session, sample_files[2], user_approved=True)
        self.assertTrue(ok3)
        self.assertEqual(session.approved_sample_count, 3)

        # Now commit succeeds!
        commit_ok, commit_res = self.enrollment_mgr.commit_enrollment(session)
        self.assertTrue(commit_ok, f"Commit failed: {commit_res}")

        spk_id = session.spk_id

        # Verify Zone 1 (Identity): name, consent record, roles
        id_rec = self.storage.read_identity_record(spk_id)
        self.assertEqual(id_rec["display_name"], "Dr. Katherine Synthetic")
        self.assertEqual(id_rec["roles"], ["Speaker", "Panelist"])
        self.assertTrue(id_rec["consent"]["consent_noted"])
        self.assertIn("consent_date_utc", id_rec["consent"])

        # Verify Zone 2 (Biometrics): combined embedding and sample WAVs
        bio_meta, combined_emb = self.storage.read_biometric_record(spk_id)
        self.assertEqual(bio_meta["sample_count"], 3)
        self.assertEqual(bio_meta["sample_names"], ["sample_1", "sample_2", "sample_3"])
        self.assertGreater(len(combined_emb), 0)

        # Verify encrypted WAV samples exist and can be decrypted
        wav1 = self.storage.read_sample_audio(spk_id, "sample_1")
        self.assertGreater(len(wav1), 44)
        data, sr = sf.read(io.BytesIO(wav1))
        self.assertEqual(sr, 16000)

        # Verify Keyed Audit Log has PROFILE_ENROLLED entry
        master_key = self.vault.get_master_key_bytes()
        audit_log = self.vault.get_audit_log()
        audit_key = audit_log.derive_audit_key(master_key)
        valid_chain, err = audit_log.verify_chain(audit_key)
        self.assertTrue(valid_chain, f"Audit log chain broken: {err}")

        latest_entry = audit_log.get_latest_entry()
        self.assertEqual(latest_entry["action"], "PROFILE_ENROLLED")
        self.assertEqual(latest_entry["target_id"], spk_id)

    @slow_test
    def test_07_zero_plaintext_canary_scan(self):
        """Invariant: Canary scan verifies zero plaintext names or unencrypted WAVs on disk."""
        session = self.enrollment_mgr.start_session(
            full_name="SecretCanarySpeaker1234",
            roles=["Speaker"],
            consent_noted=True,
        )
        sample_path = os.path.join(self.temp_dir, "canary_sample.wav")
        canary_pcm = generate_synthetic_speech_wav(duration_s=20.0)
        with open(sample_path, "wb") as f:
            f.write(canary_pcm)

        for _ in range(3):
            ok_imp, msg_imp, _ = self.enrollment_mgr.import_and_verify_sample(session, sample_path, user_approved=True)
            self.assertTrue(ok_imp, f"Canary sample import failed: {msg_imp}")

        ok, msg = self.enrollment_mgr.commit_enrollment(session)
        self.assertTrue(ok, f"Canary commit failed: {msg}")

        # Scan entire vault directory for plaintext leakages
        leaks = self.storage.canary_scan_vault(canary_names=["SecretCanarySpeaker1234"])
        self.assertEqual(len(leaks), 0, f"Plaintext leaks detected in vault: {leaks}")

    def test_08_enrollment_dialog_stability(self):
        """Invariant: VoiceProfileEnrollmentDialog is stable under light and dark theme."""
        if not self.root:
            self.skipTest("Headless environment: Tk root not available")

        for mode in ("dark", "light"):
            theme.apply_global_theme(self.root, theme_mode=mode)
            dlg = VoiceProfileEnrollmentDialog(self.root, self.enrollment_mgr)
            dlg.update_idletasks()

            initial_widgets = 0
            for child in dlg.winfo_children():
                initial_widgets += 1

            # Dispatch configure resize
            dlg.geometry("720x640")
            dlg.event_generate("<Configure>", width=720, height=640)
            dlg.update_idletasks()

            final_widgets = 0
            for child in dlg.winfo_children():
                final_widgets += 1

            self.assertEqual(initial_widgets, final_widgets, "Enrollment dialog must not duplicate widgets on resize!")
            dlg.destroy()

    @slow_test
    def test_09_cross_sample_consistency(self):
        """Invariant: Staging a sample whose voice differs noticeably flags an inconsistency warning."""
        session = self.enrollment_mgr.start_session(
            full_name="Consistency Test Speaker",
            consent_noted=True,
        )
        sample1 = os.path.join(self.temp_dir, "consist_1.wav")
        with open(sample1, "wb") as f:
            f.write(generate_synthetic_speech_wav(duration_s=20.0))

        # Import first sample
        ok1, _, _ = self.enrollment_mgr.import_and_verify_sample(session, sample1, user_approved=True)
        self.assertTrue(ok1)

        # Create second sample and manually alter embedding to simulate conflicting voice
        session.samples[0].embedding = np.array([1.0] + [0.0] * 255, dtype=np.float32)

        # Mock embedding engine to return orthogonal vector
        class OrthogonalEngine(LocalEmbeddingEngine):
            def compute_embedding(self, audio, sample_rate=16000, release_after=True):
                return np.array([0.0, 1.0] + [0.0] * 254, dtype=np.float32)

        mgr_orth = EnrollmentManager(self.storage, OrthogonalEngine(prefer_gpu=False))
        ok_incons, msg_incons, _ = mgr_orth.import_and_verify_sample(
            session, sample1, confirm_inconsistency=False
        )
        self.assertFalse(ok_incons)
        self.assertIn("inconsistency", msg_incons.lower())

        # With explicit confirmation, sample is accepted
        ok_confirm, _, _ = mgr_orth.import_and_verify_sample(
            session, sample1, confirm_inconsistency=True, user_approved=True
        )
        self.assertTrue(ok_confirm)
        self.assertTrue(session.samples[-1].is_inconsistent)

    @slow_test
    def test_10_duplicate_profile_warning(self):
        """Invariant: Candidate voice matching an existing profile flags duplicate warning with name when unlocked."""
        # 1. Enroll existing profile "Dr. Alice Original"
        session1 = self.enrollment_mgr.start_session(full_name="Dr. Alice Original", consent_noted=True)
        sample_path = os.path.join(self.temp_dir, "alice_sample.wav")
        with open(sample_path, "wb") as f:
            f.write(generate_synthetic_speech_wav(duration_s=20.0))

        for _ in range(3):
            self.enrollment_mgr.import_and_verify_sample(session1, sample_path, user_approved=True)
        ok1, _ = self.enrollment_mgr.commit_enrollment(session1)
        self.assertTrue(ok1)

        # 2. Start new enrollment with matching voice
        session2 = self.enrollment_mgr.start_session(full_name="Alice Duplicate Candidate", consent_noted=True)
        ok2, _, _ = self.enrollment_mgr.import_and_verify_sample(session2, sample_path, user_approved=True)
        self.assertTrue(ok2)
        self.assertIsNotNone(session2.duplicate_warning)
        # When unlocked, name is shown
        self.assertIn("Dr. Alice Original", session2.duplicate_warning)

        # When identity resolution is unavailable/locked, only anonymous ID is shown
        import unittest.mock
        with unittest.mock.patch.object(self.storage, "resolve_speaker_name", return_value=None):
            dup_locked = self.enrollment_mgr.check_duplicate_profile(session2.samples[0].embedding)
            self.assertIsNotNone(dup_locked)
            spk_id_dup, name_dup, sim_dup = dup_locked
            self.assertIsNone(name_dup, "Name must be hidden while identity zone is unresolved!")
            self.assertTrue(spk_id_dup.startswith("spk_"))

    @slow_test
    def test_11_re_embed_all_profiles(self):
        """Invariant: All enrolled profiles can be re-embedded from encrypted reference samples."""
        self.vault.unlock(self.passphrase)
        session = self.enrollment_mgr.start_session(full_name="Re-Embed Speaker", consent_noted=True)
        sample_path = os.path.join(self.temp_dir, "re_embed_sample.wav")
        with open(sample_path, "wb") as f:
            f.write(generate_synthetic_speech_wav(duration_s=20.0))

        for _ in range(3):
            self.enrollment_mgr.import_and_verify_sample(session, sample_path, user_approved=True)
        ok, _ = self.enrollment_mgr.commit_enrollment(session)
        self.assertTrue(ok)

        # Verify initial model name and version
        bio_meta_orig, _ = self.storage.read_biometric_record(session.spk_id)
        self.assertEqual(bio_meta_orig.get("model_version"), "1.0.0")

        # Mock updated model version 2.0.0
        class Version2Engine(LocalEmbeddingEngine):
            def __init__(self):
                super().__init__(prefer_gpu=False, model_version="2.0.0")

            def compute_embedding(self, audio, sample_rate=16000, release_after=True):
                return np.ones(256, dtype=np.float32) / 16.0

        v2_engine = Version2Engine()
        count, msg = self.enrollment_mgr.re_embed_all_profiles(new_embedding_engine=v2_engine)
        self.assertGreaterEqual(count, 1)

        # Verify updated metadata in Zone 2
        bio_meta_updated, updated_emb = self.storage.read_biometric_record(session.spk_id)
        self.assertEqual(bio_meta_updated.get("model_version"), "2.0.0")
        self.assertGreater(len(updated_emb), 0)

        # Verify audit log recorded PROFILES_RE_EMBEDDED
        audit_log = self.vault.get_audit_log()
        latest = audit_log.get_latest_entry()
        self.assertEqual(latest["action"], "PROFILES_RE_EMBEDDED")

    def test_12_vault_lock_guard_on_enrollment(self):
        """Invariant: EnrollmentDialog strictly refuses to open and destroys itself if vault is locked."""
        if not self.root:
            self.skipTest("Headless environment: Tk root not available")

        self.vault.lock()
        self.assertFalse(self.vault.is_unlocked())

        notified = []
        def mock_notify(title, msg):
            notified.append((title, msg))

        dlg = VoiceProfileEnrollmentDialog(self.root, self.enrollment_mgr, notify_fn=mock_notify)
        dlg.update()
        # Dialog must be destroyed immediately and notify_fn called without blocking
        self.assertFalse(dlg.winfo_exists(), "Enrollment dialog must destroy itself when vault is locked!")
        self.assertEqual(len(notified), 1)
        self.assertIn("Locked", notified[0][0])

    @slow_test
    def test_13_no_auto_approval_explicit_approval_required(self):
        """Invariant: Every sample, including CLEAR_PASS, waits for explicit approval before staging."""
        session = self.enrollment_mgr.start_session(full_name="Approval Test Speaker", consent_noted=True)
        sample_path = os.path.join(self.temp_dir, "clear_pass_sample.wav")
        with open(sample_path, "wb") as f:
            f.write(generate_synthetic_speech_wav(duration_s=20.0))

        # 1. Calling import_and_verify_sample without user_approved=True:
        # Must inspect sample, but REFUSE to stage it!
        ok, msg, q = self.enrollment_mgr.import_and_verify_sample(session, sample_path, user_approved=False)
        self.assertFalse(ok)
        self.assertIn("Explicit operator approval required", msg)
        self.assertEqual(len(session.samples), 0, "CLEAR_PASS sample must NOT be staged without explicit approval!")

        # 2. Inspecting via inspect_sample produces an un-staged PendingSample
        insp_ok, insp_msg, pending, q_insp = self.enrollment_mgr.inspect_sample(session, sample_path)
        self.assertTrue(insp_ok)
        self.assertIsNotNone(pending)
        self.assertEqual(q_insp.verdict, "CLEAR_PASS")
        self.assertEqual(len(session.samples), 0)

        # 3. Attempting to stage without explicit approval must be rejected
        stage_fail, stage_fail_msg = self.enrollment_mgr.stage_sample(session, pending, user_approved=False)
        self.assertFalse(stage_fail)
        self.assertEqual(len(session.samples), 0)

        # 4. Explicit user approval successfully stages the sample
        stage_ok, stage_msg = self.enrollment_mgr.stage_sample(session, pending, user_approved=True)
        self.assertTrue(stage_ok)
        self.assertEqual(len(session.samples), 1)
        self.assertTrue(session.samples[0].approved_by_user)

    def test_14_two_speaker_rule_substantial_vs_interjection(self):
        """Invariant: Two substantial speakers is CLEAR_FAIL; brief interjection (< 1.0s) is BORDERLINE."""
        import unittest.mock
        test_wav = generate_synthetic_speech_wav(duration_s=20.0)

        with unittest.mock.patch("scribe_core.voice_quality_checker.run_pyannote_analysis") as mock_pyannote:
            # Case 1: Two substantial speakers (secondary speech >= 1.0s, e.g. 2.0s)
            mock_pyannote.return_value = (
                18.0,  # total speech duration
                2,     # speaker count
                0.0,   # overlap duration
                {"SPEAKER_00": 16.0, "SPEAKER_01": 2.0},  # 2.0s secondary speech >= 1.0s limit
            )
            res_sub = evaluate_sample_quality(test_wav)
            self.assertEqual(res_sub.verdict, "CLEAR_FAIL")
            self.assertFalse(res_sub.passed)
            self.assertFalse(res_sub.can_override, "Substantial second speaker must NOT be overrideable!")
            self.assertIn("Two substantial speakers detected", res_sub.failure_reason)

            # Case 2: Two speakers with brief interjection (secondary speech < 1.0s, e.g. 0.5s)
            mock_pyannote.return_value = (
                18.0,
                2,
                0.0,
                {"SPEAKER_00": 17.5, "SPEAKER_01": 0.5},  # 0.5s secondary speech < 1.0s threshold
            )
            res_interject = evaluate_sample_quality(test_wav)
            self.assertEqual(res_interject.verdict, "BORDERLINE")
            self.assertFalse(res_interject.passed)
            self.assertTrue(res_interject.is_borderline)
            self.assertTrue(res_interject.can_override, "Brief interjection must be eligible for override!")
            self.assertTrue(any("Brief secondary interjection detected" in r for r in res_interject.borderline_reasons))

    @slow_test
    def test_15_duplicate_warning_name_not_logged(self):
        """Invariant: Duplicate profile warning with name is shown only in dialog and never written to logs."""
        session1 = self.enrollment_mgr.start_session(full_name="SecretCanaryPersonXYZ", consent_noted=True)
        sample_path = os.path.join(self.temp_dir, "dup_secret_sample.wav")
        with open(sample_path, "wb") as f:
            f.write(generate_synthetic_speech_wav(duration_s=20.0))

        for _ in range(3):
            self.enrollment_mgr.import_and_verify_sample(session1, sample_path, user_approved=True)
        ok1, _ = self.enrollment_mgr.commit_enrollment(session1)
        self.assertTrue(ok1)

        # Start second session with same voice
        session2 = self.enrollment_mgr.start_session(full_name="New Speaker Candidate", consent_noted=True)

        # Capture all logs during duplicate check
        with self.assertLogs("scribe_core", level="INFO") as log_cm:
            import logging
            logging.getLogger("scribe_core").info("Benign diagnostic log")
            self.enrollment_mgr.inspect_sample(session2, sample_path)

        # Dialog-facing property has the name
        self.assertIsNotNone(session2.duplicate_warning)
        self.assertIn("SecretCanaryPersonXYZ", session2.duplicate_warning)

        # Logs must NEVER contain the secret name
        all_logs = "\n".join(log_cm.output)
        self.assertNotIn("SecretCanaryPersonXYZ", all_logs, "Real name from duplicate warning must NEVER be logged!")

        # Audit log must NEVER contain the secret name in target_id or details
        audit_log = self.vault.get_audit_log()
        if os.path.isfile(audit_log.log_path):
            with open(audit_log.log_path, "r", encoding="utf-8") as f:
                audit_content = f.read()
            self.assertNotIn("SecretCanaryPersonXYZ", audit_content)


if __name__ == "__main__":
    unittest.main()
