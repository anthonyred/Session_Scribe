# Stage 3 Bug Fixes — Notes (26 October 2026)

## Bugs addressed

### Bug 1: Stage 3 did not refresh after Stage 2 saved a new manifest

**Root causes (four independent failures)**

| # | Location | Root cause |
|---|----------|------------|
| 1a | `EventContext.__init__` | `_notify()` wired to folder/form `StringVar`s only; `_save_manifest()` never touched them |
| 1b | `Stage3Tab._on_context_change` | Reload guard keyed on `_attempted_root`; folder did not change → guard blocked reload |
| 1c | `Stage3Tab` | No tab-focus hook, no mtime watcher |
| 1d | `Stage3Tab` | No Reload button |

**Fixes applied**

- **`gui/event_context.py`**: Added `bind_manifest_saved()` / `notify_manifest_saved()`.
  Stage 2 calls `notify_manifest_saved()` after writing `manifest.json`; Stage 3
  receives the signal and reloads immediately without restarting the app.

- **`gui/stage2_tab.py`**: `_save_manifest()` calls `self.ctx.notify_manifest_saved()`
  after a successful save (one line added).

- **`gui/stage3_tab.py`**:
  - `_on_manifest_saved_signal()`: if not running → `_load_manifest(silent=True)`;
    if running → set `_pending_reload=True` and show orange banner
    "Manifest changed — clip list will reload after this run ends."
  - `<Map>` binding on the Frame (`_on_tab_shown`) → reloads if manifest mtime changed.
  - "⟳ Reload" button added to header row (always forces a fresh read from disk;
    blocked with a notice if a run is active).
  - `_pending_reload` applied in `_on_job_finished` and `_on_job_error`.

**Detect Sessions behaviour**: already reads `os.listdir` fresh on each click — no change
needed. Deleted files are absent from the new manifest as soon as Detect Sessions
and Save Manifest are re-run.

---

### Bug 2: Already-transcribed test clips re-transcribed in full run

**Root causes**

| # | Location | Root cause |
|---|----------|------------|
| 2a | `transcribe_worker.py:173` | Skip check only looked in `output_dir`; never checked `scribe\test` for full run |
| 2b | `transcribe_worker.py:173` | Completion check was `getsize > 100`, not header-based |
| 2c | `transcription_manager.py` | No settings sidecar; glossary changes undetectable |
| 2d | `stage3_tab.py` | No pre-run copy from `scribe\test` → `scribe\transcripts` |
| 2e | `stage3_tab.py` | Clip list always `Pending`; Done/Diff states not shown |

**Fixes applied**

- **`scribe_core/transcribe_worker.py`** (full rewrite):
  - `is_completed_transcript(path)`: header-based completion check
    (`"Transcription Completed:" in first 4096 bytes`).
  - `find_completed_transcript(stem, output_dir, test_dir, transcripts_dir)`:
    searches in priority order; returns `(path, location_tag)` where tag ∈
    `{"output", "test", "transcripts", None}`.
  - Cross-dir copy: if found in `test_dir` and `output_dir == transcripts_dir`,
    copies to output (original kept).
  - Settings sidecar: `write_sidecar(meta_path, model, language, ghash, …)` written
    atomically (`.tmp` → rename) alongside every transcript.
  - `settings_differ(meta, model, language, ghash)`: reads sidecar; empty sidecar
    treated as matching (benefit of the doubt).
  - `force_clips` set: clip names in this set bypass the skip check entirely.
  - Pre-run summary emitted as `{"type": "run_summary", "message": …}` before model
    loads: *"16 clips: 2 done, 2 done (test — will copy), 12 to transcribe"*.
  - Atomic `os.replace(tmp, final)` retained; a cancelled run never leaves a file
    that looks complete.

- **`scribe_core/transcription_manager.py`**:
  - `prepare_job_config()` now accepts `test_dir`, `transcripts_dir`, `force_clips`;
    computes `glossary_hash` from `initial_prompt` and writes all to job JSON.

- **`gui/stage3_tab.py`**:
  - `_clip_disk_status(clip, paths, ghash)` → `Pending | Done | Done (test) | Diff settings | File missing`.
  - Status column colour-coded: green=Done, blue=Done(test), orange=Diff settings, red=File missing, purple=forced.
  - `_refresh_status_column()` called after manifest load, glossary change, and run end.
  - Right-click context menu: "Force re-transcribe (toggle)" — the **only** way to redo
    a finished clip; shown as purple "Pending (forced)" in the list.
  - `_build_run_summary()` computes and logs the pre-run summary before the job starts.
  - `prepare_job_config` call passes `test_dir`, `transcripts_dir`, `force_clips`.
  - `_handle_worker_event` handles new event types: `run_summary`,
    `clip_skipped_different_settings`, and `forced` flag on `clip_start`.

---

## Dummy-file tests (18/18 passed)

Run from `D:\Session_Scribe`:

```
.venv\Scripts\python.exe "C:\Users\antho\.gemini\antigravity-ide\brain\56326c66-1b09-4b23-84a8-9bfb016d0a32\scratch\test_stage3_bugs.py"
```

Tests covered:
- A. `is_completed_transcript`: missing / tiny / no-header / complete
- B. `find_completed_transcript`: nothing / test-only / transcripts / `.tmp` not found
- C. Sidecar round-trip and `settings_differ` (same/diff glossary/model/empty)
- D. `glossary_hash` stability and length

Acceptance tests (`test_stage3_acceptance.py`) still point at
`D:\Events\17.ElevateUai_cybersecurity` — **not run** this round (real data).

---

## Files changed

| File | Change |
|------|--------|
| `gui/event_context.py` | `bind_manifest_saved` / `notify_manifest_saved` added |
| `gui/stage2_tab.py` | `notify_manifest_saved()` call added after save |
| `gui/stage3_tab.py` | Reload signal, mtime backstop, Reload button, deferred reload, status column, right-click force, run summary |
| `scribe_core/transcription_manager.py` | `test_dir`, `transcripts_dir`, `force_clips`, `glossary_hash` added to `prepare_job_config` |
| `scribe_core/transcribe_worker.py` | Cross-dir skip, header check, sidecar, copy from test, force_clips, run_summary event |

Not committed (as requested).
