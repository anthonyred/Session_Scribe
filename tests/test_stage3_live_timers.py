"""
Unit & UI integration tests for Part 1: Stage 3 Live Timer & Engine Display.
Tests with a fake worker and a frozen/controlled monotonic clock.
Covers:
- Ticking continues without events
- Engine cell updates on fallback
- Zero layout jitter (no column widths change during live ticking)
- Selection and scroll survive updates (with 10-second manual scroll override)
- ETA hidden until sufficient data (>= 1 clip completed)
- Timers stop on cancel and preserve values
- Formatting of hh:mm:ss above 24 hours (no wrapping)
- Simulated screen resolutions: 1280x720 at 100% and 150% in Light and Dark with 3 and 21 rows.
"""

import os
import shutil
import sys
import tempfile
import time
import tkinter as tk
from tkinter import font
import unittest
from unittest.mock import patch, MagicMock

# sys.path guard derived from __file__
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from gui.event_context import EventContext
from gui.stage3_tab import Stage3Tab, format_duration_hhmmss, format_status_display, format_failover_time
from gui import theme
from scribe_core.voice_vault import VoiceVault


class ControlledClock:
    """Monotonic clock controller for frozen / simulated time progression."""

    def __init__(self, start_time: float = 1000.0):
        self.current_time = start_time

    def monotonic(self) -> float:
        return self.current_time

    def advance(self, seconds: float) -> None:
        self.current_time += seconds


class TestStage3LiveTimers(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        theme.make_dpi_aware()
        cls.root = tk.Tk()
        cls.root.withdraw()
        theme.init_theme_fonts(cls.root, scale=1.0)

    @classmethod
    def tearDownClass(cls):
        try:
            cls.root.destroy()
        except Exception:
            pass

    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="ss_test_s3_")
        self.keys_dir = tempfile.mkdtemp(prefix="ss_test_keys_")
        self.vault = VoiceVault(keys_dir=self.keys_dir, time_cost=1, memory_cost=64)
        self.vault.initialize("hazard galaxy pebble timber canyon velvet")

        self.ctx = EventContext()
        self.tab = Stage3Tab(self.root, ctx=self.ctx, vault=self.vault)
        self.tab.pack(fill=tk.BOTH, expand=True)
        self.tab.update_idletasks()

    def tearDown(self):
        if hasattr(self.tab, "_ui_timer_id") and self.tab._ui_timer_id:
            try:
                self.tab.after_cancel(self.tab._ui_timer_id)
            except Exception:
                pass
        self.tab.destroy()
        shutil.rmtree(self.test_dir, ignore_errors=True)
        shutil.rmtree(self.keys_dir, ignore_errors=True)

    def _setup_mock_manifest(self, clip_count: int = 3):
        self.ctx.event_dir_var.set(self.test_dir)
        self.ctx.event_name_var.set("Test Event")
        self.ctx.event_date_var.set("2026-11-13")

        videos_dir = os.path.join(self.test_dir, "data", "Videos")
        os.makedirs(videos_dir, exist_ok=True)

        clips = []
        for i in range(1, clip_count + 1):
            clip_path = os.path.join(videos_dir, f"clip_{i:02d}.mp4")
            with open(clip_path, "w", encoding="utf-8") as f:
                f.write("mock media")
            clips.append({
                "name": f"clip_{i:02d}.mp4",
                "speaker": f"Speaker {i}",
                "duration": 120.0,
                "path": clip_path,
            })
        data = {
            "event_root": self.test_dir,
            "event": {"name": "Test Event", "date": "2026-11-13"},
            "sessions": [{"session_id": "s1", "speaker": "Speaker 1", "clips": clips}],
        }
        self.tab.clips = clips
        self.tab.manifest_data = data

        paths = {
            "event_root": self.test_dir,
            "data": os.path.join(self.test_dir, "data"),
            "videos": videos_dir,
            "photos": os.path.join(self.test_dir, "data", "Photos"),
            "scribe": os.path.join(self.test_dir, "scribe"),
            "manifest": os.path.join(self.test_dir, "scribe", "manifest.json"),
            "state": os.path.join(self.test_dir, "scribe", "state"),
            "transcripts": os.path.join(self.test_dir, "scribe", "transcripts"),
            "test": os.path.join(self.test_dir, "scribe", "test"),
            "work": os.path.join(self.test_dir, "scribe", "work"),
            "logs": os.path.join(self.test_dir, "scribe", "logs"),
        }
        for p in paths.values():
            if not p.endswith(".json"):
                os.makedirs(p, exist_ok=True)
        self.tab._paths = lambda: paths

        import json
        with open(paths["manifest"], "w", encoding="utf-8") as f:
            json.dump(data, f)

        self.tab._load_manifest(silent=True)
        self.tab.update_idletasks()

    def test_hhmmss_above_24_hours(self):
        """Hours must not wrap at 24 hours."""
        sec_25h = 25 * 3600 + 12 * 60 + 34  # 25h 12m 34s
        self.assertEqual(format_duration_hhmmss(sec_25h), "25:12:34")

        sec_100h = 100 * 3600 + 5 * 60 + 9  # 100h 05m 09s
        self.assertEqual(format_duration_hhmmss(sec_100h), "100:05:09")

    def test_live_ticking_without_events(self):
        """Verify running row ticks live in hh:mm:ss without requiring worker events."""
        self._setup_mock_manifest(clip_count=3)
        clock = ControlledClock(start_time=100.0)

        with patch("time.monotonic", side_effect=clock.monotonic):
            self.tab.is_running = True
            self.tab._run_start_monotonic = clock.monotonic()
            self.tab._clips_in_current_run = list(self.tab.clips)
            self.tab._completed_clips_in_run = []

            # Simulate worker event: clip 1 start
            self.tab._handle_worker_event({"type": "clip_start", "clip": "clip_01.mp4", "index": 1, "total": 3})

            row_vals = self.tab.clip_tree.item("1", "values")
            self.assertEqual(row_vals[5], "00:00:00")
            self.assertEqual(row_vals[7], "● Transcribing")

            # Advance clock by 35 seconds without any worker events
            clock.advance(35.0)
            self.tab._on_ui_tick()

            row_vals = self.tab.clip_tree.item("1", "values")
            self.assertEqual(row_vals[5], "00:00:35")
            self.assertEqual(row_vals[7], "● Transcribing")

            # Advance clock past 60s silence -> "still working…" appended
            clock.advance(30.0)  # total 65s since last event
            self.tab._on_ui_tick()

            row_vals = self.tab.clip_tree.item("1", "values")
            self.assertEqual(row_vals[5], "00:01:05")
            self.assertIn("still working…", row_vals[7])

    def test_engine_cell_update_on_fallback(self):
        """Engine cell changes to 'Parakeet → Whisper medium' on fallback and failover ticks."""
        self._setup_mock_manifest(clip_count=3)
        clock = ControlledClock(start_time=500.0)

        with patch("time.monotonic", side_effect=clock.monotonic):
            self.tab.is_running = True
            self.tab._run_start_monotonic = clock.monotonic()
            self.tab._current_engine_name = "Parakeet (NVIDIA)"
            self.tab._clips_in_current_run = list(self.tab.clips)

            self.tab._handle_worker_event({"type": "clip_start", "clip": "clip_01.mp4", "index": 1, "total": 3})
            self.assertEqual(self.tab.clip_tree.item("1", "values")[9], "Parakeet (NVIDIA)")

            # Emit fallback event with 3.2s failover
            clock.advance(10.0)
            self.tab._handle_worker_event({
                "type": "fallback",
                "clip": "clip_01.mp4",
                "message": "Cloud connection timeout",
                "failover_time_sec": 3.2,
            })

            row_vals = self.tab.clip_tree.item("1", "values")
            self.assertEqual(row_vals[9], "Parakeet → Whisper medium")
            self.assertEqual(row_vals[6], "00:00:03.2")
            self.assertEqual(self.tab._current_engine_name, "Parakeet → Whisper medium")

    def test_no_column_widths_change_during_ticking(self):
        """Verify column widths remain absolutely stable (zero layout jitter) during ticking."""
        self._setup_mock_manifest(clip_count=3)
        self.tab._recompute_column_widths()
        initial_widths = {c: self.tab.clip_tree.column(c, "width") for c in self.tab.clip_tree["columns"]}

        clock = ControlledClock(start_time=2000.0)
        with patch("time.monotonic", side_effect=clock.monotonic):
            self.tab.is_running = True
            self.tab._run_start_monotonic = clock.monotonic()
            self.tab._clips_in_current_run = list(self.tab.clips)
            self.tab._handle_worker_event({"type": "clip_start", "clip": "clip_01.mp4", "index": 1, "total": 3})

            # Perform 20 live UI ticks over 10 seconds
            for _ in range(20):
                clock.advance(0.5)
                self.tab._on_ui_tick()

            ticking_widths = {c: self.tab.clip_tree.column(c, "width") for c in self.tab.clip_tree["columns"]}
            for c in self.tab.clip_tree["columns"]:
                self.assertEqual(
                    initial_widths[c],
                    ticking_widths[c],
                    f"Column '{c}' jumped width from {initial_widths[c]} to {ticking_widths[c]} during live ticking!",
                )

    def test_selection_and_scroll_survive_updates(self):
        """Selection is never cleared by live ticking; manual scroll freezes auto-scroll for 10s."""
        self._setup_mock_manifest(clip_count=3)
        # User selects item 2
        self.tab.clip_tree.selection_set("2")
        self.assertEqual(self.tab.clip_tree.selection(), ("2",))

        clock = ControlledClock(start_time=3000.0)
        with patch("time.monotonic", side_effect=clock.monotonic):
            self.tab.is_running = True
            self.tab._run_start_monotonic = clock.monotonic()
            self.tab._clips_in_current_run = list(self.tab.clips)
            self.tab._handle_worker_event({"type": "clip_start", "clip": "clip_01.mp4", "index": 1, "total": 3})

            # Live ticks occur
            clock.advance(1.0)
            self.tab._on_ui_tick()
            # Selection must survive unchanged
            self.assertEqual(self.tab.clip_tree.selection(), ("2",))

            # Simulate manual scroll
            self.tab._on_user_scroll()
            self.assertEqual(self.tab._last_manual_scroll_time, 3001.0)

            # Auto-scroll should not trigger within 10s of manual scroll
            with patch.object(self.tab.clip_tree, "see") as mock_see:
                clock.advance(5.0)  # 5s < 10s
                self.tab._on_ui_tick()
                mock_see.assert_not_called()

                # After 10.1s past manual scroll, auto-scroll resumes
                clock.advance(6.0)  # total 11s > 10s
                self.tab._on_ui_tick()
                mock_see.assert_called_with("1")

    def test_eta_hidden_at_first_and_shown_after_completion(self):
        """Totals row hides ETA until >= 1 clip completed."""
        self._setup_mock_manifest(clip_count=3)
        clock = ControlledClock(start_time=4000.0)

        with patch("time.monotonic", side_effect=clock.monotonic):
            self.tab.is_running = True
            self.tab._run_start_monotonic = clock.monotonic()
            self.tab._clips_in_current_run = list(self.tab.clips)
            self.tab._completed_clips_in_run = []

            # Start clip 1
            self.tab._handle_worker_event({"type": "clip_start", "clip": "clip_01.mp4", "index": 1, "total": 3})
            clock.advance(10.0)
            self.tab._on_ui_tick()

            totals_vals = self.tab.totals_tree.item("totals", "values")
            # Label area (column index 2) must NOT show ETA yet
            self.assertNotIn("ETA", totals_vals[2])
            self.assertIn("0 of 3 done", totals_vals[2])

            # Complete clip 1 in 20.0s
            self.tab._handle_worker_event({
                "type": "clip_complete",
                "clip": "clip_01.mp4",
                "index": 1,
                "transcribe_time": 20.0,
                "audio_duration": 120.0,
                "speed_factor": 6.0,
                "engine": "local",
            })

            # Start clip 2
            self.tab._handle_worker_event({"type": "clip_start", "clip": "clip_02.mp4", "index": 2, "total": 3})
            clock.advance(5.0)
            self.tab._on_ui_tick()

            totals_vals = self.tab.totals_tree.item("totals", "values")
            # Now ETA must be shown! 2 remaining clips * 20s avg = ~40s
            self.assertIn("ETA", totals_vals[2])
            self.assertIn("1 of 3 done", totals_vals[2])
            self.assertIn("ETA ~00:00:40 (est.)", totals_vals[2])

    def test_timers_stop_on_cancel_and_preserve_last_values(self):
        """Cancel stops timers and preserves last recorded values."""
        self._setup_mock_manifest(clip_count=3)
        clock = ControlledClock(start_time=5000.0)

        with patch("time.monotonic", side_effect=clock.monotonic):
            self.tab.is_running = True
            self.tab._run_start_monotonic = clock.monotonic()
            self.tab._clips_in_current_run = list(self.tab.clips)

            self.tab._handle_worker_event({"type": "clip_start", "clip": "clip_01.mp4", "index": 1, "total": 3})
            clock.advance(15.0)
            self.tab._on_ui_tick()

            vals_before_cancel = self.tab.clip_tree.item("1", "values")
            self.assertEqual(vals_before_cancel[5], "00:00:15")

            # Cancel run
            self.tab._cancel_run()
            self.assertFalse(self.tab.is_running)

            # Advance clock: time should NOT tick anymore
            clock.advance(20.0)
            vals_after_cancel = self.tab.clip_tree.item("1", "values")
            self.assertEqual(vals_after_cancel[5], "00:00:15")

    def test_simulated_screen_sizes_and_themes(self):
        """Test 1280x720 at 100% and 150% in light and dark with 3 and 21 rows."""
        for num_rows in (3, 21):
            self._setup_mock_manifest(clip_count=num_rows)
            for theme_mode in ("light", "dark"):
                theme._apply_palette(theme_mode)
                for scale in (1.0, 1.5):
                    vw = int(1280 * scale)
                    self.tab._recompute_column_widths(viewport_width=vw)
                    col_widths = [self.tab.clip_tree.column(c, "width") for c in self.tab.clip_tree["columns"]]
                    self.assertEqual(sum(col_widths), vw)
                    # Verify content fit columns never cut
                    for c in ("check", "idx", "duration", "start", "failover", "status", "approved", "engine"):
                        w = self.tab.clip_tree.column(c, "width")
                        min_w = self.tab.clip_tree.column(c, "minwidth")
                        self.assertGreaterEqual(w, min_w)


if __name__ == "__main__":
    unittest.main()
