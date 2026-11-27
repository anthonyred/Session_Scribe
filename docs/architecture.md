# Session Scribe — System Architecture & Data Flow

This document details the software architecture, component boundaries, cryptographic isolation zones, and data flow of **Session Scribe**.

---

## 1. High-Level Architecture Diagram

```mermaid
flowchart TD
    subgraph S1["1. Raw Media Ingest & Event Setup (Stage 2)"]
        RawMedia["Raw Event Media (MP4 / MOV / WAV)"] --> FFmpeg["FFmpeg Pipe (16 kHz Mono PCM)"]
        FFmpeg --> Grouper["Session Grouper (Gap Detection & Merge / Split)"]
        Grouper --> SessionModel["Session Model (Solo vs Panel Config)"]
    end

    subgraph S2["2. Compute Zone (Unencrypted Event Media)"]
        SessionModel --> Manifest["manifest.json (Opaque Event ID & Aliases S01, S02)"]
        ClipState["clip_state.json (Per-Clip Pipeline State)"]
        ClusterStore["scribe/state/clusters/ (256-dim Voice Centroids)"]
    end

    subgraph S3["3. Dual ASR Transcription Engines (Stage 3)"]
        VRAM["Hardware GPU Execution Guard (_VRAM_EXECUTION_LOCK)"] --- LocalASR
        VRAM --- CloudASR
        Manifest --> EngineRouter{"Engine Router"}
        EngineRouter -->|Local Mode| LocalASR["Local OpenAI Whisper (Medium / CUDA GPU)"]
        EngineRouter -->|Cloud Mode| CloudASR["NVIDIA Riva Cloud (Parakeet / Whisper Large-v3)"]
        CloudASR -.->|Failover / Disconnect| LocalASR
        LocalASR --> WordTimestamps["Aligned Word Timestamps & Text"]
        CloudASR --> WordTimestamps
    end

    subgraph S4["4. Diarization & Speaker Attribution"]
        WordTimestamps --> Diarizer["Pyannote Diarization 3.1 (VAD & Speaker Clustering)"]
        Diarizer --> TurnSmoother["Turn Smoother (Merge Islands < 0.7s, Protect Overlap)"]
        TurnSmoother --> ClusterCentroids["Per-Clip Cluster Centroids"]
        ClusterCentroids --> ClusterStore
    end

    subgraph S5["5. Voice Vault v2 (Encrypted Biometric Storage)"]
        MasterKey["Argon2id KDF + Windows DPAPI (User Passphrase)"]
        MasterKey --> Zone1["Zone 1: Identity Storage (Encrypted Alias Maps: evt_*.alias.enc)"]
        MasterKey --> Zone2["Zone 2: Biometric Storage (Voice Embeddings & Reference WAVs)"]
        TOTP["TOTP 2FA & Audit Log (Destructive Flow & 24h Quarantine)"] -.-> Zone1
    end

    subgraph S6["6. Identification & Late-Binding Resolution"]
        ClusterCentroids --> Matcher{"Closed-Set Matcher (Cosine Sim >= 0.65, Margin >= 0.08)"}
        Zone2 -.->|Unlocked Biometrics| Matcher
        Matcher -->|Confident Match| BoundSpeaker["Assigned Enrolled Speaker"]
        Matcher -->|Uncertain / Locked Vault| AnonymousAlias["Sequential Alias (S01, S02)"]
        Zone1 -.->|Late-Binding Resolver| TranscriptViewer["Transcript Viewer (Read / Edit / Find-Replace / Zoom)"]
        ReIdentify["Re-Identify Flow (Automated Backups & Undo)"] --> TranscriptViewer
    end

    subgraph S7["7. Review, Approval & Collation"]
        TranscriptViewer --> ApprovalGate{"User Approval Gate (Approve Selected / All)"}
        ApprovalGate -->|Approved| CollationEngine["Chronological Collation Engine"]
        CollationEngine --> CombinedFiles["scribe/combined/ (<Speaker>_combined.txt)"]
    end
```

---

## 2. Core Architectural Principles

### A. Privacy-First Compute Zone Isolation
* **Zero Names on Event Media Disks:** Compute-zone files (`manifest.json`, `clip_state.json`) store only sequential aliases (`S01`, `S02`, ...) and an opaque `event_id` (`evt_<16 hex chars>`).
* **Vault-Encrypted Identity Maps:** True speaker names and biometric profile identifiers (`spk_...`) are stored exclusively inside Vault v2 Zone 1 (`%LOCALAPPDATA%\SessionScribe\vault\v2\alias_maps\<event_id>.alias.enc`), encrypted under the user's master key.
* **Order-of-Appearance Elimination:** Unmatched speech turns are never attributed to roster speakers by accidental file order or clip sequence. Unidentified turns remain explicitly tagged as `[Unidentified]`.

### B. Hardware & Resource Isolation
* **VRAM Execution Lock (`_VRAM_EXECUTION_LOCK`):** Local Whisper speech recognition and Pyannote neural diarization alternate GPU execution sequentially. They never allocate CUDA memory concurrently, ensuring smooth execution within 4 GB to 8 GB consumer GPU VRAM limits.
* **In-Memory Audio Processing:** Audio chunks extracted for voice enrollment and snippet listening stream directly through memory pipes via FFmpeg. Raw unencrypted reference audio never touches persistent disk storage.

### C. Decoupled Workflow & Optional Vault
* **Non-Blocking Operation:** Stage 2 media ingest, Stage 3 ASR transcription, manual transcript editing, user approval gates, and file collation operate seamlessly whether the Voice Vault is locked, unlocked, or has never been initialized.
* **Late-Binding Name Resolution:** Decrypted speaker names are resolved dynamically in memory only when viewing transcripts with an unlocked vault or generating final combined text files.
* **Persistent Voice Centroids:** Diarization outputs 256-dimensional unit acoustic centroids stored in `scribe/state/clusters/<clip>.json`. This enables in-place Re-Identification across app restarts whenever the vault is unlocked later.

### D. Dual Transcription & Automatic Failover
* **Local Engine:** Local OpenAI Whisper Medium running via PyTorch CUDA. Completely offline with zero external bandwidth.
* **Cloud Engine:** Ephemeral streaming transcription via NVIDIA Riva gRPC (Parakeet TDT / Whisper Large-v3) achieving 10× to 15× real-time speeds with automatic transparent failover to local Whisper on network interruptions.
* **Midpoint Checkpoint Resume:** Session state checkpoints every 30 seconds allow interrupted jobs to resume from the last boundary without restarting the clip.

---

## 3. Cryptographic Hierarchy (Voice Vault v2)

```mermaid
flowchart TD
    Passphrase["User Passphrase"] --> Argon2id["Argon2id KDF (RFC 9106)"]
    Argon2id --> MasterKey["Vault Master Key (256-bit AES-GCM)"]
    DPAPI["Windows DPAPI (CryptProtectData)"] --> MasterKey
    
    MasterKey --> KeyZ1["Zone 1 Key (Identity)"]
    MasterKey --> KeyZ2["Zone 2 Key (Biometrics)"]
    
    KeyZ1 --> IndexEnc["Padded Index (4096-byte blocks)"]
    KeyZ1 --> AliasMaps["Event Alias Mappings (*.alias.enc)"]
    
    KeyZ2 --> Embeddings["Voice Embeddings (*.emb.enc)"]
    KeyZ2 --> AudioSamples["Encrypted Audio References (*.wav.enc)"]
    
    AuditKey["HMAC-SHA256 Key"] --> AuditLog["Append-Only Chained Audit Log"]
```

* **Zone 1 (Identity):** Profile metadata, display names, roles, consent timestamps, and per-event alias mappings.
* **Zone 2 (Biometrics):** 256-dimensional PyAnnote voice embeddings and AES-256-GCM encrypted audio snippets.
* **Audit Trail:** Monotonic sequence counter and HMAC-SHA256 signature chain with external head mirror.
