"""
scripts/capture_tk_stage3_fix.py

Capture screenshots of Session Scribe Tkinter interface after the Stage 3 table hotfix:
- Light & Dark theme modes
- Populated with dummy data ('Bhatti Vikramarka Mallu')
- Demonstrating complete 2-line headers, zero horizontal scrollbar, totals row short text ('21 of 21 timed'),
  in-app 'View ▾' dropdown, and '[ System | Light | Dark ]' segmented control.
"""

import os
import sys
import time
import tkinter as tk
from PIL import ImageGrab

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from gui.app import SessionScribeApp
import gui.theme as theme

OUTPUT_DIR = os.path.abspath("scratch/theme_review")
os.makedirs(OUTPUT_DIR, exist_ok=True)


def capture_window(app: SessionScribeApp, out_path: str):
    app.update()
    time.sleep(0.3)
    app.update_idletasks()
    x = app.winfo_rootx()
    y = app.winfo_rooty()
    w = app.winfo_width()
    h = app.winfo_height()
    # Capture window
    img = ImageGrab.grab(bbox=(x, y, x + w, y + h))
    img.save(out_path)
    print(f"Captured: {out_path} ({img.size})")


def populate_dummy_stage3(app: SessionScribeApp, count: int = 21):
    tab = app.stage3_tab
    tab._clear_clips()
    tab.clips = []
    speaker = "Bhatti Vikramarka Mallu"
    for idx in range(1, count + 1):
        c = {
            "name": f"VID_mock_20261107_{idx:03d}.mp4",
            "duration": 45.0 + (idx * 15.0),
            "speaker": speaker,
        }
        tab.clips.append(c)
        dur_m, dur_s = divmod(int(c["duration"]), 60)
        is_test = idx in (1, 2)
        tab.clip_tree.insert(
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
    tab._update_totals_row()
    tab._recompute_column_widths()


def main():
    print("Launching SessionScribeApp for screenshot capture...")
    app = SessionScribeApp()
    app.geometry("1180x750+80+60")
    app.update()

    # 1. Stage 3 Light Theme Populated
    app.set_theme_mode("light")
    app.notebook.select(1) # Stage 3
    populate_dummy_stage3(app, 21)
    app.update()
    capture_window(app, os.path.join(OUTPUT_DIR, "tk_stage3_light.png"))

    # 2. Stage 3 Dark Theme Populated
    app.set_theme_mode("dark")
    app.update()
    capture_window(app, os.path.join(OUTPUT_DIR, "tk_stage3_dark.png"))

    # 3. Stage 2 Dark Theme
    app.notebook.select(0) # Stage 2
    app.update()
    capture_window(app, os.path.join(OUTPUT_DIR, "tk_stage2_dark.png"))

    # 4. Stage 3 Empty (0 rows)
    app.notebook.select(1)
    app.stage3_tab._clear_clips()
    app.stage3_tab._update_totals_row()
    app.stage3_tab._recompute_column_widths()
    app.update()
    capture_window(app, os.path.join(OUTPUT_DIR, "tk_stage3_0_rows.png"))

    app.destroy()
    print("All screenshots captured in scratch/theme_review/")


if __name__ == "__main__":
    main()
