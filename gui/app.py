"""
Session Scribe Main GUI Application
Standard Tkinter/ttk desktop application for managing event transcription workflows.
"""

import sys
import tkinter as tk
from tkinter import ttk, messagebox
import torch

from .stage2_tab import Stage2Tab
from .stage3_tab import Stage3Tab

class SessionScribeApp(tk.Tk):
    """Main application window with stage tabs and hardware status."""

    def __init__(self):
        super().__init__()

        self.title("Session Scribe - Audio Transcription & Documentation")
        self.geometry("1100x750")
        self.minsize(950, 600)

        # Apply a clean ttk theme
        style = ttk.Style(self)
        try:
            if "vista" in style.theme_names():
                style.theme_use("vista")
            elif "clam" in style.theme_names():
                style.theme_use("clam")
        except Exception:
            pass

        self._build_layout()
        self._check_hardware_status()

    def _build_layout(self):
        # Header banner
        header = ttk.Frame(self, padding=(15, 8))
        header.pack(fill=tk.X)

        title_lbl = ttk.Label(
            header,
            text="SESSION SCRIBE",
            font=("Segoe UI", 14, "bold"),
            foreground="#292b2c"
        )
        title_lbl.pack(side=tk.LEFT)

        subtitle_lbl = ttk.Label(
            header,
            text="Audio Transcription, Diarization & Technical Documentation Pipeline",
            font=("Segoe UI", 9),
            foreground="#666"
        )
        subtitle_lbl.pack(side=tk.LEFT, padx=15, pady=(4, 0))

        # Main Notebook Tabs
        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

        # Tab 1: Stage 2 - Event Setup & Ingest
        self.stage2_tab = Stage2Tab(self.notebook, status_callback=self.set_status)
        self.notebook.add(self.stage2_tab, text=" Stage 2: Event Setup & Separation ")

        # Tab 2: Stage 3 - Transcription (Test Gate First, then Full Run)
        self.stage3_tab = Stage3Tab(self.notebook, status_callback=self.set_status)
        self.notebook.add(self.stage3_tab, text=" Stage 3: Transcription ")

        self.stage4_frame = ttk.Frame(self.notebook, padding=30)
        ttk.Label(
            self.stage4_frame,
            text="Stage 4: OCR, Slides & Frame Extraction\n(Tesseract OCR + ffmpeg slide extraction)",
            font=("Segoe UI", 11),
            justify=tk.CENTER,
            foreground="#777"
        ).pack(expand=True)
        self.notebook.add(self.stage4_frame, text=" Stage 4: OCR & Slides ", state="disabled")

        self.stage5_frame = ttk.Frame(self.notebook, padding=30)
        ttk.Label(
            self.stage5_frame,
            text="Stage 5: Combined Files & Technical Document Generation",
            font=("Segoe UI", 11),
            justify=tk.CENTER,
            foreground="#777"
        ).pack(expand=True)
        self.notebook.add(self.stage5_frame, text=" Stage 5: Tech Document ", state="disabled")

        # Bottom Status Bar
        self.status_bar = ttk.Frame(self, relief=tk.SUNKEN, padding=(10, 4))
        self.status_bar.pack(fill=tk.X, side=tk.BOTTOM)

        self.status_msg_var = tk.StringVar(value="Ready.")
        ttk.Label(self.status_bar, textvariable=self.status_msg_var, font=("Segoe UI", 9)).pack(side=tk.LEFT)

        self.hw_status_var = tk.StringVar(value="Checking Hardware...")
        ttk.Label(
            self.status_bar,
            textvariable=self.hw_status_var,
            font=("Segoe UI", 8),
            foreground="#0275d8"
        ).pack(side=tk.RIGHT)

    def set_status(self, msg: str):
        self.status_msg_var.set(msg)

    def _check_hardware_status(self):
        try:
            if torch.cuda.is_available():
                device = torch.cuda.get_device_name(0)
                free_mb, total_mb = [m / (1024**2) for m in torch.cuda.mem_get_info()]
                hw_str = f"GPU: {device} | VRAM: {free_mb:.0f}/{total_mb:.0f} MB Free | Python {sys.version.split()[0]}"
            else:
                hw_str = f"GPU: Not Available (CPU mode) | Python {sys.version.split()[0]}"
            self.hw_status_var.set(hw_str)
        except Exception:
            self.hw_status_var.set(f"Python {sys.version.split()[0]}")

def launch_gui():
    app = SessionScribeApp()
    app.mainloop()

if __name__ == "__main__":
    launch_gui()
