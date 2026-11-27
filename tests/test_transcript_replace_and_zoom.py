"""
tests/test_transcript_replace_and_zoom.py

Unit and integration tests for:
  1. TextViewerZoomer (zoom scaling, clamping, wheel delta accumulation, scroll break).
  2. TranscriptEditorDialog Find & Replace (case match, whole word, protected headers & timestamps).
  3. Replace All single-undo behavior (Ctrl+Z restores all edits in one step).
  4. Stage 3 clip table column alignment, duration hh:mm:ss formatting, and time taken resolution.
"""

import os
import sys
import tempfile
import unittest
import tkinter as tk
from tkinter import ttk

# Guard sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from gui.zoom_helper import TextViewerZoomer
from gui.stage3_tab import (
    TranscriptEditorDialog,
    Stage3Tab,
    format_duration_hhmmss,
    _get_clip_time_taken,
)
from gui.event_context import EventContext
from scribe_core.clip_state import write_clip


class TestTextViewerZoomer(unittest.TestCase):
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
        self.text = tk.Text(self.root, wrap=tk.WORD, font=("Consolas", 10))
        self.text.insert("1.0", "Line 1\nLine 2\nLine 3\n")
        self.zoomer = TextViewerZoomer(self.text, base_font_family="Consolas", base_font_size=10)

    def tearDown(self):
        try:
            self.text.destroy()
        except Exception:
            pass

    def test_zoom_range_and_stepping(self):
        self.assertEqual(self.zoomer.current_zoom, 100)
        self.assertEqual(self.zoomer.zoom_var.get(), "100%")

        # Zoom in
        self.zoomer.zoom_in()
        self.assertEqual(self.zoomer.current_zoom, 110)
        self.assertEqual(self.zoomer.zoom_var.get(), "110%")

        # Zoom out
        self.zoomer.zoom_out()
        self.zoomer.zoom_out()
        self.assertEqual(self.zoomer.current_zoom, 90)
        self.assertEqual(self.zoomer.zoom_var.get(), "90%")

        # Reset
        self.zoomer.zoom_reset()
        self.assertEqual(self.zoomer.current_zoom, 100)

        # Clamping
        self.zoomer.set_zoom(350)
        self.assertEqual(self.zoomer.current_zoom, 300)

        self.zoomer.set_zoom(20)
        self.assertEqual(self.zoomer.current_zoom, 50)

    def test_wheel_delta_accumulation(self):
        """High-resolution touchpads send micro-deltas; zoomer accumulates and fires smoothly."""
        event_small = tk.Event()
        event_small.delta = 20  # below 60 threshold
        res = self.zoomer._on_ctrl_mousewheel(event_small)
        self.assertEqual(res, "break")
        self.assertEqual(self.zoomer.current_zoom, 100)  # No change yet

        # Send second micro-delta (accumulated: 20 + 40 = 60 -> step in)
        event_small.delta = 40
        self.zoomer._on_ctrl_mousewheel(event_small)
        self.assertEqual(self.zoomer.current_zoom, 110)

        # Negative delta steps down
        event_neg = tk.Event()
        event_neg.delta = -120
        self.zoomer._on_ctrl_mousewheel(event_neg)
        self.assertEqual(self.zoomer.current_zoom, 90)


class TestTranscriptReplaceAndProtectedAreas(unittest.TestCase):
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
        self.state_dir = os.path.join(self.temp_dir, "scribe", "state")
        os.makedirs(self.state_dir, exist_ok=True)
        self.transcripts_dir = os.path.join(self.temp_dir, "scribe", "transcripts")
        os.makedirs(self.transcripts_dir, exist_ok=True)

        self.txt_file = os.path.join(self.transcripts_dir, "clip_01.txt")
        self.sample_content = (
            "=== EVENT DETAILS ===\n"
            "Event Name: Tech Summit\n"
            "Date: 2026-11-03\n\n"
            "=== TRANSCRIPT ===\n"
            "Video File: clip_01.mp4\n"
            "Start Time: 2026-11-03 10:00:00 IST\n"
            "Speaker: Alice\n"
            "Transcription Duration: 00:05:07\n"
            "Transcription Started: 2026-11-03 10:05:00 IST\n"
            "Transcription Completed: 2026-11-03 10:10:07 IST\n\n"
            "[Alice] [10:00:00 - 10:00:05] Welcome to the Tech Summit everyone.\n"
            "[Alice] [10:00:05 - 10:00:10] Alice is presenting the keynote today.\n"
        )
        with open(self.txt_file, "w", encoding="utf-8") as f:
            f.write(self.sample_content)

        self.ctx = EventContext()
        self.ctx.event_dir_var.set(self.temp_dir)
        self.tab = Stage3Tab(self.root, self.ctx)
        self.paths = {
            "root": self.temp_dir,
            "state": self.state_dir,
            "transcripts": self.transcripts_dir,
            "test": os.path.join(self.temp_dir, "scribe", "test"),
        }
        self.clip = {"name": "clip_01.mp4", "speaker": "Alice"}
        self.dialog = TranscriptEditorDialog(
            self.root, self.tab, self.clip, self.txt_file, "scribe\\transcripts", self.paths
        )

    def tearDown(self):
        try:
            self.dialog.destroy()
            self.tab.destroy()
        except Exception:
            pass

    def test_read_mode_disables_replace(self):
        """In Read mode, replace controls are disabled with guidance."""
        self.dialog.set_mode("read")
        self.assertEqual(str(self.dialog.replace_entry["state"]), "disabled")
        self.assertEqual(str(self.dialog.btn_replace["state"]), "disabled")
        self.assertEqual(str(self.dialog.btn_replace_all["state"]), "disabled")
        self.assertIn("Switch to Edit Mode", self.dialog.lbl_replace_hint.cget("text"))

        # Switch to Edit mode with empty query
        self.dialog.set_mode("edit")
        self.assertEqual(str(self.dialog.replace_entry["state"]), "normal")
        # Empty query still keeps buttons disabled
        self.assertEqual(str(self.dialog.btn_replace["state"]), "disabled")
        self.assertEqual(str(self.dialog.btn_replace_all["state"]), "disabled")

    def test_find_whole_word_matching(self):
        """Whole word matching does not match substrings inside longer words."""
        self.dialog.set_mode("edit")
        self.dialog.find_entry.delete(0, tk.END)
        self.dialog.find_entry.insert(0, "Tech")

        # Whole word off
        self.dialog.whole_word_var.set(False)
        self.dialog._find_match(forward=True, is_nav=False)
        # Matches in header and text
        self.assertGreater(len(self.dialog._matches), 0)

        # Whole word on matching exact word
        self.dialog.whole_word_var.set(True)
        self.dialog._find_match(forward=True, is_nav=False)
        self.assertGreater(len(self.dialog._matches), 0)

        # Query that is only a substring of 'Welcome'
        self.dialog.find_entry.delete(0, tk.END)
        self.dialog.find_entry.insert(0, "Welc")
        self.dialog.whole_word_var.set(False)
        self.dialog._find_match(forward=True, is_nav=False)
        self.assertEqual(len(self.dialog._matches), 1)

        self.dialog.whole_word_var.set(True)
        self.dialog._find_match(forward=True, is_nav=False)
        self.assertEqual(len(self.dialog._matches), 0)

    def test_protected_areas_skipped_by_default(self):
        """Header and [Speaker] [timestamp] prefixes are protected by default."""
        self.dialog.set_mode("edit")
        self.dialog.find_entry.delete(0, tk.END)
        self.dialog.find_entry.insert(0, "Alice")
        self.dialog.replace_entry.delete(0, tk.END)
        self.dialog.replace_entry.insert(0, "Bob")
        self.dialog.allow_header_var.set(False)

        # Matches:
        # 1. Header: 'Speaker: Alice' (protected)
        # 2. Line 1 prefix: '[Alice] ...' (protected)
        # 3. Line 2 prefix: '[Alice] ...' (protected)
        # 4. Line 2 text: '... Alice is presenting ...' (NOT protected)
        self.dialog._find_match(forward=True, is_nav=False)
        self.assertEqual(len(self.dialog._matches), 4)

        protected_spans = self.dialog._get_protected_spans()
        m0_start, m0_end = self.dialog._matches[0]
        self.assertTrue(self.dialog._is_match_protected(m0_start, m0_end, protected_spans))

        m1_start, m1_end = self.dialog._matches[1]
        self.assertTrue(self.dialog._is_match_protected(m1_start, m1_end, protected_spans))

        m3_start, m3_end = self.dialog._matches[3]
        self.assertFalse(self.dialog._is_match_protected(m3_start, m3_end, protected_spans))

    def test_replace_all_single_undo(self):
        """Replace all replaces eligible matches and undoes them all in a single Ctrl+Z."""
        self.dialog.set_mode("edit")
        self.dialog.find_entry.delete(0, tk.END)
        self.dialog.find_entry.insert(0, "keynote")
        self.dialog.replace_entry.delete(0, tk.END)
        self.dialog.replace_entry.insert(0, "presentation")

        orig_text = self.dialog.txt_widget.get("1.0", "end-1c")

        # Mock askyesno to True
        import tkinter.messagebox as mb
        orig_ask = mb.askyesno
        orig_info = mb.showinfo
        try:
            mb.askyesno = lambda *args, **kwargs: True
            mb.showinfo = lambda *args, **kwargs: None

            self.dialog._replace_all()
            new_text = self.dialog.txt_widget.get("1.0", "end-1c")
            self.assertIn("presentation today", new_text)
            self.assertNotIn("keynote today", new_text)
            self.assertTrue(self.dialog._is_dirty)

            # Single undo restores original text
            self.dialog.txt_widget.edit_undo()
            restored_text = self.dialog.txt_widget.get("1.0", "end-1c")
            self.assertEqual(restored_text, orig_text)
        finally:
            mb.askyesno = orig_ask
            mb.showinfo = orig_info


class TestStage3ClipTable(unittest.TestCase):
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
        self.state_dir = os.path.join(self.temp_dir, "scribe", "state")
        os.makedirs(self.state_dir, exist_ok=True)
        self.transcripts_dir = os.path.join(self.temp_dir, "scribe", "transcripts")
        os.makedirs(self.transcripts_dir, exist_ok=True)

        self.ctx = EventContext()
        self.ctx.event_dir_var.set(self.temp_dir)
        self.tab = Stage3Tab(self.root, self.ctx)

    def tearDown(self):
        try:
            self.tab.destroy()
        except Exception:
            pass

    def test_duration_hhmmss_formatting(self):
        # 306.9s -> 00:05:07
        self.assertEqual(format_duration_hhmmss(306.9), "00:05:07")
        # 4.4s -> 00:00:04
        self.assertEqual(format_duration_hhmmss(4.4), "00:00:04")
        # 0s -> 00:00:00
        self.assertEqual(format_duration_hhmmss(0), "00:00:00")
        # 3661s -> 01:01:01
        self.assertEqual(format_duration_hhmmss(3661), "01:01:01")

    def test_clip_time_taken_resolution(self):
        paths = {
            "state": self.state_dir,
            "transcripts": self.transcripts_dir,
            "test": os.path.join(self.temp_dir, "scribe", "test"),
        }
        # 1. Pending/missing -> "—"
        self.assertEqual(_get_clip_time_taken("clip_01.mp4", paths), "—")

        # 2. Recorded in state file
        write_clip(
            self.state_dir,
            "clip_01",
            "medium",
            "en",
            "ghash123",
            "2026-11-03 10:00:00 IST",
            processing_time_sec=42.4,
        )
        self.assertEqual(_get_clip_time_taken("clip_01.mp4", paths), "00:00:42")

        # 3. Fallback from transcript header on disk
        clip2_file = os.path.join(self.transcripts_dir, "clip_02.txt")
        with open(clip2_file, "w", encoding="utf-8") as f:
            f.write(
                "=== TRANSCRIPT ===\n"
                "Speaker: Bob\n"
                "Transcription Duration: 00:01:15\n"
                "Transcription Completed: 2026-11-03 10:00:00 IST\n"
            )
        self.assertEqual(_get_clip_time_taken("clip_02.mp4", paths), "00:01:15")

    def test_treeview_column_alignments_and_headings(self):
        """Header and column alignments must match: text W, numeric/time CENTER."""
        tree = self.tab.clip_tree

        # Text columns
        for col_name in ("clip", "speaker", "status"):
            col_cfg = tree.column(col_name)
            head_cfg = tree.heading(col_name)
            self.assertEqual(col_cfg["anchor"], "w", f"Column {col_name} should be anchor='w'")
            self.assertEqual(head_cfg["anchor"], "w", f"Heading {col_name} should be anchor='w'")

        # Numeric and time columns
        for col_name in ("check", "idx", "duration", "start", "failover", "approved", "engine"):
            col_cfg = tree.column(col_name)
            head_cfg = tree.heading(col_name)
            self.assertEqual(col_cfg["anchor"], "center", f"Column {col_name} should be anchor='center'")
            self.assertEqual(head_cfg["anchor"], "center", f"Heading {col_name} should be anchor='center'")

        # Time taken heading text (2-line wrapped per design system v2)
        self.assertIn("Time taken", tree.heading("start")["text"])
        self.assertIn("transcription", tree.heading("start")["text"])
        self.assertIn("Failover", tree.heading("failover")["text"])


if __name__ == "__main__":
    unittest.main()
