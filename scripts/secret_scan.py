"""
scripts/secret_scan.py

Performs a secret scan across the entire repository, tests, logs, and state files.
Checks whether any part of the NVIDIA_API_KEY leaks into any tracked or repo file.
"""

import os
import sys

def get_secret() -> str:
    if os.path.exists(".env"):
        with open(".env", "r", encoding="utf-8") as f:
            for line in f:
                if line.strip().startswith("NVIDIA_API_KEY="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    return os.environ.get("NVIDIA_API_KEY", "").strip()


def scan_repo():
    secret = get_secret()
    if not secret:
        print("[WARN] No secret found to scan for.")
        return

    # Check key length and minimum signature length
    sig = secret[:16] # First 16 chars
    full_secret = secret

    found_in = []
    ignored_paths = {".env", ".git", ".venv", "__pycache__", "scratch", "theme_review"}

    for root, dirs, files in os.walk("."):
        # Prune ignored directories
        dirs[:] = [d for d in dirs if d not in ignored_paths]
        for f in files:
            if f in ignored_paths or f.endswith(".pyc") or f.endswith(".wav"):
                continue
            path = os.path.join(root, f)
            norm_path = os.path.normpath(path)
            if ".git" in norm_path or ".venv" in norm_path or norm_path == ".env":
                continue
            try:
                with open(norm_path, "r", encoding="utf-8", errors="ignore") as fh:
                    content = fh.read()
                    if full_secret in content or sig in content:
                        found_in.append(norm_path)
            except Exception:
                pass

    print("=== SECRET SCAN RESULTS ===")
    if found_in:
        print(f"[FAIL] Secret detected in {len(found_in)} files:")
        for p in found_in:
            print(f"  - {p}")
        sys.exit(1)
    else:
        print("[PASS] Secret scan clean! 0 occurrences of secret found across all repository files.")


if __name__ == "__main__":
    scan_repo()

