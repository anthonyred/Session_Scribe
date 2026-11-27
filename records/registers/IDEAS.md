# Ideas Register

Parked ideas, future exploration, and decoupled tooling proposals.

---

## 1. Cloud & GPU Resource Monitoring Tool
- **Status:** Parked / Decoupled
- **Description:** Separate monitoring app: per-file cloud API usage and local compute/GPU power draw, using Session Scribe’s per-clip state data and nvidia-smi power readings. Not part of Session Scribe.
- **Data Sources:**
  - Per-clip state recorded in `<event root>\scribe\state\clip_state.json` and `session_state.json` (engine used, audio duration, processing time, failover status).
  - Real-time `nvidia-smi` queries for GPU power draw (watts), GPU utilization (%), and VRAM allocation.
- **Notes:** Keep strictly decoupled from Session Scribe core application. No database or daemon required inside Session Scribe.

## 2. Dedicated In-App Usage Tab, Credits Card, and In-App GPU Power/Energy Sampling
- **Status:** Parked
- **Description:** A standalone top-level Usage tab with visual throughput KPI cards, cloud credits consumption/sync tracking, and real-time in-app GPU power/watt-hour energy estimation.
- **Reason Parked:** The free-tier NVIDIA build.nvidia.com plan operates strictly under a rate-limit quota (up to 40 rpm) with no credit balance or sync endpoint. Full usage observability is directly integrated into Stage 3 via UTF-8 with BOM CSV files (`runs.csv`, `attempts.csv`, `account_attempts.csv`) and the Stage 3 Activity Drawer ("Selected clip" tab).

## 3. Network Egress Guard & Kernel Socket Restriction Layer
- **Status:** Parked
- **Description:** Enforce OS-level or sub-process firewall/socket filtering to physically prevent local neural compute workers (Whisper, Pyannote, PyTorch) from making unauthorized outbound TCP/UDP connections.
- **Reason Parked:** Core application operates purely locally on user media by default; cloud transcription is an explicit opt-in with strict per-host endpoint whitelisting (`grpc.nvcf.nvidia.com`). Additional OS-level socket filtering adds significant platform coupling without practical benefit for typical desktop environments.

## 4. Historical Event Bulk Migration Utility
- **Status:** Parked
- **Description:** Automated migration scanner to batch-convert legacy event manifests (`manifest.json`) containing plaintext speaker names into Vault v2 per-event alias structures (`S01`, `S02`) and generate encrypted Vault v2 alias maps.
- **Reason Parked:** Backward compatibility shim in `scribe_core/manifest.py` and `scribe_core/session_grouper.py` already seamlessly accepts and processes legacy un-aliased events end-to-end without requiring disruptive bulk migrations of user archives.

## 5. FIDO2 / WebAuthn Hardware Authenticator & TPM 2.0 Integration
- **Status:** Parked
- **Description:** Hardware-backed cryptographic keys (YubiKey / FIDO2 `hmac-secret` extension) and TPM 2.0 PCR hardware binding for master key derivation.
- **Reason Parked:** Existing RFC 9106 Argon2id passphrase derivation + Windows DPAPI user-login credential binding + software RFC 6238 TOTP 2FA provide robust, zero-server defense-in-depth on desktop Windows without requiring physical dongles or TPM provisioning.


