# Stage 2 — Media Layout Fix Notes
**Date:** 2026-10-25  
**Status:** FIXED — do not commit

---

## Bug description

Selecting `D:\Events\21. Devfest 2026` (mixed flat layout) and clicking
**Detect Sessions & Assign Photos** raised an error popup:

> "No Videos directory found at `D:\Events\21. Devfest 2026\data\Videos`"

Additionally, clicking **Dry-Run** with a missing `data\` folder showed a
`messagebox.showwarning` popup instead of inline status text.

---

## Devfest 2026 disk layout

```
D:\Events\21. Devfest 2026\
    data\
        VID20261024*.mp4   (16 videos — flat in data\, NO data\Videos\ subfolder)
        IMG20261024*.jpg   (38 photos — flat in data\, NO data\Photos\ subfolder)
    Photos-1-001.zip
```

Neither `data\Videos\` nor `data\Photos\` existed at event-load time.
They are created by the Confirm & Move step.

---

## Root causes

| # | File | Location | Issue |
|---|------|----------|-------|
| 1 | `gui/stage2_tab.py` | `_run_session_detection` L468 | Hard-required `data\Videos\` to exist; raised popup if missing |
| 2 | `gui/stage2_tab.py` | `_on_context_change` L224 | Enabled `btn_detect` at the same time as `btn_dry_run`; both got NORMAL when required fields were filled, so the user could click Detect before running any separation |
| 3 | `gui/stage2_tab.py` | `_run_dry_run` L373 | Used `messagebox.showwarning` popup for a missing `data\` folder instead of inline `sep_status_var` text |
| 4 | `gui/stage2_tab.py` | `_run_dry_run` L407 | `btn_confirm_move` was enabled regardless of `video_count + photo_count`; spec requires it only when ≥1 file to move |

---

## Fixes applied (gui/stage2_tab.py)

### 1 — `_on_context_change` rewritten

- `btn_dry_run`: NORMAL when required fields filled AND `data\` directory exists.
- `btn_detect`: NORMAL when required fields filled AND `_data_dir_has_videos()` returns True
  (looks in flat `data\` **or** `data\Videos\` subfolder).
- `btn_confirm_move`: unchanged — only set NORMAL inside `_run_dry_run` after ≥1 file to move.
- When `data\` is missing: inline `sep_status_var` message, no popup.

### 2 — New helper `_data_dir_has_videos(data_dir)`

Scans flat `data\` files first, then `data\Videos\` subfolder.
Returns True as soon as any `.mp4 / .mov / .mkv / .avi / .3gp / .m4v` file is found.

### 3 — `_run_dry_run` rewritten

- Missing `data\`: sets `sep_status_var` inline, returns silently (no popup).
- `video_count + photo_count == 0`: shows informational dialog, keeps Confirm disabled, sets inline status.
- Normal result with files: unchanged (results dialog + Confirm enabled).
- On exception: sets `sep_status_var` inline AND shows error dialog (user triggered the action).

### 4 — `_run_session_detection` rewritten

- Checks `data\Videos\` first (post-separation path).
- Falls back to flat `data\` if it contains video files (pre-separation / mixed layout).
- Falls back to flat `data\` for photos too.
- On "no videos found": sets `sep_status_var` inline, returns silently (no popup).
- Both `effective_videos_dir` and `effective_photos_dir` are passed to `_detect_thread_worker`.

---

## Three-layout acceptance test results

Script: `tests/test_stage2_layout_acceptance.py` (compact version: `tests/test_layout_runner.py`)  
Run on dummy temp files (no real event data touched).

| Case | Layout | dry_run result | has_vids | Confirm enabled? |
|------|--------|----------------|----------|-----------------|
| 1 | Mixed flat | 2 v, 2 ph, 1 unk | True | YES |
| 2 | Pre-separated | 0 to move, 3 already | True | NO |
| 3 | Empty data\\ | 0 to move | False | NO |

**ALL 3 PASSED.**

---

## Files changed

- `D:\Session_Scribe\gui\stage2_tab.py` — `_on_context_change`, `_data_dir_has_videos` (new), `_run_dry_run`, `_run_session_detection`

## Files NOT changed

- `scribe_core\media_separator.py` — its `dry_run()` already handles flat mixed layout correctly
- `gui\stage3_tab.py` — Stage 3's strict manifest checks are correct and must remain

---

## Remaining rules (unchanged)

- Stage 3 still requires a loaded manifest and valid clip paths inside the selected event root.
- All outputs for an event go to `<event root>\scribe\` only.
- Empty fields on launch; no saved defaults.
- Organiser / Venue blank: warning only, not a block.
