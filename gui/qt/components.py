"""
Session Scribe — Qt Design System Components (PySide6)
Apple- and Windows 11 Fluent-inspired reusable UI components with Light & Dark mode:
- Card (framed with soft drop shadow & rounded corners)
- SegmentedControl (pill container with active indicator)
- ToggleSwitch (smooth animated switch)
- Buttons (PrimaryButton, SecondaryButton, TextButton)
- StatusPill & TestPill (high-contrast status badges for Light & Dark)
- Toast (non-modal feedback message)
- ThinProgressBar (sleek 4px progress line)
- AdaptiveTable & TotalsBar (custom styled table with middle-elision, proportional stretch, and zero horizontal scrollbar)
- SettingsSheet (slide-in / modal configuration sheet with Light/Dark/System theme switcher)
"""

import sys
from typing import Any, Dict, List, Optional, Tuple

from PySide6.QtCore import (
    QEasingCurve,
    QEvent,
    QPoint,
    QPropertyAnimation,
    QRect,
    QRectF,
    QSize,
    QTimer,
    Qt,
    Signal,
)
from PySide6.QtGui import (
    QAction,
    QBrush,
    QColor,
    QFont,
    QFontMetrics,
    QPainter,
    QPainterPath,
    QPen,
    QTextOption,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFrame,
    QGraphicsDropShadowEffect,
    QGraphicsOpacityEffect,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMenu,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QStyle,
    QStyledItemDelegate,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from gui.qt.tokens import (
    BASE_FONT_SIZES,
    DURATION_FADE_MS,
    DURATION_SLIDE_MS,
    FONT_FAMILY_CODE,
    FONT_FAMILY_PRIMARY,
    PALETTE_DARK,
    PALETTE_LIGHT,
    RADIUS_BUTTON,
    RADIUS_CARD,
    RADIUS_INPUT,
    RADIUS_PILL,
    ROW_HEIGHT_COMFORTABLE,
    ROW_HEIGHT_COMPACT,
    get_theme_palette,
    load_ui_settings,
    save_ui_settings,
)


def get_scaled_font(role: str = "body", scale: float = 1.0) -> QFont:
    """Return a scaled QFont based on typography tokens and text scale factor."""
    font = QFont(FONT_FAMILY_PRIMARY.split(",")[0].strip())
    base_size = BASE_FONT_SIZES.get(role, 10.5)
    font.setPointSizeF(base_size * scale)
    if "title" in role or "bold" in role:
        font.setWeight(QFont.DemiBold)
    else:
        font.setWeight(QFont.Normal)
    return font


# ----------------------------------------------------------------------
# 1. Card Widget (Light & Dark Theme)
# ----------------------------------------------------------------------
class Card(QFrame):
    """
    Framed card widget with 12px border radius, subtle border,
    and soft ambient elevation shadow for both Light and Dark themes.
    """

    def __init__(self, parent: Optional[QWidget] = None, shadow: bool = True, theme_mode: str = "light"):
        super().__init__(parent)
        self.setObjectName("FluentCard")
        self._shadow = shadow
        self._theme_mode = theme_mode
        self.apply_theme(theme_mode)

    def apply_theme(self, theme_mode: str = "light") -> None:
        self._theme_mode = theme_mode
        pal = get_theme_palette(theme_mode)
        self.setStyleSheet(
            f"""
            QFrame#FluentCard {{
                background-color: {pal['card_bg']};
                border: 1px solid {pal['card_border']};
                border-radius: {RADIUS_CARD}px;
            }}
            """
        )
        if self._shadow:
            effect = QGraphicsDropShadowEffect(self)
            effect.setBlurRadius(16)
            effect.setOffset(0, 2)
            shadow_alpha = 40 if theme_mode == "dark" else 14
            effect.setColor(QColor(0, 0, 0, shadow_alpha))
            self.setGraphicsEffect(effect)


# ----------------------------------------------------------------------
# 2. Segmented Control
# ----------------------------------------------------------------------
class SegmentedControl(QWidget):
    """
    Pill-shaped segmented selection control with animated or styled
    active segment indicator.
    """

    selectionChanged = Signal(int, str)

    def __init__(
        self,
        options: List[str],
        default_index: int = 0,
        parent: Optional[QWidget] = None,
        scale: float = 1.0,
        theme_mode: str = "light",
    ):
        super().__init__(parent)
        self._options = options
        self._current_index = default_index
        self._buttons: List[QPushButton] = []
        self._scale = scale
        self._theme_mode = theme_mode

        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(3, 3, 3, 3)
        self._layout.setSpacing(2)

        for idx, text in enumerate(options):
            btn = QPushButton(text, self)
            btn.setCheckable(True)
            btn.setFlat(True)
            btn.setFont(get_scaled_font("body", self._scale))
            btn.setCursor(Qt.PointingHandCursor)
            btn.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
            btn.setFixedHeight(int(28 * max(1.0, self._scale * 0.95)))
            btn.clicked.connect(lambda checked=False, i=idx: self.set_current_index(i))
            self._buttons.append(btn)
            self._layout.addWidget(btn)

        self.apply_theme(theme_mode)

    def set_current_index(self, index: int) -> None:
        if 0 <= index < len(self._options):
            self._current_index = index
            self._update_styles()
            self.selectionChanged.emit(index, self._options[index])

    def current_index(self) -> int:
        return self._current_index

    def current_text(self) -> str:
        return self._options[self._current_index]

    def apply_theme(self, theme_mode: str = "light") -> None:
        self._theme_mode = theme_mode
        pal = get_theme_palette(theme_mode)
        bg = "#3A3A3C" if theme_mode == "dark" else "#EAEAEF"
        border = "#48484A" if theme_mode == "dark" else "#DFDFE5"
        self.setStyleSheet(
            f"""
            QWidget {{
                background-color: {bg};
                border: 1px solid {border};
                border-radius: {RADIUS_BUTTON + 2}px;
            }}
            """
        )
        self._update_styles()

    def _update_styles(self) -> None:
        pal = get_theme_palette(self._theme_mode)
        for idx, btn in enumerate(self._buttons):
            is_active = idx == self._current_index
            btn.setChecked(is_active)
            if is_active:
                btn.setStyleSheet(
                    f"""
                    QPushButton {{
                        background-color: {pal['accent']};
                        color: {pal['accent_text']};
                        border: none;
                        border-radius: {RADIUS_BUTTON}px;
                        font-weight: 600;
                        padding: 3px 12px;
                    }}
                    """
                )
            else:
                btn.setStyleSheet(
                    f"""
                    QPushButton {{
                        background-color: transparent;
                        color: {pal['secondary_text']};
                        border: none;
                        border-radius: {RADIUS_BUTTON}px;
                        padding: 3px 12px;
                    }}
                    QPushButton:hover {{
                        color: {pal['primary_text']};
                        background-color: rgba(255, 255, 255, 0.12);
                    }}
                    """
                )


# ----------------------------------------------------------------------
# 3. Animated Toggle Switch
# ----------------------------------------------------------------------
class ToggleSwitch(QWidget):
    """
    Smooth, Apple/Fluent animated toggle switch (44x24 px) for Light & Dark.
    """

    toggled = Signal(bool)

    def __init__(self, parent: Optional[QWidget] = None, checked: bool = False, theme_mode: str = "light"):
        super().__init__(parent)
        self.setFixedSize(44, 24)
        self.setCursor(Qt.PointingHandCursor)
        self._checked = checked
        self._theme_mode = theme_mode
        self._offset = 22.0 if checked else 2.0

        self._anim = QPropertyAnimation(self, b"thumb_offset", self)
        self._anim.setDuration(DURATION_FADE_MS)
        self._anim.setEasingCurve(QEasingCurve.InOutQuad)

    def isChecked(self) -> bool:
        return self._checked

    def setChecked(self, checked: bool) -> None:
        if self._checked != checked:
            self._checked = checked
            self._animate_thumb(checked)
            self.toggled.emit(self._checked)

    def apply_theme(self, theme_mode: str = "light") -> None:
        self._theme_mode = theme_mode
        self.update()

    def _animate_thumb(self, checked: bool) -> None:
        target = 22.0 if checked else 2.0
        self._anim.stop()
        self._anim.setStartValue(self._offset)
        self._anim.setEndValue(target)
        self._anim.start()

    def get_thumb_offset(self) -> float:
        return self._offset

    def set_thumb_offset(self, val: float) -> None:
        self._offset = val
        self.update()

    from PySide6.QtCore import Property
    thumb_offset = Property(float, get_thumb_offset, set_thumb_offset)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.setChecked(not self._checked)
        super().mousePressEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        pal = get_theme_palette(self._theme_mode)

        track_rect = QRectF(0, 0, 44, 24)
        track_off = "#48484A" if self._theme_mode == "dark" else "#D1D5DB"
        track_color = QColor(pal['accent']) if self._checked else QColor(track_off)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QBrush(track_color))
        painter.drawRoundedRect(track_rect, 12, 12)

        # Draw white thumb with subtle shadow
        thumb_rect = QRectF(self._offset, 2.0, 20.0, 20.0)
        painter.setBrush(QBrush(QColor("#FFFFFF")))
        painter.drawEllipse(thumb_rect)


# ----------------------------------------------------------------------
# 4. Buttons (Primary, Secondary, Text)
# ----------------------------------------------------------------------
class PrimaryButton(QPushButton):
    """Accent-filled action button."""

    def __init__(self, text: str, parent: Optional[QWidget] = None, scale: float = 1.0, theme_mode: str = "light"):
        super().__init__(text, parent)
        self.setCursor(Qt.PointingHandCursor)
        self._scale = scale
        self.setFont(get_scaled_font("body_bold", scale))
        h = int(32 * max(1.0, scale * 0.95))
        self.setFixedHeight(h)
        self.apply_theme(theme_mode)

    def apply_theme(self, theme_mode: str = "light") -> None:
        pal = get_theme_palette(theme_mode)
        self.setStyleSheet(
            f"""
            QPushButton {{
                background-color: {pal['accent']};
                color: {pal['accent_text']};
                border: none;
                border-radius: {RADIUS_BUTTON}px;
                padding: 4px 16px;
                font-weight: 600;
            }}
            QPushButton:hover {{
                background-color: {pal['accent_hover']};
            }}
            QPushButton:pressed {{
                background-color: {pal['accent_pressed']};
            }}
            QPushButton:disabled {{
                background-color: #3B5B82 if "{theme_mode}" == "dark" else #BFDBFE;
                color: #FFFFFF;
            }}
            """
        )


class SecondaryButton(QPushButton):
    """Subtle outlined button on card background."""

    def __init__(self, text: str, parent: Optional[QWidget] = None, scale: float = 1.0, theme_mode: str = "light"):
        super().__init__(text, parent)
        self.setCursor(Qt.PointingHandCursor)
        self._scale = scale
        self.setFont(get_scaled_font("body", scale))
        h = int(32 * max(1.0, scale * 0.95))
        self.setFixedHeight(h)
        self.apply_theme(theme_mode)

    def apply_theme(self, theme_mode: str = "light") -> None:
        pal = get_theme_palette(theme_mode)
        hover_bg = "#3A3A3C" if theme_mode == "dark" else "#F8FAFC"
        pressed_bg = "#48484A" if theme_mode == "dark" else "#E2E8F0"
        self.setStyleSheet(
            f"""
            QPushButton {{
                background-color: {pal['card_bg']};
                color: {pal['primary_text']};
                border: 1px solid {pal['border_strong']};
                border-radius: {RADIUS_BUTTON}px;
                padding: 4px 14px;
            }}
            QPushButton:hover {{
                background-color: {hover_bg};
                border-color: {pal['accent']};
            }}
            QPushButton:pressed {{
                background-color: {pressed_bg};
            }}
            QPushButton:disabled {{
                color: {pal['disabled_text']};
                border-color: {pal['card_border']};
            }}
            """
        )


class TextButton(QPushButton):
    """Ghost / text button without border."""

    def __init__(
        self,
        text: str,
        parent: Optional[QWidget] = None,
        scale: float = 1.0,
        accent: bool = True,
        theme_mode: str = "light",
    ):
        super().__init__(text, parent)
        self.setCursor(Qt.PointingHandCursor)
        self.setFlat(True)
        self._accent = accent
        self.setFont(get_scaled_font("body_bold" if accent else "body", scale))
        self.apply_theme(theme_mode)

    def apply_theme(self, theme_mode: str = "light") -> None:
        pal = get_theme_palette(theme_mode)
        color = pal['accent'] if self._accent else pal['secondary_text']
        hover_bg = "rgba(10, 132, 255, 0.15)" if theme_mode == "dark" else "#EFF6FF"
        self.setStyleSheet(
            f"""
            QPushButton {{
                background-color: transparent;
                color: {color};
                border: none;
                border-radius: 6px;
                padding: 4px 8px;
            }}
            QPushButton:hover {{
                background-color: {hover_bg};
                color: {pal['accent_hover']};
            }}
            QPushButton:pressed {{
                background-color: rgba(10, 132, 255, 0.25);
            }}
            QPushButton:disabled {{
                color: {pal['disabled_text']};
            }}
            """
        )


# ----------------------------------------------------------------------
# 5. Status Pill & Test Pill (Light & Dark)
# ----------------------------------------------------------------------
class StatusPill(QLabel):
    """High-contrast pill badge with icon and label."""

    def __init__(
        self,
        text: str,
        status_type: str = "neutral",
        parent: Optional[QWidget] = None,
        scale: float = 1.0,
        theme_mode: str = "light",
    ):
        super().__init__(parent)
        self._status_type = status_type
        self._raw_text = text
        self._scale = scale
        self.setAlignment(Qt.AlignCenter)

        font = get_scaled_font("caption", scale)
        font.setWeight(QFont.DemiBold)
        self.setFont(font)
        h = int(22 * max(1.0, scale * 0.95))
        self.setFixedHeight(h)

        self.apply_theme(theme_mode)

    def apply_theme(self, theme_mode: str = "light") -> None:
        pal = get_theme_palette(theme_mode)
        cfg = pal["status_colors"].get(self._status_type, pal["status_colors"]["neutral"])
        icon = cfg.get("icon", "")
        display_text = f"{icon} {self._raw_text}" if icon and not self._raw_text.startswith(icon) else self._raw_text
        self.setText(display_text)
        self.setStyleSheet(
            f"""
            QLabel {{
                background-color: {cfg['bg']};
                color: {cfg['fg']};
                border: 1px solid {cfg['border']};
                border-radius: {RADIUS_PILL}px;
                padding: 1px 8px;
            }}
            """
        )


class TestPill(QLabel):
    """Distinctive purple pill indicating a test clip [TEST]."""

    def __init__(self, parent: Optional[QWidget] = None, scale: float = 1.0, theme_mode: str = "light"):
        super().__init__("TEST", parent)
        self.setAlignment(Qt.AlignCenter)
        font = get_scaled_font("caption", scale)
        font.setBold(True)
        self.setFont(font)
        h = int(18 * max(1.0, scale * 0.95))
        self.setFixedHeight(h)
        self.apply_theme(theme_mode)

    def apply_theme(self, theme_mode: str = "light") -> None:
        pal = get_theme_palette(theme_mode)
        cfg = pal["status_colors"]["test"]
        self.setStyleSheet(
            f"""
            QLabel {{
                background-color: {cfg['bg']};
                color: {cfg['fg']};
                border: 1px solid {cfg['border']};
                border-radius: 4px;
                padding: 0px 5px;
                font-size: 8pt;
                font-weight: 700;
            }}
            """
        )


# ----------------------------------------------------------------------
# 6. Toast Notification
# ----------------------------------------------------------------------
class Toast(QWidget):
    """Non-modal floating toast notification with automatic fade-out."""

    def __init__(self, text: str, parent: Optional[QWidget] = None, duration_ms: int = 3000):
        super().__init__(parent)
        self.setWindowFlags(Qt.SubWindow | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 8, 14, 8)

        lbl = QLabel(text, self)
        lbl.setFont(get_scaled_font("body"))
        lbl.setStyleSheet("color: #FFFFFF;")
        layout.addWidget(lbl)

        self.setStyleSheet(
            """
            Toast {
                background-color: rgba(30, 30, 32, 0.94);
                border: 1px solid #48484A;
                border-radius: 8px;
            }
            """
        )

        self._opacity = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self._opacity)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._fade_out)
        self._duration_ms = duration_ms

    def show_toast(self, parent_widget: QWidget) -> None:
        self.adjustSize()
        px = (parent_widget.width() - self.width()) // 2
        py = parent_widget.height() - self.height() - 24
        self.move(px, py)
        self.show()

        self._anim = QPropertyAnimation(self._opacity, b"opacity")
        self._anim.setDuration(DURATION_FADE_MS)
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.start()

        self._timer.start(self._duration_ms)

    def _fade_out(self) -> None:
        self._anim = QPropertyAnimation(self._opacity, b"opacity")
        self._anim.setDuration(DURATION_FADE_MS)
        self._anim.setStartValue(1.0)
        self._anim.setEndValue(0.0)
        self._anim.finished.connect(self.close)
        self._anim.start()


# ----------------------------------------------------------------------
# 7. Thin Progress Bar
# ----------------------------------------------------------------------
class ThinProgressBar(QProgressBar):
    """Sleek 4px progress line."""

    def __init__(self, parent: Optional[QWidget] = None, theme_mode: str = "light"):
        super().__init__(parent)
        self.setFixedHeight(4)
        self.setTextVisible(False)
        self.apply_theme(theme_mode)

    def apply_theme(self, theme_mode: str = "light") -> None:
        pal = get_theme_palette(theme_mode)
        bg = "#3A3A3C" if theme_mode == "dark" else "#E5E5EA"
        self.setStyleSheet(
            f"""
            QProgressBar {{
                background-color: {bg};
                border: none;
                border-radius: 2px;
            }}
            QProgressBar::chunk {{
                background-color: {pal['accent']};
                border-radius: 2px;
            }}
            """
        )


# ----------------------------------------------------------------------
# 8. Multi-Line Header View & Table Item Delegates (Theme-Aware)
# ----------------------------------------------------------------------
class MultiLineHeaderView(QHeaderView):
    """
    QHeaderView that wraps header text up to 3 lines and dynamically computes
    its height from wrapped text so no header title is ever cut.
    """

    def __init__(self, orientation=Qt.Horizontal, parent=None, theme_mode="light", scale=1.0):
        super().__init__(orientation, parent)
        self.theme_mode = theme_mode
        self.scale = scale
        self.setDefaultAlignment(Qt.AlignCenter | Qt.AlignVCenter)
        self.setHighlightSections(False)
        self.setSectionsClickable(True)
        self.setSectionsMovable(False)
        self.update_header_height()

    def update_header_height(self) -> None:
        font = self.font()
        fm = QFontMetrics(font)
        line_height = fm.lineSpacing()
        needed_height = int((line_height * 2.5) + (10 * self.scale))
        self.setFixedHeight(needed_height)

    def sizeHint(self) -> QSize:
        base = super().sizeHint()
        font = self.font()
        fm = QFontMetrics(font)
        line_height = fm.lineSpacing()
        needed_height = int((line_height * 2.5) + (10 * self.scale))
        return QSize(base.width(), needed_height)

    def paintSection(self, painter: QPainter, rect: QRect, logicalIndex: int):
        painter.save()
        pal = get_theme_palette(self.theme_mode)
        bg = "#242426" if self.theme_mode == "dark" else "#F8F9FA"
        painter.fillRect(rect, QColor(bg))

        painter.setPen(QColor(pal["card_border"]))
        painter.drawLine(rect.bottomLeft(), rect.bottomRight())
        painter.drawLine(rect.topRight(), rect.bottomRight())

        model = self.model()
        if model:
            val = model.headerData(logicalIndex, self.orientation(), Qt.DisplayRole)
            if val:
                painter.setFont(self.font())
                painter.setPen(QColor(pal["secondary_text"]))
                text_rect = rect.adjusted(6, 4, -6, -4)
                opt = QTextOption(Qt.AlignCenter | Qt.AlignVCenter)
                opt.setWrapMode(QTextOption.WordWrap)
                painter.drawText(QRectF(text_rect), str(val), opt)
        painter.restore()


class Stage3TableDelegate(QStyledItemDelegate):
    """
    Item delegate implementing Part B cell formatting:
    - Col 0 (#): Index number with a small stylish 'T' badge for test clips
    - Col 1 (Clip): 9.5 pt filename font, middle-elided with tooltip
    - Col 2 (Speaker): 10 pt font, middle-elided with tooltip
    - Col 3, 4, 5 (Times): Tabular figures, centered
    - Col 6 (Status): High-contrast pill badge with icon and text ("✓ Done", etc.)
    - Col 7 (Approval): High-contrast pill badge with icon and text ("✓ Approved", etc.)
    - Col 8 (Engine): Engine icon + short name ("Parakeet", "Whisper")
    """

    def __init__(self, parent=None, theme_mode: str = "light", scale: float = 1.0):
        super().__init__(parent)
        self.theme_mode = theme_mode
        self.scale = scale

    def paint(self, painter: QPainter, option, index):
        painter.save()
        opt = option
        self.initStyleOption(opt, index)
        col = index.column()
        row = index.row()
        pal = get_theme_palette(self.theme_mode)

        if opt.state & QStyle.State_Selected:
            painter.fillRect(opt.rect, QColor(pal["row_selected"]))
            text_color = QColor(pal["row_selected_text"])
        else:
            bg = QColor(pal["row_even"]) if row % 2 == 0 else QColor(pal["row_odd"])
            if opt.state & QStyle.State_MouseOver:
                bg = QColor(pal["row_hover"])
            painter.fillRect(opt.rect, bg)
            text_color = QColor(pal["primary_text"])

        # Bottom border
        painter.setPen(QColor(pal["card_border"]))
        painter.drawLine(opt.rect.bottomLeft(), opt.rect.bottomRight())

        val = index.data(Qt.DisplayRole)
        val_str = str(val) if val is not None else ""

        if col == 0:
            # Col 0: # (with optional small "T" badge)
            is_test = "[TEST]" in val_str or val_str.endswith(" T") or " T " in val_str
            clean_num = val_str.replace("[TEST]", "").replace(" T", "").strip()
            painter.setFont(opt.font)
            painter.setPen(text_color)
            if is_test:
                fm = opt.fontMetrics
                num_w = fm.horizontalAdvance(clean_num)
                badge_w = int(16 * self.scale)
                badge_h = int(16 * self.scale)
                spacing = int(4 * self.scale)
                total_w = num_w + spacing + badge_w
                start_x = opt.rect.x() + max(4, (opt.rect.width() - total_w) // 2)

                painter.drawText(QRect(start_x, opt.rect.y(), num_w, opt.rect.height()), Qt.AlignVCenter, clean_num)
                b_rect = QRect(start_x + num_w + spacing, opt.rect.y() + (opt.rect.height() - badge_h) // 2, badge_w, badge_h)
                painter.setBrush(QColor("#0284C7" if self.theme_mode == "light" else "#38BDF8"))
                painter.setPen(Qt.NoPen)
                painter.drawRoundedRect(b_rect, 3, 3)
                painter.setPen(QColor("#FFFFFF" if self.theme_mode == "light" else "#0F172A"))
                b_font = QFont(opt.font)
                b_font.setPointSizeF(max(7.0, opt.font.pointSizeF() * 0.75))
                b_font.setBold(True)
                painter.setFont(b_font)
                painter.drawText(b_rect, Qt.AlignCenter, "T")
            else:
                painter.drawText(opt.rect, Qt.AlignCenter, clean_num)

        elif col == 1:
            # Filenames: 9.5 pt font, middle elided
            f = QFont(opt.font)
            f.setPointSizeF(9.5 * self.scale)
            painter.setFont(f)
            painter.setPen(text_color)
            fm = QFontMetrics(f)
            elided = fm.elidedText(val_str, Qt.ElideMiddle, opt.rect.width() - 12)
            painter.drawText(opt.rect.adjusted(6, 0, -6, 0), Qt.AlignLeft | Qt.AlignVCenter, elided)

        elif col == 2:
            # Speaker: 10 pt font, middle elided
            painter.setFont(opt.font)
            painter.setPen(text_color)
            fm = opt.fontMetrics
            elided = fm.elidedText(val_str, Qt.ElideMiddle, opt.rect.width() - 12)
            painter.drawText(opt.rect.adjusted(6, 0, -6, 0), Qt.AlignLeft | Qt.AlignVCenter, elided)

        elif col in (3, 4, 5):
            # Duration & Times: Tabular figures
            painter.setFont(opt.font)
            painter.setPen(text_color)
            painter.drawText(opt.rect, Qt.AlignCenter, val_str)

        elif col == 6:
            # Status Pill
            self._paint_pill(painter, opt.rect, val_str, is_approval=False)

        elif col == 7:
            # Approval Pill
            self._paint_pill(painter, opt.rect, val_str, is_approval=True)

        elif col == 8:
            # Engine Pill
            self._paint_engine(painter, opt.rect, val_str)

        else:
            painter.setFont(opt.font)
            painter.setPen(text_color)
            painter.drawText(opt.rect.adjusted(6, 0, -6, 0), opt.displayAlignment | Qt.AlignVCenter, val_str)

        painter.restore()

    def _paint_pill(self, painter: QPainter, rect: QRect, text: str, is_approval: bool = False):
        pal = get_theme_palette(self.theme_mode)
        status_map = pal.get("status_colors", {})
        t_lower = text.lower()
        if "done" in t_lower or "approved" in t_lower:
            st = status_map.get("success", {"bg": "#E8F5E9", "fg": "#1B5E20"})
            bg = st["bg"]
            fg = st["fg"]
            icon = "✓ "
        elif "running" in t_lower:
            st = status_map.get("warning", {"bg": "#FEF3C7", "fg": "#92400E"})
            bg = st["bg"]
            fg = st["fg"]
            icon = "⏳ "
        elif "failed" in t_lower or "error" in t_lower:
            st = status_map.get("error", {"bg": "#FEE2E2", "fg": "#991B1B"})
            bg = st["bg"]
            fg = st["fg"]
            icon = "✕ "
        else:
            st = status_map.get("neutral", {"bg": pal["row_odd"], "fg": pal["secondary_text"]})
            bg = st["bg"]
            fg = st["fg"]
            icon = ""

        clean_text = text.replace("✓", "").replace("⏳", "").replace("✕", "").strip()
        display_label = f"{icon}{clean_text}" if not clean_text.startswith(icon) else clean_text

        # Pill dimensions
        font = QFont(painter.font())
        font.setPointSizeF(max(8.0, font.pointSizeF() * 0.90))
        font.setBold(True)
        fm = QFontMetrics(font)
        txt_w = fm.horizontalAdvance(display_label)
        pill_w = min(rect.width() - 8, max(int(68 * self.scale), txt_w + int(14 * self.scale)))
        pill_h = int(22 * self.scale)
        px = rect.x() + (rect.width() - pill_w) // 2
        py = rect.y() + (rect.height() - pill_h) // 2
        p_rect = QRect(px, py, pill_w, pill_h)

        painter.setBrush(QColor(bg))
        painter.setPen(QColor(pal["card_border"]))
        painter.drawRoundedRect(p_rect, int(pill_h / 2), int(pill_h / 2))

        painter.setFont(font)
        painter.setPen(QColor(fg))
        painter.drawText(p_rect, Qt.AlignCenter, display_label)

    def _paint_engine(self, painter: QPainter, rect: QRect, text: str):
        pal = get_theme_palette(self.theme_mode)
        t_lower = text.lower()
        if "cloud" in t_lower or "parakeet" in t_lower:
            icon = "☁ "
            short_name = "Parakeet" if "parakeet" in t_lower else "Cloud Riva"
            if "whisper" in t_lower and "parakeet" in t_lower:
                short_name = "Parakeet → Whisper"
                icon = "⚡ "
        elif "whisper" in t_lower:
            icon = "💻 "
            short_name = "Whisper"
        else:
            icon = ""
            short_name = text

        label = f"{icon}{short_name}"
        font = QFont(painter.font())
        font.setPointSizeF(max(8.0, font.pointSizeF() * 0.90))
        fm = QFontMetrics(font)
        txt_w = fm.horizontalAdvance(label)

        pill_w = min(rect.width() - 8, max(int(74 * self.scale), txt_w + int(12 * self.scale)))
        pill_h = int(22 * self.scale)
        px = rect.x() + (rect.width() - pill_w) // 2
        py = rect.y() + (rect.height() - pill_h) // 2
        p_rect = QRect(px, py, pill_w, pill_h)

        painter.setBrush(QColor(pal["row_odd"]))
        painter.setPen(QColor(pal["card_border"]))
        painter.drawRoundedRect(p_rect, 4, 4)

        painter.setFont(font)
        painter.setPen(QColor(pal["primary_text"]))
        painter.drawText(p_rect, Qt.AlignCenter, label)


class MiddleElideDelegate(QStyledItemDelegate):
    """Paints middle-elided text (e.g. '20261031_14...Topic.mp4') with tooltip."""

    def __init__(self, parent=None, theme_mode: str = "light"):
        super().__init__(parent)
        self.theme_mode = theme_mode

    def paint(self, painter: QPainter, option, index):
        painter.save()
        opt = option
        self.initStyleOption(opt, index)

        text = index.data(Qt.DisplayRole)
        pal = get_theme_palette(self.theme_mode)

        if text:
            fm = opt.fontMetrics
            elided = fm.elidedText(text, Qt.ElideMiddle, opt.rect.width() - 8)

            if opt.state & QStyle.State_Selected:
                painter.fillRect(opt.rect, QColor(pal["row_selected"]))
                painter.setPen(QColor(pal["row_selected_text"]))
            else:
                row = index.row()
                bg = QColor(pal["row_even"]) if row % 2 == 0 else QColor(pal["row_odd"])
                if opt.state & QStyle.State_MouseOver:
                    bg = QColor(pal["row_hover"])
                painter.fillRect(opt.rect, bg)
                painter.setPen(QColor(pal["primary_text"]))

            painter.setFont(opt.font)
            text_rect = opt.rect.adjusted(4, 0, -4, 0)
            painter.drawText(text_rect, opt.displayAlignment | Qt.AlignVCenter, elided)
        painter.restore()


# ----------------------------------------------------------------------
# 9. Adaptive Table Widget (Zero Horizontal Scrollbar, Column Redistribution)
# ----------------------------------------------------------------------
class AdaptiveTable(QTableWidget):
    """
    Adaptive Table Widget following Part B specifications:
    - Column roles:
        * Fixed: # (with a small "T" badge for test clips)
        * Content-fit: Duration, Time taken for transcription, Failover time, Status, Approval, Engine
        * Flexible: Clip, Speaker
    - Width algorithm:
        * Content-fit: width is max(widest cell text, longest header word) + padding (QFontMetrics)
        * Flexible: Clip gets full filename need first, then Speaker. Elide with "…" and tooltip when short.
        * Text size step reduction (down to 9pt) if minimum window cannot fit.
        * Sum of all column widths == viewport width (zero horizontal scrollbar).
    - Resizing:
        * Dragging moves width between neighbouring columns (total never exceeds viewport).
        * Minimum widths enforced.
        * Double-click edge auto-fits that column.
        * User widths stored as fractions in ui.ini.
    - Small A−/A+ table text scaling (85% to 130%).
    - "⋯" Menu: "Fit columns" and "Reset widths".
    """

    userResizedColumns = Signal()
    columnWidthsChanged = Signal(list)

    def __init__(
        self,
        rows: int = 0,
        columns: int = 9,
        parent: Optional[QWidget] = None,
        scale: float = 1.0,
        theme_mode: str = "light",
        min_widths: Optional[Dict[int, int]] = None,
    ):
        super().__init__(rows, columns, parent)
        self._scale = scale
        self._theme_mode = theme_mode
        self._min_widths = min_widths or {}
        self._table_text_scale = 1.0
        self._user_fractions: List[float] = []
        self._resizing_in_progress = False

        self.setMouseTracking(True)
        self.setAlternatingRowColors(True)
        self.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.setShowGrid(False)
        self.verticalHeader().setVisible(False)

        # Disallow horizontal scrollbars unconditionally
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)

        # Multi-line wrapping header
        header = MultiLineHeaderView(Qt.Horizontal, self, theme_mode=theme_mode, scale=scale)
        self.setHorizontalHeader(header)
        header.sectionResized.connect(self._on_section_resized)
        header.sectionDoubleClicked.connect(self._on_section_double_clicked)

        # Stage 3 Delegate for all columns
        self.setItemDelegate(Stage3TableDelegate(self, theme_mode=theme_mode, scale=scale))

        self._load_saved_fractions()
        self.apply_theme(theme_mode)

    def _load_saved_fractions(self) -> None:
        try:
            settings = load_ui_settings()
            fracs = settings.get("table_fractions_stage3", "")
            if fracs:
                parts = [float(p.strip()) for p in fracs.split(",") if p.strip()]
                if len(parts) == self.columnCount() and abs(sum(parts) - 1.0) < 0.05:
                    self._user_fractions = parts
        except Exception:
            self._user_fractions = []

    def _save_fractions(self) -> None:
        try:
            if self._user_fractions and len(self._user_fractions) == self.columnCount():
                settings = load_ui_settings()
                settings["table_fractions_stage3"] = ",".join(f"{f:.4f}" for f in self._user_fractions)
                save_ui_settings(settings)
        except Exception:
            pass

    def _clear_saved_fractions(self) -> None:
        try:
            settings = load_ui_settings()
            if "table_fractions_stage3" in settings:
                del settings["table_fractions_stage3"]
                save_ui_settings(settings)
        except Exception:
            pass

    def apply_theme(self, theme_mode: str = "light", row_height: int = ROW_HEIGHT_COMFORTABLE) -> None:
        self._theme_mode = theme_mode
        pal = get_theme_palette(theme_mode)
        h_bg = "#242426" if theme_mode == "dark" else "#F8F9FA"

        s = self._scale * self._table_text_scale
        self.verticalHeader().setDefaultSectionSize(int(row_height * max(1.0, s * 0.9)))
        font = get_scaled_font("table", s)
        self.setFont(font)

        header = self.horizontalHeader()
        header.setFont(get_scaled_font("body_bold", s))
        if isinstance(header, MultiLineHeaderView):
            header.theme_mode = theme_mode
            header.scale = s
            header.update_header_height()

        delegate = self.itemDelegate()
        if isinstance(delegate, Stage3TableDelegate):
            delegate.theme_mode = theme_mode
            delegate.scale = s

        self.setStyleSheet(
            f"""
            QTableWidget {{
                background-color: {pal['card_bg']};
                alternate-background-color: {pal['row_odd']};
                border: 1px solid {pal['card_border']};
                border-radius: {RADIUS_CARD}px;
                outline: none;
                color: {pal['primary_text']};
                selection-background-color: {pal['row_selected']};
                selection-color: {pal['row_selected_text']};
            }}
            QTableWidget::item {{
                padding: 4px 6px;
                border-bottom: 1px solid {pal['card_border']};
            }}
            QTableWidget::item:hover {{
                background-color: {pal['row_hover']};
            }}
            QTableWidget::item:selected {{
                background-color: {pal['row_selected']};
                color: {pal['row_selected_text']};
            }}
            QHeaderView::section {{
                background-color: {h_bg};
                color: {pal['secondary_text']};
                font-weight: 600;
                padding: 6px 8px;
                border: none;
                border-bottom: 1px solid {pal['card_border']};
            }}
            """
        )

    def get_col_min_width(self, col: int) -> int:
        s = self._scale * self._table_text_scale
        default_mins = {
            0: int(46 * s),   # #
            1: int(160 * s),  # Clip
            2: int(110 * s),  # Speaker
            3: int(64 * s),   # Duration
            4: int(112 * s),  # Time taken
            5: int(78 * s),   # Failover time
            6: int(88 * s),   # Status
            7: int(88 * s),   # Approval
            8: int(92 * s),   # Engine
        }
        return default_mins.get(col, int(60 * s))

    def adapt_columns_to_width(self, available_width: Optional[int] = None) -> None:
        """
        Part B Width Algorithm:
        - Fixed: #
        - Content-fit: Duration, Time taken, Failover, Status, Approval, Engine
        - Flexible: Clip, Speaker (Clip gets full filename need first, then Speaker)
        - Sum of widths == viewport width (zero horizontal scrollbar).
        """
        col_count = self.columnCount()
        if col_count < 9:
            return

        w = available_width or self.viewport().width()
        v_bar = self.verticalScrollBar()
        if v_bar and v_bar.isVisible():
            w = min(w, self.width() - v_bar.width() - 2)
        if w <= 10:
            w = self.width() - 4
        if w <= 10:
            return

        header = self.horizontalHeader()
        header.blockSignals(True)

        fm_table = QFontMetrics(self.font())
        fm_header = QFontMetrics(header.font())
        s = self._scale * self._table_text_scale

        col_widths: Dict[int, int] = {c: self.get_col_min_width(c) for c in range(col_count)}

        # 0. Fixed column: #
        col_widths[0] = max(int(46 * s), fm_table.horizontalAdvance("999 T") + int(18 * s))

        # Content-fit columns: 3 (Duration), 4 (Time taken), 5 (Failover), 6 (Status), 7 (Approval), 8 (Engine)
        content_fit_cols = [3, 4, 5, 6, 7, 8]
        header_lines_map = {
            3: ["Duration"],
            4: ["Time taken for", "transcription"],
            5: ["Failover", "time"],
            6: ["Status"],
            7: ["Approval"],
            8: ["Engine"],
        }

        # Fonts for measuring pill badges accurately
        pill_font = QFont(self.font())
        pill_font.setPointSizeF(max(8.0, self.font().pointSizeF() * 0.90))
        pill_font.setBold(True)
        fm_pill = QFontMetrics(pill_font)

        for c in content_fit_cols:
            lines = header_lines_map.get(c, [])
            h_word_w = max((fm_header.horizontalAdvance(line) for line in lines), default=0) + int(24 * s)

            max_cell_w = 0
            for r in range(min(self.rowCount(), 40)):
                item = self.item(r, c)
                if item:
                    txt = item.text()
                    if c in (6, 7, 8):
                        badge_label = f"✓ {txt}" if c in (6, 7) and ("done" in txt.lower() or "approved" in txt.lower()) else txt
                        pill_txt_w = fm_pill.horizontalAdvance(badge_label)
                        pill_w = max(int(68 * s), pill_txt_w + int(14 * s))
                        max_cell_w = max(max_cell_w, pill_w)
                    else:
                        max_cell_w = max(max_cell_w, fm_table.horizontalAdvance(txt))

            # Minimum sample measurements when rowCount == 0 or empty
            if max_cell_w == 0:
                if c == 3:
                    max_cell_w = fm_table.horizontalAdvance("00:00:00")
                elif c == 4:
                    max_cell_w = fm_table.horizontalAdvance("00:00:00")
                elif c == 5:
                    max_cell_w = fm_table.horizontalAdvance("00:00:00")
                elif c == 6:
                    max_cell_w = max(int(68 * s), fm_pill.horizontalAdvance("✓ Done") + int(14 * s))
                elif c == 7:
                    max_cell_w = max(int(68 * s), fm_pill.horizontalAdvance("✓ Approved") + int(14 * s))
                elif c == 8:
                    max_cell_w = max(int(74 * s), fm_pill.horizontalAdvance("☁ Parakeet") + int(14 * s))

            min_w = self.get_col_min_width(c)
            col_widths[c] = max(min_w, max(h_word_w, max_cell_w + int(16 * s)))

        sum_non_flex = col_widths[0] + sum(col_widths[c] for c in content_fit_cols)
        available_flex = w - sum_non_flex

        # If available_flex is squeezed and font > 9pt, reduce font by one step
        if available_flex < int(240 * s) and self.font().pointSizeF() > 9.0:
            new_pt = max(9.0, self.font().pointSizeF() - 0.5)
            f = QFont(self.font())
            f.setPointSizeF(new_pt)
            self.setFont(f)
            fm_table = QFontMetrics(f)
            sum_non_flex = col_widths[0] + sum(col_widths[c] for c in content_fit_cols)
            available_flex = w - sum_non_flex

        # Flexible columns: Col 1 (Clip) and Col 2 (Speaker)
        f_name = QFont(self.font())
        f_name.setPointSizeF(9.5 * s)
        fm_name = QFontMetrics(f_name)

        max_clip_needed = int(180 * s)
        for r in range(min(self.rowCount(), 40)):
            item = self.item(r, 1)
            if item:
                max_clip_needed = max(max_clip_needed, fm_name.horizontalAdvance(item.text()) + int(24 * s))

        min_clip = self.get_col_min_width(1)
        min_spk = self.get_col_min_width(2)

        if available_flex <= (min_clip + min_spk):
            col_widths[1] = max(min_clip, int(available_flex * 0.60))
            col_widths[2] = max(min_spk, available_flex - col_widths[1])
        else:
            if (max_clip_needed + min_spk) <= available_flex:
                col_widths[1] = max_clip_needed
                col_widths[2] = available_flex - max_clip_needed
            else:
                col_widths[1] = available_flex - min_spk
                col_widths[2] = min_spk

        # If user has saved proportions and no fit_columns called
        if self._user_fractions and len(self._user_fractions) == col_count:
            for c in range(col_count):
                col_widths[c] = max(self.get_col_min_width(c), int(self._user_fractions[c] * w))

        # Adjust for exact pixel fit (ensure no border or subpixel overflow triggers scrollbar)
        target_w = max(10, w - 1)
        diff = target_w - sum(col_widths[c] for c in range(col_count))
        col_widths[1] = max(min_clip, col_widths[1] + diff)

        for c in range(col_count):
            header.setSectionResizeMode(c, QHeaderView.Interactive)
            self.setColumnWidth(c, max(10, col_widths[c]))

        header.blockSignals(False)
        self.columnWidthsChanged.emit([self.columnWidth(c) for c in range(col_count)])

    def _on_section_resized(self, logicalIndex: int, oldSize: int, newSize: int) -> None:
        """User drag: moves width between neighbouring columns, preserving total viewport width."""
        if self._resizing_in_progress:
            return
        nextIndex = logicalIndex + 1
        if nextIndex >= self.columnCount():
            return

        self._resizing_in_progress = True
        try:
            delta = newSize - oldSize
            curr_next = self.columnWidth(nextIndex)
            min_next = self.get_col_min_width(nextIndex)
            min_curr = self.get_col_min_width(logicalIndex)

            max_delta = curr_next - min_next
            min_delta = min_curr - oldSize

            clamped_delta = max(min_delta, min(delta, max_delta))
            actual_curr = oldSize + clamped_delta
            actual_next = curr_next - clamped_delta

            self.setColumnWidth(logicalIndex, actual_curr)
            self.setColumnWidth(nextIndex, actual_next)

            tot_w = max(1, self.viewport().width())
            self._user_fractions = [self.columnWidth(c) / tot_w for c in range(self.columnCount())]
            self._save_fractions()
            self.userResizedColumns.emit()
            self.columnWidthsChanged.emit([self.columnWidth(c) for c in range(self.columnCount())])
        finally:
            self._resizing_in_progress = False

    def _on_section_double_clicked(self, logicalIndex: int) -> None:
        """Double clicking edge auto-fits that column."""
        self.fit_columns()

    def fit_columns(self) -> None:
        """Reset column widths to fit content while maintaining zero scrollbar."""
        self._user_fractions.clear()
        self._clear_saved_fractions()
        self.adapt_columns_to_width()

    def reset_widths(self) -> None:
        """Reset column widths back to default algorithm."""
        self.fit_columns()

    def zoom_in(self) -> None:
        """A+ table text scaling (up to 130%)."""
        self._table_text_scale = min(1.30, round(self._table_text_scale + 0.05, 2))
        self.apply_theme(self._theme_mode)
        self.adapt_columns_to_width()

    def zoom_out(self) -> None:
        """A- table text scaling (down to 85%)."""
        self._table_text_scale = max(0.85, round(self._table_text_scale - 0.05, 2))
        self.apply_theme(self._theme_mode)
        self.adapt_columns_to_width()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self.adapt_columns_to_width(event.size().width() - 4)


# Alias for backward compatibility
ClipsTable = AdaptiveTable


# ----------------------------------------------------------------------
# 10. Totals Bar Widget (Pinned directly under table, aligned to columns)
# ----------------------------------------------------------------------
class TotalsBar(QFrame):
    """
    Totals row pinned directly under the table, with cell text aligned to columns.
    Short text that fits: 'Total', '21 clips', '5 speakers', '07:42', '21 of 21 timed', '0s', '21 Done', '21 Approved'.
    """

    def __init__(self, parent: Optional[QWidget] = None, scale: float = 1.0, theme_mode: str = "light"):
        super().__init__(parent)
        self._scale = scale
        self._theme_mode = theme_mode
        self.setFixedHeight(int(36 * max(1.0, scale * 0.9)))

        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(0)

        # 9 column labels exactly matching table columns
        self.labels: List[QLabel] = []
        for i in range(9):
            lbl = QLabel(self)
            lbl.setFont(get_scaled_font("body_bold", scale * 0.95))
            lbl.setAlignment(Qt.AlignCenter if i in (0, 3, 4, 5, 6, 7, 8) else Qt.AlignLeft | Qt.AlignVCenter)
            self._layout.addWidget(lbl)
            self.labels.append(lbl)

        # Default initial text
        self.labels[0].setText("Total")
        self.labels[1].setText("  0 clips")
        self.labels[2].setText("  0 speakers")
        self.labels[3].setText("00:00")
        self.labels[4].setText("0 of 0 timed")
        self.labels[5].setText("0s")
        self.labels[6].setText("0 Done")
        self.labels[7].setText("0 Approved")
        self.labels[8].setText("—")

        self.apply_theme(theme_mode)

    def sync_column_widths(self, widths: List[int]) -> None:
        for i, w in enumerate(widths):
            if i < len(self.labels):
                self.labels[i].setFixedWidth(max(10, w))

    def apply_theme(self, theme_mode: str = "light") -> None:
        self._theme_mode = theme_mode
        pal = get_theme_palette(theme_mode)
        bg = "#242426" if theme_mode == "dark" else "#F8F9FB"
        self.setStyleSheet(
            f"""
            QFrame {{
                background-color: {bg};
                border-left: 1px solid {pal['card_border']};
                border-right: 1px solid {pal['card_border']};
                border-bottom: 1px solid {pal['card_border']};
                border-top: 1px solid {pal['border_strong']};
                border-bottom-left-radius: {RADIUS_CARD}px;
                border-bottom-right-radius: {RADIUS_CARD}px;
            }}
            """
        )
        for lbl in self.labels:
            lbl.setStyleSheet(f"color: {pal['primary_text']}; border: none; padding: 2px 4px;")

    def update_totals(
        self,
        clip_count: int = 0,
        speaker_count: Any = 0,
        total_duration_str: str = "00:00",
        total_time_taken_str: str = "0s",
        total_failover_str: str = "0s",
        done_count: int = 0,
        approved_count: int = 0,
        run_status_text: Optional[str] = None,
        **kwargs,
    ) -> None:
        if isinstance(speaker_count, (int, float)):
            spk_cnt = int(speaker_count)
            dur_str = total_duration_str
            t_taken = total_time_taken_str
            fo_str = total_failover_str
            d_cnt = done_count
            appr_cnt = approved_count
        else:
            # Backwards compatibility: 5 positional arguments
            spk_cnt = 0
            dur_str = str(speaker_count)
            t_taken = total_duration_str
            fo_str = total_time_taken_str
            d_cnt = clip_count
            appr_cnt = int(total_failover_str) if isinstance(total_failover_str, (int, float)) else 0

        self.labels[0].setText("Total")
        if run_status_text:
            self.labels[1].setText(f"  {run_status_text}")
        else:
            self.labels[1].setText(f"  {clip_count} clips")
        self.labels[2].setText(f"  {spk_cnt} speakers" if spk_cnt > 0 else "")
        self.labels[3].setText(dur_str)
        self.labels[4].setText(f"{clip_count} of {clip_count} timed" if (d_cnt == clip_count and clip_count > 0) else t_taken)
        self.labels[5].setText(fo_str)
        self.labels[6].setText(f"{d_cnt} Done" if d_cnt > 0 else "—")
        self.labels[7].setText(f"{appr_cnt} Approved" if appr_cnt > 0 else "—")
        self.labels[8].setText("—")


# ----------------------------------------------------------------------
# 11. Appearance Control (Light / Dark / System Segmented Control)
# ----------------------------------------------------------------------
class AppearanceControl(QFrame):
    """
    Compact three-option segmented control (Light, Dark, System) with icons.
    Emits appearanceChanged(str) and persists selection in ui.ini.
    """

    appearanceChanged = Signal(str)

    def __init__(self, parent: Optional[QWidget] = None, current_mode: str = "system", scale: float = 1.0, theme_mode: str = "light"):
        super().__init__(parent)
        self._scale = scale
        self._theme_mode = theme_mode
        self._current_mode = current_mode.lower()

        lay = QHBoxLayout(self)
        lay.setContentsMargins(2, 2, 2, 2)
        lay.setSpacing(2)

        self.seg = SegmentedControl(
            ["☀ Light", "🌙 Dark", "💻 System"],
            default_index=0 if self._current_mode == "light" else (1 if self._current_mode == "dark" else 2),
            parent=self,
            scale=scale * 0.9,
            theme_mode=theme_mode,
        )
        self.seg.selectionChanged.connect(self._on_selection_changed)
        lay.addWidget(self.seg)
        self.apply_theme(theme_mode)

    def _on_selection_changed(self, idx: int, text: str = "") -> None:
        modes = ["light", "dark", "system"]
        if 0 <= idx < len(modes):
            self._current_mode = modes[idx]
            self.appearanceChanged.emit(self._current_mode)

    def set_current_mode(self, mode: str) -> None:
        m = mode.lower()
        self._current_mode = m
        idx = 0 if m == "light" else (1 if m == "dark" else 2)
        self.seg.set_current_index(idx)

    def apply_theme(self, theme_mode: str = "light") -> None:
        self._theme_mode = theme_mode
        self.seg.apply_theme(theme_mode)


# ----------------------------------------------------------------------
# 12. Settings Sheet (Light / Dark / System Theme Switcher)
# ----------------------------------------------------------------------
class SettingsSheet(QDialog):
    """
    Settings sheet containing Theme switcher (Light, Dark, System),
    Connection status, quota mode, model profiles, and sizing slider.
    """

    settingsChanged = Signal(dict)

    def __init__(self, parent: Optional[QWidget] = None, theme_mode: Optional[str] = None):
        super().__init__(parent)
        self.setWindowTitle("Preferences & System Settings")
        self.resize(560, 620)
        self.setModal(True)

        self._settings = load_ui_settings()
        self._scale = self._settings.get("text_scale", 1.0)
        self._theme_mode = theme_mode or self._settings.get("theme", "light")

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(20, 20, 20, 20)
        main_layout.setSpacing(14)

        # Title Card
        title_card = Card(self, shadow=False, theme_mode=self._theme_mode)
        t_lay = QVBoxLayout(title_card)
        t_lay.setContentsMargins(16, 12, 16, 12)

        lbl_title = QLabel("Settings & Preferences", title_card)
        lbl_title.setFont(get_scaled_font("page_title", self._scale))
        t_lay.addWidget(lbl_title)

        lbl_sub = QLabel("Interface theme, live text scaling, and cloud pipeline quota.", title_card)
        lbl_sub.setFont(get_scaled_font("caption", self._scale))
        t_lay.addWidget(lbl_sub)
        main_layout.addWidget(title_card)

        # Scrollable Settings Body
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll_content = QWidget()
        s_lay = QVBoxLayout(scroll_content)
        s_lay.setContentsMargins(0, 0, 0, 0)
        s_lay.setSpacing(12)

        # Section 1: Appearance & Theme Card
        app_card = Card(scroll_content, shadow=False, theme_mode=self._theme_mode)
        a_lay = QVBoxLayout(app_card)
        a_lay.setContentsMargins(16, 14, 16, 14)
        a_lay.setSpacing(12)

        lbl_sec_app = QLabel("Appearance & Theme", app_card)
        lbl_sec_app.setFont(get_scaled_font("section_title", self._scale))
        a_lay.addWidget(lbl_sec_app)

        # Theme Switcher (Light, Dark, System)
        t_row = QHBoxLayout()
        t_row.addWidget(QLabel("Color Theme:", app_card))
        self.seg_theme = SegmentedControl(
            ["Light", "Dark", "System"],
            default_index=0 if self._theme_mode == "light" else (1 if self._theme_mode == "dark" else 2),
            parent=app_card,
            scale=self._scale,
            theme_mode=self._theme_mode,
        )
        self.seg_theme.selectionChanged.connect(self._on_theme_changed)
        t_row.addWidget(self.seg_theme)
        t_row.addStretch(1)
        a_lay.addLayout(t_row)

        # Text Scale Slider (85% to 150%)
        scale_row = QHBoxLayout()
        scale_row.addWidget(QLabel("Text Sizing (85% - 150%):", app_card))
        self.lbl_scale_val = QLabel(f"{int(self._scale * 100)}%", app_card)
        self.lbl_scale_val.setFont(get_scaled_font("body_bold", self._scale))
        scale_row.addWidget(self.lbl_scale_val)
        a_lay.addLayout(scale_row)

        self.slider_scale = QSlider(Qt.Horizontal, app_card)
        self.slider_scale.setRange(85, 150)
        self.slider_scale.setValue(int(self._scale * 100))
        self.slider_scale.valueChanged.connect(self._on_scale_slider_changed)
        a_lay.addWidget(self.slider_scale)

        # Table Row Density
        dens_row = QHBoxLayout()
        dens_row.addWidget(QLabel("Table Row Density:", app_card))
        self.radio_comfort = QRadioButton("Comfortable (36px)", app_card)
        self.radio_compact = QRadioButton("Compact (28px)", app_card)
        if self._settings.get("table_density") == "compact":
            self.radio_compact.setChecked(True)
        else:
            self.radio_comfort.setChecked(True)
        dens_row.addWidget(self.radio_comfort)
        dens_row.addWidget(self.radio_compact)
        dens_row.addStretch(1)
        a_lay.addLayout(dens_row)

        s_lay.addWidget(app_card)

        # Section 2: Quota & Cloud Engine Card
        quota_card = Card(scroll_content, shadow=False, theme_mode=self._theme_mode)
        q_lay = QVBoxLayout(quota_card)
        q_lay.setContentsMargins(16, 14, 16, 14)
        q_lay.setSpacing(10)

        lbl_sec_quota = QLabel("Cloud Engine & Quota Mode", quota_card)
        lbl_sec_quota.setFont(get_scaled_font("section_title", self._scale))
        q_lay.addWidget(lbl_sec_quota)

        q_mode_row = QHBoxLayout()
        q_mode_row.addWidget(QLabel("Quota Mode:", quota_card))
        self.seg_quota_mode = SegmentedControl(
            ["Rate Limit (Default)", "Credits (Manual Sync)"],
            default_index=0 if self._settings.get("quota_mode") == "rate_limit" else 1,
            parent=quota_card,
            scale=self._scale,
            theme_mode=self._theme_mode,
        )
        q_mode_row.addWidget(self.seg_quota_mode)
        q_mode_row.addStretch(1)
        q_lay.addLayout(q_mode_row)

        rate_limit_row = QHBoxLayout()
        rate_limit_row.addWidget(QLabel("Rate Limit per Minute:", quota_card))
        self.txt_rate_limit = QLineEdit(str(self._settings.get("rate_limit_per_min", 40)), quota_card)
        self.txt_rate_limit.setFixedWidth(80)
        rate_limit_row.addWidget(self.txt_rate_limit)
        rate_limit_row.addWidget(QLabel("(default 40 req/min for free tier)", quota_card))
        rate_limit_row.addStretch(1)
        q_lay.addLayout(rate_limit_row)

        s_lay.addWidget(quota_card)
        s_lay.addStretch(1)
        scroll.setWidget(scroll_content)
        main_layout.addWidget(scroll)

        # Action Buttons
        btn_box = QHBoxLayout()
        btn_box.addStretch(1)

        btn_cancel = SecondaryButton("Cancel", self, scale=self._scale, theme_mode=self._theme_mode)
        btn_cancel.clicked.connect(self.reject)
        btn_box.addWidget(btn_cancel)

        btn_save = PrimaryButton("Apply & Save in ui.ini", self, scale=self._scale, theme_mode=self._theme_mode)
        btn_save.clicked.connect(self._on_save)
        btn_box.addWidget(btn_save)

        main_layout.addLayout(btn_box)
        self.apply_theme(self._theme_mode)

    def apply_theme(self, theme_mode: str = "light") -> None:
        self._theme_mode = theme_mode
        pal = get_theme_palette(theme_mode)
        self.setStyleSheet(f"QDialog {{ background-color: {pal['window_bg']}; color: {pal['primary_text']}; }}")

    def _on_theme_changed(self, idx: int, text: str) -> None:
        mode = text.lower()
        self.apply_theme(mode)

    def _on_scale_slider_changed(self, val: int) -> None:
        self.lbl_scale_val.setText(f"{val}%")

    def _on_save(self) -> None:
        theme = self.seg_theme.current_text().lower()
        new_scale = self.slider_scale.value() / 100.0
        density = "compact" if self.radio_compact.isChecked() else "comfortable"
        q_mode = "rate_limit" if self.seg_quota_mode.current_index() == 0 else "credits"
        try:
            r_limit = int(self.txt_rate_limit.text().strip())
        except ValueError:
            r_limit = 40

        self._settings["theme"] = theme
        self._settings["text_scale"] = new_scale
        self._settings["table_density"] = density
        self._settings["quota_mode"] = q_mode
        self._settings["rate_limit_per_min"] = r_limit

        save_ui_settings(self._settings)
        self.settingsChanged.emit(self._settings)
        self.accept()


# ----------------------------------------------------------------------
# 13. Test Transcription Dialog (5-Stage Preflight Results)
# ----------------------------------------------------------------------
class QtTestTranscriptionDialog(QDialog):
    """
    Modal dialog displaying the 5-stage preflight test transcription results
    using the bundled 5s dummy sample (zero real audio transmitted).
    """

    def __init__(
        self,
        res: Any,
        profile: Any,
        parent: Optional[QWidget] = None,
        theme_mode: str = "light",
        scale: float = 1.0,
    ):
        super().__init__(parent)
        self.setWindowTitle("Cloud Test Transcription (Bundled 5s Sample)")
        self.setMinimumSize(int(580 * scale), int(360 * scale))
        self.resize(int(620 * scale), int(400 * scale))
        pal = get_theme_palette(theme_mode)
        self.setStyleSheet(f"QDialog {{ background-color: {pal['window_bg']}; color: {pal['primary_text']}; }}")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        # Header Card
        hdr_card = Card(self, shadow=False, theme_mode=theme_mode)
        h_lay = QVBoxLayout(hdr_card)
        h_lay.setContentsMargins(16, 12, 16, 12)
        h_lay.setSpacing(4)

        status_text = "PASS — Cloud Transcription Ready" if res.success else f"FAIL — {res.error_class or 'Configuration Error'}"
        lbl_status = QLabel(status_text, hdr_card)
        lbl_status.setFont(get_scaled_font("body_bold", scale * 1.15))
        lbl_status.setStyleSheet(f"color: {'#16A34A' if res.success else '#DC2626'};")
        h_lay.addWidget(lbl_status)

        sub_text = f"Profile: {getattr(profile, 'name', 'NVIDIA')}  ·  Total time: {res.total_duration_s:.2f}s (Bundled audio only)"
        lbl_sub = QLabel(sub_text, hdr_card)
        lbl_sub.setFont(get_scaled_font("caption", scale))
        lbl_sub.setStyleSheet(f"color: {pal['secondary_text']};")
        h_lay.addWidget(lbl_sub)
        layout.addWidget(hdr_card)

        # Stages list
        stages_card = Card(self, shadow=False, theme_mode=theme_mode)
        s_lay = QVBoxLayout(stages_card)
        s_lay.setContentsMargins(14, 10, 14, 10)
        s_lay.setSpacing(8)

        for stg in getattr(res, "stages", []):
            row = QHBoxLayout()
            row.setSpacing(10)

            pill = StatusPill(stg.status, status_type="done" if stg.status == "PASS" else "missing", scale=scale * 0.85, theme_mode=theme_mode)
            row.addWidget(pill)

            lbl_name = QLabel(f"{stg.stage_name} ({stg.duration_s:.3f}s):", stages_card)
            lbl_name.setFont(get_scaled_font("body_bold", scale * 0.95))
            row.addWidget(lbl_name)

            lbl_msg = QLabel(stg.message, stages_card)
            lbl_msg.setFont(get_scaled_font("body", scale * 0.95))
            lbl_msg.setStyleSheet(f"color: {pal['secondary_text']};")
            lbl_msg.setWordWrap(True)
            row.addWidget(lbl_msg, stretch=1)

            s_lay.addLayout(row)

        layout.addWidget(stages_card, stretch=1)

        # Close button
        b_lay = QHBoxLayout()
        b_lay.addStretch(1)
        btn_close = PrimaryButton("Close", self, scale=scale, theme_mode=theme_mode)
        btn_close.clicked.connect(self.accept)
        b_lay.addWidget(btn_close)
        layout.addLayout(b_lay)


# ----------------------------------------------------------------------
# 14. Voice Vault Unlock & Initialize Dialogs
# ----------------------------------------------------------------------
class QtUnlockVaultDialog(QDialog):
    """Masked dialog to unlock Voice Vault with passphrase in Qt."""

    def __init__(self, vault: Any, parent: Optional[QWidget] = None, theme_mode: str = "light", scale: float = 1.0):
        super().__init__(parent)
        self.vault = vault
        self._scale = scale
        self._theme_mode = theme_mode
        self.setWindowTitle("Unlock Voice Vault")
        self.setFixedWidth(int(420 * scale))
        pal = get_theme_palette(theme_mode)
        self.setStyleSheet(f"QDialog {{ background-color: {pal['window_bg']}; color: {pal['primary_text']}; }}")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        lbl = QLabel("Voice Vault is locked.\nEnter passphrase to access speaker identities and voice profiles:", self)
        lbl.setWordWrap(True)
        lbl.setFont(get_scaled_font("body", scale))
        layout.addWidget(lbl)

        self.txt_pass = QLineEdit(self)
        self.txt_pass.setEchoMode(QLineEdit.Password)
        self.txt_pass.setPlaceholderText("Enter vault passphrase")
        self.txt_pass.setFixedHeight(int(34 * scale))
        layout.addWidget(self.txt_pass)

        self.lbl_err = QLabel("", self)
        self.lbl_err.setStyleSheet("color: #DC2626;")
        self.lbl_err.setFont(get_scaled_font("caption", scale))
        layout.addWidget(self.lbl_err)

        btn_box = QHBoxLayout()
        btn_box.addStretch(1)
        btn_cancel = SecondaryButton("Cancel", self, scale=scale, theme_mode=theme_mode)
        btn_cancel.clicked.connect(self.reject)
        btn_box.addWidget(btn_cancel)

        btn_unlock = PrimaryButton("Unlock", self, scale=scale, theme_mode=theme_mode)
        btn_unlock.clicked.connect(self._do_unlock)
        btn_box.addWidget(btn_unlock)
        layout.addLayout(btn_box)

    def _do_unlock(self) -> None:
        p = self.txt_pass.text()
        ok, msg = self.vault.unlock(p)
        if ok:
            self.accept()
        else:
            self.lbl_err.setText(msg or "Incorrect passphrase.")


class QtInitializeVaultDialog(QDialog):
    """Dialog to initialize Voice Vault with passphrase and strength meter in Qt."""

    def __init__(self, vault: Any, parent: Optional[QWidget] = None, theme_mode: str = "light", scale: float = 1.0):
        super().__init__(parent)
        self.vault = vault
        self._scale = scale
        self._theme_mode = theme_mode
        self.setWindowTitle("Initialize Voice Vault")
        self.setFixedWidth(int(450 * scale))
        pal = get_theme_palette(theme_mode)
        self.setStyleSheet(f"QDialog {{ background-color: {pal['window_bg']}; color: {pal['primary_text']}; }}")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(10)

        lbl = QLabel("Set a passphrase to secure the Voice Vault and speaker profiles.\nPassphrase must be at least 12 characters.", self)
        lbl.setWordWrap(True)
        lbl.setFont(get_scaled_font("body", scale))
        layout.addWidget(lbl)

        self.txt_p1 = QLineEdit(self)
        self.txt_p1.setEchoMode(QLineEdit.Password)
        self.txt_p1.setPlaceholderText("Passphrase (min 12 characters)")
        self.txt_p1.textChanged.connect(self._on_text_changed)
        layout.addWidget(self.txt_p1)

        self.txt_p2 = QLineEdit(self)
        self.txt_p2.setEchoMode(QLineEdit.Password)
        self.txt_p2.setPlaceholderText("Confirm passphrase")
        self.txt_p2.textChanged.connect(self._on_text_changed)
        layout.addWidget(self.txt_p2)

        self.lbl_strength = QLabel("Strength: —", self)
        self.lbl_strength.setFont(get_scaled_font("caption", scale))
        layout.addWidget(self.lbl_strength)

        self.lbl_err = QLabel("", self)
        self.lbl_err.setStyleSheet("color: #DC2626;")
        self.lbl_err.setFont(get_scaled_font("caption", scale))
        layout.addWidget(self.lbl_err)

        btn_box = QHBoxLayout()
        btn_box.addStretch(1)
        btn_cancel = SecondaryButton("Cancel", self, scale=scale, theme_mode=theme_mode)
        btn_cancel.clicked.connect(self.reject)
        btn_box.addWidget(btn_cancel)

        self.btn_init = PrimaryButton("Initialize Vault", self, scale=scale, theme_mode=theme_mode)
        self.btn_init.setEnabled(False)
        self.btn_init.clicked.connect(self._do_init)
        btn_box.addWidget(self.btn_init)
        layout.addLayout(btn_box)

    def _on_text_changed(self) -> None:
        from scribe_core.voice_vault import evaluate_passphrase_strength
        p1 = self.txt_p1.text()
        p2 = self.txt_p2.text()
        info = evaluate_passphrase_strength(p1)
        label = info.get("label", "Weak")
        words = info.get("word_count", 0)
        self.lbl_strength.setText(f"Strength: {label} ({words} words)")
        if not info.get("valid", False):
            feedback = info.get("feedback", ["Passphrase too short."])
            self.lbl_err.setText(feedback[0] if feedback else "Passphrase does not meet requirements.")
            self.btn_init.setEnabled(False)
        elif p1 != p2:
            self.lbl_err.setText("Passphrases do not match.")
            self.btn_init.setEnabled(False)
        else:
            self.lbl_err.setText("")
            self.btn_init.setEnabled(True)

    def _do_init(self) -> None:
        ok, msg = self.vault.initialize(self.txt_p1.text())
        if ok:
            self.accept()
        else:
            self.lbl_err.setText(msg or "Failed to initialize vault.")


# ----------------------------------------------------------------------
# 15. Voice Vault Status Chip
# ----------------------------------------------------------------------
class QtVaultChip(QWidget):
    """
    Compact Voice Vault status chip placed in the header or toolbar.
    Shows lock state and allows one-click locking / unlocking.
    """

    stateChanged = Signal(bool)

    def __init__(self, parent: Optional[QWidget] = None, vault: Optional[Any] = None, scale: float = 1.0, theme_mode: str = "light"):
        super().__init__(parent)
        self._scale = scale
        self._theme_mode = theme_mode

        if vault is not None:
            self.vault = vault
        else:
            from scribe_core.voice_vault import VoiceVault
            self.vault = VoiceVault()

        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(0)

        self._btn = SecondaryButton("🔒 Vault Locked", self, scale=scale * 0.9, theme_mode=theme_mode)
        self._btn.clicked.connect(self._toggle_or_prompt)
        self._layout.addWidget(self._btn)

        self.refresh()

    def refresh(self) -> None:
        st = self.vault.state()
        if st == "uninitialized":
            self._btn.setText("🔒 Setup Vault")
            self._btn.setToolTip("Voice Vault uninitialized. Click to configure passphrase.")
        elif st == "locked":
            self._btn.setText("🔒 Vault Locked")
            self._btn.setToolTip("Voice Vault is locked. Click to enter passphrase.")
        elif st == "locking_soon":
            rem = max(1, int(self.vault.get_remaining_idle_seconds() / 60.0 + 0.5))
            self._btn.setText(f"⏳ Locking soon ({rem}m)")
            self._btn.setToolTip(f"Voice Vault will lock soon due to inactivity ({rem}m remaining). Click to lock now.")
        else:
            self._btn.setText("🔓 Vault Unlocked")
            self._btn.setToolTip("Voice Vault is unlocked. Click to lock.")

    def _toggle_or_prompt(self) -> None:
        if not self.vault.is_initialized():
            dlg = QtInitializeVaultDialog(self.vault, self, self._theme_mode, self._scale)
            if dlg.exec():
                self.refresh()
                self.stateChanged.emit(self.vault.is_unlocked())
        elif self.vault.is_unlocked():
            self.vault.lock()
            self.refresh()
            Toast("Voice Vault locked.", self).show_toast(self.window())
            self.stateChanged.emit(False)
        else:
            dlg = QtUnlockVaultDialog(self.vault, self, self._theme_mode, self._scale)
            if dlg.exec():
                self.refresh()
                self.stateChanged.emit(True)

    def apply_theme(self, theme_mode: str = "light") -> None:
        self._theme_mode = theme_mode
        self._btn.apply_theme(theme_mode)

