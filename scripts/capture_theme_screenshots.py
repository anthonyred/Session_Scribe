"""
scripts/capture_theme_screenshots.py

Visual Verification & Screenshot Capture across Light & Dark Themes:
- Captures Stage 2, Stage 3, Usage Tab, Rename Dialog, and Settings Sheet
- Resolutions: 1100x680, 1280x720, 1920x1080
- Scales: 100%, 150%
- Themes: Light & Dark
- Strictly verifies:
  * Zero horizontal scrollbar in Stage 2 and Stage 3 tables at >= 1100px width
  * Automated WCAG AA contrast check passes for both Light & Dark
- Saves screenshots to scratch/theme_review/
"""

import os
import sys

# Configure UTF-8 for console output on Windows
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import QApplication

from gui.qt.main_window import SessionScribeMainWindow
from gui.qt.stage2_view import RenameSpeakerDialog
from gui.qt.components import SettingsSheet
from gui.qt.tokens import verify_all_token_contrasts

OUTPUT_DIR = os.path.join(REPO_ROOT, "scratch", "theme_review")
MOCK_EVENT_DIR = os.path.join(REPO_ROOT, "scratch", "mock_oracle_scribe")


def setup_mock_event():
    """Create a safe local copy of event data in scratch/ to avoid touching real D:\\Events."""
    os.makedirs(os.path.join(MOCK_EVENT_DIR, "state"), exist_ok=True)
    os.makedirs(os.path.join(MOCK_EVENT_DIR, "transcripts"), exist_ok=True)
    os.makedirs(os.path.join(MOCK_EVENT_DIR, "combined"), exist_ok=True)

    real_manifest = r"D:\Events\22.Oracle event\scribe\manifest.json"
    real_state = r"D:\Events\22.Oracle event\scribe\state\clip_state.json"

    import shutil
    if os.path.isfile(real_manifest):
        shutil.copy2(real_manifest, os.path.join(MOCK_EVENT_DIR, "manifest.json"))
    if os.path.isfile(real_state):
        shutil.copy2(real_state, os.path.join(MOCK_EVENT_DIR, "state", "clip_state.json"))


def run_visual_qa():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    setup_mock_event()
    app = QApplication.instance() or QApplication(sys.argv)

    print("=====================================================================")
    print("Session Scribe — Theme & Layout Verification QA (Light & Dark)")
    print("=====================================================================")

    # 1. Dual Palette Contrast Check
    print("\n[Step 1] Verifying WCAG AA Token Contrasts for Light & Dark...")
    for theme in ["light", "dark"]:
        contrasts = verify_all_token_contrasts(theme)
        print(f"\n--- {theme.upper()} PALETTE ---")
        for k, v in contrasts.items():
            sym = "✓ PASS" if v["passed"] else "✗ FAIL"
            print(f"  {sym} [{v['wcag_level']:>3}] {k:<26}: {v['ratio']:>5.2f}:1  ({v['fg']} on {v['bg']})")
            assert v["passed"], f"Contrast failed for {k} in {theme} mode!"
    print("  -> All token pairs meet or exceed WCAG AA requirements in both themes.")

    # 2. Main Window across Tabs, Resolutions, Scales & Themes
    print("\n[Step 2] Testing Layout & Capturing Screen Captures...")
    resolutions = [
        (1100, 680),
        (1280, 720),
        (1920, 1080),
    ]
    scales = [1.0, 1.5]
    themes = ["light", "dark"]

    h_scroll_errors = []
    captured_files = []

    # Test MainWindow at resolutions and themes
    for w, h in resolutions:
        for scale in scales:
            scale_pct = int(scale * 100)
            for theme in themes:
                window = SessionScribeMainWindow(event_scribe_dir=MOCK_EVENT_DIR)
                window._scale = scale
                window.apply_theme(theme)
                window.resize(w, h)
                window.show()
                app.processEvents()

                # Test & Capture Stage 2
                window.tab_bar.setCurrentIndex(0)
                app.processEvents()
                window.view_stage2.table_sessions.adapt_columns_to_width()
                app.processEvents()

                h_bar2 = window.view_stage2.table_sessions.horizontalScrollBar()
                if (h_bar2.isVisible() or h_bar2.maximum() > 0) and w >= 1100:
                    h_scroll_errors.append(f"Stage 2 {w}x{h} scale{scale_pct} {theme}")

                fname2 = f"stage2_{w}x{h}_scale{scale_pct}_{theme}.png"
                path2 = os.path.join(OUTPUT_DIR, fname2)
                window.grab().save(path2, "PNG")
                captured_files.append(fname2)

                # Test & Capture Stage 3
                window.tab_bar.setCurrentIndex(1)
                app.processEvents()
                window.view_stage3.table.adapt_columns_to_width()
                app.processEvents()

                h_bar3 = window.view_stage3.table.horizontalScrollBar()
                if (h_bar3.isVisible() or h_bar3.maximum() > 0) and w >= 1100:
                    h_scroll_errors.append(f"Stage 3 {w}x{h} scale{scale_pct} {theme}")

                fname3 = f"stage3_{w}x{h}_scale{scale_pct}_{theme}.png"
                path3 = os.path.join(OUTPUT_DIR, fname3)
                window.grab().save(path3, "PNG")
                captured_files.append(fname3)

                # Test & Capture Usage Tab (at 1280x720 especially)
                if (w, h) == (1280, 720):
                    window.tab_bar.setCurrentIndex(2)
                    app.processEvents()
                    window.view_usage.table_runs.adapt_columns_to_width()
                    window.view_usage.table_attempts.adapt_columns_to_width()
                    app.processEvents()

                    fname_u = f"usage_{w}x{h}_scale{scale_pct}_{theme}.png"
                    path_u = os.path.join(OUTPUT_DIR, fname_u)
                    window.grab().save(path_u, "PNG")
                    captured_files.append(fname_u)

                window.close()
                app.processEvents()

    # 3. Capture Dialogs in Light & Dark (Rename Dialog & Settings Sheet)
    print("\n[Step 3] Capturing Dialogs (Rename Dialog & Settings Sheet)...")
    for theme in themes:
        # Rename Dialog
        dlg_rename = RenameSpeakerDialog(
            old_name="Speaker 1",
            new_name="Jehosheba Amruthaluri",
            affected_transcripts_count=3,
            theme_mode=theme,
            scale=1.0,
        )
        dlg_rename.show()
        app.processEvents()
        path_ren = os.path.join(OUTPUT_DIR, f"dialog_rename_{theme}.png")
        dlg_rename.grab().save(path_ren, "PNG")
        captured_files.append(f"dialog_rename_{theme}.png")
        dlg_rename.close()
        app.processEvents()

        # Settings Sheet
        sheet = SettingsSheet(theme_mode=theme)
        sheet.show()
        app.processEvents()
        path_set = os.path.join(OUTPUT_DIR, f"dialog_settings_{theme}.png")
        sheet.grab().save(path_set, "PNG")
        captured_files.append(f"dialog_settings_{theme}.png")
        sheet.close()
        app.processEvents()

    print("\n=====================================================================")
    print("Verification Summary:")
    print(f"  - Contrast verification: PASSED (Light & Dark WCAG AA >= 4.5:1)")
    print(f"  - Horizontal scrollbar errors: {'PASSED (0 violations)' if not h_scroll_errors else f'FAILED ({len(h_scroll_errors)} violations)'}")
    print(f"  - Total screenshots captured: {len(captured_files)}")
    print(f"  - Screenshots directory: {OUTPUT_DIR}")
    print("=====================================================================")

    if h_scroll_errors:
        print("\nHorizontal scrollbar violations:")
        for e in h_scroll_errors:
            print("  *", e)
        sys.exit(1)


if __name__ == "__main__":
    run_visual_qa()
