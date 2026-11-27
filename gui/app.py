"""
Session Scribe Main GUI Application
Standard Tkinter/ttk desktop application for managing event transcription workflows.
"""

import os
import sys

# Anchor working directory to repository root so no relative paths break when launched from Start Menu
APP_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
os.chdir(APP_ROOT)
if APP_ROOT not in sys.path:
    sys.path.insert(0, APP_ROOT)

__version__ = "1.2.0"
__author__ = "Anthony Reddy"
__copyright__ = "© 2026 Anthony Reddy. All rights reserved."
__license__ = "All rights reserved © 2026 Anthony Reddy"
APP_USER_MODEL_ID = "SessionScribe.TranscriptionApp.2026"

# Set AppUserModelID so Windows taskbar groups and shows the Session Scribe icon, not the Python icon
if sys.platform == "win32":
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_USER_MODEL_ID)
    except Exception:
        pass

import tkinter as tk
from tkinter import ttk, messagebox
import torch

from .event_context import EventContext
from .stage2_tab import Stage2Tab
from .stage3_tab import Stage3Tab
from .scrollable_tab import install_application_wheel_handler
from .text_shortcuts import apply_text_shortcuts
from .vault_dialog import VaultStatusChip, UnlockVaultDialog, InitializeVaultDialog
from .enrollment_dialog import VoiceProfileEnrollmentDialog
from scribe_core.vault_v2 import VaultV2StorageEngine
from scribe_core.enrollment_manager import EnrollmentManager
from scribe_core.voice_vault import VoiceVault
from . import theme
from . import ui_settings

# Call DPI awareness before Tk initializes
theme.make_dpi_aware()


class SessionScribeApp(tk.Tk):
    """Main application window with stage tabs and hardware status."""

    def __init__(self):
        theme.make_dpi_aware()
        super().__init__()

        self.title("Session Scribe")
        self._apply_app_icons()

        # Initialize Voice Vault (does not prompt on app start)
        self.vault = VoiceVault()

        # Load persistent UI settings (scale, density, geometry, column widths) from ui.ini
        self.ui_prefs = ui_settings.load_ui_settings()
        self._current_scale = float(self.ui_prefs.get("text_scale", 1.0))
        self._current_density = str(self.ui_prefs.get("table_density", "comfortable"))
        self._theme_mode = str(self.ui_prefs.get("theme", "system"))
        self.theme_mode_var = tk.StringVar(value=self._theme_mode)

        # Monitor work area and initial geometry (~85% of usable work area, centered, min 1000x640)
        work_x, work_y, work_w, work_h = theme.get_monitor_work_area()
        saved_w = int(self.ui_prefs.get("window_width", 0))
        saved_h = int(self.ui_prefs.get("window_height", 0))
        saved_x = int(self.ui_prefs.get("window_x", -1))
        saved_y = int(self.ui_prefs.get("window_y", -1))

        target_w = int(round(work_w * 0.85))
        target_h = int(round(work_h * 0.85))

        init_w = max(1000, min(work_w, saved_w if saved_w >= 1000 else target_w))
        init_h = max(640, min(work_h, saved_h if saved_h >= 640 else target_h))

        if saved_x >= 0 and saved_y >= 0:
            init_x = max(work_x, min(work_x + work_w - init_w, saved_x))
            init_y = max(work_y, min(work_y + work_h - init_h, saved_y))
        else:
            init_x = work_x + max(0, (work_w - init_w) // 2)
            init_y = work_y + max(0, (work_h - init_h) // 2)

        self.geometry(f"{init_w}x{init_h}+{init_x}+{init_y}")
        self.minsize(1000, 640)

        # Apply central theme (System, Light, or Dark)
        self._effective_theme = theme.apply_global_theme(
            self,
            scale=self._current_scale,
            density=self._current_density,
            theme_mode=self._theme_mode,
        )

        # Install Ctrl word-edit shortcuts & wheel handlers application-wide
        apply_text_shortcuts(self)
        install_application_wheel_handler(self)

        self.event_ctx = EventContext()
        self._build_layout()
        self._apply_theme()
        self._bind_zoom_shortcuts()
        self._check_hardware_status()

        # Window resize & monitor DPI monitoring
        self._cfg_after_id = None
        self._last_window_dpi = theme.get_window_dpi(0)
        self.bind("<Configure>", self._on_window_configure)

        # Live system theme polling (every 2 seconds)
        self._poll_after_id = self.after(2000, self._poll_system_theme)

    def destroy(self):
        if getattr(self, "_poll_after_id", None):
            try:
                self.after_cancel(self._poll_after_id)
            except Exception:
                pass
            self._poll_after_id = None
        if getattr(self, "_cfg_after_id", None):
            try:
                self.after_cancel(self._cfg_after_id)
            except Exception:
                pass
            self._cfg_after_id = None
        super().destroy()

    def _bind_zoom_shortcuts(self):
        """Bind Ctrl++, Ctrl+-, Ctrl+0 to zoom scaling."""
        self.bind("<Control-plus>", lambda _e: self._increase_font_size())
        self.bind("<Control-equal>", lambda _e: self._increase_font_size())
        self.bind("<Control-minus>", lambda _e: self._decrease_font_size())
        self.bind("<Control-0>", lambda _e: self.set_ui_scale(1.0))
        self.bind("<Control-KP_Add>", lambda _e: self._increase_font_size())
        self.bind("<Control-KP_Subtract>", lambda _e: self._decrease_font_size())
        self.bind("<Control-KP_0>", lambda _e: self.set_ui_scale(1.0))

    def _open_text_size_dialog(self):
        theme.TextSizeDialog(self, current_scale=self._current_scale, on_apply=self.set_ui_scale)

    def _increase_font_size(self):
        new_scale = min(1.50, round(self._current_scale + 0.10, 2))
        self.set_ui_scale(new_scale)

    def _decrease_font_size(self):
        new_scale = max(0.85, round(self._current_scale - 0.10, 2))
        self.set_ui_scale(new_scale)

    def set_ui_scale(self, scale: float):
        """Apply dynamic UI font and row height scaling and persist preference in ui.ini."""
        self._current_scale = max(0.85, min(1.50, float(scale)))
        theme.apply_ui_scale(
            self,
            self._current_scale,
            density=self._current_density,
            theme_mode=self._theme_mode,
        )
        self.ui_prefs["text_scale"] = self._current_scale
        ui_settings.save_ui_settings(self.ui_prefs)
        self._refresh_tab_layouts()

    def set_table_density(self, density: str):
        """Set table density (compact 24px or comfortable 30px) and persist preference in ui.ini."""
        if density in ("compact", "comfortable"):
            self._current_density = density
            self.table_density_var.set(density)
            theme.apply_global_theme(
                self,
                scale=self._current_scale,
                density=self._current_density,
                theme_mode=self._theme_mode,
            )
            self.ui_prefs["table_density"] = self._current_density
            ui_settings.save_ui_settings(self.ui_prefs)
            self._refresh_tab_layouts()

    def set_theme_mode(self, mode: str):
        """Switch between 'system', 'light', and 'dark' modes and persist in ui.ini."""
        if mode in ("system", "light", "dark"):
            self._theme_mode = mode
            self.theme_mode_var.set(mode)
            self.ui_prefs["theme"] = mode
            ui_settings.save_ui_settings(self.ui_prefs)
            self._apply_theme()

    def _on_theme_mode_changed(self):
        self.set_theme_mode(self.theme_mode_var.get())

    def _poll_system_theme(self):
        """Check system dark/light mode registry setting every 2 seconds when in 'system' mode."""
        try:
            if not self.winfo_exists():
                return
        except Exception:
            return
        if self._theme_mode == "system":
            sys_theme = theme.get_windows_apps_theme()
            if sys_theme != self._effective_theme:
                self._apply_theme()
        try:
            self._poll_after_id = self.after(2000, self._poll_system_theme)
        except Exception:
            self._poll_after_id = None

    def _apply_theme(self):
        """Reconfigure global styles, colors, header bar, and tabs."""
        self._effective_theme = theme.apply_global_theme(
            self,
            scale=self._current_scale,
            density=self._current_density,
            theme_mode=self._theme_mode,
        )
        self.configure(bg=theme.COLOR_APP_BG)

        # Header bar colors
        if hasattr(self, "header_frame"):
            self.header_frame.configure(bg=theme.COLOR_HEADER_NAVY)
            self.title_lbl.configure(
                background=theme.COLOR_HEADER_NAVY,
                foreground=theme.COLOR_TITLE_WHITE,
            )
            self.subtitle_lbl.configure(
                background=theme.COLOR_HEADER_NAVY,
                foreground=theme.COLOR_SUBTITLE,
            )
            self.hdr_right.configure(bg=theme.COLOR_HEADER_NAVY)
            self.btn_a_minus.configure(
                bg=theme.COLOR_HEADER_NAVY,
                fg=theme.COLOR_TITLE_WHITE,
            )
            self.btn_a_plus.configure(
                bg=theme.COLOR_HEADER_NAVY,
                fg=theme.COLOR_TITLE_WHITE,
            )
            self.view_btn.configure(
                bg=theme.COLOR_CARD_BG,
                fg=theme.COLOR_TEXT_PRIMARY,
                activebackground=theme.COLOR_PRIMARY_BTN,
                activeforeground="#FFFFFF",
            )
            self.view_menu.configure(
                bg=theme.COLOR_CARD_BG,
                fg=theme.COLOR_TEXT_PRIMARY,
                activebackground=theme.COLOR_PRIMARY_BTN,
                activeforeground="#FFFFFF",
            )
            self.theme_seg.refresh_theme()

        # Status bar colors
        if hasattr(self, "status_bar"):
            self.status_bar.configure(
                bg=theme.COLOR_CARD_BG,
                highlightbackground=theme.COLOR_BORDER,
            )
            self.status_msg_lbl.configure(
                bg=theme.COLOR_CARD_BG,
                fg=theme.COLOR_TEXT_PRIMARY,
            )
            self.hw_status_lbl.configure(
                bg=theme.COLOR_CARD_BG,
                fg=theme.COLOR_TEXT_SECONDARY,
            )

        # Placeholder cards
        if hasattr(self, "card4"):
            self.card4.configure(bg=theme.COLOR_CARD_BG, highlightbackground=theme.COLOR_BORDER)
            self.lbl_card4_title.configure(bg=theme.COLOR_CARD_BG, fg=theme.COLOR_TITLE_WHITE if self._effective_theme == "dark" else theme.COLOR_HEADER_NAVY)
            self.lbl_card4_desc.configure(bg=theme.COLOR_CARD_BG, fg=theme.COLOR_TEXT_SECONDARY)
        if hasattr(self, "card5"):
            self.card5.configure(bg=theme.COLOR_CARD_BG, highlightbackground=theme.COLOR_BORDER)
            self.lbl_card5_title.configure(bg=theme.COLOR_CARD_BG, fg=theme.COLOR_TITLE_WHITE if self._effective_theme == "dark" else theme.COLOR_HEADER_NAVY)
            self.lbl_card5_desc.configure(bg=theme.COLOR_CARD_BG, fg=theme.COLOR_TEXT_SECONDARY)

        # Refresh tabs
        if hasattr(self, "stage2_tab") and hasattr(self.stage2_tab, "refresh_theme"):
            self.stage2_tab.refresh_theme()
        if hasattr(self, "stage3_tab") and hasattr(self.stage3_tab, "refresh_theme"):
            self.stage3_tab.refresh_theme()

    def _refresh_tab_layouts(self):
        """Trigger re-layout of active tab."""
        try:
            curr_tab = self.notebook.select()
            widget = self.notebook.nametowidget(curr_tab)
            if hasattr(widget, "_recompute_column_widths"):
                widget.after(10, widget._recompute_column_widths)
        except Exception:
            pass

    def reset_layout(self):
        """Reset text size to 100%, table density to comfortable, and clear custom column widths."""
        self._current_scale = 1.0
        self._current_density = "comfortable"
        self._theme_mode = "system"
        self.theme_mode_var.set("system")
        self.table_density_var.set("comfortable")
        self.ui_prefs = ui_settings.default_ui_settings()
        ui_settings.save_ui_settings(self.ui_prefs)
        self._apply_theme()
        messagebox.showinfo(
            "Layout Reset",
            "UI layout, theme, and font scaling reset to default (System theme, 100%, comfortable density).",
            parent=self,
        )

    def save_tree_column_widths(self, tree_key: str, tree: ttk.Treeview):
        """Save user-resized column widths for a specific Treeview."""
        try:
            widths = {}
            for col in tree["columns"]:
                w = tree.column(col, "width")
                if w:
                    widths[col] = int(w)
            if "column_widths" not in self.ui_prefs:
                self.ui_prefs["column_widths"] = {}
            self.ui_prefs["column_widths"][tree_key] = widths
            ui_settings.save_ui_settings(self.ui_prefs)
        except Exception:
            pass

    def restore_tree_column_widths(self, tree_key: str, tree: ttk.Treeview):
        """Restore saved column widths for a specific Treeview if available."""
        try:
            saved = self.ui_prefs.get("column_widths", {}).get(tree_key, {})
            for col, w in saved.items():
                if col in tree["columns"]:
                    tree.column(col, width=int(w))
        except Exception:
            pass

    def _on_window_configure(self, event):
        if event.widget == self:
            if self._cfg_after_id:
                try:
                    self.after_cancel(self._cfg_after_id)
                except Exception:
                    pass
            self._cfg_after_id = self.after(120, self._handle_window_resize_or_move)

    def _handle_window_resize_or_move(self):
        try:
            w = self.winfo_width()
            h = self.winfo_height()
            x = self.winfo_x()
            y = self.winfo_y()
            if w > 200 and h > 200:
                self.ui_prefs["window_width"] = w
                self.ui_prefs["window_height"] = h
                self.ui_prefs["window_x"] = x
                self.ui_prefs["window_y"] = y
                ui_settings.save_ui_settings(self.ui_prefs)

            # Check for monitor move / DPI change
            hwnd = self.winfo_id()
            curr_dpi = theme.get_window_dpi(hwnd)
            if curr_dpi > 0 and curr_dpi != self._last_window_dpi:
                self._last_window_dpi = curr_dpi
                self._refresh_tab_layouts()
        except Exception:
            pass

    def _build_layout(self):
        # Header banner with title, subtitle, and in-app controls
        self.header_frame = tk.Frame(self, bg=theme.COLOR_HEADER_NAVY, padx=16, pady=10)
        self.header_frame.pack(fill=tk.X)

        self.title_lbl = tk.Label(
            self.header_frame,
            text="SESSION SCRIBE",
            font="SS_Title",
            foreground=theme.COLOR_TITLE_WHITE,
            background=theme.COLOR_HEADER_NAVY,
        )
        self.title_lbl.pack(side=tk.LEFT)

        self.subtitle_lbl = tk.Label(
            self.header_frame,
            text="Audio Transcription, Diarization & Technical Documentation Pipeline",
            font="SS_Body",
            foreground=theme.COLOR_SUBTITLE,
            background=theme.COLOR_HEADER_NAVY,
        )
        self.subtitle_lbl.pack(side=tk.LEFT, padx=16, pady=(4, 0))

        # Right-aligned header controls: View dropdown, [System | Light | Dark], A-, A+
        self.hdr_right = tk.Frame(self.header_frame, bg=theme.COLOR_HEADER_NAVY)
        self.hdr_right.pack(side=tk.RIGHT)

        # Voice Vault Lock/Unlock status chip
        self.vault_chip = VaultStatusChip(self.hdr_right, self.vault)
        self.vault_chip.pack(side=tk.LEFT, padx=(0, 6))

        # Enroll Voice Profile action button
        self.btn_enroll = tk.Button(
            self.hdr_right,
            text="🎙️ Enroll Voice...",
            font="SS_BodyBold",
            bg=theme.COLOR_CARD_BG,
            fg=theme.COLOR_TEXT_PRIMARY,
            activebackground=theme.COLOR_PRIMARY_BTN,
            activeforeground="#FFFFFF",
            relief="solid",
            borderwidth=1,
            cursor="hand2",
            padx=8,
            pady=3,
            command=self._on_enroll_voice_click,
        )
        self.btn_enroll.pack(side=tk.LEFT, padx=(0, 6))

        # In-app themed Vault ▾ dropdown menu
        self.vault_btn = tk.Menubutton(
            self.hdr_right,
            text="Vault ▾",
            font="SS_BodyBold",
            bg=theme.COLOR_CARD_BG,
            fg=theme.COLOR_TEXT_PRIMARY,
            activebackground=theme.COLOR_PRIMARY_BTN,
            activeforeground="#FFFFFF",
            relief="solid",
            borderwidth=1,
            cursor="hand2",
            padx=10,
            pady=3,
        )
        self.vault_menu = tk.Menu(
            self.vault_btn,
            tearoff=0,
            font="SS_Body",
            bg=theme.COLOR_CARD_BG,
            fg=theme.COLOR_TEXT_PRIMARY,
            activebackground=theme.COLOR_PRIMARY_BTN,
            activeforeground="#FFFFFF",
        )
        self.vault_menu.add_command(label="Lock / Unlock Vault", command=self.vault_chip._toggle_or_prompt)
        self.vault_menu.add_command(label="Enroll Voice Profile...", command=self._on_enroll_voice_click)
        self.vault_menu.add_command(label="Event Speaker Roster...", command=self._open_event_roster_from_menu)
        self.vault_menu.add_separator()
        self.vault_menu.add_command(label="Quarantined Profiles (24h Hold)...", command=self._open_quarantine_manager_from_menu)
        self.vault_menu.add_command(label="Change Passphrase...", command=self._open_change_passphrase_from_menu)
        self.vault_menu.add_separator()
        self.vault_menu.add_command(label="Forgot Passphrase / Erase Vault...", command=self._open_forgot_passphrase_from_menu)
        self.vault_btn.configure(menu=self.vault_menu)
        self.vault_btn.pack(side=tk.LEFT, padx=(0, 6))

        # In-app themed View ▾ dropdown menu (replaces native OS menu bar)
        self.view_btn = tk.Menubutton(
            self.hdr_right,
            text="View ▾",
            font="SS_BodyBold",
            bg=theme.COLOR_CARD_BG,
            fg=theme.COLOR_TEXT_PRIMARY,
            activebackground=theme.COLOR_PRIMARY_BTN,
            activeforeground="#FFFFFF",
            relief="solid",
            borderwidth=1,
            cursor="hand2",
            padx=10,
            pady=3,
        )
        self.view_menu = tk.Menu(
            self.view_btn,
            tearoff=0,
            font="SS_Body",
            bg=theme.COLOR_CARD_BG,
            fg=theme.COLOR_TEXT_PRIMARY,
            activebackground=theme.COLOR_PRIMARY_BTN,
            activeforeground="#FFFFFF",
        )
        self.view_menu.add_command(
            label="Text size...",
            command=self._open_text_size_dialog,
        )
        self.view_menu.add_command(
            label="Zoom in (Ctrl++)",
            accelerator="Ctrl++",
            command=self._increase_font_size,
        )
        self.view_menu.add_command(
            label="Zoom out (Ctrl+-)",
            accelerator="Ctrl+-",
            command=self._decrease_font_size,
        )
        self.view_menu.add_command(
            label="Normal size (Ctrl+0)",
            accelerator="Ctrl+0",
            command=lambda: self.set_ui_scale(1.0),
        )
        self.view_menu.add_separator()

        density_menu = tk.Menu(
            self.view_menu,
            tearoff=0,
            font="SS_Body",
            bg=theme.COLOR_CARD_BG,
            fg=theme.COLOR_TEXT_PRIMARY,
            activebackground=theme.COLOR_PRIMARY_BTN,
            activeforeground="#FFFFFF",
        )
        self.view_menu.add_cascade(label="Table density", menu=density_menu)

        self.table_density_var = tk.StringVar(value=self._current_density)
        density_menu.add_radiobutton(
            label="Compact (24px)",
            variable=self.table_density_var,
            value="compact",
            command=lambda: self.set_table_density("compact"),
        )
        density_menu.add_radiobutton(
            label="Comfortable (30px)",
            variable=self.table_density_var,
            value="comfortable",
            command=lambda: self.set_table_density("comfortable"),
        )

        self.view_menu.add_separator()
        self.view_menu.add_command(
            label="Reset layout",
            command=self.reset_layout,
        )
        self.view_menu.add_separator()
        self.view_menu.add_command(
            label="About Session Scribe...",
            command=self._show_about_dialog,
        )
        self.view_btn.configure(menu=self.view_menu)
        self.view_btn.pack(side=tk.LEFT, padx=6)

        # About button in header
        self.btn_about = ttk.Button(
            self.hdr_right,
            text="About",
            command=self._show_about_dialog,
        )
        self.btn_about.pack(side=tk.LEFT, padx=(2, 6))

        # Window Menu Bar with Vault & Help menu cascades
        try:
            menubar = tk.Menu(self)
            vault_menu = tk.Menu(menubar, tearoff=0)
            vault_menu.add_command(label="Lock / Unlock Vault", command=self.vault_chip._toggle_or_prompt)
            vault_menu.add_command(label="Enroll Voice Profile...", command=self._on_enroll_voice_click)
            vault_menu.add_command(label="Event Speaker Roster...", command=self._open_event_roster_from_menu)
            vault_menu.add_separator()
            vault_menu.add_command(label="Quarantined Profiles (24h Hold)...", command=self._open_quarantine_manager_from_menu)
            vault_menu.add_command(label="Change Passphrase...", command=self._open_change_passphrase_from_menu)
            vault_menu.add_separator()
            vault_menu.add_command(label="Forgot Passphrase / Erase Vault...", command=self._open_forgot_passphrase_from_menu)
            menubar.add_cascade(label="Vault", menu=vault_menu)

            help_menu = tk.Menu(menubar, tearoff=0)
            help_menu.add_command(label="About Session Scribe", command=self._show_about_dialog)
            menubar.add_cascade(label="Help", menu=help_menu)
            self.config(menu=menubar)
        except Exception:
            pass

        # Segmented Control: [ System | Light | Dark ]
        self.theme_seg = theme.SegmentedControl(
            self.hdr_right,
            variable=self.theme_mode_var,
            options=[("System", "system"), ("Light", "light"), ("Dark", "dark")],
            command=self._on_theme_mode_changed,
        )
        self.theme_seg.pack(side=tk.LEFT, padx=6)

        # A- and A+ zoom buttons
        self.btn_a_minus = tk.Button(
            self.hdr_right,
            text="A−",
            font="SS_BodyBold",
            fg=theme.COLOR_TITLE_WHITE,
            bg=theme.COLOR_HEADER_NAVY,
            activebackground="#1E3A8A",
            activeforeground="#FFFFFF",
            relief="flat",
            cursor="hand2",
            padx=8,
            pady=2,
            command=self._decrease_font_size,
        )
        self.btn_a_minus.pack(side=tk.LEFT, padx=2)

        self.btn_a_plus = tk.Button(
            self.hdr_right,
            text="A+",
            font="SS_BodyBold",
            fg=theme.COLOR_TITLE_WHITE,
            bg=theme.COLOR_HEADER_NAVY,
            activebackground="#1E3A8A",
            activeforeground="#FFFFFF",
            relief="flat",
            cursor="hand2",
            padx=8,
            pady=2,
            command=self._increase_font_size,
        )
        self.btn_a_plus.pack(side=tk.LEFT, padx=2)

        # Main Notebook Tabs
        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

        # Tab 1: Stage 2 - Event Setup & Ingest
        self.stage2_tab = Stage2Tab(self.notebook, ctx=self.event_ctx, status_callback=self.set_status, vault=self.vault)
        self.notebook.add(self.stage2_tab, text=" Stage 2: Event Setup & Separation ")

        # Tab 2: Stage 3 - Transcription
        self.stage3_tab = Stage3Tab(self.notebook, ctx=self.event_ctx, status_callback=self.set_status, vault=self.vault)
        self.notebook.add(self.stage3_tab, text=" Stage 3: Transcription ")

        # Connect Stage 3 to Stage 2 for unsaved changes checks
        self.stage2_tab.set_stage3_tab(self.stage3_tab)

        # Intercept notebook tab changes to verify unsaved changes in Stage 3 and refit columns
        self.notebook.bind("<Button-1>", self._on_notebook_tab_click)
        self.notebook.bind("<<NotebookTabChanged>>", self._on_notebook_tab_changed)

        # Intercept app window close to verify unsaved changes in Stage 3
        self.protocol("WM_DELETE_WINDOW", self._on_app_close)

        # Tab 3: Stage 4 (Placeholder tab - clearly readable with '(soon)')
        self.stage4_frame = ttk.Frame(self.notebook, style="Card.TFrame", padding=30)
        self.card4 = tk.Frame(self.stage4_frame, bg=theme.COLOR_CARD_BG, highlightbackground=theme.COLOR_BORDER, highlightthickness=1, padx=24, pady=24)
        self.card4.pack(expand=True)
        self.lbl_card4_title = tk.Label(
            self.card4,
            text="Stage 4: OCR, Slides & Frame Extraction (soon)",
            font="SS_Section",
            foreground=theme.COLOR_HEADER_NAVY if self._effective_theme == "light" else theme.COLOR_TITLE_WHITE,
            background=theme.COLOR_CARD_BG,
        )
        self.lbl_card4_title.pack(pady=(0, 8))
        self.lbl_card4_desc = tk.Label(
            self.card4,
            text="Tesseract OCR + ffmpeg slide and visual element extraction pipeline will be integrated here.",
            font="SS_Body",
            foreground=theme.COLOR_TEXT_SECONDARY,
            background=theme.COLOR_CARD_BG,
            justify=tk.CENTER,
        )
        self.lbl_card4_desc.pack()
        self.notebook.add(self.stage4_frame, text=" Stage 4: OCR & Slides (soon) ")

        # Tab 4: Stage 5 (Placeholder tab - clearly readable with '(soon)')
        self.stage5_frame = ttk.Frame(self.notebook, style="Card.TFrame", padding=30)
        self.card5 = tk.Frame(self.stage5_frame, bg=theme.COLOR_CARD_BG, highlightbackground=theme.COLOR_BORDER, highlightthickness=1, padx=24, pady=24)
        self.card5.pack(expand=True)
        self.lbl_card5_title = tk.Label(
            self.card5,
            text="Stage 5: Combined Files & Technical Document Generation (soon)",
            font="SS_Section",
            foreground=theme.COLOR_HEADER_NAVY if self._effective_theme == "light" else theme.COLOR_TITLE_WHITE,
            background=theme.COLOR_CARD_BG,
        )
        self.lbl_card5_title.pack(pady=(0, 8))
        self.lbl_card5_desc = tk.Label(
            self.card5,
            text="Speaker-grouped technical documentation, timeline aggregation, and publication export will be generated here.",
            font="SS_Body",
            foreground=theme.COLOR_TEXT_SECONDARY,
            background=theme.COLOR_CARD_BG,
            justify=tk.CENTER,
        )
        self.lbl_card5_desc.pack()
        self.notebook.add(self.stage5_frame, text=" Stage 5: Tech Document (soon) ")

        # Bottom Status Bar
        self.status_bar = tk.Frame(
            self,
            bg=theme.COLOR_CARD_BG,
            highlightbackground=theme.COLOR_BORDER,
            highlightthickness=1,
            padx=12,
            pady=5,
        )
        self.status_bar.pack(fill=tk.X, side=tk.BOTTOM)

        self.status_msg_var = tk.StringVar(value="Ready.")
        self.status_msg_lbl = tk.Label(
            self.status_bar,
            textvariable=self.status_msg_var,
            font="SS_Body",
            bg=theme.COLOR_CARD_BG,
            fg=theme.COLOR_TEXT_PRIMARY,
        )
        self.status_msg_lbl.pack(side=tk.LEFT)

        self.hw_status_var = tk.StringVar(value="Checking Hardware...")
        self.hw_status_lbl = tk.Label(
            self.status_bar,
            textvariable=self.hw_status_var,
            font="SS_Body",
            bg=theme.COLOR_CARD_BG,
            fg=theme.COLOR_TEXT_SECONDARY,
        )
        self.hw_status_lbl.pack(side=tk.RIGHT)

    def _on_notebook_tab_click(self, event):
        try:
            elem = self.notebook.tk.call(self.notebook._w, "identify", "tab", event.x, event.y)
            if elem != "":
                target_idx = int(elem)
                current_idx = self.notebook.index("current")
                if target_idx != current_idx:
                    if not self.stage3_tab.check_unsaved_changes():
                        return "break"
        except Exception:
            pass

    def _on_notebook_tab_changed(self, event=None):
        try:
            curr_tab = self.notebook.select()
            widget = self.notebook.nametowidget(curr_tab)
            if hasattr(widget, "_recompute_column_widths"):
                widget.after(15, widget._recompute_column_widths)
            elif hasattr(widget, "on_tab_selected"):
                widget.on_tab_selected()
        except Exception:
            pass

    def _on_app_close(self):
        if not self.stage3_tab.check_unsaved_changes():
            return
        self.destroy()

    def _apply_app_icons(self):
        """Set window and taskbar icons using iconbitmap (.ico) and iconphoto (.png)."""
        ico_path = os.path.join(APP_ROOT, "assets", "icons", "session_scribe.ico")
        if os.path.isfile(ico_path):
            try:
                self.iconbitmap(ico_path)
            except Exception:
                pass

        png_path = os.path.join(APP_ROOT, "assets", "icons", "session_scribe_logo_1024.png")
        if os.path.isfile(png_path):
            try:
                from PIL import Image, ImageTk
                img = Image.open(png_path)
                img = img.resize((64, 64), Image.Resampling.LANCZOS)
                self._app_icon_photo = ImageTk.PhotoImage(img)
                self.iconphoto(True, self._app_icon_photo)
            except Exception:
                pass

    def _show_about_dialog(self):
        """Display 'About Session Scribe' dialog with version, author, and license."""
        dlg = tk.Toplevel(self)
        dlg.title("About Session Scribe")
        dlg.resizable(True, True)
        dlg.minsize(440, 320)
        dlg.transient(self)
        dlg.grab_set()

        card = tk.Frame(dlg, bg=theme.COLOR_CARD_BG, padx=24, pady=20)
        card.pack(fill=tk.BOTH, expand=True)

        btn_close = ttk.Button(card, text="Close", command=dlg.destroy, style="Primary.TButton")
        btn_close.pack(side=tk.BOTTOM, pady=(8, 0))

        if hasattr(self, "_app_icon_photo"):
            lbl_ico = tk.Label(card, image=self._app_icon_photo, bg=theme.COLOR_CARD_BG)
            lbl_ico.pack(pady=(0, 8))

        heading_fg = theme.COLOR_HEADER_NAVY if self._effective_theme == "light" else theme.COLOR_TITLE_WHITE
        tk.Label(card, text="Session Scribe", font="SS_Heading", fg=heading_fg, bg=theme.COLOR_CARD_BG).pack()
        tk.Label(card, text=f"Version {__version__}", font="SS_BodyBold", fg=theme.COLOR_TEXT_PRIMARY, bg=theme.COLOR_CARD_BG).pack(pady=(2, 6))
        tk.Label(card, text=f"Author: {__author__}", font="SS_Body", fg=theme.COLOR_TEXT_SECONDARY, bg=theme.COLOR_CARD_BG).pack()
        tk.Label(card, text=__copyright__, font="SS_Body", fg=theme.COLOR_TEXT_SECONDARY, bg=theme.COLOR_CARD_BG).pack(pady=(2, 4))
        tk.Label(card, text=f"License: {__license__}", font="SS_Body", fg=theme.COLOR_TEXT_MUTED, bg=theme.COLOR_CARD_BG).pack()
        lbl_desc = tk.Label(
            card,
            text="Speech Transcription & Multi-Speaker Analysis Pipeline",
            font="SS_Caption",
            fg=theme.COLOR_TEXT_MUTED,
            bg=theme.COLOR_CARD_BG,
            wraplength=400,
            justify=tk.CENTER,
        )
        lbl_desc.pack(pady=(10, 14), fill=tk.X)

        def _on_about_cfg(e):
            if e.widget == dlg and lbl_desc.winfo_exists():
                lbl_desc.configure(wraplength=max(200, e.width - 48))
        dlg.bind("<Configure>", _on_about_cfg)
        dlg.bind("<Escape>", lambda _e: dlg.destroy())

        theme.center_dialog(dlg, self, width=500, height=380, min_width=440, min_height=320)

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

    def _on_enroll_voice_click(self) -> None:
        """Launches voice profile enrollment, strictly enforcing unlocked vault state."""
        if not self.vault.is_initialized():
            dlg = InitializeVaultDialog(self, self.vault)
            self.wait_window(dlg)
            if not self.vault.is_unlocked():
                return
        elif not self.vault.is_unlocked():
            tk.messagebox.showinfo(
                "Voice Vault Locked",
                "Voice Vault is locked.\nPlease unlock the vault with your passphrase to enroll or manage voice profiles.",
                parent=self,
            )
            dlg = UnlockVaultDialog(self, self.vault)
            self.wait_window(dlg)
            if not self.vault.is_unlocked():
                return

        storage = VaultV2StorageEngine(self.vault)
        mgr = EnrollmentManager(storage)
        dlg = VoiceProfileEnrollmentDialog(self, mgr)
        self.wait_window(dlg)
        self.vault_chip.refresh()

    def _open_event_roster_from_menu(self):
        """Open Event Speaker Roster dialog from menu."""
        if hasattr(self, "stage2_tab"):
            self.stage2_tab._open_event_roster_dialog()

    def _open_quarantine_manager_from_menu(self):
        """Open Quarantined Profiles dialog from menu."""
        from scribe_core.quarantine_manager import QuarantineManager
        from gui.vault_dialog import QuarantineManagerDialog
        qm = QuarantineManager(self.vault)
        dlg = QuarantineManagerDialog(self, qm)
        self.wait_window(dlg)
        self.vault_chip.refresh()

    def _open_change_passphrase_from_menu(self):
        """Open Change Master Passphrase dialog from menu."""
        from gui.vault_dialog import ChangePassphraseDialog
        dlg = ChangePassphraseDialog(self, self.vault)
        self.wait_window(dlg)
        self.vault_chip.refresh()

    def _open_forgot_passphrase_from_menu(self):
        """Open Forgot Passphrase / Erase Vault dialog from menu."""
        from gui.vault_dialog import ForgotPassphraseDialog, InitializeVaultDialog
        def _after_erased():
            dlg = InitializeVaultDialog(self, self.vault)
            self.wait_window(dlg)
            self.vault_chip.refresh()

        dlg = ForgotPassphraseDialog(self, self.vault, on_erased=_after_erased)
        self.wait_window(dlg)
        self.vault_chip.refresh()


def launch_gui():
    from .app_logging import setup_app_logging, show_fatal_startup_error
    from .single_instance import SingleInstanceManager

    log_path = setup_app_logging()

    # Single-instance enforcement: focus running window and exit if secondary
    instance_mgr = SingleInstanceManager()
    if not instance_mgr.acquire():
        sys.exit(0)

    try:
        theme.make_dpi_aware()
        app = SessionScribeApp()

        # Register HWND with single instance manager
        try:
            hwnd = app.winfo_id()
            instance_mgr.register_window(hwnd)
        except Exception:
            pass

        # Cleanup on exit
        def on_destroy(event):
            if event.widget == app:
                instance_mgr.release()

        app.bind("<Destroy>", on_destroy, add="+")
        app.mainloop()
    except Exception as exc:
        show_fatal_startup_error(exc, log_path)
        instance_mgr.release()
        sys.exit(1)


if __name__ == "__main__":
    launch_gui()
