"""
scribe_core/model_registry.py

Model registry and capabilities catalog for Session Scribe.
Defines supported local and cloud transcription engines, VRAM constraints
(specifically for 4 GB GPUs), rate limits, and cost estimation metrics.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class ModelInfo:
    id: str
    name: str
    provider: str  # "local" | "nvidia"
    engine_type: str  # "whisper" | "parakeet" | "nemotron"
    vram_mb: int
    fits_4gb_gpu: bool
    is_active: bool
    is_disabled_template: bool
    speed_factor: float  # Approximate real-time speedup (e.g. 10.0 = 10x real-time)
    cost_per_minute_usd: float  # 0.0 for local
    rate_limit_clip_threshold_sec: float  # Duration threshold where rate limit warning triggers
    description: str
    rate_limit_note: str = ""
    function_id: str = ""


# ---------------------------------------------------------------------------
# Registered Models Catalog
# ---------------------------------------------------------------------------

MODELS_CATALOG: Dict[str, ModelInfo] = {
    # --- Local Whisper Engines (Fit <= 4 GB GPU VRAM) ---
    "whisper-tiny": ModelInfo(
        id="whisper-tiny",
        name="Whisper Tiny (Local)",
        provider="local",
        engine_type="whisper",
        vram_mb=1000,
        fits_4gb_gpu=True,
        is_active=True,
        is_disabled_template=False,
        speed_factor=15.0,
        cost_per_minute_usd=0.0,
        rate_limit_clip_threshold_sec=3600.0,
        description="39M parameters · ~1.0 GB VRAM. Ultra-fast preview transcription.",
    ),
    "whisper-base": ModelInfo(
        id="whisper-base",
        name="Whisper Base (Local)",
        provider="local",
        engine_type="whisper",
        vram_mb=1200,
        fits_4gb_gpu=True,
        is_active=True,
        is_disabled_template=False,
        speed_factor=10.0,
        cost_per_minute_usd=0.0,
        rate_limit_clip_threshold_sec=3600.0,
        description="74M parameters · ~1.2 GB VRAM. Good balance of speed and draft quality.",
    ),
    "whisper-small": ModelInfo(
        id="whisper-small",
        name="Whisper Small (Local)",
        provider="local",
        engine_type="whisper",
        vram_mb=2000,
        fits_4gb_gpu=True,
        is_active=True,
        is_disabled_template=False,
        speed_factor=5.0,
        cost_per_minute_usd=0.0,
        rate_limit_clip_threshold_sec=3600.0,
        description="244M parameters · ~2.0 GB VRAM. High accuracy for general conference speech.",
    ),
    "whisper-medium": ModelInfo(
        id="whisper-medium",
        name="Whisper Medium (Local, Default)",
        provider="local",
        engine_type="whisper",
        vram_mb=3500,
        fits_4gb_gpu=True,
        is_active=True,
        is_disabled_template=False,
        speed_factor=2.5,
        cost_per_minute_usd=0.0,
        rate_limit_clip_threshold_sec=3600.0,
        description="769M parameters · ~3.5 GB VRAM. Production quality for technical terminology.",
    ),

    # --- Cloud Engines (NVIDIA Hosted) ---
    "nvidia/parakeet-tdt-0.6b-v2": ModelInfo(
        id="nvidia/parakeet-tdt-0.6b-v2",
        name="NVIDIA Parakeet TDT 0.6B v2 (Cloud Riva)",
        provider="nvidia",
        engine_type="parakeet",
        vram_mb=0,
        fits_4gb_gpu=True,
        is_active=True,
        is_disabled_template=False,
        speed_factor=18.0,
        cost_per_minute_usd=0.003,
        rate_limit_clip_threshold_sec=600.0,  # 10 min
        description="0.6B parameters · Cloud Riva streaming. 15-20x real-time decoding.",
        rate_limit_note="NVIDIA Cloud streaming connection recommended for clips under 10 minutes. Longer clips chunk automatically.",
        function_id="d3fe9151-442b-4204-a70d-5fcc597fd610",
    ),
    "nvidia/parakeet-ctc-1.1b": ModelInfo(
        id="nvidia/parakeet-ctc-1.1b",
        name="NVIDIA Parakeet TDT 0.6B v2 (Cloud Riva)",
        provider="nvidia",
        engine_type="parakeet",
        vram_mb=0,
        fits_4gb_gpu=True,
        is_active=True,
        is_disabled_template=False,
        speed_factor=18.0,
        cost_per_minute_usd=0.003,
        rate_limit_clip_threshold_sec=600.0,
        description="0.6B parameters · Cloud Riva streaming. 15-20x real-time decoding.",
        rate_limit_note="NVIDIA Cloud streaming connection recommended for clips under 10 minutes. Longer clips chunk automatically.",
        function_id="d3fe9151-442b-4204-a70d-5fcc597fd610",
    ),
    "openai/whisper-large-v3": ModelInfo(
        id="openai/whisper-large-v3",
        name="NVIDIA Whisper Large v3 (Cloud Riva)",
        provider="nvidia",
        engine_type="whisper",
        vram_mb=0,
        fits_4gb_gpu=True,
        is_active=True,
        is_disabled_template=False,
        speed_factor=8.0,
        cost_per_minute_usd=0.006,
        rate_limit_clip_threshold_sec=480.0,  # 8 min
        description="1550M parameters · Maximum accuracy multi-lingual cloud transcription.",
        rate_limit_note="Hosted GPU queue. Rate-limits may apply on concurrent requests > 8 minutes.",
        function_id="b702f636-f60c-4a3d-a6f4-f3568c13bd7d",
    ),
    "nvidia/nemotron-speech": ModelInfo(
        id="nvidia/nemotron-speech",
        name="Nemotron Speech 3.5 Transcribe (Coming Soon)",
        provider="nvidia",
        engine_type="nemotron",
        vram_mb=0,
        fits_4gb_gpu=True,
        is_active=False,
        is_disabled_template=True,
        speed_factor=0.0,
        cost_per_minute_usd=0.0,
        rate_limit_clip_threshold_sec=0.0,
        description="Next-gen multi-lingual architecture. Preview template on build.nvidia.com.",
        rate_limit_note="Template only. Model endpoint not yet enabled in current cloud tier.",
        function_id="",
    ),
}


# ---------------------------------------------------------------------------
# Registry Access Functions
# ---------------------------------------------------------------------------

def get_model(model_id: str) -> Optional[ModelInfo]:
    """Retrieve ModelInfo by exact ID, or normalized alias."""
    if not model_id:
        return None
    normalized = model_id.strip()
    if normalized in MODELS_CATALOG:
        return MODELS_CATALOG[normalized]
    # Check alias prefixes and mappings
    alias_map = {
        "nvidia_parakeet": "nvidia/parakeet-tdt-0.6b-v2",
        "parakeet": "nvidia/parakeet-tdt-0.6b-v2",
        "parakeet-tdt": "nvidia/parakeet-tdt-0.6b-v2",
        "parakeet-ctc": "nvidia/parakeet-tdt-0.6b-v2",
        "nvidia_whisper": "openai/whisper-large-v3",
        "whisper-large": "openai/whisper-large-v3",
        "nemotron": "nvidia/nemotron-speech",
        "nvidia_nemotron": "nvidia/nemotron-speech",
    }
    if normalized in alias_map:
        return MODELS_CATALOG.get(alias_map[normalized])
    if f"whisper-{normalized}" in MODELS_CATALOG:
        return MODELS_CATALOG[f"whisper-{normalized}"]
    return None


def get_all_models() -> List[ModelInfo]:
    """Return all cataloged models."""
    return list(MODELS_CATALOG.values())


def get_local_models(max_vram_mb: int = 4096) -> List[ModelInfo]:
    """Return local Whisper models compatible with the available VRAM ceiling."""
    return [
        m for m in MODELS_CATALOG.values()
        if m.provider == "local" and m.vram_mb <= max_vram_mb and m.is_active
    ]


def get_cloud_models(include_templates: bool = True) -> List[ModelInfo]:
    """Return cloud models, optionally including disabled preview templates."""
    return [
        m for m in MODELS_CATALOG.values()
        if m.provider == "nvidia" and (include_templates or m.is_active)
    ]


def check_rate_limit_warning(model_id: str, duration_sec: float) -> Optional[str]:
    """
    Check if a model has known rate limit warnings or chunking recommendations
    for a given audio duration in seconds.
    """
    m = get_model(model_id)
    if not m or not m.rate_limit_clip_threshold_sec:
        return None

    if duration_sec > m.rate_limit_clip_threshold_sec and m.rate_limit_note:
        dur_min = duration_sec / 60.0
        thresh_min = m.rate_limit_clip_threshold_sec / 60.0
        return (
            f"Note for {m.name}: clip length ({dur_min:.1f} min) exceeds recommended "
            f"single-stream threshold ({thresh_min:.1f} min). {m.rate_limit_note}"
        )
    return None


def estimate_run_cost(model_id: str, duration_sec: float) -> Dict[str, Any]:
    """Calculate estimated cost and duration metrics for a transcription run."""
    m = get_model(model_id)
    dur_minutes = max(0.0, duration_sec / 60.0)
    if not m:
        return {
            "model_id": model_id,
            "cost_usd": 0.0,
            "estimated_time_sec": duration_sec / 2.0,
            "speedup": 2.0,
        }

    speed = m.speed_factor if m.speed_factor > 0 else 1.0
    est_time_sec = duration_sec / speed
    cost_usd = dur_minutes * m.cost_per_minute_usd

    return {
        "model_id": m.id,
        "model_name": m.name,
        "provider": m.provider,
        "cost_usd": round(cost_usd, 4),
        "estimated_time_sec": round(est_time_sec, 1),
        "speedup": speed,
    }

