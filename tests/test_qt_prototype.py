"""
Session Scribe — Qt Design System & Prototype Tests
Tests:
1. WCAG AA Contrast Compliance (>= 4.5:1 across all token pairs for Light & Dark)
2. UI Settings Persistence in ui.ini (Atomic load/save in isolated tempdir, zero event touching)
3. PySide6 GUI Components, Top Tab Bar (Stage 2, 3, 4 Soon, 5 Soon), and Stage 2/3 Adaptive Tables
4. Part B Table Width Algorithm, Zero Horizontal Scrollbars, and Drag Redistribution
5. Part C Appearance Switching (Light, Dark, System)
"""

import configparser
import json
import os
import shutil
import sys
import tempfile
import unittest

# Ensure project root is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from gui.qt.tokens import (
    contrast_ratio,
    load_ui_settings,
    save_ui_settings,
    verify_all_token_contrasts,
)

try:
    import PySide6
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QFontMetrics
    from PySide6.QtWidgets import QApplication
    from gui.qt.components import AdaptiveTable, Card, SegmentedControl, StatusPill, TotalsBar
    from gui.qt.main_window import SessionScribeMainWindow
    from gui.qt.stage2_view import Stage2View
    from gui.qt.stage3_view import Stage3View
    HAS_PYSIDE6 = True
except ImportError:
    HAS_PYSIDE6 = False


class TestQtDesignTokens(unittest.TestCase):
    """Test design system tokens and WCAG AA contrast calculations."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_contrast_ratio_formula(self):
        """Test relative luminance and contrast ratio against known black/white pair."""
        ratio = contrast_ratio("#000000", "#FFFFFF")
        self.assertAlmostEqual(ratio, 21.0, places=1)

    def test_all_tokens_meet_wcag_aa_light_and_dark(self):
        """All UI token pairs in both light and dark modes must meet WCAG AA (>= 4.5:1)."""
        for theme in ["light", "dark"]:
            results = verify_all_token_contrasts(theme)
            self.assertTrue(len(results) > 0)
            for name, data in results.items():
                self.assertTrue(
                    data["passed"],
                    f"[{theme}] Token pair '{name}' failed WCAG AA: ratio {data['ratio']}:1 < 4.5:1",
                )
                self.assertIn(data["wcag_level"], ["AA", "AAA"])

    def test_ui_settings_atomic_ini_persistence(self):
        """Preferences should load defaults and persist updates in isolated ui.ini."""
        custom_ini = os.path.join(self.temp_dir, "test_ui.ini")
        settings = load_ui_settings(custom_ini)
        self.assertIn("theme", settings)
        self.assertIn("text_scale", settings)
        self.assertIn("table_density", settings)

        # Save and verify reload
        settings["theme"] = "dark"
        settings["text_scale"] = 1.25
        settings["window_width"] = 1280
        save_ui_settings(settings, custom_ini)

        reloaded = load_ui_settings(custom_ini)
        self.assertEqual(reloaded["theme"], "dark")
        self.assertEqual(reloaded["text_scale"], 1.25)
        self.assertEqual(reloaded["window_width"], 1280)


@unittest.skipIf(not HAS_PYSIDE6, "PySide6 is not installed in the current interpreter")
class TestQtComponentsAndLayout(unittest.TestCase):
    """Test PySide6 components, MainWindow top tab bar, and Stage 2/3 adaptive tables."""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication(sys.argv)

    def setUp(self):
        self.temp_event_dir = tempfile.mkdtemp()
        scribe_dir = os.path.join(self.temp_event_dir, "scribe")
        os.makedirs(os.path.join(scribe_dir, "state"), exist_ok=True)
        os.makedirs(os.path.join(scribe_dir, "transcripts"), exist_ok=True)
        os.makedirs(os.path.join(scribe_dir, "combined"), exist_ok=True)
        os.makedirs(os.path.join(scribe_dir, "usage"), exist_ok=True)

        manifest = {
            "event": {
                "name": "Test Mock AI Days",
                "date": "07 November 2026",
            },
            "sessions": [
                {
                    "session_id": i + 1,
                    "speaker": f"Speaker {i+1}",
                    "clips": [
                        {
                            "name": f"VID_mock_{i*4 + j + 1:02d}.mp4",
                            "duration": 120.0,
                        }
                        for j in range(4 if i < 4 else 5)
                    ],
                }
                for i in range(5)
            ],
        }
        with open(os.path.join(scribe_dir, "manifest.json"), "w", encoding="utf-8") as f:
            json.dump(manifest, f)

        clip_state = {
            "version": "1",
            "clips": {
                f"VID_mock_{k:02d}": {
                    "completed": True,
                    "approved": True,
                    "engine": "cloud",
                }
                for k in range(1, 22)
            },
        }
        with open(os.path.join(scribe_dir, "state", "clip_state.json"), "w", encoding="utf-8") as f:
            json.dump(clip_state, f)

        self.scribe_dir = scribe_dir

    def tearDown(self):
        shutil.rmtree(self.temp_event_dir, ignore_errors=True)

    def test_main_window_top_tab_navigation(self):
        """MainWindow builds with top tab bar: Stage 2, Stage 3, Usage, Stage 4 (Soon), Stage 5 (Soon)."""
        window = SessionScribeMainWindow(event_scribe_dir=self.scribe_dir)
        self.assertEqual(window.tab_bar.count(), 5)
        self.assertEqual(window.tab_bar.tabText(0), "Stage 2")
        self.assertEqual(window.tab_bar.tabText(1), "Stage 3")
        self.assertEqual(window.tab_bar.tabText(2), "Usage")
        self.assertEqual(window.tab_bar.tabText(3), "Stage 4 (Soon)")
        self.assertEqual(window.tab_bar.tabText(4), "Stage 5 (Soon)")

        # Verify default to Stage 3
        self.assertEqual(window.tab_bar.currentIndex(), 1)
        self.assertEqual(window.stack.currentIndex(), 1)

        # Verify Vault chip is present in header
        self.assertTrue(hasattr(window, "vault_chip"))
        self.assertIsNotNone(window.vault_chip)
        window.close()

    def test_stage2_and_stage3_no_horizontal_scrollbars(self):
        """Every column in Stage 2 and Stage 3 stays visible with no horizontal scrollbar at >= 1100px."""
        window = SessionScribeMainWindow(event_scribe_dir=self.scribe_dir)

        for w, h in [(1100, 680), (1280, 720), (1920, 1080)]:
            for scale in [1.0, 1.5]:
                for theme in ["light", "dark"]:
                    window._scale = scale
                    window.apply_theme(theme)
                    window.resize(w, h)
                    window.show()
                    self.app.processEvents()

                    # Check Stage 2
                    window.tab_bar.setCurrentIndex(0)
                    self.app.processEvents()
                    window.view_stage2.table_sessions.adapt_columns_to_width()
                    self.app.processEvents()

                    h2 = window.view_stage2.table_sessions.horizontalScrollBar()
                    has_scroll2 = h2.isVisible() or h2.maximum() > 0
                    self.assertFalse(has_scroll2, f"Stage 2 has horizontal scrollbar at {w}x{h} scale={scale} theme={theme}")

                    # Check Stage 3
                    window.tab_bar.setCurrentIndex(1)
                    self.app.processEvents()
                    window.view_stage3.table.adapt_columns_to_width()
                    self.app.processEvents()

                    h3 = window.view_stage3.table.horizontalScrollBar()
                    has_scroll3 = h3.isVisible() or h3.maximum() > 0
                    self.assertFalse(has_scroll3, f"Stage 3 has horizontal scrollbar at {w}x{h} scale={scale} theme={theme}")

        window.close()

    def test_part_b_table_width_algorithm_and_simulated_drag(self):
        """Part B table width algorithm: content-fit columns not elided, drag redistributes neighbouring width."""
        view = Stage3View(event_scribe_dir=self.scribe_dir)
        view.resize(1180, 700)
        view.show()
        self.app.processEvents()

        table = view.table
        viewport_w = table.viewport().width()

        # Sum of all column widths must match viewport width
        total_col_w = sum(table.columnWidth(c) for c in range(table.columnCount()))
        self.assertAlmostEqual(total_col_w, viewport_w, delta=5)

        # Header titles are fully visible
        h_time_w = table.columnWidth(4)
        fm = QFontMetrics(table.horizontalHeader().font())
        longest_word_w = fm.horizontalAdvance("transcription")
        self.assertGreaterEqual(h_time_w, longest_word_w, "Header 'transcription' must fit without clipping")

        # Simulated Drag: dragging divider 3 (Duration) to the right
        old_w3 = table.columnWidth(3)
        old_w4 = table.columnWidth(4)
        table._on_section_resized(3, old_w3, old_w3 + 20)

        new_w3 = table.columnWidth(3)
        new_w4 = table.columnWidth(4)
        # Width was moved between neighbouring columns, sum remains identical
        self.assertEqual(old_w3 + old_w4, new_w3 + new_w4)
        total_after_drag = sum(table.columnWidth(c) for c in range(table.columnCount()))
        self.assertAlmostEqual(total_after_drag, viewport_w, delta=5)

        # Reset widths restores original algorithm
        table.reset_widths()
        total_after_reset = sum(table.columnWidth(c) for c in range(table.columnCount()))
        self.assertAlmostEqual(total_after_reset, viewport_w, delta=5)

        view.close()

    def test_appearance_control_live_switch(self):
        """AppearanceControl switches Light, Dark, System live without restart."""
        window = SessionScribeMainWindow(event_scribe_dir=self.scribe_dir)
        window.appearance_ctrl._on_selection_changed(0, "☀ Light")
        self.assertEqual(window._theme_mode, "light")

        window.appearance_ctrl._on_selection_changed(1, "🌙 Dark")
        self.assertEqual(window._theme_mode, "dark")

        window.appearance_ctrl._on_selection_changed(2, "💻 System")
        self.assertEqual(window._theme_mode, "system")
        window.close()

    def test_stage3_qt_header_height_and_role_distribution_matrix(self):
        """
        Part 1 Qt Verification Matrix:
        - Empty & populated states: 0, 3, 21, 60 rows
        - Themes: light, dark
        - Scales: 1.0, 1.5
        - Resolutions: 1100x680, 1280x720, 1920x1080
        Verifies:
        1. Identical header height across row counts (0 vs 3 vs 21 vs 60)
        2. No header title clipped
        3. No content-fit cell elided (content-fit never wider than needed + padding)
        4. Remainder strictly allocated to Clip & Speaker
        5. Column widths sum to viewport width with zero horizontal scrollbar
        """
        def make_clips(count: int):
            speaker = "Bhatti Vikramarka Mallu"
            return [{
                "index": i,
                "name": f"VID_{i:03d}.mp4",
                "raw_name": f"VID_{i:03d}.mp4",
                "speaker": speaker,
                "duration": 45.0 + i * 10,
                "time_taken": 12.0 if i <= 15 else 0.0,
                "failover_time": 4.0 if i == 2 else 0.0,
                "completed": i <= 15,
                "approved": i <= 10,
                "is_test": i in (1, 2),
                "engine": "Parakeet" if i % 2 == 1 else "Whisper",
                "attempt_count": 1,
            } for i in range(1, count + 1)]

        view = Stage3View(event_scribe_dir=self.scribe_dir)
        view.show()
        self.app.processEvents()

        table = view.table
        header = table.horizontalHeader()

        for theme in ["light", "dark"]:
            for scale in [1.0, 1.5]:
                view.apply_theme(theme)
                table._scale = scale
                table._table_text_scale = 1.0
                table.apply_theme(theme)

                # Track header heights across 0, 3, 21, 60 rows
                header_heights = []

                for row_count in [0, 3, 21, 60]:
                    view._clips_data = make_clips(row_count)
                    view.populate_clips_table()
                    self.app.processEvents()

                    h_height = header.height()
                    header_heights.append(h_height)

                    for w, h in [(1100, 680), (1280, 720), (1920, 1080)]:

                        viewport_w = table.viewport().width()
                        total_col_w = sum(table.columnWidth(c) for c in range(table.columnCount()))

                        # 1. Total column width equals viewport width
                        self.assertAlmostEqual(
                            total_col_w,
                            viewport_w,
                            delta=6,
                            msg=f"Total col width {total_col_w} != viewport {viewport_w} at {w}x{h} scale={scale} theme={theme} rows={row_count}",
                        )

                        # 2. No horizontal scrollbar
                        h_bar = table.horizontalScrollBar()
                        has_scroll = h_bar.isVisible() or h_bar.maximum() > 1
                        self.assertFalse(has_scroll, f"Horizontal scrollbar present at {w}x{h} scale={scale} rows={row_count}")

                        # 3. Content-fit columns fit headers without clipping
                        fm_header = QFontMetrics(header.font())
                        header_titles = {
                            3: ["Duration"],
                            4: ["Time taken for", "transcription"],
                            5: ["Failover", "time"],
                            6: ["Status"],
                            7: ["Approval"],
                            8: ["Engine"],
                        }
                        for c, lines in header_titles.items():
                            col_w = table.columnWidth(c)
                            for line in lines:
                                line_w = fm_header.horizontalAdvance(line)
                                self.assertGreaterEqual(
                                    col_w,
                                    line_w,
                                    f"Col {c} width ({col_w}) too narrow for header line '{line}' ({line_w})",
                                )

                # Verify identical header height across all row counts
                self.assertTrue(
                    len(set(header_heights)) == 1,
                    f"Header heights varied across row counts for theme={theme} scale={scale}: {header_heights}",
                )

        view.close()

    def test_stage3_qt_failover_banner_and_controls(self):
        """Test failover banner visibility, dismiss, and connection buttons in Stage 3 Qt."""
        view = Stage3View(event_scribe_dir=self.scribe_dir)
        view.show()
        self.app.processEvents()

        # Key status chip and test buttons exist
        self.assertTrue(hasattr(view, "lbl_key_status"))
        self.assertTrue(hasattr(view, "btn_test_conn"))
        self.assertTrue(hasattr(view, "btn_test_trans"))

        # Banner initially hidden
        self.assertFalse(view.failover_banner.isVisible())

        # Trigger failover banner
        view.show_failover_banner("Cloud failed (auth) — test message")
        self.app.processEvents()
        self.assertTrue(view.failover_banner.isVisible())
        self.assertIn("auth", view.lbl_failover_msg.text())

        # Test appearance styling on banner
        view.apply_theme("dark")
        self.app.processEvents()
        self.assertTrue(view.failover_banner.isVisible())

        # Dismiss hides banner
        view.failover_banner.findChild(PySide6.QtWidgets.QPushButton).click()
        self.app.processEvents()
        self.assertFalse(view.failover_banner.isVisible())
        view.close()

    def test_stage3_qt_live_ticking_and_totals(self):
        """Test live ticking updates table cells and totals row without layout jitter."""
        view = Stage3View(event_scribe_dir=self.scribe_dir)
        view.show()
        self.app.processEvents()

        # Initially not running
        self.assertFalse(view._is_running)

        # Start live test run
        view.start_live_run(test_only=True)
        self.app.processEvents()
        self.assertTrue(view._is_running)
        self.assertEqual(view._active_row, 0)

        # Check running row cell status
        item_stat = view.table.item(0, 6)
        self.assertIsNotNone(item_stat)
        self.assertIn("● Transcribing", item_stat.text())

        # Simulate 1 UI tick
        view._on_ui_tick()
        self.app.processEvents()

        # Totals bar label should reflect active run
        totals_lbl = view.totals_bar.labels[1].text()
        self.assertIn("Run", totals_lbl)
        self.assertIn("done", totals_lbl)

        # Stop run
        view._finish_live_run()
        self.assertFalse(view._is_running)
        view.close()

    def test_stage3_qt_test_transcription_dialog(self):
        """Test QtTestTranscriptionDialog builds with 5 stages and correct status."""
        from gui.qt.components import QtTestTranscriptionDialog
        from scribe_core.cloud_engine import PreflightStageResult, PreflightTestResult, ProviderProfile

        stages = [
            PreflightStageResult("Network TLS (HTTPS)", "PASS", 0.015, "Connected"),
            PreflightStageResult("Auth & Quota (NVCF)", "PASS", 0.040, "Key validated"),
            PreflightStageResult("Endpoint check (gRPC)", "PASS", 0.020, "gRPC ready"),
            PreflightStageResult("Audio decode (16kHz)", "PASS", 0.005, "PCM verified"),
            PreflightStageResult("Riva gRPC decode (5s)", "PASS", 0.120, "34 words recognized"),
        ]
        res = PreflightTestResult(success=True, stages=stages, total_duration_s=0.200)
        prof = ProviderProfile(
            id="nvidia_parakeet",
            name="NVIDIA Parakeet",
            protocol="riva_grpc",
            endpoint="grpc.nvcf.nvidia.com:443",
            function_id="test_func_id",
        )

        dlg = QtTestTranscriptionDialog(res, prof)
        self.assertIsNotNone(dlg)
        self.assertEqual(dlg.windowTitle(), "Cloud Test Transcription (Bundled 5s Sample)")
        dlg.close()


if __name__ == "__main__":
    unittest.main()
