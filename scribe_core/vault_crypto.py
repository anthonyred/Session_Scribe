"""
scribe_core/vault_crypto.py
Cryptographic primitives for Session Scribe Vault v2.

Primitives & Specifications:
- Key Derivation: Argon2id via argon2-cffi (RFC 9106, calibrated to ~0.5-1.0s, off-UI thread).
- Hardware/User Binding: Windows DPAPI (CryptProtectData / CryptUnprotectData).
- Authenticated Encryption: AES-256-GCM via cryptography with unique 96-bit nonces.
- Additional Authenticated Data (AAD): Record IDs and zone scopes bound into every envelope.
- Keyed MAC: HMAC-SHA256 for audit logging and chain integrity.
- Key Derivation Expansion: HKDF-SHA256.
- Best-effort buffer zeroing for in-memory key disposal.
- Atomic file replacement and best-effort disk overwriting.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import gc
import hashlib
import hmac
import os
import re
import secrets
import struct
import sys
import time
from typing import Any, Dict, List, Optional, Tuple, Union

try:
    import argon2.low_level as argon2_ll
except ImportError:
    argon2_ll = None  # type: ignore

try:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF
    from cryptography.hazmat.primitives import hashes
    from cryptography.exceptions import InvalidTag
except ImportError:
    AESGCM = None  # type: ignore
    HKDF = None  # type: ignore
    hashes = None  # type: ignore
    InvalidTag = Exception  # type: ignore

try:
    import zxcvbn
except ImportError:
    zxcvbn = None  # type: ignore

from scribe_core.wordlist import generate_memorable_passphrase


# ----------------------------------------------------------------------
# Exceptions
# ----------------------------------------------------------------------

class VaultCryptoError(Exception):
    """Base error for vault cryptographic operations."""
    pass


class TamperError(VaultCryptoError):
    """Raised when ciphertext, tag, AAD, or envelope fails authentication."""
    pass


class AuthenticationError(VaultCryptoError):
    """Raised when passphrase derivation or DPAPI unwrapping fails."""
    pass


class LeastPrivilegeViolationError(VaultCryptoError):
    """Raised when a zone context attempts to access an unauthorized key or record."""
    pass


# ----------------------------------------------------------------------
# Cryptographic Constants
# ----------------------------------------------------------------------

MAGIC_HEADER_V2 = b"SSV2"
MAGIC_RECORD_V2 = b"SSRE"
FORMAT_VERSION_V2 = 2

# Calibrated to ~0.5-1.0s with 256 MiB memory on target host machine
DEFAULT_ARGON2_TIME_COST = 10
DEFAULT_ARGON2_MEMORY_COST_KIB = 262144  # 256 MiB
DEFAULT_ARGON2_PARALLELISM = 4
ARGON2_SALT_LEN = 16

AES_KEY_LEN = 32        # 256-bit symmetric keys
AES_GCM_NONCE_LEN = 12  # 96-bit IV
AES_GCM_TAG_LEN = 16    # 128-bit authentication tag

VAULT_APP_ENTROPY = b"SessionScribe_VoiceVault_AppEntropy_v2026"
MIN_PASSPHRASE_LENGTH = 14
MIN_PASSPHRASE_WORDS = 5


# ----------------------------------------------------------------------
# Argon2id Key Derivation
# ----------------------------------------------------------------------

def derive_argon2id_key(
    passphrase: Union[str, bytes],
    salt: bytes,
    time_cost: int = DEFAULT_ARGON2_TIME_COST,
    memory_cost_kib: int = DEFAULT_ARGON2_MEMORY_COST_KIB,
    parallelism: int = DEFAULT_ARGON2_PARALLELISM,
    hash_len: int = AES_KEY_LEN,
) -> bytes:
    """
    Derives a symmetric key from passphrase and salt using Argon2id via argon2-cffi.
    Calibrated for ~0.5-1.0s derivation time with 256 MiB RAM.
    Must be executed in a worker thread off the UI loop.
    """
    if argon2_ll is None:
        raise VaultCryptoError("argon2-cffi is not installed or available.")

    if isinstance(passphrase, str):
        pw_bytes = passphrase.encode("utf-8")
    else:
        pw_bytes = bytes(passphrase)

    if len(salt) < ARGON2_SALT_LEN:
        raise ValueError(f"Salt must be at least {ARGON2_SALT_LEN} bytes.")

    return argon2_ll.hash_secret_raw(
        secret=pw_bytes,
        salt=salt,
        time_cost=time_cost,
        memory_cost=memory_cost_kib,
        parallelism=parallelism,
        hash_len=hash_len,
        type=argon2_ll.Type.ID,
    )


# ----------------------------------------------------------------------
# Windows DPAPI Hardware & User Binding
# ----------------------------------------------------------------------

class DATA_BLOB(ctypes.Structure):
    _fields_ = [
        ("cbData", wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_byte)),
    ]


def dpapi_protect(data: bytes, entropy: bytes = VAULT_APP_ENTROPY) -> bytes:
    """
    Encrypt data using Windows DPAPI (tied to current Windows user and hardware TPM/SAM).
    CRYPTPROTECT_UI_FORBIDDEN (0x1) ensures no UI prompts.
    """
    if sys.platform != "win32":
        # Non-Windows test mock fallback
        return b"MOCK_WIN_DPAPI:" + data

    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32

    in_bytes = (ctypes.c_byte * len(data))(*data)
    in_blob = DATA_BLOB(len(data), in_bytes)

    ent_bytes = (ctypes.c_byte * len(entropy))(*entropy)
    ent_blob = DATA_BLOB(len(entropy), ent_bytes)

    out_blob = DATA_BLOB()
    # CRYPTPROTECT_UI_FORBIDDEN = 0x1
    res = crypt32.CryptProtectData(
        ctypes.byref(in_blob),
        "SessionScribeVaultMaster",
        ctypes.byref(ent_blob),
        None,
        None,
        0x1,
        ctypes.byref(out_blob),
    )
    if not res:
        raise OSError("CryptProtectData failed to protect vault secret.")

    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        kernel32.LocalFree(out_blob.pbData)


def dpapi_unprotect(data: bytes, entropy: bytes = VAULT_APP_ENTROPY) -> bytes:
    """
    Decrypt data using Windows DPAPI. Requires the original Windows user and machine.
    """
    if sys.platform != "win32":
        if data.startswith(b"MOCK_WIN_DPAPI:"):
            return data[len(b"MOCK_WIN_DPAPI:"):]
        return data

    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32

    in_bytes = (ctypes.c_byte * len(data))(*data)
    in_blob = DATA_BLOB(len(data), in_bytes)

    ent_bytes = (ctypes.c_byte * len(entropy))(*entropy)
    ent_blob = DATA_BLOB(len(entropy), ent_bytes)

    out_blob = DATA_BLOB()
    res = crypt32.CryptUnprotectData(
        ctypes.byref(in_blob),
        None,
        ctypes.byref(ent_blob),
        None,
        None,
        0x1,
        ctypes.byref(out_blob),
    )
    if not res:
        raise AuthenticationError(
            "CryptUnprotectData failed. The vault is tied to this Windows user and machine; "
            "it cannot be opened on another PC or Windows user account."
        )

    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        kernel32.LocalFree(out_blob.pbData)


# ----------------------------------------------------------------------
# AES-256-GCM Authenticated Encryption with AAD
# ----------------------------------------------------------------------

def aes_gcm_encrypt(
    key: bytes,
    plaintext: bytes,
    aad: Optional[bytes] = None,
    nonce: Optional[bytes] = None,
) -> bytes:
    """
    Encrypts plaintext using AES-256-GCM.
    Returns: nonce (12B) + ciphertext + tag (16B).
    Guarantees unique random 96-bit nonce unless explicitly passed for testing.
    """
    if AESGCM is None:
        raise VaultCryptoError("cryptography package is not installed.")

    if len(key) != AES_KEY_LEN:
        raise ValueError(f"AES key must be {AES_KEY_LEN} bytes (256 bits).")

    if nonce is None:
        nonce = secrets.token_bytes(AES_GCM_NONCE_LEN)
    elif len(nonce) != AES_GCM_NONCE_LEN:
        raise ValueError(f"Nonce must be {AES_GCM_NONCE_LEN} bytes.")

    cipher = AESGCM(key)
    ciphertext_and_tag = cipher.encrypt(nonce, plaintext, aad)
    return nonce + ciphertext_and_tag


def aes_gcm_decrypt(
    key: bytes,
    encrypted_blob: bytes,
    aad: Optional[bytes] = None,
) -> bytes:
    """
    Decrypts and authenticates AES-256-GCM blob.
    Blob format: nonce (12B) + ciphertext + tag (16B).
    Raises TamperError if ciphertext, tag, or AAD is modified or truncated.
    """
    if AESGCM is None:
        raise VaultCryptoError("cryptography package is not installed.")

    min_len = AES_GCM_NONCE_LEN + AES_GCM_TAG_LEN
    if len(encrypted_blob) < min_len:
        raise TamperError(f"Truncated encrypted blob (length {len(encrypted_blob)} < {min_len}).")

    if len(key) != AES_KEY_LEN:
        raise ValueError(f"AES key must be {AES_KEY_LEN} bytes (256 bits).")

    nonce = encrypted_blob[:AES_GCM_NONCE_LEN]
    ciphertext_and_tag = encrypted_blob[AES_GCM_NONCE_LEN:]

    cipher = AESGCM(key)
    try:
        plaintext = cipher.decrypt(nonce, ciphertext_and_tag, aad)
        return plaintext
    except InvalidTag as e:
        raise TamperError("AES-GCM authentication verification failed: ciphertext, tag, or AAD has been tampered with.") from e


# ----------------------------------------------------------------------
# Key Derivation Expansion (HKDF-SHA256) & Keyed MAC
# ----------------------------------------------------------------------

def hkdf_derive(
    master_secret: bytes,
    length: int,
    salt: Optional[bytes] = None,
    info: bytes = b"",
) -> bytes:
    """Expands a master secret into subkeys using HKDF-SHA256."""
    if HKDF is None:
        raise VaultCryptoError("cryptography package is not installed.")

    hkdf = HKDF(
        algorithm=hashes.SHA256(),
        length=length,
        salt=salt,
        info=info,
    )
    return hkdf.derive(master_secret)


def compute_keyed_mac(key: bytes, message: bytes) -> bytes:
    """Computes an HMAC-SHA256 keyed message authentication code."""
    return hmac.new(key, message, hashlib.sha256).digest()


def verify_keyed_mac(key: bytes, message: bytes, expected_mac: bytes) -> bool:
    """Constant-time verification of keyed HMAC."""
    actual_mac = compute_keyed_mac(key, message)
    return hmac.compare_digest(actual_mac, expected_mac)


# ----------------------------------------------------------------------
# In-Memory Buffer Wiping (Best-Effort)
# ----------------------------------------------------------------------

def zero_buffer(buf: Any) -> None:
    """
    Best-effort in-place zeroing of mutable buffers in memory.
    Plain-Language Note:
    Due to CPython runtime memory management, garbage collection, and potential
    OS pagefile paging, memory zeroing is best-effort and cannot guarantee total
    eradication of transient string or byte objects from freed heap regions.
    Enabling BitLocker disk encryption on C: and D: is strongly recommended.
    """
    if buf is None:
        return
    try:
        if isinstance(buf, bytearray):
            c_buf = (ctypes.c_char * len(buf)).from_buffer(buf)
            ctypes.memset(c_buf, 0, len(buf))
        elif hasattr(buf, "_as_parameter_") or isinstance(buf, ctypes.Array):
            ctypes.memset(ctypes.byref(buf), 0, ctypes.sizeof(buf))
    except Exception:
        pass
    gc.collect()


# ----------------------------------------------------------------------
# Passphrase Strength Evaluator
# ----------------------------------------------------------------------

PREDICTABLE_TOKENS = {
    "password", "passphrase", "master", "secure", "scribe", "session",
    "admin", "welcome", "qwerty", "letmein", "changeme",
    "iloveyou", "ilove",
}

FAMOUS_WEAK_PASSPHRASES = [
    "correct horse battery staple",
    "correcthorsebatterystaple",
    "tr0ub4dor&3",
    "troubadour",
    "to be or not to be that is the question",
    "the quick brown fox jumps over the lazy dog",
    "all work and no play makes jack a dull boy",
    "my voice is my passport verify me",
    "may the force be with you always",
    "i have a dream today",
    "one small step for man one giant leap for mankind",
    "elementary my dear watson",
]

COMMON_SENTENCE_PATTERNS = [
    r"^i\s+love\s+",
    r"^this\s+is\s+my\s+",
    r"^my\s+name\s+is\s+",
    r"^the\s+quick\s+brown\s+",
]


def shannon_entropy(s: str) -> float:
    """Computes the Shannon entropy of a string in bits per character."""
    if not s:
        return 0.0
    import math
    prob = [float(s.count(c)) / len(s) for c in set(s)]
    return -sum(p * math.log2(p) for p in prob)


def evaluate_passphrase_strength(passphrase: str, user_inputs: Optional[List[str]] = None) -> Dict[str, Any]:
    """
    Evaluates passphrase strength using a zxcvbn-style guessability estimator
    with strict pattern filtering (rejecting dictionary sequences, repetitive strings,
    and conversational idioms), enforcing a minimum guess-resistance of 10^20.
    """
    if not passphrase:
        return {
            "valid": False,
            "score": 0,
            "label": "Too short",
            "word_count": 0,
            "feedback": ["Passphrase cannot be empty."],
        }

    cleaned = passphrase.strip()
    length = len(cleaned)
    words = [w.lower() for w in re.split(r"[\s\-_.,;:]+", cleaned) if len(w) > 0]
    word_count = len(words)
    unique_words = set(words)

    if length < 10:
        return {
            "valid": False,
            "score": 0,
            "label": "Too short",
            "word_count": 0,
            "feedback": ["Passphrase cannot be empty or under 10 characters."],
        }

    if length < 14 and word_count < 5:
        return {
            "valid": False,
            "score": 10,
            "label": "Weak",
            "word_count": word_count,
            "feedback": ["Passphrase must be at least 14 characters or 5-6 random words."],
        }

    # 1. Reject dummy repeating patterns: e.g. "aaaa bbbb cccc"
    dummy_words = [w for w in words if len(set(w)) == 1 and len(w) >= 3]
    if len(dummy_words) >= 2 or (word_count > 0 and len(dummy_words) / word_count >= 0.4):
        return {
            "valid": False,
            "score": 15,
            "label": "Too repetitive",
            "word_count": word_count,
            "feedback": ["Repetitive character patterns (like 'aaaa') detected; use distinct random words."],
        }

    # 2. Check for predictable tokens like 'passphrase', 'secure', 'password'
    lower_p = cleaned.lower()
    for tok in PREDICTABLE_TOKENS:
        if tok in lower_p:
            return {
                "valid": False,
                "score": 15,
                "label": "Predictable pattern",
                "word_count": word_count,
                "feedback": [f"Common predictable pattern or word '{tok}' detected. Avoid standard keywords."],
            }

    # 3. Check for famous / well-known passphrases (e.g., "correct horse battery staple")
    norm_p = re.sub(r"[^a-z0-9]", "", lower_p)
    for famous in FAMOUS_WEAK_PASSPHRASES:
        famous_norm = re.sub(r"[^a-z0-9]", "", famous.lower())
        if famous_norm in norm_p or norm_p == famous_norm:
            return {
                "valid": False,
                "score": 10,
                "label": "Famous phrase",
                "word_count": word_count,
                "feedback": [f"Famous / well-known passphrase detected ('{famous}'). Must use an unpredictable random phrase."],
            }

    # 4. Check for common conversational sentence patterns (e.g., "I love my dog Rex")
    for pat in COMMON_SENTENCE_PATTERNS:
        if re.search(pat, lower_p):
            return {
                "valid": False,
                "score": 25,
                "label": "Common phrase",
                "word_count": word_count,
                "feedback": ["Common sentence structure detected. Use uncorrelated random words."],
            }

    # 5. Higher bar for manual entry:
    # If space-separated words, require at least 6 random words (6 or 7 words recommended)
    has_spaces = bool(re.search(r"\s+", cleaned))
    if has_spaces and word_count < 6:
        return {
            "valid": False,
            "score": 20,
            "label": "Too few words",
            "word_count": word_count,
            "feedback": [f"Manual word-based passphrase has {word_count} words. Must consist of at least 6 random words (6 or 7 words recommended)."],
        }
    if not has_spaces and length < 20:
        return {
            "valid": False,
            "score": 20,
            "label": "Too short",
            "word_count": 1,
            "feedback": ["Non-word manual passphrase must be at least 20 characters with high entropy."],
        }

    # Run zxcvbn guessability evaluation
    custom_inputs = list(user_inputs or []) + [
        "sessionscribe", "scribe", "vault", "passphrase", "password", "master", "secure"
    ]
    if zxcvbn is not None:
        res = zxcvbn.zxcvbn(cleaned, user_inputs=custom_inputs)
        z_score = res["score"]  # 0 to 4
        guesses_log10 = res["guesses_log10"]
    else:
        entropy = shannon_entropy(cleaned) * length
        guesses_log10 = entropy * 0.301
        z_score = 4 if guesses_log10 >= 20.0 else int(guesses_log10 / 5.0)

    # 6. Check guessability threshold (minimum 10^20 guesses):
    if z_score < 4 or guesses_log10 < 20.0:
        return {
            "valid": False,
            "score": int(guesses_log10 * 3),
            "label": "Weak",
            "word_count": word_count,
            "feedback": ["Passphrase does not meet minimum guess-resistance threshold (need >= 10^20 guesses)."],
        }

    # Word diversity check for multi-word phrases
    if word_count >= 5 and len(unique_words) < word_count - 1:
        return {
            "valid": False,
            "score": 20,
            "label": "Repeated words",
            "word_count": word_count,
            "feedback": ["Passphrase contains duplicate words. Use unique random words."],
        }

    score_val = min(100, int(guesses_log10 * 3.1))
    return {
        "valid": True,
        "score": max(85, score_val),
        "label": "Very Strong",
        "word_count": word_count,
        "guesses_log10": guesses_log10,
        "feedback": [],
    }


# ----------------------------------------------------------------------
# Atomic File Replacement & Best-Effort Disk Shredding
# ----------------------------------------------------------------------

def atomic_write_bytes(filepath: str, data: bytes) -> None:
    """
    Writes data to a temporary file, flushes to disk via fsync,
    and atomically replaces the destination.
    """
    directory = os.path.dirname(os.path.abspath(filepath))
    os.makedirs(directory, exist_ok=True)

    tmp_path = f"{filepath}.{os.getpid()}.{secrets.token_hex(4)}.tmp"
    with open(tmp_path, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())

    os.replace(tmp_path, filepath)


def best_effort_shred_file(filepath: str) -> bool:
    """
    Overwrites file with random bytes, flushes via fsync, and unlinks it.
    Plain-Language Note:
    On SSD/NVMe drives, in-place sector overwriting is unreliable due to Flash
    Translation Layers (FTL), wear leveling, and over-provisioning. The true
    cryptographic protection is destroying the symmetric key; physical file
    overwriting is strictly best-effort.
    """
    if not os.path.isfile(filepath):
        return False
    try:
        size = os.path.getsize(filepath)
        if size > 0:
            with open(filepath, "r+b") as f:
                f.write(secrets.token_bytes(size))
                f.flush()
                os.fsync(f.fileno())
        os.remove(filepath)
        return True
    except Exception:
        try:
            os.remove(filepath)
            return True
        except Exception:
            return False


# ----------------------------------------------------------------------
# Random Speaker ID Generator (>= 128 bits)
# ----------------------------------------------------------------------

def generate_speaker_id() -> str:
    """Generates a random 128-bit persistent speaker identifier."""
    # 16 bytes = 128 bits -> 32 hex characters
    return f"spk_{secrets.token_hex(16)}"
