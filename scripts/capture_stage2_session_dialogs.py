"""
scripts/capture_stage2_session_dialogs.py

Captures reference UI stability screenshots for Stage 2 Session Model dialogs:
- EventRosterDialog
- SessionConfigDialog (Solo Mode)
- SessionConfigDialog (Panel Mode)

Captured in both Light and Dark modes at 100% and 150% scaling.
Uses synthetic dummy data only (no real personal names, voice data, or secrets).
Outputs images to scratch/ui_stability/.
"""

import os
import sys
import tempfile
import time
import tkinter as tk
from PIL import ImageGrab

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from gui import theme
from gui.event_roster_dialog import EventRosterDialog
from gui.session_config_dialog import SessionConfigDialog
from gui.reidentify_dialog import ReidentifyDialog
from scribe_core.identification_engine import SpeakerCluster
from scribe_core.voice_vault import VoiceVault
from scribe_core.vault_v2 import VaultV2StorageEngine
from scribe_core.event_roster_manager import EventRosterManager
from scribe_core.session_grouper import SessionInfo, ClipInfo
from datetime import datetime, timedelta


def capture_window(win: tk.Toplevel, filepath: str) -> None:
    win.update()
    win.deiconify()
    win.lift()
    time.sleep(0.3)
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
    root.title("Stage 2 Session Dialog Test Harness")
    root.geometry("1024x768+50+50")
    root.update()

    out_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "scratch", "ui_stability"))
    os.makedirs(out_dir, exist_ok=True)

    temp_dir = tempfile.mkdtemp(prefix="ss_s2_dlg_")
    vault = VoiceVault(keys_dir=temp_dir, time_cost=1, memory_cost=1024, parallelism=2)
    vault.initialize("galaxy velvet pebble timber hazard canyon")
    storage = VaultV2StorageEngine(vault)

    # Enrol synthetic people
    spk1 = storage.create_identity_record("Dr. Synthetic Speaker", roles=["Speaker", "Host"], consent_opt_in=True)
    spk2 = storage.create_identity_record("Synthetic Panelist", roles=["Speaker", "Panelist"], consent_opt_in=True)
    spk3 = storage.create_identity_record("Guest Moderator", roles=["Moderator"], consent_opt_in=True)
    storage.create_biometric_record(spk1, b"\x00" * 2048)
    storage.create_biometric_record(spk2, b"\x00" * 2048)
    storage.create_biometric_record(spk3, b"\x00" * 2048)

    event_id = "evt_screenshot_harness_1"
    roster_mgr = EventRosterManager(storage=storage)

    # Initial roster with S01 and S02
    alias1 = EventRosterManager.assign_next_alias([])
    alias2 = EventRosterManager.assign_next_alias([alias1])
    roster_items = [
        {"alias": alias1, "spk_id": spk1, "name": "Dr. Synthetic Speaker", "role": "Host"},
        {"alias": alias2, "spk_id": spk2, "name": "Synthetic Panelist", "role": "Panelist"},
    ]
    roster_mgr.save_event_roster(event_id, roster_items)

    now = datetime(2026, 11, 19, 10, 0, 0)
    c1 = ClipInfo("clip_01.mp4", "/path/clip_01.mp4", now, 60.0, now + timedelta(seconds=60))

    scales = [1.0, 1.5]
    modes = ["dark", "light"]

    for scale in scales:
        scale_pct = int(scale * 100)

        for mode in modes:
            theme.apply_ui_scale(root, scale=scale, theme_mode=mode)
            root.update()

            # 1. Event Roster Dialog
            dlg_roster = EventRosterDialog(
                parent=root,
                event_id=event_id,
                roster_mgr=roster_mgr,
                on_save_callback=lambda r: None,
            )
            capture_window(dlg_roster, os.path.join(out_dir, f"event_roster_dialog_{mode}_{scale_pct}pct.png"))
            dlg_roster.destroy()

            # 2. Session Config Dialog (Solo)
            session_solo = SessionInfo(
                session_id=1,
                speaker_name="S01",
                clips=[c1],
                session_type="solo",
                expected_speaker={"alias": "S01", "role": "Speaker"},
            )
            dlg_solo = SessionConfigDialog(
                parent=root,
                session=session_solo,
                event_roster=roster_items,
                on_save_callback=lambda s: None,
            )
            capture_window(dlg_solo, os.path.join(out_dir, f"session_config_solo_{mode}_{scale_pct}pct.png"))
            dlg_solo.destroy()

            # 3. Session Config Dialog (Panel)
            session_panel = SessionInfo(
                session_id=2,
                speaker_name="Panel Session 2",
                clips=[c1],
                session_type="panel",
                host={"alias": "S01", "role": "Host"},
                panelists=[{"alias": "S02", "role": "Panelist"}],
            )
            dlg_panel = SessionConfigDialog(
                parent=root,
                session=session_panel,
                event_roster=roster_items,
                on_save_callback=lambda s: None,
            )
            capture_window(dlg_panel, os.path.join(out_dir, f"session_config_panel_{mode}_{scale_pct}pct.png"))
            dlg_panel.destroy()

            # 4. Reidentify Dialog ("Who is this?")
            clusters_mock = [
                SpeakerCluster("cluster_00", matched_alias="[Unidentified]", turn_intervals=[(0.0, 4.0)], is_unidentified=True, confidence=0.52),
                SpeakerCluster("cluster_01", matched_alias="[Audience]", turn_intervals=[(10.0, 11.5)], is_unidentified=True, is_audience=True, confidence=0.48),
            ]
            dlg_reident = ReidentifyDialog(
                parent=root,
                clusters=clusters_mock,
                event_roster=roster_items,
            )
            capture_window(dlg_reident, os.path.join(out_dir, f"reidentify_dialog_{mode}_{scale_pct}pct.png"))
            dlg_reident.destroy()

    # Reset theme scale to 1.0
    theme.apply_ui_scale(root, scale=1.0, theme_mode="dark")
    vault.close()
    root.destroy()
    import shutil
    shutil.rmtree(temp_dir, ignore_errors=True)
    print("[SUCCESS] All Stage 2 dialog screenshots captured successfully.")


if __name__ == "__main__":
    main()
