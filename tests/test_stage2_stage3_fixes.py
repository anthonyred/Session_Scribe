"""
tests/test_stage2_stage3_fixes.py

Comprehensive acceptance and unit tests for:
  Task 1: Mouse wheel routing and layout/scroll stability across dialogs
  Task 2: Manifest auto-load, disk verification, speaker mapping rebuild, event switching
  Task 3: Combined transcripts per speaker, atomic write, stale archiving on rename

Rules (AGENTS.md §4):
  - No real event data touched.
  - All I/O uses tempfile.mkdtemp().
  - sys.path guard derived from __file__.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timezone, timedelta
import tkinter as tk
from tkinter import ttk

# ---- sys.path guard ---------------------------------------------------
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from gui.scrollable_tab import ScrollableTab, _wheel_delta, _app_route_wheel
from gui.event_context import EventContext
from gui.stage2_tab import Stage2Tab
from gui.stage3_tab import Stage3Tab, TranscriptEditorDialog
from scribe_core.event_paths import derive_event_paths
from scribe_core.combined_transcripts import (
    sanitize_speaker_filename,
    build_combined_transcripts,
    archive_stale_combined_file,
    is_completed_transcript_file,
)
from scribe_core.clip_state import (
    compute_transcript_hash,
    get_clip_approval,
    set_clip_approval,
    clear_multiple_clip_approvals,
    get_combined_stale,
    set_combined_stale,
    validate_and_reconcile_approvals,
    set_clip_edited,
    read_clip,
)

IST = timezone(timedelta(hours=5, minutes=30))

_SHARED_ROOT = None

def setUpModule():
    global _SHARED_ROOT
    _SHARED_ROOT = tk.Tk()
    _SHARED_ROOT.geometry("1100x750")
    _SHARED_ROOT.update()

def tearDownModule():
    global _SHARED_ROOT
    if _SHARED_ROOT is not None:
        try:
            _SHARED_ROOT.destroy()
        except Exception:
            pass
        _SHARED_ROOT = None


class TestTask1ScrollingAndLayout(unittest.TestCase):
    def setUp(self):
        self.root = _SHARED_ROOT
        for child in self.root.winfo_children():
            try:
                child.destroy()
            except Exception:
                pass
        self.ctx = EventContext()
        self.tab = Stage2Tab(self.root, ctx=self.ctx)
        self.tab.pack(fill=tk.BOTH, expand=True)
        self.root.update()

    def tearDown(self):
        for child in self.root.winfo_children():
            try:
                child.destroy()
            except Exception:
                pass

    def test_application_level_wheel_scrolls_tab_over_button(self):
        """Wheel over a non-scrollable button must scroll the tab canvas."""
        self.tab._canvas.yview_moveto(0.0)
        self.root.update()
        initial_lo = self.tab._canvas.yview()[0]

        # Simulate wheel down (-120) over event_entry or a button
        class FakeEvent:
            def __init__(self, widget, delta):
                self.widget = widget
                self.delta = delta
                self.num = 0
                self.x_root = widget.winfo_rootx() + 5
                self.y_root = widget.winfo_rooty() + 5

        btn = None
        for child in self.tab.inner.winfo_children():
            for sub in child.winfo_children():
                for s in sub.winfo_children():
                    if isinstance(s, ttk.Button):
                        btn = s
                        break

        self.assertIsNotNone(btn)
        evt = FakeEvent(btn, -120)
        res = _app_route_wheel(evt)
        self.root.update()
        new_lo = self.tab._canvas.yview()[0]

        self.assertEqual(res, "break")
        self.assertGreater(new_lo, initial_lo, "Canvas should have scrolled down")

    def test_preserve_scroll_context_manager(self):
        """preserve_scroll must preserve exact canvas offset despite internal focus/actions."""
        self.tab._canvas.yview_moveto(0.18)
        self.root.update()
        before = self.tab._canvas.yview()[0]

        with self.tab.preserve_scroll():
            # simulate spurious focus event
            self.tab._on_focus_in(tk.Event())
            self.tab._canvas.yview_moveto(0.0)

        self.root.update()
        after = self.tab._canvas.yview()[0]
        self.assertAlmostEqual(before, after, places=4)


class TestTask2ManifestAutoLoadAndVerify(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.event_root = os.path.join(self.temp_dir, "Test_Event_2026")
        self.paths = derive_event_paths(self.event_root)
        os.makedirs(self.paths["videos"], exist_ok=True)
        os.makedirs(self.paths["photos"], exist_ok=True)
        os.makedirs(self.paths["scribe"], exist_ok=True)

        # Create dummy clips on disk
        self.clip1 = os.path.join(self.paths["videos"], "VID001.mp4")
        self.clip2 = os.path.join(self.paths["videos"], "VID002.mp4")
        with open(self.clip1, "wb") as f:
            f.write(b"\x00" * 100)
        with open(self.clip2, "wb") as f:
            f.write(b"\x00" * 100)

        # Create dummy photo on disk
        self.photo1 = os.path.join(self.paths["photos"], "DSC001.jpg")
        with open(self.photo1, "wb") as f:
            f.write(b"\x00" * 100)

        # Build manifest
        self.manifest_data = {
            "version": "1.0",
            "event_root": self.event_root,
            "event": {
                "name": "Cloud Summit 2026",
                "date": "15 November 2026",
                "organiser": "TechOrg",
                "venue": "Convention Center",
                "sponsors": "TechCorp",
                "speaker_mode": "single",
            },
            "settings": {
                "session_gap_threshold_seconds": 900.0,  # 15 min
                "photo_grace_seconds": 240.0,           # 4 min
            },
            "sessions": [
                {
                    "session_id": 1,
                    "speaker": "Kalyani Patra",
                    "clips": [
                        {
                            "name": "VID001.mp4",
                            "path": self.clip1,
                            "start_time": "2026-11-15T10:00:00+05:30",
                            "duration": 600.0,
                            "end_time": "2026-11-15T10:10:00+05:30",
                            "time_source": "filename",
                        },
                        {
                            "name": "VID002.mp4",
                            "path": self.clip2,
                            "start_time": "2026-11-15T10:12:00+05:30",
                            "duration": 400.0,
                            "end_time": "2026-11-15T10:18:40+05:30",
                            "time_source": "filename",
                        },
                    ],
                    "photos": [
                        {
                            "name": "DSC001.jpg",
                            "path": self.photo1,
                            "timestamp": "2026-11-15T10:05:00+05:30",
                            "assigned_session_id": 1,
                            "assigned_speaker": "Kalyani Patra",
                            "status": "Assigned",
                            "time_source": "filename",
                        }
                    ],
                    "start_time": "2026-11-15T10:00:00+05:30",
                    "end_time": "2026-11-15T10:18:40+05:30",
                }
            ],
            "unassigned_photos": [],
        }

        with open(self.paths["manifest"], "w", encoding="utf-8") as f:
            json.dump(self.manifest_data, f, indent=2)

        self.root = _SHARED_ROOT
        for child in self.root.winfo_children():
            try:
                child.destroy()
            except Exception:
                pass
        self.ctx = EventContext()
        self.tab = Stage2Tab(self.root, ctx=self.ctx)
        self.tab.pack(fill=tk.BOTH, expand=True)
        self.root.update()

    def tearDown(self):
        for child in self.root.winfo_children():
            try:
                child.destroy()
            except Exception:
                pass
        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_load_event_with_manifest_fills_all_data(self):
        """Selecting event with manifest loads fields, settings, tables, and mapping box."""
        self.tab._apply_event_folder(self.event_root, ask_parent=False)
        self.root.update()

        # Check fields
        self.assertEqual(self.ctx.event_name_var.get(), "Cloud Summit 2026")
        self.assertEqual(self.ctx.event_date_var.get(), "15 November 2026")
        self.assertEqual(self.ctx.organiser_var.get(), "TechOrg")
        self.assertEqual(self.ctx.venue_var.get(), "Convention Center")

        # Check settings
        self.assertAlmostEqual(self.tab.gap_min_var.get(), 15.0)
        self.assertAlmostEqual(self.tab.grace_min_var.get(), 4.0)

        # Check tables
        sess_rows = self.tab.sess_tree.get_children()
        self.assertEqual(len(sess_rows), 1)
        sess_vals = self.tab.sess_tree.item(sess_rows[0], "values")
        self.assertEqual(sess_vals[1], "Kalyani Patra")
        self.assertIn("speaker", str(sess_vals[2]).lower())  # Mode column (Task B)
        self.assertEqual(str(sess_vals[3]), "2")  # 2 clips

        photo_rows = self.tab.photo_tree.get_children()
        self.assertEqual(len(photo_rows), 1)
        photo_vals = self.tab.photo_tree.item(photo_rows[0], "values")
        self.assertEqual(photo_vals[0], "DSC001.jpg")
        self.assertEqual(photo_vals[2], "Kalyani Patra")

        # Check mapping text box
        mapping_text = self.tab.mapping_text.get("1.0", tk.END).strip()
        self.assertIn("VID001.mp4, VID002.mp4 - Kalyani Patra", mapping_text)

        # Check status
        self.assertIn("All files verified on disk", self.tab.sep_status_var.get())

    def test_disk_verification_missing_clip_and_new_clip(self):
        """Deleting a clip and adding a new file flags both in status and table."""
        # Delete VID002.mp4
        os.remove(self.clip2)

        # Add new dummy file not in manifest
        new_video = os.path.join(self.paths["videos"], "NEW_CLIP.mp4")
        with open(new_video, "wb") as f:
            f.write(b"\x00" * 100)

        self.tab._apply_event_folder(self.event_root, ask_parent=False)
        self.root.update()

        # Status must flag both missing and new
        status = self.tab.sep_status_var.get()
        self.assertIn("missing from disk", status)
        self.assertIn("VID002.mp4", status)
        self.assertIn("NEW_CLIP.mp4", status)
        self.assertIn("run Detect Sessions to include them", status)

        # Check row tag in tree
        sess_rows = self.tab.sess_tree.get_children()
        tags = self.tab.sess_tree.item(sess_rows[0], "tags")
        self.assertIn("missing", tags)

    def test_switch_event_clears_previous_completely(self):
        """Switching to an event with no manifest clears tables and mapping completely."""
        self.tab._apply_event_folder(self.event_root, ask_parent=False)
        self.root.update()
        self.assertEqual(len(self.tab.sess_tree.get_children()), 1)

        # Create a second empty event
        second_event = os.path.join(self.temp_dir, "Second_Event")
        os.makedirs(second_event, exist_ok=True)

        self.tab._apply_event_folder(second_event, ask_parent=False)
        self.root.update()

        # Tables and mapping must be completely empty
        self.assertEqual(len(self.tab.sess_tree.get_children()), 0)
        self.assertEqual(len(self.tab.photo_tree.get_children()), 0)
        self.assertEqual(self.tab.mapping_text.get("1.0", tk.END).strip(), "")
        self.assertIn("No manifest.json found", self.tab.sep_status_var.get())

    def test_stage3_loads_same_clips_without_restart(self):
        """Stage 3 displays the same clips and speaker names right after event selection."""
        s3 = Stage3Tab(self.root, ctx=self.ctx)
        s3.pack(fill=tk.BOTH, expand=True)
        self.root.update()

        self.tab._apply_event_folder(self.event_root, ask_parent=False)
        self.root.update()

        # Stage 3 should have loaded clips automatically
        self.assertEqual(len(s3.clips), 2)
        self.assertEqual(s3.clips[0]["speaker"], "Kalyani Patra")
        self.assertEqual(len(s3.clip_tree.get_children()), 2)


class TestTask3CombinedTranscripts(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.event_root = os.path.join(self.temp_dir, "Event_Transcripts")
        self.paths = derive_event_paths(self.event_root)
        os.makedirs(self.paths["transcripts"], exist_ok=True)
        os.makedirs(self.paths["combined"], exist_ok=True)

        self.clip1_txt = os.path.join(self.paths["transcripts"], "VID001.txt")
        self.clip2_txt = os.path.join(self.paths["transcripts"], "VID002.txt")

        header1 = "Session Scribe Transcript\nSpeaker: Kalyani Patra\nTranscription Completed: Yes\n\n[00:00] Hello part 1."
        header2 = "Session Scribe Transcript\nSpeaker: Kalyani Patra\nTranscription Completed: Yes\n\n[05:00] Hello part 2."

        with open(self.clip1_txt, "w", encoding="utf-8") as f:
            f.write(header1)
        with open(self.clip2_txt, "w", encoding="utf-8") as f:
            f.write(header2)

        self.manifest_data = {
            "version": "1.0",
            "event_root": self.event_root,
            "sessions": [
                {
                    "session_id": 1,
                    "speaker": "Kalyani Patra",
                    "clips": [
                        {"name": "VID001.mp4", "start_time": "2026-11-15T10:00:00+05:30"},
                        {"name": "VID002.mp4", "start_time": "2026-11-15T10:10:00+05:30"},
                    ],
                },
                {
                    "session_id": 2,
                    "speaker": "Ravi Kumar / Panel",
                    "clips": [
                        {"name": "VID003.mp4", "start_time": "2026-11-15T11:00:00+05:30"},
                    ],
                },
            ],
        }

    def tearDown(self):
        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_sanitize_speaker_filename(self):
        self.assertEqual(sanitize_speaker_filename("Kalyani Patra"), "Kalyani_Patra_combined.txt")
        self.assertEqual(sanitize_speaker_filename("Ravi Kumar / Panel: 1?"), "Ravi_Kumar__Panel_1_combined.txt")
        self.assertEqual(sanitize_speaker_filename(""), "Speaker_combined.txt")

    def test_build_combined_transcripts_success_and_omission_warning(self):
        """Completed clips combined with single blank line; missing clip produces warning."""
        result = build_combined_transcripts(self.event_root, self.manifest_data)
        summary = result["summary"]
        warnings = result["warnings"]

        # Kalyani Patra should have 2 completed clips
        self.assertIn("Kalyani_Patra_combined.txt: 2 clips", summary)
        # Ravi Kumar clip3 was not completed, so warning naming VID003.mp4
        self.assertTrue(any("VID003.mp4" in w for w in warnings))

        comb_path = os.path.join(self.paths["combined"], "Kalyani_Patra_combined.txt")
        self.assertTrue(os.path.isfile(comb_path))

        with open(comb_path, "r", encoding="utf-8") as f:
            content = f.read()

        # Both clips present in order separated by single blank line
        self.assertIn("[00:00] Hello part 1.", content)
        self.assertIn("[05:00] Hello part 2.", content)
        self.assertIn("Hello part 1.\n\nSession Scribe Transcript", content)

    def test_archive_stale_combined_file_on_rename(self):
        """Renaming a speaker archives old file into scribe\\combined\\old\\."""
        build_combined_transcripts(self.event_root, self.manifest_data)
        old_comb = os.path.join(self.paths["combined"], "Kalyani_Patra_combined.txt")
        self.assertTrue(os.path.isfile(old_comb))

        archived = archive_stale_combined_file(self.event_root, "Kalyani Patra")
        self.assertIsNotNone(archived)
        self.assertTrue(os.path.isfile(archived))
        self.assertIn(os.path.join("combined", "old"), archived)
        self.assertFalse(os.path.isfile(old_comb))


class TestTask3UserApprovalAndGate(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.event_root = os.path.join(self.temp_dir, "Event_Approval")
        self.paths = derive_event_paths(self.event_root)
        os.makedirs(self.paths["videos"], exist_ok=True)
        os.makedirs(self.paths["transcripts"], exist_ok=True)
        os.makedirs(self.paths["combined"], exist_ok=True)
        os.makedirs(self.paths["scribe"], exist_ok=True)

        self.clip1 = os.path.join(self.paths["videos"], "CLIP1.mp4")
        self.clip2 = os.path.join(self.paths["videos"], "CLIP2.mp4")
        with open(self.clip1, "wb") as f: f.write(b"0" * 100)
        with open(self.clip2, "wb") as f: f.write(b"0" * 100)

        self.txt1 = os.path.join(self.paths["transcripts"], "CLIP1.txt")
        self.txt2 = os.path.join(self.paths["transcripts"], "CLIP2.txt")
        c1 = "Session Scribe Transcript\nSpeaker: Speaker A\nTranscription Completed: Yes\n\n[00:00] Part 1"
        c2 = "Session Scribe Transcript\nSpeaker: Speaker B\nTranscription Completed: Yes\n\n[00:00] Part 2"
        with open(self.txt1, "w", encoding="utf-8") as f: f.write(c1)
        with open(self.txt2, "w", encoding="utf-8") as f: f.write(c2)

        self.manifest_data = {
            "version": "1.0",
            "event_root": self.event_root,
            "event": {"name": "Test Event", "date": "2026-10-30"},
            "sessions": [
                {
                    "session_id": 1,
                    "speaker": "Speaker A",
                    "clips": [{"name": "CLIP1.mp4", "path": self.clip1, "start_time": "2026-10-30T10:00:00+05:30"}],
                },
                {
                    "session_id": 2,
                    "speaker": "Speaker B",
                    "clips": [{"name": "CLIP2.mp4", "path": self.clip2, "start_time": "2026-10-30T11:00:00+05:30"}],
                },
            ],
        }
        with open(self.paths["manifest"], "w", encoding="utf-8") as f:
            json.dump(self.manifest_data, f, indent=2)

        self.root = _SHARED_ROOT
        for child in self.root.winfo_children():
            try: child.destroy()
            except Exception: pass

        self.ctx = EventContext()
        self.ctx.event_dir_var.set(self.event_root)
        self.ctx.event_name_var.set("Test Event")
        self.ctx.event_date_var.set("2026-10-30")

        self.tab = Stage3Tab(self.root, ctx=self.ctx)
        self.tab.pack(fill=tk.BOTH, expand=True)
        self.root.update()

    def tearDown(self):
        for child in self.root.winfo_children():
            try: child.destroy()
            except Exception: pass
        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_end_of_run_does_not_trigger_combine_automatically(self):
        """Finishing a run produces no combined files and summary contains transcription metrics only."""
        from tkinter import messagebox
        orig_info = messagebox.showinfo
        shown_msgs = []
        messagebox.showinfo = lambda title, msg: shown_msgs.append((title, msg))
        try:
            summary = {
                "processed_count": 2,
                "skipped_count": 0,
                "total_run_time_str": "10s",
                "total_audio_duration_sec": 60.0,
                "overall_speed_factor": 6.0,
                "low_confidence_flags_count": 0,
            }
            self.tab._on_job_finished(summary, self.paths["transcripts"])
        finally:
            messagebox.showinfo = orig_info

        # Confirm no files were written to scribe\combined\
        comb_files = [f for f in os.listdir(self.paths["combined"]) if f.endswith(".txt")]
        self.assertEqual(len(comb_files), 0, "No combined files should appear automatically at end of run")

        # Confirm completion message shows transcription results only
        self.assertTrue(any("Run Completed Successfully!" in msg for _t, msg in shown_msgs))
        self.assertFalse(any("Combined Transcripts Per Speaker" in msg for _t, msg in shown_msgs))

    def test_combine_button_locked_with_reasons(self):
        """Combine button is disabled until all clips are Done and Approved; reasons are displayed."""
        self.root.update()
        # Initially, neither clip is approved -> reason: 2 clips not approved
        self.assertEqual(str(self.tab.btn_combine_approved["state"]), str(tk.DISABLED))
        reason = self.tab.combine_gate_var.get()
        self.assertIn("LOCKED", reason)
        self.assertIn("not approved", reason)

        # Force re-transcribe on CLIP1 -> CLIP1 is pending
        self.tab._force_clips.add("CLIP1.mp4")
        self.tab._refresh_status_column()
        self.root.update()
        reason = self.tab.combine_gate_var.get()
        self.assertIn("pending", reason)

        # Delete CLIP2 on disk -> CLIP2 is file missing
        self.tab._force_clips.clear()
        os.remove(self.clip2)
        self.tab._refresh_status_column()
        self.root.update()
        reason = self.tab.combine_gate_var.get()
        self.assertIn("missing", reason)

    def test_approve_unlocks_combine_cancel_and_confirm(self):
        """Approving all clips unlocks the button; Cancel writes nothing; Yes writes files."""
        from tkinter import messagebox
        self.root.update()

        # Approve all completed
        self.tab._approve_all_completed_clicked()
        self.root.update()

        self.assertEqual(self.tab.approval_counter_var.get(), "2 of 2 approved")
        self.assertEqual(str(self.tab.btn_combine_approved["state"]), str(tk.NORMAL))
        self.assertIn("Ready to combine", self.tab.combine_gate_var.get())

        # Test Cancel: mock askyesno to return False
        orig_ask = messagebox.askyesno
        orig_info = messagebox.showinfo
        messagebox.askyesno = lambda *args, **kwargs: False
        messagebox.showinfo = lambda *args, **kwargs: None
        try:
            self.tab._combine_approved_clicked()
        finally:
            messagebox.askyesno = orig_ask
            messagebox.showinfo = orig_info

        comb_files = [f for f in os.listdir(self.paths["combined"]) if f.endswith(".txt")]
        self.assertEqual(len(comb_files), 0, "Cancelling confirmation must not write any files")

        # Test Confirm: mock askyesno to return True
        messagebox.askyesno = lambda *args, **kwargs: True
        messagebox.showinfo = lambda *args, **kwargs: None
        try:
            self.tab._combine_approved_clicked()
        finally:
            messagebox.askyesno = orig_ask
            messagebox.showinfo = orig_info

        comb_files = sorted([f for f in os.listdir(self.paths["combined"]) if f.endswith(".txt")])
        self.assertEqual(comb_files, ["Speaker_A_combined.txt", "Speaker_B_combined.txt"])

        with open(os.path.join(self.paths["combined"], "Speaker_A_combined.txt"), "r", encoding="utf-8") as f:
            content_a = f.read()
        with open(self.txt1, "r", encoding="utf-8") as f:
            orig_txt1 = f.read()
        self.assertEqual(content_a, orig_txt1)

    def test_force_retranscribe_and_file_edit_clears_approval(self):
        """Force re-transcribe or on-disk file edit clears approval automatically and locks button."""
        self.tab._approve_all_completed_clicked()
        self.root.update()
        self.assertEqual(str(self.tab.btn_combine_approved["state"]), str(tk.NORMAL))

        # Edit txt1 on disk
        with open(self.txt1, "a", encoding="utf-8") as f:
            f.write("\n[01:00] Edited segment.")

        # Refresh reconciles hash
        from tkinter import messagebox
        orig_warn = messagebox.showwarning
        messagebox.showwarning = lambda *args, **kwargs: None
        try:
            self.tab._refresh_status_column()
        finally:
            messagebox.showwarning = orig_warn
        self.root.update()

        # CLIP1 approval revoked
        self.assertEqual(self.tab.approval_counter_var.get(), "1 of 2 approved")
        self.assertEqual(str(self.tab.btn_combine_approved["state"]), str(tk.DISABLED))
        self.assertIn("1 clip(s) not approved", self.tab.combine_gate_var.get())

    def test_rename_speaker_marks_stale_and_does_not_rebuild(self):
        """Renaming a speaker marks combined files stale, clears session approvals, and does not rebuild."""
        from tkinter import messagebox
        # First approve and combine
        self.tab._approve_all_completed_clicked()
        orig_ask = messagebox.askyesno
        orig_info = messagebox.showinfo
        messagebox.askyesno = lambda *args, **kwargs: True
        messagebox.showinfo = lambda *args, **kwargs: None
        try:
            self.tab._combine_approved_clicked()
        finally:
            messagebox.askyesno = orig_ask
            messagebox.showinfo = orig_info

        # Mark stale via rename tool simulation
        from gui.stage3_tab import _apply_rename_transcript, _update_manifest_speaker
        _apply_rename_transcript(self.txt1, "Speaker A", "Dr Speaker A")
        _update_manifest_speaker(self.paths["manifest"], 1, "Dr Speaker A")

        # In do_apply: approvals cleared for session, stale flag set
        clear_multiple_clip_approvals(self.paths["state"], ["CLIP1"])
        set_combined_stale(self.paths["state"], True)

        self.tab._load_manifest(silent=True)
        self.root.update()

        # Stale note displayed
        self.assertIn("stale", self.tab.combine_stale_lbl.cget("text").lower())
        # CLIP1 is unapproved -> button is locked
        self.assertEqual(str(self.tab.btn_combine_approved["state"]), str(tk.DISABLED))
        self.assertIn("not approved", self.tab.combine_gate_var.get())
        # Not rebuilt: Dr_Speaker_A_combined.txt does NOT exist yet
        new_file = os.path.join(self.paths["combined"], "Dr_Speaker_A_combined.txt")
        self.assertFalse(os.path.isfile(new_file))

    def test_approval_survives_reopening_and_event_switch_isolation(self):
        """Approval survives closing and reopening the app; switching events never shows other event's approvals."""
        # Approve CLIP1
        self.tab.clip_tree.selection_set("1")
        self.tab._approve_selected_clicked()
        self.root.update()
        self.assertEqual(self.tab.approval_counter_var.get(), "1 of 2 approved")

        # Simulate closing and reopening the app (destroying tab and creating a new Stage3Tab instance)
        self.tab.destroy()
        new_tab = Stage3Tab(self.root, ctx=self.ctx)
        new_tab.pack(fill=tk.BOTH, expand=True)
        self.root.update()

        # Approval state survives reopen
        self.assertEqual(new_tab.approval_counter_var.get(), "1 of 2 approved")
        vals1 = new_tab.clip_tree.item("1", "values")
        self.assertEqual(vals1[8], "✓ Approved")

        # Now switch to Event 2
        event2 = os.path.join(self.temp_dir, "Event_2")
        paths2 = derive_event_paths(event2)
        os.makedirs(paths2["scribe"], exist_ok=True)
        os.makedirs(paths2["transcripts"], exist_ok=True)
        os.makedirs(paths2["videos"], exist_ok=True)
        clip_x_path = os.path.join(paths2["videos"], "CLIP_X.mp4")
        with open(clip_x_path, "wb") as f:
            f.write(b"0" * 100)

        manifest2 = {
            "version": "1.0",
            "event_root": event2,
            "event": {"name": "Event 2", "date": "2026-11-01"},
            "sessions": [
                {
                    "session_id": 1,
                    "speaker": "Speaker X",
                    "clips": [{"name": "CLIP_X.mp4", "path": clip_x_path, "start_time": "2026-11-01T10:00:00+05:30"}],
                }
            ],
        }
        with open(paths2["manifest"], "w", encoding="utf-8") as f:
            json.dump(manifest2, f, indent=2)

        self.ctx.event_dir_var.set(event2)
        self.ctx.event_name_var.set("Event 2")
        self.ctx.event_date_var.set("2026-11-01")
        self.root.update()

        # New event must NOT have any approvals from Event 1
        self.assertEqual(new_tab.approval_counter_var.get(), "0 of 1 approved")
        vals_x = new_tab.clip_tree.item("1", "values")
        self.assertEqual(vals_x[8], "— Not approved")


class TestTask1ApprovalButtonsFeedback(unittest.TestCase):
    """
    Test suite for Task 1:
      - 'Approve selected' supports multi-selection.
      - 'Approve selected' provides explicit user feedback naming clips and reasons when unapprovable.
      - 'Approve all completed' approves every clip with completed transcripts.
      - Clips in scribe\\test count as completed and can be approved.
      - Combine step reads the same files that were approved (including fallback to scribe\\test).
      - State written to scribe\\state\\clip_state.json and survives reloads.
    """
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.event_root = os.path.join(self.temp_dir, "Event1")
        self.paths = derive_event_paths(self.event_root)
        for p in self.paths.values():
            if not p.endswith(".json") and not p.endswith(".txt"):
                os.makedirs(p, exist_ok=True)

        self.clip1 = os.path.join(self.paths["videos"], "CLIP1.mp4")
        self.clip2 = os.path.join(self.paths["videos"], "CLIP2.mp4")
        self.clip3 = os.path.join(self.paths["videos"], "CLIP3.mp4")
        with open(self.clip1, "wb") as f: f.write(b"0" * 100)
        with open(self.clip2, "wb") as f: f.write(b"0" * 100)
        with open(self.clip3, "wb") as f: f.write(b"0" * 100)

        # CLIP1: completed in transcripts
        self.txt1 = os.path.join(self.paths["transcripts"], "CLIP1.txt")
        with open(self.txt1, "w", encoding="utf-8") as f:
            f.write("=== TRANSCRIPT ===\nVideo File: CLIP1.mp4\nSpeaker: Alice\nTranscription Completed: Yes\n\n[Alice] Hello world")

        # CLIP2: completed in test dir
        self.txt2 = os.path.join(self.paths["test"], "CLIP2.txt")
        with open(self.txt2, "w", encoding="utf-8") as f:
            f.write("=== TRANSCRIPT ===\nVideo File: CLIP2.mp4\nSpeaker: Bob\nTranscription Completed: Yes\n\n[Bob] Test clip content")

        # CLIP3: no transcript (Pending)

        self.manifest_data = {
            "version": "1.0",
            "event_root": self.event_root,
            "event": {"name": "Test Event", "date": "2026-10-30"},
            "sessions": [
                {
                    "session_id": 1,
                    "speaker": "Alice",
                    "clips": [{"name": "CLIP1.mp4", "path": self.clip1, "start_time": "2026-10-30T10:00:00+05:30"}],
                },
                {
                    "session_id": 2,
                    "speaker": "Bob",
                    "clips": [
                        {"name": "CLIP2.mp4", "path": self.clip2, "start_time": "2026-10-30T11:00:00+05:30"},
                        {"name": "CLIP3.mp4", "path": self.clip3, "start_time": "2026-10-30T11:30:00+05:30"},
                    ],
                },
            ],
        }
        with open(self.paths["manifest"], "w", encoding="utf-8") as f:
            json.dump(self.manifest_data, f, indent=2)

        self.root = _SHARED_ROOT
        for child in self.root.winfo_children():
            try: child.destroy()
            except Exception: pass

        self.ctx = EventContext()
        self.ctx.event_dir_var.set(self.event_root)
        self.ctx.event_name_var.set("Test Event")
        self.ctx.event_date_var.set("2026-10-30")

        self.tab = Stage3Tab(self.root, ctx=self.ctx)
        self.tab.pack(fill=tk.BOTH, expand=True)
        self.root.update()

    def tearDown(self):
        for child in self.root.winfo_children():
            try: child.destroy()
            except Exception: pass
        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_approve_selected_multi_select_with_feedback(self):
        """'Approve selected' approves completed clips and names unapprovable ones with reasons."""
        from tkinter import messagebox
        shown_warnings = []
        shown_infos = []
        orig_warn = messagebox.showwarning
        orig_info = messagebox.showinfo
        messagebox.showwarning = lambda title, msg: shown_warnings.append((title, msg))
        messagebox.showinfo = lambda title, msg: shown_infos.append((title, msg))
        try:
            # Multi-select CLIP1 (Done) and CLIP3 (Pending)
            self.tab.clip_tree.selection_set(["1", "3"])
            self.tab._approve_selected_clicked()
            self.root.update()

            # CLIP1 should be approved, CLIP3 should not
            self.assertEqual(self.tab.approval_counter_var.get(), "1 of 3 approved")
            vals1 = self.tab.clip_tree.item("1", "values")
            vals3 = self.tab.clip_tree.item("3", "values")
            self.assertEqual(vals1[8], "✓ Approved")
            self.assertEqual(vals3[8], "— Not approved")

            # Feedback message should mention CLIP3 and reason 'pending'
            self.assertTrue(len(shown_infos) > 0 or len(shown_warnings) > 0)
            combined_msgs = " ".join([m for _t, m in shown_infos + shown_warnings])
            self.assertIn("CLIP3.mp4", combined_msgs)
            self.assertIn("pending", combined_msgs.lower())

            # Selecting only CLIP3 and clicking Approve gives a warning and approves nothing
            shown_warnings.clear()
            self.tab.clip_tree.selection_set(["3"])
            self.tab._approve_selected_clicked()
            self.root.update()
            self.assertTrue(len(shown_warnings) > 0)
            self.assertIn("CLIP3.mp4", shown_warnings[0][1])
            self.assertEqual(self.tab.approval_counter_var.get(), "1 of 3 approved")
        finally:
            messagebox.showwarning = orig_warn
            messagebox.showinfo = orig_info

    def test_approve_all_completed_handles_test_directory_and_combines(self):
        """'Approve all completed' approves clips in scribe\\test, and combine reads them."""
        from tkinter import messagebox
        orig_ask = messagebox.askyesno
        orig_info = messagebox.showinfo
        messagebox.askyesno = lambda *args, **kwargs: True
        messagebox.showinfo = lambda *args, **kwargs: None
        try:
            # Initially: CLIP1 (Done), CLIP2 (Done test), CLIP3 (Pending)
            self.tab._approve_all_completed_clicked()
            self.root.update()

            # Both CLIP1 and CLIP2 are complete, so 2 of 3 approved
            self.assertEqual(self.tab.approval_counter_var.get(), "2 of 3 approved")
            self.assertEqual(self.tab.clip_tree.item("1", "values")[8], "✓ Approved")
            self.assertEqual(self.tab.clip_tree.item("2", "values")[8], "✓ Approved")

            # Now provide a completed transcript for CLIP3 in transcripts/ and approve
            txt3 = os.path.join(self.paths["transcripts"], "CLIP3.txt")
            with open(txt3, "w", encoding="utf-8") as f:
                f.write("=== TRANSCRIPT ===\nVideo File: CLIP3.mp4\nSpeaker: Bob\nTranscription Completed: Yes\n\n[Bob] Part 3")

            self.tab._approve_all_completed_clicked()
            self.root.update()
            self.assertEqual(self.tab.approval_counter_var.get(), "3 of 3 approved")
            self.assertEqual(str(self.tab.btn_combine_approved["state"]), str(tk.NORMAL))

            # Execute combine: Bob has CLIP2 (from test/) and CLIP3 (from transcripts/)
            self.tab._combine_approved_clicked()
            bob_combined = os.path.join(self.paths["combined"], "Bob_combined.txt")
            self.assertTrue(os.path.isfile(bob_combined))
            with open(bob_combined, "r", encoding="utf-8") as f:
                content = f.read()
            self.assertIn("Test clip content", content)
            self.assertIn("Part 3", content)
        finally:
            messagebox.askyesno = orig_ask
            messagebox.showinfo = orig_info


class TestTask2InAppTranscriptEditor(unittest.TestCase):
    """
    Test suite for Task 2:
      - Double-clicking opens in-app viewer in Read mode.
      - Edit button switches to Edit mode, text becomes editable.
      - Title bar shows mode, filename, and '[modified]' when changed.
      - Saving atomically preserves UTF-8 and original line endings (\\r\\n vs \\n).
      - First save backs up original transcript to scribe\\state\\originals\\<stem>.txt.
      - Manual edit marks clip 'Edited', clears approval, marks combined files stale.
      - Settings difference check ignores manual edits.
      - Skip logic treats edited transcript as complete.
      - Header protection warns if critical lines are damaged/missing.
      - Conflict detection warns if file on disk changed externally.
      - Unsaved changes prompt (Save, Discard, Cancel) on navigation / close.
      - Force re-transcribe on edited clip warns and saves backup to scribe\\state\\edited_backup\\.
      - Read-only protection while transcription is active on that clip.
    """
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.event_root = os.path.join(self.temp_dir, "EditorEvent")
        self.paths = derive_event_paths(self.event_root)
        for p in self.paths.values():
            if not p.endswith(".json") and not p.endswith(".txt"):
                os.makedirs(p, exist_ok=True)

        self.clip1 = os.path.join(self.paths["videos"], "CLIP1.mp4")
        with open(self.clip1, "wb") as f: f.write(b"0" * 100)

        # CRLF transcript
        self.txt1 = os.path.join(self.paths["transcripts"], "CLIP1.txt")
        self.initial_content_crlf = (
            "=== TRANSCRIPT ===\r\n"
            "Video File: CLIP1.mp4\r\n"
            "Speaker: Carol\r\n"
            "Transcription Completed: 2026-10-30 10:00:00 IST\r\n"
            "\r\n"
            "[Carol] Original speech line 1.\r\n"
        )
        with open(self.txt1, "wb") as f:
            f.write(self.initial_content_crlf.encode("utf-8"))

        self.manifest_data = {
            "version": "1.0",
            "event_root": self.event_root,
            "event": {"name": "Editor Event", "date": "2026-10-30"},
            "sessions": [
                {
                    "session_id": 1,
                    "speaker": "Carol",
                    "clips": [{"name": "CLIP1.mp4", "path": self.clip1, "start_time": "2026-10-30T10:00:00+05:30"}],
                }
            ],
        }
        with open(self.paths["manifest"], "w", encoding="utf-8") as f:
            json.dump(self.manifest_data, f, indent=2)

        self.root = _SHARED_ROOT
        for child in self.root.winfo_children():
            try: child.destroy()
            except Exception: pass

        self.ctx = EventContext()
        self.ctx.event_dir_var.set(self.event_root)
        self.ctx.event_name_var.set("Editor Event")
        self.ctx.event_date_var.set("2026-10-30")

        self.tab = Stage3Tab(self.root, ctx=self.ctx)
        self.tab.pack(fill=tk.BOTH, expand=True)
        self.root.update()

    def tearDown(self):
        for child in self.root.winfo_children():
            try: child.destroy()
            except Exception: pass
        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_viewer_modes_and_editing_with_backup_and_state(self):
        """Viewer opens in Read mode, switches to Edit mode, saves with original backup and state updates."""
        from tkinter import messagebox
        orig_info = messagebox.showinfo
        messagebox.showinfo = lambda *args, **kwargs: None
        try:
            # 1. Approve CLIP1 first
            self.tab._approve_all_completed_clicked()
            self.root.update()
            self.assertEqual(self.tab.approval_counter_var.get(), "1 of 1 approved")

            # 2. Open editor
            editor = TranscriptEditorDialog(
                parent=self.root,
                stage3_tab=self.tab,
                clip=self.tab.clips[0],
                target_txt=self.txt1,
                loc_desc="scribe\\transcripts",
                paths=self.paths,
            )
            self.root.update()

            # Starts in Read mode
            self.assertEqual(editor._mode, "read")
            self.assertIn("[Read] CLIP1.txt", editor.title())
            self.assertEqual(str(editor.txt_widget.cget("state")), "disabled")

            # Switch to Edit mode
            editor.set_mode("edit")
            self.root.update()
            self.assertEqual(editor._mode, "edit")
            self.assertIn("[Edit] CLIP1.txt", editor.title())
            self.assertEqual(str(editor.txt_widget.cget("state")), "normal")

            # Make an edit
            editor.txt_widget.insert(tk.END, "[Carol] Added manual note.\n")
            editor._on_text_modified()
            self.root.update()
            self.assertTrue(editor._is_dirty)
            self.assertIn("[modified]", editor.title())

            # Save file
            saved = editor.save_file()
            self.assertTrue(saved)
            self.assertFalse(editor._is_dirty)
            self.assertNotIn("[modified]", editor.title())

            # Check original backup in scribe\\state\\originals\\
            orig_backup = os.path.join(self.paths["state"], "originals", "CLIP1.txt")
            self.assertTrue(os.path.isfile(orig_backup))
            with open(orig_backup, "rb") as f:
                self.assertEqual(f.read(), self.initial_content_crlf.encode("utf-8"))

            # Check saved file has CRLF preserved and new text
            with open(self.txt1, "rb") as f:
                saved_bytes = f.read()
            self.assertIn(b"\r\n", saved_bytes)
            self.assertIn(b"Added manual note.", saved_bytes)

            # Check Stage 3 UI updates: Status is 'Edited', Approval is cleared
            self.root.update()
            vals = self.tab.clip_tree.item("1", "values")
            self.assertEqual(vals[7], "✓ Edited")
            self.assertEqual(vals[8], "— Not approved")
            self.assertEqual(self.tab.approval_counter_var.get(), "0 of 1 approved")

            # Check state file
            rec = read_clip(self.paths["state"], "CLIP1")
            self.assertTrue(rec.get("edited"))
            self.assertFalse(rec.get("approved"))
            self.assertTrue(get_combined_stale(self.paths["state"]))

            # Second save should not overwrite the original backup
            editor.txt_widget.insert(tk.END, "[Carol] Second edit.\n")
            editor._on_text_modified()
            editor.save_file()
            with open(orig_backup, "rb") as f:
                self.assertEqual(f.read(), self.initial_content_crlf.encode("utf-8"))

            editor.destroy()
        finally:
            messagebox.showinfo = orig_info

    def test_header_protection_and_conflict_check(self):
        """Header damage triggers warning dialog; disk conflict triggers conflict prompt."""
        from tkinter import messagebox
        orig_ask = messagebox.askyesno
        orig_info = messagebox.showinfo
        dialog_prompts = []

        messagebox.askyesno = lambda title, msg, **kwargs: dialog_prompts.append((title, msg)) or False
        messagebox.showinfo = lambda *args, **kwargs: None
        try:
            editor = TranscriptEditorDialog(
                parent=self.root,
                stage3_tab=self.tab,
                clip=self.tab.clips[0],
                target_txt=self.txt1,
                loc_desc="scribe\\transcripts",
                paths=self.paths,
            )
            editor.set_mode("edit")
            self.root.update()

            # Corrupt the header (delete "Transcription Completed:")
            content = editor.txt_widget.get("1.0", "end-1c")
            content_damaged = content.replace("Transcription Completed:", "Gone:")
            editor.txt_widget.delete("1.0", tk.END)
            editor.txt_widget.insert(tk.END, content_damaged)
            editor._on_text_modified()

            # Attempt to save -> askyesno should be called with warning
            saved = editor.save_file()
            self.assertFalse(saved)
            self.assertTrue(any("Header Warning" in t for t, _m in dialog_prompts))

            # Restore valid header
            editor.txt_widget.delete("1.0", tk.END)
            editor.txt_widget.insert(tk.END, content)
            editor._on_text_modified()

            # Simulate external disk change
            with open(self.txt1, "w", encoding="utf-8") as f:
                f.write(content + "\n[External] External modification.")

            # Test conflict detection helper (schedule dialog destruction for test)
            editor.after(50, lambda: [c.destroy() for c in editor.winfo_children() if isinstance(c, tk.Toplevel)])
            res = editor._check_disk_conflict()
            self.assertIn(res, ("cancel", "ok", "reload", "save_anyway"))

            editor.destroy()
        finally:
            messagebox.askyesno = orig_ask
            messagebox.showinfo = orig_info

    def test_unsaved_changes_prompt_navigation(self):
        """prompt_unsaved_if_dirty handles Cancel, Discard, and Save."""
        from tkinter import messagebox
        orig_ask_cancel = messagebox.askyesnocancel
        orig_info = messagebox.showinfo
        messagebox.showinfo = lambda *args, **kwargs: None

        editor = TranscriptEditorDialog(
            parent=self.root,
            stage3_tab=self.tab,
            clip=self.tab.clips[0],
            target_txt=self.txt1,
            loc_desc="scribe\\transcripts",
            paths=self.paths,
        )
        editor.set_mode("edit")
        editor.txt_widget.insert(tk.END, "Some edits\n")
        editor._on_text_modified()
        self.root.update()

        try:
            # 1. Cancel returns False
            messagebox.askyesnocancel = lambda *args, **kwargs: None
            self.assertFalse(editor.prompt_unsaved_if_dirty())
            self.assertTrue(editor._is_dirty)

            # 2. Discard returns True and clears dirty
            messagebox.askyesnocancel = lambda *args, **kwargs: False
            self.assertTrue(editor.prompt_unsaved_if_dirty())
            self.assertFalse(editor._is_dirty)

            # 3. Save returns True when saved
            editor.txt_widget.insert(tk.END, "More edits\n")
            editor._on_text_modified()
            messagebox.askyesnocancel = lambda *args, **kwargs: True
            self.assertTrue(editor.prompt_unsaved_if_dirty())
            self.assertFalse(editor._is_dirty)

            editor.destroy()
        finally:
            messagebox.askyesnocancel = orig_ask_cancel
            messagebox.showinfo = orig_info

    def test_force_retranscribe_warns_and_backups_edited_clip(self):
        """Force re-transcribe on an edited clip warns user and creates backup in scribe\\state\\edited_backup\\."""
        from tkinter import messagebox
        orig_ask = messagebox.askyesno
        orig_info = messagebox.showinfo
        ask_prompts = []
        messagebox.askyesno = lambda title, msg, **kwargs: ask_prompts.append((title, msg)) or True
        messagebox.showinfo = lambda *args, **kwargs: None

        try:
            # Mark CLIP1 as edited
            editor = TranscriptEditorDialog(
                parent=self.root,
                stage3_tab=self.tab,
                clip=self.tab.clips[0],
                target_txt=self.txt1,
                loc_desc="scribe\\transcripts",
                paths=self.paths,
            )
            editor.set_mode("edit")
            editor.txt_widget.insert(tk.END, "[Carol] Manual edit before force re-transcribe.\n")
            editor._on_text_modified()
            editor.save_file()
            editor.destroy()
            self.root.update()

            self.assertEqual(self.tab.clip_tree.item("1", "values")[7], "✓ Edited")

            # Toggle Force re-transcribe
            self.tab.clip_tree.selection_set("1")
            self.tab._toggle_force_relist()
            self.root.update()

            # Confirm warning asked
            self.assertTrue(any("Overwrite Manual Edits?" in t for t, _m in ask_prompts))

            # Confirm backup created in edited_backup/
            backup_path = os.path.join(self.paths["state"], "edited_backup", "CLIP1.txt")
            self.assertTrue(os.path.isfile(backup_path))
            with open(backup_path, "r", encoding="utf-8") as f:
                self.assertIn("Manual edit before force re-transcribe", f.read())

            # Row should now be Pending (forced)
            self.assertEqual(self.tab.clip_tree.item("1", "values")[7], "● Pending (forced)")
        finally:
            messagebox.askyesno = orig_ask
            messagebox.showinfo = orig_info

    def test_read_only_protection_during_transcription(self):
        """Clip currently being transcribed cannot be switched to Edit mode."""
        from tkinter import messagebox
        shown_infos = []
        orig_info = messagebox.showinfo
        messagebox.showinfo = lambda title, msg, **kwargs: shown_infos.append((title, msg))

        try:
            # Simulate active transcription run on CLIP1.mp4
            self.tab.is_running = True
            self.tab._currently_transcribing_clip = "CLIP1.mp4"

            editor = TranscriptEditorDialog(
                parent=self.root,
                stage3_tab=self.tab,
                clip=self.tab.clips[0],
                target_txt=self.txt1,
                loc_desc="scribe\\transcripts",
                paths=self.paths,
            )
            self.root.update()

            # Attempt to switch to Edit mode
            editor.set_mode("edit")
            self.root.update()

            # Must remain in Read mode and display info dialog
            self.assertEqual(editor._mode, "read")
            self.assertTrue(any("Read-Only" in t for t, _m in shown_infos))
            self.assertIn("currently being transcribed", shown_infos[0][1])

            editor.destroy()
        finally:
            self.tab.is_running = False
            self.tab._currently_transcribing_clip = None
            messagebox.showinfo = orig_info

    def test_find_bar_visibility_and_search_features(self):
        """Find bar opens visibly between toolbar and text, pre-fills selection, supports navigation, case match, wrap-around, and Esc."""
        # Create a dummy transcript with mixed case and repeated words
        dummy_content = (
            "Session Scribe Transcript\n"
            "Speaker: Speaker A\n"
            "Transcription Completed: Yes\n\n"
            "Apple banana apple cherry APPLE date. NoMatchHere."
        )
        dummy_txt = os.path.join(self.paths["transcripts"], "FIND_TEST.txt")
        with open(dummy_txt, "w", encoding="utf-8") as f:
            f.write(dummy_content)

        dummy_clip = {
            "name": "FIND_TEST.mp4",
            "speaker": "Speaker A",
            "path": os.path.join(self.paths["videos"], "FIND_TEST.mp4"),
            "start_time": "2026-10-30T12:00:00+05:30",
        }

        # 1. Test in Read mode
        editor = TranscriptEditorDialog(
            parent=self.root,
            stage3_tab=self.tab,
            clip=dummy_clip,
            target_txt=dummy_txt,
            loc_desc="scribe\\transcripts",
            paths=self.paths,
        )
        self.root.update()

        # Initially find bar should be unmapped
        self.assertFalse(editor.find_frame.winfo_ismapped())

        # Select text in read-only txt_widget and invoke Find
        editor.txt_widget.tag_add(tk.SEL, "5.0", "5.5")  # "Apple"
        editor.btn_find.invoke()
        self.root.update()

        # Find bar must now be mapped and positioned above text_frame
        self.assertTrue(editor.find_frame.winfo_ismapped())
        self.assertLess(editor.find_frame.winfo_y(), editor.text_frame.winfo_y())
        self.assertGreater(editor.find_frame.winfo_height(), 15)

        # Pre-fill check: should have pre-filled "Apple"
        self.assertEqual(editor.find_entry.get(), "Apple")

        # Case-insensitive by default: 3 occurrences of apple ("Apple", "apple", "APPLE")
        self.assertFalse(editor.case_var.get())
        editor._find_match(forward=True, is_nav=False)
        self.assertEqual(editor.lbl_find_status.cget("text"), "1 of 3")
        self.assertEqual(len(editor.txt_widget.tag_ranges("find_match")), 6)  # 3 pairs of start/end
        self.assertEqual(len(editor.txt_widget.tag_ranges("find_active")), 2) # 1 pair

        # Test Next navigation (Enter / F3)
        editor._find_next()
        self.assertEqual(editor.lbl_find_status.cget("text"), "2 of 3")
        editor._find_next()
        self.assertEqual(editor.lbl_find_status.cget("text"), "3 of 3")

        # Wrap around forward
        editor._find_next()
        self.assertEqual(editor.lbl_find_status.cget("text"), "1 of 3 (wrapped)")

        # Previous navigation (Shift+Enter / Shift+F3)
        editor._find_prev()
        self.assertEqual(editor.lbl_find_status.cget("text"), "3 of 3 (wrapped)")
        editor._find_prev()
        self.assertEqual(editor.lbl_find_status.cget("text"), "2 of 3")

        # Test Match Case
        editor.case_var.set(True)
        editor._on_find_case_toggle()
        self.assertEqual(editor.lbl_find_status.cget("text"), "1 of 1")

        # Test No Matches
        editor.find_entry.delete(0, tk.END)
        editor.find_entry.insert(0, "ZebraNonExistent")
        editor._on_find_change()
        self.assertEqual(editor.lbl_find_status.cget("text"), "No matches")
        self.assertEqual(len(editor.txt_widget.tag_ranges("find_match")), 0)
        self.assertEqual(len(editor.txt_widget.tag_ranges("find_active")), 0)

        # Test Esc closes find bar and removes tags
        editor.case_var.set(False)
        editor.find_entry.delete(0, tk.END)
        editor.find_entry.insert(0, "apple")
        editor._on_find_change()
        self.assertEqual(editor.lbl_find_status.cget("text"), "1 of 3")

        editor._on_escape()
        self.root.update()
        self.assertFalse(editor.find_frame.winfo_ismapped())
        self.assertEqual(len(editor.txt_widget.tag_ranges("find_match")), 0)
        self.assertEqual(len(editor.txt_widget.tag_ranges("find_active")), 0)

        # Ensure finding did not mark editor dirty
        self.assertFalse(editor._is_dirty)

        # 2. Test in Edit mode
        editor.set_mode("edit")
        self.root.update()
        # Trigger Ctrl+F
        editor._on_ctrl_f()
        self.root.update()
        self.assertTrue(editor.find_frame.winfo_ismapped())
        self.assertLess(editor.find_frame.winfo_y(), editor.text_frame.winfo_y())

        # Typing search in edit mode never marks text modified or dirty
        editor.find_entry.delete(0, tk.END)
        editor.find_entry.insert(0, "banana")
        editor._on_find_change()
        self.assertEqual(editor.lbl_find_status.cget("text"), "1 of 1")
        self.assertFalse(editor._is_dirty)
        self.assertFalse(editor.txt_widget.edit_modified())

        # Close find bar via Esc
        editor._on_escape()
        self.root.update()
        self.assertFalse(editor.find_frame.winfo_ismapped())
        self.assertFalse(editor._is_dirty)

        editor.destroy()


if __name__ == "__main__":
    unittest.main()
