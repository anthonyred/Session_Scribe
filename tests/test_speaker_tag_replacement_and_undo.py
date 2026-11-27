"""
Unit tests for line-start speaker tag isolation, combined files deduplication,
and Re-Identify archiving and undo.
"""

import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scribe_core.multi_speaker import substitute_line_start_speaker_tags
from scribe_core.combined_transcripts import build_combined_transcripts


class TestSpeakerTagReplacement(unittest.TestCase):
    def test_spoken_text_with_names_and_alias_tokens_preserved(self):
        """
        Verify that when a speaker's spoken text includes another speaker's name,
        their own name, or alias tokens (e.g. S01, S02), only the line-start
        speaker tag is modified. Spoken dialogue must NEVER be touched.
        """
        original_transcript = (
            "Event: Annual Summit 2026\n"
            "Speakers: S01 (Host), S02 (Speaker)\n"
            "--------------------------------------------------\n"
            "\n"
            "[S01] [00:01:00.000 - 00:01:10.000] I asked S02 and Dr. Ada Lovelace about S01 yesterday.\n"
            "[S02] [00:01:10.000 - 00:01:20.000] Yes, S01, Dr. Ada Lovelace and Charles Babbage agree that S02 was right.\n"
        )

        alias_to_name = {
            "S01": "Dr. Ada Lovelace",
            "S02": "Charles Babbage",
        }

        # 1. Unlocked state: Alias -> Real Name
        unlocked_text, count = substitute_line_start_speaker_tags(original_transcript, alias_to_name)
        self.assertGreater(count, 0)

        # Header check: Speakers: line updated
        self.assertIn("Speakers: Dr. Ada Lovelace (Host), Charles Babbage (Speaker)", unlocked_text)

        # Line-start turn tag check: leading tags replaced
        self.assertIn("[Dr. Ada Lovelace] [00:01:00.000 - 00:01:10.000]", unlocked_text)
        self.assertIn("[Charles Babbage] [00:01:10.000 - 00:01:20.000]", unlocked_text)

        # CRITICAL: Spoken text must remain 100% unaltered
        self.assertIn("I asked S02 and Dr. Ada Lovelace about S01 yesterday.", unlocked_text)
        self.assertIn("Yes, S01, Dr. Ada Lovelace and Charles Babbage agree that S02 was right.", unlocked_text)

        # 2. Locked state: Real Name -> Alias (Reverse mapping)
        name_to_alias = {v: k for k, v in alias_to_name.items()}
        relocked_text, rev_count = substitute_line_start_speaker_tags(unlocked_text, name_to_alias)
        self.assertGreater(rev_count, 0)

        self.assertIn("Speakers: S01 (Host), S02 (Speaker)", relocked_text)
        self.assertIn("[S01] [00:01:00.000 - 00:01:10.000]", relocked_text)
        self.assertIn("[S02] [00:01:10.000 - 00:01:20.000]", relocked_text)
        self.assertIn("I asked S02 and Dr. Ada Lovelace about S01 yesterday.", relocked_text)
        self.assertIn("Yes, S01, Dr. Ada Lovelace and Charles Babbage agree that S02 was right.", relocked_text)

        # Ensure exact match with original
        self.assertEqual(original_transcript.strip(), relocked_text.strip())

    def test_single_speaker_header_replaced(self):
        content = (
            "Speaker: S01\n"
            "Role: Speaker\n"
            "\n"
            "[S01] [00:00:01.000 - 00:00:05.000] Hello everyone.\n"
        )
        tag_map = {"S01": "Alice Smith"}
        res, cnt = substitute_line_start_speaker_tags(content, tag_map)
        self.assertEqual(cnt, 2)
        self.assertIn("Speaker: Alice Smith", res)
        self.assertIn("[Alice Smith] [00:00:01.000 - 00:00:05.000] Hello everyone.", res)


class TestCombinedFilesDeduplication(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.event_dir = os.path.join(self.test_dir, "event_01")
        self.transcripts_dir = os.path.join(self.event_dir, "scribe", "transcripts")
        self.combined_dir = os.path.join(self.event_dir, "scribe", "combined")
        self.state_dir = os.path.join(self.event_dir, "scribe", "state")
        os.makedirs(self.transcripts_dir, exist_ok=True)
        os.makedirs(self.combined_dir, exist_ok=True)
        os.makedirs(self.state_dir, exist_ok=True)

        self.paths = {
            "event_root": self.event_dir,
            "transcripts": self.transcripts_dir,
            "combined": self.combined_dir,
            "state": self.state_dir,
        }

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_combined_files_stale_duplicate_avoidance(self):
        """
        When combined transcripts are rebuilt with alias_to_name_map,
        stale alternate combined files (e.g. S01_combined.txt) must be archived to
        scribe/combined/old/ and not remain side-by-side with Dr._Ada_Lovelace_combined.txt.
        """
        # Create a sample transcript
        tfile = os.path.join(self.transcripts_dir, "clip_01.txt")
        with open(tfile, "w", encoding="utf-8") as f:
            f.write(
                "Event: Test Event\n"
                "Clip: clip_01.mp4\n"
                "Speakers: S01 (Host)\n"
                "Transcription Completed: Yes\n"
                "--------------------------------------------------\n"
                "\n"
                "[S01] [00:00:00.000 - 00:00:05.000] Testing combined deduplication.\n"
            )

        manifest_data = {
            "sessions": [
                {
                    "speaker": "S01",
                    "clips": [{"name": "clip_01.mp4", "path": "d:/dummy/clip_01.mp4", "approved": True}],
                }
            ]
        }
        alias_map = {"S01": "Dr. Ada Lovelace"}

        # 1. Run build_combined_transcripts WITHOUT alias_map (e.g. locked vault)
        # Produces S01_combined.txt
        res1 = build_combined_transcripts(self.event_dir, manifest_data, alias_to_name=None)
        self.assertIn("S01_combined.txt", res1["counts"])
        s01_path = os.path.join(self.combined_dir, "S01_combined.txt")
        self.assertTrue(os.path.isfile(s01_path))

        # 2. Run build_combined_transcripts WITH alias_map (e.g. unlocked vault)
        # Must produce Dr._Ada_Lovelace_combined.txt AND move S01_combined.txt to old/
        res2 = build_combined_transcripts(self.event_dir, manifest_data, alias_to_name=alias_map)
        self.assertIn("Dr._Ada_Lovelace_combined.txt", res2["counts"])
        ada_path = os.path.join(self.combined_dir, "Dr._Ada_Lovelace_combined.txt")
        self.assertTrue(os.path.isfile(ada_path))

        # S01_combined.txt must NO LONGER exist in main combined_dir
        self.assertFalse(os.path.isfile(s01_path))

        # S01_combined.txt must be safely archived in combined/old/
        old_s01_path = os.path.join(self.combined_dir, "old", "S01_combined.txt")
        self.assertTrue(os.path.isfile(old_s01_path))


class TestReidentifyArchivingAndUndo(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.event_dir = os.path.join(self.test_dir, "event_02")
        self.transcripts_dir = os.path.join(self.event_dir, "scribe", "transcripts")
        self.state_dir = os.path.join(self.event_dir, "scribe", "state")
        os.makedirs(self.transcripts_dir, exist_ok=True)
        os.makedirs(self.state_dir, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_reidentify_archiving_and_undo_restores_original_content(self):
        from gui.stage3_tab import Stage3Tab

        # Setup mock stage3 instance
        class MockStage3:
            current_event_dir = self.event_dir
            paths = {
                "event_root": self.event_dir,
                "transcripts": self.transcripts_dir,
                "state": self.state_dir,
            }
            logs = []

            def _log(self, msg):
                self.logs.append(msg)

            def _refresh_status_column(self):
                pass

            def _update_combine_and_approval_ui(self):
                pass

        mock_s3 = MockStage3()
        # Bind Stage3Tab helper methods
        apply_fn = Stage3Tab._apply_cluster_reassignments_to_transcripts.__get__(mock_s3)

        transcript_path = os.path.join(self.transcripts_dir, "session_01.txt")
        original_content = (
            "Event: Test Event\n"
            "Speakers: S01 (Speaker), [Unidentified]\n"
            "Transcription Completed: Yes\n"
            "--------------------------------------------------\n"
            "\n"
            "[Unidentified] [00:00:10.000 - 00:00:15.000] Hello from unidentified speaker.\n"
            "[S01] [00:00:15.000 - 00:00:20.000] Welcome.\n"
        )
        with open(transcript_path, "w", encoding="utf-8") as f:
            f.write(original_content)

        # Apply re-identification: [Unidentified] -> S02
        assignments = {"[Unidentified]": "S02"}
        count, backup_dir = apply_fn(self.transcripts_dir, assignments)

        self.assertGreater(count, 0)
        self.assertTrue(os.path.isdir(backup_dir))

        # Check backup content
        backup_file = os.path.join(backup_dir, "session_01.txt")
        self.assertTrue(os.path.isfile(backup_file))
        with open(backup_file, "r", encoding="utf-8") as bf:
            self.assertEqual(bf.read(), original_content)

        # Check modified file
        with open(transcript_path, "r", encoding="utf-8") as mf:
            modified_content = mf.read()
        self.assertIn("[S02] [00:00:10.000 - 00:00:15.000]", modified_content)
        self.assertNotIn("[Unidentified] [00:00:10.000", modified_content)

        # Now test Undo restoration
        import tkinter.messagebox as mb
        orig_askyesno = mb.askyesno
        orig_showinfo = mb.showinfo
        try:
            mb.askyesno = lambda *args, **kwargs: True
            mb.showinfo = lambda *args, **kwargs: None

            undo_fn = Stage3Tab._undo_reidentify_clicked.__get__(mock_s3)
            undo_fn()

            with open(transcript_path, "r", encoding="utf-8") as uf:
                restored_content = uf.read()

            self.assertEqual(restored_content, original_content)
        finally:
            mb.askyesno = orig_askyesno
            mb.showinfo = orig_showinfo

    def test_discover_clusters_from_historical_transcripts(self):
        """
        Verify that Re-Identify can inspect historical events transcribed prior to
        this change and identify unknown speech clusters without GPU or re-transcription.
        """
        from gui.stage3_tab import Stage3Tab

        class MockStage3:
            pass

        mock_s3 = MockStage3()
        discover_fn = Stage3Tab._discover_unidentified_clusters_from_transcripts.__get__(mock_s3)

        # Create two legacy transcript files with unidentified / generic tags
        t1 = os.path.join(self.transcripts_dir, "legacy_01.txt")
        with open(t1, "w", encoding="utf-8") as f:
            f.write(
                "Event: Legacy 2025\n"
                "Transcription Completed: Yes\n"
                "--------------------------------------------------\n"
                "\n"
                "[Unidentified] [00:00:05.000 - 00:00:10.000] Mystery speaker turn.\n"
                "[Speaker 1] [00:00:10.000 - 00:00:15.000] Another generic speaker.\n"
            )

        t2 = os.path.join(self.transcripts_dir, "legacy_02.txt")
        with open(t2, "w", encoding="utf-8") as f:
            f.write(
                "Event: Legacy 2025\n"
                "Transcription Completed: Yes\n"
                "--------------------------------------------------\n"
                "\n"
                "[Unidentified] [00:01:00.000 - 00:01:05.000] Second appearance.\n"
            )

        clusters = discover_fn(self.transcripts_dir)
        cluster_ids = {c.cluster_id for c in clusters}

        self.assertIn("[Unidentified]", cluster_ids)
        self.assertIn("Speaker 1", cluster_ids)

        unid_cluster = next(c for c in clusters if c.cluster_id == "[Unidentified]")
        # 1 initial + 1 additional turn = sample_count 2
        self.assertEqual(unid_cluster.sample_count, 2)
        self.assertEqual(len(unid_cluster.turn_intervals), 2)


if __name__ == "__main__":
    unittest.main()
