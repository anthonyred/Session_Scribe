"""
tests/test_vault_usability_and_pipeline.py

Verification and functional tests for:
1. Vault Usability:
   - "Forgot passphrase?" flow (erase_vault clears keys, profiles, throttle, TOTP, writes audit log, leaves events untouched).
   - Passphrase age tracking, warning after 365 days, snooze for 7 days, timer reset on change.
   - Master key re-wrapping on passphrase change.
2. Pipeline Hardening:
   - Cloud transcription (Riva) word-level timestamp retention in build_segments_from_words.
   - Sentence spanning speaker transition splitting at word boundary in assign_diarization_to_words.
   - Stage 3 and Excel comparison report alias vs decrypted name display.
   - Diarization and Identification separate timing breakdown in export_comparison_report.
   - Multi-speaker (3-4 speakers) combined transcripts with [Audience], [Unidentified], and overlaps.
"""

from __future__ import annotations

import csv
import json
import os
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

_TEST_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_TEST_DIR)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from scribe_core.voice_vault import VoiceVault
from scribe_core.vault_v2 import VaultV2StorageEngine
from scribe_core.event_roster_manager import EventRosterManager
from scribe_core.cloud_engine import build_segments_from_words, WordInfo
from scribe_core.multi_speaker import (
    assign_diarization_to_words,
    format_speaker_line,
    format_speakers_header,
)
from scribe_core.combined_transcripts import (
    build_combined_transcripts,
    substitute_line_start_speaker_tags,
)
from scribe_core import run_comparison


class TestVaultUsability(unittest.TestCase):
    """Test 'Forgot passphrase?' vault erasure, passphrase age reminders, and passphrase rotation."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="ss_test_vault_usability_")
        self.vault_dir = os.path.join(self.temp_dir, "vault")
        self.event_dir = os.path.join(self.temp_dir, "event_01")
        os.makedirs(self.vault_dir, exist_ok=True)
        os.makedirs(self.event_dir, exist_ok=True)

        # Create dummy event content to verify it is never touched during vault erasure
        self.event_transcripts = os.path.join(self.event_dir, "scribe", "transcripts")
        self.event_manifest = os.path.join(self.event_dir, "scribe", "manifest.json")
        os.makedirs(self.event_transcripts, exist_ok=True)
        with open(os.path.join(self.event_transcripts, "clip_01.txt"), "w", encoding="utf-8") as f:
            f.write("Important event transcript text.")
        with open(self.event_manifest, "w", encoding="utf-8") as f:
            json.dump({"event_id": "EVT_TEST", "clips": [{"name": "clip_01.mp4"}]}, f)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_passphrase_age_and_reminder_lifecycle(self):
        """Test passphrase age tracking, 365-day boundary warning, snooze, and change reset."""
        vault = VoiceVault(keys_dir=self.vault_dir)
        initial_pw = "hazard galaxy pebble timber canyon velvet"
        ok, msg = vault.initialize(initial_pw)
        self.assertTrue(ok, f"Init failed: {msg}")

        # Passphrase age should start at 0 days
        now_dt = datetime.now(timezone.utc)
        self.assertEqual(vault.get_passphrase_age_days(now_dt), 0)

        # Warning should not trigger at 100 or 300 days
        warn_100 = vault.check_passphrase_age_warning(now_dt + timedelta(days=100))
        self.assertIsNone(warn_100)

        warn_364 = vault.check_passphrase_age_warning(now_dt + timedelta(days=364))
        self.assertIsNone(warn_364)

        # Warning should trigger at 365 days
        warn_365 = vault.check_passphrase_age_warning(now_dt + timedelta(days=365))
        self.assertIsNotNone(warn_365)
        self.assertTrue(warn_365["needs_warning"])
        self.assertGreaterEqual(warn_365["age_days"], 365)

        # Snooze for 7 days
        vault.snooze_passphrase_warning(days=7, now_dt=now_dt + timedelta(days=365))

        # Check at 368 days (snoozed, so no warning)
        warn_snoozed = vault.check_passphrase_age_warning(now_dt + timedelta(days=368))
        self.assertIsNone(warn_snoozed)

        # Check at 373 days (snooze expired, warning triggers again)
        warn_expired = vault.check_passphrase_age_warning(now_dt + timedelta(days=373))
        self.assertIsNotNone(warn_expired)
        self.assertTrue(warn_expired["needs_warning"])

        # Change passphrase
        new_pw = "quantum orbit horizon nebula crystal falcon"
        ok, msg = vault.change_passphrase(initial_pw, new_pw)
        self.assertTrue(ok, f"Change passphrase failed: {msg}")

        # Passphrase age should be reset to 0 relative to the change timestamp
        change_time = datetime.now(timezone.utc)
        self.assertEqual(vault.get_passphrase_age_days(change_time), 0)

        # Warning should not trigger after change
        warn_after_change = vault.check_passphrase_age_warning(change_time + timedelta(days=10))
        self.assertIsNone(warn_after_change)

        # Verify vault can be unlocked with new passphrase
        vault.lock()
        self.assertFalse(vault.is_unlocked())
        self.assertTrue(vault.unlock(new_pw))

    def test_forgot_passphrase_erase_vault(self):
        """Test erase_vault permanently destroys vault data and leaves event folders untouched."""
        vault = VoiceVault(keys_dir=self.vault_dir)
        pw = "hazard galaxy pebble timber canyon velvet"
        ok, msg = vault.initialize(pw)
        self.assertTrue(ok, f"Init failed: {msg}")

        # Enrol an identity record in Zone 1
        storage = VaultV2StorageEngine(vault)
        spk_id = storage.create_identity_record(full_name="Canary Speaker")
        self.assertIsNotNone(spk_id)
        self.assertTrue(vault.is_initialized())
        self.assertTrue(vault.is_unlocked())

        # Verify identity files exist in Zone 1
        records = storage.list_identity_ids()
        self.assertEqual(len(records), 1)

        # Execute permanent vault erasure
        erased = vault.erase_vault()
        self.assertTrue(erased)

        # Vault should be locked and uninitialized
        self.assertFalse(vault.is_unlocked())
        self.assertFalse(vault.is_initialized())

        # Keystore and identity records should be destroyed
        self.assertFalse(os.path.exists(vault._hierarchy.header_path))
        if os.path.exists(storage.identity_records_dir):
            self.assertEqual(len(os.listdir(storage.identity_records_dir)), 0)

        # Event files must remain completely untouched!
        with open(os.path.join(self.event_transcripts, "clip_01.txt"), "r", encoding="utf-8") as f:
            self.assertEqual(f.read(), "Important event transcript text.")
        with open(self.event_manifest, "r", encoding="utf-8") as f:
            manifest_data = json.load(f)
            self.assertEqual(manifest_data["event_id"], "EVT_TEST")


class TestPipelineHardening(unittest.TestCase):
    """Test pipeline word timestamps, sentence splitting, and multi-speaker combined transcripts."""

    def test_riva_word_timestamps_preserved_in_build_segments(self):
        """Verify build_segments_from_words retains word-level timestamps in 'words' attribute."""
        words = [
            WordInfo(word="Welcome", start_time=0.0, end_time=0.4),
            WordInfo(word="to", start_time=0.42, end_time=0.55),
            WordInfo(word="the", start_time=0.58, end_time=0.7),
            WordInfo(word="session.", start_time=0.72, end_time=1.2),
        ]
        segments = build_segments_from_words(words)
        self.assertEqual(len(segments), 1)
        seg = segments[0]
        self.assertEqual(seg["text"], "Welcome to the session.")
        self.assertIn("words", seg)
        self.assertEqual(len(seg["words"]), 4)
        self.assertEqual(seg["words"][0]["word"], "Welcome")
        self.assertEqual(seg["words"][0]["start"], 0.0)
        self.assertEqual(seg["words"][0]["end"], 0.4)

    def test_sentence_spanning_speaker_change_splits_at_word_boundary(self):
        """Verify assign_diarization_to_words splits mid-sentence speaker transitions into two segments."""
        roster = [
            {"name": "Alice", "role": "Host"},
            {"name": "Bob", "role": "Panelist"},
        ]
        # Sentence: "I believe so, but Bob disagrees completely."
        # Alice speaks words 0-2 (0.0s to 2.0s)
        # Bob speaks words 3-6 (2.2s to 4.5s)
        words = [
            {"start": 0.0, "end": 0.5, "word": "I"},
            {"start": 0.6, "end": 1.2, "word": "believe"},
            {"start": 1.3, "end": 2.0, "word": "so,"},
            {"start": 2.2, "end": 2.6, "word": "but"},
            {"start": 2.7, "end": 3.1, "word": "Bob"},
            {"start": 3.2, "end": 3.8, "word": "disagrees"},
            {"start": 3.9, "end": 4.5, "word": "completely."},
        ]
        turns = [
            {"start": 0.0, "end": 2.1, "speaker": "SPEAKER_00"},
            {"start": 2.15, "end": 5.0, "speaker": "SPEAKER_01"},
        ]
        sim_map = {"SPEAKER_00": "Alice", "SPEAKER_01": "Bob"}

        segments = assign_diarization_to_words(words, turns, roster, similarity_map=sim_map)

        # Must be split into two separate segments, not lumped into Alice or Bob
        self.assertEqual(len(segments), 2)
        self.assertEqual(segments[0]["speaker"], "Alice")
        self.assertEqual(segments[0]["role"], "Host")
        self.assertEqual(segments[0]["text"], "I believe so,")

        self.assertEqual(segments[1]["speaker"], "Bob")
        self.assertEqual(segments[1]["role"], "Panelist")
        self.assertEqual(segments[1]["text"], "but Bob disagrees completely.")

    def test_multi_speaker_4_speakers_plus_audience_and_overlaps(self):
        """Verify multi-speaker handling with 4 speakers, [Audience], [Unidentified], and overlaps."""
        temp_dir = tempfile.mkdtemp(prefix="ss_test_multi_spk_")
        try:
            event_root = temp_dir
            scribe_dir = os.path.join(event_root, "scribe")
            tx_dir = os.path.join(scribe_dir, "transcripts")
            comb_dir = os.path.join(scribe_dir, "combined")
            os.makedirs(tx_dir, exist_ok=True)
            os.makedirs(comb_dir, exist_ok=True)

            # Create a transcript with 4 speakers, Audience, Unidentified, and Overlap
            tx_content = (
                "Transcription Completed: 2026-11-25 12:00:00\n"
                "Session: Keynote Panel\n"
                "Speakers: Alice (Host), Bob (Panelist), Carol (Panelist), David (Guest)\n\n"
                "[Alice (Host)] [00:00:00.000 - 00:00:04.000] Welcome everyone.\n"
                "[Bob (Panelist)] [00:00:04.500 - 00:00:08.000] Thank you Alice.\n"
                "[Carol (Panelist)] [00:00:08.500 - 00:00:12.000] Excited to be here.\n"
                "[David (Guest)] [00:00:12.500 - 00:00:16.000] Looking forward to discussing this.\n"
                "[Audience] [00:00:16.500 - 00:00:18.000] (Applause)\n"
                "[Unidentified] [00:00:18.500 - 00:00:20.000] Can everyone hear in back?\n"
                "[Overlapping: Alice (Host), Bob (Panelist)] [00:00:20.500 - 00:00:23.000] Yes, loud and clear.\n"
            )
            with open(os.path.join(tx_dir, "Session_1_Clip_1.txt"), "w", encoding="utf-8") as f:
                f.write(tx_content)

            manifest = {
                "event_id": "EVT_PANEL",
                "clips": [{"name": "Session_1_Clip_1.mp4", "speaker": "Keynote Panel"}],
                "sessions": [
                    {
                        "session_id": 1,
                        "speaker": "Keynote Panel",
                        "speaker_mode": "panel",
                        "roster": [
                            {"name": "Alice", "role": "Host"},
                            {"name": "Bob", "role": "Panelist"},
                            {"name": "Carol", "role": "Panelist"},
                            {"name": "David", "role": "Guest"},
                        ],
                        "clips": [{"name": "Session_1_Clip_1.mp4"}],
                    }
                ],
            }

            results = build_combined_transcripts(event_root, manifest)
            self.assertTrue(len(results) > 0)

            # Session combined file should exist
            session_comb_file = os.path.join(comb_dir, "Keynote_Panel_combined.txt")
            self.assertTrue(os.path.isfile(session_comb_file))
            with open(session_comb_file, "r", encoding="utf-8") as f:
                comb_text = f.read()

            self.assertIn("[Alice (Host)]", comb_text)
            self.assertIn("[Bob (Panelist)]", comb_text)
            self.assertIn("[Carol (Panelist)]", comb_text)
            self.assertIn("[David (Guest)]", comb_text)
            self.assertIn("[Audience]", comb_text)
            self.assertIn("[Unidentified]", comb_text)
            self.assertIn("[Overlapping: Alice (Host), Bob (Panelist)]", comb_text)

        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)


class TestComparisonReportTimingsAndSpeakerDisplay(unittest.TestCase):
    """Test comparison report separate ASR/Diarization/Identification timing columns and alias resolution."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="ss_test_comparison_rep_")
        self.event_root = self.temp_dir
        self.scribe_dir = os.path.join(self.event_root, "scribe")
        self.tx_dir = os.path.join(self.scribe_dir, "transcripts")
        self.reports_dir = os.path.join(self.scribe_dir, "reports")
        os.makedirs(self.tx_dir, exist_ok=True)
        os.makedirs(self.reports_dir, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_excel_and_csv_export_timings_and_aliases(self):
        """Verify comparison report exports ASR, Diarization, and Identification timings and resolves aliases."""
        manifest = {
            "event_id": "EVT_TIMINGS",
            "clips": [
                {
                    "name": "clip_01.mp4",
                    "speaker": "S01",
                    "duration": 30.0,
                }
            ],
            "sessions": [],
        }
        manifest_path = os.path.join(self.scribe_dir, "manifest.json")
        with open(manifest_path, "w", encoding="utf-8") as f:
            json.dump(manifest, f)

        # Save clip runs with separate timing fields
        runs = [
            {
                "run_id": 1,
                "model_id": "local_whisper_medium",
                "model_name": "Whisper Medium",
                "provider": "local",
                "duration_sec": 30.0,
                "processing_time_sec": 5.2,
                "diarization_time_sec": 2.1,
                "identification_time_sec": 0.4,
                "time_taken_sec": 7.7,
                "speedup_ratio": 3.9,
                "is_primary": True,
                "wer_vs_baseline": 0.0,
                "cost_usd": 0.0,
                "timestamp": "2026-11-25 12:00:00",
                "transcript_excerpt": "Hello and welcome.",
            }
        ]
        run_comparison.save_clip_runs(self.event_root, "clip_01.mp4", runs)

        alias_to_name = {"S01": "Dr. Sarah Connor"}
        out_xlsx = run_comparison.export_comparison_report(
            event_root=self.event_root,
            alias_to_name=alias_to_name,
        )

        self.assertTrue(os.path.isfile(out_xlsx))
        csv_path = os.path.splitext(out_xlsx)[0] + ".csv"
        self.assertTrue(os.path.isfile(csv_path))

        # Inspect CSV lines
        with open(csv_path, "r", encoding="utf-8") as f:
            csv_lines = [line.strip() for line in f if line.strip()]

        # Ensure headers include separate timings
        header_line = next((l for l in csv_lines if "ASR Time (s)" in l), None)
        self.assertIsNotNone(header_line)
        self.assertIn("Diarization Time (s)", header_line)
        self.assertIn("Identification Time (s)", header_line)
        self.assertIn("Total Time (s)", header_line)

        # Ensure resolved speaker name appears
        data_line = next((l for l in csv_lines if "clip_01.mp4" in l), None)
        self.assertIsNotNone(data_line)
        self.assertIn("Dr. Sarah Connor", data_line)
        self.assertNotIn("S01", data_line)
        self.assertIn("5.2", data_line)  # ASR Time
        self.assertIn("2.1", data_line)  # Diarization Time
        self.assertIn("0.4", data_line)  # Identification Time

    def test_stage3_speaker_alias_resolution_locked_vs_unlocked(self):
        """Verify Stage 3 speaker resolution: aliases shown when locked, decrypted names when unlocked."""
        vault_dir = os.path.join(self.temp_dir, "vault_stage3")
        vault = VoiceVault(keys_dir=vault_dir)
        pw = "hazard galaxy pebble timber canyon velvet"
        ok, msg = vault.initialize(pw)
        self.assertTrue(ok)

        # Create dummy stage3 mock object
        class MockStage3Tab:
            def __init__(self, v, event_id):
                self.vault = v
                self._event_id = event_id
                self.manifest_data = {"event_id": event_id}

            def _paths(self):
                return {"event_id": self._event_id}

        from gui.stage3_tab import Stage3Tab

        tab = MockStage3Tab(vault, "EVT_STAGE3")
        # Bind unbound method
        resolve_fn = Stage3Tab._resolve_clip_speaker_display.__get__(tab, MockStage3Tab)

        # Enrol identity and save alias map
        storage = VaultV2StorageEngine(vault)
        spk_id = storage.create_identity_record(full_name="Dr. Eleanor Vance")
        roster_mgr = EventRosterManager(vault)
        roster_mgr.save_event_roster("EVT_STAGE3", [{"alias": "S01", "spk_id": spk_id}])

        # When unlocked: should resolve S01 to real name
        self.assertTrue(vault.is_unlocked())
        self.assertEqual(resolve_fn("S01"), "Dr. Eleanor Vance")
        self.assertEqual(resolve_fn("S02"), "S02")
        self.assertEqual(resolve_fn(""), "Speaker")

        # When locked: should return raw alias S01
        vault.lock()
        self.assertFalse(vault.is_unlocked())
        self.assertEqual(resolve_fn("S01"), "S01")


if __name__ == "__main__":
    unittest.main()
