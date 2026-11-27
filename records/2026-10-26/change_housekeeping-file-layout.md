# Housekeeping: File Layout Reorganisation

**Date:** 2026-10-26  
**Type:** change  
**Requested by:** User  
**Status:** Done — not committed

---

## What was asked

Move all `.py` files out of dated record folders (`2026-10-23/`, `2026-10-25/`).  
Establish permanent rules for where files go.  
Reorganise existing records to the `<type>_<short-title>.md` naming pattern.  
Create `AGENTS.md`, `GEMINI.md`, and `CHANGELOG.md`.

---

## What was changed

### Files moved

| Before | After | Method |
|--------|-------|--------|
| `2026-10-25/layout_test_runner.py` | `tests/test_layout_runner.py` | copy + delete (untracked) |
| `2026-10-25/test_stage2_layout_acceptance.py` | `tests/test_stage2_layout_acceptance.py` | copy + delete (untracked) |
| `2026-10-25/test_worker_null_guard.py` | `tests/test_worker_null_guard.py` | copy + delete (untracked) |
| `test_stage2_acceptance.py` | `tests/test_stage2_acceptance.py` | `git mv` |
| `test_stage3_acceptance.py` | `tests/test_stage3_acceptance.py` | `git mv` |
| `preflight_check.py` | `scripts/preflight_check.py` | `git mv` |

### Files renamed (markdown records)

| Before | After | Method |
|--------|-------|--------|
| `2026-10-23/stage_0_environment_notes.md` | `2026-10-23/build_environment-setup.md` | `git mv` |
| `2026-10-23/stage_2_setup_and_separation_notes.md` | `2026-10-23/build_stage2-ingest-separation.md` | `git mv` |
| `2026-10-23/stage_3_transcription_notes.md` | `2026-10-23/build_stage3-transcription.md` | `git mv` |
| `2026-10-25/event_selection_and_path_isolation_notes.md` | `2026-10-25/change_event-selection-path-isolation.md` | `Rename-Item` |
| `2026-10-25/stage_2_media_layout_notes.md` | `2026-10-25/bug_stage2-media-flat-layout.md` | `Rename-Item` |
| `2026-10-25/stage_2_layout_and_worker_fix_notes.md` | `2026-10-25/bug_stage2-layout-worker-null-guard.md` | `Rename-Item` |
| `2026-10-25/stage3_bug_fixes_notes.md` | `2026-10-25/bug_stage3-manifest-refresh-skip-done.md` | `Rename-Item` |

### Files created

| File | Purpose |
|------|---------|
| `AGENTS.md` | Project-wide rules for file placement, naming, tests, and CHANGELOG |
| `GEMINI.md` | Pointer file: "follow AGENTS.md" |
| `CHANGELOG.md` | Running log of all work, most recent first |
| `tests/` | New directory for all tests |
| `scripts/` | New directory for helper scripts |
| `scratch/` | Added to `.gitignore` (temporary/throwaway files) |

### Import paths repaired

| File | Change |
|------|--------|
| `tests/test_stage2_layout_acceptance.py` | `sys.path` guard derived from `__file__` (was hardcoded) |
| `tests/test_layout_runner.py` | Same; wrapped in `run()` + `if __name__` guard |
| `tests/test_worker_null_guard.py` | Same; wrapped in `run_tests()` + `if __name__` guard |
| `tests/test_stage2_acceptance.py` | `sys.path` guard added; scratch dirs changed from hardcoded project paths to `tempfile.mkdtemp()` |
| `tests/test_stage3_acceptance.py` | `sys.path` guard added |

### Notes updated

| File | Change |
|------|--------|
| `2026-10-25/bug_stage2-media-flat-layout.md` | Script path updated to `tests/test_stage2_layout_acceptance.py` |
| `2026-10-25/bug_stage2-layout-worker-null-guard.md` | Script path updated to `tests/test_worker_null_guard.py` |

---

## Deletions

None. No file was empty or a duplicate.

---

## How it was verified

```
tests\test_worker_null_guard.py         4/4  PASS
tests\test_stage2_layout_acceptance.py  9/9  PASS  (3 layout cases)
tests\test_layout_runner.py             3/3  PASS
test_stage2_acceptance: test_media_separation_and_undo + test_manifest  PASS
```

Tests that require real event data (`test_elevate_and_devx`, `test_stage3_acceptance.py` full run) were **not run** — they depend on `D:\Events\17.ElevateUai_cybersecurity` which is unchanged.
