"""
scripts/investigate_nvidia.py

Checks if credit balance is readable from API.
"""

import os
import urllib.request
import urllib.error
import json

def get_api_key() -> str:
    key = os.environ.get("NVIDIA_API_KEY", "").strip()
    if not key and os.path.exists(".env"):
        with open(".env", "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("NVIDIA_API_KEY="):
                    key = line.split("=", 1)[1].strip().strip('"').strip("'")
                    break
    return key

def main():
    api_key = get_api_key()
    endpoints = [
        "https://api.nvcf.nvidia.com/v2/nvcf/credits",
        "https://api.nvcf.nvidia.com/v2/nvcf/authorizations",
        "https://api.nvcf.nvidia.com/v2/nvcf/user",
        "https://integrate.api.nvidia.com/v1/user",
    ]
    for url in endpoints:
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {api_key}"})
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                print(f"{url} -> HTTP {resp.status}: {resp.read().decode('utf-8')[:200]}")
        except urllib.error.HTTPError as e:
            print(f"{url} -> HTTP {e.code}")
        except Exception as e:
            print(f"{url} -> {e}")

if __name__ == "__main__":
    main()
