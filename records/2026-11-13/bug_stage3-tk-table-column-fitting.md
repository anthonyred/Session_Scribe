# Bug: Stage 3 Tk Table Column Fitting and Cut-Off Cells

## Date
2026-11-13

## What was asked / symptom
In Stage 3 of the Tkinter application (`gui/stage3_tab.py`), the clip table headers and cells suffered from cutting and uneven distribution:
1. When an event was loaded, headers were cut off ("Tes", "Duratio", "Failover", second line of "Time taken for transcription").
2. Table cells were cut off ("✓ Approve" instead of "✓ Approved", "[TES" instead of "[TEST]").
3. Wide empty gaps appeared around some columns while narrower columns were cramped.
4. Drag redistribution failed when font stepping compressed the `clip` column below its minimum width.

## Root cause
1. **Dynamic Font Stepping over-shrinking content columns:** Font stepping calculated content column widths using stepped font measurements without enforcing a hard floor based on the unstepped cell text currently displayed in the Treeview (`SS_Body` 10pt).
2. **Sample measurement inflation:** Hypothetical sample strings (e.g. `Parakeet → Whisper medium` measuring 220px and `● Transcribing (still working…)` measuring 208px) were included in `max_cell_w` during normal column fitting, which inflated content columns to >880px at 1100px viewport width, squeezing the flexible `clip` column down to ~74px (far below its 160px `minwidth`).
3. **Separator Drag Clamping:** When `clip` was squeezed below 160px, dragging its separator caused the Treeview to clamp `clip` back up to its 160px minwidth rather than absorbing the +10px drag incrementally.

## What was changed / fix
1. **Separation of Actual Cell Content from Sample Estimates:** `_recompute_column_widths` now tracks `actual_cell_w_unstepped` measured strictly from current Treeview items. Sample strings are used only for baseline defaults when the table is empty or sparse.
2. **Floor on Font Stepping:** In font stepping mode, `computed[c]` enforces `max(..., actual_cell_w_unstepped[c])`, ensuring no column shrinks below its currently rendered cell text.
3. **Calibrated Column Minimums:**
   - `start` (`Time taken for transcription`): Set minimum width to 120px to comfortably hold the two-line bold header and timestamps.
   - `engine`: Set minimum width to 120px to hold "Cloud: Parakeet" and "Fallback (Local)".
   - Preserved `clip` (160px) and `speaker` (130px) proportions so remaining width comfortably exceeds 350px at the minimum 1100px window width.

## Files touched
- `gui/stage3_tab.py`
- `tests/test_stage3_tk_table.py`

## How it was verified
Ran the full Tk table test suite across 6 unit and integration tests covering viewport sizes from 1080px to 2560px, DPI scales 1.0 to 1.5, Light and Dark themes, and separator drag redistribution:
```powershell
D:\Session_Scribe\.venv\Scripts\python.exe -m pytest tests/test_stage3_tk_table.py
```
Result: 6 passed in 9.66s.

