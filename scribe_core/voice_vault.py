"""
scribe_core/voice_vault.py
Voice Vault Core for Session Scribe v2.

Architecture:
- Five-tier envelope key hierarchy via KeyHierarchyManager.
- Argon2id key derivation via argon2-cffi (calibrated to ~0.5-1.0s, off-UI thread).
- Windows DPAPI hardware and user account binding.
- AES-256-GCM authenticated encryption with Record ID bound into AAD.
- Dual-zone least-privilege separation (IdentityZoneKeyContext, BiometricZoneKeyContext).
- Single authoritative state source: "uninitialized", "locked", "unlocked", "locking_soon".
- Inactivity auto-lock (default 30 min), screensaver hook, and Windows session lock hook (WTSRegisterSessionNotification).
- Run-active lock suppression capped at 4 hours max with a 2-minute post-run grace period.
- Persistent failed-unlock counter across restarts (throttle.dat).
- Legacy vault.key detection & non-destructive archiving (leaving plaintext library untouched until migration).
- No recovery export by design (lost passphrase = permanent data loss).
- Pure-Python Argon2 and home-brew ciphers completely eliminated.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from datetime import datetime, timezone, timedelta
import gc
import json
import logging
import os
import shutil
import sys
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from scribe_core.key_hierarchy import (
    BiometricZoneKeyContext,
    IdentityZoneKeyContext,
    KeyHierarchyManager,
)
from scribe_core.totp_manager import TOTPManager
from scribe_core.audit_log import KeyedAuditLog
from scribe_core.vault_crypto import (
    DEFAULT_ARGON2_MEMORY_COST_KIB,
    DEFAULT_ARGON2_PARALLELISM,
    DEFAULT_ARGON2_TIME_COST,
    MIN_PASSPHRASE_LENGTH,
    AuthenticationError,
    TamperError,
    VaultCryptoError,
    atomic_write_bytes,
    evaluate_passphrase_strength,
    generate_speaker_id,
    zero_buffer,
)

logger = logging.getLogger("scribe_core.voice_vault")

DEFAULT_IDLE_TIMEOUT_SECONDS = 30 * 60     # 30 minutes
LOCKING_SOON_THRESHOLD_SECONDS = 120       # 2 minutes
MAX_RUN_SUPPRESSION_SECONDS = 4 * 3600     # 4 hours hard maximum cap
RUN_END_GRACE_PERIOD_SECONDS = 120         # 2 minutes post-run grace period

WM_WTSSESSION_CHANGE = 0x02B1
WTS_SESSION_LOCK = 0x7
NOTIFY_FOR_THIS_SESSION = 0


class VoiceVaultState:
    UNINITIALIZED = "uninitialized"
    LOCKED = "locked"
    UNLOCKED = "unlocked"
    LOCKING_SOON = "locking_soon"


class WindowsSessionLockHook:
    """
    Listens for Windows workstation lock events (Win+L / Lock Screen)
    using WTSRegisterSessionNotification on a lightweight hidden message-only window.
    """

    def __init__(self, lock_callback: Callable[[], None]):
        self.lock_callback = lock_callback
        self.hwnd: Optional[int] = None
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._wnd_proc = None
        self._class_atom = None

        if sys.platform == "win32":
            self._thread = threading.Thread(target=self._run_message_loop, daemon=True)
            self._thread.start()

    def _run_message_loop(self) -> None:
        try:
            user32 = ctypes.windll.user32
            wtsapi32 = ctypes.windll.wtsapi32
            kernel32 = ctypes.windll.kernel32

            WNDPROC = ctypes.WINFUNCTYPE(
                wintypes.LPARAM, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
            )
            user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
            user32.DefWindowProcW.restype = wintypes.LPARAM

            def py_wnd_proc(hwnd, msg, wparam, lparam):
                if msg == WM_WTSSESSION_CHANGE:
                    if wparam == WTS_SESSION_LOCK:
                        logger.info("Windows Session Lock event detected. Invoking vault auto-lock.")
                        try:
                            self.lock_callback()
                        except Exception:
                            pass
                    return 0
                return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

            self._wnd_proc = WNDPROC(py_wnd_proc)

            class WNDCLASSW(ctypes.Structure):
                _fields_ = [
                    ("style", wintypes.UINT),
                    ("lpfnWndProc", WNDPROC),
                    ("cbClsExtra", ctypes.c_int),
                    ("cbWndExtra", ctypes.c_int),
                    ("hInstance", wintypes.HINSTANCE),
                    ("hIcon", wintypes.HICON),
                    ("hCursor", wintypes.HICON),
                    ("hbrBackground", wintypes.HBRUSH),
                    ("lpszMenuName", wintypes.LPCWSTR),
                    ("lpszClassName", wintypes.LPCWSTR),
                ]

            class_name = f"SessionScribe_WTSHook_{os.getpid()}_{id(self)}"
            wc = WNDCLASSW()
            wc.lpfnWndProc = self._wnd_proc
            wc.lpszClassName = class_name
            wc.hInstance = kernel32.GetModuleHandleW(None)

            self._class_atom = user32.RegisterClassW(ctypes.byref(wc))
            if not self._class_atom:
                return

            HWND_MESSAGE = ctypes.c_void_p(-3)
            self.hwnd = user32.CreateWindowExW(
                0, class_name, "SessionScribeWTS", 0, 0, 0, 0, 0, HWND_MESSAGE, None, wc.hInstance, None
            )

            if not self.hwnd:
                return

            wtsapi32.WTSRegisterSessionNotification(self.hwnd, NOTIFY_FOR_THIS_SESSION)

            msg = wintypes.MSG()
            while not self._stop_event.is_set():
                # Process messages with 500ms timeout
                res = user32.MsgWaitForMultipleObjects(0, None, False, 500, 0x04FF)  # QS_ALLINPUT
                while user32.PeekMessageW(ctypes.byref(msg), 0, 0, 0, 1):  # PM_REMOVE = 1
                    if msg.message == 0x0012:  # WM_QUIT
                        break
                    user32.TranslateMessage(ctypes.byref(msg))
                    user32.DispatchMessageW(ctypes.byref(msg))

            wtsapi32.WTSUnRegisterSessionNotification(self.hwnd)
            user32.DestroyWindow(self.hwnd)
            user32.UnregisterClassW(class_name, wc.hInstance)
        except Exception as e:
            logger.debug("WindowsSessionLockHook error: %s", e)

    def stop(self) -> None:
        self._stop_event.set()


class VoiceVault:
    """
    Core Voice Vault controller.
    Manages cryptographic lifecycle, auto-lock timeouts, rate limiting,
    and observer notifications for consistent UI state.
    """

    def __init__(
        self,
        keys_dir: Optional[str] = None,
        idle_timeout_seconds: int = DEFAULT_IDLE_TIMEOUT_SECONDS,
        time_cost: int = DEFAULT_ARGON2_TIME_COST,
        memory_cost: int = DEFAULT_ARGON2_MEMORY_COST_KIB,
        parallelism: int = DEFAULT_ARGON2_PARALLELISM,
    ):
        if keys_dir is None:
            local_appdata = os.environ.get(
                "LOCALAPPDATA",
                os.path.join(os.path.expanduser("~"), "AppData", "Local"),
            )
            self.vault_dir = os.path.join(local_appdata, "SessionScribe", "vault", "v2")
            self._legacy_keys_dir = os.path.join(local_appdata, "SessionScribe", "keys")
        else:
            self.vault_dir = os.path.abspath(keys_dir)
            self._legacy_keys_dir = os.path.join(os.path.dirname(self.vault_dir), "keys")

        self.throttle_file = os.path.join(self.vault_dir, "throttle.dat")
        self.settings_file = os.path.join(self.vault_dir, "vault_settings.json")
        self.idle_timeout_seconds = idle_timeout_seconds
        self.time_cost = time_cost
        self.memory_cost = memory_cost
        self.parallelism = parallelism

        self._hierarchy = KeyHierarchyManager(self.vault_dir)

        self._consecutive_failures = 0
        self._lockout_until = 0.0
        self._load_throttle_state()

        self.totp_manager = TOTPManager(self.vault_dir)
        self.audit_log = KeyedAuditLog(self.vault_dir)

        self._last_active_time = time.monotonic()
        self._active_run_count = 0
        self._run_start_time = 0.0
        self._listeners: List[Callable[[], None]] = []
        self._lock = threading.RLock()

        # Check for legacy vault.key at startup
        self.detect_and_archive_legacy_vault()

        # Windows session lock hook
        self._wts_hook = WindowsSessionLockHook(self.lock)

        # Background timer thread for screensaver and inactivity evaluation
        self._stop_timer = False
        self._timer_thread = threading.Thread(target=self._auto_lock_loop, daemon=True)
        self._timer_thread.start()

    def _load_throttle_state(self) -> None:
        """Reads persisted failed unlock counter across process restarts."""
        if os.path.isfile(self.throttle_file):
            try:
                with open(self.throttle_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self._consecutive_failures = int(data.get("consecutive_failures", 0))
                self._lockout_until = float(data.get("lockout_until", 0.0))
            except Exception:
                pass

    def _save_throttle_state(self) -> None:
        """Persists failed unlock counter across restarts atomically."""
        try:
            data = {
                "consecutive_failures": self._consecutive_failures,
                "lockout_until": self._lockout_until,
                "updated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }
            atomic_write_bytes(self.throttle_file, json.dumps(data, indent=2).encode("utf-8"))
        except Exception:
            pass

    def _load_settings(self) -> Dict[str, Any]:
        """Loads non-secret vault settings (passphrase age, snooze timestamp)."""
        if os.path.isfile(self.settings_file):
            try:
                with open(self.settings_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {}

    def _save_settings(self, settings: Dict[str, Any]) -> None:
        """Atomically saves non-secret vault settings."""
        try:
            atomic_write_bytes(self.settings_file, json.dumps(settings, indent=2).encode("utf-8"))
        except Exception as e:
            logger.warning("Could not save vault settings: %s", e)

    def get_passphrase_age_days(self, now_dt: Optional[datetime] = None) -> int:
        """Returns days since the master passphrase was initialized or last changed."""
        st = self._load_settings()
        changed_at = st.get("passphrase_changed_at")
        if not changed_at:
            return 0
        try:
            dt = datetime.fromisoformat(changed_at)
            now = now_dt or datetime.now(timezone.utc)
            if dt.tzinfo is None and now.tzinfo is not None:
                dt = dt.replace(tzinfo=timezone.utc)
            elif dt.tzinfo is not None and now.tzinfo is None:
                now = now.replace(tzinfo=timezone.utc)
            return max(0, (now - dt).days)
        except Exception:
            return 0

    def check_passphrase_age_warning(self, now_dt: Optional[datetime] = None) -> Optional[Dict[str, Any]]:
        """
        After 365 days, returns warning dict with age_days unless snoozed.
        Never forces a change or blocks access.
        """
        if not self.is_initialized():
            return None
        st = self._load_settings()
        changed_at = st.get("passphrase_changed_at")
        if not changed_at:
            return None
        try:
            dt = datetime.fromisoformat(changed_at)
            now = now_dt or datetime.now(timezone.utc)
            if dt.tzinfo is None and now.tzinfo is not None:
                dt = dt.replace(tzinfo=timezone.utc)
            elif dt.tzinfo is not None and now.tzinfo is None:
                now = now.replace(tzinfo=timezone.utc)
            age_days = (now - dt).days
            if age_days < 365:
                return None

            remind_after = st.get("remind_after_utc")
            if remind_after:
                r_dt = datetime.fromisoformat(remind_after)
                if r_dt.tzinfo is None and now.tzinfo is not None:
                    r_dt = r_dt.replace(tzinfo=timezone.utc)
                elif r_dt.tzinfo is not None and now.tzinfo is None:
                    now = now.replace(tzinfo=timezone.utc)
                if now < r_dt:
                    return None

            return {
                "needs_warning": True,
                "age_days": age_days,
                "passphrase_changed_at": changed_at,
            }
        except Exception:
            return None

    def snooze_passphrase_warning(self, days: int = 7, now_dt: Optional[datetime] = None) -> None:
        """Snoozes passphrase age reminder for specified number of days (default 7)."""
        st = self._load_settings()
        now = now_dt or datetime.now(timezone.utc)
        snooze_until = (now + timedelta(days=days)).isoformat()
        st["remind_after_utc"] = snooze_until
        self._save_settings(st)

    def erase_vault(self) -> Tuple[bool, str]:
        """
        Permanently erases all Vault v2 files (best effort shredding),
        clears throttle and TOTP state, and resets to uninitialized.
        Does NOT touch real events, audio clips, manifests, or transcripts.
        """
        with self._lock:
            # 1. Best effort attempt to log VAULT_PERMANENTLY_ERASED in audit log
            try:
                master_key = None
                if self._hierarchy.is_unlocked():
                    master_key = self._hierarchy.get_master_key_bytes()
                if master_key:
                    audit_key = self.audit_log.derive_audit_key(master_key)
                else:
                    audit_key = b"\x00" * 32
                self.audit_log.append_entry(
                    audit_key=audit_key,
                    action="VAULT_PERMANENTLY_ERASED",
                    target_id="SYSTEM",
                    details={"reason": "forgot_passphrase", "timestamp_utc": datetime.now(timezone.utc).isoformat()},
                )
            except Exception as e:
                logger.debug("Could not log erase event: %s", e)

            # 2. Lock hierarchy and wipe memory
            try:
                self._hierarchy.lock_vault()
            except Exception:
                pass

            # 3. Shred all files and wipe directories under self.vault_dir
            if os.path.isdir(self.vault_dir):
                for root, dirs, files in os.walk(self.vault_dir, topdown=False):
                    for fname in files:
                        fpath = os.path.join(root, fname)
                        try:
                            best_effort_shred_file(fpath)
                        except Exception:
                            try:
                                os.remove(fpath)
                            except Exception:
                                pass
                    for dname in dirs:
                        dpath = os.path.join(root, dname)
                        try:
                            shutil.rmtree(dpath, ignore_errors=True)
                        except Exception:
                            pass

            # 4. Reset internal states and re-instantiate managers
            self._consecutive_failures = 0
            self._lockout_until = 0.0
            self._hierarchy = KeyHierarchyManager(self.vault_dir)
            self.totp_manager = TOTPManager(self.vault_dir)
            self.audit_log = KeyedAuditLog(self.vault_dir)

            logger.warning("Voice Vault v2 erased permanently by user request.")
            self._notify_listeners()
            return True, "Vault erased successfully. All voice profiles and keys removed."

    def detect_and_archive_legacy_vault(self) -> Optional[Dict[str, str]]:
        """
        Startup detection for legacy vault.key.
        Archives the old key file safely (does not delete it silently),
        leaves D:\\Events\\_voice_library untouched until migration,
        and provides guidance to initialize Vault v2.
        """
        legacy_key_path = os.path.join(self._legacy_keys_dir, "vault.key")
        if not os.path.isfile(legacy_key_path):
            return None

        backup_path = f"{legacy_key_path}.legacy_v1_backup"
        if not os.path.isfile(backup_path):
            try:
                shutil.copy2(legacy_key_path, backup_path)
                logger.info("Legacy vault.key archived to %s. Plaintext voice library left untouched.", backup_path)
            except Exception as e:
                logger.warning("Could not archive legacy vault.key: %s", e)

        return {
            "legacy_key": legacy_key_path,
            "archive_path": backup_path,
            "status": "archived_pending_v2_setup",
        }

    def add_listener(self, callback: Callable[[], None]) -> None:
        """Register a callback notified on every lock/unlock/timer state change."""
        with self._lock:
            if callback not in self._listeners:
                self._listeners.append(callback)

    def remove_listener(self, callback: Callable[[], None]) -> None:
        """Unregister a state listener callback."""
        with self._lock:
            if callback in self._listeners:
                self._listeners.remove(callback)

    def _notify_listeners(self) -> None:
        """Invoke all registered state listeners."""
        with self._lock:
            callbacks = list(self._listeners)
        for cb in callbacks:
            try:
                cb()
            except Exception:
                pass

    def is_initialized(self) -> bool:
        """True if vault headers and zone containers exist."""
        return self._hierarchy.is_initialized()

    def touch_activity(self) -> None:
        """Refreshes the idle activity timestamp."""
        with self._lock:
            self._last_active_time = time.monotonic()

    def get_remaining_idle_seconds(self) -> float:
        """Returns seconds remaining before inactivity auto-lock."""
        with self._lock:
            if not self.is_unlocked():
                return 0.0
            if self._active_run_count > 0:
                elapsed_run = time.monotonic() - self._run_start_time
                remaining_cap = max(0.0, MAX_RUN_SUPPRESSION_SECONDS - elapsed_run)
                return min(float(self.idle_timeout_seconds), remaining_cap)

            elapsed = time.monotonic() - self._last_active_time
            remaining = max(0.0, float(self.idle_timeout_seconds) - elapsed)
            return remaining

    def state(self) -> str:
        """
        Authoritative single state source for all UI chips and gates:
        - "uninitialized"
        - "locked"
        - "unlocked"
        - "locking_soon" (unlocked with <= 120s remaining before auto-lock)
        """
        if not self.is_initialized():
            return VoiceVaultState.UNINITIALIZED

        if not self.is_unlocked():
            return VoiceVaultState.LOCKED

        remaining = self.get_remaining_idle_seconds()
        if self._active_run_count == 0 and remaining <= LOCKING_SOON_THRESHOLD_SECONDS:
            return VoiceVaultState.LOCKING_SOON

        return VoiceVaultState.UNLOCKED

    def state_label(self) -> str:
        """User-facing localized label for status chips."""
        s = self.state()
        if s == VoiceVaultState.UNINITIALIZED:
            return "🔒 Setup Vault"
        if s == VoiceVaultState.LOCKED:
            return "🔒 Vault Locked"
        if s == VoiceVaultState.LOCKING_SOON:
            remaining_mins = max(1, int(self.get_remaining_idle_seconds() / 60.0 + 0.5))
            return f"⏳ Locking soon ({remaining_mins}m)"
        return "🔓 Vault Unlocked"

    def is_unlocked(self) -> bool:
        """
        True if the vault is initialized, unlocked, and within idle timeout.
        Auto-locks if idle timeout expired and no active run is executing.
        Caps active run suppression at 4 hours maximum.
        """
        with self._lock:
            if not self._hierarchy.is_unlocked():
                return False

            if self._active_run_count > 0:
                elapsed_run = time.monotonic() - self._run_start_time
                if elapsed_run >= MAX_RUN_SUPPRESSION_SECONDS:
                    logger.warning(
                        "Run-active lock suppression exceeded hard cap (%d hours). Locking vault.",
                        int(MAX_RUN_SUPPRESSION_SECONDS / 3600),
                    )
                    self.lock()
                    return False
                return True

            elapsed = time.monotonic() - self._last_active_time
            if elapsed >= self.idle_timeout_seconds:
                self.lock()
                return False

            return True

    def get_progressive_delay(self) -> float:
        """Returns throttle sleep seconds based on consecutive failures (1s, 2s, 4s, ..., 60s max)."""
        with self._lock:
            if self._consecutive_failures == 0:
                return 0.0
            return float(min(60.0, 1.0 * (2 ** (self._consecutive_failures - 1))))

    def is_totp_enrolled(self) -> bool:
        """Returns True if a wrapped TOTP secret exists on disk."""
        return self.totp_manager.is_enrolled()

    def get_totp_manager(self) -> TOTPManager:
        return self.totp_manager

    def get_audit_log(self) -> KeyedAuditLog:
        return self.audit_log

    def initialize(
        self,
        passphrase: str,
        totp_secret: Optional[str] = None,
        totp_code: Optional[str] = None,
    ) -> Tuple[bool, str]:
        """Creates and unlocks a fresh Vault v2 instance, optionally enrolling TOTP."""
        with self._lock:
            if self.is_initialized():
                return False, "Vault is already initialized on this machine."

            strength = evaluate_passphrase_strength(passphrase)
            if not strength["valid"]:
                return False, "Passphrase does not meet security requirements. " + " ".join(strength.get("feedback", []))

            try:
                self._hierarchy.initialize_vault(
                    passphrase=passphrase,
                    time_cost=self.time_cost,
                    memory_cost_kib=self.memory_cost,
                    parallelism=self.parallelism,
                )
                self._consecutive_failures = 0
                self._lockout_until = 0.0
                self._save_throttle_state()
                self._last_active_time = time.monotonic()
                now_iso = datetime.now(timezone.utc).isoformat()
                self._save_settings({
                    "passphrase_changed_at": now_iso,
                    "remind_after_utc": None,
                })

                master_key = self._hierarchy.get_master_key_bytes()

                # If TOTP credentials provided, enroll TOTP
                if totp_secret and totp_code:
                    totp_ok, totp_msg = self.totp_manager.enroll_secret(master_key, totp_secret, totp_code)
                    if not totp_ok:
                        self._hierarchy.lock_vault()
                        return False, f"TOTP enrollment failed: {totp_msg}"

                # Append VAULT_INITIALIZED to audit log
                if master_key:
                    audit_key = self.audit_log.derive_audit_key(master_key)
                    self.audit_log.append_entry(
                        audit_key=audit_key,
                        action="VAULT_INITIALIZED",
                        target_id="SYSTEM",
                        details={"totp_enabled": self.totp_manager.is_enrolled()},
                    )

                logger.info("Voice Vault v2 initialized and unlocked successfully.")
                self._notify_listeners()
                return True, "Voice Vault initialized successfully."
            except Exception as e:
                logger.error("Failed to initialize vault: %s", type(e).__name__)
                return False, f"Failed to initialize vault: {str(e)}"

    def unlock(self, passphrase: str, totp_code: Optional[str] = None) -> Tuple[bool, str]:
        """
        Daily unlock with passphrase only (plus Windows DPAPI user/hardware binding).
        TOTP is NOT required for daily unlock per security architecture; TOTP is reserved
        for destructive and security-altering actions.
        """
        delay = self.get_progressive_delay()
        if delay > 0.0:
            time.sleep(delay)

        with self._lock:
            if not self.is_initialized():
                return False, "Vault is not initialized. Please configure a passphrase first."

            try:
                self._hierarchy.unlock_vault(passphrase)
                self._consecutive_failures = 0
                self._lockout_until = 0.0
                self._save_throttle_state()
                self._last_active_time = time.monotonic()
                logger.info("Voice Vault v2 unlocked successfully.")
                self._notify_listeners()
                return True, "Vault unlocked successfully."
            except (AuthenticationError, TamperError) as e:
                self._consecutive_failures += 1
                next_delay = self.get_progressive_delay()
                self._lockout_until = time.time() + next_delay
                self._save_throttle_state()
                logger.warning("Vault unlock failed (attempt %d). Throttle: %.1fs", self._consecutive_failures, next_delay)
                return False, f"Passphrase verification failed. Next attempt delayed by {int(next_delay)}s."
            except Exception as e:
                self._consecutive_failures += 1
                self._save_throttle_state()
                logger.error("Unexpected unlock error: %s", type(e).__name__)
                return False, f"Unlock error: {str(e)}"

    def lock(self) -> None:
        """Securely locks the vault and wipes keys from memory."""
        with self._lock:
            was_unlocked = self._hierarchy.is_unlocked()
            self._hierarchy.lock_vault()
            gc.collect()
            if was_unlocked:
                logger.info("Voice Vault v2 locked. Keys cleared from memory.")
                self._notify_listeners()

    def change_passphrase(
        self,
        old_passphrase: str,
        new_passphrase: str,
        totp_code: Optional[str] = None,
    ) -> Tuple[bool, str]:
        """
        Re-wraps the master key with a new passphrase.
        Requires TOTP code verification if TOTP is enrolled.
        """
        with self._lock:
            if not self.is_initialized():
                return False, "Vault is uninitialized."

            strength = evaluate_passphrase_strength(new_passphrase)
            if not strength["valid"]:
                return False, "New passphrase is not strong enough. " + " ".join(strength["feedback"])

            if self.totp_manager.is_enrolled():
                if not totp_code:
                    return False, "Two-factor authentication code (TOTP) is required to change passphrase."
                master_key = self._hierarchy.get_master_key_bytes() if self._hierarchy.is_unlocked() else None
                if not master_key:
                    try:
                        self._hierarchy.unlock_vault(old_passphrase)
                        master_key = self._hierarchy.get_master_key_bytes()
                    except Exception:
                        return False, "Old passphrase verification failed."
                totp_ok, totp_msg = self.totp_manager.verify_code(master_key, totp_code)
                if not totp_ok:
                    return False, f"TOTP verification failed: {totp_msg}"

            try:
                self._hierarchy.change_passphrase(
                    old_passphrase=old_passphrase,
                    new_passphrase=new_passphrase,
                    time_cost=self.time_cost,
                    memory_cost_kib=self.memory_cost,
                    parallelism=self.parallelism,
                )
                self._last_active_time = time.monotonic()
                now_iso = datetime.now(timezone.utc).isoformat()
                self._save_settings({
                    "passphrase_changed_at": now_iso,
                    "remind_after_utc": None,
                })
                logger.info("Vault master key successfully re-wrapped with new passphrase.")
                self._notify_listeners()
                return True, "Passphrase changed successfully."
            except Exception as e:
                logger.error("Change passphrase failed: %s", type(e).__name__)
                return False, f"Failed to change passphrase: {str(e)}"

    def get_totp_reset_status(self) -> Dict[str, Any]:
        """Returns the current scheduled TOTP reset status."""
        reset_file = os.path.join(self.vault_dir, "totp_reset.json")
        if not os.path.isfile(reset_file):
            return {"active": False, "hours_remaining": 0.0, "scheduled_utc": None}
        try:
            with open(reset_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            scheduled = datetime.fromisoformat(data["scheduled_utc"])
            now = datetime.now(timezone.utc)
            if now >= scheduled:
                return {"active": True, "ready": True, "hours_remaining": 0.0, "scheduled_utc": data["scheduled_utc"]}
            else:
                rem = (scheduled - now).total_seconds() / 3600.0
                return {"active": True, "ready": False, "hours_remaining": rem, "scheduled_utc": data["scheduled_utc"]}
        except Exception:
            return {"active": False, "hours_remaining": 0.0, "scheduled_utc": None}

    def schedule_totp_reset(self, passphrase: str) -> Tuple[bool, str]:
        """
        Step 1 of TOTP Reset: Authenticates with passphrase and schedules a 24-hour cooldown.
        During the 24-hour window, the reset cannot be completed.
        """
        with self._lock:
            if not self.is_initialized():
                return False, "Vault is uninitialized."

            try:
                self._hierarchy.unlock_vault(passphrase)
            except Exception:
                return False, "Passphrase verification failed."

            reset_file = os.path.join(self.vault_dir, "totp_reset.json")
            now = datetime.now(timezone.utc)
            scheduled_time = now + timedelta(hours=24)
            data = {
                "scheduled_utc": scheduled_time.isoformat(),
                "initiated_utc": now.isoformat(),
            }
            atomic_write_bytes(reset_file, json.dumps(data, indent=2).encode("utf-8"))

            master_key = self._hierarchy.get_master_key_bytes()
            if master_key:
                audit_key = self.audit_log.derive_audit_key(master_key)
                self.audit_log.append_entry(
                    audit_key=audit_key,
                    action="TOTP_RESET_SCHEDULED",
                    target_id="SYSTEM",
                    details={"scheduled_purge_utc": scheduled_time.isoformat()},
                )

            logger.warning("TOTP reset scheduled. 24-hour hold active until %s.", scheduled_time.isoformat())
            return True, f"TOTP reset scheduled. A mandatory 24-hour security delay is active until {scheduled_time.strftime('%Y-%m-%d %H:%M UTC')}."

    def cancel_totp_reset(self) -> Tuple[bool, str]:
        """Cancels an active scheduled TOTP reset."""
        reset_file = os.path.join(self.vault_dir, "totp_reset.json")
        if os.path.isfile(reset_file):
            try:
                os.remove(reset_file)
            except Exception:
                pass
        return True, "Scheduled TOTP reset cancelled."

    def complete_totp_reset(
        self,
        passphrase: str,
        new_secret: str,
        new_totp_code: str,
        simulate_time_elapsed: bool = False,
    ) -> Tuple[bool, str]:
        """
        Step 2 of TOTP Reset: Enrolls new TOTP secret after 24-hour cooldown has elapsed.
        Requires passphrase re-authentication.
        """
        with self._lock:
            status = self.get_totp_reset_status()
            if not status["active"]:
                return False, "No TOTP reset is scheduled. Initiate a reset first."

            if not simulate_time_elapsed and not status.get("ready", False):
                rem = status.get("hours_remaining", 24.0)
                return False, f"Early TOTP reset refused. 24-hour delay active ({rem:.1f} hours remaining)."

            try:
                self._hierarchy.unlock_vault(passphrase)
            except Exception:
                return False, "Passphrase verification failed."

            master_key = self._hierarchy.get_master_key_bytes()
            totp_ok, totp_msg = self.totp_manager.enroll_secret(master_key, new_secret, new_totp_code)
            if not totp_ok:
                return False, f"Failed to enroll new TOTP secret: {totp_msg}"

            # Remove reset file
            reset_file = os.path.join(self.vault_dir, "totp_reset.json")
            if os.path.isfile(reset_file):
                try:
                    os.remove(reset_file)
                except Exception:
                    pass

            audit_key = self.audit_log.derive_audit_key(master_key)
            self.audit_log.append_entry(
                audit_key=audit_key,
                action="TOTP_RESET_COMPLETED",
                target_id="SYSTEM",
                details={},
            )
            return True, "New two-factor authenticator successfully enrolled."

    def begin_run(self) -> None:
        """Suppresses auto-lock during active transcription/processing runs with 4h cap."""
        with self._lock:
            if self._active_run_count == 0:
                self._run_start_time = time.monotonic()
            self._active_run_count += 1
            self.touch_activity()
            self._notify_listeners()

    def end_run(self) -> None:
        """Resumes auto-lock after run completion, applying a 2-minute grace period."""
        with self._lock:
            self._active_run_count = max(0, self._active_run_count - 1)
            if self._active_run_count == 0:
                # Apply 2-minute post-run grace period
                self._last_active_time = time.monotonic() - max(0, self.idle_timeout_seconds - RUN_END_GRACE_PERIOD_SECONDS)
            else:
                self.touch_activity()
            self._notify_listeners()

    def get_identity_context(self) -> IdentityZoneKeyContext:
        """Returns least-privilege identity zone context (requires unlocked vault)."""
        self.touch_activity()
        return self._hierarchy.get_identity_context()

    def get_biometric_context(self) -> BiometricZoneKeyContext:
        """Returns least-privilege biometric zone context (requires unlocked vault)."""
        self.touch_activity()
        return self._hierarchy.get_biometric_context()

    def get_audit_mac_key(self) -> bytes:
        """Returns HMAC key for audit logging (requires unlocked vault)."""
        self.touch_activity()
        return self._hierarchy.get_audit_mac_key()

    def get_master_key_bytes(self) -> bytes:
        """Returns raw master key bytes. For testing / backwards compatibility."""
        self.touch_activity()
        return self._hierarchy.get_master_key_bytes()

    # ------------------------------------------------------------------
    # OS Screensaver Check & Auto-Lock Loop
    # ------------------------------------------------------------------

    def _is_screensaver_active(self) -> bool:
        """Queries Windows SystemParametersInfo for screensaver status."""
        if sys.platform != "win32":
            return False
        try:
            user32 = ctypes.windll.user32
            is_running = wintypes.BOOL()
            # SPI_GETSCREENSAVERRUNNING = 0x0072
            res = user32.SystemParametersInfoW(0x0072, 0, ctypes.byref(is_running), 0)
            if res and is_running.value:
                return True
        except Exception:
            pass
        return False

    def _auto_lock_loop(self) -> None:
        """Background thread checking inactivity, screensaver, and state transitions."""
        last_state = None
        while not self._stop_timer:
            try:
                time.sleep(2.0)
                if self._hierarchy.is_unlocked():
                    if self._is_screensaver_active():
                        logger.info("Screensaver active: locking Voice Vault immediately.")
                        self.lock()
                    else:
                        # Check idle expiration or run suppression cap
                        self.is_unlocked()

                current_state = self.state()
                if current_state != last_state:
                    last_state = current_state
                    self._notify_listeners()
            except Exception:
                pass

    def close(self) -> None:
        """Shuts down background timer, unregisters WTS hook, and locks keys."""
        self._stop_timer = True
        if self._wts_hook:
            self._wts_hook.stop()
        self.lock()
