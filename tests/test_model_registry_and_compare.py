"""
tests/test_model_registry_and_compare.py

Tests for model registry, Replace vs Compare execution recording,
Word Error Rate (WER) computation, primary run switching, and pure-Python Excel export.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest
import zipfile

# Sys path guard per AGENTS.md Rule 4
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from scribe_core import model_registry
from scribe_core import run_comparison


class TestModelRegistry(unittest.TestCase):
    """Test model registry catalog, 4GB VRAM limits, and rate-limit warnings."""

    def test_local_models_fit_4gb_gpu(self):
        local_models = model_registry.get_local_models(max_vram_mb=4096)
        ids = [m.id for m in local_models]
        self.assertIn("whisper-tiny", ids)
        self.assertIn("whisper-base", ids)
        self.assertIn("whisper-small", ids)
        self.assertIn("whisper-medium", ids)
        for m in local_models:
            self.assertTrue(m.fits_4gb_gpu)
            self.assertLessEqual(m.vram_mb, 4000)
            self.assertEqual(m.provider, "local")
            self.assertEqual(m.cost_per_minute_usd, 0.0)

    def test_cloud_models_and_nemotron_template(self):
        cloud_models = model_registry.get_cloud_models(include_templates=True)
        c_dict = {m.id: m for m in cloud_models}
        self.assertIn("nvidia/parakeet-ctc-1.1b", c_dict)
        self.assertTrue(c_dict["nvidia/parakeet-ctc-1.1b"].is_active)
        self.assertFalse(c_dict["nvidia/parakeet-ctc-1.1b"].is_disabled_template)

        self.assertIn("openai/whisper-large-v3", c_dict)
        self.assertTrue(c_dict["openai/whisper-large-v3"].is_active)

        self.assertIn("nvidia/nemotron-speech", c_dict)
        self.assertTrue(c_dict["nvidia/nemotron-speech"].is_disabled_template)
        self.assertFalse(c_dict["nvidia/nemotron-speech"].is_active)

    def test_rate_limit_warning(self):
        # 5 minutes -> no warning for Parakeet (threshold 10 min)
        warn_short = model_registry.check_rate_limit_warning("nvidia/parakeet-ctc-1.1b", 300.0)
        self.assertIsNone(warn_short)

        # 15 minutes -> warning for Parakeet
        warn_long = model_registry.check_rate_limit_warning("nvidia/parakeet-ctc-1.1b", 900.0)
        self.assertIsNotNone(warn_long)
        self.assertIn("exceeds recommended", warn_long)

    def test_cost_estimation(self):
        # 10 minutes on Parakeet ($0.003/min)
        est = model_registry.estimate_run_cost("nvidia/parakeet-ctc-1.1b", 600.0)
        self.assertEqual(est["cost_usd"], 0.03)
        self.assertGreater(est["speedup"], 10.0)

        # Local is always $0.00
        est_loc = model_registry.estimate_run_cost("whisper-medium", 600.0)
        self.assertEqual(est_loc["cost_usd"], 0.0)


class TestRunComparison(unittest.TestCase):
    """Test WER metric, Replace vs Compare history recording, primary run switching, and export."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.event_root = os.path.join(self.temp_dir, "Event_Test")
        self.trans_dir = os.path.join(self.event_root, "scribe", "transcripts")
        os.makedirs(self.trans_dir, exist_ok=True)

        self.clip_name = "clip_01.mp4"
        self.manifest = {
            "event_name": "Test Event",
            "clips": [{"name": self.clip_name, "speaker": "Speaker One", "duration": 120.0}],
            "sessions": [{"session_id": 1, "speaker": "Speaker One", "clips": [{"name": self.clip_name}]}],
        }
        with open(os.path.join(self.event_root, "scribe", "manifest.json"), "w", encoding="utf-8") as fh:
            json.dump(self.manifest, fh)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_wer_calculation(self):
        # Exact match -> 0.0
        self.assertEqual(run_comparison.calculate_wer("Welcome to the show", "Welcome to the show"), 0.0)

        # 1 substitution in 4 words -> 0.25 (25%)
        wer = run_comparison.calculate_wer("Welcome to the show", "Welcome to the conference")
        self.assertAlmostEqual(wer, 0.25, places=2)

        # Empty strings
        self.assertEqual(run_comparison.calculate_wer("", ""), 0.0)
        self.assertEqual(run_comparison.calculate_wer("Hello", ""), 1.0)

    def test_diff_transcripts(self):
        t1 = "Line one\nLine two of presentation\nLine three"
        t2 = "Line one\nLine two with modifications\nLine three"
        res = run_comparison.diff_transcripts(t1, t2)
        self.assertIn("wer", res)
        self.assertIn("unified_diff", res)
        self.assertGreater(res["wer"], 0.0)

    def test_compare_mode_appends_and_preserves_baseline(self):
        # Run 1: Baseline with Whisper Medium
        text1 = "Welcome to the first session on audio engineering."
        r1 = run_comparison.record_run(
            event_root=self.event_root,
            clip_name=self.clip_name,
            model_id="whisper-medium",
            model_name="Whisper Medium",
            provider="local",
            duration_sec=60.0,
            time_taken_sec=24.0,
            transcript_text=text1,
            mode="compare",
        )
        self.assertEqual(r1["run_id"], 1)
        self.assertTrue(r1["is_primary"])

        # Run 2: Compare with Parakeet
        text2 = "Welcome to the first session in audio engineering."
        r2 = run_comparison.record_run(
            event_root=self.event_root,
            clip_name=self.clip_name,
            model_id="nvidia/parakeet-ctc-1.1b",
            model_name="NVIDIA Parakeet CTC 1.1B",
            provider="nvidia",
            duration_sec=60.0,
            time_taken_sec=3.5,
            transcript_text=text2,
            mode="compare",
        )
        self.assertEqual(r2["run_id"], 2)
        self.assertFalse(r2["is_primary"])
        self.assertGreater(r2["wer_vs_baseline"], 0.0)

        runs = run_comparison.load_clip_runs(self.event_root, self.clip_name)
        self.assertEqual(len(runs), 2)

        # Active transcript on disk matches primary (run 1)
        txt_path = os.path.join(self.trans_dir, "clip_01.txt")
        with open(txt_path, "r", encoding="utf-8") as fh:
            self.assertEqual(fh.read(), text1)

    def test_set_primary_run(self):
        text1 = "Version 1 text"
        text2 = "Version 2 text with improvements"
        run_comparison.record_run(
            self.event_root, self.clip_name, "whisper-medium", "Medium", "local", 60.0, 20.0, text1, mode="compare"
        )
        run_comparison.record_run(
            self.event_root, self.clip_name, "openai/whisper-large-v3", "Large-v3", "nvidia", 60.0, 8.0, text2, mode="compare"
        )

        # Promote Run 2 to primary
        success = run_comparison.set_primary_run(self.event_root, self.clip_name, target_run_id=2)
        self.assertTrue(success)

        runs = run_comparison.load_clip_runs(self.event_root, self.clip_name)
        self.assertFalse(runs[0]["is_primary"])
        self.assertTrue(runs[1]["is_primary"])

        # Verify disk transcript was updated to run 2 text
        txt_path = os.path.join(self.trans_dir, "clip_01.txt")
        with open(txt_path, "r", encoding="utf-8") as fh:
            self.assertEqual(fh.read(), text2)

    def test_replace_mode_archives_and_overwrites(self):
        text1 = "Initial text"
        text2 = "Replaced text"
        run_comparison.record_run(
            self.event_root, self.clip_name, "whisper-medium", "Medium", "local", 60.0, 20.0, text1, mode="compare"
        )
        # Re-transcribe with Replace
        r_repl = run_comparison.record_run(
            self.event_root, self.clip_name, "whisper-small", "Small", "local", 60.0, 10.0, text2, mode="replace"
        )
        self.assertTrue(r_repl["is_primary"])

        # Check archive exists
        arch_file = os.path.join(self.event_root, "scribe", "transcripts", "archive", "clip_01_prior.txt")
        self.assertTrue(os.path.isfile(arch_file))
        with open(arch_file, "r", encoding="utf-8") as fh:
            self.assertEqual(fh.read(), text1)

        # Check active transcript is replaced
        txt_path = os.path.join(self.trans_dir, "clip_01.txt")
        with open(txt_path, "r", encoding="utf-8") as fh:
            self.assertEqual(fh.read(), text2)

    def test_export_pure_xlsx_and_csv(self):
        # Create multi-run data
        run_comparison.record_run(
            self.event_root, self.clip_name, "whisper-medium", "Whisper Medium", "local", 120.0, 48.0, "Sample speech text run one.", mode="compare"
        )
        run_comparison.record_run(
            self.event_root, self.clip_name, "nvidia/parakeet-ctc-1.1b", "Parakeet CTC 1.1B", "nvidia", 120.0, 6.5, "Sample speech text run two.", mode="compare"
        )

        out_xlsx = run_comparison.export_comparison_report(self.event_root)
        self.assertTrue(os.path.isfile(out_xlsx))
        self.assertTrue(out_xlsx.endswith(".xlsx"))

        # Verify xlsx is valid zip archive with standard spreadsheet parts
        with zipfile.ZipFile(out_xlsx, "r") as zf:
            namelist = zf.namelist()
            self.assertIn("[Content_Types].xml", namelist)
            self.assertIn("xl/workbook.xml", namelist)
            self.assertIn("xl/worksheets/sheet1.xml", namelist)
            self.assertIn("xl/worksheets/sheet2.xml", namelist)

        # Verify companion CSV with UTF-8 BOM
        csv_path = os.path.splitext(out_xlsx)[0] + ".csv"
        self.assertTrue(os.path.isfile(csv_path))
        with open(csv_path, "rb") as fh:
            content = fh.read()
            self.assertTrue(content.startswith(b"\xef\xbb\xbf"))  # UTF-8 BOM


class TestStage3CompareIntegration(unittest.TestCase):
    """Test Stage3Tab UI integration with Model Choice, Comparison Drawer, and Export."""

    def setUp(self):
        import tkinter as tk
        from gui.stage3_tab import Stage3Tab, ReTranscribeDialog
        from gui.event_context import EventContext
        from scribe_core.voice_vault import VoiceVault

        self.temp_dir = tempfile.mkdtemp()
        self.event_root = os.path.join(self.temp_dir, "Event_GUI_Test")
        self.scribe_dir = os.path.join(self.event_root, "scribe")
        self.trans_dir = os.path.join(self.scribe_dir, "transcripts")
        self.state_dir = os.path.join(self.scribe_dir, "state")
        os.makedirs(self.trans_dir, exist_ok=True)
        os.makedirs(self.state_dir, exist_ok=True)

        self.clip_name = "clip_01.mp4"
        self.clip_path = os.path.join(self.event_root, "data", self.clip_name)
        os.makedirs(os.path.dirname(self.clip_path), exist_ok=True)
        with open(self.clip_path, "wb") as fh:
            fh.write(b"dummy")

        self.manifest = {
            "event_name": "GUI Test Event",
            "event_root": self.event_root,
            "clips": [{"name": self.clip_name, "path": self.clip_path, "speaker": "Speaker One", "duration": 120.0, "status": "Done"}],
            "sessions": [{"session_id": 1, "speaker": "Speaker One", "clips": [{"name": self.clip_name, "path": self.clip_path, "speaker": "Speaker One", "duration": 120.0}]}],
        }
        with open(os.path.join(self.scribe_dir, "manifest.json"), "w", encoding="utf-8") as fh:
            json.dump(self.manifest, fh)

        with open(os.path.join(self.trans_dir, "clip_01.txt"), "w", encoding="utf-8") as fh:
            fh.write("Baseline transcript audio text.\n")

        self.root = tk.Tk()
        self.root.withdraw()
        self.ctx = EventContext()
        self.ctx.event_dir_var.set(self.event_root)
        self.ctx.event_name_var.set("GUI Test Event")
        self.ctx.event_date_var.set("2026-11-17")

        self.vault = VoiceVault(keys_dir=os.path.join(self.temp_dir, "keys"))
        self.tab = Stage3Tab(self.root, ctx=self.ctx, vault=self.vault)
        self.tab._load_manifest(silent=True)
        self.root.update()

    def tearDown(self):
        try:
            self.root.destroy()
        except Exception:
            pass
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_retranscribe_dialog_estimates_and_nemotron_disabled(self):
        import tkinter as tk
        from gui.stage3_tab import ReTranscribeDialog

        dlg = ReTranscribeDialog(
            parent=self.root,
            clips=self.tab.clips,
            on_start_callback=lambda m, mod: None,
            event_root=self.event_root,
        )
        self.root.update()

        # Check default mode & model
        self.assertEqual(dlg.mode_var.get(), "compare")
        self.assertEqual(dlg.model_var.get(), "whisper-medium")
        self.assertIn("Estimated Time:", dlg.est_info_var.get())

        # Select Nemotron -> disabled
        dlg.model_var.set("nvidia/nemotron-speech")
        dlg._update_estimates()
        self.assertEqual(str(dlg.btn_start["state"]), str(tk.DISABLED))

        # Select Parakeet -> enabled, cost shown
        dlg.model_var.set("nvidia/parakeet-ctc-1.1b")
        dlg._update_estimates()
        self.assertEqual(str(dlg.btn_start["state"]), str(tk.NORMAL))
        self.assertIn("Cost: $", dlg.est_info_var.get())

        dlg.destroy()

    def test_compare_flow_shows_child_rows_and_drawer(self):
        from tkinter import messagebox
        from unittest.mock import patch
        from scribe_core import cloud_engine
        from scribe_core.usage_ledger import UsageLedger

        orig_info = messagebox.showinfo
        messagebox.showinfo = lambda *args, **kwargs: None

        def fake_extract(_bin, _src, dst, start_sec=0.0, duration_sec=None):
            with open(dst, "wb") as f:
                f.write(cloud_engine.make_silence_wav(1.0))
            return 120.0

        mock_words = [
            {"word": "Re-transcribed", "start": 0.0, "end": 0.5},
            {"word": "with", "start": 0.5, "end": 1.0},
            {"word": "NVIDIA", "start": 1.0, "end": 1.5},
            {"word": "Parakeet", "start": 1.5, "end": 2.0},
        ]

        try:
            with patch("scribe_core.transcribe_worker.extract_audio_slice", side_effect=fake_extract), \
                 patch("scribe_core.cloud_engine.find_silence_split_points", return_value=[(0.0, 120.0)]), \
                 patch("scribe_core.cloud_engine.get_nvidia_api_key", return_value="nvapi-mock-test-key"), \
                 patch("scribe_core.cloud_engine.transcribe_audio_chunk_riva", return_value=mock_words):

                # 1. Execute re-transcription in compare mode
                self.tab._execute_retranscription(self.tab.clips, mode="compare", model_id="nvidia/parakeet-ctc-1.1b")
                self.root.update()

                # Check child row inserted under item '1'
                children = self.tab.clip_tree.get_children("1")
                self.assertEqual(len(children), 2)  # Run 1 and Run 2

                child1_vals = self.tab.clip_tree.item(children[0], "values")
                self.assertIn("↳ #1", child1_vals[1])
                self.assertEqual(child1_vals[7], "Baseline")

                child2_vals = self.tab.clip_tree.item(children[1], "values")
                self.assertIn("↳ #2", child2_vals[1])
                self.assertIn("WER:", child2_vals[7])
                self.assertEqual(child2_vals[8], "[Make Primary]")

                # Check comparison drawer populated
                self.tab._show_comparison_drawer(self.clip_name)
                self.root.update()
                self.assertIn("WER:", self.tab.comp_metrics_var.get())
                self.assertIn("Run #1", self.tab.lbl_comp_run1.cget("text"))
                self.assertIn("Run #2", self.tab.lbl_comp_run2.cget("text"))

                # Promote Run 2 to primary
                self.tab._promote_comparison_to_primary()
                self.root.update()

                # Verify disk transcript matches run 2
                with open(os.path.join(self.trans_dir, "clip_01.txt"), "r", encoding="utf-8") as fh:
                    content = fh.read()
                    self.assertIn("Re-transcribed with", content)

                # Check usage ledger logged attempts
                ledger = UsageLedger(self.event_root)
                records = ledger.read_event_records()
                self.assertGreaterEqual(len(records), 1)
                self.assertEqual(records[-1]["model"], "nvidia/parakeet-ctc-1.1b")
                self.assertGreater(records[-1].get("uploaded_bytes", 0), 0)
                self.assertGreaterEqual(records[-1].get("requests_count", 0), 1)
        finally:
            messagebox.showinfo = orig_info

    def test_cloud_retranscription_must_have_nonzero_upload_and_requests(self):
        """Zero-byte and zero-request guard: cloud re-transcription MUST fail if 0 bytes or 0 requests."""
        from unittest.mock import patch

        clip_dict = {"name": self.clip_name, "path": self.clip_path, "speaker": "Speaker One", "duration": 120.0}

        # Case 1: Silence split points empty -> 0 chunks, 0 bytes, 0 requests -> MUST RAISE RuntimeError
        with patch("scribe_core.transcribe_worker.extract_audio_slice", return_value=120.0), \
             patch("scribe_core.cloud_engine.find_silence_split_points", return_value=[]), \
             patch("scribe_core.cloud_engine.get_nvidia_api_key", return_value="nvapi-mock-key"):
            with self.assertRaises(RuntimeError) as ctx:
                run_comparison.execute_clip_retranscription(
                    event_root=self.event_root,
                    clip_dict=clip_dict,
                    model_id="nvidia/parakeet-ctc-1.1b",
                    mode="compare",
                    api_key="nvapi-mock-key",
                )
            self.assertIn("0 bytes uploaded or 0 requests completed", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()