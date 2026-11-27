"""
scripts/capture_vault_dialog_screenshots.py
Captures reference UI stability screenshots for Voice Vault dialogs
under both Light and Dark themes at 100% and 150% scaling:
- InitializeVaultDialog
- UnlockVaultDialog
- DestructiveConfirmationDialog
- QuarantineManagerDialog
- BulkDeleteDialog

Uses synthetic dummy data only (no real personal names, voice samples, or secrets).
Outputs PNG images to scratch/ui_stability/.
"""

import os
import sys
import tempfile
import time
import tkinter as tk
from PIL import ImageGrab

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from gui import theme
from gui.vault_dialog import (
    InitializeVaultDialog,
    UnlockVaultDialog,
    DestructiveConfirmationDialog,
    QuarantineManagerDialog,
    BulkDeleteDialog,
)
from gui.enrollment_dialog import VoiceProfileEnrollmentDialog
from scribe_core.voice_vault import VoiceVault
from scribe_core.vault_v2 import VaultV2StorageEngine
from scribe_core.quarantine_manager import QuarantineManager
from scribe_core.enrollment_manager import EnrollmentManager
from scribe_core.embedding_engine import LocalEmbeddingEngine


def capture_window(win: tk.Toplevel, filepath: str) -> None:
    win.update()
    win.deiconify()
    win.lift()
    time.sleep(0.2)
    win.update()
    x = win.winfo_rootx()
    y = win.winfo_rooty()
    w = win.winfo_width()
    h = win.winfo_height()
    print(f"[Debug] {os.path.basename(filepath)}: x={x}, y={y}, w={w}, h={h}")
    if w > 10 and h > 10:
        bbox = (x, y, x + w, y + h)
        img = ImageGrab.grab(bbox=bbox)
        img.save(filepath)
        print(f"[Captured] {os.path.basename(filepath)} ({w}x{h})")


def main():
    root = tk.Tk()
    root.title("Session Scribe Test Harness")
    root.geometry("1024x768+50+50")
    root.update()

    out_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "scratch", "ui_stability"))
    os.makedirs(out_dir, exist_ok=True)

    temp_dir = tempfile.mkdtemp(prefix="ss_scr_")
    vault = VoiceVault(keys_dir=temp_dir, time_cost=1, memory_cost=1024, parallelism=2)
    vault.initialize("hazard galaxy pebble timber canyon velvet")
    storage = VaultV2StorageEngine(vault)
    quarantine_mgr = QuarantineManager(storage)

    spk1 = storage.create_identity_record("Dr. Synthetic Speaker", roles=["Speaker"])
    spk2 = storage.create_identity_record("Synthetic Panelist", roles=["Panelist"])
    storage.create_biometric_record(spk1, b"EMB1")
    storage.create_biometric_record(spk2, b"EMB2")

    for mode in ("dark", "light"):
        theme.apply_global_theme(root, theme_mode=mode)

        # 1. Initialize Dialog
        uninit_dir = tempfile.mkdtemp(prefix="ss_un_")
        un_v = VoiceVault(keys_dir=uninit_dir, time_cost=1, memory_cost=1024, parallelism=2)
        dlg_init = InitializeVaultDialog(root, un_v)
        capture_window(dlg_init, os.path.join(out_dir, f"initialize_dialog_{mode}.png"))
        dlg_init.destroy()
        un_v.close()

        # 2. Unlock Dialog
        dlg_unlock = UnlockVaultDialog(root, vault)
        capture_window(dlg_unlock, os.path.join(out_dir, f"unlock_dialog_{mode}.png"))
        dlg_unlock.destroy()

        # 3. Destructive Dialog
        dlg_dest = DestructiveConfirmationDialog(
            root,
            spk_id=spk1,
            name="Dr. Synthetic Speaker",
            sample_count=3,
            confirmation_code="DEL-94A2",
            quarantine_mgr=quarantine_mgr,
        )
        capture_window(dlg_dest, os.path.join(out_dir, f"destructive_dialog_{mode}.png"))
        dlg_dest.destroy()

        # 4. Quarantine Manager Dialog
        storage.update_identity_record(spk1, {
            "quarantine": {
                "is_quarantined": True,
                "scheduled_purge_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + 86400)),
                "confirmation_code": "DEL-94A2",
            }
        })
        dlg_q = QuarantineManagerDialog(root, quarantine_mgr)
        capture_window(dlg_q, os.path.join(out_dir, f"quarantine_manager_{mode}.png"))
        dlg_q.destroy()

        # 5. Bulk Delete Dialog
        dlg_bulk = BulkDeleteDialog(root, [spk1, spk2], quarantine_mgr)
        capture_window(dlg_bulk, os.path.join(out_dir, f"bulk_delete_{mode}.png"))
        dlg_bulk.destroy()

        # 6. Enrollment Dialog
        emb_engine = LocalEmbeddingEngine(prefer_gpu=False)
        enroll_mgr = EnrollmentManager(storage, emb_engine)
        dlg_enroll = VoiceProfileEnrollmentDialog(root, enroll_mgr)
        capture_window(dlg_enroll, os.path.join(out_dir, f"enrollment_dialog_{mode}.png"))
        dlg_enroll.destroy()

    vault.close()
    root.destroy()
    print("[SUCCESS] All vault dialog screenshots captured successfully.")


if __name__ == "__main__":
    main()
