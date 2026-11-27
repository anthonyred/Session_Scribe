# Build Record: Start Menu App Shortcut, Single-Instance Guard & Window Identity

- **Date:** 2026-11-17
- **What was asked:**
  - Create `scripts/install_start_menu.ps1` to place a shortcut in `%APPDATA%\Microsoft\Windows\Start Menu\Programs\Session Scribe.lnk` targeting `pythonw.exe` (`-m gui.app`) with icon `assets\icons\session_scribe.ico` and working directory `D:\Session_Scribe`.
  - Create `scripts/uninstall_start_menu.ps1` to remove the shortcut safely.
  - Set explicit Windows `AppUserModelID` (`SessionScribe.TranscriptionApp.2026`) and window icons (`iconbitmap` and `iconphoto`) so the taskbar groups and displays the Session Scribe icon instead of Python.
  - Set window title to "Session Scribe".
  - Enforce single instance: launching a second time brings the running window to the front.
  - When running without a console, show a GUI message box on fatal startup errors pointing to a rotated log file in `%LOCALAPPDATA%\SessionScribe\logs\session_scribe.log` (2MB max, rotated, scrubbing API keys/secrets).
  - Relative paths anchor to repo root regardless of launch location.
  - Add "About Session Scribe..." dialog with version ("1.2.0"), author ("Anthony Reddy"), license ("All rights reserved © 2026 Anthony Reddy"), summary, repo link, Python version, platform.
  - Update `README.md` with installation, uninstallation, Pin to Start, icon cache rebuilding (`ie4uinit.exe -show`), and note on shortcut vs exe stub.
  - Automated tests under `tests/`.

- **Why:**
  - Provide a first-class native Windows desktop experience where the app can be searched, pinned to Start and Taskbar, launched without an unwanted console pop-up, grouped under its own custom branding icon in taskbar and Alt+Tab, and safeguarded against accidental duplicate instances or cryptic headless crash failures.

- **What was built (user-visible behaviour):**
  - **Start Menu Search & Launcher:** "Session Scribe" appears in Windows Start Search and "All apps".
  - **Clean Launch:** Launching from Start Menu runs `pythonw.exe` without an extraneous terminal window.
  - **Taskbar Identity & Grouping:** The application window displays custom navy-and-gold Session Scribe branding in the title bar, Alt+Tab switcher, and Windows Taskbar.
  - **Single Instance Focus:** If the user launches Session Scribe while an instance is already running, the running instance is un-minimized and brought to the front; the second process exits cleanly.
  - **Helpful Crash Dialog:** If a catastrophic error occurs before the main window starts (e.g. missing venv or dependency), a native dialog box shows the exact error message and the path to `%LOCALAPPDATA%\SessionScribe\logs\session_scribe.log`.
  - **About Dialog:** Accessible via `View ▾` -> `About Session Scribe...` showing version 1.2.0, author, copyright/license, environment specs, and documentation links.

- **How to use it:**
  - Install shortcut: Run `& .\scripts\install_start_menu.ps1` in PowerShell.
  - Pin to Start: Search "Session Scribe" in Start menu, right click, and choose "Pin to Start".
  - Uninstall shortcut: Run `& .\scripts\uninstall_start_menu.ps1`.
  - Rebuild icon cache (if Windows displays old cached icon): Run `ie4uinit.exe -show`.

- **Settings and defaults:**
  - Shortcut location: `%APPDATA%\Microsoft\Windows\Start Menu\Programs\Session Scribe.lnk`
  - Target: `D:\Session_Scribe\.venv\Scripts\pythonw.exe`
  - Arguments: `-m gui.app`
  - Icon: `assets\icons\session_scribe.ico,0`
  - AppUserModelID: `SessionScribe.TranscriptionApp.2026`
  - Lockfile: `%LOCALAPPDATA%\SessionScribe\session_scribe.lock`
  - Mutex: `Global\SessionScribe_SingleInstance_Mutex_v2026`
  - Log file: `%LOCALAPPDATA%\SessionScribe\logs\session_scribe.log` (2MB max, 5 backup rotations)

- **Files changed:**
  - `assets/icons/session_scribe_logo.svg` (Created vector logo asset)
  - `assets/icons/session_scribe_logo_1024.png` (Created 1024x1024 master icon)
  - `assets/icons/session_scribe.ico` (Created multi-resolution icon: 16, 24, 32, 48, 64, 128, 256)
  - `scripts/install_start_menu.ps1` (Created Start menu installer script)
  - `scripts/uninstall_start_menu.ps1` (Created Start menu uninstaller script)
  - `scripts/create_app_icons.py` (Created icon generation script)
  - `gui/single_instance.py` (Created Windows named mutex & HWND focus manager)
  - `gui/app_logging.py` (Created rotating logger with secret scrubber & fatal error GUI dialog)
  - `gui/app.py` (Integrated root path anchoring, AppUserModelID, icon setup, single-instance lifecycle, and About dialog)
  - `README.md` (Updated installation, pinning, icon cache troubleshooting, and version verification)
  - `tests/test_start_menu_and_app.py` (Automated verification test suite)

- **Limitations and known issues:**
  - Standard Windows shortcut `.lnk` depends on the virtual environment remaining at `D:\Session_Scribe\.venv`. If the repo or virtual environment is moved, `install_start_menu.ps1` should be re-run.

- **How it was verified:**
  - `scripts/install_start_menu.ps1` executed and verified target, icon, arguments, and working directory on disk via `scripts/verify_shortcut.py`.
  - Uninstaller tested and confirmed removal of shortcut.
  - Multi-resolution `.ico` verified with `PIL.Image` across all 7 resolutions.
  - Secret scrubbing verified: tokens like `nvapi-...` and `hf_...` replaced with `[REDACTED_API_KEY]`.
  - Single instance test: verified second instance detects primary, invokes Win32 `ShowWindow` / `SetForegroundWindow`, and releases lockfile on process exit.
  - Automated test suite `tests/test_start_menu_and_app.py` executed: 5/5 tests passed (100%).

- **How to roll it back:**
  - Run `.\scripts\uninstall_start_menu.ps1` to delete the Start Menu shortcut.
  - Revert changes to `gui/app.py` and `README.md`.

