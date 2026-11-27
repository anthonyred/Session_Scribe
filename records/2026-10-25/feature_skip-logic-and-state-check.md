# Feature: Transcription Skip Logic and State Validation

> **Note:** Reconstructed from records and git history.

- **Date:** 2026-10-25
- **Type:** feature
- **Status:** Complete

---

## 1. What was asked
Before transcribing each clip, check if a completed transcript already exists in `scribe\transcripts` or `scribe\test`. If so, skip the clip (or copy from `test\` to `transcripts\`), check if recorded model/language/glossary settings differ, and allow forcing re-transcription.

## 2. Why
Transcribing lengthy video clips repeatedly wastes significant GPU and wall-clock time. If a transcript already finished or was verified in the test gate, it should be reused safely unless the user deliberately forces re-transcription.

## 3. What was built (user-visible behaviour)
- Pre-run summary displays exact breakdown: clips done, clips done (test only — will copy), and clips to transcribe.
- Clips already completed are marked `Done` or `Done (test)` and skipped during execution.
- If settings differ from the recorded state, the clip status is marked `Diff settings` and skipped unless force re-transcribe is chosen.
- Right-clicking a clip allows toggling "Force re-transcribe" (purple `Pending (forced)`).

## 4. How to use it
- Normal run: Click "Transcribe All" — finished clips are automatically skipped.
- Redo clip: Right-click a clip in the clip list treeview and select "Force re-transcribe", then click "Transcribe All".

## 5. Settings and defaults
- Checks `scribe\transcripts` first, then `scribe\test`.
- "Transcription Completed:" header line is mandatory for completion verification.
- Defaults to skipping matching completed transcripts.

## 6. Files changed
- `scribe_core/transcribe_worker.py`
- `scribe_core/clip_state.py`
- `gui/stage3_tab.py`

## 7. Limitations and known issues
- Manual text edits require user confirmation before forced re-transcription overwrites them.

## 8. How it was verified
- Verified via `tests/test_stage2_stage3_fixes.py` (`test_stage3_loads_same_clips_without_restart`).

## 9. How to roll it back
- Revert `find_completed_transcript` and skip check logic in `scribe_core/transcribe_worker.py`.

