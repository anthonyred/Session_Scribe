"""
Session Scribe — Windows Single Instance Manager
Ensures only one instance of Session Scribe runs at any time.
If a second instance is launched (e.g. from the Start Menu), it brings the
existing window to the foreground and exits cleanly.
"""

import ctypes
from ctypes import wintypes
import json
import logging
import os
import sys
from typing import Optional

logger = logging.getLogger("scribe.single_instance")

MUTEX_NAME = "Global\\SessionScribe_SingleInstance_Mutex_v2026"
LOCK_DIR = os.path.expandvars(r"%LOCALAPPDATA%\SessionScribe")
LOCK_FILE = os.path.join(LOCK_DIR, "session_scribe.lock")

SW_RESTORE = 9
SW_SHOW = 5


class SingleInstanceManager:
    """Manages application mutex and window focusing for single-instance enforcement."""

    def __init__(self, mutex_name: str = MUTEX_NAME, lock_file: str = LOCK_FILE):
        self.mutex_name = mutex_name
        self.lock_file = lock_file
        self._mutex_handle = None
        self._is_primary = False

    def acquire(self) -> bool:
        """
        Attempt to acquire single instance lock.
        Returns True if this is the primary instance.
        Returns False if another instance is already running (and focuses it).
        """
        if sys.platform != "win32":
            return True

        kernel32 = ctypes.windll.kernel32
        # ERROR_ALREADY_EXISTS = 183
        self._mutex_handle = kernel32.CreateMutexW(None, False, self.mutex_name)
        err = kernel32.GetLastError()

        if err == 183:  # Already exists
            logger.info("Another instance is already running. Focusing existing window...")
            self._focus_existing_instance()
            return False

        self._is_primary = True
        return True

    def register_window(self, hwnd: int) -> None:
        """Record the main window HWND and PID for future focusing."""
        if not self._is_primary or sys.platform != "win32":
            return

        try:
            os.makedirs(os.path.dirname(self.lock_file), exist_ok=True)
            payload = {
                "pid": os.getpid(),
                "hwnd": hwnd,
            }
            with open(self.lock_file, "w", encoding="utf-8") as f:
                json.dump(payload, f)
        except OSError as e:
            logger.warning(f"Could not write instance lockfile: {e}")

    def _focus_existing_instance(self) -> None:
        """Bring the running instance window to the foreground."""
        if sys.platform != "win32":
            return

        user32 = ctypes.windll.user32
        target_hwnd = 0

        # Try reading lock file first
        if os.path.isfile(self.lock_file):
            try:
                with open(self.lock_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    cand_hwnd = data.get("hwnd", 0)
                    if cand_hwnd and user32.IsWindow(cand_hwnd):
                        target_hwnd = cand_hwnd
            except Exception:
                pass

        # Fallback to finding by window title
        if not target_hwnd:
            target_hwnd = user32.FindWindowW(None, "Session Scribe")

        if target_hwnd and user32.IsWindow(target_hwnd):
            user32.ShowWindow(target_hwnd, SW_RESTORE)
            user32.SetForegroundWindow(target_hwnd)
            logger.info(f"Focused running window HWND: {target_hwnd}")

    def release(self) -> None:
        """Release mutex and clean up lock file."""
        if self._is_primary and sys.platform == "win32":
            if self._mutex_handle:
                ctypes.windll.kernel32.CloseHandle(self._mutex_handle)
                self._mutex_handle = None
            if os.path.isfile(self.lock_file):
                try:
                    os.remove(self.lock_file)
                except OSError:
                    pass
            self._is_primary = False

