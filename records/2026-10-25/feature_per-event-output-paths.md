# Feature: Per-Event Output Folders and Resolved Paths Display

> **Note:** Reconstructed from records and git history.

- **Date:** 2026-10-25
- **Type:** feature
- **Status:** Complete

---

## 1. What was asked
Isolate application data and output paths per selected event root directory (`<event root>\data\`, `<event root>\scribe\transcripts\`, `<event root>\scribe\test\`, `<event root>\scribe\state\`), and display these resolved paths clearly in the UI so the user always knows where inputs are read and outputs are written.

## 2. Why
Previously, default event folders were hardcoded or persisted across event selections, causing cross-event pollution where Stage 3 transcribed or loaded clips from the previous event.

## 3. What was built (user-visible behaviour)
- Event root selection immediately derives all subpaths (`data`, `scribe`, `transcripts`, `test`, `state`, `manifest`).
- A prominent `PathsBanner` at the top of Stage 2 and Stage 3 displays the currently active event paths.
- Selecting a new event clears previous clip lists, manifests, and tables completely.

## 4. How to use it
1. In Stage 1 / Stage 2, select an event directory.
2. The `PathsBanner` automatically updates to show the derived folders.
3. In Stage 3, the paths banner reflects the active event without restarting the application.

## 5. Settings and defaults
- Per-event state lives strictly under `<event root>\scribe\state\`.
- Transcripts default to `<event root>\scribe\transcripts\` (or `test\` during test gate).
- No external server or database configuration.

## 6. Files changed
- `scribe_core/event_paths.py`
- `gui/event_context.py`
- `gui/paths_banner.py`
- `gui/stage2_tab.py`
- `gui/stage3_tab.py`

## 7. Limitations and known issues
- If the event root path has unusual filesystem permissions, subfolder creation requires proper user access rights.

## 8. How it was verified
- Verified via `tests/test_stage2_stage3_fixes.py` (`test_load_event_with_manifest_fills_all_data`, `test_switch_event_clears_previous_completely`).

## 9. How to roll it back
- Revert changes to `gui/event_context.py` and `scribe_core/event_paths.py`.

