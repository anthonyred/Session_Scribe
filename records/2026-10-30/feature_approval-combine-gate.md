# Feature: User Approval Gate and Combined Transcripts Generation

> **Note:** Reconstructed from records and git history.

- **Date:** 2026-10-30
- **Type:** feature
- **Status:** Complete

---

## 1. What was asked
1. Gate the generation of combined speaker transcripts behind explicit user approval of completed transcripts.
2. Provide multi-select "Approve selected" and "Approve all completed" buttons with instant feedback and unapprovable reason dialogs.
3. Combine approved transcripts chronologically per speaker into `scribe\combined\<Speaker>.txt`, archiving old combined files on speaker rename.

## 2. Why
Automated combining of unreviewed or partially completed transcripts can lead to incorrect or incomplete master session logs. Explicit approval ensures the user validates accuracy and completion before creating final combined records.

## 3. What was built (user-visible behaviour)
- "Approve selected" (supports Ctrl/Shift multi-select) and "Approve all completed" buttons.
- Visual status tag and "N of M approved" live counter.
- "Combine Approved Transcripts" button strictly locked until at least one approved clip exists; displays detailed confirmation dialog.
- Combined transcript generation groups clips by speaker in chronological order.
- Invalidation: editing a transcript or changing speaker clears approval and flags the combined file as stale.

## 4. How to use it
1. Transcribe clips in Stage 3.
2. Select completed clips and click "Approve selected" (or "Approve all completed").
3. Click "Combine Approved Transcripts". Master speaker files are created under `scribe\combined\`.

## 5. Settings and defaults
- Approvals stored in `scribe\state\clip_state.json`.
- Invalidation uses SHA-256 hash comparison of transcript file content.

## 6. Files changed
- `scribe_core/clip_state.py`
- `scribe_core/combined_transcripts.py`
- `gui/stage3_tab.py`
- `tests/test_stage2_stage3_fixes.py`

## 7. Limitations and known issues
- Only clips with status `Done`, `Done (test)`, `Edited`, or `Completed` can be approved.

## 8. How it was verified
- 23 tests in `tests/test_stage2_stage3_fixes.py` (approval persistence, invalidation, combine lock, and feedback).

## 9. How to roll it back
- Revert combine lock check and approval validation in `gui/stage3_tab.py`.

