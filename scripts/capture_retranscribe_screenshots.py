"""
scripts/capture_retranscribe_screenshots.py

Captures screenshots of the redesigned ReTranscribeDialog using dummy data only
(no real event audio, identities, or secrets) in Light and Dark themes,
at 100% and 150% scales.

Saves PNGs directly into docs/images/.
"""

import os
import sys
import time
import tkinter as tk
from PIL import ImageGrab

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_SCRIPT_DIR)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from gui import theme
from gui.stage3_tab import ReTranscribeDialog

DOCS_IMG_DIR = os.path.join(_PROJECT_ROOT, "docs", "images")
os.makedirs(DOCS_IMG_DIR, exist_ok=True)

DUMMY_CLIP = {
    "name": "clip_keynote_presentation.mp4",
    "duration": 485.5,
    "speaker": "Speaker 1",
    "file_size_bytes": 104857600,
}


def capture_dialog(dialog: tk.Toplevel, out_path: str):
    dialog.update()
    time.sleep(0.4)
    dialog.update_idletasks()
    x = dialog.winfo_rootx()
    y = dialog.winfo_rooty()
    w = dialog.winfo_width()
    h = dialog.winfo_height()
    if w > 10 and h > 10:
        img = ImageGrab.grab(bbox=(x, y, x + w, y + h))
        img.save(out_path)
        print(f"Captured: {out_path} ({img.size})")
    else:
        print(f"Failed to capture dialog: invalid geometry {w}x{h}")


def run_captures():
    modes = [
        ("dark", 1.0, os.path.join(DOCS_IMG_DIR, "retranscribe_dialog_dark.png")),
        ("light", 1.0, os.path.join(DOCS_IMG_DIR, "retranscribe_dialog_light.png")),
        ("dark", 1.5, os.path.join(DOCS_IMG_DIR, "retranscribe_dialog_scale150_dark.png")),
        ("light", 1.5, os.path.join(DOCS_IMG_DIR, "retranscribe_dialog_scale150_light.png")),
    ]

    for mode, scale, out_path in modes:
        root = tk.Tk()
        root.geometry("200x200")
        root.withdraw()
        theme.init_theme_fonts(root, scale=scale)
        theme.apply_global_theme(root, scale=scale, theme_mode=mode, force=True)

        # Create dummy dialog
        dlg = ReTranscribeDialog(
            parent=root,
            clips=[DUMMY_CLIP],
            on_start_callback=lambda *a, **kw: None,
            event_root=r"C:\dummy_events\demo_event",
        )

        # Force render
        dlg.deiconify()
        dlg.lift()
        dlg.focus_force()
        dlg.update()

        capture_dialog(dlg, out_path)

        dlg.destroy()
        root.destroy()
        time.sleep(0.2)


if __name__ == "__main__":
    run_captures()
