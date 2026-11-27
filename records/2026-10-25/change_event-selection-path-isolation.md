# Event selection and path isolation (25 October 2026)

## Problem
Session Scribe opened with leftover Elevate values (event name, date, organiser, venue, folder, glossary) because those strings were hardcoded as Tk `StringVar` defaults. Stage 2 and Stage 3 each had their own folder field. Stage 3 auto-loaded `D:\Events\17.ElevateUai_cybersecurity\scribe\manifest.json` 500 ms after startup, so transcription always used Elevate even when another event was chosen on Stage 2.

`config/` was empty and unused. Nothing is remembered between launches (by design after this change).

## Behaviour now
- Launch with empty Event Folder, Name, Date, Organiser, Venue, Sponsors. Grey hints only. Speaker mode stays Single Speaker.
- Dry-Run, Detect Sessions, Save Manifest, and all Stage 3 actions stay disabled until Event Folder, Name and Date are filled. Blank Organiser or Venue is a warning only.
- The user selects the event root `D:\Events\<event>`. Derived paths are always:
  - `<event>\data`
  - `<event>\data\Photos`
  - `<event>\data\Videos`
  - `<event>\scribe` (manifest, glossary, test transcripts, full transcripts, work, logs)
- If a deeper folder (`data`, `Videos`, `scribe`, …) is chosen, the app asks whether to use the parent event root.
- A matching `<event>\scribe\manifest.json` fills empty form fields only. Typed values are never overwritten. Manifests now record `event_root`.
- Stage 2 and Stage 3 share one `EventContext`. Stage 3 has a read-only folder field, does not autoload at startup, and never hardcodes a path.
- Stage 3 stops (no fallback) if the manifest is missing, `event_root` does not match, or any clip path is missing or outside the selected event root.
- Glossary defaults to empty and is stored per event as `<event>\scribe\glossary.txt`.
- Resolved paths (event root, data, scribe, manifest) are shown in both tabs and printed at the top of UI logs and `scribe\logs\transcription.log`.

## Tests
Acceptance tests were **not run**.

- `test_stage2_acceptance.py` → `test_elevate_and_devx()` still points at real folders under `D:\Events\17.ElevateUai_cybersecurity` and DevX. Left unrun.
- `test_stage3_acceptance.py` still points at that same Elevate tree (and would write into its `scribe\` folder). Left unrun.
- `test_stage2_acceptance.py` → `test_media_separation_and_undo()` and `test_manifest()` use scratch dirs under `D:\Session_Scribe`, not real event folders. Also left unrun this round.

Path-helper checks were run in isolation with temporary directories (not real event media).

## 2026-10-23 notes
Unchanged.
