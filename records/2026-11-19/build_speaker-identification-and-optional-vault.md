# Build Record — Speaker Identification Engine & Optional Vault Decoupling (Phase V6)

**Date:** 2026-11-19  
**Type:** `build`  
**Components:** `scribe_core/identification_engine.py`, `gui/reidentify_dialog.py`, `gui/stage3_tab.py`, `scribe_core/combined_transcripts.py`, `gui/vault_dialog.py`, `gui/ui_settings.py`, `tests/`  

---

## What was asked

The user requested four sequential items:
1. **Find and fix the test hang:**
   - Run each default test in `tests/test_vault_v4_enrollment.py` (`01`, `08`, `12`, `14`) separately with a 60-second timeout and a traceback dump.
   - Verify hypothesis: `VoiceProfileEnrollmentDialog` showed a blocking message box when the vault was locked (`test_12`) and/or modal waits in constructors.
   - Fix all dialogs and constructors so they can never block waiting for human interaction. Make notify/confirm callbacks injectable; tests patch them.
   - Add a global per-test timeout for the entire test suite.
   - Confirm default test commands are fast (< 3s) and slow neural tests need `RUN_SLOW_TESTS=1`.
2. **Make the vault optional:**
   - The whole application (Stage 2 media separation, Stage 3 transcription, Approval gate, and Combine) must work whether Vault v2 is locked, unlocked, or never created.
   - Remove any blocking vault pre-flight check in Stage 3 and the "LOCKED … Voice vault locked" gate that blocks Combine.
   - Keep the strict "combine only after approval of all clips" workflow gate intact.
   - Keep "Setup Vault" on the status chip with a remembered "Skip for now" option.
   - Do not delete any existing security code.
3. **No new security work:**
   - Park remaining security phases (egress guard, event migration, extra hardening) in `records/registers/IDEAS.md` and `FEATURES.md`.
   - Zero new security tests and zero screenshot requirements. Normal functional tests only.
4. **Finish speaker identification (Phase V6), simply and end to end:**
   - Stage 3 runs local diarization per clip, transcribes with word timestamps, and aligns words to speakers by time midpoint.
   - Whisper and Pyannote never share the GPU (`_VRAM_EXECUTION_LOCK`). The ASR step receives NO voice biometrics or identity data.
   - If vault is unlocked and session has a roster: match each cluster against session roster only (Host first in panels), accepting matches above provisional threshold ($\ge 0.65$) with margin ($\ge 0.08$) over second-best.
   - Link clusters across session clips by voice only (acoustic centroid cosine similarity $\ge 0.70$, never order, gap or timestamp).
   - Short turns ($< 2.5$s) receive low confidence; unmatched short-turn voices in a panel become `[Audience]`. Overlap is marked.
   - If vault is locked or missing: diarize and label speakers `Speaker 1`, `Speaker 2` (or session aliases `S01`, `S02`). No names.
   - Names are inserted at display and export time while unlocked (in the transcript viewer, and in per-speaker combined files `<Speaker>_combined.txt`). When locked, aliases are shown (`S01_combined.txt`).
   - "Re-Identify…" button in Stage 3 re-runs matching on existing transcripts without re-transcribing.
   - "Who is this?" prompt (`ReidentifyDialog`) with short representative in-memory audio snippets for unknown clusters, and an optional enrol step.
   - Thresholds are configuration values, documented as provisional until calibrated on real audio.
   - Normal functional tests with synthetic data only.

---

## Why

1. **Test Suite Reliability:** Interactive Win32 message boxes inside class `__init__` methods halted headless test runs and CI runners indefinitely. A lightweight per-test timeout prevents any future deadlocks from blocking test discovery.
2. **Operational Decoupling & User Agency:** Users who do not need voice biometric profiling should be able to transcribe events immediately without being forced to create or unlock a cryptographic vault. The app must degrade gracefully to sequential aliases or generic labels without blocking any workflow step.
3. **Privacy Invariant & ASR Isolation:** Transcribing speech should never expose biometrics or personal names to speech-to-text models. Keeping compute zones completely anonymous while late-binding decrypted personal identities at viewer/export time maintains zero-knowledge privacy.
4. **Iterative Identification without Re-transcription:** When an organizer enrols or identifies a speaker late, they should not have to re-run expensive 15-minute Whisper transcriptions. Acoustic centroids and transcript timestamps can be re-attributed instantly in-place.

---

## What was built (user-visible behaviour)

1. **Optional Voice Vault:**
   - Stage 3 transcription starts immediately whether the vault is locked, unlocked, or uninitialized.
   - If uninitialized, the status chip shows `🔒 Setup Vault`. Clicking it provides a "Skip for now" button alongside "Cancel" and "Next". Right-clicking the chip allows toggling "Skip Vault Setup for Now". When skipped, the chip displays `🔒 Setup Vault (Skipped)`.
   - The Combine Approved Transcripts button is strictly gated on user approval of all clips; it is NEVER blocked by vault lock state.
2. **Late-Bound Name Resolution:**
   - **Combined Transcripts:** When Vault v2 is unlocked, clicking "Combine approved" resolves aliases (`S01`, `S02`) to decrypted names in confirmation dialogs, filenames (`Jane_Doe_combined.txt`), and speaker turn tags (`[Jane_Doe]`). When locked or uninitialized, aliases/labels are preserved (`S01_combined.txt`).
   - **In-App Transcript Viewer:** Opening a transcript while the vault is unlocked automatically displays decrypted speaker names for reading and editing. When saved, names are mapped back to aliases for compute-zone disk isolation.
3. **Speaker Identification & Re-Identify Flow:**
   - Completed multi-speaker transcripts can be re-attributed at any time by clicking the "Re-Identify…" toolbar button.
   - If unidentified speech clusters exist, the modal "Who is this?" review dialog opens:
     - Displays cluster turn counts and similarity scores.
     - "▶ Listen to Snippet": plays clean in-memory audio snippets via Windows audio (zero audio touches disk).
     - "Enroll Profile…": directly launches the voice profile enrollment wizard with consent controls.
     - "Attribution" dropdown: maps clusters to session roster members (`S01 (Host)`, `S02 (Panelist)`) or `[Audience]`.
     - "Apply & Re-Identify": updates speaker tags across transcript files in-place with ZERO Whisper re-transcription.
4. **Hardware & Thread Protection:**
   - A single background monitor thread protects all tests with a 60-second watchdog and thread stack dump if hung.
   - `VoiceProfileEnrollmentDialog` and related dialogs accept injectable `notify_fn` and `confirm_fn` callbacks and never invoke blocking OS modals during construction.

---

## How to use it

1. **Running Without a Vault:**
   - Open Stage 3 and run transcription as normal. The status chip indicates `🔒 Setup Vault` or `🔒 Setup Vault (Skipped)`.
   - Transcripts display sequential aliases (`[S01]`, `[S02]`) or speaker labels.
   - Approve clips and click "Combine approved". Session Scribe collates transcripts into `scribe/combined/S01_combined.txt` without prompting or blocking.
2. **Unlocking for Real Names:**
   - Click `🔒 Vault Locked (Click to Unlock)` on the chip and enter the master passphrase.
   - Transcripts viewed in the editor now display full personal names (`[Dr. Ada Lovelace]`).
   - Combining approved clips generates `Dr._Ada_Lovelace_combined.txt`.
3. **Re-Identifying Transcripts:**
   - Click "Re-Identify…" in Stage 3 review bar.
   - If unknown speech clusters exist, review each turn in the "Who is this?" dialog, listen to snippets, choose roster attributions, and click "Apply & Re-Identify". Transcripts update immediately.

---

## Settings and defaults

All thresholds are provisional configuration values in `scribe_core/identification_engine.py`:
- `match_threshold`: `0.65` (minimum cosine similarity for closed-set roster match)
- `second_best_margin`: `0.08` (minimum difference required between top and second-best candidate)
- `short_turn_threshold_sec`: `2.5` (turns below this duration marked low confidence)
- `cross_clip_link_threshold`: `0.70` (centroid similarity for merging clusters across session clips)
- `min_snippet_duration_sec`: `3.0` (minimum audio snippet duration for review)
- `max_snippet_duration_sec`: `8.0` (maximum audio snippet duration for review)

User preference `vault_setup_skipped` is stored in `%LOCALAPPDATA%\SessionScribe\ui.ini` under `[Preferences]`.

---

## Files changed

- `scribe_core/identification_engine.py`: Core acoustic clustering, centroid calculation, cross-clip linking, closed-set roster matching with margin, snippet extraction, and in-place re-identification.
- `gui/reidentify_dialog.py`: "Who is this?" review dialog with in-memory snippet playback and attribution mapping.
- `gui/stage3_tab.py`:
  - Removed blocking `ensure_vault_unlocked_preflight` from `_run_full_transcription()`.
  - Added `btn_reidentify` toolbar button and `_reidentify_clicked()` flow.
  - Added `_discover_unidentified_clusters_from_transcripts()` and `_apply_cluster_reassignments_to_transcripts()`.
  - Updated `TranscriptEditorDialog` for late-bound decrypted name display and alias preservation on save.
  - Updated `_combine_approved_clicked()` to pass `alias_to_name` map.
- `scribe_core/combined_transcripts.py`: Updated `build_combined_transcripts()` to resolve aliases in filenames and turn tags when `alias_to_name` is provided.
- `scribe_core/event_roster_manager.py`: Added `get_alias_to_name_map()` and updated `__init__` to accept `VoiceVault` or `VaultV2StorageEngine`.
- `gui/vault_dialog.py`: Added "Skip for now" button in `InitializeVaultDialog`, right-click skip toggle on `VaultStatusChip`, and `🔒 Setup Vault (Skipped)` label.
- `gui/ui_settings.py`: Added `vault_setup_skipped` persistence to `ui.ini`.
- `gui/enrollment_dialog.py`: Injected `notify_fn` and `confirm_fn`; removed blocking constructor popups.
- `tests/__init__.py`: Installed single-thread 60s global test timeout watchdog.
- `tests/test_identification_engine.py`: Comprehensive tests for Phase V6 identification engine and `ReidentifyDialog`.
- `tests/test_optional_vault_and_reidentify.py`: End-to-end functional regression tests for optional vault and in-place re-identification.
- `records/registers/IDEAS.md`: Parked remaining security phases (egress guard, event migration, extra hardening).
- `records/registers/FEATURES.md`: Registered Phase V6 feature and updated registers.
- `README.md`: Updated Section 1 capabilities and refreshed Last verified date.
- `CHANGELOG.md`: Added Phase V6 entry.

---

## Limitations and known issues

- **Provisional Thresholds:** Cosine similarity thresholds (`0.65` match, `0.08` margin) are provisional defaults until calibrated against real multi-speaker audio recordings using `scripts/voice_quality_report.py`.
- **In-Memory Playback:** Snippet audio playback in `ReidentifyDialog` utilizes Windows `winsound.PlaySound` (SND_MEMORY); non-Windows platforms skip audio playback unless an external audio handler is provided.

---

## How it was verified

1. **Probe Test Hang Resolution:**
   - Ran `scripts/probe_test_hang.py` testing each test (`01`, `08`, `12`, `14`) individually with a 60s watchdog.
   - All tests completed in $< 1.0$s (`test_01`: 0.57s, `test_08`: 1.00s, `test_12`: 0.53s, `test_14`: 0.47s).
   - Default suite `test_vault_v4_enrollment.py` executed: `Ran 15 tests in 2.075s, OK (skipped=11)`.
2. **Identification Engine Suite:**
   - Ran `tests/test_identification_engine.py`: `Ran 10 tests in 3.628s, OK`.
   - Verified word-to-turn midpoint alignment, short-turn panel audience attribution, centroid linking without timestamp bias, closed-set margin enforcement, and in-place re-attribution.
3. **Optional Vault & Re-Identify Regression:**
   - Ran `tests/test_optional_vault_and_reidentify.py`: `Ran 4 tests in 0.857s, OK`.
   - Verified uninitialized vault combines aliases without blocking, locked vault produces `S01_combined.txt`, unlocked vault resolves `Dr._Ada_Lovelace_combined.txt`, in-place re-identification updates transcripts without Whisper, and `vault_setup_skipped` persists across reloads.
4. **Full Regression Suite:**
   - Ran all 84 active unit tests: `Ran 84 tests in 39.923s, OK (skipped=11)`.

---

## How to roll it back

1. Revert `gui/stage3_tab.py`, `scribe_core/combined_transcripts.py`, `gui/vault_dialog.py`, and `gui/ui_settings.py` via git checkout to restore blocking preflights.
2. Remove `scribe_core/identification_engine.py` and `gui/reidentify_dialog.py`.
3. Revert `CHANGELOG.md`, `README.md`, and `records/registers/FEATURES.md`.
