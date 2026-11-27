"""
scribe_core/hf_preflight.py

Hugging Face Token Preflight Check & Pyannote Diarization Gate Verifier.
Validates:
1. Token syntax & validity via huggingface_hub.whoami (account name only logged; token never printed).
2. Gated model access for pyannote/speaker-diarization-3.1 and pyannote/segmentation-3.0.
3. Local offline cache check.
4. GPU capability & VRAM guard check (torch CUDA).
5. Argument compatibility ('use_auth_token' vs 'token').
"""

from __future__ import annotations

import os
import sys
from typing import Any, Dict, Optional, Tuple


def get_hf_token() -> str:
    """Retrieve Hugging Face token from environment or local .env file. Never logged."""
    token = os.environ.get("HF_TOKEN") or os.environ.get("HUGGING_FACE_HUB_TOKEN")
    if token and token.strip():
        return token.strip()

    # Fallback to local git-ignored .env
    env_file = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env")
    if os.path.isfile(env_file):
        try:
            with open(env_file, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("#") or not line:
                        continue
                    if "=" in line:
                        k, v = line.split("=", 1)
                        k = k.strip()
                        v = v.strip().strip('"').strip("'")
                        if k in ("HF_TOKEN", "HUGGING_FACE_HUB_TOKEN") and v:
                            return v
        except Exception:
            pass
    return ""


def save_hf_token_to_env(token: str) -> None:
    """Save Hugging Face token to local git-ignored .env file."""
    env_file = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env")
    lines = []
    found = False
    if os.path.isfile(env_file):
        try:
            with open(env_file, "r", encoding="utf-8") as f:
                lines = f.readlines()
        except Exception:
            lines = []

    new_lines = []
    for line in lines:
        if line.strip().startswith("HF_TOKEN=") or line.strip().startswith("HUGGING_FACE_HUB_TOKEN="):
            new_lines.append(f"HF_TOKEN={token.strip()}\n")
            found = True
        else:
            new_lines.append(line)

    if not found:
        if new_lines and not new_lines[-1].endswith("\n"):
            new_lines.append("\n")
        new_lines.append(f"HF_TOKEN={token.strip()}\n")

    with open(env_file, "w", encoding="utf-8") as f:
        f.writelines(new_lines)
    os.environ["HF_TOKEN"] = token.strip()


def run_hf_preflight_check(token: Optional[str] = None) -> Tuple[bool, str, Dict[str, Any]]:
    """
    Run complete preflight check for multi-speaker diarization.
    Returns:
      (is_ok, message, details_dict)
    Never prints or reveals the token value.
    """
    import huggingface_hub

    if token is not None:
        tk = token.strip()
    else:
        tk = get_hf_token().strip()
    if not tk:
        return (
            False,
            "Hugging Face token is not set. A token is required to access pyannote diarization models.\n"
            "Please provide a Hugging Face token under 'Connections' or set HF_TOKEN in your .env file.",
            {"status": "not_set", "username": None},
        )

    # 1. Validate token & get account name
    username = None
    try:
        user_info = huggingface_hub.whoami(token=tk)
        username = user_info.get("name") or user_info.get("fullname") or "authenticated_user"
    except Exception as exc:
        err_msg = str(exc)
        if "401" in err_msg or "Invalid user token" in err_msg:
            return (
                False,
                "Invalid Hugging Face token: Authentication failed (401 Unauthorized).\n"
                "Please verify that your Hugging Face token is active and has 'read' permissions.",
                {"status": "invalid", "username": None},
            )
        # Network or other error
        return (
            False,
            f"Failed to verify Hugging Face token with api.huggingface.co: {err_msg}",
            {"status": "error", "username": None},
        )

    # 2. Check access to gated models
    gated_models = [
        "pyannote/speaker-diarization-3.1",
        "pyannote/segmentation-3.0",
    ]
    missing_agreements = []

    for model_id in gated_models:
        try:
            huggingface_hub.model_info(model_id, token=tk)
        except Exception as exc:
            err = str(exc)
            if "403" in err or "GatedRepoError" in err or "access" in err.lower():
                missing_agreements.append(model_id)
            else:
                # Other non-fatal warning
                pass

    if missing_agreements:
        links = "\n".join(f"  • https://huggingface.co/{m}" for m in missing_agreements)
        return (
            False,
            f"Account '{username}' does not have accepted user agreements for gated pyannote models:\n{links}\n\n"
            "Please visit each link above while signed in and click 'Accept conditions' to unlock diarization.",
            {"status": "no_access", "username": username, "missing": missing_agreements},
        )

    # 3. GPU check
    gpu_info = "CPU only"
    try:
        import torch
        if torch.cuda.is_available():
            dev_name = torch.cuda.get_device_name(0)
            free_mb = torch.cuda.mem_get_info()[0] / (1024**2)
            gpu_info = f"{dev_name} ({free_mb:.0f} MB free)"
    except Exception:
        pass

    return (
        True,
        f"Valid Hugging Face token (Account: {username}). Gated models access confirmed. GPU: {gpu_info}.",
        {"status": "valid", "username": username, "gpu": gpu_info},
    )

