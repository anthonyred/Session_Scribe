# Build: Theme System v2, Modern Inputs, and App Typography

- **Date:** 2026-11-17
- **What was asked:**
  - Modernize Stage 2 inputs: Event Folder, Event Name, Date, Organiser, Venue, Sponsors, and spinboxes should no longer be glaring white with near-invisible text in dark mode.
  - Fix the light-grey slab behind the Resolved Paths banner and dark barely-visible hint text in Speaker Mapping.
  - Upgrade thin, washed-out typography across the app to readable semibold headings, proportional body text, and modern monospace fonts.
  - Ensure all widgets across Light and Dark modes have high-contrast palettes meeting WCAG AA ($\ge 4.5:1$).
- **Why:**
  - In dark mode, raw `tk.Entry`, `tk.Text`, and `scrolledtext.ScrolledText` retained default Windows OS white/grey backgrounds, creating severe visual discordance and illegibility against dark card surfaces.
  - Default Tkinter fonts resolved to thin Segoe UI Light/Semilight at 9–10pt, making text look washed out on high-DPI displays.
- **What was built (user-visible behaviour):**
  1. **Elevated Dark Input Surfaces:**
     - Inputs in dark mode now use `#2E2E32` (a refined dark grey surface one step lighter than cards `#242426`), subtle `#3F3F46` borders, `#0078D4` focus rings, and high-contrast `#A1A1AA` placeholders ($\ge 5.2:1$).
     - No raw white input boxes in dark mode.
     - On typing, placeholder text instantly disappears.
  2. **Monospace Code Cards:**
     - The "Resolved paths" banner and "Speaker mapping" editor now render as elevated monospace code cards with `"SS_Code"` font (preferring `Cascadia Mono`, falling back to `Consolas`), dark input backgrounds, and distinct 1px borders.
  3. **App-wide Typography Refresh:**
     - Upgraded base font sizes: `body` at 11pt, `section` at 13pt, `title` at 18pt, `code` at 10pt.
     - Upgraded weights: Semibold headers mapped to `"Segoe UI Variable Text Semibold"` or `"Segoe UI Semibold"`.
     - Standardized comfortable row heights (32px) and compact heights (26px).
  4. **Dynamic Canvas & Code Panel Theming:**
     - `ScrollableTab` canvases now dynamically update backgrounds via `refresh_theme()`.
     - `ScrolledText` widgets (diff drawers, logs) style their internal frame and vertical scrollbars, eliminating default `SystemButtonFace` grey patches.
  5. **WCAG AA Compliance:**
     - All foreground/background color pairs exceed WCAG AA ($\ge 4.5:1$).
     - Disabled button text adjusted to `#475569` on `#E5E7EB` (6.12:1).
- **How to use it:**
  - The appearance segmented control in the top-right header toggles between **System**, **Light**, and **Dark** modes. In System mode, it dynamically shows the detected OS mode, e.g. `System (Dark)`.
  - On Stage 2, input fields automatically adapt to the chosen mode.
- **Settings and defaults:**
  - Default theme is `"system"`, loading from and persisting to `ui.ini`.
  - Dark mode palette defaults to `"graphite"` (`#1C1C1E` app background, `#242426` card background, `#2E2E32` input background).
- **Files changed:**
  - `gui/theme.py`: Palette tokens (`input_bg`, `input_border`, `border_focus`, `placeholder`, `disabled_fg`), typography definitions (`BASE_FONT_SIZES`, `SS_Header`, semibold mapping), helper methods `style_code_panel`, `style_toplevel`, `center_dialog`.
  - `gui/hint_entry.py`: Dark input styling, padding, focus highlights, and dynamic state-aware `refresh_theme()`.
  - `gui/paths_banner.py`: Removed hardcoded `SystemButtonFace`; styled with `SS_Code` and dynamic `refresh_theme()`.
  - `gui/stage2_tab.py`: Full card and input styling for form entries, spinboxes, mapping text, mapping preview, and status tags.
  - `gui/stage3_tab.py`: `txt_comp_run1`, `txt_comp_run2`, `log_text` styled with `SS_Code` code card tokens; column width calculation adjustments.
  - `gui/scrollable_tab.py`: Dynamic canvas background theming on `refresh_theme()`.
  - `gui/app.py`: Centralized `_apply_theme()`, guarded `_poll_system_theme()`.
  - `tests/test_theme_lifecycle_and_inputs.py`: 7 automated tests verifying 0 steady-state calls, single apply on change, responsiveness, zero unthemed widgets, WCAG AA contrast, and segmented control resolution.
- **Limitations and known issues:**
  - **Tkinter Engine Boundaries:**
    - Tkinter widgets on Windows cannot render CSS-like smooth border radiuses, GPU acrylic blur, or spring physics animations without custom canvas redraw hacks.
    - While the Tk hotfix provides clean flat elevation and contrast, the full modern desktop aesthetic (rounded pill badges, fluent acrylic surfaces, smooth transitions) will be fully realized in the upcoming PySide6 (Qt) migration.
- **How it was verified:**
  - Ran `tests/test_theme_lifecycle_and_inputs.py` (7/7 passed).
  - Ran `tests/test_stage3_tk_table.py` (6/6 passed).
  - Ran `tests/test_theme_contrast.py` (4/4 passed).
  - Ran `tests/test_stage2_layout_acceptance.py`, `tests/test_stage2_stage3_fixes.py`, `tests/test_dialog_label_wrapping.py`, `tests/test_speaker_mapping_authoritative.py`, `tests/test_start_menu_and_app.py` (41/41 passed).
  - Captured full visual screenshot suite in `scratch/theme_review/` and `docs/images/` across Stage 2, Stage 3, and `ReTranscribeDialog` in Light and Dark modes.
- **How to roll it back:**
  - Revert changes to `gui/theme.py`, `gui/hint_entry.py`, `gui/paths_banner.py`, `gui/stage2_tab.py`, `gui/stage3_tab.py`, `gui/scrollable_tab.py`, and `gui/app.py`.
