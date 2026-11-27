"""
Session Scribe — Qt Design System Style Gallery
Interactive exhibition of all reusable design system components:
- Typography scale & font tokens
- Cards & elevation shadows
- Action buttons (Primary, Secondary, Text) in normal, hover, pressed, disabled states
- Segmented controls & smooth toggle switches
- High-contrast status pills & test pills (WCAG AA/AAA verified)
- Thin progress bar & toast notifications
- Interactive ClipsTable with middle-elision and TotalsBar
- Live text scaling slider (85% to 150%)
"""

import sys
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QPushButton,
    QScrollArea,
    QSlider,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from gui.qt.components import (
    Card,
    ClipsTable,
    MiddleElideDelegate,
    PrimaryButton,
    SecondaryButton,
    SegmentedControl,
    SettingsSheet,
    StatusPill,
    TestPill,
    TextButton,
    ThinProgressBar,
    Toast,
    ToggleSwitch,
    TotalsBar,
    get_scaled_font,
)
from gui.qt.tokens import (
    COLOR_CARD_BG,
    COLOR_PRIMARY_TEXT,
    COLOR_SECONDARY_TEXT,
    COLOR_WINDOW_BG,
    load_ui_settings,
    save_ui_settings,
    verify_all_token_contrasts,
)


class StyleGalleryWindow(QMainWindow):
    """Gallery exhibiting the complete Session Scribe Qt Design System."""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle("Session Scribe — Qt Design System & Style Gallery")
        self.resize(1180, 820)
        self.setMinimumSize(960, 640)

        self._settings = load_ui_settings()
        self._scale = self._settings.get("text_scale", 1.0)

        self._central = QWidget(self)
        self.setCentralWidget(self._central)

        self._root_layout = QVBoxLayout(self._central)
        self._root_layout.setContentsMargins(16, 16, 16, 16)
        self._root_layout.setSpacing(12)

        self._build_top_bar()
        self._build_scrollable_gallery()
        self.apply_theme()

    def apply_theme(self) -> None:
        self.setStyleSheet(
            f"""
            QMainWindow {{
                background-color: {COLOR_WINDOW_BG};
            }}
            """
        )

    def _build_top_bar(self) -> None:
        """Top bar with page title and text scale slider."""
        bar_card = Card(self._central, shadow=False)
        lay = QHBoxLayout(bar_card)
        lay.setContentsMargins(16, 12, 16, 12)

        # Title
        v_title = QVBoxLayout()
        lbl_title = QLabel("Design System Style Gallery", bar_card)
        lbl_title.setFont(get_scaled_font("page_title", self._scale))
        lbl_title.setStyleSheet(f"color: {COLOR_PRIMARY_TEXT}; border: none;")
        v_title.addWidget(lbl_title)

        lbl_sub = QLabel(
            "Apple- and Windows 11 Fluent-inspired tokens, contrast >= 4.5:1, and reusable components",
            bar_card,
        )
        lbl_sub.setFont(get_scaled_font("caption", self._scale))
        lbl_sub.setStyleSheet(f"color: {COLOR_SECONDARY_TEXT}; border: none;")
        v_title.addWidget(lbl_sub)
        lay.addLayout(v_title)

        lay.addStretch(1)

        # Scale slider controls
        v_scale = QVBoxLayout()
        h_scale_info = QHBoxLayout()
        lbl_zoom = QLabel("UI Scale:", bar_card)
        lbl_zoom.setFont(get_scaled_font("body_bold", self._scale))
        lbl_zoom.setStyleSheet("border: none;")
        h_scale_info.addWidget(lbl_zoom)

        self.lbl_scale_value = QLabel(f"{int(self._scale * 100)}%", bar_card)
        self.lbl_scale_value.setFont(get_scaled_font("body_bold", self._scale))
        self.lbl_scale_value.setStyleSheet("color: #0A64D6; border: none;")
        h_scale_info.addWidget(self.lbl_scale_value)
        v_scale.addLayout(h_scale_info)

        self.slider_scale = QSlider(Qt.Horizontal, bar_card)
        self.slider_scale.setRange(85, 150)
        self.slider_scale.setValue(int(self._scale * 100))
        self.slider_scale.setFixedWidth(160)
        self.slider_scale.valueChanged.connect(self._on_scale_slider_changed)
        v_scale.addWidget(self.slider_scale)

        lay.addLayout(v_scale)

        btn_settings = SecondaryButton("Open Settings Sheet", bar_card, scale=self._scale)
        btn_settings.clicked.connect(self._open_settings_sheet)
        lay.addWidget(btn_settings)

        self._root_layout.addWidget(bar_card)

    def _build_scrollable_gallery(self) -> None:
        scroll = QScrollArea(self._central)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        self._gallery_widget = QWidget()
        self._gallery_layout = QVBoxLayout(self._gallery_widget)
        self._gallery_layout.setContentsMargins(0, 0, 0, 0)
        self._gallery_layout.setSpacing(14)

        # Section 1: Typography Scale
        self._add_typography_section()

        # Section 2: Buttons & Interactive Controls
        self._add_buttons_section()

        # Section 3: Status & Test Pills
        self._add_pills_section()

        # Section 4: Progress Bar & Toast
        self._add_progress_and_feedback_section()

        # Section 5: Clips Table with Totals Bar
        self._add_table_section()

        scroll.setWidget(self._gallery_widget)
        self._root_layout.addWidget(scroll)

    def _add_typography_section(self) -> None:
        card = Card(self._gallery_widget)
        lay = QVBoxLayout(card)
        lay.setContentsMargins(18, 16, 18, 16)
        lay.setSpacing(8)

        sec_lbl = QLabel("Typography Scale (Segoe UI Variable / Segoe UI)", card)
        sec_lbl.setFont(get_scaled_font("section_title", self._scale))
        sec_lbl.setStyleSheet(f"color: {COLOR_PRIMARY_TEXT}; border: none;")
        lay.addWidget(sec_lbl)

        samples = [
            ("Page Title (22pt Semibold)", "page_title", "Session Scribe — Stage 3"),
            ("Section Title (13pt Semibold)", "section_title", "Audio Pipeline & Active Clips"),
            ("Body (10.5pt Regular)", "body", "Transcription progress completes automatically with resume checkpoints."),
            ("Body Bold (10.5pt Semibold)", "body_bold", "High-priority batch actions and verified speakers."),
            ("Table Cell (10pt Regular)", "table", "20261031_143000_RoomA_Keynote_Part1.mp4"),
            ("Caption (9pt Regular)", "caption", "Last checkpoint saved at 70.0s · Failover latency 0.0s"),
        ]

        grid = QGridLayout()
        grid.setSpacing(8)
        for row, (role_name, role_key, sample_text) in enumerate(samples):
            lbl_role = QLabel(role_name, card)
            lbl_role.setFont(get_scaled_font("caption", self._scale))
            lbl_role.setStyleSheet(f"color: {COLOR_SECONDARY_TEXT}; border: none;")
            grid.addWidget(lbl_role, row, 0)

            lbl_sample = QLabel(sample_text, card)
            lbl_sample.setFont(get_scaled_font(role_key, self._scale))
            lbl_sample.setStyleSheet(f"color: {COLOR_PRIMARY_TEXT}; border: none;")
            grid.addWidget(lbl_sample, row, 1)

        lay.addLayout(grid)
        self._gallery_layout.addWidget(card)

    def _add_buttons_section(self) -> None:
        card = Card(self._gallery_widget)
        lay = QVBoxLayout(card)
        lay.setContentsMargins(18, 16, 18, 16)
        lay.setSpacing(12)

        sec_lbl = QLabel("Buttons & Interactive Controls", card)
        sec_lbl.setFont(get_scaled_font("section_title", self._scale))
        sec_lbl.setStyleSheet(f"color: {COLOR_PRIMARY_TEXT}; border: none;")
        lay.addWidget(sec_lbl)

        # Primary Buttons (Normal, Disabled)
        row1 = QHBoxLayout()
        row1.addWidget(QLabel("Primary Buttons:", card))
        btn_p1 = PrimaryButton("Transcribe All (Normal)", card, scale=self._scale)
        row1.addWidget(btn_p1)
        btn_p2 = PrimaryButton("Transcribing... (Disabled)", card, scale=self._scale)
        btn_p2.setEnabled(False)
        row1.addWidget(btn_p2)
        row1.addStretch(1)
        lay.addLayout(row1)

        # Secondary Buttons
        row2 = QHBoxLayout()
        row2.addWidget(QLabel("Secondary Buttons:", card))
        btn_s1 = SecondaryButton("Approve Selected", card, scale=self._scale)
        row2.addWidget(btn_s1)
        btn_s2 = SecondaryButton("Disabled State", card, scale=self._scale)
        btn_s2.setEnabled(False)
        row2.addWidget(btn_s2)
        row2.addStretch(1)
        lay.addLayout(row2)

        # Text / Ghost Buttons
        row3 = QHBoxLayout()
        row3.addWidget(QLabel("Text Buttons:", card))
        btn_t1 = TextButton("Clear Approval", card, scale=self._scale, accent=False)
        row3.addWidget(btn_t1)
        btn_t2 = TextButton("Combine Transcripts →", card, scale=self._scale, accent=True)
        row3.addWidget(btn_t2)
        row3.addStretch(1)
        lay.addLayout(row3)

        # Segmented Control & Switch
        row4 = QHBoxLayout()
        row4.addWidget(QLabel("Segmented Engine Selector:", card))
        seg = SegmentedControl(
            ["Local Whisper", "Cloud Riva", "Auto (Hybrid)"],
            default_index=2,
            parent=card,
            scale=self._scale,
        )
        row4.addWidget(seg)
        row4.addSpacing(24)

        row4.addWidget(QLabel("Live Progress:", card))
        sw = ToggleSwitch(card, checked=True)
        row4.addWidget(sw)
        row4.addStretch(1)
        lay.addLayout(row4)

        self._gallery_layout.addWidget(card)

    def _add_pills_section(self) -> None:
        card = Card(self._gallery_widget)
        lay = QVBoxLayout(card)
        lay.setContentsMargins(18, 16, 18, 16)
        lay.setSpacing(12)

        sec_lbl = QLabel("Status Pills & Test Pills (WCAG AA >= 4.5:1 Verified)", card)
        sec_lbl.setFont(get_scaled_font("section_title", self._scale))
        sec_lbl.setStyleSheet(f"color: {COLOR_PRIMARY_TEXT}; border: none;")
        lay.addWidget(sec_lbl)

        row = QHBoxLayout()
        row.setSpacing(10)

        pill_success = StatusPill("Done", status_type="success", parent=card, scale=self._scale)
        row.addWidget(pill_success)

        pill_warn = StatusPill("Failover", status_type="warning", parent=card, scale=self._scale)
        row.addWidget(pill_warn)

        pill_err = StatusPill("Error", status_type="error", parent=card, scale=self._scale)
        row.addWidget(pill_err)

        pill_info = StatusPill("Transcribing", status_type="info", parent=card, scale=self._scale)
        row.addWidget(pill_info)

        pill_neut = StatusPill("Ready", status_type="neutral", parent=card, scale=self._scale)
        row.addWidget(pill_neut)

        test_pill = TestPill(parent=card, scale=self._scale)
        row.addWidget(test_pill)

        row.addStretch(1)
        lay.addLayout(row)

        self._gallery_layout.addWidget(card)

    def _add_progress_and_feedback_section(self) -> None:
        card = Card(self._gallery_widget)
        lay = QVBoxLayout(card)
        lay.setContentsMargins(18, 16, 18, 16)
        lay.setSpacing(10)

        sec_lbl = QLabel("Progress & Non-Modal Toast Feedback", card)
        sec_lbl.setFont(get_scaled_font("section_title", self._scale))
        sec_lbl.setStyleSheet(f"color: {COLOR_PRIMARY_TEXT}; border: none;")
        lay.addWidget(sec_lbl)

        lbl_p = QLabel("Thin Progress Bar (4px height):", card)
        lbl_p.setFont(get_scaled_font("caption", self._scale))
        lay.addWidget(lbl_p)

        prog = ThinProgressBar(card)
        prog.setValue(68)
        lay.addWidget(prog)

        h_toast = QHBoxLayout()
        btn_toast = SecondaryButton("Trigger Floating Toast Notification", card, scale=self._scale)
        btn_toast.clicked.connect(self._trigger_toast)
        h_toast.addWidget(btn_toast)
        h_toast.addStretch(1)
        lay.addLayout(h_toast)

        self._gallery_layout.addWidget(card)

    def _add_table_section(self) -> None:
        card = Card(self._gallery_widget)
        lay = QVBoxLayout(card)
        lay.setContentsMargins(18, 16, 18, 16)
        lay.setSpacing(8)

        sec_lbl = QLabel("Clips Table with Middle-Elision & Docked Totals Bar", card)
        sec_lbl.setFont(get_scaled_font("section_title", self._scale))
        sec_lbl.setStyleSheet(f"color: {COLOR_PRIMARY_TEXT}; border: none;")
        lay.addWidget(sec_lbl)

        # 4-row demonstration table
        self.table = ClipsTable(4, 9, parent=card, scale=self._scale)
        headers = [
            "#",
            "Clip",
            "Speaker",
            "Duration",
            "Time taken",
            "Failover time",
            "Status",
            "Approval",
            "Engine",
        ]
        self.table.setHorizontalHeaderLabels(headers)

        # Populate sample data
        sample_rows = [
            (
                "1",
                "20261031_143000_RoomA_Jehosheba_Amruthaluri_Part1_Intro.mp4",
                "Jehosheba Amruthaluri",
                "03:15",
                "00:18",
                "-",
                ("Done", "success"),
                "✓ Approved",
                "Cloud Riva",
            ),
            (
                "2 [TEST]",
                "20261031_144000_RoomA_Surender_Tanwar_Keynote_Speech.mp4",
                "Surender Tanwar",
                "05:42",
                "00:34",
                "-",
                ("Done", "success"),
                "✓ Approved",
                "Cloud Riva",
            ),
            (
                "3",
                "20261031_145500_RoomA_Madhav_Poosarla_Architecture_Deep_Dive.mp4",
                "Madhav Poosarla",
                "08:10",
                "01:05",
                "00:12",
                ("Failover", "warning"),
                "✓ Approved",
                "Local Whisper",
            ),
            (
                "4",
                "20261031_151500_RoomA_Kishore_Katta_Closing_Session_Panel.mp4",
                "Kishore Katta",
                "04:20",
                "00:26",
                "-",
                ("Done", "success"),
                "✓ Approved",
                "Cloud Riva",
            ),
        ]

        for r_idx, row_data in enumerate(sample_rows):
            for c_idx in range(9):
                val = row_data[c_idx]
                if c_idx == 6:  # Status Pill
                    pill_text, p_type = val
                    pill = StatusPill(pill_text, status_type=p_type, parent=self.table, scale=self._scale)
                    self.table.setCellWidget(r_idx, c_idx, pill)
                else:
                    item = QTableWidgetItem(str(val))
                    if c_idx in [0, 3, 4, 5, 7, 8]:
                        item.setTextAlignment(Qt.AlignCenter)
                    self.table.setItem(r_idx, c_idx, item)

        # Configure responsive column sizing
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        header.setSectionResizeMode(2, QHeaderView.Interactive)
        header.resizeSection(2, 170)
        for i in range(3, 9):
            header.setSectionResizeMode(i, QHeaderView.ResizeToContents)

        self.table.setFixedHeight(180)
        lay.addWidget(self.table)

        # Docked Totals Bar
        self.totals_bar = TotalsBar(card, scale=self._scale)
        self.totals_bar.update_totals(
            clip_count=4,
            total_duration_str="21m 27s",
            total_time_taken_str="02m 23s",
            total_failover_str="00m 12s",
            approved_count=4,
        )
        lay.addWidget(self.totals_bar)

        self._gallery_layout.addWidget(card)

    def _on_scale_slider_changed(self, val: int) -> None:
        self.lbl_scale_value.setText(f"{val}%")
        self._scale = val / 100.0
        self._settings["text_scale"] = self._scale
        save_ui_settings(self._settings)

    def _trigger_toast(self) -> None:
        toast = Toast("Transcripts combined successfully to 22.Oracle event/combined", self)
        toast.show_toast(self)

    def _open_settings_sheet(self) -> None:
        sheet = SettingsSheet(self)
        sheet.settingsChanged.connect(self._on_settings_applied)
        sheet.exec()

    def _on_settings_applied(self, settings: dict) -> None:
        new_scale = settings.get("text_scale", 1.0)
        self.slider_scale.setValue(int(new_scale * 100))


def run_style_gallery():
    app = QApplication.instance() or QApplication(sys.argv)
    window = StyleGalleryWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    run_style_gallery()
