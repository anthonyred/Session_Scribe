"""
gui/vault_dialog.py
Voice Vault Dialogs and Status Chip (Tkinter)

Provides:
- VaultStatusChip: clickable status chip displaying Lock/Unlock state and Lock button.
- UnlockVaultDialog: masked entry dialog for unlocking with progressive delay notice and TOTP prompt.
- InitializeVaultDialog: setup wizard with 6-word memorable passphrase generator and in-memory TOTP QR enrollment.
- DestructiveConfirmationDialog: 2FA re-authentication and 6-char confirmation code dialog for quarantine.
- QuarantineManagerDialog: management interface for viewing, restoring, or permanently crypto-shredding profiles.
- BulkDeleteDialog: extra-confirmation purge dialog requiring "PURGE ALL PROFILES" confirmation text.
- ensure_vault_unlocked_preflight: gate function called before runs requiring voice/identity.
"""

from __future__ import annotations

from datetime import datetime, timezone
import os
import tkinter as tk
from tkinter import ttk, messagebox
from typing import Any, Callable, Dict, List, Optional, Tuple

try:
    from PIL import Image, ImageTk
except ImportError:
    Image = None  # type: ignore
    ImageTk = None  # type: ignore

from scribe_core.voice_vault import (
    VoiceVault,
    evaluate_passphrase_strength,
    MIN_PASSPHRASE_LENGTH,
)
from scribe_core.wordlist import generate_memorable_passphrase
from scribe_core.totp_manager import TOTPManager
from scribe_core.quarantine_manager import QuarantineManager, BULK_CONFIRMATION_TEXT
from gui import theme
from gui.ui_settings import is_vault_setup_skipped, set_vault_setup_skipped


class VaultStatusChip(ttk.Frame):
    """
    Compact status chip placed in the header or toolbar.
    Shows lock state and allows one-click locking / unlocking.
    """

    def __init__(self, parent, vault: VoiceVault, on_state_changed: Optional[Callable[[], None]] = None):
        super().__init__(parent)
        self.vault = vault
        self.on_state_changed = on_state_changed

        self._btn = ttk.Button(self, text="🔒 Vault Locked", command=self._toggle_or_prompt, style="TButton")
        self._btn.pack(side="left", padx=(0, 4))
        self._btn.bind("<Button-3>", self._on_chip_right_click)

        # Register as listener for unified application-wide vault state
        self.vault.add_listener(self._on_vault_notified)
        self.bind("<Destroy>", self._on_destroy)
        self.refresh()

    def _on_vault_notified(self) -> None:
        """Called automatically whenever the vault locks, unlocks, or state changes."""
        try:
            if self.winfo_exists():
                self.refresh()
                if self.on_state_changed:
                    self.on_state_changed()
        except Exception:
            pass

    def _on_destroy(self, _event=None) -> None:
        try:
            self.vault.remove_listener(self._on_vault_notified)
        except Exception:
            pass

    def refresh(self) -> None:
        """Update chip text and style according to vault state."""
        st = self.vault.state()
        age_days = self.vault.get_passphrase_age_days() if self.vault.is_initialized() else 0
        if st == "uninitialized":
            if is_vault_setup_skipped():
                self._btn.config(text="🔒 Setup Vault (Skipped)")
            else:
                self._btn.config(text="🔒 Setup Vault")
        elif st == "locked":
            self._btn.config(text=f"🔒 Vault Locked ({age_days}d)")
        elif st == "locking_soon":
            rem = max(1, int(self.vault.get_remaining_idle_seconds() / 60.0 + 0.5))
            self._btn.config(text=f"⏳ Locking soon ({rem}m)")
        else:
            self._btn.config(text=f"🔓 Vault Unlocked ({age_days}d)")

    def _on_chip_right_click(self, event=None) -> None:
        menu = tk.Menu(self, tearoff=0)
        if not self.vault.is_initialized():
            if is_vault_setup_skipped():
                menu.add_command(label="Enable Vault Setup Prompt", command=self._unskip_vault_setup)
            else:
                menu.add_command(label="Skip Vault Setup for Now", command=self._skip_vault_setup)
            menu.add_command(label="Setup Voice Vault...", command=self._toggle_or_prompt)
        else:
            if self.vault.is_unlocked():
                menu.add_command(label="Lock Voice Vault", command=self.vault.lock)
            else:
                menu.add_command(label="Unlock Voice Vault...", command=self._toggle_or_prompt)
            menu.add_separator()
            menu.add_command(label="Change Passphrase...", command=self._open_change_passphrase)
            menu.add_command(label="Quarantined Profiles (24h Hold)...", command=self._open_quarantine_manager)
            menu.add_separator()
            menu.add_command(label="Reset / Erase Vault...", command=self._open_forgot_passphrase)

        if event:
            menu.tk_popup(event.x_root, event.y_root)

    def _open_change_passphrase(self) -> None:
        dlg = ChangePassphraseDialog(self.winfo_toplevel(), self.vault)
        self.winfo_toplevel().wait_window(dlg)
        self.refresh()

    def _open_quarantine_manager(self) -> None:
        qm = QuarantineManager(self.vault)
        dlg = QuarantineManagerDialog(self.winfo_toplevel(), qm)
        self.winfo_toplevel().wait_window(dlg)
        self.refresh()

    def _open_forgot_passphrase(self) -> None:
        def _on_erased():
            dlg = InitializeVaultDialog(self.winfo_toplevel(), self.vault)
            self.winfo_toplevel().wait_window(dlg)

        dlg = ForgotPassphraseDialog(self.winfo_toplevel(), self.vault, on_erased=_on_erased)
        self.winfo_toplevel().wait_window(dlg)
        self.refresh()

    def _skip_vault_setup(self) -> None:
        set_vault_setup_skipped(True)
        self.refresh()
        if self.on_state_changed:
            self.on_state_changed()

    def _unskip_vault_setup(self) -> None:
        set_vault_setup_skipped(False)
        self.refresh()
        if self.on_state_changed:
            self.on_state_changed()

    def _toggle_or_prompt(self) -> None:
        if not self.vault.is_initialized():
            dlg = InitializeVaultDialog(self.winfo_toplevel(), self.vault)
            self.winfo_toplevel().wait_window(dlg)
        elif self.vault.is_unlocked():
            self.vault.lock()
        else:
            dlg = UnlockVaultDialog(self.winfo_toplevel(), self.vault)
            self.winfo_toplevel().wait_window(dlg)

        self.refresh()
        if self.on_state_changed:
            self.on_state_changed()


class UnlockVaultDialog(tk.Toplevel):
    """Masked dialog to unlock the vault, supporting passphrase and TOTP."""

    def __init__(self, parent, vault: VoiceVault):
        super().__init__(parent)
        self.vault = vault
        self.success = False

        self.title("Unlock Voice Vault")
        self.minsize(380, 260)
        self.resizable(True, True)
        self.transient(parent)
        self.grab_set()

        self._build_ui()
        self.center_window()
        self.bind("<Configure>", self._on_configure)
        self.bind("<Escape>", lambda _e: self.destroy())

    def _on_configure(self, event) -> None:
        if event.widget == self:
            avail = max(180, event.width - 40)
            if hasattr(self, "_lbl_title") and self._lbl_title.winfo_exists():
                self._lbl_title.configure(wraplength=avail)
            if hasattr(self, "status_lbl") and self.status_lbl.winfo_exists():
                self.status_lbl.configure(wraplength=avail)

    def center_window(self) -> None:
        theme.center_dialog(self, self.master, width=460, height=330, min_width=380, min_height=260)

    def _build_ui(self) -> None:
        container = ttk.Frame(self, padding=16)
        container.pack(fill="both", expand=True)

        # Footer packed first at the bottom to guarantee visibility
        btn_box = ttk.Frame(container)
        btn_box.pack(fill="x", side="bottom")

        cancel_btn = ttk.Button(btn_box, text="Cancel", command=self.destroy)
        cancel_btn.pack(side="right", padx=(8, 0))

        unlock_btn = ttk.Button(btn_box, text="Unlock", command=self._on_unlock, style="Accent.TButton")
        unlock_btn.pack(side="right")

        self._lbl_title = ttk.Label(
            container,
            text="Voice Vault is locked.\nEnter credentials to access speaker identities and voice profiles:",
            wraplength=400,
        )
        self._lbl_title.pack(fill="x", pady=(0, 12))

        ttk.Label(container, text="Master Passphrase:").pack(anchor="w")
        self.pass_var = tk.StringVar()
        self.entry_pass = ttk.Entry(container, textvariable=self.pass_var, show="•", width=36)
        self.entry_pass.pack(fill="x", pady=(2, 8))
        self.entry_pass.focus_set()
        self.entry_pass.bind("<Return>", lambda e: self._on_unlock())

        # Status label (hidden until text is populated)
        self.status_lbl = ttk.Label(container, text="", foreground="#C0392B", wraplength=400)

        # "Forgot passphrase?" action link
        self.link_box = ttk.Frame(container)
        self.link_box.pack(fill="x", pady=(0, 8))
        btn_forgot = ttk.Button(
            self.link_box,
            text="Forgot passphrase?",
            command=self._on_forgot_passphrase,
        )
        btn_forgot.pack(side="left")

    def _set_status(self, text: str, fg: str = "#C0392B") -> None:
        if not text:
            self.status_lbl.config(text="")
            if self.status_lbl.winfo_ismapped():
                self.status_lbl.pack_forget()
            return
        self.status_lbl.config(text=text, foreground=fg)
        if not self.status_lbl.winfo_ismapped():
            self.status_lbl.pack(fill="x", pady=(0, 6), before=self.link_box)

    def _on_forgot_passphrase(self) -> None:
        def _after_erased():
            self.destroy()
            dlg = InitializeVaultDialog(self.master, self.vault)
            self.master.wait_window(dlg)

        ForgotPassphraseDialog(self, self.vault, on_erased=_after_erased)

    def _on_unlock(self) -> None:
        pwd = self.pass_var.get().strip()
        if not pwd:
            self._set_status("Please enter a passphrase.", "#C0392B")
            return

        self._set_status("Verifying...", "#555555")
        self.update_idletasks()

        ok, msg = self.vault.unlock(pwd)
        if ok:
            self.success = True
            age_warning = self.vault.check_passphrase_age_warning()
            self.destroy()
            if age_warning:
                PassphraseAgeWarningDialog(self.master, self.vault, age_days=age_warning["age_days"])
        else:
            self._set_status(msg, "#C0392B")
            self.pass_var.set("")
            self.entry_pass.focus_set()


class ForgotPassphraseDialog(tk.Toplevel):
    """
    Destructive recovery dialog when the master passphrase is forgotten.
    Explains plainly that there is no recovery backdoor, that continuing
    permanently erases the vault (all profiles, alias maps, and TOTP config)
    while event transcripts/manifests remain untouched, and requires typing 'ERASE VAULT'.
    """

    def __init__(self, parent, vault: VoiceVault, on_erased: Optional[Callable[[], None]] = None):
        super().__init__(parent)
        self.vault = vault
        self.on_erased = on_erased
        self.erased = False

        self.title("Forgot Passphrase — Erase Voice Vault")
        self.minsize(480, 400)
        self.resizable(True, True)
        self.transient(parent)
        self.grab_set()

        self._build_ui()
        self.center_window()
        self.bind("<Escape>", lambda _e: self.destroy())

    def center_window(self) -> None:
        theme.center_dialog(self, self.master, width=540, height=480, min_width=480, min_height=400)

    def _build_ui(self) -> None:
        container = ttk.Frame(self, padding=16)
        container.pack(fill="both", expand=True)

        # Footer packed first at the bottom to guarantee visibility
        btn_box = ttk.Frame(container)
        btn_box.pack(fill="x", side="bottom", pady=(12, 0))

        btn_cancel = ttk.Button(btn_box, text="Cancel", command=self.destroy)
        btn_cancel.pack(side="right", padx=(8, 0))

        self.btn_erase = ttk.Button(
            btn_box,
            text="Permanently Erase Vault",
            command=self._on_erase,
            style="Accent.TButton",
            state=tk.DISABLED,
        )
        self.btn_erase.pack(side="right")

        # Scrollable body frame for small screens / high scaling
        canvas = tk.Canvas(container, highlightthickness=0)
        vscroll = ttk.Scrollbar(container, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=vscroll.set)

        body = ttk.Frame(canvas)
        body_id = canvas.create_window((0, 0), window=body, anchor="nw")

        def _on_body_cfg(_e):
            canvas.configure(scrollregion=canvas.bbox("all"))

        body.bind("<Configure>", _on_body_cfg)

        def _on_canvas_cfg(e):
            canvas.itemconfig(body_id, width=e.width)
            if hasattr(self, "lbl_info") and self.lbl_info.winfo_exists():
                self.lbl_info.configure(wraplength=max(200, e.width - 10))

        canvas.bind("<Configure>", _on_canvas_cfg)

        canvas.pack(side="left", fill="both", expand=True)
        vscroll.pack(side="right", fill="y")

        ttk.Label(
            body,
            text="⚠️ Permanently Erase Voice Vault",
            font=("Segoe UI", 11, "bold"),
            foreground="#C0392B",
        ).pack(anchor="w", pady=(0, 8))

        msg = (
            "There is NO password recovery backdoor by design.\n\n"
            "If you cannot recall your passphrase, continuing will permanently erase the Voice Vault:\n"
            "  • All enrolled speaker voices and acoustic embeddings will be deleted.\n"
            "  • All event speaker alias links will be cleared.\n"
            "  • Two-factor authenticator (TOTP) pairing will be removed.\n\n"
            "Notice: Event data (audio files, transcripts, manifests, and notes) will remain untouched."
        )
        self.lbl_info = ttk.Label(body, text=msg, wraplength=480)
        self.lbl_info.pack(anchor="w", pady=(0, 10))

        ttk.Label(
            body,
            text="To confirm permanent erasure, type ERASE VAULT below:",
            font=("Segoe UI", 9, "bold"),
        ).pack(anchor="w")

        self.confirm_var = tk.StringVar()
        self.entry_confirm = ttk.Entry(body, textvariable=self.confirm_var, width=28)
        self.entry_confirm.pack(anchor="w", pady=(4, 4))
        self.entry_confirm.focus_set()
        self.entry_confirm.bind("<Return>", lambda e: self._on_enter_pressed())

        self.match_lbl = ttk.Label(body, text="Type 'ERASE VAULT' to enable button", font=("Segoe UI", 9), foreground="#7F8C8D")
        self.match_lbl.pack(anchor="w", pady=(0, 6))

        self.confirm_var.trace_add("write", self._on_confirm_change)

        self.status_lbl = ttk.Label(body, text="", foreground="#C0392B", wraplength=480)
        # Not packed initially

    def _set_status(self, text: str) -> None:
        if not text:
            self.status_lbl.config(text="")
            if self.status_lbl.winfo_ismapped():
                self.status_lbl.pack_forget()
            return
        self.status_lbl.config(text=text)
        if not self.status_lbl.winfo_ismapped():
            self.status_lbl.pack(fill="x", pady=(0, 8))

    def _on_confirm_change(self, *args) -> None:
        typed = self.confirm_var.get().strip().upper()
        if typed == "ERASE VAULT":
            self.match_lbl.config(text="✓ Phrase matches — Erasure enabled", foreground="#27AE60")
            self.btn_erase.configure(state=tk.NORMAL)
            self._set_status("")
        elif typed:
            self.match_lbl.config(text="Phrase does not match 'ERASE VAULT'", foreground="#D35400")
            self.btn_erase.configure(state=tk.DISABLED)
        else:
            self.match_lbl.config(text="Type 'ERASE VAULT' to enable button", foreground="#7F8C8D")
            self.btn_erase.configure(state=tk.DISABLED)

    def _on_enter_pressed(self) -> None:
        if str(self.btn_erase["state"]) != tk.DISABLED:
            self._on_erase()

    def _on_erase(self) -> None:
        typed = self.confirm_var.get().strip().upper()
        if typed != "ERASE VAULT":
            self._set_status("Must type 'ERASE VAULT' to proceed.")
            return

        ok, msg = self.vault.erase_vault()
        if ok:
            self.erased = True
            messagebox.showinfo(
                "Vault Erased",
                "Voice Vault has been completely erased.\n\n"
                "All enrolled voices and alias maps have been removed.\n"
                "You can now set up a new passphrase and 2FA.",
                parent=self,
            )
            if self.on_erased:
                self.on_erased()
            self.destroy()
        else:
            self._set_status(f"Failed to erase vault: {msg}")


class PassphraseAgeWarningDialog(tk.Toplevel):
    """
    Non-blocking reminder dialog shown when master passphrase is >= 365 days old.
    Offers 'Change now' (opens ChangePassphraseDialog) and 'Remind me in 7 days'.
    Never forces a change.
    """

    def __init__(self, parent, vault: VoiceVault, age_days: int):
        super().__init__(parent)
        self.vault = vault
        self.age_days = age_days

        self.title("Passphrase Age Reminder")
        self.minsize(420, 210)
        self.resizable(True, True)
        self.transient(parent)

        self._build_ui()
        self.center_window()
        self.bind("<Escape>", lambda _e: self.destroy())

    def center_window(self) -> None:
        theme.center_dialog(self, self.master, width=500, height=270, min_width=420, min_height=210)

    def _build_ui(self) -> None:
        container = ttk.Frame(self, padding=16)
        container.pack(fill="both", expand=True)

        btn_box = ttk.Frame(container)
        btn_box.pack(fill="x", side="bottom")

        ttk.Button(btn_box, text="Dismiss", command=self.destroy).pack(side="left")
        ttk.Button(btn_box, text="Remind me in 7 days", command=self._on_snooze).pack(side="left", padx=8)
        ttk.Button(btn_box, text="Change now", command=self._on_change_now, style="Accent.TButton").pack(side="right")

        ttk.Label(
            container,
            text="🛡️ Master Passphrase Age Reminder",
            font=("Segoe UI", 11, "bold"),
            foreground="#2C3E50",
        ).pack(anchor="w", pady=(0, 8))

        msg = (
            f"Your master passphrase was configured {self.age_days} days ago.\n\n"
            "For security best practices, we recommend updating your passphrase periodically.\n"
            "You can change it now or snooze this reminder."
        )
        ttk.Label(container, text=msg, wraplength=440).pack(anchor="w", pady=(0, 14))

    def _on_snooze(self) -> None:
        self.vault.snooze_passphrase_warning(7)
        self.destroy()

    def _on_change_now(self) -> None:
        self.destroy()
        dlg = ChangePassphraseDialog(self.master, self.vault)
        self.master.wait_window(dlg)


class ChangePassphraseDialog(tk.Toplevel):
    """
    Dialog to change the master passphrase of Voice Vault.
    Re-encrypts the master key envelope under the new passphrase.
    Requires old passphrase, new passphrase, and live TOTP 2FA code (if enrolled).
    """

    def __init__(self, parent, vault: VoiceVault):
        super().__init__(parent)
        self.vault = vault
        self.success = False

        self.title("Change Voice Vault Passphrase")
        self.minsize(460, 440)
        self.resizable(True, True)
        self.transient(parent)
        self.grab_set()

        self._build_ui()
        self.center_window()
        self.bind("<Escape>", lambda _e: self.destroy())

    def center_window(self) -> None:
        theme.center_dialog(self, self.master, width=540, height=540, min_width=460, min_height=440)

    def _build_ui(self) -> None:
        container = ttk.Frame(self, padding=16)
        container.pack(fill="both", expand=True)

        btn_box = ttk.Frame(container)
        btn_box.pack(fill="x", side="bottom")

        ttk.Button(btn_box, text="Cancel", command=self.destroy).pack(side="right", padx=(8, 0))
        ttk.Button(
            btn_box,
            text="Update Passphrase",
            command=self._on_submit,
            style="Accent.TButton",
        ).pack(side="right")

        ttk.Label(
            container,
            text="Change Master Passphrase",
            font=("Segoe UI", 11, "bold"),
            foreground="#2C3E50",
        ).pack(anchor="w", pady=(0, 4))

        ttk.Label(
            container,
            text="Important: Change the passphrase immediately if you think it was exposed.\nRe-wrapping the master key protects all profiles against offline dictionary attacks.",
            foreground="#555555",
            wraplength=480,
        ).pack(anchor="w", pady=(0, 10))

        ttk.Label(container, text="Current Passphrase:").pack(anchor="w")
        self.old_pass_var = tk.StringVar()
        self.entry_old = ttk.Entry(container, textvariable=self.old_pass_var, show="•")
        self.entry_old.pack(fill="x", pady=(2, 8))
        self.entry_old.focus_set()

        gen_box = ttk.Frame(container)
        gen_box.pack(fill="x", pady=(0, 6))
        ttk.Label(gen_box, text="New Passphrase:").pack(side="left")
        ttk.Button(
            gen_box,
            text="🎲 6-Word Phrase",
            command=self._generate_new_passphrase,
        ).pack(side="right")

        self.new_pass_var = tk.StringVar()
        self.new_pass_var.trace_add("write", self._on_new_pass_change)
        self.entry_new = ttk.Entry(container, textvariable=self.new_pass_var, show="•")
        self.entry_new.pack(fill="x", pady=(2, 4))

        self.strength_lbl = ttk.Label(container, text="Strength: Empty", font=("Segoe UI", 9))
        self.strength_lbl.pack(anchor="w", pady=(0, 6))

        ttk.Label(container, text="Confirm New Passphrase:").pack(anchor="w")
        self.confirm_pass_var = tk.StringVar()
        self.entry_confirm = ttk.Entry(container, textvariable=self.confirm_pass_var, show="•")
        self.entry_confirm.pack(fill="x", pady=(2, 8))

        # Show/Hide Toggle
        self.show_pass_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            container,
            text="Show Passphrases",
            variable=self.show_pass_var,
            command=self._on_toggle_show,
        ).pack(anchor="w", pady=(0, 8))

        self.has_totp = self.vault.totp_manager.is_enrolled()
        if self.has_totp:
            ttk.Label(container, text="Live TOTP 6-Digit Code (from authenticator app):").pack(anchor="w")
            self.totp_var = tk.StringVar()
            self.entry_totp = ttk.Entry(container, textvariable=self.totp_var, width=16)
            self.entry_totp.pack(anchor="w", pady=(2, 8))
        else:
            self.totp_var = tk.StringVar(value="")

        self.status_lbl = ttk.Label(container, text="", foreground="#C0392B", wraplength=480)
        # Not packed initially

    def _set_status(self, text: str, fg: str = "#C0392B") -> None:
        if not text:
            self.status_lbl.config(text="")
            if self.status_lbl.winfo_ismapped():
                self.status_lbl.pack_forget()
            return
        self.status_lbl.config(text=text, foreground=fg)
        if not self.status_lbl.winfo_ismapped():
            self.status_lbl.pack(fill="x", pady=(0, 10))

    def _generate_new_passphrase(self) -> None:
        phrase = generate_memorable_passphrase(6)
        self.new_pass_var.set(phrase)
        self.confirm_pass_var.set(phrase)
        self.show_pass_var.set(True)
        self._on_toggle_show()

    def _on_toggle_show(self) -> None:
        char = "" if self.show_pass_var.get() else "•"
        self.entry_old.configure(show=char)
        self.entry_new.configure(show=char)
        self.entry_confirm.configure(show=char)

    def _on_new_pass_change(self, *args) -> None:
        pwd = self.new_pass_var.get()
        info = evaluate_passphrase_strength(pwd)
        score = info.get("score", 0)
        label = info.get("label", "Weak")
        words = info.get("word_count", 0)

        color = "#888888"
        if score < 35:
            color = "#C0392B"
        elif score < 60:
            color = "#D35400"
        elif score < 85:
            color = "#27AE60"
        else:
            color = "#16A085"

        txt = f"Strength: {label} ({words} words)"
        if info.get("feedback"):
            txt += f" — {info['feedback'][0]}"
        self.strength_lbl.config(text=txt, foreground=color)

    def _on_submit(self) -> None:
        old_p = self.old_pass_var.get().strip()
        new_p = self.new_pass_var.get().strip()
        conf_p = self.confirm_pass_var.get().strip()
        totp_code = self.totp_var.get().strip() if self.has_totp else None

        if not old_p:
            self._set_status("Please enter your current passphrase.")
            return

        if not new_p:
            self._set_status("Please enter a new passphrase.")
            return

        if new_p != conf_p:
            self._set_status("New passphrases do not match.")
            return

        if self.has_totp and (not totp_code or len(totp_code) != 6 or not totp_code.isdigit()):
            self._set_status("Please enter the 6-digit numeric TOTP code from your authenticator.")
            return

        self._set_status("Deriving Argon2id key and re-wrapping master key...", foreground="#555555")
        self.update_idletasks()

        ok, msg = self.vault.change_passphrase(
            old_passphrase=old_p,
            new_passphrase=new_p,
            totp_code=totp_code,
        )

        if ok:
            self.success = True
            messagebox.showinfo(
                "Passphrase Updated",
                "Master passphrase changed successfully.\n\n"
                "The passphrase age timer has been reset.",
                parent=self,
            )
            self.destroy()
        else:
            self._set_status(msg, foreground="#C0392B")


class InitializeVaultDialog(tk.Toplevel):
    """
    Setup wizard to create a new passphrase for the Voice Vault
    with random 6-word passphrase generator and in-memory TOTP 2FA enrollment.
    """

    def __init__(self, parent, vault: VoiceVault):
        super().__init__(parent)
        self.vault = vault
        self.success = False

        self.title("Set Up Voice Vault (Two-Factor Protection)")
        self.minsize(460, 480)
        self.resizable(True, True)
        self.transient(parent)
        self.grab_set()

        self._totp_secret = self.vault.totp_manager.generate_new_secret()
        self._qr_photo = None

        self._build_ui()
        self.center_window()
        self.bind("<Escape>", lambda _e: self.destroy())
        # Default to generated 6-word passphrase immediately
        self._on_generate_passphrase(6)

    def center_window(self) -> None:
        theme.center_dialog(self, self.master, width=540, height=580, min_width=460, min_height=460)

    def _build_ui(self) -> None:
        self.container = ttk.Frame(self, padding=16)
        self.container.pack(fill="both", expand=True)

        # Step 1: Passphrase Frame
        self.frame_step1 = ttk.Frame(self.container)
        self.frame_step1.pack(fill="both", expand=True)

        btn_box1 = ttk.Frame(self.frame_step1)
        btn_box1.pack(fill="x", side="bottom")
        ttk.Button(btn_box1, text="Skip for now", command=self._on_skip_for_now).pack(side="left")
        ttk.Button(btn_box1, text="Cancel", command=self.destroy).pack(side="right", padx=(8, 0))
        ttk.Button(
            btn_box1,
            text="Next: Setup 2FA (TOTP) ➔",
            command=self._on_next_to_step2,
            style="Accent.TButton",
        ).pack(side="right")

        lbl_intro = ttk.Label(
            self.frame_step1,
            text="Step 1 of 2: Create Master Passphrase\nRequires 5-6 random words or a high-entropy string. There is no reset if lost.",
            wraplength=460,
            font=("Segoe UI", 9, "bold"),
        )
        lbl_intro.pack(fill="x", pady=(0, 10))

        # Generator buttons: offer 6 or 7 words
        gen_box = ttk.Frame(self.frame_step1)
        gen_box.pack(fill="x", pady=(0, 8))
        ttk.Button(
            gen_box,
            text="🎲 6-Word Phrase",
            command=lambda: self._on_generate_passphrase(6),
        ).pack(side="left", padx=(0, 6))
        ttk.Button(
            gen_box,
            text="🎲 7-Word Phrase (Extra Strong)",
            command=lambda: self._on_generate_passphrase(7),
        ).pack(side="left")

        ttk.Label(self.frame_step1, text="Master Passphrase:").pack(anchor="w")
        self.pass1_var = tk.StringVar()
        self.pass1_var.trace_add("write", self._on_pass_change)
        self.entry1 = ttk.Entry(self.frame_step1, textvariable=self.pass1_var, show="•")
        self.entry1.pack(fill="x", pady=(2, 6))

        # Show/Hide Toggle
        self.show_pass_var = tk.BooleanVar(value=False)
        chk_show = ttk.Checkbutton(
            self.frame_step1,
            text="Show Passphrase",
            variable=self.show_pass_var,
            command=self._on_toggle_show,
        )
        chk_show.pack(anchor="w", pady=(0, 6))

        # Strength meter
        self.strength_lbl = ttk.Label(self.frame_step1, text="Strength: Empty", font=("Segoe UI", 9))
        self.strength_lbl.pack(anchor="w", pady=(0, 8))

        ttk.Label(self.frame_step1, text="Confirm Passphrase:").pack(anchor="w")
        self.pass2_var = tk.StringVar()
        self.entry2 = ttk.Entry(self.frame_step1, textvariable=self.pass2_var, show="•")
        self.entry2.pack(fill="x", pady=(2, 8))

        self.error_lbl1 = ttk.Label(self.frame_step1, text="", foreground="#C0392B", wraplength=460)
        # Not packed initially

        # Step 2: TOTP Frame (Initially hidden)
        self.frame_step2 = ttk.Frame(self.container)

        btn_box2 = ttk.Frame(self.frame_step2)
        btn_box2.pack(fill="x", side="bottom")
        ttk.Button(btn_box2, text="Cancel", command=self.destroy).pack(side="right", padx=(8, 0))
        ttk.Button(btn_box2, text="Finalize & Create Vault", command=self._on_finalize, style="Accent.TButton").pack(side="right")
        ttk.Button(btn_box2, text="⬅ Back", command=self._on_back_to_step1).pack(side="right", padx=(0, 8))

        lbl_step2 = ttk.Label(
            self.frame_step2,
            text="Step 2 of 2: Set Up Two-Factor Authentication (TOTP)\nScan the QR code with your authenticator app (Google Authenticator, Authy, etc.):",
            wraplength=460,
            font=("Segoe UI", 9, "bold"),
        )
        lbl_step2.pack(fill="x", pady=(0, 8))

        # In-memory QR code preview
        self.qr_label = ttk.Label(self.frame_step2)
        self.qr_label.pack(pady=(0, 8))

        # Base32 Secret reveal button (never stored to disk or copied to clipboard by default)
        self.sec_box = ttk.Frame(self.frame_step2)
        self.sec_box.pack(fill="x", pady=(0, 8))
        self.btn_reveal_key = ttk.Button(
            self.sec_box,
            text="🔑 Reveal TOTP Setup Key (for offline storage)",
            command=self._on_reveal_setup_key,
        )
        self.btn_reveal_key.pack(anchor="w")
        self.sec_lbl = ttk.Label(self.sec_box, text="", font=("Consolas", 10, "bold"), wraplength=440)
        self.sec_lbl.pack(anchor="w", pady=(4, 0))

        ttk.Label(self.frame_step2, text="Enter 6-digit verification code from your app:").pack(anchor="w")
        self.totp_code_var = tk.StringVar()
        self.entry_totp_code = ttk.Entry(self.frame_step2, textvariable=self.totp_code_var, width=16)
        self.entry_totp_code.pack(anchor="w", pady=(2, 8))

        self.error_lbl2 = ttk.Label(self.frame_step2, text="", foreground="#C0392B", wraplength=460)
        # Not packed initially

    def _set_error1(self, text: str) -> None:
        if not text:
            self.error_lbl1.config(text="")
            if self.error_lbl1.winfo_ismapped():
                self.error_lbl1.pack_forget()
            return
        self.error_lbl1.config(text=text)
        if not self.error_lbl1.winfo_ismapped():
            self.error_lbl1.pack(fill="x", pady=(0, 10))

    def _set_error2(self, text: str, fg: str = "#C0392B") -> None:
        if not text:
            self.error_lbl2.config(text="")
            if self.error_lbl2.winfo_ismapped():
                self.error_lbl2.pack_forget()
            return
        self.error_lbl2.config(text=text, foreground=fg)
        if not self.error_lbl2.winfo_ismapped():
            self.error_lbl2.pack(fill="x", pady=(0, 10))

    def _on_reveal_setup_key(self) -> None:
        self.sec_lbl.config(
            text=f"Setup Key: {self._totp_secret}\n(Store this offline. The QR code is never saved to disk.)",
            foreground="#27AE60",
        )
        self.btn_reveal_key.pack_forget()

    def _on_generate_passphrase(self, word_count: int = 6) -> None:
        phrase = generate_memorable_passphrase(word_count)
        self.pass1_var.set(phrase)
        self.pass2_var.set(phrase)
        self.show_pass_var.set(True)
        self._on_toggle_show()

    def _on_toggle_show(self) -> None:
        show_char = "" if self.show_pass_var.get() else "•"
        self.entry1.configure(show=show_char)
        self.entry2.configure(show=show_char)

    def _on_pass_change(self, *args) -> None:
        pwd = self.pass1_var.get()
        info = evaluate_passphrase_strength(pwd)
        score = info.get("score", 0)
        label = info.get("label", "Weak")
        words = info.get("word_count", 0)

        color = "#888888"
        if score < 35:
            color = "#C0392B"
        elif score < 60:
            color = "#D35400"
        elif score < 85:
            color = "#27AE60"
        else:
            color = "#16A085"

        txt = f"Strength: {label} ({words} words)"
        if info.get("feedback"):
            txt += f" — {info['feedback'][0]}"
        self.strength_lbl.config(text=txt, foreground=color)

    def _on_next_to_step2(self) -> None:
        p1 = self.pass1_var.get().strip()
        p2 = self.pass2_var.get().strip()

        info = evaluate_passphrase_strength(p1)
        if not info["valid"]:
            msg = "Passphrase does not meet requirements. " + " ".join(info.get("feedback", []))
            self._set_error1(msg)
            return

        if p1 != p2:
            self._set_error1("Passphrases do not match.")
            return

        self._set_error1("")

        # Render QR code in-memory
        try:
            uri = self.vault.totp_manager.get_provisioning_uri(self._totp_secret)
            pil_img = self.vault.totp_manager.get_qr_image_in_memory(uri)
            if ImageTk and Image:
                # Resize slightly for crisp display
                pil_img = pil_img.resize((160, 160), Image.Resampling.NEAREST)
                self._qr_photo = ImageTk.PhotoImage(pil_img)
                self.qr_label.configure(image=self._qr_photo)
            else:
                self.qr_label.configure(text="[QR library missing: Enter secret key manually above]")
        except Exception as e:
            self.qr_label.configure(text=f"[QR Error: {e}]")

        self.frame_step1.pack_forget()
        self.frame_step2.pack(fill="both", expand=True)
        self.entry_totp_code.focus_set()

    def _on_skip_for_now(self) -> None:
        set_vault_setup_skipped(True)
        self.destroy()

    def _on_back_to_step1(self) -> None:
        self.frame_step2.pack_forget()
        self.frame_step1.pack(fill="both", expand=True)
        self.entry1.focus_set()

    def _on_finalize(self) -> None:
        p1 = self.pass1_var.get().strip()
        code = self.totp_code_var.get().strip()

        if not code or len(code) != 6 or not code.isdigit():
            self._set_error2("Enter the 6-digit numeric verification code from your authenticator app.")
            return

        self._set_error2("Deriving Argon2id keys and enrolling TOTP 2FA...", foreground="#555555")
        self.update_idletasks()

        ok, msg = self.vault.initialize(
            passphrase=p1,
            totp_secret=self._totp_secret,
            totp_code=code,
        )

        if ok:
            self.success = True
            set_vault_setup_skipped(False)
            messagebox.showinfo(
                "Voice Vault Created",
                "Voice vault initialized successfully with TOTP two-factor protection.\n\n"
                "Important: There is no recovery backdoor by design. Retain your passphrase and authenticator app.",
                parent=self,
            )
            self.destroy()
        else:
            self._set_error2(msg, foreground="#C0392B")


class DestructiveConfirmationDialog(tk.Toplevel):
    """
    Step 2 confirmation dialog for profile quarantine/deletion:
    Displays person's resolved name, samples to be removed, warning notice,
    and requires entering passphrase, TOTP code, and a short typed confirmation code.
    """

    def __init__(
        self,
        parent,
        spk_id: str,
        name: str,
        sample_count: int,
        confirmation_code: str,
        quarantine_mgr: QuarantineManager,
        on_success: Optional[Callable[[], None]] = None,
    ):
        super().__init__(parent)
        self.spk_id = spk_id
        self.name = name
        self.sample_count = sample_count
        self.confirmation_code = confirmation_code
        self.quarantine_mgr = quarantine_mgr
        self.on_success = on_success
        self.success = False

        self.title("Confirm Destructive Action")
        self.minsize(440, 380)
        self.resizable(True, True)
        self.transient(parent)
        self.grab_set()

        self._build_ui()
        self.center_window()
        self.bind("<Escape>", lambda _e: self.destroy())

    def center_window(self) -> None:
        theme.center_dialog(self, self.master, width=500, height=440, min_width=440, min_height=380)

    def _build_ui(self) -> None:
        container = ttk.Frame(self, padding=16)
        container.pack(fill="both", expand=True)

        btn_box = ttk.Frame(container)
        btn_box.pack(fill="x", side="bottom")

        ttk.Button(btn_box, text="Cancel", command=self.destroy).pack(side="right", padx=(8, 0))
        ttk.Button(
            btn_box,
            text="Quarantine Profile",
            command=self._on_confirm,
            style="Accent.TButton",
        ).pack(side="right")

        ttk.Label(
            container,
            text=f"⚠️ Move Profile to 24-Hour Quarantine",
            font=("Segoe UI", 11, "bold"),
            foreground="#C0392B",
        ).pack(anchor="w", pady=(0, 8))

        info_box = ttk.LabelFrame(container, text="Target Profile", padding=8)
        info_box.pack(fill="x", pady=(0, 10))

        ttk.Label(info_box, text=f"Speaker Name: {self.name}", font=("Segoe UI", 9, "bold")).pack(anchor="w")
        ttk.Label(info_box, text=f"Anonymous ID: {self.spk_id}").pack(anchor="w")
        ttk.Label(info_box, text=f"Audio Samples to be purged: {self.sample_count} clips").pack(anchor="w")
        ttk.Label(
            info_box,
            text="Notice: Profile will be quarantined for 24 hours before permanent crypto-shredding.",
            foreground="#555555",
        ).pack(anchor="w", pady=(4, 0))

        ttk.Label(container, text="Master Passphrase:").pack(anchor="w")
        self.pass_var = tk.StringVar()
        self.entry_pass = ttk.Entry(container, textvariable=self.pass_var, show="•")
        self.entry_pass.pack(fill="x", pady=(2, 6))

        ttk.Label(container, text="Live TOTP 6-Digit Code:").pack(anchor="w")
        self.totp_var = tk.StringVar()
        self.entry_totp = ttk.Entry(container, textvariable=self.totp_var, width=16)
        self.entry_totp.pack(anchor="w", pady=(2, 6))

        code_lbl = ttk.Label(
            container,
            text=f"Type confirmation code: {self.confirmation_code}",
            font=("Segoe UI", 9, "bold"),
        )
        code_lbl.pack(anchor="w", pady=(4, 2))

        self.code_var = tk.StringVar()
        self.entry_code = ttk.Entry(container, textvariable=self.code_var, width=16)
        self.entry_code.pack(anchor="w", pady=(2, 8))

        self.status_lbl = ttk.Label(container, text="", foreground="#C0392B", wraplength=440)
        # Not packed initially

    def _set_status(self, text: str) -> None:
        if not text:
            self.status_lbl.config(text="")
            if self.status_lbl.winfo_ismapped():
                self.status_lbl.pack_forget()
            return
        self.status_lbl.config(text=text)
        if not self.status_lbl.winfo_ismapped():
            self.status_lbl.pack(fill="x", pady=(0, 8))

    def _on_confirm(self) -> None:
        entered_code = self.code_var.get().strip().upper()
        if entered_code != self.confirmation_code:
            self._set_status(f"Confirmation code must match '{self.confirmation_code}'.")
            return

        passphrase = self.pass_var.get().strip()
        totp = self.totp_var.get().strip()

        ok, msg = self.quarantine_mgr.move_to_quarantine(
            spk_id=self.spk_id,
            passphrase=passphrase,
            totp_code=totp,
            expected_code=self.confirmation_code,
            entered_code=entered_code,
        )

        if ok:
            self.success = True
            messagebox.showinfo("Profile Quarantined", msg, parent=self)
            if self.on_success:
                self.on_success()
            self.destroy()
        else:
            self._set_status(msg)


class QuarantineManagerDialog(tk.Toplevel):
    """
    Management dialog showing quarantined profiles, countdown timers,
    and options to Restore or Crypto-Shred after 24 hours.
    """

    def __init__(self, parent, quarantine_mgr: QuarantineManager, on_changed: Optional[Callable[[], None]] = None):
        super().__init__(parent)
        self.quarantine_mgr = quarantine_mgr
        self.on_changed = on_changed

        self.title("Quarantined Profiles (24-Hour Hold)")
        self.minsize(540, 320)
        self.resizable(True, True)
        self.transient(parent)
        self.grab_set()

        self._build_ui()
        self.center_window()
        self.bind("<Escape>", lambda _e: self.destroy())
        self._refresh_list()

    def center_window(self) -> None:
        theme.center_dialog(self, self.master, width=660, height=440, min_width=540, min_height=320)

    def _build_ui(self) -> None:
        container = ttk.Frame(self, padding=16)
        container.pack(fill="both", expand=True)

        btn_row = ttk.Frame(container)
        btn_row.pack(fill="x", side="bottom")

        ttk.Button(btn_row, text="Restore Profile", command=self._on_restore).pack(side="left", padx=(0, 8))
        ttk.Button(btn_row, text="Crypto-Shred Permanently", command=self._on_shred).pack(side="left")
        ttk.Button(btn_row, text="Close", command=self.destroy).pack(side="right")

        ttk.Label(
            container,
            text="Quarantined Profiles (24-Hour Safety Hold)",
            font=("Segoe UI", 11, "bold"),
        ).pack(anchor="w", pady=(0, 6))

        # Treeview table
        columns = ("name", "spk_id", "hours_left", "status")
        self.tree = ttk.Treeview(container, columns=columns, show="headings", height=8)
        self.tree.heading("name", text="Speaker Name")
        self.tree.heading("spk_id", text="Anonymous ID")
        self.tree.heading("hours_left", text="Hours Left")
        self.tree.heading("status", text="Action Status")

        self.tree.column("name", width=140)
        self.tree.column("spk_id", width=180)
        self.tree.column("hours_left", width=90, anchor="center")
        self.tree.column("status", width=120, anchor="center")
        self.tree.pack(fill="both", expand=True, pady=(0, 10))

    def _refresh_list(self) -> None:
        for item in self.tree.get_children():
            self.tree.delete(item)
        items = self.quarantine_mgr.list_quarantined_profiles()
        for it in items:
            status_text = "Eligible to Shred" if it["can_shred"] else f"{it['hours_remaining']}h hold"
            self.tree.insert(
                "",
                "end",
                iid=it["spk_id"],
                values=(it["name"], it["spk_id"], f"{it['hours_remaining']}h", status_text),
            )

    def _get_selected_spk_id(self) -> Optional[str]:
        sel = self.tree.selection()
        if not sel:
            messagebox.showwarning("Selection Required", "Please select a quarantined profile.", parent=self)
            return None
        return sel[0]

    def _on_restore(self) -> None:
        spk_id = self._get_selected_spk_id()
        if not spk_id:
            return

        dlg = UnlockVaultDialog(self, self.quarantine_mgr.vault)
        self.wait_window(dlg)
        if not dlg.success:
            return

        passphrase = dlg.pass_var.get()
        totp = dlg.totp_var.get()

        ok, msg = self.quarantine_mgr.cancel_quarantine(spk_id, passphrase, totp)
        if ok:
            messagebox.showinfo("Profile Restored", msg, parent=self)
            self._refresh_list()
            if self.on_changed:
                self.on_changed()
        else:
            messagebox.showerror("Restore Failed", msg, parent=self)

    def _on_shred(self) -> None:
        spk_id = self._get_selected_spk_id()
        if not spk_id:
            return

        dlg = UnlockVaultDialog(self, self.quarantine_mgr.vault)
        self.wait_window(dlg)
        if not dlg.success:
            return

        passphrase = dlg.pass_var.get()
        totp = dlg.totp_var.get()

        ok, msg = self.quarantine_mgr.crypto_shred(spk_id, passphrase, totp)
        if ok:
            messagebox.showinfo("Crypto-Shred Complete", msg, parent=self)
            self._refresh_list()
            if self.on_changed:
                self.on_changed()
        else:
            messagebox.showerror("Shred Refused", msg, parent=self)


class BulkDeleteDialog(tk.Toplevel):
    """
    Confirmation dialog for bulk profile deletion requiring
    typing the exact phrase 'PURGE ALL PROFILES'.
    """

    def __init__(
        self,
        parent,
        spk_ids: List[str],
        quarantine_mgr: QuarantineManager,
        on_success: Optional[Callable[[], None]] = None,
    ):
        super().__init__(parent)
        self.spk_ids = spk_ids
        self.quarantine_mgr = quarantine_mgr
        self.on_success = on_success
        self.success = False

        self.title("Confirm Bulk Quarantine Purge")
        self.minsize(420, 300)
        self.resizable(True, True)
        self.transient(parent)
        self.grab_set()

        self._build_ui()
        self.center_window()
        self.bind("<Escape>", lambda _e: self.destroy())

    def center_window(self) -> None:
        theme.center_dialog(self, self.master, width=500, height=380, min_width=420, min_height=300)

    def _build_ui(self) -> None:
        container = ttk.Frame(self, padding=16)
        container.pack(fill="both", expand=True)

        btn_box = ttk.Frame(container)
        btn_box.pack(fill="x", side="bottom")

        ttk.Button(btn_box, text="Cancel", command=self.destroy).pack(side="right", padx=(8, 0))
        ttk.Button(btn_box, text="Confirm Bulk Quarantine", command=self._on_confirm, style="Accent.TButton").pack(side="right")

        ttk.Label(
            container,
            text=f"⚠️ Bulk Delete {len(self.spk_ids)} Voice Profiles",
            font=("Segoe UI", 11, "bold"),
            foreground="#C0392B",
        ).pack(anchor="w", pady=(0, 8))

        ttk.Label(
            container,
            text=f"To place all {len(self.spk_ids)} profiles into 24-hour quarantine, type exactly:\n{BULK_CONFIRMATION_TEXT}",
            wraplength=440,
        ).pack(anchor="w", pady=(0, 8))

        ttk.Label(container, text="Master Passphrase:").pack(anchor="w")
        self.pass_var = tk.StringVar()
        self.entry_pass = ttk.Entry(container, textvariable=self.pass_var, show="•")
        self.entry_pass.pack(fill="x", pady=(2, 6))

        ttk.Label(container, text="Live TOTP 6-Digit Code:").pack(anchor="w")
        self.totp_var = tk.StringVar()
        self.entry_totp = ttk.Entry(container, textvariable=self.totp_var, width=16)
        self.entry_totp.pack(anchor="w", pady=(2, 6))

        ttk.Label(container, text=f"Confirmation Phrase:").pack(anchor="w")
        self.phrase_var = tk.StringVar()
        self.entry_phrase = ttk.Entry(container, textvariable=self.phrase_var)
        self.entry_phrase.pack(fill="x", pady=(2, 8))

        self.status_lbl = ttk.Label(container, text="", foreground="#C0392B", wraplength=440)
        # Not packed initially

    def _set_status(self, text: str) -> None:
        if not text:
            self.status_lbl.config(text="")
            if self.status_lbl.winfo_ismapped():
                self.status_lbl.pack_forget()
            return
        self.status_lbl.config(text=text)
        if not self.status_lbl.winfo_ismapped():
            self.status_lbl.pack(fill="x", pady=(0, 8))

    def _on_confirm(self) -> None:
        entered_phrase = self.phrase_var.get().strip()
        if entered_phrase != BULK_CONFIRMATION_TEXT:
            self._set_status(f"Must type exactly '{BULK_CONFIRMATION_TEXT}'.")
            return

        passphrase = self.pass_var.get().strip()
        totp = self.totp_var.get().strip()

        ok, msg = self.quarantine_mgr.bulk_quarantine(
            spk_ids=self.spk_ids,
            passphrase=passphrase,
            totp_code=totp,
            confirmation_phrase=entered_phrase,
        )

        if ok:
            self.success = True
            messagebox.showinfo("Bulk Quarantine Complete", msg, parent=self)
            if self.on_success:
                self.on_success()
            self.destroy()
        else:
            self._set_status(msg)


def ensure_vault_unlocked_preflight(parent, vault: VoiceVault) -> bool:
    """
    Preflight gate called before any run needing speaker names or voice matching.
    Never prompts mid-run.
    Returns True if unlocked, False if user cancelled or failed.
    """
    if vault.is_unlocked():
        return True

    if not vault.is_initialized():
        dlg = InitializeVaultDialog(parent, vault)
        parent.wait_window(dlg)
        return vault.is_unlocked()

    dlg = UnlockVaultDialog(parent, vault)
    parent.wait_window(dlg)
    return vault.is_unlocked()
