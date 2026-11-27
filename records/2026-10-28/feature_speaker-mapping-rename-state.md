# Feature: Speaker mapping box, pre-run guard, rename tool, and state-file store

**Date:** 2026-10-28  
**Type:** feature  
**Record:** `records/2026-10-28/feature_speaker-mapping-rename-state.md`

---

## What was asked

### Task A

1. **Paste-a-mapping box in Stage 2** — the user pastes blocks of the form  
   `VID001.mp4, VID002.mp4 - Kalyani Patra` (filenames comma/newline-separated,
   `.mp4` optional, extra spaces tolerated).  
   Show a preview with name and clip count. Report before applying:  
   - filenames not found in the manifest  
   - a clip listed under two names  
   - clips in the manifest that no block covers  
   Apply only after confirmation. Uncovered clips fall back to time-gap grouping
   under a clearly marked `"Speaker N (auto)"` name.

2. **Pre-run guard in Stage 3** — if any session still has a `"Speaker N"`
   placeholder name, show a warning dialog with "Go Back" and "Continue Anyway".

3. **Rename-existing-transcripts tool** — dialog to pick a session, type a new name,
   preview the dry-run (which files and which lines will change), then apply.  
   Replace only:
   - the `Speaker: OldName` header line (exact match)
   - `[OldName]` at the start of segment lines  
   Spoken text must not be touched. Write each file atomically. Do not run GPU /
   re-transcribe.  
   Update the manifest atomically.

4. **A rename is not a "different settings" change** — must never trigger
   re-transcription or mark a clip as outdated.

### Task B

- `.meta.json` files beside transcripts: output folder must contain only `.txt`
  transcripts.
- Store per-clip settings in a single state file `scribe\state\clip_state.json`.
- Skip logic and GUI status column read from the state file.
- Migrate existing `.meta.json` files into the state file once, then move originals
  to `scribe\state\old_meta\`. Do not delete.
- Logs stay in `scribe\logs\`.

---

## What was changed

### New files

| File | Purpose |
|------|---------|
| `scribe_core/clip_state.py` | Centralised per-clip state store (write/read/differ/migrate). All writes atomic. |
| `tests/test_mapping_and_rename.py` | 31-test verification suite for all Task A and Task B additions. |
| `records/2026-10-28/bug_speaker-name-placeholder.md` | Bug record (root cause). |
| `records/2026-10-28/feature_speaker-mapping-rename-state.md` | This file. |

### Modified files

| File | What changed |
|------|-------------|
| `scribe_core/event_paths.py` | Added `"state": os.path.join(scribe, "state")` to `derive_event_paths()`. |
| `scribe_core/transcription_manager.py` | Added `state_dir` parameter to `prepare_job_config()`; included in job JSON. |
| `scribe_core/transcribe_worker.py` | **Rewrote** state-handling: imports `scribe_core.clip_state` via sys.path; runs migration on startup; writes to state file instead of `.meta.json`; reads state file for skip-check; never writes `.meta.json` beside transcripts. |
| `gui/stage2_tab.py` | **Added** paste-mapping box (`_validate_mapping`, `_apply_mapping`, `_set_mapping_preview`). |
| `gui/stage3_tab.py` | **Added** state-file-aware `_clip_disk_status`; pre-run `_check_placeholder_names` guard; `_open_rename_dialog` with dry-run preview and atomic apply; passes `state_dir` to `prepare_job_config`. |

---

## Design decisions

### Parse strategy for the mapping box
Split on the **last** ` - ` in each line (using `str.rfind`), so filenames that
themselves contain a dash are tolerated.  Pending filename tokens accumulate
across lines until a ` - Speaker Name` terminator is found, allowing multi-line
blocks.

### Rename isolation
`_apply_rename_transcript` only matches:
- Exact line `Speaker: OldName` (strip `\r\n` before comparing)
- Lines that start with `[OldName]`

It uses `str.startswith` and `str.replace` with `count=1` to ensure only the
label prefix is replaced, never any text that happens to contain the name.

### Rename ≠ re-transcription guarantee
The state file (`clip_state.json`) stores `model`, `language`, `glossary_hash`, and
`transcribed_at`. The rename operation reads and writes `.txt` files only —
`clip_state.json` is never opened, so `settings_differ()` returns the same value
before and after a rename.  This is verified by
`test_rename_does_not_touch_state_file` and `test_skip_logic_unaffected_after_rename`.

### Migration is idempotent
`migrate_meta_json` scans for `.meta.json` files and **moves** them to
`old_meta/`. After the first run there are no files left to migrate, so subsequent
calls are O(directory listing) no-ops.  Existing state entries are never overwritten,
so a second migration of a recovered `.meta.json` cannot corrupt data.

---

## Files touched

- `scribe_core/clip_state.py` (new)
- `scribe_core/event_paths.py`
- `scribe_core/transcription_manager.py`
- `scribe_core/transcribe_worker.py`
- `gui/stage2_tab.py`
- `gui/stage3_tab.py`
- `tests/test_mapping_and_rename.py` (new)

---

## How it was verified

```
pytest tests/test_mapping_and_rename.py -v
```

```
31 passed in 9.04s
```

**Test coverage:**

| Class | Tests | Covers |
|-------|-------|--------|
| `TestClipState` | 6 | write/read/differ/atomic |
| `TestMigration` | 3 | migrate, idempotent, no JSON left |
| `TestMappingParser` | 6 | 11+12 clips, unknown, duplicate, uncovered, no-ext, multiline |
| `TestRenameTranscript` | 6 | dry-run, label-only, atomic, unchanged file, state immunity, skip logic |
| `TestPlaceholderDetection` | 8 | parametrised regex |
| `TestNoJsonInTranscriptFolder` | 2 | clean folder, state-file skip |
