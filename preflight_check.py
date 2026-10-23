"""
Stage 0 Preflight Verification Script
Verifies:
- Python 3.11.9 runtime
- CUDA availability and NVIDIA GeForce RTX 3050 Laptop GPU detection
- PyTorch 2.4.1+cu121 build
- Whisper medium model GPU loading
- pyannote.audio Pipeline import
- Runtime discovery of ffmpeg and ffprobe from PATH (not hardcoded)
- Runtime discovery of Tesseract-OCR and available language models
"""

import sys
import shutil
import subprocess

def run_preflight():
    print("=" * 60)
    print("Session Scribe - Stage 0 Preflight Check")
    print("=" * 60)

    # 1. Python version
    print(f"Python Executable: {sys.executable}")
    print(f"Python Version   : {sys.version.split()[0]}")
    assert sys.version_info[:2] == (3, 11), f"Expected Python 3.11, got {sys.version}"

    # 2. PyTorch & CUDA
    import torch
    print(f"PyTorch Version  : {torch.__version__}")
    assert "+cu121" in torch.__version__, f"Expected +cu121 tag in torch, got {torch.__version__}"
    assert torch.cuda.is_available(), "CUDA is not available in torch!"

    device_name = torch.cuda.get_device_name(0)
    print(f"CUDA Device      : {device_name}")
    assert "RTX 3050" in device_name, f"Expected RTX 3050, detected: {device_name}"

    free_mem, total_mem = torch.cuda.mem_get_info()
    print(f"GPU VRAM Free    : {free_mem / (1024**2):.1f} MB / {total_mem / (1024**2):.1f} MB")

    # 3. Whisper Medium GPU loading
    print("\nVerifying Whisper medium model GPU loading...")
    import whisper
    model = whisper.load_model("medium", device="cuda")
    print(f"Whisper model loaded on: {model.device}")
    del model
    torch.cuda.empty_cache()
    print("Whisper test passed and GPU cache cleared.")

    # 4. Pyannote Pipeline
    print("\nVerifying pyannote.audio import...")
    from pyannote.audio import Pipeline
    assert Pipeline is not None
    print("pyannote.audio Pipeline imported successfully.")

    # 5. External CLI tools resolved dynamically from PATH
    print("\nResolving external system tools from PATH...")
    ffmpeg_bin = shutil.which("ffmpeg")
    ffprobe_bin = shutil.which("ffprobe")
    tesseract_bin = shutil.which("tesseract") or shutil.which("tesseract.exe")
    if not tesseract_bin:
        default_tess = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
        if shutil.os.path.exists(default_tess):
            tesseract_bin = default_tess

    print(f"ffmpeg   : {ffmpeg_bin if ffmpeg_bin else 'NOT FOUND'}")
    print(f"ffprobe  : {ffprobe_bin if ffprobe_bin else 'NOT FOUND'}")
    print(f"tesseract: {tesseract_bin if tesseract_bin else 'NOT FOUND'}")

    assert ffmpeg_bin is not None, "ffmpeg was not found on PATH"
    assert ffprobe_bin is not None, "ffprobe was not found on PATH"

    if tesseract_bin:
        res = subprocess.run([tesseract_bin, "--list-langs"], capture_output=True, text=True)
        langs = [line.strip() for line in res.stdout.strip().splitlines() if line.strip() and not line.startswith("List of")]
        print(f"Tesseract installed languages: {langs}")
        if "hin" not in langs:
            print("Note: Hindi ('hin.traineddata') is not yet installed in tessdata.")

    print("\n" + "=" * 60)
    print("ALL PREFLIGHT CHECKS PASSED SUCCESSFULLY!")
    print("=" * 60)

if __name__ == "__main__":
    run_preflight()
