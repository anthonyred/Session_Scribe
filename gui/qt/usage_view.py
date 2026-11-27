"""
Session Scribe — Usage and Quota Monitoring View (PySide6)
Apple- and Windows 11 Fluent-inspired Usage Tab:
- Scope selector (This Event / All Events) and Engine filter
- Summary cards: Runs & attempts, audio minutes (cloud & local), total time, requests/retries/429s, tokens, quota status, local compute stats
- Quota card with Rate Limit pace (default 40 req/min) and optional Manual Sync Credits mode
- Run history table (newest first)
- Clip attempt history with 'Open this transcript version' for archived versions
- Clean day-by-day Usage chart (audio minutes & requests) using QPainter for Light & Dark modes
- Adaptive column widths with zero horizontal scrollbar
"""

import csv
import json
import os
import sys
from datetime import datetime
from typing import Any, Dict, List, Optional

from PySide6.QtCore import QPoint, QRect, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from gui.qt.components import (
    AdaptiveTable,
    Card,
    PrimaryButton,
    SecondaryButton,
    SegmentedControl,
    StatusPill,
    TextButton,
    Toast,
    get_scaled_font,
)
from gui.qt.tokens import (
    FONT_FAMILY_PRIMARY,
    RADIUS_CARD,
    get_theme_palette,
    load_ui_settings,
    save_ui_settings,
)
from scribe_core.usage_ledger import DEFAULT_ACCOUNT_LEDGER_PATH, UsageLedger


# ----------------------------------------------------------------------
# Clean Day-by-Day Usage Chart (QPainter based, Light & Dark aware)
# ----------------------------------------------------------------------
class UsageBarChart(QWidget):
    """
    Renders daily audio minutes and cloud requests bar chart
    without requiring external charting packages.
    """

    def __init__(self, parent: Optional[QWidget] = None, theme_mode: str = "light", scale: float = 1.0):
        super().__init__(parent)
        self._theme_mode = theme_mode
        self._scale = scale
        self._daily_data: List[Dict[str, Any]] = []
        self.setFixedHeight(int(160 * max(1.0, scale * 0.95)))

    def set_data(self, daily_data: List[Dict[str, Any]]) -> None:
        self._daily_data = daily_data
        self.update()

    def apply_theme(self, theme_mode: str = "light") -> None:
        self._theme_mode = theme_mode
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        pal = get_theme_palette(self._theme_mode)
        w = self.width()
        h = self.height()

        # Background card
        painter.setPen(QColor(pal["card_border"]))
        painter.setBrush(QColor(pal["card_bg"]))
        painter.drawRoundedRect(0, 0, w - 1, h - 1, RADIUS_CARD, RADIUS_CARD)

        # Title
        painter.setPen(QColor(pal["primary_text"]))
        font_title = get_scaled_font("body_bold", self._scale * 0.95)
        painter.setFont(font_title)
        painter.drawText(16, 22, "Daily Usage Activity (Audio Minutes & Requests)")

        if not self._daily_data:
            painter.setPen(QColor(pal["secondary_text"]))
            painter.setFont(get_scaled_font("caption", self._scale))
            painter.drawText(QRect(0, 0, w, h), Qt.AlignCenter, "No usage records logged yet for this period.")
            return

        # Layout bars
        chart_top = 40
        chart_bottom = h - 30
        chart_h = chart_bottom - chart_top
        max_minutes = max(max(d.get("minutes", 0.0) for d in self._daily_data), 1.0)

        n = len(self._daily_data)
        bar_avail_w = (w - 60) / max(n, 1)
        bar_w = max(16, min(48, int(bar_avail_w * 0.65)))

        accent_col = QColor(pal["accent"])
        req_col = QColor(pal["accent_hover"]) if self._theme_mode == "dark" else QColor("#93C5FD")

        font_label = get_scaled_font("caption", self._scale * 0.9)
        painter.setFont(font_label)

        for i, d in enumerate(self._daily_data):
            x = 30 + int(i * bar_avail_w + (bar_avail_w - bar_w) / 2)
            mins = d.get("minutes", 0.0)
            reqs = d.get("requests", 0)
            bar_height = int((mins / max_minutes) * (chart_h - 10))

            y = chart_bottom - bar_height

            # Draw Audio Minutes bar
            painter.setPen(Qt.NoPen)
            painter.setBrush(accent_col)
            painter.drawRoundedRect(x, y, bar_w, bar_height, 4, 4)

            # Bar label (date)
            date_str = d.get("date", "")
            short_date = date_str[-5:] if len(date_str) >= 5 else date_str
            painter.setPen(QColor(pal["secondary_text"]))
            painter.drawText(x - 10, chart_bottom + 16, bar_w + 20, 16, Qt.AlignCenter, short_date)

            # Value label above bar
            val_text = f"{mins:.1f}m"
            painter.setPen(QColor(pal["primary_text"]))
            painter.drawText(x - 12, max(25, y - 4), bar_w + 24, 14, Qt.AlignCenter, val_text)


# ----------------------------------------------------------------------
# Usage View Widget
# ----------------------------------------------------------------------
class UsageView(QWidget):
    """
    Complete Usage & Quota Monitoring view for Session Scribe.
    """

    clipSelected = Signal(str)

    def __init__(
        self,
        event_scribe_dir: Optional[str] = None,
        account_ledger_path: Optional[str] = None,
        parent: Optional[QWidget] = None,
        theme_mode: str = "light",
        scale: float = 1.0,
    ):
        super().__init__(parent)
        self.event_scribe_dir = os.path.abspath(event_scribe_dir) if event_scribe_dir else None
        self._theme_mode = theme_mode
        self._scale = scale
        self._settings = load_ui_settings()

        acct_path = account_ledger_path or DEFAULT_ACCOUNT_LEDGER_PATH
        self.ledger = UsageLedger(
            event_scribe_dir=self.event_scribe_dir,
            account_ledger_path=acct_path,
        )

        self._active_scope = "event"  # 'event' or 'account'
        self._active_engine_filter = "All"

        self._build_ui()
        self.refresh_usage_data()

    def set_event_dir(self, event_scribe_dir: str) -> None:
        self.event_scribe_dir = os.path.abspath(event_scribe_dir)
        self.ledger = UsageLedger(
            event_scribe_dir=self.event_scribe_dir,
            account_ledger_path=DEFAULT_ACCOUNT_LEDGER_PATH,
        )
        self.refresh_usage_data()

    def apply_theme(self, theme_mode: str = "light") -> None:
        self._theme_mode = theme_mode
        pal = get_theme_palette(theme_mode)
        self.setStyleSheet(f"QWidget {{ background-color: {pal['window_bg']}; color: {pal['primary_text']}; }}")

        for card in self.findChildren(Card):
            card.apply_theme(theme_mode)
        for tbl in self.findChildren(AdaptiveTable):
            tbl.apply_theme(theme_mode)
        for btn in self.findChildren(PrimaryButton):
            btn.apply_theme(theme_mode)
        for btn in self.findChildren(SecondaryButton):
            btn.apply_theme(theme_mode)
        for btn in self.findChildren(TextButton):
            btn.apply_theme(theme_mode)
        for seg in self.findChildren(SegmentedControl):
            seg.apply_theme(theme_mode)
        if hasattr(self, "chart"):
            self.chart.apply_theme(theme_mode)

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(10)

        # 1. Top Controls Bar Card
        bar_card = Card(self, shadow=False, theme_mode=self._theme_mode)
        b_lay = QHBoxLayout(bar_card)
        b_lay.setContentsMargins(16, 10, 16, 10)
        b_lay.setSpacing(12)

        lbl_scope = QLabel("Scope:", bar_card)
        lbl_scope.setFont(get_scaled_font("body_bold", self._scale))
        b_lay.addWidget(lbl_scope)

        self.seg_scope = SegmentedControl(
            ["This Event", "All Events (Account)"],
            default_index=0,
            parent=bar_card,
            scale=self._scale,
            theme_mode=self._theme_mode,
        )
        self.seg_scope.selectionChanged.connect(self._on_scope_changed)
        b_lay.addWidget(self.seg_scope)

        b_lay.addSpacing(16)

        lbl_eng = QLabel("Engine Filter:", bar_card)
        lbl_eng.setFont(get_scaled_font("body", self._scale))
        b_lay.addWidget(lbl_eng)

        self.combo_eng = QComboBox(bar_card)
        self.combo_eng.addItems(["All", "Cloud Riva", "Local Whisper"])
        self.combo_eng.setFont(get_scaled_font("body", self._scale))
        self.combo_eng.currentTextChanged.connect(self._on_engine_filter_changed)
        b_lay.addWidget(self.combo_eng)

        b_lay.addStretch(1)

        btn_export = SecondaryButton("Export CSV", bar_card, scale=self._scale, theme_mode=self._theme_mode)
        btn_export.clicked.connect(self._export_csv)
        b_lay.addWidget(btn_export)

        btn_open_ledger = SecondaryButton("Open Ledger Folder", bar_card, scale=self._scale, theme_mode=self._theme_mode)
        btn_open_ledger.clicked.connect(self._open_ledger_folder)
        b_lay.addWidget(btn_open_ledger)

        layout.addWidget(bar_card)

        # 2. Scrollable Body
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll_content = QWidget()
        s_lay = QVBoxLayout(scroll_content)
        s_lay.setContentsMargins(0, 0, 0, 0)
        s_lay.setSpacing(10)

        # Summary Metrics Grid (Cards)
        s_lay.addLayout(self._build_summary_cards())

        # Usage Chart
        self.chart = UsageBarChart(scroll_content, theme_mode=self._theme_mode, scale=self._scale)
        s_lay.addWidget(self.chart)

        # Tables: Run History & Clip Attempts
        s_lay.addLayout(self._build_history_tables_section())

        scroll.setWidget(scroll_content)
        layout.addWidget(scroll)

    def _build_summary_cards(self) -> QHBoxLayout:
        cards_layout = QHBoxLayout()
        cards_layout.setSpacing(10)

        # Card 1: Pipeline Throughput
        card1 = Card(self, shadow=False, theme_mode=self._theme_mode)
        c1_lay = QVBoxLayout(card1)
        c1_lay.setContentsMargins(14, 10, 14, 10)
        c1_lay.setSpacing(4)
        c1_lay.addWidget(self._make_sec_title("Throughput & Time", card1))
        self.lbl_runs_attempts = QLabel("Runs: 0 | Attempts: 0", card1)
        self.lbl_audio_proc = QLabel("Audio: 0.0m cloud · 0.0m local", card1)
        self.lbl_total_time = QLabel("Total time taken: 0s", card1)
        for l in [self.lbl_runs_attempts, self.lbl_audio_proc, self.lbl_total_time]:
            l.setFont(get_scaled_font("body", self._scale))
            c1_lay.addWidget(l)
        cards_layout.addWidget(card1, stretch=1)

        # Card 2: Cloud Requests & Rate Limits
        card2 = Card(self, shadow=False, theme_mode=self._theme_mode)
        c2_lay = QVBoxLayout(card2)
        c2_lay.setContentsMargins(14, 10, 14, 10)
        c2_lay.setSpacing(4)
        c2_lay.addWidget(self._make_sec_title("Cloud Requests & Quota", card2))
        self.lbl_reqs_retries = QLabel("Requests: 0 · 429s: 0", card2)
        self.lbl_rate_pace = QLabel("Rate pace: 0 / 40 req/min", card2)
        self.lbl_tokens_note = QLabel("Tokens: n/a for Cloud Riva", card2)
        for l in [self.lbl_reqs_retries, self.lbl_rate_pace, self.lbl_tokens_note]:
            l.setFont(get_scaled_font("body", self._scale))
            c2_lay.addWidget(l)
        cards_layout.addWidget(card2, stretch=1)

        # Card 3: Local Compute & GPU Energy
        card3 = Card(self, shadow=False, theme_mode=self._theme_mode)
        c3_lay = QVBoxLayout(card3)
        c3_lay.setContentsMargins(14, 10, 14, 10)
        c3_lay.setSpacing(4)
        c3_lay.addWidget(self._make_sec_title("Local Compute (GPU)", card3))
        self.lbl_gpu_stats = QLabel("Decoding: 0s · Peak VRAM: 0 MB", card3)
        self.lbl_energy_stat = QLabel("Avg Power: 0 W · Energy: 0.00 Wh", card3)
        self.lbl_basis_tag = QLabel("[GPU only, estimated]", card3)
        self.lbl_basis_tag.setFont(get_scaled_font("caption", self._scale))
        self.lbl_basis_tag.setStyleSheet("color: #6B7280;")
        for l in [self.lbl_gpu_stats, self.lbl_energy_stat, self.lbl_basis_tag]:
            if l != self.lbl_basis_tag:
                l.setFont(get_scaled_font("body", self._scale))
            c3_lay.addWidget(l)
        cards_layout.addWidget(card3, stretch=1)

        return cards_layout

    def _build_history_tables_section(self) -> QVBoxLayout:
        sec_layout = QVBoxLayout()
        sec_layout.setSpacing(10)

        # Run History Table Card
        run_card = Card(self, shadow=False, theme_mode=self._theme_mode)
        r_lay = QVBoxLayout(run_card)
        r_lay.setContentsMargins(14, 10, 14, 10)
        r_lay.setSpacing(8)

        r_head = QHBoxLayout()
        r_head.addWidget(self._make_sec_title("Run History (Newest First)", run_card))
        r_head.addStretch(1)
        r_lay.addLayout(r_head)

        run_cols = ["Started", "Scope", "Trigger", "Engine", "Duration", "Audio", "Requests", "Tokens", "Outcome"]
        self.table_runs = AdaptiveTable(
            0, len(run_cols),
            parent=run_card,
            scale=self._scale,
            theme_mode=self._theme_mode,
            min_widths={0: 140, 1: 90, 2: 85, 3: 95, 4: 75, 5: 75, 6: 75, 7: 85, 8: 95},
        )
        self.table_runs.setHorizontalHeaderLabels(run_cols)
        self.table_runs.setFixedHeight(140)
        r_lay.addWidget(self.table_runs)
        sec_layout.addWidget(run_card)

        # Clip Attempt History Table Card
        clip_card = Card(self, shadow=False, theme_mode=self._theme_mode)
        c_lay = QVBoxLayout(clip_card)
        c_lay.setContentsMargins(14, 10, 14, 10)
        c_lay.setSpacing(8)

        c_head = QHBoxLayout()
        c_head.addWidget(self._make_sec_title("Clip Attempt History", clip_card))
        c_head.addSpacing(12)
        c_head.addWidget(QLabel("Select Clip:", clip_card))

        self.combo_clip_filter = QComboBox(clip_card)
        self.combo_clip_filter.addItem("All Clips")
        self.combo_clip_filter.currentTextChanged.connect(self._on_clip_filter_changed)
        c_head.addWidget(self.combo_clip_filter)
        c_head.addStretch(1)
        c_lay.addLayout(c_head)

        att_cols = ["#", "Clip", "Started", "Time Taken", "Engine", "Outcome", "Requests", "Tokens", "Failover", "Compute", "Transcript Action"]
        self.table_attempts = AdaptiveTable(
            0, len(att_cols),
            parent=clip_card,
            scale=self._scale,
            theme_mode=self._theme_mode,
            min_widths={0: 55, 1: 200, 2: 120, 3: 75, 4: 95, 5: 90, 6: 70, 7: 75, 8: 85, 9: 110, 10: 120},
        )
        self.table_attempts.setHorizontalHeaderLabels(att_cols)
        self.table_attempts.setFixedHeight(220)
        c_lay.addWidget(self.table_attempts)
        sec_layout.addWidget(clip_card)

        return sec_layout

    def _make_sec_title(self, text: str, parent: QWidget) -> QLabel:
        lbl = QLabel(text, parent)
        lbl.setFont(get_scaled_font("section_title", self._scale))
        return lbl

    # ------------------------------------------------------------------
    # Data Refresh & Metrics Calculation
    # ------------------------------------------------------------------
    def refresh_usage_data(self) -> None:
        records = (
            self.ledger.read_event_records()
            if self._active_scope == "event"
            else self.ledger.read_account_records()
        )

        # Apply engine filter
        if self._active_engine_filter != "All":
            ef = self._active_engine_filter.lower()
            records = [
                r for r in records
                if (r.get("engine") and ef in r.get("engine", "").lower())
                or (r.get("engine_start") and ef in r.get("engine_start", "").lower())
                or (r.get("engine_end") and ef in r.get("engine_end", "").lower())
            ]

        runs = [r for r in records if r.get("record_type") == "run"]
        attempts = [r for r in records if r.get("record_type") == "attempt"]

        # 1. Throughput calculations
        total_cloud_s = sum(a.get("audio_seconds_cloud", 0.0) for a in attempts)
        total_audio_s = sum(a.get("audio_seconds_clip", 0.0) for a in attempts)
        total_local_s = max(0.0, total_audio_s - total_cloud_s)
        total_time_taken = sum(a.get("time_taken_s", 0.0) for a in attempts)

        self.lbl_runs_attempts.setText(f"Runs: {len(runs)} | Attempts: {len(attempts)}")
        self.lbl_audio_proc.setText(f"Audio: {total_cloud_s / 60.0:.1f}m cloud · {total_local_s / 60.0:.1f}m local")
        m, s = divmod(int(total_time_taken), 60)
        self.lbl_total_time.setText(f"Total time taken: {m}m {s:02d}s")

        # 2. Cloud requests & rate pace
        req_metrics = self.ledger.get_rate_limit_metrics()
        total_reqs = sum(a.get("requests_count", 1) for a in attempts if a.get("engine_start") == "cloud")
        total_429s = sum(a.get("rate_limit_429_count", 0) for a in attempts)
        self.lbl_reqs_retries.setText(f"Requests: {total_reqs} · 429 hits: {total_429s}")
        self.lbl_rate_pace.setText(f"Rate pace: {req_metrics['requests_last_minute']} / {req_metrics['default_limit_per_min']} req/min (free tier)")

        # 3. Local compute
        local_attempts = [a for a in attempts if a.get("local_compute")]
        if local_attempts:
            tot_dec = sum(a["local_compute"].get("decoding_s", 0.0) for a in local_attempts)
            max_vram = max((a["local_compute"].get("peak_vram_mb", 0.0) for a in local_attempts), default=0.0)
            avg_pwr = sum(a["local_compute"].get("avg_power_w", 0.0) for a in local_attempts) / len(local_attempts)
            tot_energy = sum(a["local_compute"].get("est_energy_wh", 0.0) for a in local_attempts)
            self.lbl_gpu_stats.setText(f"Decoding: {tot_dec:.1f}s · Peak VRAM: {max_vram:.0f} MB")
            self.lbl_energy_stat.setText(f"Avg Power: {avg_pwr:.1f} W · Energy: {tot_energy:.3f} Wh")
        else:
            self.lbl_gpu_stats.setText("Decoding: 0s · Peak VRAM: —")
            self.lbl_energy_stat.setText("Avg Power: — · Energy: —")

        # 4. Populate daily chart data
        daily_map: Dict[str, Dict[str, Any]] = {}
        for a in attempts:
            ts = a.get("started_at") or a.get("ended_at")
            if not ts:
                continue
            date_key = ts[:10]
            if date_key not in daily_map:
                daily_map[date_key] = {"date": date_key, "minutes": 0.0, "requests": 0}
            daily_map[date_key]["minutes"] += a.get("audio_seconds_clip", 0.0) / 60.0
            daily_map[date_key]["requests"] += a.get("requests_count", 1)

        daily_sorted = sorted(daily_map.values(), key=lambda x: x["date"])
        if not daily_sorted:
            # Seed demo day if empty
            daily_sorted = [{"date": datetime.now().strftime("%Y-%m-%d"), "minutes": total_audio_s / 60.0, "requests": total_reqs}]
        self.chart.set_data(daily_sorted)

        # 5. Populate Run Table
        self._populate_runs_table(runs)

        # 6. Populate Clip Attempts Table
        self._populate_attempts_table(attempts)

    def _populate_runs_table(self, runs: List[Dict[str, Any]]) -> None:
        runs_sorted = sorted(runs, key=lambda x: x.get("started_at", ""), reverse=True)
        self.table_runs.setRowCount(len(runs_sorted))

        for r_idx, r in enumerate(runs_sorted):
            # Started
            t_str = r.get("started_at", "—")
            item_t = QTableWidgetItem(t_str[:16].replace("T", " ") if "T" in t_str else t_str)
            self.table_runs.setItem(r_idx, 0, item_t)

            # Scope
            self.table_runs.setItem(r_idx, 1, QTableWidgetItem(r.get("event_name", "Current Event")))

            # Trigger
            self.table_runs.setItem(r_idx, 2, QTableWidgetItem(r.get("trigger", "manual")))

            # Engine
            self.table_runs.setItem(r_idx, 3, QTableWidgetItem(r.get("engine", "Auto")))

            # Duration
            dur = r.get("duration_s", "—")
            self.table_runs.setItem(r_idx, 4, QTableWidgetItem(f"{dur}s" if isinstance(dur, (int, float)) else str(dur)))

            # Audio
            aud = r.get("audio_s", "—")
            self.table_runs.setItem(r_idx, 5, QTableWidgetItem(f"{aud}s" if isinstance(aud, (int, float)) else str(aud)))

            # Requests
            self.table_runs.setItem(r_idx, 6, QTableWidgetItem(str(r.get("requests_count", "—"))))

            # Tokens
            self.table_runs.setItem(r_idx, 7, QTableWidgetItem("n/a"))

            # Outcome
            out_str = r.get("outcome_summary", "completed")
            pill = StatusPill(out_str, status_type="success" if out_str == "completed" else "warning", theme_mode=self._theme_mode)
            self.table_runs.setCellWidget(r_idx, 8, pill)

        self.table_runs.adapt_columns_to_width()

    def _populate_attempts_table(self, attempts: List[Dict[str, Any]]) -> None:
        selected_clip = self.combo_clip_filter.currentText()
        if selected_clip and selected_clip != "All Clips":
            attempts = [a for a in attempts if a.get("clip_name") == selected_clip]

        # Populate combo choices
        existing_clips = sorted(list(set(a.get("clip_name") for a in attempts if a.get("clip_name"))))
        if self.combo_clip_filter.count() <= 1 and existing_clips:
            self.combo_clip_filter.blockSignals(True)
            for c in existing_clips:
                self.combo_clip_filter.addItem(c)
            self.combo_clip_filter.blockSignals(False)

        attempts_sorted = sorted(attempts, key=lambda x: (x.get("clip_name", ""), x.get("attempt_number", 0)), reverse=True)
        self.table_attempts.setRowCount(len(attempts_sorted))

        for r_idx, a in enumerate(attempts_sorted):
            # Attempt #
            att_num = str(a.get("attempt_number", 1))
            item_num = QTableWidgetItem(f"#{att_num}")
            item_num.setTextAlignment(Qt.AlignCenter)
            self.table_attempts.setItem(r_idx, 0, item_num)

            # Clip
            clip_name = a.get("clip_name", "—")
            item_clip = QTableWidgetItem(clip_name)
            item_clip.setToolTip(clip_name)
            self.table_attempts.setItem(r_idx, 1, item_clip)

            # Started
            t_str = a.get("started_at", "—")
            self.table_attempts.setItem(r_idx, 2, QTableWidgetItem(t_str[:16].replace("T", " ") if "T" in t_str else t_str))

            # Time Taken
            tt = a.get("time_taken_s", 0.0)
            self.table_attempts.setItem(r_idx, 3, QTableWidgetItem(f"{tt:.1f}s" if tt else "—"))

            # Engine
            eng = a.get("engine_start", "cloud")
            if a.get("engine_end") and a.get("engine_end") != eng:
                eng_disp = f"{eng} → {a.get('engine_end')}"
            else:
                eng_disp = eng
            self.table_attempts.setItem(r_idx, 4, QTableWidgetItem(eng_disp))

            # Outcome
            outc = a.get("outcome", "completed")
            pill_type = "success" if outc == "completed" else ("warning" if outc in ["failed", "resumed"] else "neutral")
            pill = StatusPill(outc, status_type=pill_type, theme_mode=self._theme_mode)
            self.table_attempts.setCellWidget(r_idx, 5, pill)

            # Requests
            self.table_attempts.setItem(r_idx, 6, QTableWidgetItem(str(a.get("requests_count", 1))))

            # Tokens
            out_toks = a.get("output_tokens", {})
            tok_count = out_toks.get("count") if isinstance(out_toks, dict) else None
            self.table_attempts.setItem(r_idx, 7, QTableWidgetItem(str(tok_count) if tok_count is not None else "n/a"))

            # Failover
            fo = a.get("failover")
            fo_str = f"{fo.get('resume_offset_s', 0)}s" if fo else "—"
            self.table_attempts.setItem(r_idx, 8, QTableWidgetItem(fo_str))

            # Compute
            lc = a.get("local_compute")
            lc_str = f"{lc.get('avg_power_w', 0)}W" if lc else "—"
            self.table_attempts.setItem(r_idx, 9, QTableWidgetItem(lc_str))

            # Action / Open Archived Version
            archived = a.get("archived_transcript")
            if archived:
                btn_open_ver = TextButton("Open Version", scale=self._scale, accent=True, theme_mode=self._theme_mode)
                btn_open_ver.clicked.connect(lambda ch=False, p=archived: self._open_archived_file(p))
                self.table_attempts.setCellWidget(r_idx, 10, btn_open_ver)
            else:
                self.table_attempts.setItem(r_idx, 10, QTableWidgetItem("Current"))

        self.table_attempts.adapt_columns_to_width()

    def _open_archived_file(self, rel_path: str) -> None:
        full_path = os.path.join(self.event_scribe_dir, rel_path) if self.event_scribe_dir else rel_path
        if os.path.isfile(full_path):
            os.startfile(full_path)
        else:
            Toast("Archived file not found", self).show_toast(self)

    def _on_scope_changed(self, idx: int, text: str) -> None:
        self._active_scope = "event" if idx == 0 else "account"
        self.refresh_usage_data()

    def _on_engine_filter_changed(self, text: str) -> None:
        self._active_engine_filter = text
        self.refresh_usage_data()

    def _on_clip_filter_changed(self, text: str) -> None:
        records = (
            self.ledger.read_event_records()
            if self._active_scope == "event"
            else self.ledger.read_account_records()
        )
        attempts = [r for r in records if r.get("record_type") == "attempt"]
        self._populate_attempts_table(attempts)

    def _export_csv(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Export Usage Ledger CSV", "usage_ledger_export.csv", "CSV Files (*.csv)")
        if not path:
            return

        records = self.ledger.read_event_records() if self._active_scope == "event" else self.ledger.read_account_records()
        if not records:
            Toast("No records to export", self).show_toast(self)
            return

        try:
            keys = set()
            for r in records:
                keys.update(r.keys())
            fieldnames = sorted(list(keys))

            with open(path, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                for r in records:
                    row = {k: (json.dumps(v) if isinstance(v, (dict, list)) else v) for k, v in r.items()}
                    writer.writerow(row)
            Toast(f"Exported {len(records)} records to CSV", self).show_toast(self)
        except Exception as e:
            Toast(f"Export failed: {e}", self).show_toast(self)

    def _open_ledger_folder(self) -> None:
        target_dir = self.ledger.event_ledger_dir if self._active_scope == "event" and self.ledger.event_ledger_dir else os.path.dirname(self.ledger.account_ledger_path)
        if target_dir and os.path.isdir(target_dir):
            os.startfile(target_dir)
        else:
            Toast(f"Ledger directory does not exist: {target_dir}", self).show_toast(self)

