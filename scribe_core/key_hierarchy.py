"""
scribe_core/key_hierarchy.py
Five-tier envelope key hierarchy and least-privilege key isolation for Vault v2.

Architecture:
1. User Passphrase -> Argon2id (t, m, p) -> K_argon
2. K_argon + DPAPI secret -> HKDF-SHA256 -> K_master_wrap
3. K_master_wrap -> AES-256-GCM -> K_master (256-bit random)
4. K_master -> AES-256-GCM -> K_zone_identity, K_zone_biometric, K_audit_mac
5. Zone Key -> AES-256-GCM (AAD: spk_id) -> K_person_id, K_person_bio

Least Privilege:
- Acoustic Matcher receives ONLY BiometricZoneKeyContext (no identity access).
- Late-Binding Resolver receives ONLY IdentityZoneKeyContext (no biometric access).
- Neither context can derive or reach the other zone's cryptographic keys.
"""

from __future__ import annotations

import json
import os
import secrets
import struct
from typing import Any, Dict, List, Optional, Tuple

from scribe_core.vault_crypto import (
    AES_GCM_NONCE_LEN,
    AES_GCM_TAG_LEN,
    AES_KEY_LEN,
    ARGON2_SALT_LEN,
    DEFAULT_ARGON2_MEMORY_COST_KIB,
    DEFAULT_ARGON2_PARALLELISM,
    DEFAULT_ARGON2_TIME_COST,
    FORMAT_VERSION_V2,
    MAGIC_HEADER_V2,
    MAGIC_RECORD_V2,
    AuthenticationError,
    LeastPrivilegeViolationError,
    TamperError,
    VaultCryptoError,
    aes_gcm_decrypt,
    aes_gcm_encrypt,
    atomic_write_bytes,
    best_effort_shred_file,
    compute_keyed_mac,
    derive_argon2id_key,
    dpapi_protect,
    dpapi_unprotect,
    generate_speaker_id,
    hkdf_derive,
    verify_keyed_mac,
    zero_buffer,
)


class IdentityZoneKeyContext:
    """
    Least-privilege cryptographic context for the Identity Zone.
    Can decrypt/encrypt ONLY speaker names, consent records, and per-event alias maps.
    Has ZERO access to acoustic embeddings, reference audio, or biometric zone keys.
    """

    def __init__(self, zone_key: bytes):
        if len(zone_key) != AES_KEY_LEN:
            raise ValueError(f"Zone key must be {AES_KEY_LEN} bytes.")
        self._zone_key = bytearray(zone_key)
        self._person_keys: Dict[str, bytearray] = {}

    def get_or_create_person_key(self, spk_id: str, wrapped_key_blob: Optional[bytes] = None) -> Tuple[bytes, bytes]:
        """
        Returns (person_key, wrapped_key_blob).
        If wrapped_key_blob is given, unwrap it; otherwise generate a fresh random key.
        """
        if spk_id in self._person_keys:
            key = bytes(self._person_keys[spk_id])
            wrapped = aes_gcm_encrypt(bytes(self._zone_key), key, aad=f"id_key:{spk_id}".encode("utf-8"))
            return key, wrapped

        if wrapped_key_blob:
            key = aes_gcm_decrypt(bytes(self._zone_key), wrapped_key_blob, aad=f"id_key:{spk_id}".encode("utf-8"))
            self._person_keys[spk_id] = bytearray(key)
            return key, wrapped_key_blob

        new_key = secrets.token_bytes(AES_KEY_LEN)
        self._person_keys[spk_id] = bytearray(new_key)
        wrapped = aes_gcm_encrypt(bytes(self._zone_key), new_key, aad=f"id_key:{spk_id}".encode("utf-8"))
        return new_key, wrapped

    def encrypt_identity_record(self, spk_id: str, plaintext: bytes) -> bytes:
        """Encrypts an identity record under the per-person identity key with AAD."""
        person_key, wrapped_key = self.get_or_create_person_key(spk_id)
        aad = f"identity:{spk_id}".encode("utf-8")
        ciphertext_blob = aes_gcm_encrypt(person_key, plaintext, aad=aad)

        # Container: MAGIC(4B) + VER(2B) + L_id(2B) + spk_id + L_wrap(2B) + wrapped_key + ciphertext_blob
        spk_bytes = spk_id.encode("utf-8")
        header = (
            MAGIC_RECORD_V2
            + struct.pack(">H", FORMAT_VERSION_V2)
            + struct.pack(">H", len(spk_bytes))
            + spk_bytes
            + struct.pack(">H", len(wrapped_key))
            + wrapped_key
        )
        return header + ciphertext_blob

    def decrypt_identity_record(self, spk_id: str, record_bytes: bytes) -> bytes:
        """Decrypts an identity record under the per-person identity key with AAD."""
        if not record_bytes.startswith(MAGIC_RECORD_V2):
            raise TamperError("Invalid record envelope magic.")

        offset = 4
        version = struct.unpack_from(">H", record_bytes, offset)[0]
        offset += 2
        if version != FORMAT_VERSION_V2:
            raise TamperError(f"Unsupported record version: {version}")

        l_id = struct.unpack_from(">H", record_bytes, offset)[0]
        offset += 2
        record_spk_id = record_bytes[offset : offset + l_id].decode("utf-8")
        offset += l_id

        if record_spk_id != spk_id:
            raise TamperError(f"Record ID mismatch: expected {spk_id}, found {record_spk_id} (swapped record attack).")

        l_wrap = struct.unpack_from(">H", record_bytes, offset)[0]
        offset += 2
        wrapped_key = record_bytes[offset : offset + l_wrap]
        offset += l_wrap

        ciphertext_blob = record_bytes[offset:]
        person_key, _ = self.get_or_create_person_key(spk_id, wrapped_key_blob=wrapped_key)

        aad = f"identity:{spk_id}".encode("utf-8")
        return aes_gcm_decrypt(person_key, ciphertext_blob, aad=aad)

    def encrypt_alias_map(self, event_id: str, alias_map: Dict[str, str]) -> bytes:
        """Encrypts the per-event alias map under the identity zone key."""
        plaintext = json.dumps(alias_map, indent=2).encode("utf-8")
        aad = f"alias_map:{event_id}".encode("utf-8")
        return aes_gcm_encrypt(bytes(self._zone_key), plaintext, aad=aad)

    def decrypt_alias_map(self, event_id: str, ciphertext_blob: bytes) -> Dict[str, str]:
        """Decrypts the per-event alias map under the identity zone key."""
        aad = f"alias_map:{event_id}".encode("utf-8")
        plaintext = aes_gcm_decrypt(bytes(self._zone_key), ciphertext_blob, aad=aad)
        return json.loads(plaintext.decode("utf-8"))

    def encrypt_index(self, index_data: Dict[str, Any]) -> bytes:
        """
        Encrypts the identity zone index concealing record counts and lengths
        by padding to a multiple of 4096 bytes.
        """
        raw = json.dumps(index_data).encode("utf-8")
        # Pad to bucket of 4096 bytes to conceal exact string counts
        target_len = ((len(raw) + 4095) // 4096) * 4096
        padding_needed = target_len - len(raw)
        # Store 4 bytes length prefix followed by raw + random padding
        padded = struct.pack(">I", len(raw)) + raw + secrets.token_bytes(padding_needed)
        return aes_gcm_encrypt(bytes(self._zone_key), padded, aad=b"zone_identity:index")

    def decrypt_index(self, ciphertext_blob: bytes) -> Dict[str, Any]:
        """Decrypts the identity zone index and strips size-concealing padding."""
        padded = aes_gcm_decrypt(bytes(self._zone_key), ciphertext_blob, aad=b"zone_identity:index")
        orig_len = struct.unpack_from(">I", padded, 0)[0]
        raw = padded[4 : 4 + orig_len]
        return json.loads(raw.decode("utf-8"))

    def clear(self) -> None:
        """Best-effort clearing of in-memory keys."""
        zero_buffer(self._zone_key)
        for k in self._person_keys.values():
            zero_buffer(k)
        self._person_keys.clear()


class BiometricZoneKeyContext:
    """
    Least-privilege cryptographic context for the Biometric Zone.
    Can decrypt/encrypt ONLY acoustic voice embeddings and reference WAV audio.
    Has ZERO access to speaker names, consent records, or identity zone keys.
    """

    def __init__(self, zone_key: bytes):
        if len(zone_key) != AES_KEY_LEN:
            raise ValueError(f"Zone key must be {AES_KEY_LEN} bytes.")
        self._zone_key = bytearray(zone_key)
        self._person_keys: Dict[str, bytearray] = {}

    def get_or_create_person_key(self, spk_id: str, wrapped_key_blob: Optional[bytes] = None) -> Tuple[bytes, bytes]:
        """
        Returns (person_key, wrapped_key_blob).
        If wrapped_key_blob is given, unwrap it; otherwise generate a fresh random key.
        """
        if spk_id in self._person_keys:
            key = bytes(self._person_keys[spk_id])
            wrapped = aes_gcm_encrypt(bytes(self._zone_key), key, aad=f"bio_key:{spk_id}".encode("utf-8"))
            return key, wrapped

        if wrapped_key_blob:
            key = aes_gcm_decrypt(bytes(self._zone_key), wrapped_key_blob, aad=f"bio_key:{spk_id}".encode("utf-8"))
            self._person_keys[spk_id] = bytearray(key)
            return key, wrapped_key_blob

        new_key = secrets.token_bytes(AES_KEY_LEN)
        self._person_keys[spk_id] = bytearray(new_key)
        wrapped = aes_gcm_encrypt(bytes(self._zone_key), new_key, aad=f"bio_key:{spk_id}".encode("utf-8"))
        return new_key, wrapped

    def encrypt_biometric_record(self, spk_id: str, embedding_bytes: bytes) -> bytes:
        """Encrypts an acoustic embedding vector under the per-person biometric key with AAD."""
        person_key, wrapped_key = self.get_or_create_person_key(spk_id)
        aad = f"biometric:{spk_id}".encode("utf-8")
        ciphertext_blob = aes_gcm_encrypt(person_key, embedding_bytes, aad=aad)

        spk_bytes = spk_id.encode("utf-8")
        header = (
            MAGIC_RECORD_V2
            + struct.pack(">H", FORMAT_VERSION_V2)
            + struct.pack(">H", len(spk_bytes))
            + spk_bytes
            + struct.pack(">H", len(wrapped_key))
            + wrapped_key
        )
        return header + ciphertext_blob

    def decrypt_biometric_record(self, spk_id: str, record_bytes: bytes) -> bytes:
        """Decrypts an acoustic embedding vector under the per-person biometric key with AAD."""
        if not record_bytes.startswith(MAGIC_RECORD_V2):
            raise TamperError("Invalid record envelope magic.")

        offset = 4
        version = struct.unpack_from(">H", record_bytes, offset)[0]
        offset += 2
        if version != FORMAT_VERSION_V2:
            raise TamperError(f"Unsupported record version: {version}")

        l_id = struct.unpack_from(">H", record_bytes, offset)[0]
        offset += 2
        record_spk_id = record_bytes[offset : offset + l_id].decode("utf-8")
        offset += l_id

        if record_spk_id != spk_id:
            raise TamperError(f"Record ID mismatch: expected {spk_id}, found {record_spk_id} (swapped record attack).")

        l_wrap = struct.unpack_from(">H", record_bytes, offset)[0]
        offset += 2
        wrapped_key = record_bytes[offset : offset + l_wrap]
        offset += l_wrap

        ciphertext_blob = record_bytes[offset:]
        person_key, _ = self.get_or_create_person_key(spk_id, wrapped_key_blob=wrapped_key)

        aad = f"biometric:{spk_id}".encode("utf-8")
        return aes_gcm_decrypt(person_key, ciphertext_blob, aad=aad)

    def encrypt_sample_audio(self, spk_id: str, sample_name: str, wav_bytes: bytes) -> bytes:
        """Encrypts a reference audio WAV under the per-person biometric key with AAD."""
        person_key, wrapped_key = self.get_or_create_person_key(spk_id)
        aad = f"sample:{spk_id}:{sample_name}".encode("utf-8")
        ciphertext_blob = aes_gcm_encrypt(person_key, wav_bytes, aad=aad)

        spk_bytes = spk_id.encode("utf-8")
        header = (
            MAGIC_RECORD_V2
            + struct.pack(">H", FORMAT_VERSION_V2)
            + struct.pack(">H", len(spk_bytes))
            + spk_bytes
            + struct.pack(">H", len(wrapped_key))
            + wrapped_key
        )
        return header + ciphertext_blob

    def decrypt_sample_audio(self, spk_id: str, sample_name: str, record_bytes: bytes) -> bytes:
        """Decrypts a reference audio WAV under the per-person biometric key with AAD."""
        if not record_bytes.startswith(MAGIC_RECORD_V2):
            raise TamperError("Invalid record envelope magic.")

        offset = 4
        version = struct.unpack_from(">H", record_bytes, offset)[0]
        offset += 2
        if version != FORMAT_VERSION_V2:
            raise TamperError(f"Unsupported record version: {version}")

        l_id = struct.unpack_from(">H", record_bytes, offset)[0]
        offset += 2
        record_spk_id = record_bytes[offset : offset + l_id].decode("utf-8")
        offset += l_id

        if record_spk_id != spk_id:
            raise TamperError(f"Record ID mismatch: expected {spk_id}, found {record_spk_id} (swapped record attack).")

        l_wrap = struct.unpack_from(">H", record_bytes, offset)[0]
        offset += 2
        wrapped_key = record_bytes[offset : offset + l_wrap]
        offset += l_wrap

        ciphertext_blob = record_bytes[offset:]
        person_key, _ = self.get_or_create_person_key(spk_id, wrapped_key_blob=wrapped_key)

        aad = f"sample:{spk_id}:{sample_name}".encode("utf-8")
        return aes_gcm_decrypt(person_key, ciphertext_blob, aad=aad)

    def encrypt_index(self, index_data: Dict[str, Any]) -> bytes:
        """
        Encrypts the biometric zone index concealing record counts and lengths
        by padding to a multiple of 4096 bytes.
        """
        raw = json.dumps(index_data).encode("utf-8")
        target_len = ((len(raw) + 4095) // 4096) * 4096
        padding_needed = target_len - len(raw)
        padded = struct.pack(">I", len(raw)) + raw + secrets.token_bytes(padding_needed)
        return aes_gcm_encrypt(bytes(self._zone_key), padded, aad=b"zone_biometric:index")

    def decrypt_index(self, ciphertext_blob: bytes) -> Dict[str, Any]:
        """Decrypts the biometric zone index and strips size-concealing padding."""
        padded = aes_gcm_decrypt(bytes(self._zone_key), ciphertext_blob, aad=b"zone_biometric:index")
        orig_len = struct.unpack_from(">I", padded, 0)[0]
        raw = padded[4 : 4 + orig_len]
        return json.loads(raw.decode("utf-8"))

    def clear(self) -> None:
        """Best-effort clearing of in-memory keys."""
        zero_buffer(self._zone_key)
        for k in self._person_keys.values():
            zero_buffer(k)
        self._person_keys.clear()


class KeyHierarchyManager:
    """
    Manages the 5-tier key hierarchy, master header creation/unpacking,
    and least-privilege context provisioning.
    """

    def __init__(self, vault_dir: str):
        self.vault_dir = os.path.abspath(vault_dir)
        self.header_path = os.path.join(self.vault_dir, "vault.hdr")
        self.identity_hdr_path = os.path.join(self.vault_dir, "zone_identity", "zone.hdr")
        self.biometric_hdr_path = os.path.join(self.vault_dir, "zone_biometric", "zone.hdr")

        self._master_key: Optional[bytearray] = None
        self._identity_context: Optional[IdentityZoneKeyContext] = None
        self._biometric_context: Optional[BiometricZoneKeyContext] = None
        self._audit_mac_key: Optional[bytearray] = None

    def is_initialized(self) -> bool:
        """True if master header and zone envelopes exist."""
        return (
            os.path.isfile(self.header_path)
            and os.path.isfile(self.identity_hdr_path)
            and os.path.isfile(self.biometric_hdr_path)
        )

    def is_unlocked(self) -> bool:
        """True if master key and zone contexts are currently in memory."""
        return self._master_key is not None and len(self._master_key) == AES_KEY_LEN

    def initialize_vault(
        self,
        passphrase: str,
        time_cost: int = DEFAULT_ARGON2_TIME_COST,
        memory_cost_kib: int = DEFAULT_ARGON2_MEMORY_COST_KIB,
        parallelism: int = DEFAULT_ARGON2_PARALLELISM,
    ) -> None:
        """
        Creates a new vault from scratch with a fresh master key, fresh zone keys,
        and serializes the master and zone headers.
        """
        os.makedirs(os.path.join(self.vault_dir, "zone_identity", "records"), exist_ok=True)
        os.makedirs(os.path.join(self.vault_dir, "zone_biometric", "records"), exist_ok=True)
        os.makedirs(os.path.join(self.vault_dir, "quarantine"), exist_ok=True)
        os.makedirs(os.path.join(self.vault_dir, "audit"), exist_ok=True)

        salt = secrets.token_bytes(ARGON2_SALT_LEN)
        k_argon = derive_argon2id_key(
            passphrase=passphrase,
            salt=salt,
            time_cost=time_cost,
            memory_cost_kib=memory_cost_kib,
            parallelism=parallelism,
        )

        dpapi_secret = secrets.token_bytes(AES_KEY_LEN)
        dpapi_blob = dpapi_protect(dpapi_secret)

        k_master_wrap = hkdf_derive(
            master_secret=k_argon + dpapi_secret,
            length=AES_KEY_LEN,
            salt=salt,
            info=b"session-scribe-master-wrap",
        )

        # Generate fresh 256-bit random master key
        raw_master = secrets.token_bytes(AES_KEY_LEN)
        wrapped_master = aes_gcm_encrypt(k_master_wrap, raw_master, aad=b"master_key")

        # Serialize master header:
        # MAGIC(4B) + VER(2B) + SALT(16B) + t(4B) + m(4B) + p(4B) + L_dpapi(4B) + dpapi_blob + wrapped_master
        hdr_bytes = (
            MAGIC_HEADER_V2
            + struct.pack(">H", FORMAT_VERSION_V2)
            + salt
            + struct.pack(">III", time_cost, memory_cost_kib, parallelism)
            + struct.pack(">I", len(dpapi_blob))
            + dpapi_blob
            + wrapped_master
        )
        atomic_write_bytes(self.header_path, hdr_bytes)

        # Generate fresh zone keys
        raw_zone_id = secrets.token_bytes(AES_KEY_LEN)
        raw_zone_bio = secrets.token_bytes(AES_KEY_LEN)

        wrapped_zone_id = aes_gcm_encrypt(raw_master, raw_zone_id, aad=b"zone_identity")
        wrapped_zone_bio = aes_gcm_encrypt(raw_master, raw_zone_bio, aad=b"zone_biometric")

        atomic_write_bytes(self.identity_hdr_path, wrapped_zone_id)
        atomic_write_bytes(self.biometric_hdr_path, wrapped_zone_bio)

        # Keep keys active in memory
        self._master_key = bytearray(raw_master)
        self._identity_context = IdentityZoneKeyContext(raw_zone_id)
        self._biometric_context = BiometricZoneKeyContext(raw_zone_bio)
        audit_mac = hkdf_derive(raw_master, length=AES_KEY_LEN, info=b"session-scribe-audit-mac")
        self._audit_mac_key = bytearray(audit_mac)

    def unlock_vault(self, passphrase: str) -> None:
        """
        Unlocks the vault: reads header parameters, derives K_argon, unwraps DPAPI,
        decrypts master key, and instantiates least-privilege zone contexts.
        """
        if not os.path.isfile(self.header_path):
            raise VaultCryptoError("Vault header not found; vault is uninitialized.")

        with open(self.header_path, "rb") as f:
            hdr_bytes = f.read()

        if not hdr_bytes.startswith(MAGIC_HEADER_V2):
            raise TamperError("Invalid vault header magic.")

        offset = 4
        version = struct.unpack_from(">H", hdr_bytes, offset)[0]
        offset += 2
        if version != FORMAT_VERSION_V2:
            raise TamperError(f"Unsupported vault version: {version}")

        salt = hdr_bytes[offset : offset + ARGON2_SALT_LEN]
        offset += ARGON2_SALT_LEN

        time_cost, memory_cost_kib, parallelism = struct.unpack_from(">III", hdr_bytes, offset)
        offset += 12

        l_dpapi = struct.unpack_from(">I", hdr_bytes, offset)[0]
        offset += 4

        dpapi_blob = hdr_bytes[offset : offset + l_dpapi]
        offset += l_dpapi

        wrapped_master = hdr_bytes[offset:]

        # Derive K_argon using exact stored parameters
        k_argon = derive_argon2id_key(
            passphrase=passphrase,
            salt=salt,
            time_cost=time_cost,
            memory_cost_kib=memory_cost_kib,
            parallelism=parallelism,
        )

        # Unwrap DPAPI secret
        dpapi_secret = dpapi_unprotect(dpapi_blob)

        # Derive master wrap key
        k_master_wrap = hkdf_derive(
            master_secret=k_argon + dpapi_secret,
            length=AES_KEY_LEN,
            salt=salt,
            info=b"session-scribe-master-wrap",
        )

        # Decrypt master key
        raw_master = aes_gcm_decrypt(k_master_wrap, wrapped_master, aad=b"master_key")

        # Unwrap zone keys
        with open(self.identity_hdr_path, "rb") as f:
            wrapped_zone_id = f.read()
        with open(self.biometric_hdr_path, "rb") as f:
            wrapped_zone_bio = f.read()

        raw_zone_id = aes_gcm_decrypt(raw_master, wrapped_zone_id, aad=b"zone_identity")
        raw_zone_bio = aes_gcm_decrypt(raw_master, wrapped_zone_bio, aad=b"zone_biometric")

        self._master_key = bytearray(raw_master)
        self._identity_context = IdentityZoneKeyContext(raw_zone_id)
        self._biometric_context = BiometricZoneKeyContext(raw_zone_bio)
        audit_mac = hkdf_derive(raw_master, length=AES_KEY_LEN, info=b"session-scribe-audit-mac")
        self._audit_mac_key = bytearray(audit_mac)

    def change_passphrase(
        self,
        old_passphrase: str,
        new_passphrase: str,
        time_cost: int = DEFAULT_ARGON2_TIME_COST,
        memory_cost_kib: int = DEFAULT_ARGON2_MEMORY_COST_KIB,
        parallelism: int = DEFAULT_ARGON2_PARALLELISM,
    ) -> None:
        """
        Re-wraps the master key with a new passphrase and fresh salt.
        Does NOT alter or re-encrypt zone keys or person records.
        """
        if not self.is_unlocked():
            self.unlock_vault(old_passphrase)

        current_master = bytes(self._master_key)  # type: ignore

        new_salt = secrets.token_bytes(ARGON2_SALT_LEN)
        new_k_argon = derive_argon2id_key(
            passphrase=new_passphrase,
            salt=new_salt,
            time_cost=time_cost,
            memory_cost_kib=memory_cost_kib,
            parallelism=parallelism,
        )

        new_dpapi_secret = secrets.token_bytes(AES_KEY_LEN)
        new_dpapi_blob = dpapi_protect(new_dpapi_secret)

        new_master_wrap = hkdf_derive(
            master_secret=new_k_argon + new_dpapi_secret,
            length=AES_KEY_LEN,
            salt=new_salt,
            info=b"session-scribe-master-wrap",
        )

        new_wrapped_master = aes_gcm_encrypt(new_master_wrap, current_master, aad=b"master_key")

        new_hdr_bytes = (
            MAGIC_HEADER_V2
            + struct.pack(">H", FORMAT_VERSION_V2)
            + new_salt
            + struct.pack(">III", time_cost, memory_cost_kib, parallelism)
            + struct.pack(">I", len(new_dpapi_blob))
            + new_dpapi_blob
            + new_wrapped_master
        )
        atomic_write_bytes(self.header_path, new_hdr_bytes)

    def lock_vault(self) -> None:
        """Wipes master key and least-privilege contexts from memory."""
        if self._identity_context:
            self._identity_context.clear()
            self._identity_context = None

        if self._biometric_context:
            self._biometric_context.clear()
            self._biometric_context = None

        if self._audit_mac_key:
            zero_buffer(self._audit_mac_key)
            self._audit_mac_key = None

        if self._master_key:
            zero_buffer(self._master_key)
            self._master_key = None

    def get_identity_context(self) -> IdentityZoneKeyContext:
        """Returns the IdentityZoneKeyContext. Requires unlocked vault."""
        if not self.is_unlocked() or self._identity_context is None:
            raise PermissionError("Vault is locked. Identity context cannot be accessed.")
        return self._identity_context

    def get_biometric_context(self) -> BiometricZoneKeyContext:
        """Returns the BiometricZoneKeyContext. Requires unlocked vault."""
        if not self.is_unlocked() or self._biometric_context is None:
            raise PermissionError("Vault is locked. Biometric context cannot be accessed.")
        return self._biometric_context

    def get_audit_mac_key(self) -> bytes:
        """Returns the audit HMAC key. Requires unlocked vault."""
        if not self.is_unlocked() or self._audit_mac_key is None:
            raise PermissionError("Vault is locked. Audit key cannot be accessed.")
        return bytes(self._audit_mac_key)

    def get_master_key_bytes(self) -> bytes:
        """Returns raw master key bytes. For backward compatibility / testing only."""
        if not self.is_unlocked() or self._master_key is None:
            raise PermissionError("Vault is locked. Master key cannot be accessed.")
        return bytes(self._master_key)
