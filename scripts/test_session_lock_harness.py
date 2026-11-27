"""
scripts/test_session_lock_harness.py
Manual and automated test harness for Windows session-lock auto-lock.

Usage:
  # Automated simulation:
  python scripts/test_session_lock_harness.py --simulate

  # Interactive manual test (user locks Windows with Win+L):
  python scripts/test_session_lock_harness.py
"""

import argparse
import ctypes
from ctypes import wintypes
import os
import sys
import tempfile
import time

# Ensure project root is in sys.path
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from scribe_core.voice_vault import VoiceVault, WindowsSessionLockHook


def run_harness(interactive: bool = True):
    print("=" * 65)
    print("  SESSION SCRIBE — WINDOWS SESSION-LOCK AUTO-LOCK HARNESS")
    print("=" * 65)

    if sys.platform != "win32":
        print("[ERROR] This test harness requires Windows.")
        return 1

    temp_dir = tempfile.mkdtemp(prefix="ss_session_lock_test_")
    keys_dir = os.path.join(temp_dir, "vault")
    os.makedirs(keys_dir, exist_ok=True)

    print(f"[*] Initializing test vault in synthetic directory: {keys_dir}")
    vault = VoiceVault(keys_dir=keys_dir, idle_timeout_seconds=3600)
    passphrase = "correct horse battery staple blue sky"
    ok, msg = vault.initialize(passphrase)
    if not ok:
        print(f"[FAIL] Vault initialization failed: {msg}")
        return 1

    print(f"[+] Vault initialized and unlocked. State: is_unlocked = {vault.is_unlocked()}")

    # Ensure hook is running
    hook = WindowsSessionLockHook(lock_callback=vault.lock)
    time.sleep(0.5)  # Allow message loop thread to create window & register

    if not hook.hwnd:
        print("[FAIL] WindowsSessionLockHook failed to register message window.")
        hook.stop()
        return 1

    print(f"[+] WTSRegisterSessionNotification active on window HWND: {hook.hwnd}")

    if interactive:
        print("\n" + "*" * 65)
        print("  INSTRUCTIONS FOR MANUAL VERIFICATION:")
        print("  1. Press Win + L on your keyboard to lock your Windows workstation.")
        print("  2. Wait 3-5 seconds while locked.")
        print("  3. Log back in to Windows.")
        print("  4. Return to this console to see the auto-lock confirmation.")
        print("*" * 65 + "\n")
        print("[*] Monitoring vault status (press Ctrl+C to cancel)...")

        start_time = time.monotonic()
        locked = False
        try:
            while time.monotonic() - start_time < 60:
                if not vault.is_unlocked():
                    locked = True
                    break
                time.sleep(0.5)
        except KeyboardInterrupt:
            print("\n[-] Cancelled by user.")

        if locked:
            print(f"\n[PASS] Workstation lock detected! Vault auto-locked immediately.")
            print(f"[+] Vault state: is_unlocked = {vault.is_unlocked()}")
        else:
            print("\n[TIMEOUT] 60 seconds elapsed without detecting a session lock.")
    else:
        print("[*] Running programmatic simulation of WM_WTSSESSION_CHANGE (WTS_SESSION_LOCK)...")
        WM_WTSSESSION_CHANGE = 0x02B1
        WTS_SESSION_LOCK = 0x7

        user32 = ctypes.windll.user32
        user32.SendMessageW(hook.hwnd, WM_WTSSESSION_CHANGE, WTS_SESSION_LOCK, 0)
        time.sleep(0.5)

        if not vault.is_unlocked():
            print("[PASS] Programmatic WTS_SESSION_LOCK simulated successfully! Vault locked.")
        else:
            print("[FAIL] Vault was not locked following WTS_SESSION_LOCK message.")

    hook.stop()
    import shutil
    shutil.rmtree(temp_dir, ignore_errors=True)
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Test Windows session lock auto-lock hook.")
    parser.add_argument("--simulate", action="store_true", help="Send synthetic WM_WTSSESSION_CHANGE message instead of waiting for Win+L")
    args = parser.parse_args()

    sys.exit(run_harness(interactive=not args.simulate))
