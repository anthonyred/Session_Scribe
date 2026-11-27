"""
Session Scribe — Logging and Startup Error Diagnostics
Provides:
- Rotating file logging to %LOCALAPPDATA%\\SessionScribe\\logs\\
- Secret-scrubbing filter ensuring tokens, keys, and passphrases are never written
- Graphical fatal startup error notification when running under pythonw.exe
"""

import logging
from logging.handlers import RotatingFileHandler
import os
import re
import sys
import tkinter as tk
from tkinter import messagebox

LOG_DIR = os.path.expandvars(r"%LOCALAPPDATA%\SessionScribe\logs")
LOG_PATH = os.path.join(LOG_DIR, "session_scribe.log")

# Regex to scrub sensitive tokens and keys
SECRET_PATTERNS = [
    re.compile(r"nvapi-[A-Za-z0-9_-]{20,}", re.IGNORECASE),
    re.compile(r"hf_[A-Za-z0-9]{20,}", re.IGNORECASE),
    re.compile(r"(Bearer\s+)[A-Za-z0-9_\-\.]{20,}", re.IGNORECASE),
    re.compile(r"(api[_-]?key\s*[:=]\s*)['\"]?[A-Za-z0-9_\-]{16,}['\"]?", re.IGNORECASE),
    re.compile(r"(passphrase\s*[:=]\s*)['\"]?[^\s'\"]{8,}['\"]?", re.IGNORECASE),
]


class SecretScrubbingFilter(logging.Filter):
    """Logging filter that scrubs tokens, API keys, and passphrases from log messages."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            for pat in SECRET_PATTERNS:
                record.msg = pat.sub("[REDACTED_SECRET]", record.msg)
        if record.args:
            if isinstance(record.args, dict):
                clean_args = {}
                for k, v in record.args.items():
                    if isinstance(v, str):
                        for pat in SECRET_PATTERNS:
                            v = pat.sub("[REDACTED_SECRET]", v)
                        clean_args[k] = v
                    else:
                        clean_args[k] = v
                record.args = clean_args
            elif isinstance(record.args, tuple):
                clean_list = []
                for v in record.args:
                    if isinstance(v, str):
                        for pat in SECRET_PATTERNS:
                            v = pat.sub("[REDACTED_SECRET]", v)
                        clean_list.append(v)
                    else:
                        clean_list.append(v)
                record.args = tuple(clean_list)
        return True


def setup_app_logging(log_path: str = LOG_PATH) -> str:
    """Configure rotated file logging with secret scrubbing. Returns the log file path."""
    try:
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
    except OSError:
        pass

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)

    # Avoid duplicate handlers on re-init
    for h in list(root_logger.handlers):
        if isinstance(h, RotatingFileHandler):
            root_logger.removeHandler(h)

    try:
        file_handler = RotatingFileHandler(
            log_path,
            maxBytes=2 * 1024 * 1024,  # 2 MB per file
            backupCount=5,
            encoding="utf-8",
        )
        file_handler.setLevel(logging.INFO)
        formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
        file_handler.setFormatter(formatter)
        file_handler.addFilter(SecretScrubbingFilter())
        root_logger.addHandler(file_handler)
    except OSError:
        pass

    return log_path


def show_fatal_startup_error(exc: Exception, log_path: str = LOG_PATH) -> None:
    """
    Display a clean graphical error dialog when the app fails to start,
    especially under pythonw.exe where stdout/stderr are hidden.
    """
    logging.exception(f"Fatal startup failure: {exc}")

    title = "Session Scribe — Fatal Startup Error"
    detail_msg = (
        f"Session Scribe encountered a fatal error during startup and cannot continue:\n\n"
        f"  {type(exc).__name__}: {str(exc)}\n\n"
        f"Diagnostic details have been recorded in the log file:\n"
        f"  {log_path}\n\n"
        f"Please verify that the virtual environment (.venv) is installed and up-to-date:\n"
        f"  D:\\Session_Scribe\\.venv\\Scripts\\python.exe scripts\\preflight_check.py"
    )

    try:
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        messagebox.showerror(title, detail_msg)
        root.destroy()
    except Exception:
        # Fallback to direct Win32 MessageBoxW
        if sys.platform == "win32":
            try:
                import ctypes
                ctypes.windll.user32.MessageBoxW(0, detail_msg, title, 0x10)  # MB_ICONERROR
            except Exception:
                pass

