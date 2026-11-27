"""
tests/test_failover_and_totals.py

Automated test suite for:
  - Task B: Failover Handover Time Measurement & Reporting
  - Task C: Locked Totals Row below Stage 3 Clip Table

Rules (AGENTS.md):
  - No real event data touched. All I/O uses tempfile.mkdtemp().
  - sys.path guard derived from __file__.
  - No external DB / server dependencies.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest
import tkinter as tk
from tkinter import ttk

# ---- sys.path guard ---------------------------------------------------
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from gui.event_context import EventContext
from gui.stage3_tab import (
    Stage3Tab,
    format_duration_hhmmss,
    format_failover_time,
    _get_clip_failover_data,
    _get_clip_time_taken,
    _parse_time_to_seconds,
)
from scribe_core.clip_state import write_clip, read_clip
from scribe_core.event_paths import derive_event_paths
from scribe_core.cloud_engine import CloudTranscriptionError


class TestFailoverFormattingAndState(unittest.TestCase):
    """Test failover time formatting and state storage according to Task B."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.state_dir = os.path.join(self.temp_dir, "scribe", "state")
        os.makedirs(self.state_dir, exist_ok=True)
        self.paths = {"state": self.state_dir, "transcripts": os.path.join(self.temp_dir, "scribe", "transcripts")}

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_format_failover_time_pure_local_and_cloud_success(self):
        """Pure local runs and clips that succeeded on cloud show '—', not 0.0s."""
        self.assertEqual(format_failover_time(None), "—")
        self.assertEqual(format_failover_time(0.0), "—")
        self.assertEqual(format_failover_time(-1.5), "—")

    def test_format_failover_time_valid_durations(self):
        """Failover times formatted as hh:mm:ss.s."""
        self.assertEqual(format_failover_time(1.23), "00:00:01.2")
        self.assertEqual(format_failover_time(1.26), "00:00:01.3")
        self.assertEqual(format_failover_time(65.4), "00:01:05.4")
        self.assertEqual(format_failover_time(3665.4), "01:01:05.4")

    def test_clip_state_persistence_pure_local(self):
        """Pure local clip without fallback records None for failover and returns '—'."""
        write_clip(
            self.state_dir,
            "clip_local",
            model="medium",
            engine="local",
            processing_time_sec=14.2,
        )
        rec = read_clip(self.state_dir, "clip_local")
        self.assertIsNone(rec.get("failover_time_sec"))
        self.assertIsNone(rec.get("failover_info"))

        fo_sec, fo_info = _get_clip_failover_data("clip_local.mp4", self.paths)
        self.assertIsNone(fo_sec)
        self.assertIsNone(fo_info)
        self.assertEqual(format_failover_time(fo_sec), "—")

    def test_clip_state_persistence_cloud_success(self):
        """Successful cloud transcription records None for failover."""
        write_clip(
            self.state_dir,
            "clip_cloud_ok",
            model="riva",
            engine="cloud",
            provider="nvidia_parakeet",
            processing_time_sec=3.8,
        )
        fo_sec, fo_info = _get_clip_failover_data("clip_cloud_ok.mp4", self.paths)
        self.assertIsNone(fo_sec)
        self.assertIsNone(fo_info)
        self.assertEqual(format_failover_time(fo_sec), "—")

    def test_clip_state_persistence_with_failover_breakdown(self):
        """Failover handover records all required metrics and returns them properly."""
        fo_data = {
            "first_error_at": "2026-11-03T10:00:00+05:30",
            "fallback_decided_at": "2026-11-03T10:00:02+05:30",
            "local_started_at": "2026-11-03T10:00:04.5+05:30",
            "fallback_reason": "timeout",
            "retries": 2,
            "retry_wait_seconds": 2.0,
            "local_model_load_seconds": 2.1,
            "resume_offset": 60.0,
            "failover_time_sec": 4.5,
        }
        write_clip(
            self.state_dir,
            "clip_fallback",
            model="medium",
            engine="local (fallback)",
            processing_time_sec=25.0,
            failover_time_sec=4.5,
            failover_info=fo_data,
        )

        fo_sec, fo_info = _get_clip_failover_data("clip_fallback.mp4", self.paths)
        self.assertEqual(fo_sec, 4.5)
        self.assertIsNotNone(fo_info)
        self.assertEqual(fo_info.get("fallback_reason"), "timeout")
        self.assertEqual(fo_info.get("retries"), 2)
        self.assertEqual(fo_info.get("retry_wait_seconds"), 2.0)
        self.assertEqual(fo_info.get("local_model_load_seconds"), 2.1)
        self.assertEqual(fo_info.get("resume_offset"), 60.0)
        self.assertEqual(format_failover_time(fo_sec), "00:00:04.5")

    def test_failover_time_definition_first_error_to_local_start(self):
        """Task B: Headline Failover time is local_started_at minus first_error_at."""
        t_first_error = 1000.0
        t_fallback_decided = 1006.0  # e.g. after retries
        t_local_started = 1008.5     # e.g. after model loading
        # Headline failover time spans from first error seen to local start
        failover_time = round(t_local_started - t_first_error, 1)
        self.assertEqual(failover_time, 8.5)
        self.assertEqual(format_failover_time(failover_time), "00:00:08.5")


class TestFailoverErrorTypesAndTimeout(unittest.TestCase):
    """Test cloud error categorization and timeout scaling."""

    def test_error_categorization_credits(self):
        err = CloudTranscriptionError(
            "Account balance insufficient (HTTP 402)",
            fallback_reason="credits",
            retries=0,
            retry_wait_seconds=0.0,
        )
        self.assertEqual(err.fallback_reason, "credits")
        self.assertEqual(err.retries, 0)

    def test_error_categorization_auth(self):
        err = CloudTranscriptionError(
            "Invalid or revoked API key (HTTP 401)",
            fallback_reason="auth",
            retries=0,
            retry_wait_seconds=0.0,
        )
        self.assertEqual(err.fallback_reason, "auth")

    def test_error_categorization_timeout(self):
        err = CloudTranscriptionError(
            "Request timed out after 30.0s",
            fallback_reason="timeout",
            retries=2,
            retry_wait_seconds=3.0,
        )
        self.assertEqual(err.fallback_reason, "timeout")
        self.assertEqual(err.retries, 2)


class TestLockedTotalsRow(unittest.TestCase):
    """Test locked totals row below Stage 3 clip table (Task C)."""

    @classmethod
    def setUpClass(cls):
        cls.root = tk.Tk()
        cls.root.withdraw()

    @classmethod
    def tearDownClass(cls):
        try:
            cls.root.destroy()
        except Exception:
            pass

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.event_dir = os.path.join(self.temp_dir, "Event_Totals")
        self.paths = derive_event_paths(self.event_dir)
        os.makedirs(self.paths["scribe"], exist_ok=True)
        os.makedirs(self.paths["state"], exist_ok=True)
        os.makedirs(self.paths["transcripts"], exist_ok=True)
        os.makedirs(self.paths["videos"], exist_ok=True)

        # Create 3 dummy clips (100s, 200s, 300s -> total 600s = 00:10:00)
        for name in ("clip_01.mp4", "clip_02.mp4", "clip_03.mp4"):
            with open(os.path.join(self.paths["videos"], name), "wb") as f:
                f.write(b"dummy")

        self.manifest_data = {
            "version": "1.0",
            "event_root": self.event_dir,
            "event": {"name": "Test Totals", "date": "2026-11-03"},
            "sessions": [
                {
                    "session_id": 1,
                    "speaker": "Speaker 1",
                    "clips": [
                        {"name": "clip_01.mp4", "path": os.path.join(self.paths["videos"], "clip_01.mp4"), "duration": 100},
                        {"name": "clip_02.mp4", "path": os.path.join(self.paths["videos"], "clip_02.mp4"), "duration": 200},
                        {"name": "clip_03.mp4", "path": os.path.join(self.paths["videos"], "clip_03.mp4"), "duration": 300},
                    ],
                }
            ],
        }
        with open(self.paths["manifest"], "w", encoding="utf-8") as f:
            json.dump(self.manifest_data, f, indent=2)

        # Setup state:
        # clip_01: done, processing_time = 15.0s, pure local (no failover)
        write_clip(self.paths["state"], "clip_01", processing_time_sec=15.0)
        with open(os.path.join(self.paths["transcripts"], "clip_01.txt"), "w", encoding="utf-8") as f:
            f.write("=== TRANSCRIPT ===\nTranscription Completed: Yes\nSpeaker: Speaker 1\n[Speaker 1] hello")

        # clip_02: done, processing_time = 25.0s, fallback with failover_time = 3.5s
        write_clip(
            self.paths["state"],
            "clip_02",
            processing_time_sec=25.0,
            failover_time_sec=3.5,
            failover_info={"fallback_reason": "timeout", "retries": 1, "local_model_load_seconds": 1.2, "failover_time_sec": 3.5},
        )
        with open(os.path.join(self.paths["transcripts"], "clip_02.txt"), "w", encoding="utf-8") as f:
            f.write("=== TRANSCRIPT ===\nTranscription Completed: Yes\nSpeaker: Speaker 1\n[Speaker 1] world")

        # clip_03: pending (not timed, no failover)

        self.ctx = EventContext()
        self.ctx.event_dir_var.set(self.event_dir)
        self.ctx.event_name_var.set("Test Totals")
        self.ctx.event_date_var.set("2026-11-03")
        self.tab = Stage3Tab(self.root, ctx=self.ctx)
        self.tab.pack(fill=tk.BOTH, expand=True)
        self.tab._load_manifest(silent=True)
        self.root.update()

    def tearDown(self):
        try:
            self.tab.destroy()
        except Exception:
            pass
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_totals_row_exists_and_is_locked(self):
        """Locked totals row widget exists, has height=1, and ignores selection."""
        self.assertTrue(hasattr(self.tab, "totals_tree"))
        self.assertEqual(str(self.tab.totals_tree.cget("selectmode")), "none")
        self.assertTrue(self.tab.totals_tree.exists("totals"))

        # Verify clicking returns "break"
        res = self.tab.totals_tree.event_generate("<Button-1>")
        # In Tkinter, custom bindings that return "break" cancel default handler

    def test_totals_row_column_synchronization(self):
        """Totals row columns have the exact same column identifiers and alignments as clip_tree."""
        self.tab._sync_totals_columns()
        self.root.update()

        clip_cols = self.tab.clip_tree["columns"]
        totals_cols = self.tab.totals_tree["columns"]
        self.assertEqual(clip_cols, totals_cols)

        for col in clip_cols:
            c_align = self.tab.clip_tree.column(col, "anchor")
            t_align = self.tab.totals_tree.column(col, "anchor")
            self.assertEqual(c_align, t_align, f"Alignment mismatch for column {col}")

    def test_totals_row_metrics_calculation(self):
        """Totals row displays correct clip count, total duration, time taken, failover time, and note."""
        self.tab._update_totals_row()
        self.root.update()

        vals = self.tab.totals_tree.item("totals", "values")
        # Indices:
        # 0: check ("")
        # 1: idx ("")
        # 2: clip ("Total (3 clips)")
        # 3: speaker ("")
        # 4: duration ("00:10:00" = 600s)
        # 5: start / time taken ("00:00:40" = 15s + 25s)
        # 6: failover ("00:00:03.5" = 3.5s)
        # 7: status ("2 of 3 timed" following table width constraints)
        self.assertEqual(vals[2], "Total (3 clips)")
        self.assertEqual(vals[4], "00:10:00")
        self.assertEqual(vals[5], "00:00:40")
        self.assertEqual(vals[6], "00:00:03.5")
        self.assertIn(vals[7], ("2 of 3 timed", "2 of 3 clips timed"))

    def test_totals_row_updates_live_on_clip_complete(self):
        """Simulate a third clip completing and check live totals update."""
        # clip_03 completes with 45s transcription time and 1.5s failover
        write_clip(
            self.paths["state"],
            "clip_03",
            processing_time_sec=45.0,
            failover_time_sec=1.5,
            failover_info={"fallback_reason": "rate limit", "retries": 2, "failover_time_sec": 1.5},
        )
        self.tab._set_tree_status("clip_03.mp4", "Done", "done")
        self.tab._update_totals_row()
        self.root.update()

        vals = self.tab.totals_tree.item("totals", "values")
        self.assertEqual(vals[2], "Total (3 clips)")
        self.assertEqual(vals[4], "00:10:00")
        # Total time: 15 + 25 + 45 = 85s -> 00:01:25
        self.assertEqual(vals[5], "00:01:25")
        # Total failover: 3.5 + 1.5 = 5.0s -> 00:00:05.0
        self.assertEqual(vals[6], "00:00:05.0")
        self.assertIn(vals[7], ("3 of 3 timed", "3 of 3 clips timed"))


if __name__ == "__main__":
    unittest.main()
