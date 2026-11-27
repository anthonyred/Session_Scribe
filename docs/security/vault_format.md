# Session Scribe — Vault Format & Cryptographic Specification

**Document Status:** Final (Phase V1 Approved Specification)  
**Security Classification:** Confidential / Cryptographic Engineering Specification  
**Format Version:** Session Scribe Vault v2.0 (`SSV2`)  
**Target Version:** Session Scribe v2.0 Architecture  
**Author:** Security & Core Architecture Team  
**Date:** 2026-11-19  

---

## 1. Scope & Core Architecture

This document defines the storage layout, envelope encryption hierarchy, binary record headers, data structures, and lifecycle protocols for **Session Scribe Vault v2**.

Vault v2 replaces all legacy storage mechanisms (including plaintext `.npy` embedding caches, plaintext JSON files, and unencrypted reference WAV audio) with a zero-leakage, dual-zone, hardware-anchored envelope encryption architecture.

### Primary Cryptographic Principles
1. **Standardized Cryptographic Libraries Only:**
   - Key derivation: `argon2-cffi` (C-optimized libargon2 implementation). Pure-Python implementations are banned.
   - Symmetric authenticated encryption: `cryptography.hazmat.primitives.ciphers.aead.AESGCM`. All custom XOR/RC4 stream ciphers are completely eradicated.
2. **Dual-Zone Segregation:** Identity records (names, consent, roles) and biometric records (acoustic embeddings, reference audio) reside in separate subtrees encrypted under independent zone keys. Compromise of a single record key or single zone file reveals zero information about the opposing zone.
3. **No Plaintext on Disk:** Every persistent asset containing personal identities, voice samples, or vector embeddings exists on disk solely as AES-256-GCM ciphertext.
4. **Crash Safety & Atomic Writes:** Every write operation is staged to a temporary file (`*.tmp`), flushed to disk (`os.fsync`), and atomically swapped into place (`os.replace`).
5. **Zero Backdoors or Recovery Escrow:** Vault security is strictly zero-knowledge. If the user forgets their master passphrase, **all vault data is irrecoverable by design**.

---

## 2. Cryptographic Primitives & Justifications

| Primitive | Parameter / Standard | Justification & Benchmark Evidence |
|:---|:---|:---|
| **Key Derivation Function (KDF)** | **Argon2id** via `argon2-cffi`<br>- Time cost ($t$): `10`<br>- Memory cost ($m$): `262144 KiB` (256 MiB)<br>- Parallelism ($p$): `4` lanes<br>- Salt length: `16 bytes` (`os.urandom`)<br>- Output key length: `32 bytes` (256 bits) | **Measured Performance on Target CPU:**<br>- $m=256\text{MB}, t=10, p=4$: **~578 ms (0.58 s)**<br>- $m=256\text{MB}, t=12, p=4$: **~679 ms (0.68 s)**<br>- $m=256\text{MB}, t=14, p=4$: **~784 ms (0.78 s)**<br><br>*Justification:* Calibrated to the target 0.5–1.0 s threshold off the UI thread. Consumes 256 MiB of RAM, leaving generous headroom for Whisper and Pyannote models on this 16GB host. Parameters are serialized in the vault header so they can be dynamically raised in the future. Passphrase setup enforces high entropy (5–6 random words minimum) with visual strength feedback. |
| **Hardware Credential & Machine Binding** | **Windows DPAPI** (`CryptProtectData`)<br>- Scope: `CurrentUser`<br>- Flags: `CRYPTPROTECT_UI_FORBIDDEN` | Ties master key envelope decryption directly to the user's logged-in Windows session and TPM/SAM credentials. **Plain-Language Boundary:** The vault is strictly bound to this specific Windows user account and hardware. An OS reinstallation, user account deletion, or migration to a new PC renders the vault permanently unrecoverable and gone. |
| **Symmetric Cipher** | **AES-256-GCM** via `cryptography`<br>- Key size: `256 bits` (32 bytes)<br>- Nonce/IV size: `96 bits` (12 bytes, `os.urandom`)<br>- Tag length: `128 bits` (16 bytes) | Authenticated encryption with associated data (AEAD). Guarantees both confidentiality and tamper-evident message authenticity. Nonces are cryptographically random and never reused. |
| **AAD Binding** | **Record Identifier in AAD**<br>(e.g. `spk_<32hex>` or `event_<uuid>`) | Passing the unique record ID as Additional Authenticated Data (`AAD`) cryptographically binds the ciphertext to its specific record slot. Prevents record-swapping or replay attacks where an attacker swaps ciphertext files. |
| **Keyed MAC Audit Chain** | **HMAC-SHA256** | Keyed MAC chain using a dedicated audit key derived from $K_{\text{master}}$ via HKDF. Guarantees tamper-evidence and prevents local recalculation by unauthorized entities. The latest head hash is mirrored externally (e.g. n8n webhook notification). |

---

## 3. Cryptographic Key Hierarchy

Vault v2 utilizes a five-tier envelope key hierarchy:

```
[ User Passphrase ]
         |
         v  Argon2id (t=4, m=64MB, p=4, Salt=16B) [~61.5 ms off-UI thread]
  [ K_argon (256-bit) ]
         |
         +-----------------------+
         |                       |
         v                       v
[ Windows DPAPI Secret ]  [ HKDF-SHA256 Extract/Expand ]
         |                       |
         +-----------> (Combined Key: K_master_wrap)
                                 |
                                 v  AES-256-GCM Unwrapping
                         [ K_master (256-bit Random) ]
                                 |
         +-----------------------+-----------------------+
         |                                               |
         v  AES-256-GCM (AAD: "zone_identity")           v  AES-256-GCM (AAD: "zone_biometric")
[ K_zone_identity (256-bit) ]                 [ K_zone_biometric (256-bit) ]
         |                                               |
         v  AES-256-GCM (AAD: spk_id)                    v  AES-256-GCM (AAD: spk_id)
[ K_person_id (256-bit Random) ]              [ K_person_bio (256-bit Random) ]
         |                                               |
         v  AES-256-GCM Encrypt                          v  AES-256-GCM Encrypt
[ Identity Record: Name, Consent ]             [ Biometric Record: Embeddings, WAV ]
```

### Key Management & Least-Privilege Rules:
1. **Master Key ($K_{\text{master}}$):** A cryptographically secure 256-bit random key generated via `os.urandom(32)`. Never written to disk unencrypted.
2. **Master Wrapping Key ($K_{\text{master\_wrap}}$):** Derived by combining the Argon2id passphrase output ($K_{\text{argon}}$) with a 256-bit DPAPI-protected secret via HKDF-SHA256 (`info=b"session-scribe-master-wrap"`).
3. **Zone Keys ($K_{\text{zone\_identity}}$, $K_{\text{zone\_biometric}}$):** 256-bit random keys wrapped under $K_{\text{master}}$ using AES-256-GCM. The zone name is passed as AAD.
4. **Strict Least-Privilege Separation:**
   - The **Acoustic Matcher** receives *only* $K_{\text{zone\_biometric}}$ (and per-person biometric subkeys). It has zero access to $K_{\text{zone\_identity}}$ and cannot decrypt speaker names, consent records, or identity mappings.
   - The **Late-Binding Resolver** receives *only* $K_{\text{zone\_identity}}$ (and per-person identity subkeys). It has zero access to $K_{\text{zone\_biometric}}$ and cannot decrypt acoustic embeddings or reference WAV audio.
   - Neither component can derive, inspect, or invoke the other zone's cryptographic keys.
5. **Per-Person Data Keys:** Each enrolled speaker is assigned independent 256-bit random keys for their identity record and biometric record. These keys are wrapped under their respective zone keys with the speaker ID (`spk_<32hex>`) bound into AAD.
6. **Best-Effort Memory Zeroing:** When the vault locks or closes, all decrypted keys ($K_{\text{master}}$, $K_{\text{zone}}$, $K_{\text{person}}$) are explicitly wiped from RAM using in-place buffer zeroing (`ctypes.memset(buf, 0, len(buf))`). Due to CPython runtime memory management, garbage collection, and potential OS pagefile paging, memory zeroing is best-effort and cannot guarantee total elimination of transient byte objects from unallocated RAM blocks. Enabling BitLocker disk encryption on `C:` and `D:` is strongly recommended to protect against cold pagefile extraction.

---

## 4. Vault Directory & Storage Layout

The vault is externalized entirely from the project workspace and resides in the user's local application data directory:

```
%LOCALAPPDATA%\SessionScribe\vault\v2\
├── vault.hdr                         # Master Vault Header (Salt, KDF params, wrapped master key)
├── totp.dat                          # Encrypted TOTP 2FA secret container
├── zone_identity/                    # ZONE 1: Identity Zone
│   ├── zone.hdr                      # Wrapped K_zone_identity envelope
│   ├── index.dat                     # Encrypted index of enrolled speakers & metadata
│   ├── alias_map.dat                 # Encrypted per-event session alias mapping tables
│   └── records/
│       ├── spk_01934a5f8b9e.id.enc   # Encrypted identity & consent record for person
│       └── ...
├── zone_biometric/                   # ZONE 2: Biometric Zone
│   ├── zone.hdr                      # Wrapped K_zone_biometric envelope
│   ├── index.dat                     # Encrypted biometric catalog (sample counts, vector stats)
│   └── records/
│       ├── spk_01934a5f8b9e.bio.enc  # Encrypted voice embedding vector (.npy container)
│       └── spk_01934a5f8b9e_samples/ # Encrypted reference audio samples
│           ├── sample_001.wav.enc    # Encrypted 16kHz mono WAV sample
│           └── sample_002.wav.enc
├── quarantine/                       # 24-Hour Quarantine State
│   └── spk_01934a5f8b9e.quarantine   # Encrypted scheduled deletion descriptor
└── audit/
    ├── audit.log                     # Append-only hash-chained audit log (JSONL)
    └── audit.sig                     # Latest chain head validation digest
```

---

## 5. Binary Record & File Headers

All encrypted files in Vault v2 utilize structured, versioned binary envelopes.

### 5.1 Master Header Format (`vault.hdr`)

The master header authenticates and bootstraps the cryptographic subsystem:

```
+-----------------------------------------------------------------------+
| Magic Bytes (4B): 'SSV2' (0x53, 0x53, 0x56, 0x32)                     |
+-----------------------------------------------------------------------+
| Version (2B, Big-Endian): 0x0002                                      |
+-----------------------------------------------------------------------+
| Argon2id Salt (16B): Cryptographically random salt                    |
+-----------------------------------------------------------------------+
| Argon2id Parameters:                                                  |
|   - Time Cost t (4B, Big-Endian): 4                                   |
|   - Memory Cost m (4B, Big-Endian): 65536 (KiB)                       |
|   - Parallelism p (4B, Big-Endian): 4                                 |
+-----------------------------------------------------------------------+
| DPAPI Envelope Length (4B, Big-Endian): Length L_dpapi                |
+-----------------------------------------------------------------------+
| DPAPI Protected Blob (L_dpapi Bytes): CryptProtectData ciphertext     |
+-----------------------------------------------------------------------+
| Master Key AES-GCM IV (12B): Random 96-bit nonce                      |
+-----------------------------------------------------------------------+
| Master Key Ciphertext Length (4B, Big-Endian): 32 + 16 = 48 bytes     |
+-----------------------------------------------------------------------+
| Master Key Ciphertext + GCM Tag (48B): Encrypted K_master             |
+-----------------------------------------------------------------------+
```

### 5.2 Record Envelope Header (`*.enc`, `*.dat`)

Every encrypted record file uses a unified container structure:

```
+-----------------------------------------------------------------------+
| Container Magic (4B): 'SSRE' (0x53, 0x53, 0x52, 0x45)                 |
+-----------------------------------------------------------------------+
| Container Version (2B, Big-Endian): 0x0002                            |
+-----------------------------------------------------------------------+
| Record ID Length (2B, Big-Endian): Length L_id (e.g. 20 bytes)        |
+-----------------------------------------------------------------------+
| Record ID String (L_id Bytes): Bound into AAD (e.g. "spk_...")         |
+-----------------------------------------------------------------------+
| Wrapped Data Key Length (2B, Big-Endian): Length L_k (48 bytes)       |
+-----------------------------------------------------------------------+
| Wrapped Data Key (L_k Bytes): AES-GCM envelope of K_person under Zone |
+-----------------------------------------------------------------------+
| Payload AES-GCM IV (12B): Random 96-bit nonce                         |
+-----------------------------------------------------------------------+
| Payload Length (8B, Big-Endian): Length L_data                        |
+-----------------------------------------------------------------------+
| Payload Ciphertext + Tag (L_data + 16 Bytes): Encrypted Content       |
+-----------------------------------------------------------------------+
```

---

## 6. Data Schemas (Plaintext Payloads before Encryption)

### 6.1 Identity Record Schema (`<spk_id>.id.enc`)
- **Speaker ID Requirement:** Every enrolled individual is assigned a random 128-bit identifier formatted as `spk_<32hex>` (generated via `secrets.token_hex(16)`).
- **Plaintext JSON Payload:**
```json
{
  "schema_version": 2,
  "spk_id": "spk_7b9c3f4e2a1d8e0f9b8a7c6d5e4f3a21",
  "full_name": "Jane Doe",
  "display_name": "Jane Doe",
  "affiliation": "Global Engineering Foundation",
  "created_utc": "2026-11-19T12:00:00Z",
  "updated_utc": "2026-11-19T12:00:00Z",
  "roles": ["Speaker", "Panelist"],
  "consent": {
    "opt_in": true,
    "consent_timestamp_utc": "2026-11-19T12:00:00Z",
    "policy_version": "2.0",
    "method": "explicit_ui_checkbox",
    "verified_by": "local_user",
    "consent_statement": "The speaker has provided explicit permission to store voice biometrics locally for transcription identification."
  },
  "metadata": {
    "sample_count": 2,
    "last_matched_event_id": null
  }
}
```

### 6.2 Biometric Record Schema (`<spk_id>.bio.enc`)
- **Payload:** Encrypted binary container holding the average 512-dimensional speaker embedding vector generated by `pyannote/speaker-diarization-3.1`.
- **Internal Binary Layout:**
  - Header (32 bytes): Model identifier (`pyannote-3.1`), vector dimension (512, uint32), float dtype (float32, uint16), sample count (uint16), quality metric (float32).
  - Array Data (2048 bytes): Raw IEEE 754 float32 array bytes.
  - Verification Digest (32 bytes): SHA-256 of the unencrypted array.

### 6.3 Encrypted Reference Audio (`<spk_id>_samples/sample_*.wav.enc`)
- **Payload:** Complete 16 kHz mono 16-bit PCM WAV audio file, encrypted in its entirety.
- **AAD Binding:** `spk_id + ":" + sample_filename`.

---

## 7. Per-Event Alias Map Architecture

In accordance with Invariant 1 and Invariant 4, Compute-Zone artifacts (working transcripts, manifests, `clip_state.json`) store only ephemeral aliases (`S01`, `S02`, ...).

The mapping between aliases and persistent speaker identities lives exclusively inside Vault v2:

```
[ Compute Zone: clip_state.json ]
  - Clip 01: Speaker "S01", Duration 00:04:12
  - Clip 02: Speaker "S02", Duration 00:08:30

[ Vault Zone: zone_identity/alias_map.dat ] (Encrypted with K_zone_identity)
  Key: Event ID "ev_8f21a4bc..." (UUIDv4)
  Value: {
    "S01": "spk_7b9c3f4e2a1d8e0f9b8a7c6d5e4f3a21",  --> Resolves to "Jane Doe"
    "S02": "spk_104f98e2bc7801a2389d7fa0b4c891e5"   --> Resolves to "John Smith"
  }
```

### Late-Binding Resolver Workflow:
1. When the user opens the Transcript Viewer in GUI:
   - If Vault is **Unlocked**: The viewer invokes `LateBindingResolver.resolve_text(transcript_text, event_id)`. The resolver loads `alias_map.dat`, maps `S01` to `spk_id`, queries `zone_identity` for `display_name`, and renders `"Jane Doe: Welcome everyone..."` in the UI.
   - If Vault is **Locked**: The viewer renders `"S01: Welcome everyone..."`. An amber status chip displays `"🔒 Vault Locked — Showing Aliases"`.
2. When the user exports combined transcripts (`scribe\combined\`):
   - If Vault is **Unlocked**: Combined files are generated as `scribe\combined\Jane_Doe.txt`.
   - If Vault is **Locked**: Combined files are generated as `scribe\combined\Speaker_S01.txt`.

---

## 8. Security Protocols & Operational Lifecycle

### 8.1 Auto-Lock & Workstation Hook
- **Inactivity Timer:** A background daemon timer tracks mouse and keyboard interaction. After 30 continuous minutes without UI interaction, `vault.lock()` is triggered.
- **Windows OS Session Hooks:**
  - Registers for `WTSRegisterSessionNotification(hwnd, NOTIFY_FOR_THIS_SESSION)`.
  - Upon receiving `WM_WTSSESSION_CHANGE` with event `WTS_SESSION_LOCK` (Windows+L or lock screen): triggers immediate synchronous `vault.lock()`.
  - Windows screensaver query via `SystemParametersInfo(SPI_GETSCREENSAVERRUNNING)`: locks immediately upon screensaver activation.
- **Lock Procedure:**
  1. Overwrites all decrypted key bytearrays in RAM with `0x00`.
  2. Discards decrypted identity dictionaries and in-memory embedding caches.
  3. Emits `VaultStatusChanged(locked=True)` event to update GUI chips to amber.

### 8.2 Destructive Actions, 24-Hour Quarantine & Crypto-Shredding
To eliminate accidental deletions, insider sabotage, or automated wiping, deleting an enrolled speaker requires a strict multi-gate protocol:

```
[ Step 1: User Deletion Request ]
         |
         v
[ Step 2: Multi-Factor Re-Authentication ]
  - Enter Master Passphrase (verified via Argon2id)
  - Enter 6-digit TOTP Code (verified against totp.dat)
         |
         v
[ Step 3: Typed Confirmation Phrase ]
  - User must manually type: "SHRED spk_7b9c3f4e2a1d8e0f9b8a7c6d5e4f3a21"
         |
         v
[ Step 4: 24-Hour Quarantine Hold ]
  - Record moved to quarantine/ state with scheduled_purge_utc = Now + 24 Hours
  - Speaker hidden from active matching and UI rosters
  - User may cancel deletion at any time during the 24-hour window
         |
         v (After 24 Hours)
[ Step 5: Cryptographic Shredding ]
  1. Irrevocably overwrite and destroy per-person data key K_person and all wrapped envelopes in memory.
  2. Overwrite the on-disk person key container with random bytes and flush synchronously (os.fsync).
  3. Best-Effort Disk Overwrite: Overwrite ciphertext record files (*.id.enc, *.bio.enc, samples/) and unlink them.
     *Note on Solid-State Drives:* On modern SSD/NVMe drives, in-place sector overwriting cannot be guaranteed due to Flash Translation Layers (FTL), wear leveling, over-provisioning, and controller-level block remapping. The true mathematical protection is the complete eradication of K_person; without the key, orphaned flash blocks contain only indecipherable AES-256-GCM ciphertext.
  4. Disk Encryption Recommendation: BitLocker full-disk encryption on C: and D: is strongly recommended to protect against raw chip-off physical extraction. A best-effort free-space zero-fill is performed following legacy migration.
  5. Append record deletion entry to the Keyed-MAC Audit Log.
```

### 8.3 TOTP Two-Factor Authentication & Policy Boundary
- **Algorithm:** RFC 6238 TOTP (HMAC-SHA1, 30-second step, 6-digit code, $\pm 1$ step drift tolerance).
- **Storage:** The 20-byte base32 secret seed is encrypted under $K_{\text{master}}$ and stored in `totp.dat`.
- **Policy Enforcement:**
  - **Daily Unlock:** Requires the Master Passphrase only (plus Windows DPAPI user and hardware binding).
  - **TOTP Verification Required For:** Delete profile, replace profile, modify profile, bulk purge (`"PURGE ALL PROFILES"`), passphrase change, and TOTP reset.
  - **Enrollment Flow:** The provisioning QR code is rendered strictly in memory and never written to disk. The user is offered the option to view the raw Base32 setup key once to store it offline.
  - **TOTP Reset Cooldown:** Resetting or replacing a lost authenticator requires passphrase verification followed by a mandatory 24-hour security delay (`totp_reset.json`), preventing rapid unauthorized takeover.
- **Honest Architectural Boundary:**
  - Because the TOTP seed is stored encrypted under the master key, TOTP serves strictly as an **application-level access control layer** during live interactive sessions. It does not provide an independent cryptographic factor against an offline attacker who steals the vault files. If an attacker recovers the master key offline on a machine with identical DPAPI credentials, they can unwrap the TOTP secret. For true cryptographic hardware two-factor derivation offline, future revisions plan to support a **FIDO2 `hmac-secret`** token (e.g. YubiKey).

### 8.4 Crypto-Shredding Finality & External Storage Copies
- **Key Eradication:** Crypto-shredding permanently destroys the symmetric data key and unlinks ciphertext files.
- **Boundary Condition:** Crypto-shredding is mathematically final **only if no earlier copy of the vault or ciphertext exists**. If Windows Volume Shadow Copies (VSS), Windows System Restore points, external drive backups, cloud folder synchronization, or the legacy migration backup contain snapshots of the vault files, those earlier copies may remain accessible to anyone who possesses the passphrase and DPAPI state.

### 8.5 Local Audit Mirror & Remote Notification
- **Location:** The head mirror (`audit_head.mirror`) is written to an independent directory outside the vault root (`%LOCALAPPDATA%\SessionScribe\audit_mirror\`).
- **Limitation:** While separating the mirror outside the vault directory protects against accidental directory deletion or localized single-folder tampering, any local mirror on the same machine remains weak against malware running under the same Windows user account. Irrefutable tamper-evidence is achieved once the remote off-machine mirror (via n8n webhook notification) is active.

### 8.4 Keyed-MAC Append-Only Audit Log
All security-relevant actions are recorded in `%LOCALAPPDATA%\SessionScribe\vault\v2\audit\audit.log`.

- **Keyed MAC Construction:** Unlike a simple public SHA-256 digest, the audit chain uses **HMAC-SHA256** keyed with an audit MAC key ($K_{\text{audit\_mac}}$) derived from $K_{\text{master}}$ via HKDF (`info=b"session-scribe-audit-mac"`). An attacker without the master key cannot forge or rewrite historical log entries.
- **External Mirroring (n8n Webhook Endpoint):** To detect local truncation or catastrophic local file deletion, the latest entry head hash is mirrored to an external audit endpoint (configurable n8n notification webhook) upon each write. A mismatch between the local log head and the mirrored head immediately signals tampering.
- **Log Entry Format (JSON Lines):**
```json
{
  "index": 104,
  "timestamp_utc": "2026-11-19T12:05:30.124Z",
  "action": "ENROL_SPEAKER",
  "spk_id": "spk_7b9c3f4e2a1d8e0f9b8a7c6d5e4f3a21",
  "actor": "local_user",
  "details": {
    "consent_verified": true,
    "sample_count": 2
  },
  "prev_mac": "c4d28e7a89b01f92e3d4c5b6a7890123456789abcdef0123456789abcdef0123",
  "entry_mac": "8a9b0c1d2e3f4a5b6c7d8e9f0a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b"
}
```
- **Chain Invariant:**
  $$\text{entry\_mac} = \text{HMAC-SHA256}_{K_{\text{audit\_mac}}}(\text{index} \,\|\, \text{timestamp\_utc} \,\|\, \text{action} \,\|\, \text{spk\_id} \,\|\, \text{prev\_mac})$$
  Genesis entry has $\text{prev\_mac} = \text{"0000000000000000000000000000000000000000000000000000000000000000"}$.
- **Tamper Evidence:** Modifying, deleting, or reordering any log record invalidates all subsequent hash links across the entire chain.

---

## 9. Egress Guard & Network Isolation Specification

The **Egress Guard** (`scribe_core/egress_guard.py`) intercepts all network calls to ensure zero PII or biometrics escape the host machine.

### 9.1 Schema Allowlist (Cloud ASR - NVIDIA Riva)
Every gRPC streaming request payload is inspected against a rigid allowlist:

```python
RIVA_REQUEST_ALLOWLIST = {
    "allowed_fields": {
        "streaming_config": {
            "config": {
                "encoding": {"type": int, "allowed": [1]},             # LINEAR_PCM
                "sample_rate_hertz": {"type": int, "allowed": [16000]}, # 16 kHz
                "language_code": {"type": str, "allowed": ["en-US"]},   # Supported language
                "max_alternatives": {"type": int, "allowed": [1]},
                "profanity_filter": {"type": bool},
                "enable_automatic_punctuation": {"type": bool},
                "verbatim_transcripts": {"type": bool},
                "model": {"type": str, "allowed": ["parakeet-1.1b", "whisper-large-v3"]}
            },
            "interim_results": {"type": bool}
        },
        "audio_content": {"type": bytes}  # Raw PCM audio bytes only
    },
    "forbidden_fields": [
        "speaker_id", "speaker_name", "user_id", "embedding", 
        "metadata", "transcript_hint", "custom_fields"
    ]
}
```
If any request includes fields outside the allowlist or contains forbidden fields, the Egress Guard raises an `EgressViolationError` and immediately aborts the network connection.

### 9.2 Glossary Boosting (`boosted_lm_words`) Policy
Custom vocabulary lists submitted to improve transcription accuracy are subject to dynamic security filtering:
- **Vault Unlocked:** The Egress Guard retrieves all enrolled speaker names from the Identity Zone and removes any word matching an enrolled first name, last name, or full name before compiling the boosting list.
- **Vault Locked:** Because enrolled names cannot be checked while the vault is locked, glossary boosting is **blocked entirely for cloud transcription** unless the user explicitly checks an override confirmation dialog acknowledging potential name transmission.

### 9.3 Local Inference Offline Enforcement
To prevent third-party libraries (Hugging Face `transformers`, `pyannote.audio`) from making unauthorized background calls or telemetry checks:
- The application sets:
  ```python
  os.environ["HF_HUB_OFFLINE"] = "1"
  os.environ["TRANSFORMERS_OFFLINE"] = "1"
  os.environ["HF_DATASETS_OFFLINE"] = "1"
  ```
- These environment flags are asserted in `sys.path` startup guards prior to importing PyTorch or Hugging Face modules.

---

## 10. Legacy Migration Plan & Safe Decommissioning

Session Scribe v1 stored speaker data across unencrypted directories (`D:\Events\_voice_library\`) with plaintext `.npy` files and event-local `voice_samples/`.

The migration protocol transitions all historical assets into Vault v2 without data loss and guarantees complete removal of legacy plaintext files.

### 10.1 Inventory of Legacy Artifacts
1. `D:\Events\_voice_library\library.json`: Plaintext metadata and speaker name mappings.
2. `D:\Events\_voice_library\embeddings.npy`: Unencrypted NumPy float array holding voiceprints.
3. `D:\Events\_voice_library\<Speaker_Name>\sample_*.wav`: Unencrypted reference audio recordings.
4. `D:\Events\<Event>\scribe\state\voice_samples\`: Event-local unencrypted reference WAVs.

### 10.2 Migration Procedure (Phased Execution)

```
[ Phase 1: Dry-Run Inspection ]
  - Scan D:\Events\_voice_library\ and event folders.
  - Verify file hashes and integrity of WAVs and .npy arrays.
  - Output migration manifest to user without touching any files.

[ Phase 2: Pre-Migration Encrypted Backup ]
  - Create secure archive of all legacy voice files.
  - Encrypt the archive under Vault v2 Master Key: %LOCALAPPDATA%\SessionScribe\vault\v2\migration_backup.tar.enc.

[ Phase 3: Vault Ingestion & Transformation ]
  - For each legacy person:
    1. Generate fresh 128-bit spk_id.
    2. Read name and consent; construct encrypted Identity Record (<spk_id>.id.enc).
    3. Extract embedding vector; construct encrypted Biometric Record (<spk_id>.bio.enc).
    4. Encrypt reference WAV samples into zone_biometric/records/<spk_id>_samples/.
    5. Verify decryption round-trip and cosine similarity against original embedding.

[ Phase 4: Compute-Zone Anonymization ]
  - Scan all historical event manifests, clip_state.json, and transcripts.
  - Replace hard-coded names with session aliases (S01, S02).
  - Populate alias_map.dat inside Vault v2.

[ Phase 5: Verification Canary Scan ]
  - Execute deep regex and byte scan across D:\Session_Scribe\ and D:\Events\.
  - Canary scan asserts zero occurrences of known speaker names in CSVs, logs, state files, or unencrypted WAV paths.

[ Phase 6: Secure Cryptographic Shredding of Legacy Files ]
  - Multi-pass pseudorandom overwrite of D:\Events\_voice_library\*.npy and *.wav files.
  - Flush disk buffers (os.fsync) and delete legacy folders.
```

---

## 11. Implementation & Verification Roadmap

| Phase | Milestone | Deliverables | Verification Criterion |
|:---:|:---|:---|:---|
| **V1** | **Threat Model & Specifications** | `threat_model.md`, `vault_format.md` | Formal approval by user; zero code written. |
| **V2** | **Cryptographic Core & KDF** | `vault_crypto.py`, `key_hierarchy.py` | Unit tests for Argon2id, DPAPI, AES-GCM, memory zeroing. |
| **V3** | **Dual-Zone Vault Implementation** | `vault_v2.py`, `identity_zone.py`, `biometric_zone.py` | Atomic record read/write, AAD binding, auto-lock tests. |
| **V4** | **Alias Map & Late-Binding Resolver** | `alias_map.py`, `late_binding_resolver.py` | Compute zone sees only `S01`; name injection verified. |
| **V5** | **Egress Guard & Network Isolation** | `egress_guard.py`, `offline_guard.py` | Mock gRPC interception, allowlist reject, HF offline test. |
| **V6** | **Audit Log & Destruction Protocols** | `audit_log.py`, `crypto_shredder.py`, `quarantine.py` | Hash chain verification, 24h quarantine, multi-pass wipe. |
| **V7** | **Legacy Migration Tooling** | `migrate_vault_v2.py` | Dry-run, encrypted backup, canary scan pass, wipe pass. |
| **V8** | **UI Integration & Final Hardening** | Qt/Tk dialogs, status chips, full regression | End-to-end multi-speaker ingest, transcription, export. |

---

## 12. Document Approval

This document represents the authoritative cryptographic design for Session Scribe v2. No application code modifications shall proceed until explicit user review and formal authorization of this specification.
