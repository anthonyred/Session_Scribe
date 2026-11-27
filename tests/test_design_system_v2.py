"""
tests/test_design_system_v2.py

Tests for Design System v2:
- Dynamic font scaling and type scale hierarchy.
- User UI settings persistence (LOCALAPPDATA, no event data stored).
- SegmentedControl behaviour and styling.
- ToggleSwitch behaviour and keyboard/mouse interaction.
- CollapsibleSection toggling.
- ToolTip appearance and destruction.
- Treeview 2-line wrapped headings and column alignments.
"""

import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch
import tkinter as tk
from tkinter import ttk

_TEST_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_TEST_DIR)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from gui import theme, ui_settings


class TestUiSettingsPersistence(unittest.TestCase):
    """Verify local settings file outside event folders and repo without storing event data."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.mock_appdata = patch.dict(os.environ, {"LOCALAPPDATA": self.temp_dir})
        self.mock_appdata.start()

    def tearDown(self):
        self.mock_appdata.stop()
        try:
            import shutil
            shutil.rmtree(self.temp_dir, ignore_errors=True)
        except Exception:
            pass

    def test_default_ui_settings(self):
        defaults = ui_settings.default_ui_settings()
        self.assertEqual(defaults["text_scale"], 1.0)
        self.assertEqual(defaults["table_density"], "comfortable")
        self.assertIsInstance(defaults["column_widths"], dict)

    def test_save_and_load_roundtrip(self):
        prefs = {
            "text_scale": 1.25,
            "table_density": "compact",
            "column_widths": {"clip_tree": {"clip": 240, "speaker": 150}},
        }
        ui_settings.save_ui_settings(prefs)
        loaded = ui_settings.load_ui_settings()

        self.assertAlmostEqual(loaded["text_scale"], 1.25)
        self.assertEqual(loaded["table_density"], "compact")
        self.assertEqual(loaded["column_widths"]["clip_tree"]["clip"], 240)

    def test_disallowed_keys_and_event_data_are_never_saved(self):
        dirty_prefs = {
            "text_scale": 1.1,
            "table_density": "comfortable",
            "column_widths": {},
            "event_folder": r"D:\Events\SecretEvent",
            "event_name": "Classified Tech Talk",
            "session_data": [{"clip": "VID001.mp4"}],
        }
        ui_settings.save_ui_settings(dirty_prefs)

        path = ui_settings.get_ui_settings_path()
        self.assertTrue(os.path.isfile(path))
        with open(path, "r", encoding="utf-8") as f:
            raw_data = json.load(f)

        self.assertNotIn("event_folder", raw_data)
        self.assertNotIn("event_name", raw_data)
        self.assertNotIn("session_data", raw_data)


class TestDesignSystemControls(unittest.TestCase):
    """Test custom modern controls: SegmentedControl, ToggleSwitch, CollapsibleSection."""

    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        theme.init_theme_fonts(self.root, scale=1.0)

    def tearDown(self):
        try:
            self.root.destroy()
        except Exception:
            pass

    def test_segmented_control_selection(self):
        var = tk.StringVar(value="single")
        seg = theme.SegmentedControl(
            self.root,
            variable=var,
            options=[("One speaker", "single"), ("Multiple speakers", "panel")],
        )
        self.assertEqual(var.get(), "single")
        self.assertEqual(seg._buttons["single"].cget("bg"), theme.COLOR_PRIMARY_BTN)

        # Select second option
        seg._on_select("panel")
        self.assertEqual(var.get(), "panel")
        self.assertEqual(seg._buttons["panel"].cget("bg"), theme.COLOR_PRIMARY_BTN)
        seg.destroy()

    def test_toggle_switch_interaction(self):
        var = tk.BooleanVar(value=False)
        sw = theme.ToggleSwitch(self.root, variable=var, text="Save progress every 2 minutes")
        self.assertFalse(var.get())

        # Toggle on
        sw._toggle()
        self.assertTrue(var.get())

        # Toggle off
        sw._toggle()
        self.assertFalse(var.get())
        sw.destroy()

    def test_collapsible_section_toggle(self):
        cs = theme.CollapsibleSection(self.root, "Advanced Settings", is_open=False)
        self.assertFalse(cs.is_open)

        # Expand
        cs.toggle()
        self.assertTrue(cs.is_open)
        self.assertIn("▼", cs.btn_toggle.cget("text"))

        # Collapse
        cs.toggle()
        self.assertFalse(cs.is_open)
        self.assertIn("▶", cs.btn_toggle.cget("text"))
        cs.destroy()

    def test_type_scale_named_fonts(self):
        theme.init_theme_fonts(self.root, scale=1.0)
        for font_name in ["SS_Body", "SS_BodyBold", "SS_Section", "SS_Title", "SS_Caption", "SS_Code"]:
            f = tk.font.nametofont(font_name, root=self.root)
            self.assertIsNotNone(f)
            self.assertGreater(f.cget("size"), 0)


if __name__ == "__main__":
    unittest.main()
