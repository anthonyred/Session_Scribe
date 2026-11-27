# Session Scribe — Work Records

This folder (`records/`) contains all architectural, feature, bug, and change records for the Session Scribe repository.

---

## How Records Work

1. **Location:** Everything under `records/` lives inside a folder: either a dated folder (`records/YYYY-MM-DD/`) or `records/registers/`. Never place loose files or dated folders at the repository root.
2. **Markdown Only:** Dated folders strictly contain markdown files (`.md`). No scripts, code, logs, or binaries.
3. **Naming Scheme:** `<type>_<short-title>.md`, where `<type>` is one of:
   - `build`: Initial creation of components or infrastructure.
   - `bug`: Investigation, root cause, and fix for issues.
   - `feature`: Enhancements and new user-facing functionality.
   - `change`: Refactors, migrations, or housekeeping without new features.
4. **Daily Indexes:** Every dated subfolder contains an `INDEX.md` listing that day's records.
5. **Registers:**
   - [FEATURES.md](FEATURES.md): Comprehensive register of features, where they are used, and links to their records.
   - [BUGS.md](BUGS.md): Comprehensive register of bug fixes, symptoms, root causes, and links to their records.
   - [IDEAS.md](IDEAS.md): Register of parked ideas and decoupled tooling proposals.
   - `CHANGELOG.md` (at project root): Chronological one-line summaries linking into `records/`.

---

## Index of Days

| Date | Folder | Summary |
|------|--------|---------|
| 2026-10-23 | [2026-10-23/](../2026-10-23/INDEX.md) | Initial setup (Python 3.11, CUDA venv, Whisper medium, Media ingest, Stage 2/3 architecture) |
| 2026-10-25 | [2026-10-25/](../2026-10-25/INDEX.md) | Event selection, path isolation, flat layout support, manifest refresh, and skip logic |
| 2026-10-26 | [2026-10-26/](../2026-10-26/INDEX.md) | Housekeeping: standardized directory layout, moving test scripts into tests/ |
| 2026-10-28 | [2026-10-28/](../2026-10-28/INDEX.md) | Scrollable tabs, text shortcuts, speaker bulk mapping, and centralised clip_state.json |
| 2026-10-30 | [2026-10-30/](../2026-10-30/INDEX.md) | Cloud ASR engine, local fallback, in-app editor, approval gate, and session tracking |
| 2026-11-03 | [2026-11-03/](../2026-11-03/INDEX.md) | Records restructure, transcript Replace & Zoom, Stage 3 clip table refinements |
| 2026-11-07 | [2026-11-07/](../2026-11-07/INDEX.md) | Qt migration amendments (top tabs, adaptive tables, dark mode) and usage/quota ledger monitoring |
| 2026-11-13 | [2026-11-13/](../2026-11-13/INDEX.md) | Stage 3 live timers and engine display, Voice Vault (Argon2id + DPAPI), Tk table column fitting fix, and complete 12-section README.md |
| 2026-11-17 | [2026-11-17/](../2026-11-17/INDEX.md) | NVIDIA Cloud transcription fix, bundled 5s preflight test, Start Menu app & single-instance guard, Model Registry & compare suite, ReTranscribeDialog hot loop fix, Theme System v2 with modern inputs, Re-transcribe mock elimination & drawer sync, and Universal smooth scrolling |
| 2026-11-19 | [2026-11-19/](../2026-11-19/INDEX.md) | Security Overhaul & Voice Biometrics: Phase V1 Threat Model (`threat_model.md`), Phase V2 Cryptographic Core (`vault_crypto.py`), Phase V2b Dual-Zone Storage Engine (`vault_v2.py`), Phase V3 Destructive Flow & TOTP 2FA, Phase V4 Voice Profile Enrollment Engine & Quality Calibration, Phase V5 Stage 2 Session Model & Compute Zone Privacy Isolation, and Phase V6 Speaker Identification Engine & Optional Vault Decoupling |
| 2026-11-20 | [2026-11-20/](../2026-11-20/INDEX.md) | Post-V6 Verification & Hardening: Line-start speaker tag isolation, combined files deduplication, Re-Identify automated archiving with one-click undo, historical cluster discovery, and slow test watchdog limit (300s) |
| 2026-11-25 | [2026-11-25/](../2026-11-25/INDEX.md) | Start Menu Desktop App, Persistent Voice Centroids & Cluster Matching: `scribe\state\clusters\<clip>.json` voice centroid persistence, delayed unlock matching in Re-Identify, 256-dim real code path test, user-level Start menu shortcut (`pythonw.exe`), AppUserModelID, single instance, and About dialog |


