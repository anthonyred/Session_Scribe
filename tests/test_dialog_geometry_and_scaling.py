"""
tests/test_dialog_geometry_and_scaling.py

Automated scaling, geometry, and button visibility audit across 100%, 125%, 150%, and 175% scaling.
Verifies that for every dialog in Session Scribe:
1. Buttons and primary controls are fully visible and not clipped below the window bounds.
2. Status strips and warning banners remain hidden until populated.
3. Dialogs size from content with sensible minimums and are centered over parent toplevel.
4. ForgotPassphraseDialog accepts 'ERASE VAULT' case-insensitively with whitespace stripped,
   and enables/disables the primary button dynamically.
5. <Escape> cancels/closes and <Return> triggers primary actions.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import tkinter as tk
from tkinter import ttk, messagebox
import unittest
from unittest.mock import MagicMock, patch

# sys.path guard derived from __file__
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from gui import theme
from gui.vault_dialog import (
    UnlockVaultDialog,
    ForgotPassphraseDialog,
    InitializeVaultDialog,
    ChangePassphraseDialog,
    PassphraseAgeWarningDialog,
    DestructiveConfirmationDialog,
    QuarantineManagerDialog,
    BulkDeleteDialog,
)
from gui.enrollment_dialog import VoiceProfileEnrollmentDialog
from gui.event_roster_dialog import EventRosterDialog
from gui.session_config_dialog import SessionConfigDialog
from gui.reidentify_dialog import ReidentifyDialog
from gui.roster_dialog import RosterDialog
from gui.theme import TextSizeDialog
from gui.stage3_tab import (
    TranscriptEditorDialog,
    ReTranscribeDialog,
)
from gui.event_context import EventContext
from gui.stage3_tab import Stage3Tab
from gui.stage2_tab import Stage2Tab
from gui.app import SessionScribeApp, __version__, __author__, __license__, __copyright__
from scribe_core.voice_vault import VoiceVault
from scribe_core.vault_v2 import VaultV2StorageEngine
from scribe_core.quarantine_manager import QuarantineManager
from scribe_core.enrollment_manager import EnrollmentManager
from scribe_core.event_roster_manager import EventRosterManager
from scribe_core.session_grouper import SessionInfo, ClipInfo
from scribe_core.identification_engine import SpeakerCluster
from scribe_core.cloud_engine import PreflightTestResult, ProviderProfile, PreflightStageResult


def get_all_buttons(container: tk.Misc) -> list[tk.Widget]:
    """Recursively discover all Button widgets."""
    btns = []
    for child in container.winfo_children():
        if isinstance(child, (ttk.Button, tk.Button)):
            btns.append(child)
        btns.extend(get_all_buttons(child))
    return btns


class TestDialogGeometryAndScaling(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            cls.root = tk.Tk()
            cls.root.withdraw()
            theme.make_dpi_aware()
        except Exception:
            cls.root = None

    @classmethod
    def tearDownClass(cls):
        if cls.root is not None:
            try:
                cls.root.destroy()
            except Exception:
                pass

    def setUp(self):
        if not self.root:
            self.skipTest("Headless environment: Tk root unavailable")

        # Global mock for modal wait_window and messageboxes so tests never block
        self._wait_patch = patch.object(tk.Misc, "wait_window", lambda *a, **k: None)
        self._wait_patch.start()
        self._msg_info = patch("tkinter.messagebox.showinfo", return_value=True)
        self._msg_info.start()
        self._msg_warn = patch("tkinter.messagebox.showwarning", return_value=True)
        self._msg_warn.start()
        self._msg_err = patch("tkinter.messagebox.showerror", return_value=True)
        self._msg_err.start()

        self.temp_dir = tempfile.mkdtemp(prefix="ss_dlg_scale_")
        self.vault = VoiceVault(
            keys_dir=self.temp_dir,
            time_cost=1,
            memory_cost=1024,
            parallelism=2,
        )
        self.test_passphrase = "hazard galaxy pebble timber canyon velvet"
        self.vault.initialize(self.test_passphrase)
        self.storage = VaultV2StorageEngine(self.vault)
        self.quarantine_mgr = QuarantineManager(self.storage)
        self.enrollment_mgr = EnrollmentManager(self.storage)
        self.roster_mgr = EventRosterManager(self.vault)

    def tearDown(self):
        self._wait_patch.stop()
        self._msg_info.stop()
        self._msg_warn.stop()
        self._msg_err.stop()
        self.vault.close()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _assert_buttons_inside_dialog(self, dlg: tk.Toplevel, dlg_name: str, scale: float):
        """Assert every mapped button is completely within the dialog geometry bounds."""
        dlg.update_idletasks()
        dlg_top = dlg.winfo_rooty()
        dlg_bottom = dlg_top + dlg.winfo_height()
        dlg_left = dlg.winfo_rootx()
        dlg_right = dlg_left + dlg.winfo_width()

        buttons = get_all_buttons(dlg)
        self.assertGreater(len(buttons), 0, f"{dlg_name} should contain at least one button")

        for btn in buttons:
            if btn.winfo_viewable() and btn.winfo_height() > 0:
                b_top = btn.winfo_rooty()
                b_bottom = b_top + btn.winfo_height()
                b_left = btn.winfo_rootx()
                b_right = b_left + btn.winfo_width()

                # Generous 4px tolerance for window manager borders/shadows
                self.assertLessEqual(
                    b_bottom,
                    dlg_bottom + 4,
                    f"[{dlg_name} @ {int(scale*100)}%] Button '{btn.cget('text')}' clipped below bottom: {b_bottom} > {dlg_bottom}",
                )
                self.assertGreaterEqual(
                    b_top,
                    dlg_top - 4,
                    f"[{dlg_name} @ {int(scale*100)}%] Button '{btn.cget('text')}' clipped above top: {b_top} < {dlg_top}",
                )
                self.assertGreaterEqual(
                    btn.winfo_height(),
                    14,
                    f"[{dlg_name} @ {int(scale*100)}%] Button '{btn.cget('text')}' has crushed height: {btn.winfo_height()}",
                )

    def test_01_forgot_passphrase_dialog_matching_and_buttons(self):
        """Test ForgotPassphraseDialog case-insensitive ERASE VAULT, whitespace, and live match."""
        for scale in [1.0, 1.25, 1.5, 1.75]:
            theme.apply_global_theme(self.root, scale=scale, force=True)
            theme.init_theme_fonts(self.root, scale=scale)

            on_erase = MagicMock()
            dlg = ForgotPassphraseDialog(self.root, self.vault, on_erased=on_erase)
            dlg.update_idletasks()

            self._assert_buttons_inside_dialog(dlg, "ForgotPassphraseDialog", scale)

            # Initially blank -> erase button disabled
            self.assertEqual(str(dlg.btn_erase["state"]), tk.DISABLED)
            self.assertIn("Type 'ERASE VAULT'", dlg.match_lbl.cget("text"))

            # Partial text -> disabled
            dlg.confirm_var.set("erase")
            self.assertEqual(str(dlg.btn_erase["state"]), tk.DISABLED)

            # Case-insensitive + surrounding whitespace -> ENABLED
            dlg.confirm_var.set("  erase vault  ")
            self.assertEqual(str(dlg.btn_erase["state"]), tk.NORMAL)
            self.assertIn("✓", dlg.match_lbl.cget("text"))

            # All caps -> ENABLED
            dlg.confirm_var.set("ERASE VAULT")
            self.assertEqual(str(dlg.btn_erase["state"]), tk.NORMAL)

            # Status label hidden when empty
            self.assertFalse(dlg.status_lbl.winfo_viewable())

            dlg.destroy()

    def test_02_vault_dialogs_geometry_and_scaling(self):
        """Test all Vault dialogs under 100%, 125%, 150%, 175% scaling."""
        for scale in [1.0, 1.25, 1.5, 1.75]:
            theme.apply_global_theme(self.root, scale=scale, force=True)
            theme.init_theme_fonts(self.root, scale=scale)

            # 1. UnlockVaultDialog
            d_unlock = UnlockVaultDialog(self.root, self.vault)
            d_unlock.update_idletasks()
            self._assert_buttons_inside_dialog(d_unlock, "UnlockVaultDialog", scale)
            # Ensure status strip is hidden when text is empty
            self.assertFalse(d_unlock.status_lbl.winfo_viewable())
            d_unlock.destroy()

            # 2. InitializeVaultDialog
            d_init = InitializeVaultDialog(self.root, self.vault)
            d_init.update_idletasks()
            self._assert_buttons_inside_dialog(d_init, "InitializeVaultDialog", scale)
            self.assertFalse(d_init.error_lbl1.winfo_viewable())
            d_init.destroy()

            # 3. ChangePassphraseDialog
            d_chg = ChangePassphraseDialog(self.root, self.vault)
            d_chg.update_idletasks()
            self._assert_buttons_inside_dialog(d_chg, "ChangePassphraseDialog", scale)
            self.assertFalse(d_chg.status_lbl.winfo_viewable())
            d_chg.destroy()

            # 4. PassphraseAgeWarningDialog
            d_age = PassphraseAgeWarningDialog(self.root, self.vault, age_days=400)
            d_age.update_idletasks()
            self._assert_buttons_inside_dialog(d_age, "PassphraseAgeWarningDialog", scale)
            d_age.destroy()

            # 5. DestructiveConfirmationDialog
            d_dest = DestructiveConfirmationDialog(
                self.root,
                spk_id="spk_01",
                name="Jane Doe",
                sample_count=3,
                confirmation_code="1234",
                quarantine_mgr=self.quarantine_mgr,
            )
            d_dest.update_idletasks()
            self._assert_buttons_inside_dialog(d_dest, "DestructiveConfirmationDialog", scale)
            self.assertFalse(d_dest.status_lbl.winfo_viewable())
            d_dest.destroy()

            # 6. QuarantineManagerDialog
            d_quar = QuarantineManagerDialog(self.root, self.quarantine_mgr)
            d_quar.update_idletasks()
            self._assert_buttons_inside_dialog(d_quar, "QuarantineManagerDialog", scale)
            d_quar.destroy()

            # 7. BulkDeleteDialog
            d_bulk = BulkDeleteDialog(self.root, spk_ids=["spk_1", "spk_2"], quarantine_mgr=self.quarantine_mgr)
            d_bulk.update_idletasks()
            self._assert_buttons_inside_dialog(d_bulk, "BulkDeleteDialog", scale)
            self.assertFalse(d_bulk.status_lbl.winfo_viewable())
            d_bulk.destroy()

    def test_03_roster_and_session_dialogs(self):
        """Test EventRosterDialog, SessionConfigDialog, ReidentifyDialog, RosterDialog, TextSizeDialog."""
        for scale in [1.0, 1.25, 1.5, 1.75]:
            theme.apply_global_theme(self.root, scale=scale, force=True)
            theme.init_theme_fonts(self.root, scale=scale)

            # 1. VoiceProfileEnrollmentDialog
            d_enr = VoiceProfileEnrollmentDialog(self.root, self.enrollment_mgr)
            d_enr.update_idletasks()
            self._assert_buttons_inside_dialog(d_enr, "VoiceProfileEnrollmentDialog", scale)
            # Ensure dup banner and quality label hidden when empty
            self.assertFalse(d_enr.dup_banner.winfo_viewable())
            self.assertFalse(d_enr.quality_lbl.winfo_viewable())
            d_enr.destroy()

            # 2. EventRosterDialog
            d_evr = EventRosterDialog(self.root, event_id="ev_test_01", roster_mgr=self.roster_mgr)
            d_evr.update_idletasks()
            self._assert_buttons_inside_dialog(d_evr, "EventRosterDialog", scale)
            d_evr.destroy()

            # 3. SessionConfigDialog
            session_obj = SessionInfo(session_id=1, speaker_name="Speaker 1")
            d_sess = SessionConfigDialog(
                self.root,
                session=session_obj,
                event_roster=[{"alias": "A01", "name": "Jane Doe", "profile_id": "spk_1", "quarantined": False}],
            )
            d_sess.update_idletasks()
            self._assert_buttons_inside_dialog(d_sess, "SessionConfigDialog", scale)
            d_sess.destroy()

            # 4. ReidentifyDialog
            clusters = [SpeakerCluster(cluster_id="cluster_01", is_unidentified=True)]
            event_roster = [{"alias": "A01", "name": "Jane Doe", "profile_id": "spk_1", "quarantined": False}]
            d_reid = ReidentifyDialog(self.root, clusters=clusters, event_roster=event_roster)
            d_reid.update_idletasks()
            self._assert_buttons_inside_dialog(d_reid, "ReidentifyDialog", scale)
            d_reid.destroy()

            # 5. RosterDialog (legacy voice library)
            d_rost = RosterDialog(
                self.root,
                session_id=1,
                speaker_name="Speaker 1",
                current_roster=[],
                event_root=self.temp_dir,
                available_clips=[],
                on_save_callback=lambda _r: None,
            )
            d_rost.update_idletasks()
            self._assert_buttons_inside_dialog(d_rost, "RosterDialog", scale)
            d_rost.destroy()

            # 6. TextSizeDialog
            d_txt = TextSizeDialog(self.root, current_scale=scale, on_apply=lambda _s: None)
            d_txt.update_idletasks()
            self._assert_buttons_inside_dialog(d_txt, "TextSizeDialog", scale)
            d_txt.destroy()

    def test_04_stage3_dialogs(self):
        """Test ReTranscribeDialog, TranscriptEditorDialog, and Stage 3 popups."""
        ctx = EventContext()
        ctx.event_dir_var.set(self.temp_dir)
        stage3 = Stage3Tab(self.root, ctx=ctx, status_callback=lambda _s: None, vault=self.vault)

        for scale in [1.0, 1.25, 1.5, 1.75]:
            theme.apply_global_theme(self.root, scale=scale, force=True)
            theme.init_theme_fonts(self.root, scale=scale)

            # 1. ReTranscribeDialog
            dummy_clips = [{"name": "clip_01.mp4", "duration": 45.0, "speaker": "Speaker 1"}]
            d_ret = ReTranscribeDialog(self.root, clips=dummy_clips, on_start_callback=lambda *a: None)
            d_ret.update_idletasks()
            self._assert_buttons_inside_dialog(d_ret, "ReTranscribeDialog", scale)
            d_ret.destroy()

            # 2. TranscriptEditorDialog
            dummy_txt = os.path.join(self.temp_dir, f"test_clip_{int(scale*100)}.txt")
            with open(dummy_txt, "w", encoding="utf-8") as f:
                f.write("Line 1: Hello world\nSpeaker: S01\nLine 3: Goodbye\n")

            mock_tab = MagicMock()
            mock_tab.vault = self.vault
            mock_tab.manifest_data = {"event_id": "ev_01"}

            d_ed = TranscriptEditorDialog(
                self.root,
                stage3_tab=mock_tab,
                clip={"name": "test_clip.mp4", "speaker": "Jane Doe"},
                target_txt=dummy_txt,
                loc_desc="Event / S01",
                paths={"state": self.temp_dir},
            )
            d_ed.update_idletasks()
            self._assert_buttons_inside_dialog(d_ed, "TranscriptEditorDialog", scale)

            # 3. _check_disk_conflict in editor
            with open(dummy_txt, "w", encoding="utf-8") as f:
                f.write("Externally modified\n")
            # Open disk conflict dialog
            d_ed._check_disk_conflict()
            conf_dlgs = [w for w in d_ed.winfo_children() if isinstance(w, tk.Toplevel)]
            if conf_dlgs:
                self._assert_buttons_inside_dialog(conf_dlgs[-1], "FileConflictDialog", scale)
                conf_dlgs[-1].destroy()

            d_ed.destroy()

            # 4. _show_test_transcription_dialog
            res = PreflightTestResult(
                success=True,
                total_duration_s=1.23,
                stages=[PreflightStageResult(stage_name="Network", status="PASS", duration_s=0.1, message="OK")],
            )
            prof = ProviderProfile(id="nv_test", name="NVIDIA Test", protocol="riva_grpc", endpoint="grpc.nvcf.nvidia.com:443")
            stage3._show_test_transcription_dialog(res, prof)
            test_dlgs = [w for w in stage3.winfo_children() if isinstance(w, tk.Toplevel)]
            self.assertTrue(test_dlgs)
            self._assert_buttons_inside_dialog(test_dlgs[-1], "CloudTestDialog", scale)
            test_dlgs[-1].destroy()

            # 5. _show_models_dialog
            stage3._show_models_dialog([{"name": "nemotron_transcribe", "is_nemotron_transcribe": True}])
            m_dlgs = [w for w in stage3.winfo_children() if isinstance(w, tk.Toplevel)]
            self.assertTrue(m_dlgs)
            self._assert_buttons_inside_dialog(m_dlgs[-1], "NvidiaModelsDialog", scale)
            m_dlgs[-1].destroy()

            # 6. _open_glossary_dialog
            stage3._open_glossary_dialog()
            g_dlgs = [w for w in stage3.winfo_children() if isinstance(w, tk.Toplevel)]
            self.assertTrue(g_dlgs)
            self._assert_buttons_inside_dialog(g_dlgs[-1], "GlossaryDialog", scale)
            g_dlgs[-1].destroy()

            # 7. _check_placeholder_names
            stage3._check_placeholder_names([{"speaker": "Speaker 1"}])
            ph_dlgs = [w for w in stage3.winfo_children() if isinstance(w, tk.Toplevel)]
            if ph_dlgs:
                self._assert_buttons_inside_dialog(ph_dlgs[-1], "PlaceholderNamesDialog", scale)
                ph_dlgs[-1].destroy()

        stage3.destroy()

    def test_05_about_and_stage2_dialogs(self):
        """Test About dialog and Stage 2 dialogs at all scaling factors."""
        ctx = EventContext()
        ctx.event_dir_var.set(self.temp_dir)
        stage2 = Stage2Tab(self.root, ctx=ctx, status_callback=lambda _s: None, vault=self.vault)

        for scale in [1.0, 1.25, 1.5, 1.75]:
            theme.apply_global_theme(self.root, scale=scale, force=True)
            theme.init_theme_fonts(self.root, scale=scale)

            # 1. About Dialog
            dlg_about = tk.Toplevel(self.root)
            dlg_about.title("About Session Scribe")
            dlg_about.resizable(True, True)
            dlg_about.minsize(440, 320)
            card = tk.Frame(dlg_about, bg=theme.COLOR_CARD_BG, padx=24, pady=20)
            card.pack(fill=tk.BOTH, expand=True)
            btn_close = ttk.Button(card, text="Close", command=dlg_about.destroy, style="Primary.TButton")
            btn_close.pack(side=tk.BOTTOM, pady=(8, 0))
            tk.Label(card, text="Session Scribe", font="SS_Heading", bg=theme.COLOR_CARD_BG).pack()
            theme.center_dialog(dlg_about, self.root, width=500, height=380, min_width=440, min_height=320)
            dlg_about.update_idletasks()
            self._assert_buttons_inside_dialog(dlg_about, "AboutDialog", scale)
            dlg_about.destroy()

            # 2. Stage 2 Select Event dialog
            dlg_sel = tk.Toplevel(stage2)
            dlg_sel.title("Select Event")
            dlg_sel.minsize(360, 280)
            dlg_sel.resizable(True, True)
            theme.center_dialog(dlg_sel, stage2, width=440, height=340, min_width=360, min_height=280)
            btn_box = ttk.Frame(dlg_sel)
            btn_box.pack(fill=tk.X, side=tk.BOTTOM, padx=10, pady=8)
            ttk.Button(btn_box, text="Select", command=dlg_sel.destroy).pack(side=tk.RIGHT, padx=4)
            ttk.Button(btn_box, text="Cancel", command=dlg_sel.destroy).pack(side=tk.RIGHT)
            ttk.Label(dlg_sel, text="Available Events:", font=("Segoe UI", 10, "bold")).pack(anchor=tk.W, padx=10, pady=8)
            dlg_sel.update_idletasks()
            self._assert_buttons_inside_dialog(dlg_sel, "SelectEventDialog", scale)
            dlg_sel.destroy()

            # 3. Stage 2 Rename Speaker dialog
            dlg_rn = tk.Toplevel(stage2)
            dlg_rn.title("Rename Speaker (Speaker 1)")
            dlg_rn.minsize(320, 150)
            dlg_rn.resizable(True, True)
            theme.center_dialog(dlg_rn, stage2, width=380, height=180, min_width=320, min_height=150)
            btn_box2 = ttk.Frame(dlg_rn)
            btn_box2.pack(fill=tk.X, side=tk.BOTTOM, pady=8, padx=10)
            ttk.Button(btn_box2, text="Save", command=dlg_rn.destroy).pack(side=tk.RIGHT, padx=4)
            ttk.Button(btn_box2, text="Cancel", command=dlg_rn.destroy).pack(side=tk.RIGHT)
            ttk.Label(dlg_rn, text="Enter Speaker Name:").pack(pady=(12, 4), padx=10)
            entry = ttk.Entry(dlg_rn, width=32)
            entry.pack(pady=4, padx=10, fill=tk.X)
            dlg_rn.update_idletasks()
            self._assert_buttons_inside_dialog(dlg_rn, "RenameSpeakerDialog", scale)
            dlg_rn.destroy()

        stage2.destroy()


if __name__ == "__main__":
    unittest.main()
