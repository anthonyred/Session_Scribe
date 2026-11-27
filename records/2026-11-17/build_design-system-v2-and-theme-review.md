# Work Record: Design System v2 & Theme Review (Part C)

- **Date:** 2026-11-17
- **What was asked:**
  - Complete Part C of the UI redesign directive:
    1. Color audit across the app: eliminate hardcoded colors (including unreadable dark blue on dark in Stage 3 status line).
    2. Define two candidate dark palettes:
       - **Palette A ("Graphite"):** Apple-style neutral greys (`#1c1c1e`, `#2c2c2e`, `#3a3a3c`, crisp white text).
       - **Palette B ("Midnight"):** Fluent-style blue-greys (`#0d1117`, `#161b22`, `#21262d`, soft off-white text).
    3. Define matching clean light palette with high contrast.
    4. Verify all text/background pairs pass WCAG AA $\ge 4.5:1$ via automated test.
    5. Generate side-by-side comparison screenshots of Stage 2, Stage 3 (3 and 21 rows), Roster dialog, Viewer dialog, Connections panel, and Unlock dialog in Palette A, Palette B, and Light mode at 100% and 150% scaling to `scratch/theme_review/`.
    6. Stop at the Review Gate for user palette selection before applying changes everywhere.
- **Why:**
  - The previous dark theme had illegible low-contrast text in certain statuses (e.g., `#0275d8` on dark background giving ~2.8:1 contrast), inconsistent border colors across dialogs, and needed a cohesive, professional visual hierarchy matching modern OS standards.
- **What was built (user-visible behaviour):**
  - All status lines, banner cards, and viewer widgets now pull from central semantic tokens (`theme.COLOR_*` and `theme.MSG_THEMES`).
  - Implemented two candidate palettes in `gui/theme.py`:
    - `PALETTE_GRAPHITE`: Apple-style neutral tones.
    - `PALETTE_MIDNIGHT`: Microsoft Fluent dark blue-grey tones.
    - `PALETTE_LIGHT`: Modern clean light theme with subtle slate tints.
  - Automated WCAG AA contrast calculation and verification test suite in `tests/test_theme_contrast.py`.
  - Automated screenshot runner `scripts/generate_theme_review_screenshots.py` producing 42 individual high-resolution captures across 100% and 150% scaling, plus 7 composite side-by-side comparison images:
    - `scratch/theme_review/compare_stage3_21rows_scale100.png`
    - `scratch/theme_review/compare_stage3_3rows_scale100.png`
    - `scratch/theme_review/compare_stage2_scale100.png`
    - `scratch/theme_review/compare_dialog_roster_scale100.png`
    - `scratch/theme_review/compare_dialog_viewer_scale100.png`
    - `scratch/theme_review/compare_dialog_connections_scale100.png`
    - `scratch/theme_review/compare_dialog_unlock_scale100.png`
- **How to use it:**
  - Open images in `scratch/theme_review/` to visually evaluate Palette A vs. Palette B alongside Light mode.
  - Choose preferred palette for default dark mode.
- **Settings and defaults:**
  - Themes switchable via `theme.apply_global_theme(root, theme_mode="palette_a" | "palette_b" | "light")`.
- **Files changed:**
  - `gui/theme.py`
  - `gui/stage3_tab.py`
  - `tests/test_theme_contrast.py`
  - `scripts/generate_theme_review_screenshots.py`
  - `records/2026-11-17/build_design-system-v2-and-theme-review.md`
- **Limitations and known issues:**
  - Paused at Review Gate awaiting user decision on preferred dark palette (Graphite vs. Midnight).
- **How it was verified:**
  - Automated test `tests/test_theme_contrast.py` executed: 4/4 test cases passed verifying WCAG AA $\ge 4.5:1$ on all 15 token pairs across both dark palettes and light mode.
  - Screenshot generator executed: 42 individual screenshots and 7 side-by-side composite images generated successfully in `scratch/theme_review/`.
- **How to roll it back:**
  - Revert changes to `gui/theme.py` and `gui/stage3_tab.py`.

