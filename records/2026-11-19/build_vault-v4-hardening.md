# Build Record — Phase V4 Voice Profile Enrollment Hardening & Acoustic Quality Calibration

- **Date:** 2026-11-19
- **Type:** `build`
- **Status:** Complete (Zero commits, synthetic test verification, full regression passed)

---

## 1. What was asked

The user conditionally approved Phase V4 and requested comprehensive hardening before commencing Phase V5:
1. **Quality Checker:** Replace custom music and overlap heuristics with Pyannote's neural diarization (`pyannote/speaker-diarization-3.1`), voice-activity detection (VAD), and overlapped-speech duration measurement. Enforce single-speaker requirement. Retain custom signal analysis for clipping ratio, loudness (RMS), and SNR. Enforce 3-tier verdicts: `CLEAR_PASS`, `BORDERLINE` (displaying numbers and requiring explicit user override "Accept Anyway", logged to the Keyed-MAC Audit Log), and `CLEAR_FAIL` (unconditional rejection).
2. **Calibration CLI Tool:** Create `scripts/voice_quality_report.py`, taking an audio or video file and optional time range (`--start`, `--end`), printing all measured values and the verdict without storing anything or writing audio to disk.
3. **Cross-Sample Consistency:** Flag any sample whose embedding vector noticeably diverges ($< 0.65$ cosine similarity) from other samples in the enrollment session and require explicit user confirmation.
4. **Duplicate-Profile Warning:** Detect when a new voice is very similar ($\ge 0.82$ cosine similarity) to an existing vault profile (display matching profile name when vault is unlocked; display anonymous `spk_` ID when locked/unresolved).
5. **Embedding Model Versioning & Re-Embedding Path:** Store model name and version with every biometric embedding, and provide a re-embed path (`re_embed_all_profiles()`) to recalculate embeddings directly from encrypted reference samples.
6. **Wordlist Specification:** Clarify the exact wordlist, size, and bits per word (BIP-39 English Wordlist, 2,048 words, 11 bits/word).
7. **App Wiring:** Connect `VoiceProfileEnrollmentDialog` to the main GUI (header bar next to vault chip and Stage 2 Tab next to manifest controls), strictly requiring an unlocked vault before opening.
8. **Scratch Cleanup:** Remove loose scratch files and confirm `scratch/` is git-ignored.
9. **Gate:** Stop and report before starting Phase V5.

---

## 2. Why

1. **Acoustic Reliability:** Simple heuristics (e.g. raw spectral flux or stationary pitch thresholds) can be brittle across diverse acoustics. Pyannote's neural models provide state-of-the-art speaker diarization, VAD, and overlap classification.
2. **Overriding Edge Cases Safely:** Field audio may feature minor secondary interjections or sub-optimal acoustic environments. Allowing an explicit, auditable override for borderline samples avoids blocking operators while maintaining accountability in the Keyed-MAC audit trail.
3. **Preventing Dirty Profile Contamination:** Inadvertently importing a clip from a different speaker or accidentally re-enrolling an existing person under a duplicate identity damages speaker diarization accuracy. Cross-sample consistency checks and duplicate warnings protect library integrity.
4. **Model Evolution / Future-Proofing:** Machine learning models improve over time. Storing reference audio encrypted in Zone 2 allows the app to upgrade the embedding engine without needing re-recording.

---

## 3. What was built (user-visible behaviour)

1. **Acoustic Quality Checker (`scribe_core/voice_quality_checker.py`):**
   - Integrates Pyannote neural diarization (`pyannote/speaker-diarization-3.1`) and VAD.
   - Measures speech duration, speaker count, overlapping speech duration, clipping ratio, and SNR.
   - Categorizes samples into:
     - `CLEAR_PASS`: $\ge 15.0$s speech, 1 speaker, overlap $\le 0.3$s, $\text{SNR} \ge 12.0$ dB, clipping $\le 0.5\%$.
     - `BORDERLINE`: 10–15s speech, 1 speaker (or secondary speech $< 1.0$s), overlap 0.3–1.5s, SNR 9–12 dB, clipping 0.5–2.0%. Presents numbers and enables "⚠️ Accept Anyway (Override Borderline)".
     - `CLEAR_FAIL`: $< 10.0$s speech, 0 or $> 2$ speakers, overlap $> 1.5$s, SNR $< 9$ dB, clipping $> 2.0\%$. Unconditionally rejected.
2. **Quality Calibration CLI (`scripts/voice_quality_report.py`):**
   - Command-line tool for assessing audio/video files without writing audio to disk.
   - Supports `--start` and `--end` second offsets.
   - Outputs comprehensive acoustic breakdown and final quality classification.
3. **Cross-Sample Consistency & Duplicate Warnings (`scribe_core/enrollment_manager.py`):**
   - Scans staged samples; if a sample's embedding similarity to the session centroid is $< 0.65$, flags a warning and pauses for user confirmation.
   - Compares incoming voice embeddings against all enrolled profiles in Zone 2. If similarity $\ge 0.82$, shows a duplicate profile warning (showing real name when unlocked; anonymous `spk_` ID when locked).
4. **Biometric Model Versioning & Re-Embedding Path:**
   - Biometric records (`.bio.enc`) record `model`, `model_version`, and `dimension`.
   - `re_embed_all_profiles(new_embedding_engine)` recalculates embeddings from Zone 2 encrypted WAVs and records `PROFILES_RE_EMBEDDED` with target `ALL_PROFILES` in the Keyed-MAC audit log.
5. **UI Accessibility & Vault Unlocked Guard:**
   - Added `🎙️ Enroll Voice...` button to header bar and `🎙️ Enroll Voice Profile...` button in Stage 2 tab.
   - Both locations check `vault.is_unlocked()`. If locked, enrollment dialog refuses to open and displays a warning prompt.
   - Added "⚠️ Accept Anyway (Override Borderline)" action and duplicate warning banners in `gui/enrollment_dialog.py`.

---

## 4. How to use it

### Calibration Tool (CLI)
Run the calibration CLI on any media file to evaluate acoustic quality:
```powershell
.\.venv\Scripts\python.exe scripts/voice_quality_report.py "D:\Events\Sample_Event\Audio\Clip01.wav" --start 10.0 --end 40.0
```

### Voice Profile Enrollment (GUI)
1. Ensure the Voice Vault is unlocked (green chip in the header bar).
2. Click **🎙️ Enroll Voice...** in the top header or **🎙️ Enroll Voice Profile...** in Stage 2.
3. Enter speaker's real name and confirm auditable consent.
4. Browse or enter path to an audio/video clip, specifying optional time range.
5. Click **Add Sample**.
   - If `CLEAR_PASS`, sample stages automatically.
   - If `BORDERLINE`, numbers and reasons appear; click **⚠️ Accept Anyway (Override Borderline)** to approve.
   - If duplicate profile detected, orange banner alerts the operator with the existing profile name.
6. Once $\ge 3$ verified samples are approved, click **Commit Profile to Vault**.

---

## 5. Settings and defaults

| Parameter | Default Value | Notes |
|:---|:---:|:---|
| Minimum Speech Duration (Pass) | 15.0 s | Speech detected by neural VAD |
| Borderline Speech Duration | 10.0 s – 15.0 s | Allowed with explicit user override |
| Hard Rejection Speech Duration | < 10.0 s | Clear failure |
| Minimum Clean SNR | $\ge 12.0$ dB | Custom acoustic energy analysis |
| Borderline SNR | 9.0 dB – 12.0 dB | Allowed with explicit override |
| Hard Noise SNR Limit | < 9.0 dB | Clear failure |
| Max Overlap Duration (Pass) | $\le 0.3$ s | Pyannote powerset / segment intersection |
| Borderline Overlap Duration | 0.3 s – 1.5 s | Allowed with explicit override |
| Hard Overlap Rejection | > 1.5 s | Clear failure |
| Cross-Sample Inconsistency Threshold | < 0.65 cosine sim | Flags diverging sample within session |
| Duplicate Profile Warning Threshold | $\ge 0.82$ cosine sim | Scans all enrolled profiles in Zone 2 |
| Wordlist | BIP-39 English (2,048 words) | 11.0 bits per word (6 words = 66 bits entropy) |

---

## 6. Files changed

| File | Nature of Change |
|:---|:---|
| `scribe_core/voice_quality_checker.py` | Integrated Pyannote neural diarization/VAD, 3-tier verdicts (`CLEAR_PASS`, `BORDERLINE`, `CLEAR_FAIL`), custom SNR/clipping/RMS analysis. |
| `scripts/voice_quality_report.py` | Standalone calibration CLI tool with in-memory audio extraction, zero disk writes, and detailed metric reporting. |
| `scribe_core/vault_v2.py` | Added `model_version: str = "1.0.0"` tracking to biometric records and indexes. |
| `scribe_core/embedding_engine.py` | Added `model_version: str = "1.0.0"` attribute. |
| `scribe_core/enrollment_manager.py` | Added duplicate profile scanning ($\ge 0.82$), cross-sample consistency tracking ($< 0.65$), borderline override logging to audit log, and `re_embed_all_profiles()`. |
| `scribe_core/wordlist.py` | Documented and exported `WORDLIST_SPEC` for the BIP-39 English Wordlist (2,048 words, 11 bits/word). |
| `gui/enrollment_dialog.py` | Added unlocked vault security guard, duplicate profile banner, "Accept Anyway" borderline override flow, and cross-sample inconsistency prompt. |
| `gui/stage2_tab.py` | Added `btn_enroll_voice` button and unlocked vault check. |
| `gui/app.py` | Added `btn_enroll` button to header bar and unlocked vault check. |
| `tests/test_vault_v4_enrollment.py` | Expanded test suite to 12 tests covering duration, noise, borderline override, multi-speaker, canary scan, UI stability, consistency, duplicate warning, re-embed path, and vault lock guard. |

---

## 7. Limitations and known issues

- Pyannote diarization model relies on PyTorch / TorchAudio. On machines without GPU acceleration, neural segmentation takes approximately 2–4 seconds per 30-second audio sample.
- If PySide6 is not installed on the system, Qt prototype tests are skipped (8 skipped tests documented). Tkinter implementation is active and fully functional.

---

## 8. How it was verified

1. **V4 Unit Tests:**
   ```powershell
   .\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_vault_v4_enrollment.py"
   ```
   *Result:* 12 passed in 39.595s, 0 failures, 0 errors.
2. **Full Test Suite Regression:**
   ```powershell
   .\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py"
   ```
   *Result:* 246 passed in 147.955s, 8 skipped (Qt prototype), 0 failures, 0 errors.
3. **Secret Scan:**
   ```powershell
   .\.venv\Scripts\python.exe scripts/secret_scan.py
   ```
   *Result:* 0 secret occurrences found across all repository files.

---

## 9. How to roll it back

Revert changes to `scribe_core/voice_quality_checker.py`, `scripts/voice_quality_report.py`, `scribe_core/enrollment_manager.py`, `gui/enrollment_dialog.py`, `gui/stage2_tab.py`, and `gui/app.py`.
