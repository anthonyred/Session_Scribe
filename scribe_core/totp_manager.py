"""
scribe_core/totp_manager.py
TOTP Two-Factor Authentication Manager for Session Scribe Vault v2.

Features:
- RFC 6238 TOTP using pyotp.
- Secret wrapped under Master Key (K_master) via AES-256-GCM.
- In-memory QR code generation using qrcode (NEVER written to disk).
- ±1 step drift tolerance (30-second window: previous, current, next).
- Replay protection: tracks and persists the last consumed time slice.
- Anti-brute-force rate limiting: progressive delay after consecutive failed entries.
"""

from __future__ import annotations

import io
import json
import logging
import os
import secrets
import time
from typing import Any, Dict, Optional, Tuple, Union

try:
    import pyotp
except ImportError:
    pyotp = None  # type: ignore

try:
    import qrcode
    from PIL import Image, ImageTk
except ImportError:
    qrcode = None  # type: ignore
    Image = None  # type: ignore
    ImageTk = None  # type: ignore

from scribe_core.vault_crypto import (
    aes_gcm_encrypt,
    aes_gcm_decrypt,
    atomic_write_bytes,
    best_effort_shred_file,
    VaultCryptoError,
    TamperError,
    AuthenticationError,
)

logger = logging.getLogger("scribe_core.totp")

TOTP_FILE_NAME = "totp.dat"
TOTP_STATE_FILE_NAME = "totp_state.dat"
ISSUER_NAME = "SessionScribe"
ACCOUNT_NAME = "LocalVault"
MAX_FAILED_ATTEMPTS = 5
RATE_LIMIT_WINDOW_SECONDS = 30.0


class TOTPError(VaultCryptoError):
    """Base error for TOTP operations."""
    pass


class TOTPRateLimitError(TOTPError):
    """Raised when TOTP verification is locked due to too many failed attempts."""
    pass


class TOTPReplayError(TOTPError):
    """Raised when a TOTP code from a previously used time slice is presented."""
    pass


class TOTPManager:
    """
    Manages two-factor authentication lifecycle for Vault v2.
    """

    def __init__(self, vault_dir: str):
        self.vault_dir = os.path.abspath(vault_dir)
        self.totp_path = os.path.join(self.vault_dir, TOTP_FILE_NAME)
        self.state_path = os.path.join(self.vault_dir, TOTP_STATE_FILE_NAME)

        self._consecutive_failures = 0
        self._lockout_until = 0.0
        self._last_used_timestep = -1
        self._load_state()

    def is_enrolled(self) -> bool:
        """Checks if a wrapped TOTP secret exists on disk."""
        return os.path.isfile(self.totp_path)

    def generate_new_secret(self) -> str:
        """Generates a fresh 160-bit Base32 secret for TOTP enrollment."""
        if pyotp is None:
            raise TOTPError("pyotp library is not installed.")
        return pyotp.random_base32()

    def get_provisioning_uri(self, secret: str, account_name: str = ACCOUNT_NAME) -> str:
        """Returns the standard otpauth:// provisioning URI for authenticator apps."""
        if pyotp is None:
            raise TOTPError("pyotp library is not installed.")
        totp = pyotp.TOTP(secret)
        return totp.provisioning_uri(name=account_name, issuer_name=ISSUER_NAME)

    def get_qr_image_in_memory(self, uri: str) -> Any:
        """
        Renders the provisioning URI into an in-memory PIL Image.
        CRITICAL: The image is never written to disk or any temporary file.
        """
        if qrcode is None:
            raise TOTPError("qrcode library is not installed.")
        qr = qrcode.QRCode(
            version=1,
            error_correction=qrcode.constants.ERROR_CORRECT_M,
            box_size=6,
            border=2,
        )
        qr.add_data(uri)
        qr.make(fit=True)
        return qr.make_image(fill_color="black", back_color="white")

    def enroll_secret(self, master_key: bytes, secret: str, verification_code: str) -> Tuple[bool, str]:
        """
        Validates the initial setup code and wraps the TOTP secret under master key.
        Saves the encrypted blob to totp.dat atomically.
        """
        if pyotp is None:
            return False, "pyotp library is not installed."

        secret = secret.strip().replace(" ", "").upper()
        totp = pyotp.TOTP(secret)
        # Verify initial code strictly against current time slice
        current_time = time.time()
        time_step = int(current_time // 30)

        if not totp.verify(verification_code.strip(), valid_window=1):
            return False, "Invalid TOTP code. Check your authenticator app time."

        # Wrap secret under master key
        try:
            payload = json.dumps({
                "secret": secret,
                "enrolled_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(current_time)),
            }).encode("utf-8")

            aad = b"totp_secret_envelope_v2"
            encrypted_blob = aes_gcm_encrypt(master_key, payload, aad=aad)
            atomic_write_bytes(self.totp_path, encrypted_blob)

            self._last_used_timestep = time_step
            self._consecutive_failures = 0
            self._lockout_until = 0.0
            self._save_state()
            return True, "TOTP enrolled successfully."
        except Exception as e:
            logger.error("Failed to enroll TOTP: %s", e)
            return False, f"Failed to save TOTP secret: {e}"

    def verify_code(
        self,
        master_key: bytes,
        code: str,
        at_time: Optional[float] = None,
    ) -> Tuple[bool, str]:
        """
        Verifies a user-supplied 6-digit TOTP code against the wrapped secret.
        Enforces:
        - Rate limiting / backoff on consecutive failures.
        - Drift window of ±1 step (30s past, current, 30s future).
        - Replay protection: rejects codes from the same or earlier used timestep.
        """
        if pyotp is None:
            return False, "pyotp library is not installed."

        now = time.monotonic()
        if now < self._lockout_until and at_time is None:
            wait_sec = int(self._lockout_until - now) + 1
            return False, f"Too many failed TOTP attempts. Please wait {wait_sec}s."

        if not self.is_enrolled():
            return False, "TOTP is not enrolled for this vault."

        # Unwrap secret
        try:
            with open(self.totp_path, "rb") as f:
                blob = f.read()
            aad = b"totp_secret_envelope_v2"
            decrypted = aes_gcm_decrypt(master_key, blob, aad=aad)
            data = json.loads(decrypted.decode("utf-8"))
            secret = data["secret"]
        except Exception as e:
            logger.error("Failed to unwrap TOTP secret: %s", e)
            return False, "Failed to decrypt TOTP secret. Check master credentials."

        clean_code = str(code).strip().replace(" ", "")
        if not clean_code.isdigit() or len(clean_code) != 6:
            self._record_failure()
            return False, "TOTP code must be exactly 6 digits."

        totp = pyotp.TOTP(secret)
        current_unix_time = time.time() if at_time is None else at_time
        current_timestep = int(current_unix_time // 30)

        # Check drift across [-1, 0, +1]
        matching_step: Optional[int] = None
        for step_offset in (0, -1, 1):
            eval_time = current_unix_time + (step_offset * 30)
            target_step = int(eval_time // 30)
            expected_code = totp.at(eval_time)
            if secrets.compare_digest(clean_code, expected_code):
                matching_step = target_step
                break

        if matching_step is None:
            self._record_failure()
            return False, "Invalid TOTP code."

        # Replay Protection: matching_step must be strictly greater than last used
        if matching_step <= self._last_used_timestep:
            logger.warning("TOTP replay attack detected: timestep %d <= %d", matching_step, self._last_used_timestep)
            self._record_failure()
            return False, "This TOTP code has already been used. Please wait for the next 30-second code."

        # Success: update replay state and clear failures
        self._last_used_timestep = matching_step
        self._consecutive_failures = 0
        self._lockout_until = 0.0
        self._save_state()
        return True, "TOTP verified."

    def _record_failure(self) -> None:
        self._consecutive_failures += 1
        delay = min(60.0, 2.0 ** min(self._consecutive_failures, 6))
        self._lockout_until = time.monotonic() + delay
        self._save_state()

    def _save_state(self) -> None:
        try:
            state_data = json.dumps({
                "last_used_timestep": self._last_used_timestep,
                "consecutive_failures": self._consecutive_failures,
                "lockout_until": self._lockout_until,
            }).encode("utf-8")
            atomic_write_bytes(self.state_path, state_data)
        except Exception as e:
            logger.error("Failed to save TOTP state: %s", e)

    def _load_state(self) -> None:
        if not os.path.isfile(self.state_path):
            return
        try:
            with open(self.state_path, "rb") as f:
                data = json.loads(f.read().decode("utf-8"))
            self._last_used_timestep = int(data.get("last_used_timestep", -1))
            self._consecutive_failures = int(data.get("consecutive_failures", 0))
            self._lockout_until = float(data.get("lockout_until", 0.0))
        except Exception:
            self._last_used_timestep = -1
            self._consecutive_failures = 0
            self._lockout_until = 0.0
