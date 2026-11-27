# Build Record: Stage 2 Session Model, Event Roster & Compute Zone Isolation (Phase V5)

- **Date:** 2026-11-19
- **Type:** `build`
- **Component:** Stage 2 Media Ingest, Session Grouper, Multi-Speaker Pipeline, and Security Architecture
- **Status:** Complete / Approved for Verification

---

## What was asked

Implement Phase V5 of the Session Scribe v2.0 Security Architecture:
1. **Session Model in Stage 2:** Support Solo and Panel session types. Solo sessions designate a single expected speaker; Panel sessions designate a host plus multiple panelists.
2. **Event Roster Management:** Roster selected per event from Vault v2 enrolled profiles. Profiles in 24-hour quarantine are strictly excluded. Names are visible only while Vault v2 is unlocked.
3. **One Person = One ID Invariant:** Every person maintains a single, persistent `spk_` ID (128-bit random token) across all sessions and events.
4. **Manual Merge and Split:** Maintain automatic time-gap session grouping as default, but provide explicit user controls in Stage 2 to merge adjacent/selected sessions or split a session at a specific clip boundary.
5. **Compute Zone Privacy Isolation:** Compute-zone files (`manifest.json`, `clip_state.json`) store only sequential event aliases (`S01`, `S02`, ...) and an opaque `event_id` (`evt_<16 hex chars>`). Zero `spk_` IDs or plaintext personal names are ever written to the compute zone. The authoritative mapping `alias -> spk_id` resides strictly in encrypted Vault v2 Zone 1 (`alias_maps/<event_id>.alias.enc`).
6. **Order-of-Appearance Fallback Removal:** Eliminate the insecure `roster[idx]` order-of-appearance assignment in `scribe_core/multi_speaker.py`. Unidentified speech turns remain anonymously labeled (`[Unidentified]`).
7. **Event Migration Design:** Author `docs/security/event_migration_design.md` specifying an interactive, dry-run-first migration workflow with vault-encrypted backups (`manifest.json.bak.enc`) and atomic rollback.
8. **No Changes to Transcript Generation Yet:** Leave Stage 3 transcription generation logic unchanged until Phase V6.

---

## Why

1. **Security & Privacy Isolation:** In earlier versions, `manifest.json` and `clip_state.json` stored plaintext personal names directly in the unencrypted event folder on disk, exposing attendees and speakers to unauthorized local processes or file crawlers. Isolating personal identities inside Vault v2 and exposing only per-event aliases (`S01`, `S02`, ...) in the compute zone guarantees privacy even if the event media folder is copied or shared.
2. **False Attribution Elimination:** In multi-speaker sessions, the previous heuristic assigned unidentified audio clusters to roster members based on the order they appeared in the roster list (`roster[idx]`). This produced dangerous false attributions when an unexpected speaker spoke or when an attendee interjected. Removing this fallback ensures speech is only attributed when biometric match confidence exceeds threshold.
3. **Flexible Session Organization:** Automated time-gap splitting often misclassifies brief stage breaks or combines distinct back-to-back presentations. Giving organizers explicit Merge and Split tools allows perfect session alignment before transcription begins.

---

## What was built (user-visible behaviour)

1. **Stage 2 Sessions Toolbar Actions:**
   - **"Event Roster…" Button:** Opens the modal Event Roster Manager. Displays available Vault v2 profiles (only while unlocked), assigned event aliases (`S01`, `S02`, ...), and session roles (Speaker, Host, Panelist, Moderator, Guest). Quarantined profiles never appear.
   - **"Configure Session…" Button:** Configures the selected session as **Solo** (single expected speaker dropdown) or **Panel** (host dropdown plus multi-select panelist checkboxes).
   - **"Merge Sessions" Button:** Select two or more sessions in the treeview and click to combine them into a single session, merging clips and recalculating aggregate time bounds.
   - **"Split Session…" Button:** Select a session with multiple clips to split it into two distinct sessions at the chosen clip index.
2. **Dynamic Alias & Name Rendering:**
   - When Vault v2 is **unlocked**, the Sessions table displays speaker names alongside aliases: `Jane Doe (S01)`.
   - When Vault v2 is **locked**, the Sessions table safely displays aliases only: `[S01]`, preventing identity leakage without locking up the UI.
3. **Authoritative Alias Mapping:**
   - Manifest creation automatically assigns an `event_id` (`evt_...`) and saves `compute_zone_safe=True` session dictionaries containing only aliases, start/end timestamps, and clip lists.
   - The alias-to-identity mapping is persisted inside Vault v2 Zone 1 under AES-256-GCM encryption.
4. **Anonymous Fallback Attribution:**
   - Diarized clusters without a positive voice biometric match are assigned `[Unidentified]` instead of being arbitrarily assigned to the next roster name.

---

## How to use it

1. Open Session Scribe and unlock Vault v2 using your master passphrase.
2. Navigate to **Stage 2: Media Ingest**.
3. Select an event folder and click **"Scan Clips & Detect Sessions"**.
4. Click **"Event Roster…"**:
   - In the left pane, view available enrolled profiles from Vault v2.
   - Select profiles and click **"Add to Event Roster ➔"**.
   - The system assigns aliases (`S01`, `S02`, ...) and default roles. Click **Save Event Roster**.
5. Select a session in the Sessions table and click **"Configure Session…"**:
   - Select **Solo** to assign an expected speaker, or **Panel** to select a Host and Panelists.
   - Click **Apply Configuration**.
6. If automated detection grouped clips incorrectly:
   - To merge: Select multiple sessions in the list (Ctrl+Click or Shift+Click) and click **"Merge Sessions"**.
   - To split: Select a multi-clip session, click **"Split Session…"**, select the cut point clip, and click **Split**.
7. Click **"Create Manifest & Next Steps"** to serialize the privacy-safe manifest.

---

## Settings and defaults

- Default session type: `"solo"`.
- Default session speaker mode: `"single"` for Solo, `"multi"` for Panel.
- Alias format: Sequential format `S{:02d}` (`S01`, `S02`, ...).
- Event ID format: `evt_<16 hex chars>` generated via `secrets.token_hex(8)`.
- Compute zone serialization: `compute_zone_safe=True` strips `expected_speaker`, `host`, `panelists`, and raw `spk_` IDs from `manifest.json`.

---

## Files changed

- `scribe_core/session_grouper.py`: Added `session_type`, `expected_speaker`, `host`, `panelists`, `sync_roster_fields()`, `to_dict(compute_zone_safe=True)`, `merge_sessions()`, and `split_session()`.
- `scribe_core/manifest.py`: Added `event_id` generation, compute-zone-safe session serialization, and roundtrip preservation.
- `scribe_core/multi_speaker.py`: Removed `roster[idx]` order-of-appearance attribution fallback; unassigned clusters remain `[Unidentified]`.
- `scribe_core/event_roster_manager.py`: Created manager for linking Vault v2 profiles to event-local aliases (`S01`, `S02`, ...) with quarantine enforcement and locked-vault privacy guards.
- `gui/event_roster_dialog.py`: Created dual-pane modal dialog for assembling event rosters from Vault v2 profiles with theme token styling.
- `gui/session_config_dialog.py`: Created modal dialog for configuring Solo vs Panel session parameters.
- `gui/event_context.py`: Added `event_id_var` to track the active event ID.
- `gui/stage2_tab.py`: Integrated multi-session selection, Event Roster, Configure Session, Merge, Split, and lock-state-aware alias rendering.
- `docs/security/event_migration_design.md`: Authored comprehensive migration specification for legacy unencrypted events.
- `tests/test_stage2_session_model.py`: Authored 10-suite unit test verifying session models, merge/split operations, compute zone isolation, and locked-vault privacy.

---

## Limitations and known issues

- PySide6 Qt GUI components for Stage 2 session model will be implemented once PySide6 is installed in the target environment; current implementation operates cleanly in Tkinter/ttk with full theme token compliance.
- Stage 3 transcription worker does not yet resolve `S01` aliases back to personal names during output collation; this will be implemented in Phase V6 (Transcription & Diarization Pipeline integration).

---

## How it was verified

- **Unit Test Suite:** `tests/test_stage2_session_model.py` executed:
  - `test_01_session_model_solo_and_panel_attributes`: Verified initialization and roster synchronization.
  - `test_02_session_grouper_compute_zone_safe_serialization`: Verified complete absence of personal names and `spk_` IDs in compute zone JSON.
  - `test_03_merge_sessions`: Verified merging multiple sessions, clip consolidation, and boundary recalculation.
  - `test_04_split_session`: Verified split point partitioning and photo time-gap distribution.
  - `test_05_event_roster_manager_alias_assignment`: Verified sequential alias generation (`S01`, `S02`).
  - `test_06_event_roster_manager_quarantine_exclusion`: Verified quarantined profiles are strictly excluded from event roster selection.
  - `test_07_event_roster_manager_vault_locked_privacy`: Verified locked vault returns empty profile list and hides identities.
  - `test_08_multi_speaker_no_order_of_appearance_fallback`: Verified unmatched clusters remain `[Unidentified]` without false attribution.
  - `test_09_manifest_roundtrip_with_event_id`: Verified `event_id` and privacy-safe sessions survive manifest save and load.
  - `test_10_dialog_stability_and_theme_tokens`: Verified dialog initialization, geometry, and styling.
  - **Result:** `Ran 10 tests in 7.430s - OK`.
- **Existing Regressions:**
  - `tests/test_multi_speaker.py`: `Ran 10 tests in 4.908s - OK`.
  - `tests/test_speaker_mapping_authoritative.py`: `Ran 7 tests in 0.088s - OK`.
  - `tests/test_vault_v4_enrollment.py`: `Ran 15 tests in 51.234s - OK`.

---

## How to roll it back

Revert the modified files via `git checkout -- scribe_core/session_grouper.py scribe_core/manifest.py scribe_core/multi_speaker.py gui/stage2_tab.py gui/event_context.py` and remove new files `scribe_core/event_roster_manager.py`, `gui/event_roster_dialog.py`, `gui/session_config_dialog.py`, `docs/security/event_migration_design.md`, and `tests/test_stage2_session_model.py`.
