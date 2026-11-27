"""
gui/theme.py

Design System v2 central theme module for Session Scribe.
Provides:
  - Windows DPI-awareness configuration (Per-Monitor V2, V1, System fallback).
  - Win32 system helpers: monitor work area, window DPI, immersive dark title bar, registry theme reading.
  - Multi-theme manager supporting: System (auto-detects Windows theme & polls), Light, and Dark.
  - Type scale: app title 18 pt semibold, section titles 12 pt semibold, body 10 pt, table text 10 pt, captions 9 pt, Consolas 10 pt.
    Uses Segoe UI Variable with fallback to Segoe UI.
  - WCAG AA compliant color palettes (contrast ratio >= 4.5:1 on all text/bg pairs for both Light and Dark).
  - TTK style configuration using extended clam theme:
    * 3px blue underline on selected notebook tab.
    * Table density: Comfortable (30px row height) or Compact (24px row height).
    * Selected row #DBEAFE with navy text (#0B2545) in light, #1E3A5F with #93C5FD in dark.
    * Row tinting ONLY for running, failed, and diff settings.
    * Flat 14px scrollbars and blue accent progress bars.
    * Primary and secondary button styles.
  - User-controlled sizing: dynamic text scale (85% to 150%) and table density.
  - SegmentedControl: modern pill-shaped alternative to radio buttons.
  - ToggleSwitch: modern switch alternative to plain checkboxes.
  - MessageBar component with 4px left accent and crisp non-italic text.
"""

from __future__ import annotations

import logging
import sys
import time
import tkinter as tk
from tkinter import ttk, font
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# High-DPI Awareness & Win32 Helpers
# ---------------------------------------------------------------------------

def make_dpi_aware() -> None:
    """Enable per-monitor DPI awareness on Windows to prevent blurry text."""
    if sys.platform == "win32":
        try:
            import ctypes
            # Try Per-Monitor V2 (-4) first
            ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        except Exception:
            try:
                # Shcore SetProcessDpiAwareness: 2 = PROCESS_PER_MONITOR_DPI_AWARE
                ctypes.windll.shcore.SetProcessDpiAwareness(2)
            except Exception:
                try:
                    # Fallback to User32 SetProcessDPIAware
                    ctypes.windll.user32.SetProcessDPIAware()
                except Exception:
                    pass


def get_monitor_work_area(hwnd: Optional[int] = None) -> Tuple[int, int, int, int]:
    """
    Return (x, y, width, height) of the usable monitor work area (excluding taskbar).
    Supports multi-monitor setups if an HWND is provided.
    """
    if sys.platform != "win32":
        return (0, 0, 1920, 1040)
    try:
        import ctypes
        from ctypes import wintypes

        class RECT(ctypes.Structure):
            _fields_ = [
                ("left", wintypes.LONG),
                ("top", wintypes.LONG),
                ("right", wintypes.LONG),
                ("bottom", wintypes.LONG),
            ]

        class MONITORINFO(ctypes.Structure):
            _fields_ = [
                ("cbSize", wintypes.DWORD),
                ("rcMonitor", RECT),
                ("rcWork", RECT),
                ("dwFlags", wintypes.DWORD),
            ]

        user32 = ctypes.windll.user32
        if hwnd:
            MONITOR_DEFAULTTONEAREST = 2
            hMon = user32.MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST)
            if hMon:
                info = MONITORINFO()
                info.cbSize = ctypes.sizeof(MONITORINFO)
                if user32.GetMonitorInfoW(hMon, ctypes.byref(info)):
                    r = info.rcWork
                    return (r.left, r.top, r.right - r.left, r.bottom - r.top)

        # Fallback to primary work area
        SPI_GETWORKAREA = 0x0030
        rect = RECT()
        if user32.SystemParametersInfoW(SPI_GETWORKAREA, 0, ctypes.byref(rect), 0):
            return (rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top)
    except Exception:
        pass
    return (0, 0, 1920, 1040)


def get_window_dpi(hwnd: Optional[int] = None) -> int:
    """Return the current DPI of the window or primary monitor."""
    if sys.platform != "win32":
        return 96
    try:
        import ctypes
        user32 = ctypes.windll.user32
        if hwnd:
            try:
                dpi = user32.GetDpiForWindow(hwnd)
                if dpi > 0:
                    return dpi
            except Exception:
                pass
            try:
                hdc = user32.GetDC(hwnd)
                LOGPIXELSX = 88
                dpi = ctypes.windll.gdi32.GetDeviceCaps(hdc, LOGPIXELSX)
                user32.ReleaseDC(hwnd, hdc)
                if dpi > 0:
                    return dpi
            except Exception:
                pass
        # System DPI fallback
        hdc = user32.GetDC(0)
        dpi = ctypes.windll.gdi32.GetDeviceCaps(hdc, 88)
        user32.ReleaseDC(0, hdc)
        return dpi if dpi > 0 else 96
    except Exception:
        return 96


def set_window_dark_title_bar(root_or_hwnd: Any, dark: bool = True) -> bool:
    """
    Configure Windows DWM to draw an immersive dark or light title bar.
    In Tkinter, root.winfo_id() is often a child window; we resolve parent HWND if needed.
    """
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        from ctypes import wintypes, c_int, byref

        hwnd = root_or_hwnd
        if hasattr(root_or_hwnd, "winfo_id"):
            try:
                raw_id = root_or_hwnd.winfo_id()
                parent = ctypes.windll.user32.GetParent(raw_id)
                hwnd = parent if parent else raw_id
            except Exception:
                return False

        if not hwnd:
            return False

        dwmapi = ctypes.windll.dwmapi
        DWMWA_USE_IMMERSIVE_DARK_MODE = 20
        DWMWA_USE_IMMERSIVE_DARK_MODE_OLD = 19
        val = c_int(1 if dark else 0)

        res = dwmapi.DwmSetWindowAttribute(hwnd, DWMWA_USE_IMMERSIVE_DARK_MODE, byref(val), ctypes.sizeof(val))
        if res != 0:
            dwmapi.DwmSetWindowAttribute(hwnd, DWMWA_USE_IMMERSIVE_DARK_MODE_OLD, byref(val), ctypes.sizeof(val))
        return True
    except Exception:
        return False


def get_windows_apps_theme() -> str:
    """
    Read Windows registry to check system dark mode setting for apps:
    HKCU\Software\Microsoft\Windows\CurrentVersion\Themes\Personalize\AppsUseLightTheme
    Returns 'dark' or 'light'.
    """
    if sys.platform != "win32":
        return "light"
    try:
        import winreg
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
        )
        val, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
        winreg.CloseKey(key)
        return "light" if val == 1 else "dark"
    except Exception:
        return "light"


# ---------------------------------------------------------------------------
# WCAG Contrast Math
# ---------------------------------------------------------------------------

def srgb_to_lin(c: float) -> float:
    c = c / 255.0
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def relative_luminance(hex_color: str) -> float:
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    return 0.2126 * srgb_to_lin(r) + 0.7152 * srgb_to_lin(g) + 0.0722 * srgb_to_lin(b)


def contrast_ratio(hex_fg: str, hex_bg: str) -> float:
    """Calculate WCAG contrast ratio between foreground and background."""
    l1 = relative_luminance(hex_fg)
    l2 = relative_luminance(hex_bg)
    lighter = max(l1, l2)
    darker = min(l1, l2)
    return (lighter + 0.05) / (darker + 0.05)


# ---------------------------------------------------------------------------
# Complete Palettes: Light & Dark
# ---------------------------------------------------------------------------

PALETTE_LIGHT = {
    "app_bg": "#F3F5F9",
    "card_bg": "#FFFFFF",
    "input_bg": "#FFFFFF",
    "input_border": "#CBD5E1",
    "border": "#D5DBE5",
    "border_focus": "#1D4ED8",
    "header_band": "#0B2545",
    "title_text": "#FFFFFF",
    "subtitle_text": "#C9D6EA",
    "text_primary": "#111827",
    "text_secondary": "#334155",      # >= 10:1 on card_bg
    "text_muted": "#4B5563",
    "placeholder": "#64748B",         # 4.7:1 on white (>= 4.5:1)
    "primary_btn": "#1D4ED8",
    "primary_btn_hover": "#1E40AF",
    "btn_border": "#94A3B8",
    "disabled_bg": "#E5E7EB",
    "disabled_fg": "#475569",
    "readonly_bg": "#F1F5F9",
    "tab_unselected_bg": "#E2E8F0",
    "tab_unselected_fg": "#1F2937",
    "tab_selected_bg": "#FFFFFF",
    "tab_selected_fg": "#0B2545",
    "tab_underline": "#1D4ED8",
    "table_header_bg": "#E8EDF5",
    "row_even": "#FFFFFF",
    "row_odd": "#F8FAFC",
    "row_sel_bg": "#DBEAFE",
    "row_sel_fg": "#0B2545",
    "tint_running_bg": "#DBEAFE",
    "tint_running_fg": "#1E3A8A",
    "tint_failed_bg": "#FEE2E2",
    "tint_failed_fg": "#7F1D1D",
    "tint_diff_bg": "#FEF3C7",
    "tint_diff_fg": "#78350F",
    "scroll_track": "#E5E7EB",
    "scroll_thumb": "#94A3B8",
    "scroll_active": "#64748B",
    "info_fg": "#1D4ED8",
    "success_fg": "#15803D",
    "warning_fg": "#B45309",
    "error_fg": "#B91C1C",
    "msg_themes": {
        "info": {"bg": "#EFF6FF", "fg": "#1E3A8A", "bar": "#1D4ED8"},
        "success": {"bg": "#F0FDF4", "fg": "#14532D", "bar": "#16A34A"},
        "warning": {"bg": "#FFFBEB", "fg": "#78350F", "bar": "#D97706"},
        "error": {"bg": "#FEF2F2", "fg": "#7F1D1D", "bar": "#DC2626"},
    },
}

PALETTE_GRAPHITE = {
    "name": "Graphite",
    "app_bg": "#1C1C1E",
    "card_bg": "#242426",
    "input_bg": "#2E2E32",            # Flat input surface one step lighter than card_bg #242426, not white
    "input_border": "#3F3F46",
    "border": "#3A3A3C",
    "border_focus": "#0078D4",
    "header_band": "#141416",
    "title_text": "#F5F5F7",
    "subtitle_text": "#A1A1A6",
    "text_primary": "#F5F5F7",
    "text_secondary": "#B8B8BD",      # >= 7.8:1 on card_bg #242426
    "text_muted": "#98989D",
    "placeholder": "#A1A1AA",         # >= 5.2:1 on input_bg #2E2E32 (>= 4.5:1)
    "primary_btn": "#0078D4",       # 4.53:1 contrast with white
    "primary_btn_hover": "#1084D9",
    "btn_border": "#48484A",
    "disabled_bg": "#202022",
    "disabled_fg": "#71717A",
    "readonly_bg": "#28282A",
    "tab_unselected_bg": "#242426",
    "tab_unselected_fg": "#A1A1A6",
    "tab_selected_bg": "#2C2C2E",
    "tab_selected_fg": "#F5F5F7",
    "tab_underline": "#0A84FF",
    "table_header_bg": "#242426",
    "row_even": "#2C2C2E",
    "row_odd": "#242426",
    "row_sel_bg": "#1E3A5F",
    "row_sel_fg": "#93C5FD",
    "tint_running_bg": "#102A45",
    "tint_running_fg": "#93C5FD",
    "tint_failed_bg": "#3D1414",
    "tint_failed_fg": "#FCA5A5",
    "tint_diff_bg": "#3D2E0A",
    "tint_diff_fg": "#FDE047",
    "scroll_track": "#242426",
    "scroll_thumb": "#48484A",
    "scroll_active": "#636366",
    "info_fg": "#93C5FD",
    "success_fg": "#86EFAC",
    "warning_fg": "#FDE047",
    "error_fg": "#FCA5A5",
    "msg_themes": {
        "info": {"bg": "#102A45", "fg": "#93C5FD", "bar": "#0A84FF"},
        "success": {"bg": "#14381C", "fg": "#86EFAC", "bar": "#30D158"},
        "warning": {"bg": "#3D2E0A", "fg": "#FDE047", "bar": "#FF9F0A"},
        "error": {"bg": "#3D1414", "fg": "#FCA5A5", "bar": "#FF453A"},
    },
}

PALETTE_MIDNIGHT = {
    "name": "Midnight",
    "app_bg": "#0D1117",
    "card_bg": "#161B22",
    "input_bg": "#21262D",            # One step lighter than card_bg #161B22
    "input_border": "#30363D",
    "border": "#30363D",
    "border_focus": "#58A6FF",
    "header_band": "#090D13",
    "title_text": "#F0F6FC",
    "subtitle_text": "#8B949E",
    "text_primary": "#F0F6FC",
    "text_secondary": "#B0B8C4",      # >= 7.0:1 on card_bg #161B22
    "text_muted": "#6E7681",
    "placeholder": "#9BA4AF",         # >= 6.0:1 on input_bg #21262D
    "primary_btn": "#1F6FEB",
    "primary_btn_hover": "#388BFD",
    "btn_border": "#30363D",
    "disabled_bg": "#13171F",
    "disabled_fg": "#6E7681",
    "readonly_bg": "#1A202C",
    "tab_unselected_bg": "#13171F",
    "tab_unselected_fg": "#8B949E",
    "tab_selected_bg": "#161B22",
    "tab_selected_fg": "#F0F6FC",
    "tab_underline": "#58A6FF",
    "table_header_bg": "#13171F",
    "row_even": "#161B22",
    "row_odd": "#11151C",
    "row_sel_bg": "#1F385C",
    "row_sel_fg": "#79C0FF",
    "tint_running_bg": "#112240",
    "tint_running_fg": "#79C0FF",
    "tint_failed_bg": "#3B1219",
    "tint_failed_fg": "#FFA198",
    "tint_diff_bg": "#382806",
    "tint_diff_fg": "#E3B341",
    "scroll_track": "#13171F",
    "scroll_thumb": "#30363D",
    "scroll_active": "#484F58",
    "info_fg": "#79C0FF",
    "success_fg": "#7EE787",
    "warning_fg": "#E3B341",
    "error_fg": "#FFA198",
    "msg_themes": {
        "info": {"bg": "#112240", "fg": "#79C0FF", "bar": "#1F6FEB"},
        "success": {"bg": "#112E1B", "fg": "#7EE787", "bar": "#2EA043"},
        "warning": {"bg": "#382806", "fg": "#E3B341", "bar": "#D29922"},
        "error": {"bg": "#3B1219", "fg": "#FFA198", "bar": "#F85149"},
    },
}

PALETTE_DARK = PALETTE_GRAPHITE

# Active module-level color constants (initially Light)
COLOR_APP_BG = PALETTE_LIGHT["app_bg"]
COLOR_CARD_BG = PALETTE_LIGHT["card_bg"]
COLOR_BORDER = PALETTE_LIGHT["border"]
COLOR_HEADER_NAVY = PALETTE_LIGHT["header_band"]
COLOR_TITLE_WHITE = PALETTE_LIGHT["title_text"]
COLOR_SUBTITLE = PALETTE_LIGHT["subtitle_text"]
COLOR_TEXT_PRIMARY = PALETTE_LIGHT["text_primary"]
COLOR_TEXT_SECONDARY = PALETTE_LIGHT["text_secondary"]
COLOR_TEXT_MUTED = PALETTE_LIGHT["text_muted"]
COLOR_PLACEHOLDER = PALETTE_LIGHT["placeholder"]
COLOR_PRIMARY_BTN = PALETTE_LIGHT["primary_btn"]
COLOR_PRIMARY_BTN_HOVER = PALETTE_LIGHT["primary_btn_hover"]
COLOR_BTN_BORDER = PALETTE_LIGHT["btn_border"]
COLOR_DISABLED_BG = PALETTE_LIGHT["disabled_bg"]
COLOR_DISABLED_FG = PALETTE_LIGHT["disabled_fg"]
COLOR_TAB_UNSELECTED_BG = PALETTE_LIGHT["tab_unselected_bg"]
COLOR_TAB_UNSELECTED_FG = PALETTE_LIGHT["tab_unselected_fg"]
COLOR_TAB_SELECTED_BG = PALETTE_LIGHT["tab_selected_bg"]
COLOR_TAB_SELECTED_FG = PALETTE_LIGHT["tab_selected_fg"]
COLOR_TAB_UNDERLINE = PALETTE_LIGHT["tab_underline"]
COLOR_TABLE_HEADER_BG = PALETTE_LIGHT["table_header_bg"]
COLOR_ROW_EVEN = PALETTE_LIGHT["row_even"]
COLOR_ROW_ODD = PALETTE_LIGHT["row_odd"]
COLOR_SEL_BG = PALETTE_LIGHT["row_sel_bg"]
COLOR_SEL_FG = PALETTE_LIGHT["row_sel_fg"]
TINT_RUNNING_BG = PALETTE_LIGHT["tint_running_bg"]
TINT_RUNNING_FG = PALETTE_LIGHT["tint_running_fg"]
TINT_FAILED_BG = PALETTE_LIGHT["tint_failed_bg"]
TINT_FAILED_FG = PALETTE_LIGHT["tint_failed_fg"]
TINT_DIFF_BG = PALETTE_LIGHT["tint_diff_bg"]
TINT_DIFF_FG = PALETTE_LIGHT["tint_diff_fg"]
COLOR_INPUT_BG = PALETTE_LIGHT["input_bg"]
COLOR_INPUT_BORDER = PALETTE_LIGHT["input_border"]
COLOR_BORDER_FOCUS = PALETTE_LIGHT["border_focus"]
COLOR_READONLY_BG = PALETTE_LIGHT["readonly_bg"]
COLOR_INFO_FG = PALETTE_LIGHT["info_fg"]
COLOR_SUCCESS_FG = PALETTE_LIGHT["success_fg"]
COLOR_WARNING_FG = PALETTE_LIGHT["warning_fg"]
COLOR_ERROR_FG = PALETTE_LIGHT["error_fg"]

STATUS_COLORS: Dict[str, Tuple[str, str]] = {
    "done": (COLOR_ROW_EVEN, COLOR_TEXT_PRIMARY),
    "done_test": (COLOR_ROW_EVEN, COLOR_TEXT_PRIMARY),
    "running": (TINT_RUNNING_BG, TINT_RUNNING_FG),
    "edited": (COLOR_ROW_EVEN, COLOR_TEXT_PRIMARY),
    "diff": (TINT_DIFF_BG, TINT_DIFF_FG),
    "missing": (TINT_FAILED_BG, TINT_FAILED_FG),
    "failed": (TINT_FAILED_BG, TINT_FAILED_FG),
    "forced": (COLOR_ROW_EVEN, COLOR_TEXT_PRIMARY),
    "resuming": (TINT_RUNNING_BG, TINT_RUNNING_FG),
}

MSG_THEMES = dict(PALETTE_LIGHT["msg_themes"])

# Verification table for WCAG AA (>= 4.5:1)
THEME_COLOR_PAIRS = [
    # Light theme pairs
    ("Title white on navy header", PALETTE_LIGHT["title_text"], PALETTE_LIGHT["header_band"]),
    ("Subtitle on navy header", PALETTE_LIGHT["subtitle_text"], PALETTE_LIGHT["header_band"]),
    ("Primary text on white card", PALETTE_LIGHT["text_primary"], PALETTE_LIGHT["card_bg"]),
    ("Primary text on app background", PALETTE_LIGHT["text_primary"], PALETTE_LIGHT["app_bg"]),
    ("Secondary text on white card", PALETTE_LIGHT["text_secondary"], PALETTE_LIGHT["card_bg"]),
    ("Secondary text on app background", PALETTE_LIGHT["text_secondary"], PALETTE_LIGHT["app_bg"]),
    ("Muted text on white card", PALETTE_LIGHT["text_muted"], PALETTE_LIGHT["card_bg"]),
    ("Placeholder text on white entry", PALETTE_LIGHT["placeholder"], PALETTE_LIGHT["card_bg"]),
    ("Primary button white text on blue", "#FFFFFF", PALETTE_LIGHT["primary_btn"]),
    ("Primary button hover white text on dark blue", "#FFFFFF", PALETTE_LIGHT["primary_btn_hover"]),
    ("Secondary button text on white", PALETTE_LIGHT["text_primary"], PALETTE_LIGHT["card_bg"]),
    ("Disabled button text on disabled bg", PALETTE_LIGHT["disabled_fg"], PALETTE_LIGHT["disabled_bg"]),
    ("Selected tab text on white tab", PALETTE_LIGHT["tab_selected_fg"], PALETTE_LIGHT["tab_selected_bg"]),
    ("Unselected tab text on slate tab", PALETTE_LIGHT["tab_unselected_fg"], PALETTE_LIGHT["tab_unselected_bg"]),
    ("Table header navy text on header bg", PALETTE_LIGHT["header_band"], PALETTE_LIGHT["table_header_bg"]),
    ("Table primary text on even row", PALETTE_LIGHT["text_primary"], PALETTE_LIGHT["row_even"]),
    ("Table primary text on odd row", PALETTE_LIGHT["text_primary"], PALETTE_LIGHT["row_odd"]),
    ("Selected row navy text on blue row", PALETTE_LIGHT["row_sel_fg"], PALETTE_LIGHT["row_sel_bg"]),
    ("Running row navy text on blue tint", PALETTE_LIGHT["tint_running_fg"], PALETTE_LIGHT["tint_running_bg"]),
    ("Failed row red text on red tint", PALETTE_LIGHT["tint_failed_fg"], PALETTE_LIGHT["tint_failed_bg"]),
    ("Diff settings row amber text on amber tint", PALETTE_LIGHT["tint_diff_fg"], PALETTE_LIGHT["tint_diff_bg"]),
    ("Info message text on info bg", PALETTE_LIGHT["msg_themes"]["info"]["fg"], PALETTE_LIGHT["msg_themes"]["info"]["bg"]),
    ("Success message text on success bg", PALETTE_LIGHT["msg_themes"]["success"]["fg"], PALETTE_LIGHT["msg_themes"]["success"]["bg"]),
    ("Warning message text on warning bg", PALETTE_LIGHT["msg_themes"]["warning"]["fg"], PALETTE_LIGHT["msg_themes"]["warning"]["bg"]),
    ("Error message text on error bg", PALETTE_LIGHT["msg_themes"]["error"]["fg"], PALETTE_LIGHT["msg_themes"]["error"]["bg"]),

    # Dark theme pairs
    ("Dark title on dark header", PALETTE_DARK["title_text"], PALETTE_DARK["header_band"]),
    ("Dark subtitle on dark header", PALETTE_DARK["subtitle_text"], PALETTE_DARK["header_band"]),
    ("Dark primary text on dark card", PALETTE_DARK["text_primary"], PALETTE_DARK["card_bg"]),
    ("Dark primary text on dark app bg", PALETTE_DARK["text_primary"], PALETTE_DARK["app_bg"]),
    ("Dark secondary text on dark card", PALETTE_DARK["text_secondary"], PALETTE_DARK["card_bg"]),
    ("Dark secondary text on dark app bg", PALETTE_DARK["text_secondary"], PALETTE_DARK["app_bg"]),
    ("Dark muted text on dark card", PALETTE_DARK["text_muted"], PALETTE_DARK["card_bg"]),
    ("Dark primary button white text", "#FFFFFF", PALETTE_DARK["primary_btn"]),
    ("Dark selected row text on row", PALETTE_DARK["row_sel_fg"], PALETTE_DARK["row_sel_bg"]),
    ("Dark running tint fg on bg", PALETTE_DARK["tint_running_fg"], PALETTE_DARK["tint_running_bg"]),
    ("Dark failed tint fg on bg", PALETTE_DARK["tint_failed_fg"], PALETTE_DARK["tint_failed_bg"]),
    ("Dark diff tint fg on bg", PALETTE_DARK["tint_diff_fg"], PALETTE_DARK["tint_diff_bg"]),

    # Midnight theme pairs (Palette B)
    ("Midnight title on dark header", PALETTE_MIDNIGHT["title_text"], PALETTE_MIDNIGHT["header_band"]),
    ("Midnight subtitle on dark header", PALETTE_MIDNIGHT["subtitle_text"], PALETTE_MIDNIGHT["header_band"]),
    ("Midnight primary text on card", PALETTE_MIDNIGHT["text_primary"], PALETTE_MIDNIGHT["card_bg"]),
    ("Midnight primary text on app bg", PALETTE_MIDNIGHT["text_primary"], PALETTE_MIDNIGHT["app_bg"]),
    ("Midnight secondary text on card", PALETTE_MIDNIGHT["text_secondary"], PALETTE_MIDNIGHT["card_bg"]),
    ("Midnight secondary text on app bg", PALETTE_MIDNIGHT["text_secondary"], PALETTE_MIDNIGHT["app_bg"]),
    ("Midnight primary button white text", "#FFFFFF", PALETTE_MIDNIGHT["primary_btn"]),
    ("Midnight selected row text on row", PALETTE_MIDNIGHT["row_sel_fg"], PALETTE_MIDNIGHT["row_sel_bg"]),
    ("Midnight running tint fg on bg", PALETTE_MIDNIGHT["tint_running_fg"], PALETTE_MIDNIGHT["tint_running_bg"]),
    ("Midnight failed tint fg on bg", PALETTE_MIDNIGHT["tint_failed_fg"], PALETTE_MIDNIGHT["tint_failed_bg"]),
    ("Midnight diff tint fg on bg", PALETTE_MIDNIGHT["tint_diff_fg"], PALETTE_MIDNIGHT["tint_diff_bg"]),
    ("Midnight info text on bg", PALETTE_MIDNIGHT["msg_themes"]["info"]["fg"], PALETTE_MIDNIGHT["msg_themes"]["info"]["bg"]),
    ("Midnight success text on bg", PALETTE_MIDNIGHT["msg_themes"]["success"]["fg"], PALETTE_MIDNIGHT["msg_themes"]["success"]["bg"]),
    ("Midnight warning text on bg", PALETTE_MIDNIGHT["msg_themes"]["warning"]["fg"], PALETTE_MIDNIGHT["msg_themes"]["warning"]["bg"]),
    ("Midnight error text on bg", PALETTE_MIDNIGHT["msg_themes"]["error"]["fg"], PALETTE_MIDNIGHT["msg_themes"]["error"]["bg"]),
]


def _apply_palette(mode: str) -> None:
    """Update active module-level color constants to match the given mode ('light', 'dark', 'graphite', 'midnight')."""
    global COLOR_APP_BG, COLOR_CARD_BG, COLOR_BORDER, COLOR_HEADER_NAVY
    global COLOR_TITLE_WHITE, COLOR_SUBTITLE, COLOR_TEXT_PRIMARY, COLOR_TEXT_SECONDARY
    global COLOR_TEXT_MUTED, COLOR_PLACEHOLDER, COLOR_PRIMARY_BTN, COLOR_PRIMARY_BTN_HOVER
    global COLOR_BTN_BORDER, COLOR_DISABLED_BG, COLOR_DISABLED_FG, COLOR_TAB_UNSELECTED_BG
    global COLOR_TAB_UNSELECTED_FG, COLOR_TAB_SELECTED_BG, COLOR_TAB_SELECTED_FG
    global COLOR_TAB_UNDERLINE, COLOR_TABLE_HEADER_BG, COLOR_ROW_EVEN, COLOR_ROW_ODD
    global COLOR_SEL_BG, COLOR_SEL_FG, TINT_RUNNING_BG, TINT_RUNNING_FG
    global TINT_FAILED_BG, TINT_FAILED_FG, TINT_DIFF_BG, TINT_DIFF_FG, COLOR_INPUT_BG
    global COLOR_INPUT_BORDER, COLOR_BORDER_FOCUS, COLOR_READONLY_BG
    global COLOR_INFO_FG, COLOR_SUCCESS_FG, COLOR_WARNING_FG, COLOR_ERROR_FG
    global STATUS_COLORS, MSG_THEMES

    mode_lower = mode.lower()
    if mode_lower in ("midnight", "palette_b"):
        p = PALETTE_MIDNIGHT
    elif mode_lower in ("graphite", "palette_a", "dark"):
        p = PALETTE_GRAPHITE
    else:
        p = PALETTE_LIGHT

    COLOR_APP_BG = p["app_bg"]
    COLOR_CARD_BG = p["card_bg"]
    COLOR_BORDER = p["border"]
    COLOR_HEADER_NAVY = p["header_band"]
    COLOR_TITLE_WHITE = p["title_text"]
    COLOR_SUBTITLE = p["subtitle_text"]
    COLOR_TEXT_PRIMARY = p["text_primary"]
    COLOR_TEXT_SECONDARY = p["text_secondary"]
    COLOR_TEXT_MUTED = p["text_muted"]
    COLOR_PLACEHOLDER = p["placeholder"]
    COLOR_PRIMARY_BTN = p["primary_btn"]
    COLOR_PRIMARY_BTN_HOVER = p["primary_btn_hover"]
    COLOR_BTN_BORDER = p["btn_border"]
    COLOR_DISABLED_BG = p["disabled_bg"]
    COLOR_DISABLED_FG = p["disabled_fg"]
    COLOR_TAB_UNSELECTED_BG = p["tab_unselected_bg"]
    COLOR_TAB_UNSELECTED_FG = p["tab_unselected_fg"]
    COLOR_TAB_SELECTED_BG = p["tab_selected_bg"]
    COLOR_TAB_SELECTED_FG = p["tab_selected_fg"]
    COLOR_TAB_UNDERLINE = p["tab_underline"]
    COLOR_TABLE_HEADER_BG = p["table_header_bg"]
    COLOR_ROW_EVEN = p["row_even"]
    COLOR_ROW_ODD = p["row_odd"]
    COLOR_SEL_BG = p["row_sel_bg"]
    COLOR_SEL_FG = p["row_sel_fg"]
    TINT_RUNNING_BG = p["tint_running_bg"]
    TINT_RUNNING_FG = p["tint_running_fg"]
    TINT_FAILED_BG = p["tint_failed_bg"]
    TINT_FAILED_FG = p["tint_failed_fg"]
    TINT_DIFF_BG = p["tint_diff_bg"]
    TINT_DIFF_FG = p["tint_diff_fg"]
    COLOR_INPUT_BG = p.get("input_bg", p["card_bg"])
    COLOR_INPUT_BORDER = p.get("input_border", p["border"])
    COLOR_BORDER_FOCUS = p.get("border_focus", p["primary_btn"])
    COLOR_READONLY_BG = p.get("readonly_bg", p["disabled_bg"])
    COLOR_INFO_FG = p.get("info_fg", "#1D4ED8")
    COLOR_SUCCESS_FG = p.get("success_fg", "#15803D")
    COLOR_WARNING_FG = p.get("warning_fg", "#B45309")
    COLOR_ERROR_FG = p.get("error_fg", "#B91C1C")

    STATUS_COLORS.clear()
    STATUS_COLORS.update({
        "done": (COLOR_ROW_EVEN, COLOR_TEXT_PRIMARY),
        "done_test": (COLOR_ROW_EVEN, COLOR_TEXT_PRIMARY),
        "running": (TINT_RUNNING_BG, TINT_RUNNING_FG),
        "edited": (COLOR_ROW_EVEN, COLOR_TEXT_PRIMARY),
        "diff": (TINT_DIFF_BG, TINT_DIFF_FG),
        "missing": (TINT_FAILED_BG, TINT_FAILED_FG),
        "failed": (TINT_FAILED_BG, TINT_FAILED_FG),
        "forced": (COLOR_ROW_EVEN, COLOR_TEXT_PRIMARY),
        "resuming": (TINT_RUNNING_BG, TINT_RUNNING_FG),
    })

    MSG_THEMES.clear()
    MSG_THEMES.update(p["msg_themes"])


# ---------------------------------------------------------------------------
# Font Hierarchy & Dynamic Scaling
# ---------------------------------------------------------------------------

BASE_FONT_SIZES = {
    "body": 11,           # Base size 10.5–11 pt for crisp legibility
    "heading": 11,        # Semibold for labels, headings, table headers
    "section": 13,        # Section headings 13–15 pt
    "title": 18,
    "caption": 9,
    "code": 10,
    "rowheight_comfortable": 32,
    "rowheight_compact": 26,
}

_CURRENT_SCALE = 1.0
_CURRENT_DENSITY = "comfortable"
_CURRENT_THEME_MODE = "system"
_EFFECTIVE_THEME = "light"
_TAB_IMAGES: Dict[str, tk.PhotoImage] = {}


def resolve_font_family(style_type: str = "text") -> str:
    """Return Segoe UI Variable when available on the system, otherwise fallback to Segoe UI."""
    try:
        fams = font.families()
        if style_type == "display":
            if "Segoe UI Variable Display" in fams:
                return "Segoe UI Variable Display"
            if "Segoe UI Variable Text" in fams:
                return "Segoe UI Variable Text"
        elif style_type == "code":
            if "Cascadia Mono" in fams:
                return "Cascadia Mono"
            return "Consolas"
        elif style_type == "semibold":
            if "Segoe UI Variable Text Semibold" in fams:
                return "Segoe UI Variable Text Semibold"
            if "Segoe UI Semibold" in fams:
                return "Segoe UI Semibold"
            if "Segoe UI Variable Text" in fams:
                return "Segoe UI Variable Text"
        else:
            if "Segoe UI Variable Text" in fams:
                return "Segoe UI Variable Text"
    except Exception:
        pass
    if style_type == "code":
        return "Consolas"
    return "Segoe UI"


def get_scaled_size(key: str, scale: Optional[float] = None) -> int:
    s = _CURRENT_SCALE if scale is None else scale
    base = BASE_FONT_SIZES.get(key, 11)
    return max(7, int(round(base * s)))


def init_theme_fonts(root: tk.Misc, scale: float = 1.0) -> None:
    """Create or update application-wide named fonts for dynamic scaling."""
    global _CURRENT_SCALE
    _CURRENT_SCALE = scale

    body_fam = resolve_font_family("text")
    bold_fam = resolve_font_family("semibold")
    display_fam = resolve_font_family("display")
    code_fam = resolve_font_family("code")

    body_sz = get_scaled_size("body", scale)
    heading_sz = get_scaled_size("heading", scale)
    section_sz = get_scaled_size("section", scale)
    title_sz = get_scaled_size("title", scale)
    caption_sz = get_scaled_size("caption", scale)
    code_sz = get_scaled_size("code", scale)

    named_specs = [
        ("SS_Body", body_fam, body_sz, "normal"),
        ("SS_BodyBold", bold_fam, heading_sz, "bold"),
        ("SS_Header", display_fam, section_sz, "bold"),
        ("SS_Section", display_fam, section_sz, "bold"),
        ("SS_Title", display_fam, title_sz, "bold"),
        ("SS_Caption", body_fam, caption_sz, "normal"),
        ("SS_Code", code_fam, code_sz, "normal"),
        ("SS_CodeBold", code_fam, code_sz, "bold"),
    ]

    for name, family, sz, weight in named_specs:
        try:
            if name in font.names(root):
                root.tk.call("font", "configure", name, "-family", family, "-size", sz, "-weight", weight)
            else:
                root.tk.call("font", "create", name, "-family", family, "-size", sz, "-weight", weight)
        except Exception:
            try:
                f = font.nametofont(name, root=root)
                f.configure(family=family, size=sz, weight=weight)
            except Exception:
                pass

    for std_name, sz, weight in [
        ("TkDefaultFont", body_sz, "normal"),
        ("TkTextFont", body_sz, "normal"),
        ("TkFixedFont", code_sz, "normal"),
        ("TkHeadingFont", heading_sz, "bold"),
        ("TkCaptionFont", caption_sz, "normal"),
    ]:
        try:
            f = font.nametofont(std_name, root=root)
            f.configure(family=body_fam if "Fixed" not in std_name else "Consolas", size=sz, weight=weight)
        except Exception:
            pass


def _create_tab_images(root: tk.Misc, force_refresh: bool = False) -> Tuple[tk.PhotoImage, tk.PhotoImage]:
    """Create tab background images with 3px blue underline on selected tab."""
    global _TAB_IMAGES
    if not force_refresh and "unselected" in _TAB_IMAGES and "selected" in _TAB_IMAGES:
        return _TAB_IMAGES["unselected"], _TAB_IMAGES["selected"]

    img_unselected = tk.PhotoImage(master=root, width=16, height=16)
    img_selected = tk.PhotoImage(master=root, width=16, height=16)

    # Unselected tab
    img_unselected.put(COLOR_BORDER, to=(0, 0, 16, 1))      # top
    img_unselected.put(COLOR_BORDER, to=(0, 0, 1, 16))      # left
    img_unselected.put(COLOR_BORDER, to=(15, 0, 16, 16))    # right
    img_unselected.put(COLOR_BORDER, to=(0, 15, 16, 16))    # bottom
    img_unselected.put(COLOR_TAB_UNSELECTED_BG, to=(1, 1, 15, 15)) # body

    # Selected tab: 3px blue underline at bottom
    img_selected.put(COLOR_BORDER, to=(0, 0, 16, 1))        # top
    img_selected.put(COLOR_BORDER, to=(0, 0, 1, 16))        # left
    img_selected.put(COLOR_BORDER, to=(15, 0, 16, 16))      # right
    img_selected.put(COLOR_CARD_BG, to=(1, 1, 15, 13))      # body
    img_selected.put(COLOR_TAB_UNDERLINE, to=(0, 13, 16, 16))# 3px underline

    _TAB_IMAGES["unselected"] = img_unselected
    _TAB_IMAGES["selected"] = img_selected
    return img_unselected, img_selected


# ---------------------------------------------------------------------------
# Global Style Application & Loop Guard
# ---------------------------------------------------------------------------

_LAST_APPLIED_STATE: Dict[str, Any] = {
    "scale": None,
    "density": None,
    "theme_mode": None,
    "effective": None,
}
_APPLY_TIMESTAMPS: List[float] = []
_IN_APPLY_THEME: bool = False


def apply_global_theme(
    root: tk.Misc,
    scale: float = 1.0,
    density: str = "comfortable",
    theme_mode: str = "system",
    force: bool = False,
) -> str:
    """
    Configure ttk.Style defaults for widgets application-wide.
    Idempotent, debounced, and re-entrancy guarded.
    Returns effective theme ('light' or 'dark').
    """
    global _CURRENT_DENSITY, _CURRENT_SCALE, _CURRENT_THEME_MODE, _EFFECTIVE_THEME
    global _LAST_APPLIED_STATE, _APPLY_TIMESTAMPS, _IN_APPLY_THEME

    # Rate limiter / loop guard: break if called > 6 times in 1 second
    now = time.perf_counter()
    _APPLY_TIMESTAMPS = [t for t in _APPLY_TIMESTAMPS if now - t < 1.0]
    if len(_APPLY_TIMESTAMPS) >= 6 and not force:
        logger.warning("Theme apply throttled by loop guard (%d calls in 1s)", len(_APPLY_TIMESTAMPS))
        return _EFFECTIVE_THEME
    _APPLY_TIMESTAMPS.append(now)

    # Re-entrancy guard
    if _IN_APPLY_THEME:
        logger.warning("apply_global_theme re-entrancy blocked")
        return _EFFECTIVE_THEME

    # Resolve effective theme
    effective = get_windows_apps_theme() if theme_mode == "system" else theme_mode
    if effective not in ("light", "dark"):
        effective = "light"

    # Idempotency check: if state is identical and not forced, return immediately
    new_state = {
        "scale": round(float(scale), 2),
        "density": str(density),
        "theme_mode": str(theme_mode),
        "effective": effective,
    }
    if not force and new_state == _LAST_APPLIED_STATE:
        return effective

    _IN_APPLY_THEME = True
    try:
        _CURRENT_DENSITY = density
        _CURRENT_SCALE = scale
        _CURRENT_THEME_MODE = theme_mode
        _EFFECTIVE_THEME = effective
        _LAST_APPLIED_STATE = new_state

        _apply_palette(effective)
        init_theme_fonts(root, scale)
        set_window_dark_title_bar(root, dark=(effective == "dark"))

        try:
            if hasattr(root, "configure"):
                root.configure(bg=COLOR_APP_BG)
        except Exception:
            pass

        style = ttk.Style(root)
        try:
            style.theme_use("clam")
        except Exception:
            pass

        base_rh = BASE_FONT_SIZES["rowheight_compact"] if density == "compact" else BASE_FONT_SIZES["rowheight_comfortable"]
        row_h = max(20, int(round(base_rh * scale)))
        pad_y = max(3, int(round(5 * scale)))
        pad_x = max(6, int(round(10 * scale)))

        # Global base
        style.configure(
            ".",
            font="SS_Body",
            foreground=COLOR_TEXT_PRIMARY,
            background=COLOR_APP_BG,
        )

        # Frames
        style.configure("TFrame", background=COLOR_APP_BG)
        style.configure("Card.TFrame", background=COLOR_CARD_BG, relief="solid", borderwidth=1, bordercolor=COLOR_BORDER)
        style.configure("CardInner.TFrame", background=COLOR_CARD_BG)

        # Labels
        style.configure("TLabel", font="SS_Body", foreground=COLOR_TEXT_PRIMARY, background=COLOR_CARD_BG)
        style.configure("AppBg.TLabel", font="SS_Body", foreground=COLOR_TEXT_PRIMARY, background=COLOR_APP_BG)
        style.configure("Muted.TLabel", font="SS_Body", foreground=COLOR_TEXT_MUTED, background=COLOR_CARD_BG)
        style.configure("Heading.TLabel", font="SS_BodyBold", foreground=COLOR_HEADER_NAVY if effective == "light" else COLOR_TITLE_WHITE, background=COLOR_CARD_BG)
        style.configure("Caption.TLabel", font="SS_Caption", foreground=COLOR_TEXT_MUTED, background=COLOR_CARD_BG)

        # LabelFrames (Cards)
        style.configure(
            "TLabelframe",
            background=COLOR_CARD_BG,
            relief="solid",
            borderwidth=1,
            bordercolor=COLOR_BORDER,
        )
        style.configure(
            "TLabelframe.Label",
            font="SS_Section",
            foreground=COLOR_HEADER_NAVY if effective == "light" else COLOR_TITLE_WHITE,
            background=COLOR_CARD_BG,
        )

        # Buttons: Secondary (Default TButton)
        pressed_bg = "#242426" if effective == "dark" else "#F1F5F9"
        active_bg = "#3A3A3C" if effective == "dark" else "#F8FAFC"
        style.configure(
            "TButton",
            font="SS_Body",
            background=COLOR_CARD_BG,
            foreground=COLOR_TEXT_PRIMARY,
            bordercolor=COLOR_BTN_BORDER,
            lightcolor=COLOR_CARD_BG,
            darkcolor=COLOR_CARD_BG,
            padding=(pad_x, pad_y),
            relief="solid",
            borderwidth=1,
            focuscolor=COLOR_PRIMARY_BTN,
        )
        style.map(
            "TButton",
            background=[("disabled", COLOR_DISABLED_BG), ("pressed", pressed_bg), ("active", active_bg)],
            foreground=[("disabled", COLOR_DISABLED_FG), ("pressed", COLOR_TEXT_PRIMARY), ("active", COLOR_TEXT_PRIMARY)],
            bordercolor=[("disabled", COLOR_BORDER), ("pressed", COLOR_BTN_BORDER), ("active", COLOR_BTN_BORDER)],
            lightcolor=[("disabled", COLOR_DISABLED_BG), ("pressed", pressed_bg), ("active", active_bg)],
            darkcolor=[("disabled", COLOR_DISABLED_BG), ("pressed", pressed_bg), ("active", active_bg)],
        )

        # Buttons: Primary (Primary.TButton)
        style.configure(
            "Primary.TButton",
            font="SS_BodyBold",
            background=COLOR_PRIMARY_BTN,
            foreground="#FFFFFF",
            bordercolor=COLOR_PRIMARY_BTN,
            lightcolor=COLOR_PRIMARY_BTN,
            darkcolor=COLOR_PRIMARY_BTN,
            padding=(pad_x + 2, pad_y),
            relief="solid",
            borderwidth=1,
            focuscolor=COLOR_PRIMARY_BTN_HOVER,
        )
        style.map(
            "Primary.TButton",
            background=[("disabled", COLOR_DISABLED_BG), ("pressed", "#063F8A"), ("active", COLOR_PRIMARY_BTN_HOVER)],
            foreground=[("disabled", COLOR_DISABLED_FG), ("pressed", "#FFFFFF"), ("active", "#FFFFFF")],
            bordercolor=[("disabled", COLOR_BORDER), ("pressed", "#063F8A"), ("active", COLOR_PRIMARY_BTN_HOVER)],
            lightcolor=[("disabled", COLOR_DISABLED_BG), ("pressed", "#063F8A"), ("active", COLOR_PRIMARY_BTN_HOVER)],
            darkcolor=[("disabled", COLOR_DISABLED_BG), ("pressed", "#063F8A"), ("active", COLOR_PRIMARY_BTN_HOVER)],
        )

        # Radiobuttons & Checkbuttons
        style.configure("TRadiobutton", font="SS_Body", background=COLOR_CARD_BG, foreground=COLOR_TEXT_PRIMARY)
        style.configure("TCheckbutton", font="SS_Body", background=COLOR_CARD_BG, foreground=COLOR_TEXT_PRIMARY)

        # Modern Entries: 1px subtle border, ~8x6 padding, distinct readonly & disabled
        style.configure(
            "TEntry",
            font="SS_Body",
            fieldbackground=COLOR_INPUT_BG,
            foreground=COLOR_TEXT_PRIMARY,
            insertcolor=COLOR_TEXT_PRIMARY,
            bordercolor=COLOR_INPUT_BORDER,
            lightcolor=COLOR_INPUT_BG,
            darkcolor=COLOR_INPUT_BG,
            padding=(8, 6),
            relief="solid",
            borderwidth=1,
        )
        style.map(
            "TEntry",
            bordercolor=[("focus", COLOR_BORDER_FOCUS), ("disabled", COLOR_BORDER)],
            fieldbackground=[("disabled", COLOR_DISABLED_BG), ("readonly", COLOR_READONLY_BG)],
            foreground=[("disabled", COLOR_DISABLED_FG)],
        )

        # Comboboxes
        style.configure(
            "TCombobox",
            font="SS_Body",
            fieldbackground=COLOR_INPUT_BG,
            foreground=COLOR_TEXT_PRIMARY,
            bordercolor=COLOR_INPUT_BORDER,
            padding=(8, 5),
            relief="solid",
            borderwidth=1,
        )
        style.map(
            "TCombobox",
            bordercolor=[("focus", COLOR_BORDER_FOCUS), ("disabled", COLOR_BORDER)],
            fieldbackground=[("readonly", COLOR_INPUT_BG), ("disabled", COLOR_DISABLED_BG)],
            foreground=[("disabled", COLOR_DISABLED_FG)],
        )

        # Spinboxes
        style.configure(
            "TSpinbox",
            font="SS_Body",
            fieldbackground=COLOR_INPUT_BG,
            foreground=COLOR_TEXT_PRIMARY,
            bordercolor=COLOR_INPUT_BORDER,
            arrowcolor=COLOR_TEXT_PRIMARY,
            padding=(6, 4),
            relief="solid",
            borderwidth=1,
        )
        style.map(
            "TSpinbox",
            bordercolor=[("focus", COLOR_BORDER_FOCUS), ("disabled", COLOR_BORDER)],
            fieldbackground=[("disabled", COLOR_DISABLED_BG)],
            foreground=[("disabled", COLOR_DISABLED_FG)],
            arrowcolor=[("disabled", COLOR_DISABLED_FG)],
        )

        # Notebook & Tabs
        style.configure("TNotebook", background=COLOR_APP_BG, borderwidth=0)

        img_unselected, img_selected = _create_tab_images(root, force_refresh=True)
        try:
            style.element_create(
                "Custom.tab",
                "image",
                img_unselected,
                ("selected", img_selected),
                border=4,
                sticky="nsew",
            )
        except Exception:
            pass

        try:
            style.layout(
                "TNotebook.Tab",
                [
                    (
                        "Custom.tab",
                        {
                            "sticky": "nswe",
                            "children": [
                                (
                                    "Notebook.padding",
                                    {
                                        "side": "top",
                                        "sticky": "nswe",
                                        "children": [("Notebook.label", {"side": "top", "sticky": ""})],
                                    },
                                )
                            ],
                        },
                    )
                ],
            )
        except Exception:
            pass

        style.configure(
            "TNotebook.Tab",
            padding=(int(round(16 * scale)), int(round(8 * scale))),
            font="SS_BodyBold",
            background=COLOR_TAB_UNSELECTED_BG,
            foreground=COLOR_TAB_UNSELECTED_FG,
        )
        style.map(
            "TNotebook.Tab",
            background=[("selected", COLOR_TAB_SELECTED_BG), ("!selected", COLOR_TAB_UNSELECTED_BG)],
            foreground=[("selected", COLOR_TAB_SELECTED_FG), ("!selected", COLOR_TAB_UNSELECTED_FG)],
        )

        # Treeview
        style.configure(
            "Treeview",
            font="SS_Body",
            rowheight=row_h,
            background=COLOR_CARD_BG,
            fieldbackground=COLOR_CARD_BG,
            foreground=COLOR_TEXT_PRIMARY,
            bordercolor=COLOR_BORDER,
            relief="flat",
        )

        try:
            f_bold = font.nametofont("SS_BodyBold") if "SS_BodyBold" in font.names(root) else None
            linespace = f_bold.metrics("linespace") if f_bold else max(14, int(round(16 * scale)))
        except Exception:
            linespace = max(14, int(round(16 * scale)))

        max_hdr_lines = 2
        extra_y = (max_hdr_lines - 1) * linespace
        pad_top = int(round(5 * scale)) + extra_y // 2
        pad_bottom = int(round(5 * scale)) + (extra_y - extra_y // 2)
        pad_x = int(round(6 * scale))

        style.configure(
            "Treeview.Heading",
            font="SS_BodyBold",
            background=COLOR_TABLE_HEADER_BG,
            foreground=COLOR_HEADER_NAVY if effective == "light" else COLOR_TITLE_WHITE,
            relief="flat",
            padding=(pad_x, pad_top, pad_x, pad_bottom),
        )
        style.map(
            "Treeview",
            background=[("selected", COLOR_SEL_BG)],
            foreground=[("selected", COLOR_SEL_FG)],
        )

        # Totals Treeview style
        style.configure(
            "Totals.Treeview",
            font="SS_BodyBold",
            rowheight=row_h,
            background=COLOR_TABLE_HEADER_BG,
            fieldbackground=COLOR_TABLE_HEADER_BG,
            foreground=COLOR_HEADER_NAVY if effective == "light" else COLOR_TITLE_WHITE,
            relief="flat",
        )
        style.map(
            "Totals.Treeview",
            background=[("selected", COLOR_TABLE_HEADER_BG)],
            foreground=[("selected", COLOR_HEADER_NAVY if effective == "light" else COLOR_TITLE_WHITE)],
        )

        # Scrollbars: Thin, flat, 14px wide
        p_pal = PALETTE_DARK if effective == "dark" else PALETTE_LIGHT
        track_c = p_pal["scroll_track"]
        thumb_c = p_pal["scroll_thumb"]
        active_thumb = p_pal["scroll_active"]

        style.configure(
            "Vertical.TScrollbar",
            troughcolor=track_c,
            background=thumb_c,
            bordercolor=track_c,
            lightcolor=thumb_c,
            darkcolor=thumb_c,
            arrowcolor=COLOR_TEXT_MUTED,
            arrowsize=11,
            width=14,
        )
        style.map("Vertical.TScrollbar", background=[("active", active_thumb), ("pressed", active_thumb)])

        style.configure(
            "Horizontal.TScrollbar",
            troughcolor=track_c,
            background=thumb_c,
            bordercolor=track_c,
            lightcolor=thumb_c,
            darkcolor=thumb_c,
            arrowcolor=COLOR_TEXT_MUTED,
            arrowsize=11,
            width=14,
        )
        style.map("Horizontal.TScrollbar", background=[("active", active_thumb), ("pressed", active_thumb)])

        # Progress bar
        style.configure(
            "Horizontal.TProgressbar",
            troughcolor=track_c,
            background=COLOR_PRIMARY_BTN,
            bordercolor=track_c,
            lightcolor=COLOR_PRIMARY_BTN,
            darkcolor=COLOR_PRIMARY_BTN,
        )
        return effective
    finally:
        _IN_APPLY_THEME = False


def apply_ui_scale(
    root: tk.Misc,
    scale: float,
    density: Optional[str] = None,
    theme_mode: Optional[str] = None,
) -> None:
    """Scale application fonts, row heights, and layout metrics."""
    global _CURRENT_SCALE, _CURRENT_DENSITY, _CURRENT_THEME_MODE
    _CURRENT_SCALE = scale
    if density:
        _CURRENT_DENSITY = density
    if theme_mode:
        _CURRENT_THEME_MODE = theme_mode
    apply_global_theme(root, scale=_CURRENT_SCALE, density=_CURRENT_DENSITY, theme_mode=_CURRENT_THEME_MODE)


def configure_treeview_status_tags(tree: ttk.Treeview) -> None:
    """
    Configure status tags for treeviews.
    Rules:
      - Alternating rows: even and odd colors.
      - Row tinting ONLY for:
          * Running
          * Failed / missing
          * Different settings
      - Done rows stay neutral (no row background override).
      - Dark contrast text everywhere (>= 4.5:1).
    """
    tree.tag_configure("even_row", background=COLOR_ROW_EVEN, foreground=COLOR_TEXT_PRIMARY)
    tree.tag_configure("odd_row", background=COLOR_ROW_ODD, foreground=COLOR_TEXT_PRIMARY)

    tree.tag_configure("running", background=TINT_RUNNING_BG, foreground=TINT_RUNNING_FG)
    tree.tag_configure("resuming", background=TINT_RUNNING_BG, foreground=TINT_RUNNING_FG)
    tree.tag_configure("failed", background=TINT_FAILED_BG, foreground=TINT_FAILED_FG)
    tree.tag_configure("missing", background=TINT_FAILED_BG, foreground=TINT_FAILED_FG)
    tree.tag_configure("diff", background=TINT_DIFF_BG, foreground=TINT_DIFF_FG)

    tree.tag_configure("done", foreground=COLOR_TEXT_PRIMARY)
    tree.tag_configure("done_test", foreground=COLOR_TEXT_PRIMARY)
    tree.tag_configure("edited", foreground=COLOR_TEXT_PRIMARY)
    tree.tag_configure("forced", foreground=COLOR_TEXT_PRIMARY)
    tree.tag_configure("pending", foreground=COLOR_TEXT_PRIMARY)

    tree.tag_configure("approved_tag", foreground=MSG_THEMES["success"]["fg"])
    tree.tag_configure("unapproved_tag", foreground=COLOR_TEXT_MUTED)


# ---------------------------------------------------------------------------
# MessageBar Component
# ---------------------------------------------------------------------------

class MessageBar(tk.Frame):
    """
    Message bar with a 4px left accent bar and high-contrast, non-italic text.
    Kinds: 'info', 'success', 'warning', 'error'.
    """

    def __init__(
        self,
        master: tk.Misc,
        text: str = "",
        kind: str = "info",
        textvariable: Optional[tk.StringVar] = None,
        **kwargs,
    ):
        super().__init__(master, relief="flat", borderwidth=0, **kwargs)
        self._kind = kind
        self._theme = MSG_THEMES.get(kind, MSG_THEMES["info"])
        self._textvariable = textvariable

        self.configure(bg=self._theme["bg"], highlightbackground=COLOR_BORDER, highlightthickness=1)

        self._bar = tk.Frame(self, bg=self._theme["bar"], width=4)
        self._bar.pack(side=tk.LEFT, fill=tk.Y)

        # Initial sensible wraplength so label never requests unbounded width before layout
        try:
            init_w = master.winfo_width()
            init_wrap = max(200, min(700, init_w - 24 if init_w > 200 else 580))
        except Exception:
            init_wrap = 580

        self._lbl = tk.Label(
            self,
            text=text,
            font="SS_Body",
            bg=self._theme["bg"],
            fg=self._theme["fg"],
            anchor=tk.W,
            justify=tk.LEFT,
            padx=8,
            pady=6,
            wraplength=init_wrap,
        )
        self._lbl.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.bind("<Configure>", self._on_configure)

        if self._textvariable is not None:
            self._on_var_changed()
            self._textvariable.trace_add("write", self._on_var_changed)

    def _on_configure(self, event) -> None:
        if (event.widget == self or str(event.widget) == str(self)) and hasattr(self, "_lbl") and self._lbl.winfo_exists():
            w = event.width if getattr(event, "width", 0) > 1 else self.winfo_width()
            try:
                top = self.winfo_toplevel()
                if top and top.winfo_exists():
                    top_w = top.winfo_width()
                    if top_w > 50 and w >= top_w:
                        w = top_w - 48
            except Exception:
                pass
            avail_w = max(80, min(1200, w - 24))
            curr = self._lbl.cget("wraplength")
            try:
                curr_val = int(str(curr))
            except Exception:
                curr_val = 0
            if abs(curr_val - avail_w) > 4:
                self._lbl.configure(wraplength=avail_w)

    def _on_var_changed(self, *_args):
        try:
            if not self.winfo_exists():
                return
            if self._textvariable is not None:
                txt = self._textvariable.get()
                self.set_message(txt)
        except Exception:
            pass

    def set_message(self, text: str, kind: Optional[str] = None) -> None:
        """Update displayed text and automatically detect or apply kind."""
        stripped = (text or "").strip()
        if not stripped:
            self._bar.pack_forget()
            self._lbl.pack_forget()
            self.configure(bg=COLOR_APP_BG, highlightthickness=0)
            self._lbl.configure(text="")
            return

        if kind is None:
            lower = stripped.lower()
            if any(k in lower for k in ("[approved]", "ready to combine", "success", "verified", "passed", "connected")):
                kind = "success"
            elif any(k in lower for k in ("[locked]", "error", "failed", "missing", "corrupt", "not match", "cancel")):
                kind = "error"
            elif any(k in lower for k in ("warning", "diff", "different settings", "unassigned")):
                kind = "warning"
            else:
                kind = self._kind or "info"

        if kind in MSG_THEMES:
            self._kind = kind
            self._theme = MSG_THEMES[kind]
            self.configure(bg=self._theme["bg"], highlightbackground=COLOR_BORDER, highlightthickness=1)
            self._bar.configure(bg=self._theme["bar"])
            self._lbl.configure(bg=self._theme["bg"], fg=self._theme["fg"])

        self._bar.pack(side=tk.LEFT, fill=tk.Y)
        self._lbl.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self._lbl.configure(text=text)

    def configure(self, cnf=None, **kwargs):
        if "text" in kwargs:
            self.set_message(kwargs.pop("text"), kind=kwargs.pop("kind", None))
        if "foreground" in kwargs or "fg" in kwargs:
            fg_val = kwargs.pop("foreground", kwargs.pop("fg", None))
            if fg_val in ("#28a745", "#14532D", "green", "#86EFAC"):
                self.set_message(self.get_text(), kind="success")
            elif fg_val in ("#d9534f", "#dc3545", "#7F1D1D", "red", "#FCA5A5"):
                self.set_message(self.get_text(), kind="error")
            elif fg_val in ("#e67e00", "#78350F", "orange", "amber", "#FDE047"):
                self.set_message(self.get_text(), kind="warning")
            elif fg_val:
                self._lbl.configure(fg=fg_val)
        if kwargs or cnf:
            super().configure(cnf, **kwargs)

    def cget(self, key: str):
        if key in ("text", "foreground", "fg"):
            return self._lbl.cget(key)
        return super().cget(key)

    def get_text(self) -> str:
        return self._lbl.cget("text")


# ---------------------------------------------------------------------------
# SegmentedControl (Modern pill-shaped alternative to radio buttons)
# ---------------------------------------------------------------------------

class SegmentedControl(tk.Frame):
    """
    Two- or three-pill segmented control.
    Theme-aware: adapts pill backgrounds, active color, and borders dynamically.
    """

    def __init__(
        self,
        master: tk.Misc,
        variable: tk.StringVar,
        options: List[Tuple[str, str]],
        command: Optional[Callable[[], None]] = None,
        **kwargs,
    ):
        kwargs.setdefault("bg", COLOR_TAB_UNSELECTED_BG)
        kwargs.setdefault("highlightthickness", 1)
        kwargs.setdefault("highlightbackground", COLOR_BORDER)
        super().__init__(master, **kwargs)

        self.variable = variable
        self.options = options
        self.command = command
        self._buttons: Dict[str, tk.Label] = {}

        for idx, (label_text, val) in enumerate(options):
            lbl = tk.Label(
                self,
                text=label_text,
                font="SS_Body",
                padx=10,
                pady=3,
                cursor="hand2",
                takefocus=1,
            )
            lbl.grid(row=0, column=idx, padx=1, pady=1, sticky="nsew")
            lbl.bind("<Button-1>", lambda _e, v=val: self._on_select(v))
            lbl.bind("<Return>", lambda _e, v=val: self._on_select(v))
            lbl.bind("<space>", lambda _e, v=val: self._on_select(v))
            self._buttons[val] = lbl

        for col_idx in range(len(options)):
            self.columnconfigure(col_idx, weight=1)

        self.variable.trace_add("write", lambda *_: self._refresh())
        self._refresh()

    def _on_select(self, val: str) -> None:
        self.variable.set(val)
        if self.command:
            self.command()

    def refresh_theme(self) -> None:
        self.configure(bg=COLOR_TAB_UNSELECTED_BG, highlightbackground=COLOR_BORDER)
        self._refresh()

    def _refresh(self) -> None:
        curr = self.variable.get()
        for label_text, val in self.options:
            lbl = self._buttons.get(val)
            if not lbl:
                continue
            display_text = label_text
            if val == "system":
                display_text = f"System ({_EFFECTIVE_THEME.capitalize()})"
            if val == curr:
                lbl.configure(
                    text=display_text,
                    bg=COLOR_PRIMARY_BTN,
                    fg="#FFFFFF",
                    font="SS_BodyBold",
                )
            else:
                lbl.configure(
                    text=display_text,
                    bg=COLOR_TAB_UNSELECTED_BG,
                    fg=COLOR_TEXT_PRIMARY,
                    font="SS_Body",
                )


# ---------------------------------------------------------------------------
# ToggleSwitch (Modern switch alternative to plain checkboxes)
# ---------------------------------------------------------------------------

class ToggleSwitch(tk.Frame):
    """
    Modern iOS / Windows 11 style toggle switch.
    Track: 40x22px pill. When ON: primary accent. When OFF: disabled bg.
    Thumb: 16px circular disk.
    """

    def __init__(
        self,
        master: tk.Misc,
        variable: tk.BooleanVar,
        text: str = "",
        command: Optional[Callable[[], None]] = None,
        **kwargs,
    ):
        parent_bg = COLOR_CARD_BG
        try:
            if hasattr(master, "cget"):
                parent_bg = master.cget("bg")
        except Exception:
            pass
        kwargs.setdefault("bg", parent_bg)
        super().__init__(master, **kwargs)

        self.variable = variable
        self.command = command
        self._bg_color = kwargs.get("bg", parent_bg)

        self.canvas = tk.Canvas(
            self,
            width=42,
            height=22,
            bg=self._bg_color,
            highlightthickness=0,
            cursor="hand2",
            takefocus=1,
        )
        self.canvas.pack(side=tk.LEFT, padx=(0, 6))

        self.lbl = None
        if text:
            self.lbl = tk.Label(
                self,
                text=text,
                font="SS_Body",
                bg=self._bg_color,
                fg=COLOR_TEXT_PRIMARY,
                cursor="hand2",
            )
            self.lbl.pack(side=tk.LEFT)
            self.lbl.bind("<Button-1>", lambda _e: self._toggle())

        self.canvas.bind("<Button-1>", lambda _e: self._toggle())
        self.canvas.bind("<space>", lambda _e: self._toggle())
        self.canvas.bind("<Return>", lambda _e: self._toggle())

        self.variable.trace_add("write", lambda *_: self._draw())
        self._draw()

    def _toggle(self) -> None:
        self.variable.set(not self.variable.get())
        if self.command:
            self.command()

    def refresh_theme(self, parent_bg: Optional[str] = None) -> None:
        if parent_bg:
            self._bg_color = parent_bg
            self.configure(bg=parent_bg)
            self.canvas.configure(bg=parent_bg)
            if self.lbl:
                self.lbl.configure(bg=parent_bg, fg=COLOR_TEXT_PRIMARY)
        self._draw()

    def _draw(self) -> None:
        self.canvas.delete("all")
        on = self.variable.get()
        track_color = COLOR_PRIMARY_BTN if on else COLOR_DISABLED_BG
        thumb_color = "#FFFFFF"

        self.canvas.create_oval(2, 2, 20, 20, fill=track_color, outline=track_color)
        self.canvas.create_oval(22, 2, 40, 20, fill=track_color, outline=track_color)
        self.canvas.create_rectangle(11, 2, 31, 20, fill=track_color, outline=track_color)

        if not on:
            self.canvas.create_arc(2, 2, 20, 20, start=90, extent=180, outline=COLOR_BTN_BORDER, width=1, style="arc")
            self.canvas.create_arc(22, 2, 40, 20, start=270, extent=180, outline=COLOR_BTN_BORDER, width=1, style="arc")
            self.canvas.create_line(11, 2, 31, 2, fill=COLOR_BTN_BORDER, width=1)
            self.canvas.create_line(11, 20, 31, 20, fill=COLOR_BTN_BORDER, width=1)

        tx = 23 if on else 3
        outline_c = COLOR_BORDER if not on else COLOR_PRIMARY_BTN
        self.canvas.create_oval(tx, 3, tx + 16, 19, fill=thumb_color, outline=outline_c)


# ---------------------------------------------------------------------------
# TextSizeDialog
# ---------------------------------------------------------------------------

class TextSizeDialog(tk.Toplevel):
    """Dialog providing a live slider from 85% to 150% with real-time preview."""

    def __init__(self, parent: tk.Misc, current_scale: float, on_apply: Callable[[float], None]):
        super().__init__(parent)
        self.title("Text Size & Scaling")
        self.resizable(True, True)
        self.configure(bg=COLOR_APP_BG)
        self.transient(parent)
        self.grab_set()

        self.on_apply = on_apply
        self.scale_var = tk.DoubleVar(value=round(current_scale * 100))

        content = tk.Frame(self, bg=COLOR_CARD_BG, highlightbackground=COLOR_BORDER, highlightthickness=1, padx=20, pady=16)
        content.pack(fill=tk.BOTH, expand=True, padx=16, pady=16)

        btn_row = tk.Frame(content, bg=COLOR_CARD_BG)
        btn_row.pack(fill=tk.X, side=tk.BOTTOM, pady=(8, 0))
        ttk.Button(btn_row, text="Reset to 100%", command=self._reset_100).pack(side=tk.LEFT)
        ttk.Button(btn_row, text="Close", style="Primary.TButton", command=self.destroy).pack(side=tk.RIGHT)

        tk.Label(content, text="UI Text Size", font="SS_Section", bg=COLOR_CARD_BG, fg=COLOR_HEADER_NAVY if _EFFECTIVE_THEME == "light" else COLOR_TITLE_WHITE).pack(anchor=tk.W)
        tk.Label(
            content,
            text="Adjust text scaling from 85% to 150%. Changes apply in real time.",
            font="SS_Body",
            bg=COLOR_CARD_BG,
            fg=COLOR_TEXT_SECONDARY,
        ).pack(anchor=tk.W, pady=(2, 12))

        slider_row = tk.Frame(content, bg=COLOR_CARD_BG)
        slider_row.pack(fill=tk.X, pady=4)

        self.pct_label = tk.Label(slider_row, text=f"{int(self.scale_var.get())}%", font="SS_BodyBold", bg=COLOR_CARD_BG, fg=COLOR_PRIMARY_BTN, width=6)
        self.pct_label.pack(side=tk.RIGHT)

        slider = ttk.Scale(
            slider_row,
            from_=85,
            to=150,
            variable=self.scale_var,
            orient=tk.HORIZONTAL,
            command=self._on_slider_moved,
        )
        slider.pack(side=tk.LEFT, fill=tk.X, expand=True)

        self.preview_frame = tk.Frame(content, bg=COLOR_APP_BG, highlightbackground=COLOR_BORDER, highlightthickness=1, padx=10, pady=8)
        self.preview_frame.pack(fill=tk.BOTH, expand=True, pady=6)

        self.preview_heading = tk.Label(self.preview_frame, text="Sample Heading", font="SS_Section", bg=COLOR_APP_BG, fg=COLOR_HEADER_NAVY if _EFFECTIVE_THEME == "light" else COLOR_TITLE_WHITE)
        self.preview_heading.pack(anchor=tk.W)

        self.preview_body = tk.Label(self.preview_frame, text="Sample body text in Segoe UI Variable.", font="SS_Body", bg=COLOR_APP_BG, fg=COLOR_TEXT_PRIMARY)
        self.preview_body.pack(anchor=tk.W)

        center_dialog(self, parent, width=440, height=320, min_width=380, min_height=260)
        self.bind("<Escape>", lambda _e: self.destroy())

    def _on_slider_moved(self, _val: str) -> None:
        pct = int(round(self.scale_var.get()))
        self.pct_label.configure(text=f"{pct}%")
        scale = pct / 100.0
        self.on_apply(scale)

    def _reset_100(self) -> None:
        self.scale_var.set(100)
        self._on_slider_moved("100")


# ---------------------------------------------------------------------------
# ToolTip Helper
# ---------------------------------------------------------------------------

class ToolTip:
    """Lightweight tooltip that appears when hovering over a widget."""

    def __init__(self, widget: tk.Misc, text: str):
        self.widget = widget
        self.text = text
        self.tip_window: Optional[tk.Toplevel] = None
        self.widget.bind("<Enter>", self.show_tip, add="+")
        self.widget.bind("<Leave>", self.hide_tip, add="+")

    def show_tip(self, _event=None) -> None:
        if self.tip_window or not self.text:
            return
        x = self.widget.winfo_rootx() + 15
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 5
        self.tip_window = tw = tk.Toplevel(self.widget)
        tw.wm_overrideredirect(True)
        tw.wm_geometry(f"+{x}+{y}")
        lbl = tk.Label(
            tw,
            text=self.text,
            font="SS_Caption",
            background=COLOR_CARD_BG,
            foreground=COLOR_TEXT_PRIMARY,
            relief="solid",
            borderwidth=1,
            padx=8,
            pady=4,
        )
        lbl.pack()

    def hide_tip(self, _event=None) -> None:
        if self.tip_window:
            try:
                self.tip_window.destroy()
            except Exception:
                pass
            self.tip_window = None


# ---------------------------------------------------------------------------
# CollapsibleSection Helper
# ---------------------------------------------------------------------------

class CollapsibleSection(ttk.Frame):
    """Modern collapsible container with chevron toggle."""

    def __init__(self, parent: tk.Misc, title: str, is_open: bool = False, **kwargs):
        super().__init__(parent, **kwargs)
        self.title_text = title
        self.is_open = is_open

        self.hdr = ttk.Frame(self)
        self.hdr.pack(fill=tk.X, pady=(2, 2))

        arrow = "▼" if is_open else "▶"
        self.btn_toggle = ttk.Button(
            self.hdr,
            text=f"{arrow}  {title}",
            command=self.toggle,
        )
        self.btn_toggle.pack(side=tk.LEFT)

        self.content = ttk.Frame(self, padding=(4, 4))
        if is_open:
            self.content.pack(fill=tk.X)

    def toggle(self) -> None:
        self.is_open = not self.is_open
        arrow = "▼" if self.is_open else "▶"
        self.btn_toggle.configure(text=f"{arrow}  {self.title_text}")
        if self.is_open:
            self.content.pack(fill=tk.X)
        else:
            self.content.pack_forget()

    def open(self) -> None:
        if not self.is_open:
            self.toggle()

    def close(self) -> None:
        if self.is_open:
            self.toggle()


# ---------------------------------------------------------------------------
# Monospace Code Card & Dialog Helpers
# ---------------------------------------------------------------------------

def style_code_panel(widget: tk.Misc, readonly: bool = False) -> None:
    """Style a tk.Text or ScrolledText as a modern elevated monospace code card."""
    try:
        widget.configure(
            font="SS_Code",
            bg=COLOR_INPUT_BG,
            fg=COLOR_TEXT_PRIMARY,
            insertbackground=COLOR_TEXT_PRIMARY,
            highlightthickness=1,
            highlightbackground=COLOR_BORDER,
            highlightcolor=COLOR_BORDER_FOCUS,
            relief="flat",
            padx=8,
            pady=6,
        )
        if hasattr(widget, "frame"):
            widget.frame.configure(bg=COLOR_CARD_BG)
        if hasattr(widget, "vbar"):
            widget.vbar.configure(
                bg=COLOR_CARD_BG,
                troughcolor=COLOR_APP_BG,
                activebackground=COLOR_PRIMARY_BTN,
                highlightthickness=0,
            )
    except Exception:
        pass


def style_toplevel(win: tk.Toplevel) -> None:
    """Configure Toplevel background and immersive titlebar for current theme."""
    try:
        win.configure(bg=COLOR_APP_BG)
        set_window_dark_title_bar(win, dark=(_EFFECTIVE_THEME == "dark"))
    except Exception:
        pass


def center_dialog(
    dialog: tk.Toplevel,
    parent: Optional[tk.Misc] = None,
    width: Optional[int] = None,
    height: Optional[int] = None,
    min_width: Optional[int] = None,
    min_height: Optional[int] = None,
) -> None:
    """
    Center a dialog over the app window (winfo_toplevel()), falling back to the screen/monitor center.
    Sizes dynamically from content if width/height are not supplied.
    Ensures minimum size and prevents positioning off-screen.
    """
    try:
        dialog.update_idletasks()
    except Exception:
        pass

    # Determine dimensions
    w = width if width and width > 0 else dialog.winfo_reqwidth()
    h = height if height and height > 0 else dialog.winfo_reqheight()

    # Enforce minimums
    mw_limit = min_width if min_width is not None else min(400, w)
    mh_limit = min_height if min_height is not None else min(260, h)
    try:
        dialog.minsize(mw_limit, mh_limit)
    except Exception:
        pass

    # Monitor / screen bounds
    try:
        mx, my, mw, mh = get_monitor_work_area()
    except Exception:
        mx, my, mw, mh = 0, 0, 1920, 1040

    if mw > 100:
        w = min(w, mw - 20)
    if mh > 100:
        h = min(h, mh - 40)

    # Resolve target parent toplevel
    top = None
    if parent is not None:
        try:
            top = parent.winfo_toplevel() if hasattr(parent, "winfo_toplevel") else parent
            top.update_idletasks()
        except Exception:
            top = None

    if top is not None:
        try:
            pw = top.winfo_width()
            ph = top.winfo_height()
            px = top.winfo_rootx()
            py = top.winfo_rooty()
            if pw > 100 and ph > 100 and top.winfo_viewable():
                x = px + (pw - w) // 2
                y = py + (ph - h) // 2
                x = max(mx, min(x, mx + mw - w))
                y = max(my, min(y, my + mh - h))
                dialog.geometry(f"{w}x{h}+{x}+{y}")
                return
        except Exception:
            pass

    # Fallback to monitor / screen center
    x = mx + max(0, (mw - w) // 2)
    y = my + max(0, (mh - h) // 2)
    dialog.geometry(f"{w}x{h}+{x}+{y}")

