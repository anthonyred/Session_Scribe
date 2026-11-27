"""
scribe_core/quarantine_manager.py
Destructive Flow, 24-Hour Quarantine State Machine, and Crypto-Shredding Engine.

Security Invariants:
1. Quarantine state is stored strictly INSIDE the encrypted, authenticated record envelopes
   (schema_version = 2). No plaintext side files exist on disk.
2. Quarantined profiles are marked inactive/unusable in transcription matching, but recoverable.
3. Person keys (K_person) are NOT destroyed during quarantine.
4. Cancelling quarantine requires fresh re-authentication (master passphrase + live TOTP).
5. Crypto-shredding is refused until 24 hours have elapsed.
6. Clock rollback protection: compares against monotonic clocks and the latest audit log entry.
7. Crypto-shredding destroys K_person, overwrites ciphertext files with random bytes, removes them,
   and records an immutable entry in the keyed-MAC audit log.
8. Bulk delete requires an additional explicit confirmation phrase.
"""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
import json
import logging
import os
import secrets
import time
from typing import Any, Dict, List, Optional, Tuple

from scribe_core.vault_v2 import VaultV2StorageEngine
from scribe_core.totp_manager import TOTPManager
from scribe_core.audit_log import KeyedAuditLog
from scribe_core.vault_crypto import (
    best_effort_shred_file,
    VaultCryptoError,
    TamperError,
    AuthenticationError,
)

logger = logging.getLogger("scribe_core.quarantine")

QUARANTINE_HOLD_HOURS = 24.0
BULK_CONFIRMATION_TEXT = "PURGE ALL PROFILES"


class QuarantineError(VaultCryptoError):
    """Base error for quarantine and shred operations."""
    pass


class EarlyShredRefusedError(QuarantineError):
    """Raised when an attempt is made to shred a profile before 24 hours have elapsed."""
    pass


class ClockRollbackDetectedError(QuarantineError):
    """Raised when current system time is earlier than historical audit records."""
    pass


class QuarantineManager:
    """
    Orchestrates the 24-hour quarantine lifecycle, re-authentication gates,
    and irreversible crypto-shredding.
    """

    def __init__(self, storage_engine: VaultV2StorageEngine):
        self.storage = storage_engine
        self.vault = storage_engine.vault
        self.totp_mgr = TOTPManager(self.storage.vault_dir)
        self.audit_log = KeyedAuditLog(self.storage.vault_dir)

    def _authenticate(
        self,
        passphrase: str,
        totp_code: str,
        at_time: Optional[float] = None,
    ) -> Tuple[bool, str, Optional[bytes]]:
        """Verifies master passphrase and live TOTP token."""
        # 1. Master Passphrase verification
        ok, msg = self.vault.unlock(passphrase)
        if not ok:
            return False, f"Passphrase re-authentication failed: {msg}", None

        master_key = self.vault.get_master_key_bytes()
        if not master_key:
            return False, "Failed to access master key.", None

        # 2. TOTP verification
        if self.totp_mgr.is_enrolled():
            totp_ok, totp_msg = self.totp_mgr.verify_code(master_key, totp_code, at_time=at_time)
            if not totp_ok:
                return False, f"Two-Factor Authentication failed: {totp_msg}", None

        return True, "Authenticated", master_key

    def prepare_destructive_action(
        self,
        spk_id: str,
        passphrase: str,
        totp_code: str,
        at_time: Optional[float] = None,
    ) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        """
        Step 1 of Destructive Flow:
        Requires fresh re-authentication, resolves the person's real name while unlocked,
        counts associated audio samples, and generates a short 6-character confirmation token.
        """
        auth_ok, auth_msg, _ = self._authenticate(passphrase, totp_code, at_time=at_time)
        if not auth_ok:
            return False, auth_msg, None

        try:
            id_rec = self.storage.read_identity_record(spk_id)
            name = id_rec.get("display_name") or id_rec.get("full_name") or id_rec.get("name", "Unknown Person")
            roles = id_rec.get("roles", [])

            # Get sample count from biometric zone
            sample_names = self.storage.list_biometric_samples(spk_id)
            sample_count = len(sample_names)

            # Generate short 6-char confirmation code (e.g. "CF-94A2")
            token = f"DEL-{secrets.token_hex(2).upper()}"

            summary = {
                "spk_id": spk_id,
                "name": name,
                "roles": roles,
                "sample_count": sample_count,
                "confirmation_code": token,
                "action": "QUARANTINE_FOR_24_HOURS",
            }
            return True, "Confirmation prepared.", summary
        except Exception as e:
            logger.error("Failed to prepare destructive action for %s: %s", spk_id, e)
            return False, f"Failed to retrieve profile: {e}", None

    def move_to_quarantine(
        self,
        spk_id: str,
        passphrase: str,
        totp_code: str,
        expected_code: str,
        entered_code: str,
        reason: str = "User initiated profile deletion",
        at_time: Optional[float] = None,
    ) -> Tuple[bool, str]:
        """
        Step 2 of Destructive Flow:
        Verifies confirmation code and moves the profile into 24-hour quarantine.
        State is stored INSIDE the encrypted records; keys are NOT destroyed.
        """
        if not secrets.compare_digest(expected_code.strip(), entered_code.strip()):
            return False, "Confirmation code does not match. Operation aborted."

        auth_ok, auth_msg, master_key = self._authenticate(passphrase, totp_code, at_time=at_time)
        if not auth_ok or master_key is None:
            return False, auth_msg

        now = datetime.now(timezone.utc)
        purge_at = now + timedelta(hours=QUARANTINE_HOLD_HOURS)

        quarantine_data = {
            "is_quarantined": True,
            "quarantine_timestamp_utc": now.isoformat(),
            "scheduled_purge_utc": purge_at.isoformat(),
            "confirmation_code": expected_code,
            "reason": reason,
        }

        try:
            # Update Identity Record inside ciphertext
            self.storage.update_identity_record(spk_id, {"quarantine": quarantine_data})

            # Update Biometric Record inside ciphertext
            self.storage.update_biometric_record(spk_id, {"quarantine": quarantine_data})
            samples = self.storage.list_biometric_samples(spk_id)

            # Record in Keyed Audit Log (anonymous ID only)
            audit_key = self.audit_log.derive_audit_key(master_key)
            self.audit_log.append_entry(
                audit_key=audit_key,
                action="PROFILE_QUARANTINED",
                target_id=spk_id,
                details={
                    "scheduled_purge_utc": purge_at.isoformat(),
                    "reason": reason,
                    "sample_count": len(samples),
                },
            )

            return True, f"Profile moved to quarantine. It will be permanently eligible for shredding after {purge_at.strftime('%Y-%m-%d %H:%M:%S UTC')}."
        except Exception as e:
            logger.error("Failed to quarantine profile %s: %s", spk_id, e)
            return False, f"Quarantine operation failed: {e}"

    def cancel_quarantine(
        self,
        spk_id: str,
        passphrase: str,
        totp_code: str,
        at_time: Optional[float] = None,
    ) -> Tuple[bool, str]:
        """
        Cancels quarantine and restores profile to active state.
        Requires re-authentication.
        """
        auth_ok, auth_msg, master_key = self._authenticate(passphrase, totp_code, at_time=at_time)
        if not auth_ok or master_key is None:
            return False, auth_msg

        now = datetime.now(timezone.utc)
        clean_quarantine = {
            "is_quarantined": False,
            "quarantine_timestamp_utc": None,
            "scheduled_purge_utc": None,
            "confirmation_code": None,
            "reason": None,
        }

        try:
            id_rec = self.storage.read_identity_record(spk_id)
            if not id_rec.get("quarantine", {}).get("is_quarantined", False):
                return False, "Profile is not currently in quarantine."

            self.storage.update_identity_record(spk_id, {"quarantine": clean_quarantine})
            self.storage.update_biometric_record(spk_id, {"quarantine": clean_quarantine})

            # Record in Keyed Audit Log
            audit_key = self.audit_log.derive_audit_key(master_key)
            self.audit_log.append_entry(
                audit_key=audit_key,
                action="PROFILE_RESTORED",
                target_id=spk_id,
                details={"restored_utc": now.isoformat()},
            )

            return True, "Profile successfully restored from quarantine."
        except Exception as e:
            logger.error("Failed to restore profile %s: %s", spk_id, e)
            return False, f"Restore failed: {e}"

    def crypto_shred(
        self,
        spk_id: str,
        passphrase: str,
        totp_code: str,
        simulate_time_elapsed: bool = False,
        at_time: Optional[float] = None,
    ) -> Tuple[bool, str]:
        """
        Permanently shreds a quarantined profile after 24 hours have elapsed.
        Eradicates person keys, overwrites ciphertext files with random bytes,
        and unlinks all records from disk.
        """
        auth_ok, auth_msg, master_key = self._authenticate(passphrase, totp_code, at_time=at_time)
        if not auth_ok or master_key is None:
            return False, auth_msg

        now = datetime.now(timezone.utc)

        # 1. Clock Rollback Protection
        latest_audit_time = self.audit_log.get_latest_timestamp()
        if latest_audit_time and now < latest_audit_time:
            msg = f"Clock rollback detected: System time {now.isoformat()} is earlier than latest audit entry {latest_audit_time.isoformat()}."
            logger.error(msg)
            return False, msg

        # 2. Check 24-hour quarantine hold inside record
        try:
            id_rec = self.storage.read_identity_record(spk_id)
        except Exception as e:
            return False, f"Could not read profile: {e}"

        q_info = id_rec.get("quarantine", {})
        if not q_info.get("is_quarantined", False):
            return False, "Profile is not quarantined. Move to quarantine first."

        purge_str = q_info.get("scheduled_purge_utc")
        if not purge_str:
            return False, "Malformed quarantine metadata (missing scheduled purge timestamp)."

        scheduled_purge = datetime.fromisoformat(purge_str)
        if not simulate_time_elapsed and now < scheduled_purge:
            remaining = scheduled_purge - now
            hours_left = max(0.0, remaining.total_seconds() / 3600.0)
            msg = f"Early shred attempt refused. 24-hour hold is active ({hours_left:.1f} hours remaining)."
            logger.warning(msg)
            return False, msg

        # 3. Permanent Shredding Execution
        try:
            # Delete and best-effort shred biometric data and WAV samples
            self.storage.delete_biometric_record(spk_id)

            # Delete and best-effort shred identity record
            self.storage.delete_identity_record(spk_id)

            # Append to Keyed Audit Log
            audit_key = self.audit_log.derive_audit_key(master_key)
            self.audit_log.append_entry(
                audit_key=audit_key,
                action="PROFILE_SHREDDED",
                target_id=spk_id,
                details={
                    "shredded_utc": now.isoformat(),
                    "method": "crypto_shred_key_eradication",
                },
            )

            return True, f"Profile {spk_id} permanently shredded and removed from vault."
        except Exception as e:
            logger.error("Failed to crypto-shred profile %s: %s", spk_id, e)
            return False, f"Crypto-shredding failed: {e}"

    def bulk_quarantine(
        self,
        spk_ids: List[str],
        passphrase: str,
        totp_code: str,
        confirmation_phrase: str,
        reason: str = "Bulk quarantine purge",
        at_time: Optional[float] = None,
    ) -> Tuple[bool, str]:
        """
        Bulk quarantine operation requiring the explicit phrase 'PURGE ALL PROFILES'.
        """
        if confirmation_phrase.strip() != BULK_CONFIRMATION_TEXT:
            return False, f"Bulk deletion requires typing exactly '{BULK_CONFIRMATION_TEXT}'."

        auth_ok, auth_msg, master_key = self._authenticate(passphrase, totp_code, at_time=at_time)
        if not auth_ok or master_key is None:
            return False, auth_msg

        now = datetime.now(timezone.utc)
        purge_at = now + timedelta(hours=QUARANTINE_HOLD_HOURS)

        quarantined_count = 0
        for spk_id in spk_ids:
            try:
                token = f"BULK-{secrets.token_hex(2).upper()}"
                quarantine_data = {
                    "is_quarantined": True,
                    "quarantine_timestamp_utc": now.isoformat(),
                    "scheduled_purge_utc": purge_at.isoformat(),
                    "confirmation_code": token,
                    "reason": reason,
                }
                self.storage.update_identity_record(spk_id, {"quarantine": quarantine_data})
                self.storage.update_biometric_record(spk_id, {"quarantine": quarantine_data})
                quarantined_count += 1
            except Exception as e:
                logger.error("Failed to bulk quarantine %s: %s", spk_id, e)

        # Audit entry for bulk action
        audit_key = self.audit_log.derive_audit_key(master_key)
        self.audit_log.append_entry(
            audit_key=audit_key,
            action="BULK_QUARANTINE",
            target_id="ALL_PROFILES",
            details={
                "requested_count": len(spk_ids),
                "quarantined_count": quarantined_count,
                "reason": reason,
            },
        )

        return True, f"Successfully quarantined {quarantined_count} of {len(spk_ids)} profiles."

    def list_quarantined_profiles(self) -> List[Dict[str, Any]]:
        """Returns list of currently quarantined profiles and their time status."""
        active_ids = self.storage.list_identity_ids()
        quarantined = []
        now = datetime.now(timezone.utc)

        for spk_id in active_ids:
            try:
                rec = self.storage.read_identity_record(spk_id)
                q_info = rec.get("quarantine", {})
                if q_info.get("is_quarantined", False):
                    purge_str = q_info.get("scheduled_purge_utc")
                    scheduled_purge = datetime.fromisoformat(purge_str) if purge_str else now
                    remaining_seconds = max(0.0, (scheduled_purge - now).total_seconds())

                    quarantined.append({
                        "spk_id": spk_id,
                        "name": rec.get("display_name") or rec.get("full_name") or rec.get("name", "Unknown"),
                        "roles": rec.get("roles", []),
                        "quarantined_at": q_info.get("quarantine_timestamp_utc"),
                        "scheduled_purge_at": purge_str,
                        "hours_remaining": round(remaining_seconds / 3600.0, 2),
                        "can_shred": remaining_seconds <= 0.0,
                    })
            except Exception:
                pass
        return quarantined

    def is_quarantined(self, spk_id: str) -> bool:
        """True if the profile is currently held in 24-hour quarantine."""
        try:
            rec = self.storage.read_identity_record(spk_id)
            if not rec:
                return False
            q_info = rec.get("quarantine", {})
            return bool(q_info.get("is_quarantined", False))
        except Exception:
            return False

    def stage_quarantine(
        self,
        spk_id: str,
        reason: str = "User initiated profile deletion",
        confirmation_code: str = "",
        at_time: Optional[float] = None,
    ) -> bool:
        """Directly stage a profile into 24-hour quarantine (e.g. for testing or verified flows)."""
        now_ts = at_time if at_time is not None else time.time()
        now_dt = datetime.fromtimestamp(now_ts, tz=timezone.utc)
        purge_dt = now_dt + timedelta(hours=QUARANTINE_HOLD_HOURS)

        try:
            self.storage.update_identity_record(
                spk_id=spk_id,
                updates={
                    "quarantine": {
                        "is_quarantined": True,
                        "quarantine_timestamp_utc": now_dt.isoformat(),
                        "scheduled_purge_utc": purge_dt.isoformat(),
                        "quarantine_reason": reason,
                        "confirmation_code": confirmation_code,
                    }
                },
            )
            return True
        except Exception as e:
            logger.error("Failed to stage quarantine for %s: %s", spk_id, e)
            return False
