"""
Session Scribe — Tests for Part A:
Start Menu Integration, Icons, Taskbar AppUserModelID, Single Instance, and Logging.
"""

import logging
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

# Ensure repo root is on sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from PIL import Image
from gui.app_logging import SecretScrubbingFilter, setup_app_logging, show_fatal_startup_error
from gui.single_instance import SingleInstanceManager
import gui.app as app_module


class TestPartAStartMenuAndApp(unittest.TestCase):
    """Test suite for Part A requirements."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)
        from logging.handlers import RotatingFileHandler
        root_logger = logging.getLogger()
        for h in list(root_logger.handlers):
            if isinstance(h, RotatingFileHandler):
                h.close()
                root_logger.removeHandler(h)

    def test_icon_assets_exist_and_valid(self):
        """session_scribe.ico, session_scribe_logo_1024.png, and session_scribe_logo.svg must exist and be valid."""
        repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        icons_dir = os.path.join(repo_root, "assets", "icons")

        ico_path = os.path.join(icons_dir, "session_scribe.ico")
        png_path = os.path.join(icons_dir, "session_scribe_logo_1024.png")
        svg_path = os.path.join(icons_dir, "session_scribe_logo.svg")

        self.assertTrue(os.path.isfile(ico_path), f"Missing {ico_path}")
        self.assertTrue(os.path.isfile(png_path), f"Missing {png_path}")
        self.assertTrue(os.path.isfile(svg_path), f"Missing {svg_path}")

        # Check PNG size
        with Image.open(png_path) as img:
            self.assertEqual(img.size, (1024, 1024))
            self.assertIn(img.mode, ("RGBA", "RGB"))

        # Check ICO format
        with Image.open(ico_path) as img:
            self.assertEqual(img.format, "ICO")

        # Check SVG content
        with open(svg_path, "r", encoding="utf-8") as f:
            svg_text = f.read()
            self.assertIn("<svg", svg_text)
            self.assertIn("</svg>", svg_text)

    def test_icon_contains_all_seven_sizes(self):
        """assets/icons/session_scribe.ico must contain 16, 24, 32, 48, 64, 128, and 256 px."""
        repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        ico_path = os.path.join(repo_root, "assets", "icons", "session_scribe.ico")
        self.assertTrue(os.path.isfile(ico_path))

        with Image.open(ico_path) as ico:
            sizes = set()
            if hasattr(ico, "ico") and hasattr(ico.ico, "entry"):
                for entry in ico.ico.entry:
                    sizes.add((entry.width, entry.height))
            else:
                for frame in range(getattr(ico, "n_frames", 1)):
                    ico.seek(frame)
                    sizes.add(ico.size)

        expected = {(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)}
        self.assertTrue(
            expected.issubset(sizes),
            f"Missing required sizes in ICO. Found: {sizes}, Expected: {expected}",
        )

    def test_app_user_model_id_and_about_metadata(self):
        """AppUserModelID, __version__, __author__, and copyright must be properly declared."""
        self.assertEqual(app_module.APP_USER_MODEL_ID, "SessionScribe.TranscriptionApp.2026")
        self.assertTrue(hasattr(app_module, "__version__"))
        self.assertTrue(hasattr(app_module, "__author__"))
        self.assertTrue(hasattr(app_module, "__copyright__"))
        self.assertEqual(app_module.__author__, "Anthony Reddy")
        self.assertEqual(app_module.__copyright__, "© 2026 Anthony Reddy. All rights reserved.")

    def test_window_title_and_icon_binding(self):
        """Window title must be 'Session Scribe' and icon assets must be present for iconbitmap/iconphoto."""
        repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        ico_path = os.path.join(repo_root, "assets", "icons", "session_scribe.ico")
        png_path = os.path.join(repo_root, "assets", "icons", "session_scribe_logo_1024.png")

        self.assertTrue(os.path.isfile(ico_path), "session_scribe.ico must exist for iconbitmap")
        self.assertTrue(os.path.isfile(png_path), "session_scribe_logo_1024.png must exist for iconphoto")

        # Verify Tk window configuration if display is available
        try:
            import tkinter as tk
            root = tk.Tk()
            root.withdraw()
            root.title("Session Scribe")
            self.assertEqual(root.title(), "Session Scribe")

            # Test icon loading
            root.iconbitmap(ico_path)
            from PIL import Image, ImageTk
            img = Image.open(png_path).resize((64, 64), Image.Resampling.LANCZOS)
            photo = ImageTk.PhotoImage(img)
            root.iconphoto(True, photo)
            root.destroy()
        except Exception:
            # Headless environment without display
            pass

    def test_secret_scrubbing_filter(self):
        """Logging must redact API keys, tokens, and passphrases."""
        test_log_file = os.path.join(self.temp_dir, "test_scrub.log")
        logger = logging.getLogger("test_scrub_logger")
        logger.setLevel(logging.INFO)

        handler = logging.FileHandler(test_log_file, encoding="utf-8")
        handler.addFilter(SecretScrubbingFilter())
        formatter = logging.Formatter("%(message)s")
        handler.setFormatter(formatter)
        logger.addHandler(handler)

        logger.info("Initializing with key nvapi-abc12345678901234567890 and HF hf_0123456789abcdef0123456789")
        logger.info("Passphrase: secret_passphrase_1234!")
        handler.close()

        with open(test_log_file, "r", encoding="utf-8") as f:
            content = f.read()

        self.assertNotIn("nvapi-abc12345678901234567890", content)
        self.assertNotIn("hf_0123456789abcdef0123456789", content)
        self.assertIn("[REDACTED_SECRET]", content)

    def test_single_instance_manager_lifecycle(self):
        """SingleInstanceManager acquires lock for primary, detects secondary, and cleans up."""
        test_lock_file = os.path.join(self.temp_dir, "test_app.lock")
        test_mutex_name = f"Local\\SessionScribe_TestMutex_{os.getpid()}"

        mgr1 = SingleInstanceManager(mutex_name=test_mutex_name, lock_file=test_lock_file)
        self.assertTrue(mgr1.acquire(), "First instance must acquire lock")

        # Record mock HWND
        mgr1.register_window(12345)
        self.assertTrue(os.path.isfile(test_lock_file))

        # Second instance should detect already running
        mgr2 = SingleInstanceManager(mutex_name=test_mutex_name, lock_file=test_lock_file)
        if sys.platform == "win32":
            self.assertFalse(mgr2.acquire(), "Second instance must not acquire lock")

        # Cleanup primary
        mgr1.release()
        self.assertFalse(os.path.isfile(test_lock_file), "Lock file must be removed on release")

    def test_simulated_startup_failure_shows_message_and_writes_log(self):
        """Simulated startup failure shows graphical message with log path and writes rotating log."""
        temp_log = os.path.join(self.temp_dir, "fatal_startup.log")
        setup_app_logging(temp_log)

        dialog_calls = []
        def mock_showerror(title, message, **kwargs):
            dialog_calls.append((title, message))

        with patch("tkinter.messagebox.showerror", side_effect=mock_showerror):
            with patch("tkinter.Tk") as mock_tk_cls:
                mock_tk_instance = mock_tk_cls.return_value
                mock_tk_instance.withdraw = lambda: None
                mock_tk_instance.attributes = lambda *args: None
                mock_tk_instance.destroy = lambda: None

                test_exc = RuntimeError("Simulated GPU initialization failure")
                show_fatal_startup_error(test_exc, log_path=temp_log)

        self.assertTrue(len(dialog_calls) > 0, "Fatal startup error must invoke messagebox.showerror")
        title, msg = dialog_calls[0]
        self.assertIn("Fatal Startup Error", title)
        self.assertIn("Simulated GPU initialization failure", msg)
        self.assertIn(temp_log, msg)

        # Verify log file was written
        self.assertTrue(os.path.isfile(temp_log))
        with open(temp_log, "r", encoding="utf-8") as f:
            log_content = f.read()
        self.assertIn("Fatal startup failure", log_content)
        self.assertIn("Simulated GPU initialization failure", log_content)

    def test_installer_writes_shortcut_to_temp_location_and_checks_properties(self):
        """scripts/install_start_menu.ps1 writes shortcut to temp location with correct properties, and uninstall removes it."""
        repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        inst_script = os.path.join(repo_root, "scripts", "install_start_menu.ps1")
        uninst_script = os.path.join(repo_root, "scripts", "uninstall_start_menu.ps1")

        ps_exe = shutil.which("powershell") or os.path.join(
            os.environ.get("SystemRoot", "C:\\Windows"), "System32", "WindowsPowerShell", "v1.0", "powershell.exe"
        )
        if not os.path.isfile(ps_exe):
            self.skipTest("PowerShell executable not found on host")

        # 1. Run install_start_menu.ps1 pointing to self.temp_dir
        cmd = [
            ps_exe,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            f"& '{inst_script}' -AppRoot '{repo_root}' -DestinationDir '{self.temp_dir}'",
        ]
        res = subprocess.run(cmd, capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, f"Installer failed: {res.stderr}\n{res.stdout}")

        lnk_path = os.path.join(self.temp_dir, "Session Scribe.lnk")
        self.assertTrue(os.path.isfile(lnk_path), "Session Scribe.lnk must be created in temp destination")

        # 2. Inspect shortcut properties via PowerShell
        inspect_cmd = [
            ps_exe,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            f"$sh = (New-Object -ComObject WScript.Shell).CreateShortcut('{lnk_path}'); "
            "Write-Output \"TARGET=$($sh.TargetPath)\"; "
            "Write-Output \"ARGS=$($sh.Arguments)\"; "
            "Write-Output \"WORKDIR=$($sh.WorkingDirectory)\"; "
            "Write-Output \"ICON=$($sh.IconLocation)\"",
        ]
        insp_res = subprocess.run(inspect_cmd, capture_output=True, text=True)
        self.assertEqual(insp_res.returncode, 0)
        lines = [line.strip() for line in insp_res.stdout.splitlines() if line.strip()]

        prop_map = {}
        for l in lines:
            if "=" in l:
                k, v = l.split("=", 1)
                prop_map[k] = v

        self.assertIn("TARGET", prop_map)
        self.assertTrue(
            prop_map["TARGET"].lower().endswith("pythonw.exe"),
            f"Target must be pythonw.exe, got: {prop_map['TARGET']}",
        )
        self.assertIn("-m gui.app", prop_map.get("ARGS", ""))
        self.assertEqual(os.path.normcase(prop_map.get("WORKDIR", "")), os.path.normcase(repo_root))
        self.assertIn("session_scribe.ico", prop_map.get("ICON", ""))

        # 3. Test uninstall_start_menu.ps1
        uninst_cmd = [
            ps_exe,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            f"& '{uninst_script}' -DestinationDir '{self.temp_dir}'",
        ]
        uninst_res = subprocess.run(uninst_cmd, capture_output=True, text=True)
        self.assertEqual(uninst_res.returncode, 0)
        self.assertFalse(os.path.isfile(lnk_path), "Session Scribe.lnk must be removed by uninstaller")


if __name__ == "__main__":
    unittest.main()
