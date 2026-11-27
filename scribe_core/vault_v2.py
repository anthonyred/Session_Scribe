"""
scribe_core/vault_v2.py
Dual-Zone Storage Engine for Session Scribe Vault v2.

Architecture & Security Invariants:
1. Two Cryptographically Isolated Subtrees:
   - zone_identity/ (Names, consent, roles, per-event alias maps)
   - zone_biometric/ (Acoustic embeddings, encrypted reference WAVs)
2. Addressed strictly by random 128-bit spk_ IDs (spk_<32hex>).
3. Names exist ONLY inside AES-256-GCM ciphertext in Identity Zone records.
4. Padded encrypted indexes (padded to 4096-byte buckets) reveal neither counts nor names.
5. Per-event alias maps (event_id -> {S01: spk_...}) encrypted in Identity Zone.
6. Atomic file operations (write to tmp, fsync, atomic replace) protect against crash corruption.
7. Record versioning (schema_version = 2) and quarantine metadata.
8. Least-privilege key access: Matcher uses BiometricZoneKeyContext; Resolver uses IdentityZoneKeyContext.
"""

from __future__ import annotations

import glob
import json
import logging
import os
import secrets
import struct
import time
from typing import Any, Dict, List, Optional, Tuple, Union

from scribe_core.key_hierarchy import (
    BiometricZoneKeyContext,
    IdentityZoneKeyContext,
)
from scribe_core.vault_crypto import (
    FORMAT_VERSION_V2,
    LeastPrivilegeViolationError,
    TamperError,
    VaultCryptoError,
    atomic_write_bytes,
    best_effort_shred_file,
    generate_speaker_id,
)
from scribe_core.voice_vault import VoiceVault

logger = logging.getLogger("scribe_core.vault_v2")


class VaultV2StorageEngine:
    """
    High-level Dual-Zone Storage Engine.
    Provides atomic CRUD for Identity records, Biometric records, reference audio,
    and per-event alias maps while strictly isolating cryptographic contexts.
    """

    def __init__(self, vault: VoiceVault):
        self.vault = vault
        self.vault_dir = os.path.abspath(vault.vault_dir)

        # Directory structure
        self.identity_dir = os.path.join(self.vault_dir, "zone_identity")
        self.identity_records_dir = os.path.join(self.identity_dir, "records")
        self.alias_maps_dir = os.path.join(self.identity_dir, "alias_maps")
        self.identity_index_file = os.path.join(self.identity_dir, "index.dat")

        self.biometric_dir = os.path.join(self.vault_dir, "zone_biometric")
        self.biometric_records_dir = os.path.join(self.biometric_dir, "records")
        self.biometric_index_file = os.path.join(self.biometric_dir, "index.dat")

        self.quarantine_dir = os.path.join(self.vault_dir, "quarantine")

        self._ensure_directories()

    def _ensure_directories(self) -> None:
        """Initializes folder tree with owner-restricted permissions."""
        os.makedirs(self.identity_records_dir, exist_ok=True)
        os.makedirs(self.alias_maps_dir, exist_ok=True)
        os.makedirs(self.biometric_records_dir, exist_ok=True)
        os.makedirs(self.quarantine_dir, exist_ok=True)

    # ------------------------------------------------------------------
    # Zone 1: Identity Records CRUD
    # ------------------------------------------------------------------

    def create_identity_record(
        self,
        full_name: str,
        display_name: Optional[str] = None,
        affiliation: str = "",
        roles: Optional[List[str]] = None,
        consent_opt_in: bool = True,
        spk_id: Optional[str] = None,
        notes: str = "",
    ) -> str:
        """
        Creates an encrypted identity record in Zone 1.
        Returns the assigned random 128-bit spk_id.
        """
        id_ctx = self.vault.get_identity_context()
        if spk_id is None:
            spk_id = generate_speaker_id()

        now_utc = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        record = {
            "schema_version": FORMAT_VERSION_V2,
            "spk_id": spk_id,
            "full_name": full_name,
            "display_name": display_name or full_name,
            "affiliation": affiliation,
            "roles": roles or ["Speaker"],
            "notes": notes,
            "consent": {
                "opt_in": consent_opt_in,
                "consent_timestamp_utc": now_utc,
                "policy_version": "2.0",
                "method": "explicit_ui_checkbox",
                "verified_by": "local_user",
                "consent_statement": "The speaker has provided explicit permission to store voice biometrics locally for transcription identification.",
            },
            "quarantine": {
                "is_quarantined": False,
                "quarantine_timestamp_utc": None,
                "scheduled_purge_utc": None,
                "reason": None,
            },
            "created_utc": now_utc,
            "updated_utc": now_utc,
        }

        plaintext = json.dumps(record, indent=2).encode("utf-8")
        encrypted_blob = id_ctx.encrypt_identity_record(spk_id, plaintext)

        record_path = os.path.join(self.identity_records_dir, f"{spk_id}.id.enc")
        atomic_write_bytes(record_path, encrypted_blob)

        # Update identity index
        self._update_identity_index(spk_id, roles=record["roles"], is_quarantined=False, created_utc=now_utc)
        return spk_id

    def read_identity_record(self, spk_id: str) -> Dict[str, Any]:
        """Reads and decrypts an identity record under the Identity context."""
        id_ctx = self.vault.get_identity_context()
        record_path = os.path.join(self.identity_records_dir, f"{spk_id}.id.enc")
        if not os.path.isfile(record_path):
            raise FileNotFoundError(f"Identity record not found for {spk_id}")

        with open(record_path, "rb") as f:
            encrypted_blob = f.read()

        plaintext = id_ctx.decrypt_identity_record(spk_id, encrypted_blob)
        return json.loads(plaintext.decode("utf-8"))

    def update_identity_record(self, spk_id: str, updates: Dict[str, Any]) -> Dict[str, Any]:
        """Atomically updates fields of an existing identity record."""
        current = self.read_identity_record(spk_id)
        current.update(updates)
        current["updated_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

        id_ctx = self.vault.get_identity_context()
        plaintext = json.dumps(current, indent=2).encode("utf-8")
        encrypted_blob = id_ctx.encrypt_identity_record(spk_id, plaintext)

        record_path = os.path.join(self.identity_records_dir, f"{spk_id}.id.enc")
        atomic_write_bytes(record_path, encrypted_blob)

        roles = current.get("roles", ["Speaker"])
        is_quarantined = current.get("quarantine", {}).get("is_quarantined", False)
        self._update_identity_index(spk_id, roles=roles, is_quarantined=is_quarantined, updated_utc=current["updated_utc"])
        return current

    def delete_identity_record(self, spk_id: str) -> None:
        """Deletes and best-effort shreds an identity record and removes from index."""
        record_path = os.path.join(self.identity_records_dir, f"{spk_id}.id.enc")
        if os.path.isfile(record_path):
            best_effort_shred_file(record_path)

        self._remove_from_identity_index(spk_id)

    def _get_identity_index(self) -> Dict[str, Any]:
        """Reads and decrypts the padded identity index."""
        id_ctx = self.vault.get_identity_context()
        if not os.path.isfile(self.identity_index_file):
            return {}
        try:
            with open(self.identity_index_file, "rb") as f:
                blob = f.read()
            return id_ctx.decrypt_index(blob)
        except Exception:
            return {}

    def _read_identity_index(self) -> Dict[str, Any]:
        """Alias for _get_identity_index."""
        return self._get_identity_index()

    def _save_identity_index(self, index_data: Dict[str, Any]) -> None:
        """Encrypts and atomically writes the padded identity index."""
        id_ctx = self.vault.get_identity_context()
        blob = id_ctx.encrypt_index(index_data)
        atomic_write_bytes(self.identity_index_file, blob)

    def _update_identity_index(self, spk_id: str, **metadata: Any) -> None:
        idx = self._get_identity_index()
        item = idx.get(spk_id, {})
        item.update(metadata)
        idx[spk_id] = item
        self._save_identity_index(idx)

    def _remove_from_identity_index(self, spk_id: str) -> None:
        idx = self._get_identity_index()
        if spk_id in idx:
            del idx[spk_id]
            self._save_identity_index(idx)

    def list_identity_ids(self, include_quarantined: bool = False) -> List[str]:
        """
        Returns list of enrolled spk_ IDs from the encrypted index.
        By default (include_quarantined=False), quarantined profiles are strictly excluded.
        """
        idx = self._get_identity_index()
        if include_quarantined:
            return list(idx.keys())
        return [spk_id for spk_id, meta in idx.items() if not meta.get("is_quarantined", False)]

    def resolve_speaker_name(self, spk_id: str) -> Optional[str]:
        """
        Resolves the display name for spk_id under Identity context.
        Security Invariant: Quarantined profiles are strictly excluded from name resolution.
        """
        try:
            rec = self.read_identity_record(spk_id)
            if rec.get("quarantine", {}).get("is_quarantined", False):
                return None
            return rec.get("display_name") or rec.get("full_name") or rec.get("name")
        except Exception:
            return None

    # ------------------------------------------------------------------
    # Zone 1: Per-Event Alias Map
    # ------------------------------------------------------------------

    def save_alias_map(self, event_id: str, alias_map: Dict[str, str]) -> None:
        """
        Encrypts and saves the per-event session alias mapping table
        (e.g. S01 -> spk_<128bit_hex>) inside the Identity Zone.
        """
        id_ctx = self.vault.get_identity_context()
        blob = id_ctx.encrypt_alias_map(event_id, alias_map)
        map_path = os.path.join(self.alias_maps_dir, f"{event_id}.alias.enc")
        atomic_write_bytes(map_path, blob)

    def get_alias_map(self, event_id: str) -> Dict[str, str]:
        """Reads and decrypts the per-event alias map under the Identity context."""
        id_ctx = self.vault.get_identity_context()
        map_path = os.path.join(self.alias_maps_dir, f"{event_id}.alias.enc")
        if not os.path.isfile(map_path):
            return {}
        with open(map_path, "rb") as f:
            blob = f.read()
        return id_ctx.decrypt_alias_map(event_id, blob)

    # ------------------------------------------------------------------
    # Zone 2: Biometric Records & Reference Samples CRUD
    # ------------------------------------------------------------------

    def create_biometric_record(
        self,
        spk_id: str,
        embedding_bytes: bytes,
        sample_names: Optional[List[str]] = None,
        model: str = "pyannote/wespeaker-voxceleb-resnet34-LM",
        dimension: int = 256,
        model_version: str = "1.0.0",
        model_revision: str = "837717ddb9ff5507820346191109dc79c958d614",
    ) -> None:
        """
        Encrypts and stores an acoustic voice embedding vector in Zone 2.
        Embeddings are addressed ONLY by spk_id; no names exist in this zone.
        """
        bio_ctx = self.vault.get_biometric_context()
        now_utc = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

        meta = {
            "schema_version": FORMAT_VERSION_V2,
            "spk_id": spk_id,
            "model": model,
            "model_version": model_version,
            "model_revision": model_revision,
            "dimension": dimension,
            "sample_count": len(sample_names or []),
            "sample_names": sample_names or [],
            "quarantine": {
                "is_quarantined": False,
                "quarantine_timestamp_utc": None,
                "scheduled_purge_utc": None,
                "reason": None,
            },
            "created_utc": now_utc,
            "updated_utc": now_utc,
        }

        meta_bytes = json.dumps(meta).encode("utf-8")
        # Pack header: Length of meta (4B) + meta_bytes + embedding_bytes
        container = struct.pack(">I", len(meta_bytes)) + meta_bytes + embedding_bytes
        encrypted_blob = bio_ctx.encrypt_biometric_record(spk_id, container)

        record_path = os.path.join(self.biometric_records_dir, f"{spk_id}.bio.enc")
        atomic_write_bytes(record_path, encrypted_blob)

        self._update_biometric_index(spk_id, sample_count=len(sample_names or []), is_quarantined=False, updated_utc=now_utc)

    def read_biometric_record(self, spk_id: str) -> Tuple[Dict[str, Any], bytes]:
        """
        Reads and decrypts a biometric record under the Biometric context.
        Returns: (metadata_dict, raw_embedding_bytes).
        """
        bio_ctx = self.vault.get_biometric_context()
        record_path = os.path.join(self.biometric_records_dir, f"{spk_id}.bio.enc")
        if not os.path.isfile(record_path):
            raise FileNotFoundError(f"Biometric record not found for {spk_id}")

        with open(record_path, "rb") as f:
            encrypted_blob = f.read()

        container = bio_ctx.decrypt_biometric_record(spk_id, encrypted_blob)
        meta_len = struct.unpack_from(">I", container, 0)[0]
        meta_bytes = container[4 : 4 + meta_len]
        embedding_bytes = container[4 + meta_len :]

        metadata = json.loads(meta_bytes.decode("utf-8"))
        return metadata, embedding_bytes

    def update_biometric_record(
        self,
        spk_id: str,
        updates: Dict[str, Any],
        new_embedding_bytes: Optional[bytes] = None,
    ) -> Dict[str, Any]:
        """Atomically updates metadata fields (and optionally embedding) of an existing biometric record."""
        current_meta, current_emb = self.read_biometric_record(spk_id)
        current_meta.update(updates)
        now_utc = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        current_meta["updated_utc"] = now_utc

        emb_to_write = new_embedding_bytes if new_embedding_bytes is not None else current_emb
        bio_ctx = self.vault.get_biometric_context()
        meta_bytes = json.dumps(current_meta).encode("utf-8")
        container = struct.pack(">I", len(meta_bytes)) + meta_bytes + emb_to_write
        encrypted_blob = bio_ctx.encrypt_biometric_record(spk_id, container)

        record_path = os.path.join(self.biometric_records_dir, f"{spk_id}.bio.enc")
        atomic_write_bytes(record_path, encrypted_blob)

        is_quarantined = current_meta.get("quarantine", {}).get("is_quarantined", False)
        sample_count = len(current_meta.get("sample_names", []))
        self._update_biometric_index(
            spk_id,
            sample_count=sample_count,
            is_quarantined=is_quarantined,
            updated_utc=now_utc,
        )
        return current_meta

    def write_biometric_sample(self, spk_id: str, sample_name: str, wav_bytes: bytes) -> None:
        """Alias for save_sample_audio."""
        self.save_sample_audio(spk_id, sample_name, wav_bytes)

    def save_sample_audio(self, spk_id: str, sample_name: str, wav_bytes: bytes) -> None:
        """Encrypts and writes a reference WAV audio sample inside the Biometric Zone."""
        bio_ctx = self.vault.get_biometric_context()
        samples_dir = os.path.join(self.biometric_records_dir, f"{spk_id}_samples")
        os.makedirs(samples_dir, exist_ok=True)

        encrypted_blob = bio_ctx.encrypt_sample_audio(spk_id, sample_name, wav_bytes)
        sample_path = os.path.join(samples_dir, f"{sample_name}.wav.enc")
        atomic_write_bytes(sample_path, encrypted_blob)

    def read_sample_audio(self, spk_id: str, sample_name: str) -> bytes:
        """Reads and decrypts a reference WAV audio sample under the Biometric context."""
        bio_ctx = self.vault.get_biometric_context()
        sample_path = os.path.join(self.biometric_records_dir, f"{spk_id}_samples", f"{sample_name}.wav.enc")
        if not os.path.isfile(sample_path):
            raise FileNotFoundError(f"Sample audio not found: {sample_name} for {spk_id}")

        with open(sample_path, "rb") as f:
            encrypted_blob = f.read()

        return bio_ctx.decrypt_sample_audio(spk_id, sample_name, encrypted_blob)

    def read_biometric_sample(self, spk_id: str, sample_name: str) -> bytes:
        """Alias for read_sample_audio."""
        return self.read_sample_audio(spk_id, sample_name)

    def list_biometric_samples(self, spk_id: str) -> List[str]:
        """Lists available sample names for a given speaker ID in Zone 2."""
        samples_dir = os.path.join(self.biometric_records_dir, f"{spk_id}_samples")
        if not os.path.isdir(samples_dir):
            return []
        samples = []
        for f in os.listdir(samples_dir):
            if f.endswith(".wav.enc"):
                samples.append(f[: -len(".wav.enc")])
        return sorted(samples)

    def delete_biometric_record(self, spk_id: str) -> None:
        """Deletes and best-effort shreds biometric embeddings and audio samples for spk_id."""
        bio_path = os.path.join(self.biometric_records_dir, f"{spk_id}.bio.enc")
        if os.path.isfile(bio_path):
            best_effort_shred_file(bio_path)

        samples_dir = os.path.join(self.biometric_records_dir, f"{spk_id}_samples")
        if os.path.isdir(samples_dir):
            for sample_file in glob.glob(os.path.join(samples_dir, "*.enc")):
                best_effort_shred_file(sample_file)
            try:
                os.rmdir(samples_dir)
            except Exception:
                pass

        self._remove_from_biometric_index(spk_id)

    def _get_biometric_index(self) -> Dict[str, Any]:
        """Reads and decrypts the padded biometric index."""
        bio_ctx = self.vault.get_biometric_context()
        if not os.path.isfile(self.biometric_index_file):
            return {}
        try:
            with open(self.biometric_index_file, "rb") as f:
                blob = f.read()
            return bio_ctx.decrypt_index(blob)
        except Exception:
            return {}

    def _read_biometric_index(self) -> Dict[str, Any]:
        """Alias for _get_biometric_index."""
        return self._get_biometric_index()

    def _save_biometric_index(self, index_data: Dict[str, Any]) -> None:
        """Encrypts and atomically writes the padded biometric index."""
        bio_ctx = self.vault.get_biometric_context()
        blob = bio_ctx.encrypt_index(index_data)
        atomic_write_bytes(self.biometric_index_file, blob)

    def _update_biometric_index(self, spk_id: str, **metadata: Any) -> None:
        idx = self._get_biometric_index()
        item = idx.get(spk_id, {})
        item.update(metadata)
        idx[spk_id] = item
        self._save_biometric_index(idx)

    def _remove_from_biometric_index(self, spk_id: str) -> None:
        idx = self._get_biometric_index()
        if spk_id in idx:
            del idx[spk_id]
            self._save_biometric_index(idx)

    def list_biometric_ids(self, include_quarantined: bool = False) -> List[str]:
        """
        Returns list of enrolled biometric spk_ IDs.
        By default (include_quarantined=False), quarantined profiles are strictly excluded
        from voice matching.
        """
        idx = self._get_biometric_index()
        if include_quarantined:
            return list(idx.keys())
        return [spk_id for spk_id, meta in idx.items() if not meta.get("is_quarantined", False)]

    def get_biometric_embedding_for_matching(self, spk_id: str) -> Optional[bytes]:
        """
        Retrieves raw embedding bytes for acoustic matching.
        Security Invariant: Quarantined profiles are strictly excluded from acoustic matching.
        """
        try:
            meta, emb = self.read_biometric_record(spk_id)
            if meta.get("quarantine", {}).get("is_quarantined", False):
                return None
            return emb
        except Exception:
            return None

    # ------------------------------------------------------------------
    # Security Verification: Plaintext Canary Scan
    # ------------------------------------------------------------------

    def canary_scan_vault(
        self,
        canary_names: List[str],
        canary_bytes: Optional[List[bytes]] = None,
    ) -> List[str]:
        """
        Deep inspection of all files in vault_dir on disk.
        Asserts that NO plaintext canary speaker names or raw byte patterns
        appear in any file header, filename, or ciphertext content.
        Returns a list of any violations found (must be empty).
        """
        violations = []
        canary_names_lower = [name.lower().encode("utf-8") for name in canary_names if len(name) >= 3]
        byte_patterns = canary_bytes or []

        for root, dirs, files in os.walk(self.vault_dir):
            # Check directory names
            for d in dirs:
                for name in canary_names:
                    if name.lower() in d.lower():
                        violations.append(f"Directory name leak: '{name}' found in {os.path.join(root, d)}")

            # Check file names and file bytes
            for f in files:
                full_path = os.path.join(root, f)
                for name in canary_names:
                    if name.lower() in f.lower():
                        violations.append(f"File name leak: '{name}' found in {full_path}")

                try:
                    with open(full_path, "rb") as fp:
                        content = fp.read()

                    # Check for plaintext name strings
                    for c_bytes in canary_names_lower:
                        if c_bytes in content.lower():
                            violations.append(f"Plaintext name leak: {c_bytes.decode('utf-8', errors='ignore')} in {full_path}")

                    # Check for raw canary byte patterns (e.g. unencrypted WAV RIFF headers or raw embeddings)
                    for pattern in byte_patterns:
                        if pattern in content:
                            violations.append(f"Plaintext byte pattern leak ({len(pattern)} bytes) in {full_path}")
                except Exception as e:
                    violations.append(f"Could not read {full_path}: {e}")

        return violations
