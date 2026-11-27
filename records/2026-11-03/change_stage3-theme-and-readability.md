# Change Record: Stage 3 Theme Module, Typography & WCAG AA Contrast

- **Date:** 2026-11-03
- **What was asked:**
  - Create a central theme module `gui/theme.py` with Windows DPI-awareness, standardized font hierarchies (Segoe UI 10pt normal/bold, Consolas 10pt), 26px table row height, dark text (`#1F2937`) on white/stripes (`#F3F4F6`), headers `#E5E7EB`, and tinted status tags with high-contrast text.
  - Verify WCAG AA contrast $\ge 4.5:1$ on every text and background pair via automated tests.
  - Apply the theme module to Stage 3.
- **Why:**
  Improve accessibility, text legibility, and visual consistency across high-DPI Windows displays. Previously, low-contrast text on colored badges and standard compact row heights reduced readability in large event runs.
- **What was built (user-visible behaviour):**
  - High-DPI awareness enabled via Windows `SetProcessDpiAwareness` to eliminate display scaling blurriness.
  - Alternating table row striping (`#FFFFFF` and `#F3F4F6`) with 26px row heights.
  - Accessible status badges with verified dark text:
    - Done: Green-100 bg (`#DCFCE7`), Green-900 text (`#14532D`) — 8.30:1 contrast.
    - Done (test) / Running: Blue-100 bg (`#DBEAFE`), Blue-900 text (`#1E3A8A`) — 8.49:1 contrast.
    - Edited: Indigo-100 bg (`#E0E7FF`), Indigo-900 text (`#3730A3`) — 8.43:1 contrast.
    - Diff settings: Amber-100 bg (`#FEF3C7`), Amber-900 text (`#78350F`) — 8.15:1 contrast.
    - Failed / Missing: Red-100 bg (`#FEE2E2`), Red-900 text (`#7F1D1D`) — 8.20:1 contrast.
    - Forced: Purple-100 bg (`#F3E8FF`), Purple-900 text (`#581C87`) — 8.88:1 contrast.
- **How to use it:**
  - Applied automatically on application launch and within Stage 3.
- **Settings and defaults:**
  - Font standard: `Segoe UI 10pt` (headers and totals in bold).
  - Row height: `26px`.
- **Files changed:**
  - `gui/theme.py`: Created central theme module and contrast calculation formulas.
  - `gui/stage3_tab.py`: Integrated `apply_global_theme`, `make_dpi_aware`, and `configure_treeview_status_tags`.
  - `tests/test_theme_contrast.py`: Automated WCAG contrast verification suite.
- **Limitations and known issues:**
  - Tkinter `clam` theme engine is used for consistent cross-platform border and background styling.
- **How it was verified:**
  - `pytest tests/test_theme_contrast.py` verified all 15 color pairs exceed 4.5:1 (ranging from 6.70:1 to 14.68:1).
- **How to roll it back:**
  - Remove `gui/theme.py` import from `gui/stage3_tab.py` and revert tag configurations.

