"""
scribe_core/audit_log.py
Keyed-MAC Audit Log for Session Scribe Vault v2.

Features:
- Keyed hash-chain using HMAC-SHA256 bound to K_audit (derived from K_master via HKDF).
- Anonymous IDs only (strictly 128-bit spk_ IDs or system tokens; zero names or biometrics).
- Append-only integrity verification: detecting any modified, deleted, inserted, or truncated entries.
- Head Hash Mirroring: persists the latest head hash to a secondary mirror path.
- n8n Webhook Payload Schema: defines the payload structure for external hash verification.
"""

from __future__ import annotations

import dataclasses
from datetime import datetime, timezone
import hashlib
import hmac
import json
import logging
import os
import time
from typing import Any, Dict, List, Optional, Tuple

from scribe_core.vault_crypto import (
    hkdf_derive,
    atomic_write_bytes,
    TamperError,
    VaultCryptoError,
)

logger = logging.getLogger("scribe_core.audit")

AUDIT_LOG_NAME = "audit.log"
AUDIT_HEAD_MIRROR_NAME = "audit_head.mirror"
AUDIT_HKDF_INFO = b"session_scribe_audit_v2_keyed_mac"
GENESIS_PREV_MAC = "0" * 64


@dataclasses.dataclass(frozen=True)
class N8nAuditPayload:
    """
    Schema for the external n8n webhook notification.
    Contains ONLY anonymous IDs, cryptographic hashes, action types, and UTC timestamps.
    Never transmits names, biometrics, or sensitive plaintext.
    """
    schema_version: str
    timestamp: str          # ISO 8601 UTC
    index: int
    action: str             # e.g., "VAULT_INIT", "PROFILE_QUARANTINED", "PROFILE_SHREDDED"
    target_id: str          # Anonymous spk_ ID or "SYSTEM"
    head_mac: str           # 64-character HMAC-SHA256 hex string

    def to_dict(self) -> Dict[str, Any]:
        return dataclasses.asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)


class KeyedAuditLog:
    """
    Append-only tamper-evident audit logger using a keyed HMAC chain.
    """

    def __init__(self, vault_dir: str, mirror_dir: Optional[str] = None):
        self.vault_dir = os.path.abspath(vault_dir)
        self.audit_dir = os.path.join(self.vault_dir, "audit")
        os.makedirs(self.audit_dir, exist_ok=True)
        self.log_path = os.path.join(self.audit_dir, AUDIT_LOG_NAME)

        # Local secondary location for head hash mirroring outside the vault directory
        if mirror_dir:
            self.mirror_dir = os.path.abspath(mirror_dir)
        else:
            vault_parent = os.path.dirname(self.vault_dir)
            self.mirror_dir = os.path.join(vault_parent, "audit_mirror")
        os.makedirs(self.mirror_dir, exist_ok=True)
        self.mirror_path = os.path.join(self.mirror_dir, AUDIT_HEAD_MIRROR_NAME)

    @staticmethod
    def derive_audit_key(master_key: bytes) -> bytes:
        """Derives a dedicated 256-bit audit signing key from the master key."""
        return hkdf_derive(master_key, length=32, info=AUDIT_HKDF_INFO)

    def append_entry(
        self,
        audit_key: bytes,
        action: str,
        target_id: str,
        details: Optional[Dict[str, Any]] = None,
        timestamp: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Appends a new authenticated record to the audit chain.
        - action: High-level event identifier (e.g. PROFILE_QUARANTINED, PROFILE_SHREDDED).
        - target_id: Anonymous 128-bit spk_ ID or "SYSTEM" (strictly no personal names).
        - details: Optional dictionary of anonymous metadata.
        - timestamp: Optional explicit ISO UTC timestamp string (for simulation/testing).
        """
        # Strict validation: Target ID must be anonymous
        if not target_id.startswith("spk_") and target_id not in ("SYSTEM", "ALL_PROFILES"):
            raise ValueError(f"Security Invariant Violation: Non-anonymous target_id '{target_id}' rejected.")

        last_entry = self.get_latest_entry()
        if last_entry is None:
            new_index = 0
            prev_mac = GENESIS_PREV_MAC
        else:
            new_index = last_entry["index"] + 1
            prev_mac = last_entry["entry_mac"]

        now_utc = timestamp or datetime.now(timezone.utc).isoformat()
        monotonic_ts = time.monotonic()

        record_core = {
            "index": new_index,
            "timestamp": now_utc,
            "monotonic_ts": round(monotonic_ts, 4),
            "action": action,
            "target_id": target_id,
            "details": details or {},
            "prev_mac": prev_mac,
        }

        # Compute HMAC-SHA256 over canonical JSON string of record core
        canonical_bytes = json.dumps(record_core, sort_keys=True).encode("utf-8")
        entry_mac = hmac.new(audit_key, canonical_bytes, hashlib.sha256).hexdigest()

        full_entry = dict(record_core)
        full_entry["entry_mac"] = entry_mac

        # Append to audit.log with line flush
        line = json.dumps(full_entry, sort_keys=True) + "\n"
        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write(line)
            f.flush()
            os.fsync(f.fileno())

        # Update secondary head hash mirror
        self._update_head_mirror(full_entry)

        return full_entry

    def get_latest_entry(self) -> Optional[Dict[str, Any]]:
        """Returns the last entry in the audit log or None if empty."""
        if not os.path.isfile(self.log_path):
            return None
        last_line = ""
        with open(self.log_path, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    last_line = line
        if not last_line:
            return None
        try:
            return json.loads(last_line)
        except Exception:
            return None

    def verify_chain(self, audit_key: bytes) -> Tuple[bool, Optional[str]]:
        """
        Verifies the complete audit log hash chain from genesis to head.
        Asserts:
        - HMAC signature validity on every line.
        - Correct chain continuity (prev_mac matches preceding entry_mac).
        - Strictly increasing sequence indices.
        - Secondary head mirror consistency.
        Returns: (True, None) on success, or (False, failure_reason).
        """
        if not os.path.isfile(self.log_path):
            return True, None

        expected_prev_mac = GENESIS_PREV_MAC
        expected_index = 0
        last_entry: Optional[Dict[str, Any]] = None

        with open(self.log_path, "r", encoding="utf-8") as f:
            for line_no, line in enumerate(f, start=1):
                raw = line.strip()
                if not raw:
                    continue
                try:
                    entry = json.loads(raw)
                except Exception as e:
                    return False, f"Line {line_no}: Malformed JSON ({e})"

                entry_index = entry.get("index")
                if entry_index != expected_index:
                    return False, f"Line {line_no}: Sequence break (expected index {expected_index}, got {entry_index})"

                if entry.get("prev_mac") != expected_prev_mac:
                    return False, f"Line {line_no}: Broken hash chain (prev_mac mismatch)"

                # Verify HMAC
                claimed_mac = entry.get("entry_mac")
                record_core = {
                    "index": entry["index"],
                    "timestamp": entry["timestamp"],
                    "monotonic_ts": entry["monotonic_ts"],
                    "action": entry["action"],
                    "target_id": entry["target_id"],
                    "details": entry["details"],
                    "prev_mac": entry["prev_mac"],
                }
                canonical_bytes = json.dumps(record_core, sort_keys=True).encode("utf-8")
                computed_mac = hmac.new(audit_key, canonical_bytes, hashlib.sha256).hexdigest()

                if not hmac.compare_digest(claimed_mac, computed_mac):
                    return False, f"Line {line_no}: HMAC verification failed (tampered content)"

                expected_prev_mac = claimed_mac
                expected_index += 1
                last_entry = entry

        # Verify mirror consistency if mirror exists
        if last_entry and os.path.isfile(self.mirror_path):
            try:
                with open(self.mirror_path, "r", encoding="utf-8") as mf:
                    mirror_data = json.loads(mf.read())
                if mirror_data.get("head_mac") != last_entry["entry_mac"]:
                    return False, "Head hash does not match secondary mirror (possible log truncation)"
            except Exception as e:
                logger.warning("Could not read audit mirror: %s", e)

        return True, None

    def get_latest_timestamp(self) -> Optional[datetime]:
        """Returns the UTC datetime of the latest audit log entry for clock rollback detection."""
        latest = self.get_latest_entry()
        if not latest:
            return None
        try:
            return datetime.fromisoformat(latest["timestamp"])
        except Exception:
            return None

    def generate_n8n_payload(self) -> Optional[N8nAuditPayload]:
        """Constructs the n8n notification payload representing the current head of the log."""
        latest = self.get_latest_entry()
        if not latest:
            return None
        return N8nAuditPayload(
            schema_version="1.0",
            timestamp=latest["timestamp"],
            index=latest["index"],
            action=latest["action"],
            target_id=latest["target_id"],
            head_mac=latest["entry_mac"],
        )

    def _update_head_mirror(self, head_entry: Dict[str, Any]) -> None:
        try:
            mirror_content = json.dumps({
                "index": head_entry["index"],
                "timestamp": head_entry["timestamp"],
                "head_mac": head_entry["entry_mac"],
                "target_id": head_entry["target_id"],
                "action": head_entry["action"],
            }, indent=2).encode("utf-8")
            atomic_write_bytes(self.mirror_path, mirror_content)
        except Exception as e:
            logger.error("Failed to update audit head mirror: %s", e)
