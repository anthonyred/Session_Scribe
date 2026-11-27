# Bug: Speaker names show as "Speaker 1" / "Speaker 2" in finished transcripts

**Date:** 2026-10-28  
**Type:** bug  
**Record:** `records/2026-10-28/bug_speaker-name-placeholder.md`

---

## What was asked

Speaker names appear as "Speaker 1" and "Speaker 2" in finished transcript `.txt` files.  
Find where the placeholder is read at run time and why it was used.

---

## Root cause

Two independent causes, both required for the bug to manifest.

### Cause 1 — No bulk mapping UI (primary)

`SessionGrouper.group_clips()` (`scribe_core/session_grouper.py` L108–131) assigns
`f"Speaker {current_session_id}"` whenever no `custom_speakers` dict is provided.
`stage2_tab.py` always calls `grouper.group_clips(clips)` **without** a
`custom_speakers` argument, so every auto-detected session receives a placeholder.

The only rename mechanism was a per-row double-click dialog on the Treeview — one
session at a time, no bulk entry — and it showed no reminder that placeholder names
still exist.  Most users saved the manifest without ever renaming.

### Cause 2 — No pre-run guard

`stage3_tab.py` read `s.get("speaker", "Speaker")` from the manifest and stamped it
onto every clip dict, which the worker then wrote verbatim into:

```
Speaker: Speaker 1           ← transcript header
[Speaker 1] [10:17:16.000 …] ← every segment line
```

There was no check in Stage 3 that any session name was still a placeholder before
starting transcription.

### Full propagation chain

```
SessionGrouper.group_clips()       → "Speaker N" default
stage2_tab._detect_thread_worker() → no custom_speakers arg
stage2_tab._save_manifest()        → saves "Speaker N" to manifest.json
stage3_tab._load_manifest()        → reads "speaker" and stamps onto clip dicts
transcription_manager.prepare_job_config() → passes clips verbatim
transcribe_worker.py L337          → speaker = clip.get("speaker", "Speaker")
transcribe_worker.py L471, L481    → written to .txt file
```

---

## What was changed

| File | Change |
|------|--------|
| `gui/stage2_tab.py` | Added **paste-mapping box** (see `feature_speaker-mapping-rename-state.md`) |
| `gui/stage3_tab.py` | Added **pre-run guard** — detects `Speaker N` pattern before launching worker |
| `gui/stage3_tab.py` | Added **Rename Speaker…** dialog — post-hoc rename without re-transcription |

The root `session_grouper.py` logic is unchanged; the default fallback is correct
behaviour for uncovered/auto-grouped clips.

---

## Files touched

- `gui/stage2_tab.py`
- `gui/stage3_tab.py`
- `tests/test_mapping_and_rename.py` (new)

---

## How it was verified

```
pytest tests/test_mapping_and_rename.py -v
```

All 31 tests passed.

- `TestMappingParser::test_two_speakers_11_and_12_clips` — confirms 11 + 12 clip
  mapping resolves correctly.
- `TestMappingParser::test_unknown_filename_reported_before_apply` — unknown
  filename raises an error before any sessions are rebuilt.
- `TestMappingParser::test_duplicate_filename_reported_before_apply` — a clip
  listed under two names raises an error before apply.
- `TestPlaceholderDetection` — 8 parametrised cases confirm the `Speaker N` regex
  matches only the correct pattern.
- `TestRenameTranscript::test_apply_changes_only_labels` — spoken text untouched.
- `TestRenameTranscript::test_rename_does_not_touch_state_file` — state file
  glossary_hash identical before and after rename.
- `TestRenameTranscript::test_skip_logic_unaffected_after_rename` — `settings_differ`
  returns `False` after rename; no re-transcription would be triggered.
