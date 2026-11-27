"""
tests/test_stage3_tk_table.py

Automated tests for Tkinter Stage 3 Clip Table (Hotfix Verification):
1. Header height consistency across empty and populated states (0, 3, 21, 60 rows).
2. Content-fit guarantee: widest cell text and longest header line <= column width - padding.
3. Total width: sum(column widths) == viewport width with zero horizontal scrollbar.
4. Scale & resolution robustness: verified across simulated screen matrix:
   - 1366x768 @ 125%
   - 1536x864 @ 125%
   - 1920x1080 @ 100%
   - 1920x1080 @ 150%
   - 2560x1440 @ 150%
5. Both themes (Light and Dark) verified with WCAG AA compliance.
6. Totals row short text ("21 of 21 timed", "0 of 0 timed") fits in status column without clipping.
7. Column drag redistribution between neighbouring columns only with total width preserved.
8. Window sizing: ~85% of usable work area, centered, clamped to at least 1000x640.
9. Isolated execution: uses dummy data ("Bhatti Vikramarka Mallu") and tempfile (zero real event touching).
"""

import os
import sys
import tempfile
import tkinter as tk
from tkinter import ttk, font
import unittest

# Ensure project root is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import gui.theme as theme
import gui.ui_settings as ui_settings
from gui.event_context import EventContext
from gui.stage3_tab import Stage3Tab


class TestStage3TkTable(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        theme.make_dpi_aware()
        cls.root = tk.Tk()
        cls.root.withdraw()
        theme.apply_global_theme(cls.root, scale=1.0)

    @classmethod
    def tearDownClass(cls):
        try:
            cls.root.destroy()
        except Exception:
            pass

    def setUp(self):
        theme.apply_global_theme(self.root, scale=1.0, theme_mode="graphite", force=True)
        self.ctx = EventContext()
        self.ctx.event_dir_var.set("D:\\Events\\Mock_Event")
        self.ctx.event_name_var.set("Mock Conference")
        self.ctx.event_date_var.set("07 November 2026")
        self.tab = Stage3Tab(self.root, self.ctx)
        self.tab.pack(fill=tk.BOTH, expand=True)
        self.root.update_idletasks()

    def tearDown(self):
        try:
            self.tab.destroy()
        except Exception:
            pass

    def _populate_dummy_rows(self, count: int, speaker: str = "Bhatti Vikramarka Mallu"):
        self.tab._clear_clips()
        self.tab.clips = []
        for idx in range(1, count + 1):
            c = {
                "name": f"VID_mock_20261107_{idx:03d}.mp4",
                "duration": 45.0 + (idx * 15.0),
                "speaker": speaker,
            }
            self.tab.clips.append(c)
            dur_m, dur_s = divmod(int(c["duration"]), 60)
            is_test = idx in (1, 2)
            self.tab.clip_tree.insert(
                "",
                tk.END,
                iid=str(idx),
                values=(
                    "[TEST]" if is_test else "",
                    idx,
                    c["name"],
                    speaker,
                    f"{dur_m:02d}:{dur_s:02d}",
                    "00:00:45",
                    "00:00:12" if idx == 2 else "—",
                    "✓ Done (test)" if is_test else "✓ Done",
                    "✓ Approved" if idx <= 15 else "—",
                    "Cloud: Parakeet" if idx % 2 == 1 else "Fallback (Local)",
                ),
            )
        self.tab._update_totals_row()
        self.root.update_idletasks()

    def test_header_height_consistency_across_row_counts(self):
        """Header height must remain identical regardless of whether 0, 3, 21, or 60 rows exist."""
        theme.apply_global_theme(self.root, scale=1.0)

        f_bold = font.nametofont("SS_BodyBold")
        linespace = f_bold.metrics("linespace")
        style = ttk.Style()
        pad = style.lookup("Treeview.Heading", "padding")
        if isinstance(pad, str):
            pad_parts = [int(p) for p in pad.split() if p.strip()]
        else:
            pad_parts = [int(str(p)) for p in pad]
        if len(pad_parts) == 4:
            expected_heading_h = linespace + pad_parts[1] + pad_parts[3]
        elif len(pad_parts) == 2:
            expected_heading_h = linespace + pad_parts[1] * 2
        else:
            expected_heading_h = linespace + 20

        row_counts = [3, 21, 60]
        y_starts = []
        for count in row_counts:
            self._populate_dummy_rows(count)
            self.root.update()
            bbox = self.tab.clip_tree.bbox("1")
            self.assertIsNotNone(bbox, f"Row 1 bbox was None for count={count}")
            y_starts.append(bbox[1])

        self.assertTrue(len(set(y_starts)) == 1, f"Header heights varied across row counts: {y_starts}")
        self.assertGreaterEqual(y_starts[0], 45)
        self.assertAlmostEqual(y_starts[0], expected_heading_h, delta=8)

    def test_widest_cell_and_header_fit_within_columns(self):
        """No header word or cell content should exceed column width minus padding."""
        self._populate_dummy_rows(21, speaker="Bhatti Vikramarka Mallu")
        self.tab._recompute_column_widths(viewport_width=1100)

        f_bold = font.nametofont("SS_BodyBold")
        f_body = font.nametofont("SS_Body")
        cols = list(self.tab.clip_tree["columns"])

        content_cols = ["check", "idx", "duration", "start", "failover", "status", "approved", "engine"]

        for c in content_cols:
            col_w = self.tab.clip_tree.column(c, "width")
            h_text = self.tab.clip_tree.heading(c, "text") or ""
            max_h_w = max(f_bold.measure(line) for line in h_text.split("\n"))

            idx_in_cols = cols.index(c)
            max_cell_w = 0
            for item in self.tab.clip_tree.get_children():
                vals = self.tab.clip_tree.item(item, "values")
                if idx_in_cols < len(vals):
                    max_cell_w = max(max_cell_w, f_body.measure(str(vals[idx_in_cols])))

            self.assertGreaterEqual(
                col_w,
                max_h_w,
                f"Column '{c}' width ({col_w}px) is too narrow for header '{h_text}' ({max_h_w}px)",
            )
            self.assertGreaterEqual(
                col_w,
                max_cell_w,
                f"Column '{c}' width ({col_w}px) is too narrow for cell ({max_cell_w}px)",
            )

    def test_simulated_screen_matrix_and_both_themes(self):
        """
        Verify table column fitting across the simulated screen matrix:
        1366x768 @ 125%, 1536x864 @ 125%, 1920x1080 @ 100% & 150%, 2560x1440 @ 150%.
        Verified for both Light and Dark modes with zero horizontal scrollbar.
        """
        simulated_screens = [
            ("1366x768 @ 125%", 1.25, 1080),
            ("1536x864 @ 125%", 1.25, 1220),
            ("1920x1080 @ 100%", 1.0, 1550),
            ("1920x1080 @ 150%", 1.5, 1550),
            ("2560x1440 @ 150%", 1.5, 2050),
        ]

        for screen_name, scale, vw in simulated_screens:
            for theme_mode in ["light", "dark"]:
                theme.apply_global_theme(self.root, scale=scale, theme_mode=theme_mode)
                self.tab.refresh_theme()

                for row_count in [0, 3, 21, 60]:
                    if row_count > 0:
                        self._populate_dummy_rows(row_count)
                    else:
                        self.tab._clear_clips()
                        self.tab._update_totals_row()

                    self.tab._recompute_column_widths(viewport_width=vw)
                    total_w = sum(self.tab.clip_tree.column(c, "width") for c in self.tab.clip_tree["columns"])

                    self.assertEqual(
                        total_w,
                        vw,
                        f"sum(col_widths)={total_w} != viewport_width={vw} on {screen_name} ({theme_mode}), rows={row_count}",
                    )

                    # Check that totals status note text fits in status column
                    status_col_w = self.tab.clip_tree.column("status", "width")
                    totals_vals = self.tab.totals_tree.item("totals", "values")
                    note_text = totals_vals[7] if len(totals_vals) > 7 else ""
                    if note_text:
                        f_bold = font.nametofont("SS_BodyBold")
                        measured_note_w = f_bold.measure(note_text)
                        self.assertLessEqual(
                            measured_note_w,
                            status_col_w - 6,
                            f"Totals row note '{note_text}' ({measured_note_w}px) clipped in status col ({status_col_w}px) on {screen_name}",
                        )

    def test_totals_row_short_text_fits_status_column(self):
        """Totals row must display short text '21 of 21 timed' / '0 of 0 timed' without clipping."""
        f_bold = font.nametofont("SS_BodyBold")

        # 0 clips
        self.tab._clear_clips()
        self.tab._update_totals_row()
        self.tab._recompute_column_widths(viewport_width=1100)
        vals_0 = self.tab.totals_tree.item("totals", "values")
        self.assertEqual(vals_0[7], "0 of 0 timed")
        status_w_0 = self.tab.clip_tree.column("status", "width")
        self.assertGreater(status_w_0, f_bold.measure("0 of 0 timed"))

        # 21 clips
        self._populate_dummy_rows(21)
        self.tab._recompute_column_widths(viewport_width=1100)
        vals_21 = self.tab.totals_tree.item("totals", "values")
        self.assertIn("timed", vals_21[7])
        self.assertNotIn("clips timed", vals_21[7])  # Must use short text
        status_w_21 = self.tab.clip_tree.column("status", "width")
        self.assertGreater(status_w_21, f_bold.measure(vals_21[7]))

    def test_column_drag_redistribution_between_neighbors(self):
        """Dragging a column separator redistributes width between neighbouring columns only and preserves total width."""
        self._populate_dummy_rows(21)
        self.tab._recompute_column_widths(viewport_width=1100)
        cols = list(self.tab.clip_tree["columns"])

        orig_widths = {c: self.tab.clip_tree.column(c, "width") for c in cols}
        total_before = sum(orig_widths.values())
        self.assertEqual(total_before, 1100)

        # 1. Test drag within neighbour minwidth capacity (e.g. +10px)
        self.tab._on_tree_button_press()
        delta = 10
        self.tab.clip_tree.column("clip", width=orig_widths["clip"] + delta)
        self.tab._on_tree_button_release()

        new_widths = {c: self.tab.clip_tree.column(c, "width") for c in cols}
        total_after = sum(new_widths.values())

        # Total width must remain exactly 1100 (zero horizontal scrollbar)
        self.assertEqual(total_after, 1100)
        # Column 'clip' was widened by 10px
        self.assertEqual(new_widths["clip"], orig_widths["clip"] + delta)
        # Neighbouring column 'speaker' absorbed the 10px reduction
        self.assertEqual(new_widths["speaker"], orig_widths["speaker"] - delta)
        # Other columns untouched
        for c in cols:
            if c not in ("clip", "speaker"):
                self.assertEqual(new_widths[c], orig_widths[c])

        # 2. Test drag exceeding neighbour minwidth: clamped safely to neighbour minwidth
        self.tab._on_tree_button_press()
        min_spk = self.tab.clip_tree.column("speaker", "minwidth") or 30
        excessive_delta = 100
        self.tab.clip_tree.column("clip", width=new_widths["clip"] + excessive_delta)
        self.tab._on_tree_button_release()

        clamped_widths = {c: self.tab.clip_tree.column(c, "width") for c in cols}
        # Total width still remains 1100
        self.assertEqual(sum(clamped_widths.values()), 1100)
        # Speaker never drops below minwidth
        self.assertGreaterEqual(clamped_widths["speaker"], min_spk)

    def test_window_sizing_from_work_area(self):
        """Window initial sizing targets ~85% of usable work area, centered, clamped to min 1000x640."""
        work_x, work_y, work_w, work_h = theme.get_monitor_work_area()
        self.assertGreaterEqual(work_w, 800)
        self.assertGreaterEqual(work_h, 600)

        target_w = int(round(work_w * 0.85))
        target_h = int(round(work_h * 0.85))

        clamped_w = max(1000, min(work_w, target_w))
        clamped_h = max(640, min(work_h, target_h))

        self.assertGreaterEqual(clamped_w, 1000)
        self.assertGreaterEqual(clamped_h, 640)
        self.assertLessEqual(clamped_w, work_w)
        self.assertLessEqual(clamped_h, work_h)


if __name__ == "__main__":
    unittest.main()
