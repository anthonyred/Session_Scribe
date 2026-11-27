"""
scripts/generate_theme_review_screenshots.py

Generates comparison screenshots of Session Scribe in:
- Palette A: Graphite (Apple-style neutral dark greys)
- Palette B: Midnight (Fluent-style blue-greys)
- Light: Clean, high-contrast light mode
Across 100% and 150% UI scaling for:
- Stage 2 (Ingest & Session Detection)
- Stage 3 with 3 clips
- Stage 3 with 21 clips
- Speaker Roster Dialog
- Transcript Viewer Dialog
- Connections Panel (NVIDIA Models Dialog)
- Unlock Voice Vault Dialog

Outputs saved to scratch/theme_review/
"""

import os
import sys
import time
import tempfile
import json
import shutil
import tkinter as tk
from tkinter import ttk
from PIL import Image, ImageGrab

# Anchor path to repo root
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
os.chdir(REPO_ROOT)
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from gui import theme
from gui.event_context import EventContext
from gui.stage2_tab import Stage2Tab
from gui.stage3_tab import Stage3Tab, TranscriptEditorDialog
from gui.roster_dialog import RosterDialog
from gui.vault_dialog import UnlockVaultDialog
from scribe_core.voice_vault import VoiceVault


def capture_window(win: tk.Misc, out_path: str, delay_s: float = 0.2):
    """Capture bounding box of a visible Tkinter window."""
    win.update_idletasks()
    win.update()
    time.sleep(delay_s)
    win.update_idletasks()
    win.update()

    x = win.winfo_rootx()
    y = win.winfo_rooty()
    w = win.winfo_width()
    h = win.winfo_height()

    # Grab pixel area
    img = ImageGrab.grab(bbox=(x, y, x + w, y + h))
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    img.save(out_path)
    return img


def create_mock_event(temp_dir: str, num_clips: int = 21):
    """Create simulated event files for screenshots."""
    event_root = os.path.join(temp_dir, f"Event_Sim_{num_clips}")
    scribe_dir = os.path.join(event_root, "scribe")
    trans_dir = os.path.join(scribe_dir, "transcripts")
    state_dir = os.path.join(scribe_dir, "state")
    os.makedirs(trans_dir, exist_ok=True)
    os.makedirs(state_dir, exist_ok=True)

    clips = []
    speakers = ["Dr. Sarah Chen", "Marcus Vance", "Elena Rostova", "David Kim"]
    for i in range(1, num_clips + 1):
        c_name = f"clip_{i:02d}.mp4"
        speaker = speakers[(i - 1) % len(speakers)]
        dur = 120.0 + (i * 15.0)
        status = "Done" if i > 2 else "Done (test)"
        engine = "nvidia/parakeet-ctc-1.1b" if i % 2 == 0 else "openai/whisper-medium"

        clips.append({
            "name": c_name,
            "speaker": speaker,
            "duration": dur,
            "status": status,
            "engine": engine,
            "time_taken": dur / 12.0,
            "failover": 0.0,
            "approved": i % 3 != 0,
        })

        # Mock transcript
        txt_path = os.path.join(trans_dir, f"clip_{i:02d}.txt")
        with open(txt_path, "w", encoding="utf-8") as fh:
            fh.write(f"[{speaker}] [00:00:00 - 00:02:15] Welcome everyone to the presentation.\n")
            fh.write("Today we discuss neural speech recognition architectures and local privacy boundaries.\n")

    manifest = {
        "event_name": f"Event Sim {num_clips}",
        "clips": clips,
        "sessions": [
            {"session_id": 1, "speaker": speakers[0], "clips": clips[:5]},
            {"session_id": 2, "speaker": speakers[1], "clips": clips[5:10]},
        ],
    }
    manifest_path = os.path.join(scribe_dir, "manifest.json")
    with open(manifest_path, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2)

    clip_state = {
        f"clip_{i:02d}": {
            "status": c["status"],
            "approved": c["approved"],
            "engine": c["engine"],
            "model": c["engine"],
            "time_taken": c["time_taken"],
            "failover_time": c["failover"],
        }
        for i, c in enumerate(clips, 1)
    }
    with open(os.path.join(state_dir, "clip_state.json"), "w", encoding="utf-8") as fh:
        json.dump(clip_state, fh, indent=2)

    return event_root, clips


def make_side_by_side(images: list[Image.Image], labels: list[str], out_path: str):
    """Combine images horizontally with title labels for side-by-side comparison."""
    if not images:
        return
    max_h = max(img.height for img in images)
    total_w = sum(img.width for img in images) + ((len(images) - 1) * 10)

    combined = Image.new("RGB", (total_w, max_h), (24, 24, 24))
    cur_x = 0
    for img in images:
        combined.paste(img, (cur_x, 0))
        cur_x += img.width + 10

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    combined.save(out_path)


def main():
    theme.make_dpi_aware()
    scratch_dir = os.path.join(REPO_ROOT, "scratch", "theme_review")
    os.makedirs(scratch_dir, exist_ok=True)
    temp_dir = tempfile.mkdtemp()

    palettes = [
        ("palette_a", "Palette A (Graphite - Apple Dark)"),
        ("palette_b", "Palette B (Midnight - Fluent Blue-Dark)"),
        ("light", "Light Mode (Clean High-Contrast)"),
    ]
    scales = [(1.0, "scale100"), (1.5, "scale150")]

    ev_root_3, clips_3 = create_mock_event(temp_dir, num_clips=3)
    ev_root_21, clips_21 = create_mock_event(temp_dir, num_clips=21)

    print("=== Generating Theme Review Screenshots ===")

    for scale, scale_name in scales:
        for pal_key, pal_label in palettes:
            print(f"Rendering {pal_label} at {scale_name}...")

            root = tk.Tk()
            root.title(f"Session Scribe — {pal_label} ({scale_name})")
            root.geometry("1200x780+50+50")

            # Apply global theme
            theme.apply_global_theme(root, scale=scale, density="comfortable", theme_mode=pal_key)
            theme.apply_ui_scale(root, scale=scale, density="comfortable", theme_mode=pal_key)

            ctx = EventContext()
            ctx.event_dir_var.set(ev_root_21)
            ctx.event_name_var.set("Event Sim 21")
            ctx.event_date_var.set("2026-11-17")
            vault = VoiceVault(keys_dir=os.path.join(temp_dir, "keys"))

            notebook = ttk.Notebook(root)
            notebook.pack(fill=tk.BOTH, expand=True)

            stage2 = Stage2Tab(notebook, ctx=ctx)
            notebook.add(stage2, text=" Stage 2 ")

            stage3 = Stage3Tab(notebook, ctx=ctx, vault=vault)
            notebook.add(stage3, text=" Stage 3 ")

            root.update()

            # 1. Capture Stage 2
            notebook.select(0)
            root.update()
            s2_path = os.path.join(scratch_dir, f"stage2_{pal_key}_{scale_name}.png")
            capture_window(root, s2_path)

            # 2. Capture Stage 3 (21 rows)
            notebook.select(1)
            ctx.event_dir_var.set(ev_root_21)
            ctx.event_name_var.set("Event Sim 21")
            stage3._load_manifest(silent=True)
            root.update()
            s3_21_path = os.path.join(scratch_dir, f"stage3_21rows_{pal_key}_{scale_name}.png")
            capture_window(root, s3_21_path)

            # 3. Capture Stage 3 (3 rows)
            ctx.event_dir_var.set(ev_root_3)
            ctx.event_name_var.set("Event Sim 3")
            stage3._load_manifest(silent=True)
            root.update()
            s3_3_path = os.path.join(scratch_dir, f"stage3_3rows_{pal_key}_{scale_name}.png")
            capture_window(root, s3_3_path)

            # 4. Capture Roster Dialog
            roster_dlg = RosterDialog(
                parent=root,
                session_id=1,
                speaker_name="Dr. Sarah Chen",
                current_roster=[
                    {"name": "Dr. Sarah Chen", "role": "Host", "sample_path": None},
                    {"name": "Marcus Vance", "role": "Panelist", "sample_path": None},
                ],
                event_root=ev_root_21,
                available_clips=clips_21[:4],
                on_save_callback=lambda _r: None,
            )
            roster_path = os.path.join(scratch_dir, f"dialog_roster_{pal_key}_{scale_name}.png")
            capture_window(roster_dlg, roster_path)
            roster_dlg.destroy()

            # 5. Capture Viewer Dialog
            viewer_dlg = TranscriptEditorDialog(
                parent=root,
                stage3_tab=stage3,
                clip={"name": "clip_01.mp4", "speaker": "Dr. Sarah Chen"},
                target_txt=os.path.join(ev_root_21, "scribe", "transcripts", "clip_01.txt"),
                loc_desc="Videos/clip_01.mp4",
                paths={"transcripts": os.path.join(ev_root_21, "scribe", "transcripts")},
            )
            viewer_path = os.path.join(scratch_dir, f"dialog_viewer_{pal_key}_{scale_name}.png")
            capture_window(viewer_dlg, viewer_path)
            viewer_dlg.destroy()

            # 6. Capture Connections (Models) Dialog
            stage3._show_models_dialog([
                {"name": "Parakeet CTC 1.1B", "id": "nvidia/parakeet-ctc-1.1b", "status": "ACTIVE"},
                {"name": "Whisper Large v3", "id": "openai/whisper-large-v3", "status": "ACTIVE"},
                {"name": "Nemotron Speech (Preview)", "id": "nvidia/nemotron-speech", "status": "COMING_SOON", "is_nemotron_transcribe": False},
            ])
            # Find the opened dialog
            conn_dlg = None
            for child in list(stage3.winfo_children()) + list(root.winfo_children()):
                if isinstance(child, tk.Toplevel) and "Models" in child.title():
                    conn_dlg = child
                    break
            if conn_dlg:
                conn_path = os.path.join(scratch_dir, f"dialog_connections_{pal_key}_{scale_name}.png")
                capture_window(conn_dlg, conn_path)
                conn_dlg.destroy()

            # 7. Capture Unlock Vault Dialog
            unlock_dlg = UnlockVaultDialog(parent=root, vault=vault)
            unlock_path = os.path.join(scratch_dir, f"dialog_unlock_{pal_key}_{scale_name}.png")
            capture_window(unlock_dlg, unlock_path)
            unlock_dlg.destroy()

            root.destroy()

    # Generate side-by-side composites for easy 100% review
    print("Generating side-by-side comparison composites...")
    views = [
        ("stage3_21rows", "Stage 3 (21 rows)"),
        ("stage3_3rows", "Stage 3 (3 rows)"),
        ("stage2", "Stage 2 Setup"),
        ("dialog_roster", "Roster Dialog"),
        ("dialog_viewer", "Viewer Dialog"),
        ("dialog_connections", "Connections Dialog"),
        ("dialog_unlock", "Unlock Vault Dialog"),
    ]

    for v_key, v_title in views:
        img_graphite = Image.open(os.path.join(scratch_dir, f"{v_key}_palette_a_scale100.png"))
        img_midnight = Image.open(os.path.join(scratch_dir, f"{v_key}_palette_b_scale100.png"))
        img_light = Image.open(os.path.join(scratch_dir, f"{v_key}_light_scale100.png"))

        comp_path = os.path.join(scratch_dir, f"compare_{v_key}_scale100.png")
        make_side_by_side([img_graphite, img_midnight, img_light], ["Graphite", "Midnight", "Light"], comp_path)

    shutil.rmtree(temp_dir, ignore_errors=True)
    print("All theme review screenshots successfully generated in scratch/theme_review/!")


if __name__ == "__main__":
    main()
