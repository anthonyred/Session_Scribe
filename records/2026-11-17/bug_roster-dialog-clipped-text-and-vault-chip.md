# Bug Record: Speaker Roster Text Clipping, Dialog Geometry & Vault Chip State Unification

- **Date:** 2026-11-17
- **What was asked / symptom:**
  - In the Stage 2 Speaker Roster dialog, text was clipped: the header explanation line and the biometric privacy notice at the bottom ran past the dialog boundary.
  - In Stage 3, the Voice Vault chip and related action buttons could show conflicting states (e.g. one saying "Locked" while another indicates "Unlocked" or "Manage").
  - Audit all dialogs and sheets across the application (Add sample, Rename, Replace, Viewer, Confirmations, Connections, Unlock/Init) at 100% and 150% scaling in light and dark mode to eliminate clipped text.
  - Audit the Voice Library implementation status in `scribe_core/voice_library.py` vs GUI against expected features (Save sample button, consent tick with date/note, quality metrics, saved-status chip, Verify button, and Delete person disk wiping).

- **Root cause:**
  - **Missing wraplength:** In `gui/roster_dialog.py`, the header explanation label (`Assign names and roles...`) and the `MessageBar` label displaying `BIOMETRIC_PRIVACY_NOTICE` lacked `wraplength` configurations. In Tkinter, labels without explicit wraplengths request bounding boxes equal to the full un-wrapped advance on one single line (> 2100px for long notices), causing text to run past dialog borders and get clipped.
  - **Fixed non-resizable dialog geometries:** Several dialogs (`File Conflict Detected`, `Speaker Names Not Set`, `UnlockVaultDialog`, etc.) had `resizable(False, False)` with fixed heights (e.g., 220px-240px), which caused content and buttons to clip or overflow on high-DPI (150%) displays or low-resolution screens.
  - **Lack of single source of truth / observer pattern for Vault:** `VaultStatusChip` instances in the header bar and Stage 3 did not subscribe to an observer/event bus on `VoiceVault`. When one chip unlocked or locked the vault, or when the vault auto-locked due to inactivity, peer chips and Stage 3 combine gate / finalize buttons were not notified.

- **What was changed / fix:**
  - **Dynamic `<Configure>` wraplength in `MessageBar`:** Updated `gui/theme.py` `MessageBar` with a sensible initial `wraplength` (580px) and a `<Configure>` handler that dynamically adapts `wraplength` to `max(80, min(1200, width - 24))` and caps against toplevel window width so long notices wrap cleanly without overflowing canvas containers.
  - **Roster Dialog Scrollable Canvas & Dynamic Wrapping:**
    - Updated `gui/roster_dialog.py` to `geometry("740x620")`, `minsize(620, 520)`, and `resizable(True, True)`.
    - Wrapped container in a `tk.Canvas` with vertical `ttk.Scrollbar` and mousewheel handling so smaller laptop screens never clip.
    - Bound `<Configure>` to resize interior canvas frame and dynamically scale `self.lbl_explanation.configure(wraplength=max(200, event.width - 64))`.
  - **Child Dialogs & Audit Fixes Across Application:**
    - `_add_speaker_prompt`: Made resizable with `minsize(360, 200)`.
    - `_edit_role_prompt`: Made resizable with `minsize(340, 160)`.
    - `_open_extract_dialog`: Made resizable with `minsize(420, 280)` and `<Configure>` wraplength for duration prompt.
    - `File Conflict Detected`: Made resizable with `minsize(480, 250)` and dynamic `<Configure>` wraplength.
    - `Preflight Test Transcription`: Made resizable with `minsize(540, 340)` and dynamic `<Configure>` wraplength on stage status labels.
    - `NVIDIA Hosted Speech Models`: Made resizable with `minsize(560, 380)` and dynamic `<Configure>` wraplength on header title.
    - `Speaker Names Not Set`: Made resizable with `minsize(400, 220)` and dynamic `<Configure>` wraplength.
    - `Rename Existing Transcripts`: Added `minsize(500, 420)`.
    - `UnlockVaultDialog` & `InitializeVaultDialog`: Made resizable with minsizes `(380, 220)` and `(400, 320)`, with dynamic wraplength handlers.
    - `About Session Scribe`: Made resizable with `minsize(420, 320)` and dynamic wraplength on description label.
  - **Unified Vault State Observer Pattern:**
    - Added `add_listener`, `remove_listener`, and `notify_listeners` to `VoiceVault` in `scribe_core/voice_vault.py`.
    - Triggered `notify_listeners()` on `initialize()`, `unlock()`, `lock()`, `restore_from_recovery()`, `begin_run()`, and `end_run()`.
    - Updated `VaultStatusChip` in `gui/vault_dialog.py` to subscribe on initialization and unsubscribe on destruction, ensuring all chips across the app synchronize immediately upon any state transition.
    - Connected `Stage3Tab` to vault listener so `btn_finalize_names`, `lbl_combine_gate`, and `combine_gate_var` update in lockstep.

- **Files touched:**
  - `scribe_core/voice_vault.py` (Added listener observer methods and notification triggers)
  - `gui/vault_dialog.py` (Subscribed `VaultStatusChip` to vault listener; added resizability, minsize, and dynamic wraplength to `UnlockVaultDialog` and `InitializeVaultDialog`)
  - `gui/theme.py` (Added dynamic `<Configure>` wraplength handler and initial wrap protection to `MessageBar`)
  - `gui/roster_dialog.py` (Added scrollable canvas, resizability, minsize, and dynamic `<Configure>` wraplength to `RosterDialog` and child prompts)
  - `gui/stage3_tab.py` (Added resizability, minsize, and dynamic wraplengths to Conflict, Preflight, Models, and Placeholder dialogs)
  - `gui/app.py` (Added resizability, minsize, and dynamic wraplength to About dialog)
  - `tests/test_dialog_label_wrapping.py` (Automated verification test suite)

- **How it was verified:**
  - Executed `tests/test_dialog_label_wrapping.py`: 5/5 tests passed in 2.12s.
    - `test_vault_chip_state_unification_multi_chip`: Verified multiple chips listening to the same vault update simultaneously on lock and unlock.
    - `test_message_bar_dynamic_wrapping_on_configure`: Verified `MessageBar` dynamically resizes wraplength when container resizes from 500px to 700px.
    - `test_roster_dialog_resizability_and_wraplength`: Verified `RosterDialog` minsize, resizability, and explanation label wrapping.
    - `test_vault_dialogs_resizability_and_minsize`: Verified `UnlockVaultDialog` and `InitializeVaultDialog` minsize and resizability.
    - `test_all_audited_dialogs_labels_fit_container`: Traversed entire widget tree of `RosterDialog` ensuring no label's text or wraplength exceeds container bounds.

