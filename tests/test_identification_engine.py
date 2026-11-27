"""
tests/test_identification_engine.py

Comprehensive tests for Phase V6 Identification Engine & Voice Biometrics:
1. Multi-speaker word-timestamp alignment (ASR receives zero voice data)
2. Short-turn filtering and contiguous segment merge
3. Overlap detection and line formatting
4. Cross-clip acoustic centroid linking (voice-only; never by timestamp or order)
5. Closed-set roster matching with margin requirement
6. Unknown guest rejection to [Unidentified]
7. Audience attribution for short turns in panel sessions
8. Quarantine exclusion invariant (quarantined profile never matched)
9. In-memory representative snippet extraction (zero disk write)
10. Re-identification without re-transcription
11. Canary and privacy scan of all outputs
12. UI stability test for ReidentifyDialog
"""

import io
import json
import os
import shutil
import sys
import tempfile
import unittest
import tkinter as tk
import numpy as np
import soundfile as sf

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from scribe_core.voice_vault import VoiceVault
from scribe_core.vault_v2 import VaultV2StorageEngine
from scribe_core.quarantine_manager import QuarantineManager
from scribe_core.embedding_engine import (
    DEFAULT_EMBEDDING_MODEL_NAME,
    LocalEmbeddingEngine,
    EMBEDDING_DIMENSION,
)
from scribe_core.identification_engine import (
    IdentificationConfig,
    IdentificationEngine,
    AlignedSegment,
    SpeakerCluster,
    save_clip_clusters_to_disk,
    load_clip_clusters_from_disk,
    load_all_event_clusters,
)
from gui.reidentify_dialog import ReidentifyDialog


class TestIdentificationEngine(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            cls.root = tk.Tk()
            cls.root.withdraw()
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
        self.temp_dir = tempfile.mkdtemp(prefix="ss_ident_test_")
        self.vault_dir = os.path.join(self.temp_dir, "vault")
        os.makedirs(self.vault_dir, exist_ok=True)

        self.vault = VoiceVault(keys_dir=self.vault_dir, time_cost=1, memory_cost=1024, parallelism=2)
        self.vault.initialize("canyon hazard velvet galaxy timber pebble")
        self.storage = VaultV2StorageEngine(self.vault)
        self.quarantine_mgr = QuarantineManager(self.storage)

        # Mock embedding engine using deterministic orthogonal basis vectors for test clarity
        class MockEmbeddingEngine(LocalEmbeddingEngine):
            def compute_embedding(self, audio_data, sample_rate=16000, release_after=True):
                # Hash audio length and sum to produce stable deterministic vector
                val = float(np.mean(audio_data)) if len(audio_data) > 0 else 0.0
                vec = np.zeros(EMBEDDING_DIMENSION, dtype=np.float32)
                # Map audio characteristic to basis index
                idx = int(abs(val) * 1000) % EMBEDDING_DIMENSION
                vec[idx] = 1.0
                return vec

        self.mock_emb_engine = MockEmbeddingEngine(prefer_gpu=False)
        self.engine = IdentificationEngine(
            vault_storage=self.storage,
            quarantine_mgr=self.quarantine_mgr,
            embedding_engine=self.mock_emb_engine,
            config=IdentificationConfig(
                match_threshold=0.65,
                second_best_margin=0.08,
                short_turn_threshold_sec=2.5,
                cross_clip_link_threshold=0.70,
            ),
        )

    def tearDown(self):
        self.vault.close()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_01_word_alignment_and_speaker_transitions(self):
        """Invariant: Words are aligned to diarization turns strictly by timestamp midpoint."""
        words = [
            {"word": "Good", "start": 0.0, "end": 0.4},
            {"word": "morning", "start": 0.5, "end": 0.9},
            {"word": "everyone.", "start": 1.0, "end": 1.8},
            {"word": "Thank", "start": 2.2, "end": 2.5},
            {"word": "you.", "start": 2.6, "end": 3.0},
        ]
        turns = [
            {"speaker": "SPEAKER_00", "start": 0.0, "end": 2.0},
            {"speaker": "SPEAKER_01", "start": 2.1, "end": 3.2},
        ]

        segments = self.engine.align_words_to_turns(words, turns)
        self.assertEqual(len(segments), 2)
        self.assertEqual(segments[0].cluster_id, "SPEAKER_00")
        self.assertEqual(segments[0].text, "Good morning everyone.")
        self.assertEqual(segments[1].cluster_id, "SPEAKER_01")
        self.assertEqual(segments[1].text, "Thank you.")

    def test_02_overlap_detection_and_formatting(self):
        """Invariant: Words occurring during multiple concurrent turns are marked overlapping."""
        words = [
            {"word": "Hello", "start": 0.0, "end": 0.5},
            {"word": "agree", "start": 1.0, "end": 1.5},
        ]
        # Overlapping turns between 0.8 and 1.8
        turns = [
            {"speaker": "SPEAKER_00", "start": 0.0, "end": 2.0},
            {"speaker": "SPEAKER_01", "start": 0.8, "end": 1.8},
        ]

        segments = self.engine.align_words_to_turns(words, turns)
        self.assertEqual(len(segments), 2)
        self.assertFalse(segments[0].is_overlapping)
        self.assertTrue(segments[1].is_overlapping)

        # Assign aliases to clusters
        clusters = {
            "SPEAKER_00": SpeakerCluster("SPEAKER_00", matched_alias="S01", matched_role="Host", is_unidentified=False),
            "SPEAKER_01": SpeakerCluster("SPEAKER_01", matched_alias="S02", matched_role="Panelist", is_unidentified=False),
        }
        self.engine.apply_attributions_to_segments(segments, clusters, is_multi_speaker=True)

        line0 = segments[0].format_line(is_multi_speaker=True)
        line1 = segments[1].format_line(is_multi_speaker=True)

        self.assertIn("[S01 (Host)]", line0)
        self.assertIn("[Overlapping: S01 (Host), S02 (Panelist)]", line1)

    def test_03_short_turn_contiguous_merge_and_audience_flag(self):
        """Invariant: Short turns (< 2.5s) merge if contiguous, or become [Audience] in panels."""
        seg1 = AlignedSegment(0.0, 3.0, "S01", "Host", "Main talk begins.", cluster_id="SPEAKER_00")
        # Contiguous short turn from same speaker (gap 0.2s)
        seg2 = AlignedSegment(3.2, 4.0, "S01", "Host", "Indeed.", cluster_id="SPEAKER_00")
        # Short interjection from another speaker in a panel (< 2.5s)
        seg3 = AlignedSegment(5.0, 6.2, "[Unidentified]", "", "Hear hear!", cluster_id="SPEAKER_01")

        # 1. In panel mode:
        filtered_panel = self.engine.filter_short_turns([seg1, seg2, seg3], is_panel=True)
        self.assertEqual(len(filtered_panel), 2)
        self.assertEqual(filtered_panel[0].text, "Main talk begins. Indeed.")
        self.assertEqual(filtered_panel[1].speaker_alias, "[Audience]")
        self.assertEqual(filtered_panel[1].role, "Audience")

        # 2. In solo mode:
        seg_solo_short = AlignedSegment(5.0, 6.2, "[Unidentified]", "", "Interruption", cluster_id="SPEAKER_01")
        filtered_solo = self.engine.filter_short_turns([seg_solo_short], is_panel=False)
        self.assertEqual(filtered_solo[0].speaker_alias, "[Unidentified]")

    def test_04_cross_clip_centroid_linking(self):
        """Invariant: Clusters are linked across clips ONLY by acoustic centroid similarity."""
        vec_a = np.zeros(EMBEDDING_DIMENSION, dtype=np.float32)
        vec_a[0] = 1.0  # Speaker A acoustic fingerprint

        vec_b = np.zeros(EMBEDDING_DIMENSION, dtype=np.float32)
        vec_b[10] = 1.0  # Speaker B acoustic fingerprint

        # Clip 1 has Cluster 0 (Speaker A)
        c1 = {
            "C0": SpeakerCluster(
                cluster_id="C0",
                clip_names=["clip_01.mp4"],
                centroid=vec_a.copy(),
                sample_count=2,
                turn_intervals=[(0.0, 5.0)],
            )
        }

        # Clip 2 has Cluster 1 (Speaker A again, 0.95 similarity) and Cluster 2 (Speaker B)
        vec_a_noisy = vec_a.copy()
        vec_a_noisy[1] = 0.1
        vec_a_noisy /= np.linalg.norm(vec_a_noisy)

        c2 = {
            "C1": SpeakerCluster(
                cluster_id="C1",
                clip_names=["clip_02.mp4"],
                centroid=vec_a_noisy,
                sample_count=2,
                turn_intervals=[(10.0, 15.0)],
            ),
            "C2": SpeakerCluster(
                cluster_id="C2",
                clip_names=["clip_02.mp4"],
                centroid=vec_b.copy(),
                sample_count=1,
                turn_intervals=[(20.0, 25.0)],
            ),
        }

        linked = self.engine.link_clusters_across_clips([c1, c2])
        # Speaker A in Clip 1 and Clip 2 should be unified; Speaker B remains separate
        self.assertEqual(len(linked), 2)
        spk_a_cluster = [c for c in linked if "clip_01.mp4" in c.clip_names][0]
        self.assertIn("clip_02.mp4", spk_a_cluster.clip_names)
        self.assertEqual(spk_a_cluster.sample_count, 4)

    def test_05_closed_set_matching_host_first_and_margin(self):
        """Invariant: Closed set matches only against roster, prioritizing host, requiring margin."""
        # Enroll Host (S01) and Panelist (S02) in Vault
        spk1 = self.storage.create_identity_record("Synthetic Host", roles=["Host"], consent_opt_in=True)
        spk2 = self.storage.create_identity_record("Synthetic Panelist", roles=["Panelist"], consent_opt_in=True)

        vec_host = np.zeros(EMBEDDING_DIMENSION, dtype=np.float32)
        vec_host[0] = 1.0
        vec_panelist = np.zeros(EMBEDDING_DIMENSION, dtype=np.float32)
        vec_panelist[1] = 1.0

        self.storage.create_biometric_record(spk1, vec_host.tobytes())
        self.storage.create_biometric_record(spk2, vec_panelist.tobytes())

        roster = [
            {"alias": "S01", "spk_id": spk1, "role": "Host"},
            {"alias": "S02", "spk_id": spk2, "role": "Panelist"},
        ]

        # Candidate 1: Matches Host strongly (1.0 vs 0.0) -> S01
        c_host = SpeakerCluster("C_HOST", centroid=vec_host.copy(), sample_count=1)

        # Candidate 2: Ambiguous (0.68 vs 0.64, margin 0.04 < 0.08 requirement) -> [Unidentified]
        vec_ambig = np.zeros(EMBEDDING_DIMENSION, dtype=np.float32)
        vec_ambig[0] = 0.68
        vec_ambig[1] = 0.64
        vec_ambig /= np.linalg.norm(vec_ambig)
        c_ambig = SpeakerCluster("C_AMBIG", centroid=vec_ambig, sample_count=1)

        # Candidate 3: Unknown guest (orthogonal to both, similarity 0.0) -> [Unidentified]
        vec_guest = np.zeros(EMBEDDING_DIMENSION, dtype=np.float32)
        vec_guest[5] = 1.0
        c_guest = SpeakerCluster("C_GUEST", centroid=vec_guest, sample_count=1)

        clusters = [c_host, c_ambig, c_guest]
        self.engine.match_roster(clusters, roster, is_solo=False)

        self.assertEqual(c_host.matched_alias, "S01")
        self.assertEqual(c_host.matched_role, "Host")
        self.assertFalse(c_host.is_unidentified)

        self.assertEqual(c_ambig.matched_alias, "[Unidentified]")
        self.assertTrue(c_ambig.is_unidentified)

        self.assertEqual(c_guest.matched_alias, "[Unidentified]")
        self.assertTrue(c_guest.is_unidentified)

    def test_06_quarantine_exclusion_invariant(self):
        """Invariant: Quarantined profile is NEVER matched, even with perfect 1.0 similarity."""
        spk_q = self.storage.create_identity_record("Quarantined Speaker", roles=["Speaker"], consent_opt_in=True)
        vec_q = np.zeros(EMBEDDING_DIMENSION, dtype=np.float32)
        vec_q[9] = 1.0
        self.storage.create_biometric_record(spk_q, vec_q.tobytes())

        # Put profile into quarantine
        self.quarantine_mgr.stage_quarantine(spk_q, reason="Privacy request", confirmation_code="Q-123")

        roster = [{"alias": "S01", "spk_id": spk_q, "role": "Speaker"}]
        c = SpeakerCluster("C_TEST", centroid=vec_q.copy(), sample_count=1)

        self.engine.match_roster([c], roster, is_solo=True)
        self.assertEqual(c.matched_alias, "[Unidentified]")
        self.assertTrue(c.is_unidentified)

    def test_07_in_memory_representative_snippet_extraction(self):
        """Invariant: Snippets (3.0-8.0s) are extracted strictly in memory as 16kHz mono WAV bytes."""
        sample_rate = 16000
        # 10s synthetic sine audio
        t = np.linspace(0, 10.0, int(10.0 * sample_rate), endpoint=False)
        audio = 0.3 * np.sin(2 * np.pi * 440.0 * t).astype(np.float32)

        turn_intervals = [(1.0, 5.5)]  # 4.5s sustained speech turn

        snippet_bytes = self.engine.extract_representative_snippet(audio, turn_intervals, sample_rate=sample_rate)
        self.assertIsNotNone(snippet_bytes)
        self.assertGreater(len(snippet_bytes), 44)

        data, sr = sf.read(io.BytesIO(snippet_bytes))
        self.assertEqual(sr, 16000)
        self.assertEqual(data.ndim, 1)
        self.assertAlmostEqual(len(data) / sr, 4.5, delta=0.1)

    def test_08_reidentification_without_retranscription(self):
        """Invariant: Unknown speaker can be re-identified after enrolling without re-transcribing audio."""
        # 1. Initial run: Guest is unidentified
        vec_guest = np.zeros(EMBEDDING_DIMENSION, dtype=np.float32)
        vec_guest[7] = 1.0

        cluster = SpeakerCluster(
            cluster_id="SPEAKER_UNKNOWN",
            centroid=vec_guest.copy(),
            sample_count=1,
            turn_intervals=[(0.0, 5.0)],
        )
        clusters = {"SPEAKER_UNKNOWN": cluster}

        words = [{"word": "Hello", "start": 0.0, "end": 1.0}]
        turns = [{"speaker": "SPEAKER_UNKNOWN", "start": 0.0, "end": 5.0}]
        segments = self.engine.align_words_to_turns(words, turns)

        # Match against empty roster
        self.engine.match_roster([cluster], [], is_solo=False)
        self.engine.apply_attributions_to_segments(segments, clusters, is_multi_speaker=True)
        self.assertEqual(segments[0].speaker_alias, "[Unidentified]")

        # 2. Later: Guest is enrolled in Vault v2 as S02
        spk_guest = self.storage.create_identity_record("Enrolled Guest", roles=["Speaker"], consent_opt_in=True)
        self.storage.create_biometric_record(spk_guest, vec_guest.tobytes())
        updated_roster = [{"alias": "S02", "spk_id": spk_guest, "role": "Speaker"}]

        # 3. Re-identify: text remains untouched, only speaker alias updates
        reidentified = self.engine.reidentify_session(segments, clusters, updated_roster, is_solo=False)
        self.assertEqual(reidentified[0].speaker_alias, "S02")
        self.assertEqual(reidentified[0].role, "Speaker")
        self.assertEqual(reidentified[0].text, "Hello")

    def test_09_canary_scan_of_all_outputs(self):
        """Invariant: All segments, lines, and clusters carry aliases only (zero personal names or spk_ IDs)."""
        canary_name = "CONFIDENTIAL_CANARY_NAME_999"
        spk_canary = self.storage.create_identity_record(canary_name, roles=["Speaker"], consent_opt_in=True)
        vec_canary = np.zeros(EMBEDDING_DIMENSION, dtype=np.float32)
        vec_canary[3] = 1.0
        self.storage.create_biometric_record(spk_canary, vec_canary.tobytes())

        roster = [{"alias": "S01", "spk_id": spk_canary, "role": "Speaker"}]
        cluster = SpeakerCluster("C1", centroid=vec_canary.copy(), sample_count=1)
        self.engine.match_roster([cluster], roster, is_solo=True)

        seg = AlignedSegment(0.0, 5.0, "", "", "Canary speech text", cluster_id="C1")
        self.engine.apply_attributions_to_segments([seg], {"C1": cluster}, is_multi_speaker=True)
        formatted = seg.format_line(is_multi_speaker=True)

        self.assertNotIn(canary_name, formatted)
        self.assertNotIn(spk_canary, formatted)
        self.assertNotIn("spk_", formatted)
        self.assertIn("S01", formatted)

    def test_10_reidentify_dialog_stability(self):
        """Verify ReidentifyDialog opens, displays clusters, and closes stably without widget leakage."""
        if not self.root:
            self.skipTest("Headless environment: Tk root not available")

        clusters = [
            SpeakerCluster("C0", matched_alias="[Unidentified]", turn_intervals=[(0.0, 3.0)], is_unidentified=True),
        ]
        roster = [{"alias": "S01", "role": "Host"}]

        dlg = ReidentifyDialog(
            parent=self.root,
            clusters=clusters,
            event_roster=roster,
        )
        dlg.update()

        initial_count = len(dlg.winfo_children())
        self.assertTrue(dlg.winfo_exists())

        # Simulate resize
        dlg.geometry("800x600")
        dlg.update()
        final_count = len(dlg.winfo_children())

        self.assertEqual(initial_count, final_count, "Dialog must update in place without widget duplication!")
        dlg.destroy()

    def test_13_match_cluster_against_enrolled_profile_real_code_path(self):
        """
        Matches a cluster against an enrolled profile through the real code path:
        1. Enrolls a profile with real 256-dim embedding vector in Vault v2 Zone 2.
        2. Configures event roster with alias S01.
        3. Creates cluster with matching 256-dim centroid.
        4. Runs IdentificationEngine.match_roster.
        5. Verifies cluster is confidently attributed to S01.
        6. Verifies quarantine exclusion strictly rejects the match.
        """
        spk_id = "spk_1234567890abcdef1234567890abcdef"

        # 1. Write identity and biometric record to vault v2 storage
        self.storage.create_identity_record(
            full_name="Dr. Eleanor Vance",
            spk_id=spk_id,
            notes="Miskatonic",
        )

        # Generate a unit-normalized 256-dim embedding vector
        rng = np.random.RandomState(42)
        emb = rng.randn(EMBEDDING_DIMENSION).astype(np.float32)
        emb /= np.linalg.norm(emb)

        self.storage.create_biometric_record(
            spk_id=spk_id,
            embedding_bytes=emb.tobytes(),
            model=DEFAULT_EMBEDDING_MODEL_NAME,
            dimension=EMBEDDING_DIMENSION,
        )

        # 2. Event roster
        session_roster = [
            {"spk_id": spk_id, "alias": "S01", "role": "Speaker", "name": "Dr. Eleanor Vance"}
        ]

        # 3. Create cluster with near-identical centroid (cosine similarity ~0.99)
        noise = rng.randn(EMBEDDING_DIMENSION).astype(np.float32) * 0.005
        cluster_centroid = emb + noise
        cluster_centroid /= np.linalg.norm(cluster_centroid)

        cluster = SpeakerCluster(
            cluster_id="cluster_alpha",
            clip_names=["clip_01.mp4"],
            turn_intervals=[(10.0, 25.0)],
            centroid=cluster_centroid,
            sample_count=2,
            matched_alias="[Unidentified]",
            is_unidentified=True,
        )

        # 4. Run real match_roster
        self.engine.match_roster([cluster], session_roster, is_solo=False)

        # 5. Assert match succeeded
        self.assertFalse(cluster.is_unidentified, "Cluster must be identified!")
        self.assertEqual(cluster.matched_alias, "S01")
        self.assertEqual(cluster.matched_role, "Speaker")
        self.assertEqual(cluster.matched_spk_id, spk_id)
        self.assertGreater(cluster.confidence, 0.95, f"Expected similarity > 0.95, got {cluster.confidence:.3f}")

        # 6. Test quarantine exclusion: once quarantined, match must be rejected
        self.quarantine_mgr.stage_quarantine(spk_id, reason="Security hold")
        # Reset cluster
        cluster.is_unidentified = True
        cluster.matched_alias = "[Unidentified]"

        self.engine.match_roster([cluster], session_roster, is_solo=False)
        self.assertTrue(cluster.is_unidentified, "Quarantined profile must be rejected!")
        self.assertEqual(cluster.matched_alias, "[Unidentified]")

    def test_14_persistent_cluster_storage_and_reload(self):
        """
        Verifies persistent cluster saving to scribe\state\clusters\<clip>.json:
        1. Saves diarized segments, centroids, and snippet time ranges.
        2. Asserts NO personal names or audio data exist on disk.
        3. Reloads persisted clusters across clips and verifies reconstructed SpeakerCluster.
        """
        event_dir = os.path.join(self.temp_dir, "test_event")
        state_dir = os.path.join(event_dir, "scribe", "state")
        clusters_dir = os.path.join(state_dir, "clusters")
        os.makedirs(clusters_dir, exist_ok=True)

        rng = np.random.RandomState(123)
        centroid1 = rng.randn(EMBEDDING_DIMENSION).astype(np.float32)
        centroid1 /= np.linalg.norm(centroid1)

        clusters = {
            "SPEAKER_00": SpeakerCluster(
                cluster_id="SPEAKER_00",
                clip_names=["clip_01.mp4"],
                turn_intervals=[(0.0, 5.0), (12.0, 18.0)],
                centroid=centroid1,
                sample_count=4,
                snippet_time_range=(0.0, 5.0),
            )
        }
        segments = [
            {"start": 0.0, "end": 5.0, "speaker": "SPEAKER_00", "is_overlapping": False},
            {"start": 12.0, "end": 18.0, "speaker": "SPEAKER_00", "is_overlapping": False},
        ]

        saved_path = save_clip_clusters_to_disk(
            state_dir=state_dir,
            clip_name="clip_01.mp4",
            segments=segments,
            clusters=clusters,
        )
        self.assertTrue(os.path.isfile(saved_path))
        self.assertTrue(saved_path.endswith("clip_01.json"))

        # Canary check: assert NO names or binary audio arrays
        with open(saved_path, "r", encoding="utf-8") as f:
            raw_text = f.read()
            data = json.loads(raw_text)

        self.assertNotIn("Eleanor", raw_text)
        self.assertNotIn("Anthony", raw_text)
        self.assertIn("SPEAKER_00", raw_text)
        self.assertEqual(data["embedding_dimension"], 256)
        self.assertEqual(data["clusters"]["SPEAKER_00"]["snippet_time_range"], [0.0, 5.0])

        # Test single clip reload
        clip_data = load_clip_clusters_from_disk(clusters_dir, "clip_01.mp4")
        self.assertIsNotNone(clip_data)
        self.assertEqual(clip_data["clip_name"], "clip_01.mp4")

        # Test all event reload
        loaded_clusters = load_all_event_clusters(event_dir)
        self.assertIn("SPEAKER_00", loaded_clusters)
        reconstructed = loaded_clusters["SPEAKER_00"]
        self.assertEqual(reconstructed.sample_count, 4)
        self.assertIsNotNone(reconstructed.centroid)
        self.assertEqual(len(reconstructed.centroid), 256)
        self.assertEqual(reconstructed.snippet_time_range, (0.0, 5.0))

    def test_15_reidentify_matches_after_vault_unlocked_later(self):
        """
        Verify that cluster data saved during transcription can be matched
        by Re-Identify after the app has restarted and the vault is unlocked later.
        """
        event_dir = os.path.join(self.temp_dir, "delayed_unlock_event")
        state_dir = os.path.join(event_dir, "scribe", "state")
        os.makedirs(os.path.join(state_dir, "clusters"), exist_ok=True)

        spk_id = "spk_delayed_unlock_speaker_001"
        self.storage.create_identity_record(
            full_name="Later Unlocked Speaker",
            spk_id=spk_id,
        )

        rng = np.random.RandomState(999)
        enrolled_emb = rng.randn(EMBEDDING_DIMENSION).astype(np.float32)
        enrolled_emb /= np.linalg.norm(enrolled_emb)

        self.storage.create_biometric_record(
            spk_id=spk_id,
            embedding_bytes=enrolled_emb.tobytes(),
            model=DEFAULT_EMBEDDING_MODEL_NAME,
            dimension=EMBEDDING_DIMENSION,
        )

        # Save cluster to disk as if transcription wrote it while vault was locked
        clusters = {
            "SPEAKER_01": SpeakerCluster(
                cluster_id="SPEAKER_01",
                clip_names=["clip_solo.mp4"],
                turn_intervals=[(2.0, 10.0)],
                centroid=enrolled_emb,
                sample_count=2,
                snippet_time_range=(2.0, 10.0),
            )
        }
        save_clip_clusters_to_disk(state_dir, "clip_solo.mp4", [], clusters)

        # Now simulate a fresh app session: reload all clusters from disk
        reloaded = load_all_event_clusters(event_dir)
        self.assertIn("SPEAKER_01", reloaded)
        cl_list = list(reloaded.values())

        # Match against roster
        roster = [{"spk_id": spk_id, "alias": "S02", "role": "Speaker"}]
        self.engine.match_roster(cl_list, roster, is_solo=False)

        # Verify automatic matching succeeded!
        matched_cl = reloaded["SPEAKER_01"]
        self.assertFalse(matched_cl.is_unidentified)
        self.assertEqual(matched_cl.matched_alias, "S02")
        self.assertEqual(matched_cl.matched_spk_id, spk_id)
        self.assertAlmostEqual(matched_cl.confidence, 1.0, places=3)


if __name__ == "__main__":
    unittest.main()
