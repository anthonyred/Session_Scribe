# Change Record: Professional Theme for All Tabs

- **Date:** 2026-11-03
- **What was asked:** Rework the central theme module so every tab (Stage 2 to Stage 5, including placeholder tabs) uses a cohesive, high-contrast, professional design built on ttk clam theme without adding dependencies. Standardize typography on Segoe UI (11pt body, 11pt bold headings/table headers, 13pt bold section titles, 20pt bold app title, Consolas 11pt code/logs/transcripts) with full DPI-awareness and a View menu with dynamic UI size scaling (Normal 100%, Large 120%, Extra large 140%). Establish a light neutral palette (app background `#F3F5F9`, white cards/panels `#FFFFFF` with 1px border `#D5DBE5`, navy header `#0B2545` with white title and `#C9D6EA` subtitle, primary text `#111827`, secondary text `#374151`, no text lighter than `#4B5563`, input placeholders no lighter than `#6B7280`). Provide primary buttons (`#1D4ED8` white text, hover `#1E40AF`), secondary buttons (white with `#94A3B8` border and dark text), and disabled buttons (`#E5E7EB` with `#4B5563` text). Structure tabs with a selected white tab, bold navy text, and 3px blue underline (`#1D4ED8`), unselected `#E2E8F0` with `#1F2937` text, and readable placeholder tabs with `(soon)`. Reorganize Stage 2 into three cards ("Event details", "Media separation", "Sessions and photos") with an even two-column grid (spacing in multiples of 8px) and a clear two-option speaker mode control. Style tables with 30px row height, `#E8EDF5` navy headers, alternating white and `#F8FAFC` rows, `#DBEAFE` selection with navy text, explicit symbols with text in Status and Approval cells (`✓ Done`, `● Running`, `✕ Failed`, `! Different settings`, `✓ Approved`, `— Not approved`), and row tinting only for Running (`#DBEAFE`), Failed (`#FEE2E2`), and Different settings (`#FEF3C7`) with dark text while Done rows stay neutral. Replace italic text with 4px left-accent MessageBars (info `#EFF6FF`/`#1E3A8A`, success `#F0FDF4`/`#14532D`, warning `#FFFBEB`/`#78350F`, error `#FEF2F2`/`#7F1D1D`) for Loaded line, test gate banner, and combine gate. Style thin 14px flat scrollbars and blue accent progress bars. Add automated contrast tests and capture screenshots across all tabs, sizes, and scales.
- **Why:** The previous UI had inconsistent contrast, small default 9pt fonts, low-contrast disabled tabs, default Tk buttons, unaligned inputs, and italic notification labels. This overhaul establishes a unified, accessible, and polished professional desktop interface that complies strictly with WCAG AA standards (all text/bg pairs $\ge 4.5:1$), prevents status from being conveyed by color alone, and scales smoothly on high-DPI displays.
- **What was built (user-visible behaviour):**
  - **DPI-Aware & Dynamic Typography:** All fonts standardized to Segoe UI (body 11pt, table text 11pt, headings 11pt bold, section titles 13pt bold, app title 20pt bold, code/logs Consolas 11pt). A new "View" menu provides "UI size" options: Normal (100%), Large (120%), and Extra large (140%), which instantly scales fonts, inputs, and table row heights application-wide.
  - **Light Neutral Palette:** Clean `#F3F5F9` app canvas with white card containers (`#FFFFFF`) with crisp 1px borders (`#D5DBE5`).
  - **Navy Header Band:** Solid `#0B2545` header with bold white app title (`#FFFFFF`, 15.8:1 contrast) and subtitle (`#C9D6EA`, 10.7:1 contrast).
  - **Interactive Notebook Tabs:** Custom tab layout featuring an active 3px blue underline indicator (`#1D4ED8`) on the selected tab with dark navy text (`#0B2545`). Unselected tabs use `#E2E8F0` with `#1F2937` text (11.8:1 contrast). Placeholder tabs for Stage 4 and Stage 5 are rendered cleanly with `"(soon)"` and informative placeholder cards when clicked.
  - **Card-Structured Stage 2 Layout:** Reorganized into three distinct cards:
    1. *Event details:* Event Folder input with browse/scan buttons, paths banner, and an even two-column grid (Event Name, Organiser, Sponsors on left; Date, Venue, Speaker Mode on right) with 8px multiple spacing. Speaker Mode provides a clear two-option control (Single speaker vs Panel discussion).
    2. *Media separation:* Features an info MessageBar and primary button `Dry-Run Media Separation` (`#1D4ED8`).
    3. *Sessions and photos:* Detection threshold spinboxes, primary action `Detect Sessions & Assign Photos`, `Save Manifest`, speaker mapping text box, and alternating sessions/photos tables.
  - **Polished Data Tables:** 30px row height with `#E8EDF5` navy headers, alternating white and `#F8FAFC` rows, and `#DBEAFE` row selection with navy text that stays clearly visible even when unfocused.
  - **Accessible Status & Approval Symbols:** Every status and approval cell combines a symbol and text:
    - Status: `✓ Done`, `✓ Done (test)`, `✓ Edited`, `● Running`, `✕ Failed`, `✕ File missing`, `! Different settings`, `● Pending (forced)`, `Pending`.
    - Approval: `✓ Approved`, `— Not approved`.
    - Row background tinting is restricted strictly to active/alert rows (`Running`, `Failed`, `Different settings`), all using verified dark text ($\ge 8.1:1$). Done rows remain neutral.
  - **MessageBar Component (Zero Italic Text):** Standardized message banner widget with a 4px left accent bar and non-italic text used for the Loaded manifest summary, test gate indicator, and combine approval gate.
  - **Flat 14px Scrollbars & Progress Bar:** Thin flat scrollbar with `#E5E7EB` track and `#94A3B8` thumb; progress bar with `#1D4ED8` blue fill.
- **How to use it:**
  - Launch Session Scribe via `python main.py`.
  - Use the top menu **View → UI size** to select Normal (100%), Large (120%), or Extra large (140%).
  - Navigate Stage 2 through the 3 cards. Primary actions are highlighted in solid blue (`#1D4ED8`).
  - View Status and Approval in Stage 3 table with symbols and text.
- **Settings and defaults:**
  - Default UI Size: Normal (1.0x, Segoe UI 11pt, row height 30px).
  - Default Speaker Mode: Single Speaker.
  - All color pairs verified $\ge 4.5:1$.
- **Files changed:**
  - `gui/theme.py`: Complete theme rewrite with DPI awareness, dynamic font scaling, custom notebook tab elements, button styles, treeview styles, tag configurations, and `MessageBar` widget.
  - `gui/hint_entry.py`: Full box styling with 1px `#94A3B8` border, 32px height, `#1D4ED8` blue focus border, and `#6B7280` placeholder.
  - `gui/app.py`: Navy header band, View menu with UI scaling, notebook tab updates, readable placeholder tabs.
  - `gui/stage2_tab.py`: Grouping into 3 cards, even two-column grid, speaker mode control, primary buttons, MessageBar status, alternating row tags.
  - `gui/stage3_tab.py`: Table row height 30px, status/approval symbols with text, primary button styles, MessageBar integration for Loaded, Test Gate, and Combine Gate, removal of italic fonts.
  - `tests/test_theme_contrast.py`: Added automated tests for theme pairs, status badges, message bars, and non-color-only status verification.
  - `tests/test_stage2_stage3_fixes.py`: Updated test assertions to match formatted symbols (`✓ Approved`, `— Not approved`, `✓ Edited`, `● Pending (forced)`).
  - `scripts/capture_theme_screenshots.py`: Automated multi-tab, multi-geometry, multi-scale screenshot generator.
- **Limitations and known issues:**
  - Tkinter font scaling scales all font-driven widgets and ttk styles; native Windows title bar text size is managed by the OS display settings.
- **How it was verified:**
  - Automated contrast tests in `tests/test_theme_contrast.py` passed with 100% compliance ($\ge 4.5:1$).
  - Full test suite in `tests/` ran 70 tests with 0 failures and 0 errors.
  - Captured 24 screenshots across all 4 tabs, 3 geometries (default, small, maximized), and 2 scaling factors (100%, 150%) to `scratch/theme_review/`.
- **How to roll it back:**
  - Revert changes to `gui/theme.py`, `gui/hint_entry.py`, `gui/app.py`, `gui/stage2_tab.py`, and `gui/stage3_tab.py`.

