"""
Session Scribe — Qt Screenshot Capture & Layout Verification Script
Automated visual QA:
- Tests Style Gallery and Stage 3 Prototype
- Resolutions: 1100x680, 1280x720, 1920x1080
- Scales: 85%, 100%, 150%
- Verifies:
  * Zero horizontal scrollbar at >= 1100px width
  * Automated WCAG AA contrast compliance (>= 4.5:1)
  * Renders and saves PNG screenshots to scratch/ui_review/
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

# Ensure repository root is in sys.path
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QColor, QFont, QPixmap
from PySide6.QtWidgets import QApplication

from gui.qt.components import ROW_HEIGHT_COMFORTABLE, ROW_HEIGHT_COMPACT
from gui.qt.stage3_view import Stage3View
from gui.qt.style_gallery import StyleGalleryWindow
from gui.qt.tokens import verify_all_token_contrasts

OUTPUT_DIR = os.path.join(REPO_ROOT, "scratch", "ui_review")


def run_verification_and_captures():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    app = QApplication.instance() or QApplication(sys.argv)

    print("=====================================================================")
    print("Session Scribe — Qt Prototype Verification & Screenshot Capture")
    print("=====================================================================")

    # 1. Automated WCAG AA Contrast Check
    print("\n[Step 1] Verifying Design System WCAG AA Token Contrast Ratios...")
    contrast_results = verify_all_token_contrasts()
    all_contrast_pass = True
    for token_name, data in contrast_results.items():
        ratio = data["ratio"]
        level = data["wcag_level"]
        status_sym = "✓ PASS" if data["passed"] else "✗ FAIL"
        if not data["passed"]:
            all_contrast_pass = False
        print(f"  {status_sym} [{level:>3}] {token_name:<28}: {ratio:>5.2f}:1  ({data['fg']} on {data['bg']})")

    assert all_contrast_pass, "Contrast check failed for one or more tokens!"
    print("  -> All token pairs meet or exceed WCAG AA requirements (>= 4.5:1).")

    # Configurations to test
    resolutions = [
        (1100, 680),
        (1280, 720),
        (1920, 1080),
    ]
    scales = [0.85, 1.0, 1.5]

    print("\n[Step 2] Testing Stage 3 Prototype Layout & Capturing Screenshots...")
    scrollbar_errors = []
    captured_files = []

    for w, h in resolutions:
        for scale in scales:
            scale_pct = int(scale * 100)
            prefix = f"stage3_{w}x{h}_scale{scale_pct}"

            # Create view
            view = Stage3View()
            view._scale = scale
            view.resize(w, h)
            view.show()
            app.processEvents()

            # Refresh styles for the given scale
            view.table.apply_theme()
            view.table.adapt_columns_to_width()
            app.processEvents()

            # Verify horizontal scrollbar is NOT active / needed
            h_bar = view.table.horizontalScrollBar()
            has_h_scroll = h_bar.isVisible() or h_bar.maximum() > 0

            # Measure columns total width vs available viewport
            viewport_w = view.table.viewport().width()
            col_total = sum(view.table.columnWidth(i) for i in range(view.table.columnCount()))

            status_str = "✓ NO H-SCROLL" if not has_h_scroll else "✗ HAS H-SCROLL"
            print(f"  {status_str} | Resolution: {w}x{h} | Scale: {scale_pct:>3}% | Viewport: {viewport_w}px | Columns: {col_total}px")

            if has_h_scroll and w >= 1100:
                scrollbar_errors.append(f"{prefix}: viewport={viewport_w}px, cols={col_total}px")

            # Capture screenshot
            pixmap = view.grab()
            img_path = os.path.join(OUTPUT_DIR, f"{prefix}.png")
            pixmap.save(img_path, "PNG")
            captured_files.append(img_path)

            view.close()
            app.processEvents()

    # Capture Style Gallery
    print("\n[Step 3] Testing Style Gallery & Capturing Screenshots...")
    for w, h in [(1180, 820), (1280, 720)]:
        for scale in [1.0, 1.25]:
            scale_pct = int(scale * 100)
            prefix = f"gallery_{w}x{h}_scale{scale_pct}"

            gallery = StyleGalleryWindow()
            gallery._scale = scale
            gallery.resize(w, h)
            gallery.show()
            app.processEvents()

            pixmap = gallery.grab()
            img_path = os.path.join(OUTPUT_DIR, f"{prefix}.png")
            pixmap.save(img_path, "PNG")
            captured_files.append(img_path)
            print(f"  ✓ Captured {prefix}.png")

            gallery.close()
            app.processEvents()

    print("\n=====================================================================")
    print("Verification Summary:")
    print(f"  - Contrast verification: {'PASSED' if all_contrast_pass else 'FAILED'}")
    print(f"  - Horizontal scrollbar check: {'PASSED (0 violations)' if not scrollbar_errors else f'FAILED ({len(scrollbar_errors)} violations)'}")
    print(f"  - Total screenshots captured: {len(captured_files)}")
    print(f"  - Screenshots directory: {OUTPUT_DIR}")
    print("=====================================================================")

    if scrollbar_errors:
        print("\nViolations found:")
        for err in scrollbar_errors:
            print("  *", err)
        sys.exit(1)


if __name__ == "__main__":
    run_verification_and_captures()
