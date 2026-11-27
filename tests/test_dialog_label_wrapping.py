"""
Session Scribe — Tests for Part B:
Speaker Roster Dialog Dynamic Wraplength, Dialog Audits, and Vault Chip State Unification.
"""

import os
import shutil
import sys
import tempfile
import unittest
import tkinter as tk
from tkinter import ttk

# Ensure repo root is on sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from scribe_core.voice_vault import VoiceVault
from gui.vault_dialog import VaultStatusChip, UnlockVaultDialog, InitializeVaultDialog
from gui.roster_dialog import RosterDialog
from gui import theme


class TestPartBDialogsAndVaultUnification(unittest.TestCase):
    """Test suite for Part B requirements."""

    @classmethod
    def setUpClass(cls):
        try:
            cls.root = tk.Tk()
            cls.root.withdraw()
            theme.make_dpi_aware()
        except tk.TclError:
            cls.root = None

    @classmethod
    def tearDownClass(cls):
        if cls.root is not None:
            cls.root.destroy()

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_vault_chip_state_unification_multi_chip(self):
        """All vault chips listening to a VoiceVault must update together on lock/unlock."""
        if self.root is None:
            self.skipTest("No Tk display available")

        keys_dir = os.path.join(self.temp_dir, "keys")
        vault = VoiceVault(keys_dir=keys_dir)
        test_passphrase = "hazard galaxy pebble timber canyon velvet"
        ok, _ = vault.initialize(test_passphrase)
        self.assertTrue(ok)
        self.assertTrue(vault.is_unlocked())

        # Create two separate chips listening to the same vault (like header chip and Stage 3 chip)
        chip1 = VaultStatusChip(self.root, vault)
        chip2 = VaultStatusChip(self.root, vault)

        self.assertIn("Unlocked", chip1._btn.cget("text"))
        self.assertIn("Unlocked", chip2._btn.cget("text"))

        # Lock the vault
        vault.lock()
        self.assertFalse(vault.is_unlocked())

        # Both chips must now show Locked automatically
        self.assertIn("Locked", chip1._btn.cget("text"))
        self.assertIn("Locked", chip2._btn.cget("text"))

        # Unlock the vault
        ok, _ = vault.unlock(test_passphrase)
        self.assertTrue(ok)
        self.assertTrue(vault.is_unlocked())

        # Both chips must show Unlocked automatically
        self.assertIn("Unlocked", chip1._btn.cget("text"))
        self.assertIn("Unlocked", chip2._btn.cget("text"))

        chip1.destroy()
        chip2.destroy()

    def test_message_bar_dynamic_wrapping_on_configure(self):
        """MessageBar must automatically adjust wraplength when its width changes."""
        if self.root is None:
            self.skipTest("No Tk display available")

        top = tk.Toplevel(self.root)
        top.geometry("500x200")
        bar = theme.MessageBar(
            top,
            text="Very long biometric privacy notice and data sovereignty guarantee text that must wrap cleanly.",
        )
        bar.pack(fill=tk.X, expand=True)
        top.update()

        lbl_wrap_500 = int(str(bar._lbl.cget("wraplength")))
        self.assertGreater(lbl_wrap_500, 0)
        self.assertLessEqual(lbl_wrap_500, 500)
        self.assertGreaterEqual(lbl_wrap_500, 400)

        # Resize to 700px width
        top.geometry("700x200")
        top.update()
        lbl_wrap_700 = int(str(bar._lbl.cget("wraplength")))
        self.assertGreater(lbl_wrap_700, lbl_wrap_500)
        self.assertLessEqual(lbl_wrap_700, 700)
        self.assertGreaterEqual(lbl_wrap_700, 600)

        top.destroy()

    def test_roster_dialog_resizability_and_wraplength(self):
        """RosterDialog must be resizable, have minimum dimensions >= (620, 500), and wrap labels."""
        if self.root is None:
            self.skipTest("No Tk display available")

        dlg = RosterDialog(
            parent=self.root,
            session_id=1,
            speaker_name="Dr. Jane Doe",
            current_roster=[],
            event_root=self.temp_dir,
            available_clips=[],
            on_save_callback=lambda _r: None,
        )
        self.root.update_idletasks()

        # Verify resizable
        resizable_x, resizable_y = dlg.resizable()
        self.assertTrue(resizable_x)
        self.assertTrue(resizable_y)

        # Verify minsize
        min_w, min_h = dlg.minsize()
        self.assertGreaterEqual(min_w, 620)
        self.assertGreaterEqual(min_h, 500)

        # Verify explanation label has wraplength configured
        wrap = int(str(dlg.lbl_explanation.cget("wraplength")))
        self.assertGreater(wrap, 0)
        self.assertLessEqual(wrap, 740)

        dlg.destroy()

    def test_vault_dialogs_resizability_and_minsize(self):
        """UnlockVaultDialog and InitializeVaultDialog must be resizable with minimum dimensions."""
        if self.root is None:
            self.skipTest("No Tk display available")

        keys_dir = os.path.join(self.temp_dir, "keys")
        vault = VoiceVault(keys_dir=keys_dir)

        # UnlockVaultDialog
        dlg_unlock = UnlockVaultDialog(self.root, vault)
        self.root.update_idletasks()
        rx, ry = dlg_unlock.resizable()
        self.assertTrue(rx)
        self.assertTrue(ry)
        mw, mh = dlg_unlock.minsize()
        self.assertGreaterEqual(mw, 380)
        self.assertGreaterEqual(mh, 220)
        dlg_unlock.destroy()

        # InitializeVaultDialog
        dlg_init = InitializeVaultDialog(self.root, vault)
        self.root.update_idletasks()
        rx, ry = dlg_init.resizable()
        self.assertTrue(rx)
        self.assertTrue(ry)
        mw, mh = dlg_init.minsize()
        self.assertGreaterEqual(mw, 400)
        self.assertGreaterEqual(mh, 320)
        dlg_init.destroy()

    def test_all_audited_dialogs_labels_fit_container(self):
        """Audit all dialogs to ensure no label width exceeds container width."""
        if self.root is None:
            self.skipTest("No Tk display available")

        # 1. Roster Add Speaker Dialog
        from gui.roster_dialog import RosterDialog
        dlg_roster = RosterDialog(
            parent=self.root,
            session_id=1,
            speaker_name="Dr. Jane Doe",
            current_roster=[],
            event_root=self.temp_dir,
            available_clips=[{"name": "clip1.wav", "path": os.path.join(self.temp_dir, "clip1.wav")}],
            on_save_callback=lambda _r: None,
        )
        self.root.update()

        # Check all labels in RosterDialog
        def check_labels(w, max_width):
            for child in w.winfo_children():
                if isinstance(child, (tk.Label, ttk.Label)):
                    req_w = child.winfo_reqwidth()
                    wrap = child.cget("wraplength")
                    try:
                        wrap_val = int(str(wrap))
                    except Exception:
                        wrap_val = 0
                    if wrap_val > 0:
                        self.assertLessEqual(wrap_val, max_width, f"Label {child} wraplength exceeds container {max_width}")
                    else:
                        self.assertLessEqual(req_w, max_width + 40, f"Unwrapped label {child} ({req_w}px) exceeds container ({max_width}px)")
                check_labels(child, max_width)

        check_labels(dlg_roster, 740)
        dlg_roster.destroy()


if __name__ == "__main__":
    unittest.main()
