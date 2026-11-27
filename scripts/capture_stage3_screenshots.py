"""
scripts/capture_stage3_screenshots.py

Generates Stage 3 review screenshots with DUMMY DATA only:
- Resolutions: 1100x680 (minimum), 1280x720 (standard), 1920x1080 (HD)
- Scales: 100% (1.0), 150% (1.5)
- Themes: Light, Dark
- Row counts: 21 rows, 60 rows
- Long names: "Jehosheba Amruthaluri"
- Drawer states: Collapsed, Expanded
- Saves to scratch/theme_review/ and docs/images/
- Strictly zero keys, tokens, or audio.
"""

import json
import os
import shutil
import sys
import tempfile
from typing import Optional

# Ensure root in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from PySide6.QtCore import QPoint, QRect, QSize, Qt
from PySide6.QtGui import QGuiApplication, QPixmap
from PySide6.QtWidgets import QApplication

from gui.qt.main_window import SessionScribeMainWindow
from gui.qt.stage3_view import Stage3View
from scribe_core.usage_csv import UsageCsvTracker, now_ist_iso


def setup_mock_event(dest_scribe: str, row_count: int = 21, temp_root: Optional[str] = None):
    os.makedirs(os.path.join(dest_scribe, "state"), exist_ok=True)
    os.makedirs(os.path.join(dest_scribe, "transcripts"), exist_ok=True)
    os.makedirs(os.path.join(dest_scribe, "combined"), exist_ok=True)
    os.makedirs(os.path.join(dest_scribe, "usage"), exist_ok=True)

    acc_path = os.path.join(temp_root or os.path.dirname(dest_scribe), "_usage", "account_attempts.csv")
    speakers = [
        "Jehosheba Amruthaluri",
        "Surender Tanwar",
        "Madhav Poosarla",
        "Kishore Katta",
        "Subhankar Dey",
    ]

    sessions = []
    clips_per_speaker = row_count // len(speakers)
    rem = row_count % len(speakers)

    clip_num = 1
    for s_idx, spk in enumerate(speakers):
        count = clips_per_speaker + (1 if s_idx < rem else 0)
        clips = []
        for c in range(count):
            clips.append({
                "name": f"VID_mock_20261107_{clip_num:03d}_{spk.split()[0]}.mp4",
                "duration": 45.0 + (c * 15.0),
            })
            clip_num += 1
        sessions.append({
            "session_id": s_idx + 1,
            "speaker": spk,
            "clips": clips,
        })

    manifest = {
        "event": {
            "name": "Demo Tech Days 2026",
            "date": "07 November 2026",
        },
        "sessions": sessions,
    }
    with open(os.path.join(dest_scribe, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)

    clips_state = {}
    for session in sessions:
        for c in session["clips"]:
            stem = os.path.splitext(c["name"])[0]
            clips_state[stem] = {
                "completed": True,
                "approved": True,
                "engine": "cloud",
            }
            # Create dummy transcript file
            t_path = os.path.join(dest_scribe, "transcripts", f"{stem}.txt")
            with open(t_path, "w", encoding="utf-8") as f:
                f.write(f"Speaker: {session['speaker']}\nTranscript text for {c['name']} verified.\n")

    with open(os.path.join(dest_scribe, "state", "clip_state.json"), "w", encoding="utf-8") as f:
        json.dump({"version": "1", "clips": clips_state}, f, indent=2)

    # Populate usage CSV attempts
    tracker = UsageCsvTracker(event_scribe_dir=dest_scribe, account_attempts_path=acc_path)
    for session in sessions:
        for c in session["clips"]:
            tracker.log_attempt({
                "attempt_id": f"att_{c['name'][:12]}",
                "run_id": "run_001",
                "event": "Demo Tech Days 2026",
                "clip": c["name"],
                "attempt_no_for_clip": 1,
                "started_at": now_ist_iso(),
                "ended_at": now_ist_iso(),
                "time_taken_s": 8.5,
                "trigger": "full",
                "engine_start": "ai-parakeet-tdt-0_6b-v2",
                "outcome": "completed",
                "audio_s_clip": c["duration"],
                "requests": 2,
            })


def main():
    app = QApplication.instance() or QApplication(sys.argv)

    out_scratch = os.path.abspath("scratch/theme_review")
    out_docs = os.path.abspath("docs/images")
    os.makedirs(out_scratch, exist_ok=True)
    os.makedirs(out_docs, exist_ok=True)

    temp_root = tempfile.mkdtemp(prefix="stage3_screens_")
    scribe_21 = os.path.join(temp_root, "mock_21", "scribe")
    scribe_60 = os.path.join(temp_root, "mock_60", "scribe")
    setup_mock_event(scribe_21, row_count=21, temp_root=temp_root)
    setup_mock_event(scribe_60, row_count=60, temp_root=temp_root)

    resolutions = [
        ("1100x680", 1100, 680),
        ("1280x720", 1280, 720),
        ("1920x1080", 1920, 1080),
    ]

    captured_files = []

    for rows_label, s_dir in [("21rows", scribe_21), ("60rows", scribe_60)]:
        window = SessionScribeMainWindow(event_scribe_dir=s_dir)
        window.tab_bar.setCurrentIndex(1)  # Stage 3

        for res_label, w, h in resolutions:
            for scale in [1.0, 1.5]:
                scale_label = f"scale_{int(scale*100)}"
                window._scale = scale

                for theme in ["light", "dark"]:
                    window.apply_theme(theme)
                    window.resize(w, h)
                    window.show()
                    app.processEvents()

                    # 1. Drawer Collapsed
                    window.view_stage3.drawer_content.setVisible(False)
                    window.view_stage3._is_drawer_open = False
                    window.view_stage3.btn_toggle_drawer.setText("▶  Activity Drawer")
                    window.view_stage3.table.adapt_columns_to_width()
                    app.processEvents()

                    fname_col = f"stage3_{theme}_{res_label}_{scale_label}_{rows_label}_collapsed.png"
                    p_scratch = os.path.join(out_scratch, fname_col)
                    pix = window.grab()
                    pix.save(p_scratch)
                    captured_files.append(fname_col)

                    # Also save key standard screenshots to docs/images
                    if res_label == "1280x720" and scale == 1.0 and rows_label == "21rows":
                        p_docs = os.path.join(out_docs, f"stage3_{theme}_overview.png")
                        pix.save(p_docs)

                    # 2. Drawer Expanded (select row 0 to show clip attempts)
                    window.view_stage3.drawer_content.setVisible(True)
                    window.view_stage3._is_drawer_open = True
                    window.view_stage3.btn_toggle_drawer.setText("▼  Activity Drawer")
                    window.view_stage3.table.selectRow(0)
                    window.view_stage3.table.adapt_columns_to_width()
                    app.processEvents()

                    fname_exp = f"stage3_{theme}_{res_label}_{scale_label}_{rows_label}_expanded.png"
                    p_scratch_exp = os.path.join(out_scratch, fname_exp)
                    pix = window.grab()
                    pix.save(p_scratch_exp)
                    captured_files.append(fname_exp)

                    if res_label == "1280x720" and scale == 1.0 and rows_label == "21rows":
                        p_docs_exp = os.path.join(out_docs, f"stage3_{theme}_drawer_attempts.png")
                        pix.save(p_docs_exp)

        window.close()

    shutil.rmtree(temp_root, ignore_errors=True)
    print(f"Captured {len(captured_files)} screenshots successfully.")
    for f in captured_files[:10]:
        print(f"  - {f}")
    if len(captured_files) > 10:
        print(f"  ... and {len(captured_files) - 10} more.")


if __name__ == "__main__":
    main()
