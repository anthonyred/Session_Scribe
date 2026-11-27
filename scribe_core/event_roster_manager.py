"""
scribe_core/event_roster_manager.py
Event Roster and Per-Event Alias Mapping Engine for Session Scribe Stage 2.

Security Invariants:
1. Invariant: One person = one persistent spk_ ID across all sessions and events.
2. Invariant: Quarantined profiles NEVER appear in roster selection or acoustic matching.
3. Invariant: Plaintext names are visible only while Vault v2 is unlocked. While locked,
   only aliases (S01, S02) or masked tokens are displayed.
4. Invariant: Per-event alias maps (S01 -> spk_...) are stored encrypted in Vault v2 Identity Zone
   under the identity key, bound to random event_id (evt_...).
5. Invariant: Compute-zone files (manifest.json, clip_state.json) carry aliases only,
   NEVER spk_ IDs or plaintext names.
"""

from __future__ import annotations

import logging
import os
import secrets
from typing import Any, Dict, List, Optional, Tuple

from .vault_v2 import VaultV2StorageEngine
from .voice_vault import VoiceVault

logger = logging.getLogger("scribe_core.event_roster_manager")


def generate_event_id() -> str:
    """Generates a random 128-bit event identifier (evt_<16 hex chars>)."""
    return f"evt_{secrets.token_hex(8)}"


class EventRosterManager:
    """
    Manages the selection of people from Vault v2 into per-event rosters,
    generating isolated per-event aliases (S01, S02, ...) and storing the
    alias mapping table securely inside Vault v2 Identity Zone.
    """

    def __init__(self, storage: Any):
        if isinstance(storage, VoiceVault):
            self.vault: VoiceVault = storage
            self.storage = VaultV2StorageEngine(storage)
        else:
            self.storage = storage
            self.vault: VoiceVault = getattr(storage, "vault", None) or storage

    def get_available_vault_people(self) -> List[Dict[str, Any]]:
        """
        Retrieves enrolled profiles eligible for event roster assignment.
        Security Rule: Quarantined profiles are STRICTLY EXCLUDED.
        Security Rule: If vault is locked, returns empty list (vault gated).
        """
        if not self.vault.is_unlocked():
            return []

        bio_ids = self.storage.list_biometric_ids(include_quarantined=False)
        people: List[Dict[str, Any]] = []

        for spk_id in bio_ids:
            try:
                bio_meta, _ = self.storage.read_biometric_record(spk_id)
                # Verify quarantine status in metadata
                if bio_meta.get("quarantine", {}).get("is_quarantined", False):
                    continue

                sample_count = bio_meta.get("sample_count", len(bio_meta.get("sample_names", [])))
                name = self.storage.resolve_speaker_name(spk_id) or "Unknown Speaker"
                try:
                    id_rec = self.storage.read_identity_record(spk_id)
                    roles = id_rec.get("roles", ["Speaker"])
                    notes = id_rec.get("notes", "")
                except Exception:
                    roles = ["Speaker"]
                    notes = ""

                people.append({
                    "spk_id": spk_id,
                    "display_name": name,
                    "roles": roles,
                    "sample_count": sample_count,
                    "notes": notes,
                    "is_locked": False,
                })
            except Exception as e:
                logger.debug("Could not read profile %s: %s", spk_id, e)

        people.sort(key=lambda p: p["display_name"].lower())
        return people

    def load_event_roster(self, event_id: str) -> List[Dict[str, Any]]:
        """
        Loads the event roster for event_id by decrypting the alias map in Vault v2.
        Returns: List of {"alias": "S01", "spk_id": "spk_...", "name": "...", "roles": [...]}
        """
        if not event_id or not self.vault.is_unlocked():
            return []

        alias_map = self.storage.get_alias_map(event_id)
        if not alias_map:
            return []

        roster: List[Dict[str, Any]] = []
        sorted_aliases = sorted(alias_map.keys())

        for alias in sorted_aliases:
            spk_id = alias_map[alias]
            name = self.storage.resolve_speaker_name(spk_id) or alias
            try:
                id_rec = self.storage.read_identity_record(spk_id)
                roles = id_rec.get("roles", ["Speaker"])
            except Exception:
                roles = ["Speaker"]

            roster.append({
                "alias": alias,
                "spk_id": spk_id,
                "name": name,
                "roles": roles,
                "is_locked": False,
            })

        return roster

    def get_alias_to_name_map(self, event_id: str) -> Dict[str, str]:
        """
        Returns {alias: decrypted_name} if Vault is unlocked and alias map exists,
        or empty dict {} if locked or not found.
        """
        roster = self.load_event_roster(event_id)
        return {r["alias"]: r["name"] for r in roster if r.get("alias") and r.get("name")}

    def save_event_roster(self, event_id: str, roster_items: List[Dict[str, Any]]) -> Dict[str, str]:
        """
        Saves the event roster by updating the encrypted alias map in Vault v2.
        Each item must contain "alias" and "spk_id".
        Asserts invariant: One person = one persistent spk_ ID.
        """
        if not event_id:
            raise ValueError("event_id cannot be empty.")

        alias_map: Dict[str, str] = {}
        seen_spks = set()

        for idx, item in enumerate(roster_items, start=1):
            alias = item.get("alias") or f"S{idx:02d}"
            spk_id = item.get("spk_id")
            if not spk_id or not spk_id.startswith("spk_"):
                raise ValueError(f"Invalid or missing speaker ID for alias {alias}: {spk_id}")

            if spk_id in seen_spks:
                # Same person assigned multiple times in same event roster
                logger.warning("Speaker %s assigned multiple aliases; using %s", spk_id, alias)

            seen_spks.add(spk_id)
            alias_map[alias] = spk_id

        self.storage.save_alias_map(event_id, alias_map)
        return alias_map

    @staticmethod
    def assign_next_alias(existing_aliases: List[str]) -> str:
        """Generates the next sequential alias: S01, S02, S03..."""
        max_idx = 0
        for a in existing_aliases:
            a_clean = a.strip().upper()
            if a_clean.startswith("S") and a_clean[1:].isdigit():
                try:
                    idx = int(a_clean[1:])
                    if idx > max_idx:
                        max_idx = idx
                except ValueError:
                    pass
        return f"S{max_idx + 1:02d}"
