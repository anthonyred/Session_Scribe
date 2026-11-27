# Bug Fix: Dialog Clipping, Top-Left Positioning, and Layout Scaling Audit

- **Date:** 2026-11-27
- **What was asked / symptom:**
  - The "Forgot Passphrase — Erase Voice Vault" dialog was too short; the Erase and Cancel buttons were clipped below the bottom edge of the window, preventing the user from executing the vault erasure flow.
  - Both the Forgot Passphrase dialog and the Unlock dialog opened in the top-left corner (`+0+0`) of the screen rather than centered over the application window.
  - The Unlock dialog displayed an empty bordered/card-colored strip underneath the passphrase field when no error was active.
  - Audit every dialog across the application across 100%, 125%, 150%, and 175% display scaling (and with the A+ font zoom setting) to guarantee all buttons and primary controls remain fully visible, center over `winfo_toplevel()`, hide empty status strips, and support Escape to cancel and Enter to confirm.

---

## Root Cause Analysis

### 1. Button Clipping in `ForgotPassphraseDialog`
- **Fixed geometry:** The dialog previously specified `self.geometry("520x400")`. At 100% scaling with standard 9pt Segoe UI, the multi-line warning text (320+ characters, 7 lines), title, entry box, prompt, live match indicator, status label, and action buttons required 410–440px minimum. At 125% scaling this required ~490px; at 150% ~560px; and at 175% ~640px.
- **Pack order:** The footer button frame (`btn_box`) was packed *after* the warning message and input fields inside `container`. In Tkinter's packing algorithm, earlier-packed widgets are allocated space first; when the window height was constrained to 400px, earlier widgets consumed the vertical allocation and `btn_box` was compressed or pushed completely below the visible canvas edge.

### 2. Dialog Top-Left Positioning (`+0+0`)
- **Parent size calculation:** `center_window()` computed parent bounds using `self.master.winfo_width()` and `self.master.winfo_height()` instead of the application window (`winfo_toplevel()`).
- When a child dialog was spawned from another modal dialog (e.g. `ForgotPassphraseDialog` 520x400 spawned from `UnlockVaultDialog` 450x320), `pw < w` caused `(pw - w) // 2` to become negative.
- `max(0, (pw - w) // 2)` clamped negative offsets to 0, placing the dialog at `(px + 0, py + 0)`.
- When parent dialogs had not yet finished mapping or were offscreen, `px=0, py=0`, forcing child dialogs to `+0+0` (top-left screen corner).

### 3. Empty Bordered Strip in `UnlockVaultDialog`
- In `UnlockVaultDialog._build_ui()`, `self.status_lbl = ttk.Label(container, text="", ...)` was packed on init with `pady=(0, 6)`.
- In `gui/theme.py`, `TLabel` has `background=COLOR_CARD_BG` (#FFFFFF in light, #242426 in dark) while `container` (`TFrame`) has `background=COLOR_APP_BG` (#F3F5F9 in light, #1C1C1E in dark). Even with `text=""`, ttk labels occupy line height, rendering an empty card-colored strip.

---

## What was Changed / Fix

### 1. Unified Dialog Centering & Geometry Engine (`gui/theme.py`)
- Rewrote `center_dialog(dialog, parent, width, height, min_width, min_height)`:
  - Queries `parent.winfo_toplevel()` to center over the actual main window.
  - Queries monitor work area via `get_monitor_work_area()` to clamp coordinates within active display bounds.
  - Falls back to monitor work-area center if parent is unmapped, minimized, or smaller than 100x100px.
  - Sets sensible content-derived minsize and enables resizability.
- Hardened `MessageBar._on_var_changed` with a `winfo_exists()` guard to prevent callback race conditions during widget destruction.

### 2. Redesigned `ForgotPassphraseDialog` (`gui/vault_dialog.py`)
- Sized dynamically from content with minimum size `480x400`, resizable.
- Footer frame packed with `side="bottom", fill="x"` first, ensuring action buttons are always pinned and visible.
- Body wrapped in scrollable canvas with dynamic `<Configure>` wraplength tracking for small displays and extreme DPI scaling.
- Case-insensitive confirmation: checks `confirm_var.get().strip().upper() == "ERASE VAULT"`.
- Live indicator label updates dynamically (gray prompt -> orange mismatch -> green match).
- Erase button enabled only on match; `<Return>` triggers erasure when enabled, `<Escape>` cancels.
- `status_lbl` hidden until populated via `_set_status()`.

### 3. Application-Wide Dialog Audit & Hardening
- **`UnlockVaultDialog`**: Pinned footer at bottom first; `status_lbl` hidden until populated; centered over toplevel; `<Escape>` cancels.
- **`InitializeVaultDialog`**: Pinned button rows at bottom first; error labels hidden until populated; centered over toplevel; `<Escape>` cancels.
- **`ChangePassphraseDialog`**: Pinned footer at bottom first; `status_lbl` hidden until populated; centered over toplevel; `<Escape>` cancels.
- **`PassphraseAgeWarningDialog`**: Pinned footer at bottom first; centered over toplevel; `<Escape>` cancels.
- **`DestructiveConfirmationDialog`**: Pinned footer at bottom first; `status_lbl` hidden until populated; centered over toplevel; `<Escape>` cancels.
- **`QuarantineManagerDialog`**: Pinned footer at bottom first; centered over toplevel; `<Escape>` cancels.
- **`BulkDeleteDialog`**: Pinned footer at bottom first; `status_lbl` hidden until populated; centered over toplevel; `<Escape>` cancels.
- **`VoiceProfileEnrollmentDialog`**: Pinned footer at bottom first; `dup_banner` and `quality_lbl` hidden until populated; centered over toplevel; `<Escape>` cancels.
- **`EventRosterDialog`**: Pinned footer at bottom first; centered over toplevel; `<Escape>` cancels.
- **`SessionConfigDialog`**: Pinned footer at bottom first; centered over toplevel; `<Escape>` cancels.
- **`ReidentifyDialog`**: Pinned footer at bottom first; centered over toplevel; `<Escape>` cancels.
- **`RosterDialog`**: Pinned footer outside scrollable canvas so buttons never scroll off-screen; centered over toplevel; `<Escape>` cancels.
- **`TextSizeDialog`**: Pinned footer at bottom first; resizable; centered over toplevel; `<Escape>` cancels.
- **`Select Event` Dialog (`gui/stage2_tab.py`)**: Pinned footer at bottom first; resizable; centered over toplevel; `<Escape>` cancels.
- **`Rename Speaker` Dialog (`gui/stage2_tab.py`)**: Pinned footer at bottom first; resizable; centered over toplevel; `<Escape>` cancels.
- **`TranscriptEditorDialog` (`gui/stage3_tab.py`)**: Pinned `status_frame` at bottom before `text_frame`; centered over toplevel; `<Escape>` cancels.
- **`_check_disk_conflict` (`gui/stage3_tab.py`)**: Pinned footer at bottom first; centered over toplevel; `<Escape>` cancels.
- **`_show_test_transcription_dialog` (`gui/stage3_tab.py`)**: Pinned footer at bottom first; centered over toplevel; `<Escape>` cancels.
- **`_show_models_dialog` (`gui/stage3_tab.py`)**: Pinned footer at bottom first; centered over toplevel; `<Escape>` cancels.
- **`_open_glossary_dialog` (`gui/stage3_tab.py`)**: Pinned footer at bottom first; centered over toplevel; `<Escape>` cancels.
- **`_check_placeholder_names` (`gui/stage3_tab.py`)**: Pinned footer at bottom first; centered over toplevel; `<Escape>` cancels.
- **`_open_rename_dialog` (`gui/stage3_tab.py`)**: Pinned footer at bottom first; centered over toplevel; `<Escape>` cancels.
- **`_show_about_dialog` (`gui/app.py`)**: Pinned Close button at bottom first; centered over toplevel; `<Escape>` cancels.

---

## Files Touched

- `gui/theme.py`
- `gui/vault_dialog.py`
- `gui/enrollment_dialog.py`
- `gui/event_roster_dialog.py`
- `gui/session_config_dialog.py`
- `gui/reidentify_dialog.py`
- `gui/roster_dialog.py`
- `gui/stage2_tab.py`
- `gui/stage3_tab.py`
- `gui/app.py`
- `tests/test_dialog_geometry_and_scaling.py` (new automated test)

---

## How it was Verified

1. **Light Automated Scaling & Geometry Test (`tests/test_dialog_geometry_and_scaling.py`):**
   - Instantiates every dialog with synthetic dummy data across 100%, 125%, 150%, and 175% scaling and with A+ font setting.
   - Asserts every mapped button lies strictly within the dialog's window geometry bounds (`b_bottom <= dlg_bottom + 4`).
   - Asserts `ForgotPassphraseDialog` case-insensitivity (`erase vault`, `  ERASE VAULT  `, etc.), live indicator state, and button enable/disable logic.
   - Asserts empty status strips and warning banners remain hidden on initialization.
   - Runs fast in ~7.8s without blocking or requiring human clicks.
2. **Full Fast Test Discovery:**
   - Ran all discovered test files in fast mode.
