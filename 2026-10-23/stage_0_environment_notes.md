# Stage 0: Environment and Workspace Build Notes

**Date:** 2026-10-23  
**Workspace:** `D:\Session_Scribe\`  
**Shared Venv:** `D:\Session_Scribe\.venv`  
**Host Python:** `C:\Users\antho\AppData\Local\Programs\Python\Python311\python.exe` (Python 3.11.9, 64-bit MSC v.1938)  
**Target Hardware:** NVIDIA GeForce RTX 3050 Laptop GPU (4 GB VRAM, compute capability 8.6)  

---

## 1. Background & Root Cause Analysis

In the earlier setup (e.g., the `18. Mongo DB` event environment):
1. A standard `pip install` pulled the default CPU-only PyTorch build from PyPI (`torch==2.14.1` / standard wheels).
2. The user force-installed `torch==2.4.1+cu121` and `torchaudio==2.4.1+cu121` from the PyTorch CUDA index (`https://download.pytorch.org/whl/cu121`).
3. That left a known pip dependency conflict: `speechbrain 1.1.2` declares `torch >= 2.7.1` and `torchaudio >= 2.7.1` in its package metadata.
4. Because the original requirements file lacked explicit torch pins and CUDA index specifications, subsequent installs or freezes could silently fall back to CPU torch or produce dependency solver failures.

---

## 2. Speechbrain Conflict Resolution

We investigated speechbrain package metadata across PyPI releases:
- `speechbrain==1.1.2`: Requires `torch >= 2.7.1` and `torchaudio >= 2.7.1` (causing a direct conflict with `torch==2.4.1`).
- `speechbrain==1.1.1`: Requires `torch >= 2.1.0` and `torchaudio >= 2.1.0`.
- `speechbrain==1.0.2`: Requires `torch >= 1.9` and `torchaudio`.

`pyannote.audio==3.1.1` specifies `speechbrain >= 0.5.14`.  
**Resolution Decision:** Pin `speechbrain==1.1.1`.
- `speechbrain==1.1.1` perfectly satisfies `pyannote.audio 3.1.1` (`>=0.5.14`) AND `torch 2.4.1+cu121` (`>=2.1.0`).
- No monkey-patching or ignored dependencies required.
- `pip check` reports: **"No broken requirements found."**

---

## 3. Preserved Version Pins

All required pins have been strictly verified and locked:
| Package | Version | Notes |
| :--- | :--- | :--- |
| `torch` | `2.4.1+cu121` | CUDA 12.1 GPU build |
| `torchaudio` | `2.4.1+cu121` | CUDA 12.1 build |
| `numpy` | `1.26.4` | Kept fixed as required |
| `pyannote.audio` | `3.1.1` | Kept fixed as required |
| `pyannote.core` | `5.0.0` | Kept fixed as required |
| `huggingface_hub` | `0.26.5` | Kept fixed as required |
| `openai-whisper` | `20250625` | Kept fixed as required |
| `soundfile` | `0.14.0` | Installed |
| `speechbrain` | `1.1.1` | Resolved conflict with torch 2.4.1 |

---

## 4. Constraints and Lock File Strategy

Two critical configuration files are placed in `D:\Session_Scribe\`:
1. `constraints.txt`:
   ```
   --extra-index-url https://download.pytorch.org/whl/cu121
   torch==2.4.1+cu121
   torchaudio==2.4.1+cu121
   speechbrain==1.1.1
   ```
   *Guarantees pip cannot accidentally substitute CPU torch during any subsequent package additions.*

2. `requirements-lock.txt`:
   Full frozen dependency list with `--extra-index-url https://download.pytorch.org/whl/cu121` prepended.

---

## 5. Acceptance Check Verification

All Stage 0 acceptance checks were executed and passed:

1. **CUDA Availability & RTX 3050 Detection:**
   - `torch.cuda.is_available()`: `True`
   - `torch.__version__`: `2.4.1+cu121`
   - `torch.cuda.get_device_name(0)`: `NVIDIA GeForce RTX 3050 Laptop GPU`
   - Device Compute Capability: `(8, 6)`
   - VRAM: ~3.3 GB free detected before model loading

2. **Whisper Medium GPU Loading:**
   - Model loaded successfully via `whisper.load_model("medium", device="cuda")`
   - Confirmed loaded on `cuda:0`
   - Cache successfully freed with `del model` and `torch.cuda.empty_cache()`

3. **Pyannote Audio Pipeline Import:**
   - `from pyannote.audio import Pipeline` executed cleanly without runtime error.

4. **Clean Replay in Throwaway Virtual Environment:**
   - Created a clean throwaway venv.
   - Installed `requirements-lock.txt`.
   - `pip check` verified: `No broken requirements found.`
   - Verified `torch.cuda.is_available()` returns `True` and detects `NVIDIA GeForce RTX 3050 Laptop GPU` with `2.4.1+cu121`.
   - Throwaway environment subsequently removed.

5. **External Tools & Language Pack Verification:**
   - `ffmpeg` resolved dynamically from PATH at: `C:\ffmpeg\ffmpeg-2026-08-09-git-6bbc22dc09-full_build\ffmpeg-2026-08-09-git-6bbc22dc09-full_build\bin\ffmpeg.EXE`
   - `ffprobe` resolved dynamically from PATH at: `C:\ffmpeg\ffmpeg-2026-08-09-git-6bbc22dc09-full_build\ffmpeg-2026-08-09-git-6bbc22dc09-full_build\bin\ffprobe.EXE`
   - `tesseract` located at: `C:\Program Files\Tesseract-OCR\tesseract.exe`
   - Language packs verified in `tessdata`: `['eng', 'osd']` (English and Orientation/Script Detection present; Hindi `hin.traineddata` not yet installed).
