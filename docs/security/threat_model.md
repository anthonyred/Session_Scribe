# Session Scribe — Threat Model & Security Architecture

**Document Status:** Final (Phase V1 Approved Architecture)  
**Security Classification:** Confidential / Internal Security Documentation  
**Target Version:** Session Scribe v2.0 Architecture  
**Author:** Security & Core Architecture Team  
**Date:** 2026-11-19  

---

## 1. Executive Summary & Architectural Scope

Session Scribe is an automated desktop application designed to ingest recorded conference audio/video, detect speaker boundaries, perform automatic speech recognition (ASR) via local GPU models (OpenAI Whisper) or cloud-hosted engines (NVIDIA Riva), and assemble approved per-speaker transcripts.

Because the system captures, processes, and persists high-resolution human voice biometrics (acoustic voiceprints, speaker embedding vectors, reference WAV audio) alongside personal identifiable information (PII; speaker names, affiliations, presentation roles), security and privacy must be foundational guarantees rather than add-on features.

This document establishes the formal **Threat Model**, **Adversary Analysis**, and **Security Invariants** governing the transition from Session Scribe v1 to the zero-leakage, cryptographic-isolation architecture of Session Scribe v2.

---

## 2. Core Architectural Separation: Two Zones

To enforce provable security invariants across heterogeneous workflows, Session Scribe v2 bisects all operations into two strictly isolated environments:

```
+-----------------------------------------------------------------------------------+
|                              COMPUTE ZONE (Semi-Trusted)                         |
|  - ASR Inference (OpenAI Whisper medium, NVIDIA Riva gRPC)                        |
|  - Diarization Engine (PyAnnote Audio 3.1)                                        |
|  - Workspace Storage: D:\Session_Scribe\                                          |
|  - Event Trees: D:\Events\<Event>\scribe\ (state, manifests, audio WAVs)          |
|  - Logs, Telemetry & CSVs (attempts.csv, runs.csv, session_state.json)            |
|  - Excel Reports (*.xlsx with zero external dependencies)                         |
|  - Working Transcripts: "S01: Welcome everyone...", "S02: Thank you..."           |
|  * ENFORCEMENT: Strictly anonymous IDs only (S01, S02, spk_hex128). ZERO PII.     |
+-----------------------------------------+-----------------------------------------+
                                          |
                        Late-Binding Data Resolver
                        (Vault must be UNLOCKED)
                                          |
+-----------------------------------------v-----------------------------------------+
|                               VAULT ZONE (Trusted)                                |
|  - Location: %LOCALAPPDATA%\SessionScribe\vault\v2\                               |
|  - Master Key Protection: RFC 9106 Argon2id + Windows DPAPI                      |
|  - Zone 1 (Identity): Real Names <--> spk_ID, Consent Records, Roles, Affiliation  |
|  - Zone 2 (Biometrics): spk_ID <--> Encrypted Embeddings, Encrypted Reference WAVs|
|  - Security Services: Hash-Chained Audit Log, 24h Quarantine, Crypto-Shredder     |
|  - Per-Event Alias Map: Encrypted Table (Event_ID -> {S01: spk_ID})               |
|  * ENFORCEMENT: Independent AES-256-GCM envelope encryption per record.          |
+-----------------------------------------------------------------------------------+
```

1. **The Compute Zone (Semi-Trusted):**
   - Encompasses the local audio decoding pipelines, neural inference (PyTorch CUDA Whisper), diarization clustering, streaming cloud network clients, application logging, disk state files, and user interface controls.
   - **Invariant Guarantee:** The Compute Zone possesses zero knowledge of real human identities. All speakers are represented solely by per-event session aliases (`S01`, `S02`, etc.) or random 128-bit persistent speaker identifiers (`spk_<32hex>`). No speaker names, consent forms, or raw biometric templates are ever readable or writable by Compute Zone routines.
2. **The Vault Zone (Trusted):**
   - Encompasses isolated, encrypted cryptographic storage situated completely outside the project repository and event directories, residing by default in `%LOCALAPPDATA%\SessionScribe\vault\v2\`.
   - Divides into two cryptographically segregated zones with independent encryption keys:
     - **Zone 1 (Identity):** Protects names, affiliations, event histories, and auditable consent declarations.
     - **Zone 2 (Biometric):** Protects acoustic voice embedding vectors and encrypted reference audio clips.
   - **Late-Binding Interface:** Personal names are only resolved and bound to transcript aliases in memory at user display time or upon explicit document export, and *strictly* while the Vault Zone is actively authenticated and unlocked.

---

## 3. The 13 Security Invariants

The following 13 security invariants are non-negotiable. Every system release, pull request, and refactoring must preserve all 13 properties:

| # | Invariant | Architectural Enforcement |
|---|:---|:---|
| **1** | **Compute Zone Anonymity** | The Compute Zone (ASR, diarization, local LLMs, logs, state files, CSVs, Excel reports) contains only anonymous aliases (`S01`, `S02`, ...) or random 128-bit IDs (`spk_<32hex>`), never personal names and never raw biometric embeddings. |
| **2** | **Zero Biometric Egress** | No voice signature, acoustic feature vector, or embedding ever leaves the local machine. Cloud ASR receives only anonymous audio chunks and a language code (`en-US`), along with approved model decoding parameters. |
| **3** | **Dual Vault Zone Separation & Least Privilege** | Two cryptographically independent zones protected by distinct symmetric keys: Zone 1 (Identity: name $\leftrightarrow$ `spk_` ID, consent record) and Zone 2 (Biometric: `spk_` ID $\leftrightarrow$ encrypted embeddings and sample metadata). The matcher receives only the biometric zone key; the resolver receives only the identity zone key; neither component can access the other zone's key. |
| **4** | **Late Binding of Real Names** | Working transcripts hold session aliases only (`S01`, `S02`). Real names are substituted into the view or export stream by the late-binding resolver *only* while the vault is unlocked. When the vault is locked, the application displays and exports aliases. |
| **5** | **Zero Leakage in Telemetry & Outputs** | Zero personal names or embedding vectors in application log files, exception traces, crash logs, CSV telemetry (`attempts.csv`, `runs.csv`), Excel exports, filesystem paths, state sidecars, or the system clipboard. |
| **6** | **Externalized Vault Path & Machine Binding** | The vault resides exclusively outside the project repository and outside event folders, under `%LOCALAPPDATA%\SessionScribe\vault\v2\`. Zero voice samples or embeddings are placed in `D:\Events\_voice_library\` or event-local directories. The vault is cryptographically bound to the current Windows user and machine; a Windows reinstall or migration to a new PC means the vault is irrecoverable and gone. |
| **7** | **Auto-Lock & Key Zeroing** | The vault automatically locks after 30 minutes of user inactivity, upon Windows screensaver activation, and upon Windows workstation lock (`WM_WTSSESSION_CHANGE` with `WTS_SESSION_LOCK`). Upon locking, master keys and data keys are securely overwritten with zeros in memory (best-effort buffer clearing). |
| **8** | **Destructive Action Guards & Crypto-Shredding** | Deleting an enrolled speaker requires multi-factor re-authentication (passphrase + TOTP), a typed confirmation phrase (`SHRED <spk_id>`), a mandatory 24-hour quarantine delay, and cryptographic shredding. The primary protection is the irrevocable destruction of the per-person data keys and all wrapped envelopes; physical overwriting of ciphertext on SSD/NVMe is strictly best-effort. Full-disk BitLocker encryption on C: and D: is strongly recommended. |
| **9** | **Egress Guard & Offline Enforcement** | Outbound network calls are restricted by a strict schema allowlist allowing only raw audio chunks and language parameters. Local inference engines run with `HF_HUB_OFFLINE=1` and `TRANSFORMERS_OFFLINE=1` post-cache to prevent network access. |
| **10** | **Explicit Consent & Zero Auto-Enrolment** | Enrolment into the voice vault requires an explicit opt-in checkbox, an auditable consent record containing a UTC timestamp and policy version, and clear user explanation. Diarization clusters never auto-enrol into the persistent vault. Passphrase setup enforces high entropy (5–6 random words minimum) with visual strength feedback. |
| **11** | **Keyed-MAC Audit Log with External Mirroring** | Every identity creation, modification, name resolution, decryption, and deletion is recorded in an append-only JSONL log authenticated by a keyed MAC chain (HMAC-SHA256 with key derived from the master key). Contains only anonymous IDs (`spk_`, `event_`). The latest head hash is mirrored to an external audit endpoint (e.g. n8n webhook notification) to detect local log truncation or tampering. |
| **12** | **Local Opt-in Spoken-Name Scrubbing** | Automated scrubbing of spoken names ("Thank you, Alice") in transcripts is opt-in, default-off, with a prominent disclaimer regarding probabilistic limits. Scrubbing runs 100% locally on the host machine prior to any cloud submission or export. |
| **13** | **Voice-Only Speaker Linking** | Speaker identification is performed strictly by acoustic voice matching (cosine similarity $\ge \tau$ against enrolled reference embeddings). Speakers are **never** assigned by order of appearance or session sequence. *(Note: Grouping clips into sessions by timestamp gaps via `session_grouper.py` is an accepted media layout feature and remains intact).* |

---

## 4. Adversary Models & Threat Analysis

To rigorously assess the posture of Session Scribe v2, we evaluate five distinct adversary classes.

```
+-----------------------------------------------------------------------------------+
|                                ADVERSARY TAXONOMY                                 |
+-----------------------------------------------------------------------------------+
|  [ADV-A] Stolen Laptop / Cold Storage Theft (At-Rest Physical Attacker)          |
|  [ADV-B] Another Windows User on Shared Host (Local OS Multi-User Boundary)       |
|  [ADV-C] Malware Running in Current User Space (Co-Privileged Execution Context)  |
|  [ADV-D] Untrusted Cloud Provider / Eavesdropping (Network & Cloud API)           |
|  [ADV-E] Local LLMs, AI Agents & Code Indexers (Repository File Readers)          |
+-----------------------------------------------------------------------------------+
```

### Adversary A: Stolen Laptop / Physical Disk Theft (At-Rest Attacker)
- **Profile:** An attacker gains physical possession of the powered-off laptop or extracts the NVMe SSD. They mount the filesystem or inspect raw block storage on another machine.
- **Attacker Capabilities:**
  - Full read access to all unencrypted disk partitions and files.
  - Ability to inspect `D:\Session_Scribe\`, `D:\Events\`, and `%LOCALAPPDATA%\`.
  - Offline brute-force dictionary attacks against password hashes or encrypted envelopes.
- **Mitigations in Session Scribe v2:**
  - **Hardware-Calibrated Argon2id Key Derivation:** Derives wrapping keys using 256 MiB of RAM (`m=262144 KiB`), 10 iterations (`t=10`), and 4 parallel lanes (`p=4`), measured at ~0.58 seconds on this machine in a background thread. Parameters are serialized in the vault header so they can be dynamically raised. Offline dictionary attacks are rendered computationally cost-prohibitive. Passphrase creation requires high entropy (5–6 random words minimum) with visual strength feedback.
  - **Windows DPAPI Machine & User Binding:** Master key envelopes are double-wrapped with Windows Data Protection API (`CryptProtectData`), tying decryption to the user's Windows credential encryption keys stored in the TPM/SAM. The vault is strictly bound to this specific Windows user and hardware; a Windows reinstallation or moving files to a foreign PC renders the vault permanently unrecoverable.
  - **AES-256-GCM Authenticated Encryption:** Every vault record is encrypted with a distinct 256-bit data key and a unique 96-bit nonce; tampering triggers authentication tag verification failures.
  - **True Crypto-Shredding vs. Best-Effort Disk Overwriting:** Physical overwriting on modern SSD/NVMe drives is unreliable due to Flash Translation Layers (FTL), wear leveling, over-provisioning, and out-of-place block writes. The fundamental protection is the irrevocable destruction of the 256-bit per-person data key and all wrapped envelopes in RAM and on disk, rendering the stored ciphertext mathematically undecipherable. Users are strongly advised to enable Windows BitLocker on `C:` and `D:`, and a best-effort free-space zero-fill is performed following legacy migration.
  - **Zero Plaintext Biometric Cache:** All legacy `.npy` embedding caches and event-local `voice_samples/` WAV files are migrated into the vault or crypto-shredded.
- **Adversary Cannot:**
  - Decrypt identity records, recover speaker names, or extract voice embeddings from cold storage without both the user passphrase and the Windows DPAPI machine/user master key. (Note: A new PC or OS reinstallation means the vault cannot be transferred or recovered).

---

### Adversary B: Another Windows User on a Shared Workstation (Local Multi-User)
- **Profile:** A secondary user with a distinct local account on the same Windows machine attempts to access Session Scribe records or biometrics.
- **Attacker Capabilities:**
  - Standard non-administrative user privileges.
  - Ability to browse world-readable directories or shared folders on volume `D:\`.
- **Mitigations in Session Scribe v2:**
  - **User-Scoped `%LOCALAPPDATA%` Storage:** The vault resides inside the user profile directory (`%LOCALAPPDATA%\SessionScribe\vault\v2\`), which by default Windows NTFS permissions restricts access strictly to the owner and `NT AUTHORITY\SYSTEM`.
  - **DPAPI User Scoping:** Windows DPAPI keys are scoped strictly to the current user token (`CRYPTPROTECT_UI_FORBIDDEN`). Even if permissions on `D:\` were misconfigured, another user account running on the same OS cannot unwrap the DPAPI envelope.
  - **Event Directory Anonymity:** Event folders on `D:\Events\` store only anonymous session aliases (`S01`, `S02`) in manifests, clip states, and transcripts.
- **Adversary Cannot:**
  - Read or unwrap the vault master keys, decrypt biometric templates, or correlate event transcripts with human identities.

---

### Adversary C: Malware Running in User Space (Co-Privileged Execution Context)
- **Profile:** A malicious background executable, infostealer, or trojan runs in the same user session as Session Scribe (e.g. from an untrusted browser download or phishing attachment).
- **Attacker Capabilities:**
  - Same OS user privileges as Session Scribe.
  - Ability to read user files in `%LOCALAPPDATA%` and call Windows APIs.
  - Ability to monitor filesystem changes and inspect process memory.
- **Mitigations in Session Scribe v2:**
  - **Locked-State Invariant:** When the vault is locked (the default state upon app start, after 30 minutes of inactivity, upon workstation lock, or on screensaver), the master key does *not* exist in memory, and the disk files are ciphertext. Malware cannot decrypt records without obtaining the user passphrase.
  - **Dual-Zone Segregation:** Identity records and biometric templates are stored in separate files under separate subkeys. A partial memory compromise or file capture does not automatically correlate an identity to a voiceprint.
  - **Explicit Destruction Barriers:** Destructive actions require user re-authentication with passphrase, TOTP code, and a 24-hour quarantine hold, preventing automated silent wiping of voice archives.
- **Honest Limitations & Out-of-Scope Boundaries:**
  - **Active Runtime Memory Scraping:** If sophisticated malware executes concurrently *while* the vault is actively unlocked and uses `ReadProcessMemory` or DLL injection against the Session Scribe process, it can read data currently decrypted in RAM. Standard user-space applications cannot defend against kernel rootkits or same-integrity debuggers without proprietary kernel-level anti-cheat drivers.
  - **Mitigation Strategy:** Minimize memory residency time. Decrypted plaintexts are discarded immediately after display rendering; master keys are zeroed upon lock; and the auto-lock timer strictly limits the active vulnerability window.

---

### Adversary D: Untrusted Cloud Provider / Eavesdropping (Network & Cloud API)
- **Profile:** An untrusted employee, compromise, or data mining pipeline at a cloud transcription vendor (e.g. NVIDIA Riva, third-party ASR endpoints), or an adversary observing outbound network packets.
- **Attacker Capabilities:**
  - Full inspection of all outbound gRPC/HTTPS network requests and streaming payloads.
  - Ability to log and analyze submitted audio, headers, and metadata.
- **Mitigations in Session Scribe v2:**
  - **Strict Egress Guard:** Network clients are wrapped by a runtime schema validator that enforces a rigid allowlist. The outgoing payload is restricted strictly to:
    1. Raw audio chunks (`pcm_s16le`, 16 kHz mono).
    2. Transcription language parameter (`en-US`).
    3. Model configuration parameters (e.g., sample rate, encoding format).
  - **Zero Identity & Zero Biometric Transmission:** No speaker names, no `spk_` IDs, no session aliases, and no PyAnnote acoustic embeddings are ever serialized into outbound cloud requests.
  - **Sanitized Glossary Boosting (`boosted_lm_words`):**
    - *When Vault is Unlocked:* The Egress Guard cross-checks the user's custom glossary against all enrolled speaker names and automatically strips any term matching an enrolled identity.
    - *When Vault is Locked:* Glossary boosting for cloud transcription is completely blocked unless the user explicitly acknowledges an on-screen warning and confirms transmission.
  - **Offline Isolation for Local Models:** Local PyTorch Whisper and PyAnnote inference run with `HF_HUB_OFFLINE=1` and `TRANSFORMERS_OFFLINE=1`, ensuring local inference never initiates outbound telemetry or update queries.
- **Adversary Cannot:**
  - Associate spoken audio chunks with enrolled speaker identities, extract stored voiceprints, or construct speaker profiles across distinct events.

---

### Adversary E: Local LLMs, AI Agents & Code Indexers
- **Profile:** Local AI coding assistants (e.g. Copilot, local Ollama instances, IDE indexers, git status tools) recursively scanning workspace directories (`D:\Session_Scribe\`) and event folders (`D:\Events\`).
- **Attacker Capabilities:**
  - Recursive file discovery, string searching, tokenization, and context injection of all files within indexed folders.
- **Mitigations in Session Scribe v2:**
  - **Complete Relocation of Vault:** The entire biometric and identity repository is stored outside the workspace root and outside the event folder, residing solely in `%LOCALAPPDATA%\SessionScribe\vault\v2\`.
  - **Anonymous Compute-Zone Artifacts:** State files (`clip_state.json`), logs (`session_scribe.log`), CSVs (`attempts.csv`, `runs.csv`), and manifests contain only `S01`, `S02`, etc.
  - **Strict .gitignore Enforcement:** All transient scratch folders, logs, and user cache trees are excluded from version control.
- **Adversary Cannot:**
  - Discover speaker identities, voice embeddings, or consent records by scanning or indexing the code repository or event data folders.

---

## 5. Honest Limitations & Operational Boundaries

A secure architecture requires absolute transparency regarding what the system can and cannot guarantee:

1. **Spoken Names in Transcribed Audio ("Thank You, Alice"):**
   - Transcripts inherently record words uttered by speakers. If speaker A says *"Thank you, Alice, for that introduction"*, the text *"Alice"* appears naturally in the raw transcript text.
   - *Mitigation:* Session Scribe provides an opt-in, 100% local spoken-name scrubber that can mask detected names prior to cloud transmission or document export. However, because natural language processing is probabilistic, the system **cannot guarantee 100% masking of spoken names**. Users are explicitly notified of this boundary in the UI.
2. **Same-User Process Memory Access (Co-Privileged Malware):**
   - Under the Windows security architecture, any process executing under the same user token possesses `PROCESS_VM_READ` rights to other processes of equal integrity unless Protected Process Light (PPL) or kernel virtualization is employed. If malware is running concurrently while the vault is actively unlocked, it can read memory.
   - *Mitigation:* The 30-minute auto-lock, workstation lock hook, and explicit memory zeroing (`ctypes.memset`) aggressively minimize the exposure window.
3. **CPython Memory Lifecycle & OS Paging:**
   - CPython manages memory via private heaps and garbage collection. Immutable objects (such as `bytes` and `str`) cannot always be guaranteed zeroed immediately upon deallocation.
   - *Mitigation:* Cryptographic keys are maintained as mutable bytearrays or managed via native C buffers (`cryptography` library primitives) and explicitly wiped via memset routines. OS pagefile encryption (BitLocker) is recommended in documentation for defense against cold pagefile analysis.
4. **No Backdoor Recovery:**
   - In accordance with zero-knowledge cryptographic principles, Session Scribe v2 eliminates all "recovery exports" or master override keys. If the user loses their passphrase, **data in the vault is permanently lost by design**.
5. **TOTP is an Access-Control Layer (Not Offline Cryptographic Factor):**
   - The TOTP secret key is encrypted and stored wrapped under the vault master key in `totp.dat`. Therefore, TOTP operates strictly as an application-level access-control gate during live interactive sessions (protecting profile deletion, replacement, modification, bulk purge, passphrase changes, and TOTP resets). It is **not** a separate cryptographic key derivation factor against an offline attacker who steals the vault files. If an attacker cracks the master passphrase offline on a machine with identical DPAPI keys, they recover the master key and can unwrap the TOTP secret. For true cryptographic multi-factor hardware derivation offline, future roadmaps evaluate a **FIDO2 `hmac-secret`** hardware token (e.g. YubiKey).
6. **Crypto-Shredding Finality & External Storage Copies:**
   - Crypto-shredding eradicates the per-person encryption keys, overwrites ciphertext files with random bytes, and unlinks them. However, crypto-shredding is final and complete **only if no earlier copy of the vault exists**. If Windows Volume Shadow Copies (VSS), Windows System Restore points, full-disk image backups, cloud synchronization tools (OneDrive, Dropbox), or unencrypted legacy migration backups contain earlier versions of the vault or audio, those snapshots remain potentially readable. Users must ensure backup retention policies and BitLocker full-disk encryption align with their privacy requirements.
7. **Local Audit Mirror Limitations Prior to External (n8n) Sync:**
   - The local head mirror (`audit_head.mirror`) is written to an independent directory outside the vault root (`%LOCALAPPDATA%\SessionScribe\audit_mirror\`). While separating the mirror outside the vault root mitigates accidental directory wipes or localized single-folder tampering, any local mirror residing on the same filesystem remains weak against malware running under the same Windows user account. Cryptographic audit integrity is fully guaranteed only once the remote external mirror (e.g. automated n8n webhook notification) receives and persists the head hash off-machine.
8. **Exclusion of Quarantined Profiles from Acoustic Matching and Name Resolution:**
   - When a profile is placed into 24-hour quarantine, it is strictly excluded from all operational pipelines. It does not appear in candidate lists, cannot be matched against diarization clusters by acoustic similarity, and cannot be resolved by name in manifests or exported transcripts. To the Compute Zone and daily operations, a quarantined profile is completely inert and invisible.

---

## 6. Threat Mitigation Matrix

| Threat ID | Threat Description | Severity | Mitigation Strategy | Residual Risk |
|:---|:---|:---:|:---|:---|
| **T-01** | Cold disk theft / laptop loss | Critical | Argon2id ($t=10, m=256\text{MB}, p=4$) + DPAPI + AES-256-GCM. | Negligible without passphrase + TPM DPAPI state. |
| **T-02** | Plaintext biometric harvesting | High | Complete migration into encrypted Vault v2; crypto-shredding legacy `.npy`/WAVs. | Zero plaintext on disk post-migration. |
| **T-03** | Cloud ASR identity profiling | High | Strict Egress Guard allowlist (audio chunks + language only); name-stripped glossary boosting. | Spoken names inside audio stream (residual T-07). |
| **T-04** | Accidental PII commit via AI tools | High | External vault path (`%LOCALAPPDATA%`); anonymous compute-zone state files. | None in repository or event directories. |
| **T-05** | Unauthorized biometric modification | High | AES-256-GCM authenticated encryption with Record ID bound into AAD. | Any ciphertext tampering triggers tag rejection. |
| **T-06** | Accidental or malicious vault wipe | High | Multi-factor re-auth (passphrase + TOTP) + typed phrase + 24-hour quarantine delay. | User authorized deletion during quarantine window. |
| **T-07** | Spoken names in transcript text | Medium | Opt-in local NER/scrubber; prominent user disclaimer. | NLP false-negatives (inherent acoustic limit). |
| **T-08** | Inactivity exposure while unattended | Medium | 30m idle timer, screensaver hook, and Windows session lock hook (`WM_WTSSESSION_CHANGE`). | Attacker physical access during active unlocked 30m window. |
| **T-09** | Identity correlation via appearance order | Medium | Enforce voice-only cosine similarity matching ($\ge 0.75$); remove all order-of-appearance linking. | Unenrolled speakers remain anonymous aliases (`S01`). |
| **T-10** | Untracked tampering or data export | Low | Cryptographically keyed MAC append-only audit log with HMAC-SHA256 and external head mirroring. | Truncation detectable via broken chain and external head hash mismatch. |

---

## 7. Approval & Sign-Off

This Threat Model constitutes the foundational specification for Phase V1. Application code implementation (Phase V2 through Phase V8) must strictly adhere to the invariants, cryptographic boundaries, and egress constraints defined herein.
