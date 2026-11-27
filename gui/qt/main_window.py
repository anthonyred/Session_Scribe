"""
Session Scribe — Qt Main Window Shell (PySide6)
Top Tab Navigation & Responsive Multi-Screen Shell:
- Top Tab Bar: Stage 2, Stage 3, Stage 4 (Soon), Stage 5 (Soon)
- Appearance Control (Light, Dark, System) with live switching
- System mode tracks Qt styleHints colorSchemeChanged / Windows app mode
- Top-right Settings Gear
- Adaptive geometry: opens at ~85% available screen (min 1100x680) and persists in ui.ini
- Inter-tab signaling: Stage 2 speaker renames sync cleanly with Stage 3
"""

import os
import sys
from typing import Any, Dict, Optional

from PySide6.QtCore import QEvent, QPoint, QRect, QSize, Qt
from PySide6.QtGui import QAction, QColor, QFont, QGuiApplication, QIcon, QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMenu,
    QMenuBar,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QTabBar,
    QVBoxLayout,
    QWidget,
)

from gui.qt.components import (
    AppearanceControl,
    Card,
    QtVaultChip,
    SecondaryButton,
    SegmentedControl,
    SettingsSheet,
    StatusPill,
    Toast,
    get_scaled_font,
)
from gui.qt.stage2_view import Stage2View
from gui.qt.stage3_view import Stage3View
from gui.qt.usage_view import UsageView
from gui.qt.tokens import (
    RADIUS_CARD,
    get_theme_palette,
    load_ui_settings,
    save_ui_settings,
)

DEFAULT_ORACLE_EVENT_DIR = r"D:\Events\22.Oracle event\scribe"


class SoonPlaceholderTab(QWidget):
    """Placeholder view for Stage 4 and Stage 5 upcoming features."""

    def __init__(self, stage_num: int, title: str, description: str, parent: Optional[QWidget] = None, theme_mode: str = "system", scale: float = 1.0):
        super().__init__(parent)
        self._theme_mode = theme_mode
        self._scale = scale

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setAlignment(Qt.AlignCenter)

        card = Card(self, shadow=False, theme_mode=theme_mode)
        card.setFixedSize(int(540 * scale), int(260 * scale))
        c_lay = QVBoxLayout(card)
        c_lay.setContentsMargins(24, 20, 24, 20)
        c_lay.setAlignment(Qt.AlignCenter)
        c_lay.setSpacing(12)

        pill = StatusPill(f"Stage {stage_num} (Coming Soon)", status_type="test", parent=card, scale=scale, theme_mode=theme_mode)
        c_lay.addWidget(pill, alignment=Qt.AlignCenter)

        lbl_t = QLabel(title, card)
        lbl_t.setFont(get_scaled_font("page_title", scale * 0.85))
        lbl_t.setAlignment(Qt.AlignCenter)
        c_lay.addWidget(lbl_t)

        lbl_d = QLabel(description, card)
        lbl_d.setWordWrap(True)
        lbl_d.setFont(get_scaled_font("body", scale))
        lbl_d.setAlignment(Qt.AlignCenter)
        c_lay.addWidget(lbl_d)

        layout.addWidget(card)

    def apply_theme(self, theme_mode: str = "system") -> None:
        self._theme_mode = theme_mode
        pal = get_theme_palette(theme_mode)
        self.setStyleSheet(f"QWidget {{ background-color: {pal['window_bg']}; color: {pal['primary_text']}; }}")
        for card in self.findChildren(Card):
            card.apply_theme(theme_mode)


class SessionScribeMainWindow(QMainWindow):
    """
    Main application shell with Top Tab Bar navigation and adaptive table support.
    """

    def __init__(self, event_scribe_dir: str = DEFAULT_ORACLE_EVENT_DIR, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.event_scribe_dir = os.path.abspath(event_scribe_dir)
        self._settings = load_ui_settings()
        self._theme_mode = self._settings.get("theme", "system")
        self._scale = float(self._settings.get("text_scale", 1.0))

        self.setWindowTitle("Session Scribe")

        self._init_window_geometry()

        self._central = QWidget(self)
        self.setCentralWidget(self._central)
        self._root_layout = QVBoxLayout(self._central)
        self._root_layout.setContentsMargins(0, 0, 0, 0)
        self._root_layout.setSpacing(0)

        self._build_menu_bar()
        self._build_top_header()
        self._build_tabbed_views()

        # Connect Qt System Color Scheme Change if available
        app = QGuiApplication.instance()
        if app and hasattr(app, "styleHints"):
            hints = app.styleHints()
            if hasattr(hints, "colorSchemeChanged"):
                hints.colorSchemeChanged.connect(self._on_system_scheme_changed)

        self.apply_theme(self._theme_mode)

    def _init_window_geometry(self) -> None:
        screen = QGuiApplication.primaryScreen()
        avail_geo = screen.availableGeometry() if screen else QRect(0, 0, 1920, 1080)

        target_w = int(avail_geo.width() * 0.85)
        target_h = int(avail_geo.height() * 0.85)

        saved_w = int(self._settings.get("window_width", target_w))
        saved_h = int(self._settings.get("window_height", target_h))
        saved_x = int(self._settings.get("window_x", -1))
        saved_y = int(self._settings.get("window_y", -1))

        final_w = max(1100, min(saved_w, avail_geo.width()))
        final_h = max(680, min(saved_h, avail_geo.height()))

        self.resize(final_w, final_h)
        self.setMinimumSize(1100, 680)

        if saved_x >= 0 and saved_y >= 0 and avail_geo.contains(saved_x, saved_y):
            self.move(saved_x, saved_y)
        else:
            cx = avail_geo.x() + (avail_geo.width() - final_w) // 2
            cy = avail_geo.y() + (avail_geo.height() - final_h) // 2
            self.move(cx, cy)

    def closeEvent(self, event) -> None:
        self._settings["window_width"] = self.width()
        self._settings["window_height"] = self.height()
        self._settings["window_x"] = self.x()
        self._settings["window_y"] = self.y()
        self._settings["theme"] = self._theme_mode
        save_ui_settings(self._settings)
        super().closeEvent(event)

    def _build_menu_bar(self) -> None:
        mb = self.menuBar()
        menu_view = mb.addMenu("&View")

        menu_theme = menu_view.addMenu("Appearance")
        act_sys = menu_theme.addAction("System (Follows Windows)")
        act_sys.triggered.connect(lambda: self.apply_theme("system"))
        act_light = menu_theme.addAction("Light")
        act_light.triggered.connect(lambda: self.apply_theme("light"))
        act_dark = menu_theme.addAction("Dark")
        act_dark.triggered.connect(lambda: self.apply_theme("dark"))

        menu_view.addSeparator()
        act_fit = menu_view.addAction("Fit Columns")
        act_fit.triggered.connect(self._fit_active_table)
        act_reset = menu_view.addAction("Reset Widths")
        act_reset.triggered.connect(self._reset_active_table)

    def _fit_active_table(self) -> None:
        if hasattr(self, "view_stage3") and hasattr(self.view_stage3, "table"):
            self.view_stage3.table.fit_columns()

    def _reset_active_table(self) -> None:
        if hasattr(self, "view_stage3") and hasattr(self.view_stage3, "table"):
            self.view_stage3.table.reset_widths()

    # ------------------------------------------------------------------
    # Top Navigation Header with Top Tab Bar, Appearance, & Settings Gear
    # ------------------------------------------------------------------
    def _build_top_header(self) -> None:
        self.header_bar = QFrame(self._central)
        h_lay = QHBoxLayout(self.header_bar)
        h_lay.setContentsMargins(16, 8, 16, 4)
        h_lay.setSpacing(14)

        # App Brand Title
        v_title = QVBoxLayout()
        v_title.setSpacing(0)
        lbl_logo = QLabel("Session Scribe", self.header_bar)
        lbl_logo.setFont(get_scaled_font("body_bold", self._scale * 1.15))
        v_title.addWidget(lbl_logo)
        h_lay.addLayout(v_title)

        h_lay.addSpacing(12)

        # Top Tab Bar (Part D: Stage 2, Stage 3, Usage, Stage 4 Soon, Stage 5 Soon)
        self.tab_bar = QTabBar(self.header_bar)
        self.tab_bar.setShape(QTabBar.RoundedNorth)
        self.tab_bar.setFont(get_scaled_font("body_bold", self._scale * 0.95))
        self.tab_bar.setCursor(Qt.PointingHandCursor)

        self.tab_bar.addTab("Stage 2")
        self.tab_bar.addTab("Stage 3")
        self.tab_bar.addTab("Usage")
        self.tab_bar.addTab("Stage 4 (Soon)")
        self.tab_bar.addTab("Stage 5 (Soon)")

        self.tab_bar.setCurrentIndex(1)  # Default to Stage 3
        self.tab_bar.currentChanged.connect(self._on_tab_changed)
        h_lay.addWidget(self.tab_bar)

        h_lay.addStretch(1)

        # Part C: Visible Appearance Control (Light, Dark, System) in Header
        self.appearance_ctrl = AppearanceControl(
            parent=self.header_bar,
            current_mode=self._theme_mode,
            scale=self._scale,
            theme_mode=self._theme_mode,
        )
        self.appearance_ctrl.appearanceChanged.connect(self.apply_theme)
        h_lay.addWidget(self.appearance_ctrl)

        # Voice Vault Status Chip
        self.vault_chip = QtVaultChip(
            parent=self.header_bar,
            scale=self._scale,
            theme_mode=self._theme_mode,
        )
        h_lay.addWidget(self.vault_chip)

        # Top Right Settings Gear Button
        self.btn_gear = SecondaryButton("⚙ Settings", self.header_bar, scale=self._scale * 0.95, theme_mode=self._theme_mode)
        self.btn_gear.setToolTip("Configure Color Theme, UI Scaling, Model Profiles, and Timeouts")
        self.btn_gear.clicked.connect(self._open_settings)
        h_lay.addWidget(self.btn_gear)

        self._root_layout.addWidget(self.header_bar)

    # ------------------------------------------------------------------
    # Tabbed Views Container & Inter-Tab Signal Wiring
    # ------------------------------------------------------------------
    def _build_tabbed_views(self) -> None:
        self.stack = QStackedWidget(self._central)

        # Tab 0: Stage 2
        self.view_stage2 = Stage2View(event_scribe_dir=self.event_scribe_dir, parent=self.stack, theme_mode=self._theme_mode, scale=self._scale)
        self.view_stage2.speakerRenamed.connect(self._on_speaker_renamed_in_stage2)
        self.stack.addWidget(self.view_stage2)

        # Tab 1: Stage 3 (Redesigned)
        self.view_stage3 = Stage3View(event_scribe_dir=self.event_scribe_dir, parent=self.stack, theme_mode=self._theme_mode, scale=self._scale)
        self.stack.addWidget(self.view_stage3)

        # Tab 2: Usage
        usage_acct_path = os.path.join(self.event_scribe_dir, "usage", "account_ledger.jsonl") if self.event_scribe_dir else None
        self.view_usage = UsageView(event_scribe_dir=self.event_scribe_dir, account_ledger_path=usage_acct_path, parent=self.stack, theme_mode=self._theme_mode, scale=self._scale)
        self.stack.addWidget(self.view_usage)

        # Tab 3: Stage 4 (Soon)
        self.view_stage4 = SoonPlaceholderTab(
            stage_num=4,
            title="Stage 4: Post-Processing & Audio Enhancement",
            description="Automated noise reduction, volume normalization, and intro/outro crossfades.",
            parent=self.stack,
            theme_mode=self._theme_mode,
            scale=self._scale,
        )
        self.stack.addWidget(self.view_stage4)

        # Tab 4: Stage 5 (Soon)
        self.view_stage5 = SoonPlaceholderTab(
            stage_num=5,
            title="Stage 5: Publishing & Feed Packaging",
            description="One-click packaging into RSS podcast feeds, chapter markers, and archive export.",
            parent=self.stack,
            theme_mode=self._theme_mode,
            scale=self._scale,
        )
        self.stack.addWidget(self.view_stage5)

        self.stack.setCurrentIndex(1)
        self._root_layout.addWidget(self.stack, stretch=1)

    # ------------------------------------------------------------------
    # Inter-Tab Handlers
    # ------------------------------------------------------------------
    def _on_tab_changed(self, index: int) -> None:
        self.stack.setCurrentIndex(index)

    def _on_speaker_renamed_in_stage2(self, old_name: str, new_name: str) -> None:
        self.view_stage3.set_event_dir(self.event_scribe_dir)
        Toast(f"Stage 3 synchronized with renamed speaker: '{new_name}'", self).show_toast(self)

    def _on_system_scheme_changed(self) -> None:
        """Called automatically by Qt when Windows toggles light/dark mode."""
        if self._theme_mode.lower() == "system":
            self.apply_theme("system")

    # ------------------------------------------------------------------
    # Theme Application
    # ------------------------------------------------------------------
    def apply_theme(self, theme_mode: str = "system") -> None:
        self._theme_mode = theme_mode
        self._settings["theme"] = theme_mode
        save_ui_settings(self._settings)

        pal = get_theme_palette(theme_mode)

        border_b = pal["card_border"]
        tab_hover_bg = "#3A3A3C" if (pal["window_bg"] == "#1C1C1E") else "#F0F4FA"

        self.setStyleSheet(
            f"""
            QMainWindow {{
                background-color: {pal['window_bg']};
                color: {pal['primary_text']};
            }}
            QMenuBar {{
                background-color: {pal['card_bg']};
                color: {pal['primary_text']};
                border-bottom: 1px solid {border_b};
            }}
            QMenuBar::item:selected {{
                background-color: {pal['row_hover']};
            }}
            QMenu {{
                background-color: {pal['card_bg']};
                color: {pal['primary_text']};
                border: 1px solid {pal['card_border']};
            }}
            QMenu::item:selected {{
                background-color: {pal['row_selected']};
                color: {pal['row_selected_text']};
            }}
            QFrame {{
                color: {pal['primary_text']};
            }}
            QTabBar::tab {{
                background: transparent;
                color: {pal['secondary_text']};
                padding: 8px 16px;
                border: none;
                border-bottom: 2px solid transparent;
                margin-right: 4px;
            }}
            QTabBar::tab:hover {{
                color: {pal['primary_text']};
                background: {tab_hover_bg};
                border-radius: 6px;
            }}
            QTabBar::tab:selected {{
                color: {pal['accent']};
                border-bottom: 2px solid {pal['accent']};
                font-weight: 600;
            }}
            """
        )

        self.header_bar.setStyleSheet(f"background-color: {pal['card_bg']}; border-bottom: 1px solid {border_b};")
        self.appearance_ctrl.apply_theme(theme_mode)
        if hasattr(self, "vault_chip"):
            self.vault_chip.apply_theme(theme_mode)
        self.btn_gear.apply_theme(theme_mode)

        self.view_stage2.apply_theme(theme_mode)
        self.view_stage3.apply_theme(theme_mode)
        if hasattr(self, "view_usage"):
            self.view_usage.apply_theme(theme_mode)
        self.view_stage4.apply_theme(theme_mode)
        self.view_stage5.apply_theme(theme_mode)

    def _open_settings(self) -> None:
        sheet = SettingsSheet(self, theme_mode=self._theme_mode)
        sheet.settingsChanged.connect(self._on_settings_applied)
        sheet.exec()

    def _on_settings_applied(self, settings: dict) -> None:
        new_theme = settings.get("theme", "system")
        new_scale = settings.get("text_scale", 1.0)
        self._scale = new_scale
        self.appearance_ctrl.set_current_mode(new_theme)
        self.apply_theme(new_theme)


def launch_main_window(event_dir: str = DEFAULT_ORACLE_EVENT_DIR):
    app = QApplication.instance() or QApplication(sys.argv)
    window = SessionScribeMainWindow(event_scribe_dir=event_dir)
    window.show()
    return app.exec()


if __name__ == "__main__":
    ev = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_ORACLE_EVENT_DIR
    launch_main_window(ev)
