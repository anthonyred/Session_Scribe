"""
tests/test_theme_contrast.py

Automated verification of WCAG AA compliance (contrast ratio >= 4.5:1)
for all foreground/background color pairs defined in gui/theme.py,
and verification that status is never conveyed by color alone.
"""

import os
import sys
import unittest

# Guard sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from gui.theme import (
    THEME_COLOR_PAIRS,
    STATUS_COLORS,
    MSG_THEMES,
    contrast_ratio,
)


class TestThemeContrast(unittest.TestCase):
    def test_all_theme_pairs_meet_wcag_aa(self):
        """Every text and background pair in the theme must have contrast >= 4.5:1."""
        min_required_ratio = 4.5
        failures = []

        for name, fg, bg in THEME_COLOR_PAIRS:
            ratio = contrast_ratio(fg, bg)
            if ratio < min_required_ratio:
                failures.append(f"{name} ({fg} on {bg}) = {ratio:.2f}:1 (requires >= {min_required_ratio}:1)")

        self.assertEqual(
            len(failures),
            0,
            "The following theme color pairs failed WCAG AA contrast:\n" + "\n".join(failures),
        )

    def test_status_badges_all_exceed_four_point_five(self):
        """All status badge colors must have high contrast."""
        for status, (bg, fg) in STATUS_COLORS.items():
            ratio = contrast_ratio(fg, bg)
            self.assertGreaterEqual(
                ratio,
                4.5,
                f"Status '{status}' ({fg} on {bg}) contrast {ratio:.2f}:1 is below 4.5:1",
            )

    def test_message_bars_meet_wcag_aa(self):
        """All message bar text on message bar background must meet WCAG AA contrast."""
        for kind, th in MSG_THEMES.items():
            ratio = contrast_ratio(th["fg"], th["bg"])
            self.assertGreaterEqual(
                ratio,
                4.5,
                f"MessageBar '{kind}' text ({th['fg']} on {th['bg']}) contrast {ratio:.2f}:1 is below 4.5:1",
            )

    def test_status_not_conveyed_by_color_alone(self):
        """
        Verify status symbols and textual representation ensure status is never conveyed
        by color alone. Every status has a unique text representation.
        """
        from gui.stage3_tab import format_status_display, format_approval_display

        test_statuses = ["Done", "Done (test)", "Running", "Failed", "Diff settings", "File missing"]
        display_statuses = [format_status_display(s) for s in test_statuses]

        # Ensure distinct strings for distinct statuses
        self.assertEqual(len(set(display_statuses)), len(test_statuses))

        # Check symbols are present
        self.assertIn("✓", format_status_display("Done"))
        self.assertIn("●", format_status_display("Running"))
        self.assertIn("✕", format_status_display("Failed"))
        self.assertIn("!", format_status_display("Diff settings"))

        self.assertIn("✓", format_approval_display(True))
        self.assertIn("—", format_approval_display(False))


if __name__ == "__main__":
    unittest.main()
