# Stage 2 Layout Fix + Stage 3 Worker Null-Guard Fix Notes
**Date:** 2026-10-25  
**Status:** FIXED — do not commit

---

## Bug 1 — Buttons clipped, window doesn't resize (Layout)

### Symptom
Dry-Run, Confirm & Move, Undo buttons and the blue status line were clipped to
thin slivers and could not be clicked. The window did not reflow when resized.

### Root cause
`ttk.PanedWindow` divides *available space* by weight. The upper pane had
`weight=1` and the lower (session table) had `weight=3`, giving the upper pane
1/4 of the tab height (~160 px). The natural height of section 1 after adding
`PathsBanner` was ~235–240 px — so buttons were silently clipped.

`PathsBanner` used a free-growing `ttk.Label` with no height constraint.
When an event was selected, the label expanded to 4 lines of Consolas text
(~75–80 px), pushing everything below it off-screen.

Neither tab had a scroll fallback so overflow was invisible.

### Files changed

| File | Change |
|------|--------|
| `gui/paths_banner.py` | Replaced `ttk.Label` with `tk.Text(height=4, state=DISABLED)` — fixed 4 lines, never grows, read-only |
| `gui/stage2_tab.py` | `paned.add(upper_frame, weight=0)` — content-sized; `paned.add(middle_frame, weight=1)`; added `form_frame.columnconfigure(1/3, weight=1)` so Entry fields stretch |
| `gui/stage3_tab.py` | `paned.add(upper, weight=0)` — same fix |
| `gui/app.py` | `minsize(950, 680)` — raised from 600 to match actual minimum content height |

### Verification
Launched app, buttons fully visible, banner compact (4 lines), tables stretch on
maximize, Stage 3 clip tree fills available space.

---

## Bug 2 — Stage 3 Test Gate crash: `AttributeError: 'NoneType' object has no attribute 'strip'`

### Symptom
Starting a Test Gate run immediately failed with:

```
AttributeError: 'NoneType' object has no attribute 'strip'
  File "transcribe_worker.py", line 91, in main
    initial_prompt = job.get("initial_prompt", "").strip() or None
```

### Root cause
`dict.get(key, default)` only returns the default when the **key is absent**.
`prepare_job_config` writes `"initial_prompt": null` to the JSON job file when
no glossary is entered (the GUI passes `None`). The worker loads the JSON and
calls `.get("initial_prompt", "")` — the key **is present** with value `None`,
so `.get()` returns `None` (ignores the default), and `.strip()` crashes on
`None`.

The same vulnerability existed for `language` (would propagate `None` into
Whisper's `transcribe()` call).

### Files changed

**`scribe_core/transcribe_worker.py` lines 90–91:**

```diff
-    language = job.get("language", "en")
-    initial_prompt = job.get("initial_prompt", "").strip() or None
+    language = job.get("language") or "en"
+    initial_prompt = (job.get("initial_prompt") or "").strip() or None
```

`or ""` / `or "en"` coerces both "key absent" and "key = null" to the safe
default before any string method is called.

### Test results

Script: `tests/test_worker_null_guard.py`

```
[PASS] initial_prompt=None in JSON -> None after fix (no crash)
[PASS] language=en preserved
[PASS] initial_prompt="" -> None,  language=null -> en
[PASS] real glossary value preserved correctly
ALL WORKER PARSE TESTS PASSED
```

---

## Summary of all changes today

| File | Change |
|------|--------|
| `gui/paths_banner.py` | Fixed-height 4-line read-only `tk.Text` (was free-growing label) |
| `gui/stage2_tab.py` | Layout fix (weight=0 upper), Entry columns stretch, mixed layout fallback |
| `gui/stage3_tab.py` | Layout fix (weight=0 upper) |
| `gui/app.py` | minsize 600→680 |
| `scribe_core/transcribe_worker.py` | Null-guard on `initial_prompt` and `language` |

No changes committed.
