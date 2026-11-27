import os
import sys
import subprocess

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from scribe_core import cloud_engine

def main():
    gui_key = cloud_engine.get_nvidia_api_key()
    gui_first_last = f"{gui_key[:2]}...{gui_key[-2:]}" if len(gui_key) >= 4 else "<EMPTY>"
    
    # Run a subprocess mimicking how worker resolves the key
    code = (
        "import os, sys\n"
        "sys.path.insert(0, os.path.abspath('.'))\n"
        "from scribe_core import cloud_engine\n"
        "k = os.environ.get('NVIDIA_API_KEY') or cloud_engine.get_nvidia_api_key()\n"
        "print(f'{k[:2]}...{k[-2:]}' if len(k) >= 4 else '<EMPTY>')\n"
    )
    res = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True
    )
    worker_first_last = res.stdout.strip()
    
    print(f"GUI key first/last two: {gui_first_last}")
    print(f"Worker key first/last two: {worker_first_last}")
    print(f"Do they match: {gui_first_last == worker_first_last and len(gui_key) > 0}")

if __name__ == "__main__":
    main()

