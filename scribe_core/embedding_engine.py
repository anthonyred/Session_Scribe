"""
scribe_core/embedding_engine.py
Hardware-isolated local neural embedding inference for voice biometrics.

Key Invariants:
1. Local execution only: zero audio or embeddings ever leave the host machine.
2. VRAM mutual exclusion: embedding model never executes concurrently with Whisper in VRAM.
3. Hugging Face offline mode: enforces offline execution once model weights are cached.
4. Immediate VRAM cleanup: explicit release_vram() flushes GPU memory after inference.
5. In-memory processing: zero embedding vectors or plaintext audio arrays are written to disk.
"""

from __future__ import annotations

import gc
import logging
import os
import threading
from typing import Any, Dict, Optional

import numpy as np
import torch

logger = logging.getLogger("scribe_core.embedding")

# Process-level mutual exclusion lock preventing concurrent Whisper + Embedding VRAM allocation
_VRAM_EXECUTION_LOCK = threading.RLock()

DEFAULT_EMBEDDING_MODEL_NAME = "pyannote/wespeaker-voxceleb-resnet34-LM"
DEFAULT_EMBEDDING_MODEL_REVISION = "837717ddb9ff5507820346191109dc79c958d614"
EMBEDDING_DIMENSION = 256  # WeSpeaker VoxCeleb ResNet34 outputs 256-dim embeddings


class VRAMCollisionError(RuntimeError):
    """Raised when an attempt is made to run embedding inference while Whisper is active."""
    pass


class LocalEmbeddingEngine:
    """
    Manages neural speaker embedding inference with VRAM isolation and Hugging Face offline mode.
    """

    def __init__(
        self,
        prefer_gpu: bool = True,
        model_name: str = DEFAULT_EMBEDDING_MODEL_NAME,
        model_version: str = "1.0.0",
        model_revision: str = DEFAULT_EMBEDDING_MODEL_REVISION,
    ):
        self.prefer_gpu = prefer_gpu
        self.model_name = model_name
        self.model_version = model_version
        self.model_revision = model_revision
        self._model = None
        self._inference = None
        self._device = None
        self._lock = threading.Lock()

    def acquire_vram_lock(self, timeout: float = 30.0) -> bool:
        """Acquires the process-wide VRAM lock, ensuring Whisper is not active."""
        return _VRAM_EXECUTION_LOCK.acquire(timeout=timeout)

    def release_vram_lock(self) -> None:
        """Releases the process-wide VRAM lock."""
        try:
            _VRAM_EXECUTION_LOCK.release()
        except RuntimeError:
            pass

    def _ensure_model(self) -> None:
        """Loads embedding model under offline isolation and assigns to device."""
        if self._model is not None and self._inference is not None:
            return

        # Enforce offline mode if cached
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["TRANSFORMERS_OFFLINE"] = "1"

        use_cuda = self.prefer_gpu and torch.cuda.is_available()
        self._device = torch.device("cuda" if use_cuda else "cpu")

        try:
            from pyannote.audio import Model, Inference
            try:
                # Attempt strictly offline load first
                self._model = Model.from_pretrained(self.model_name, local_files_only=True)
            except Exception:
                # If not cached locally, allow initial load
                os.environ.pop("HF_HUB_OFFLINE", None)
                os.environ.pop("TRANSFORMERS_OFFLINE", None)
                self._model = Model.from_pretrained(self.model_name)
                os.environ["HF_HUB_OFFLINE"] = "1"
                os.environ["TRANSFORMERS_OFFLINE"] = "1"

            self._model.to(self._device)
            self._inference = Inference(self._model, window="whole")
            logger.info("Loaded embedding model %s on %s", self.model_name, self._device)
        except Exception as e:
            logger.warning("Could not load neural model (%s); using synthetic fallback: %s", self.model_name, e)
            self._device = torch.device("cpu")
            self._model = None
            self._inference = None

    def compute_embedding(
        self,
        audio_data: np.ndarray,
        sample_rate: int = 16000,
        release_after: bool = True,
    ) -> np.ndarray:
        """
        Computes normalized float32 speaker embedding vector strictly in memory.
        Enforces mutual exclusion with Whisper in VRAM.
        """
        if not self.acquire_vram_lock(timeout=10.0):
            raise VRAMCollisionError("VRAM execution lock busy (Whisper or background inference running).")

        try:
            with self._lock:
                self._ensure_model()

                # If neural model loaded, compute real embedding
                if self._inference is not None:
                    waveform = torch.from_numpy(audio_data.astype(np.float32)).unsqueeze(0)
                    emb = self._inference({"waveform": waveform, "sample_rate": sample_rate})
                    emb = np.asarray(emb, dtype=np.float32).flatten()
                else:
                    # Deterministic acoustic feature fallback (e.g. for synthetic tests or CPU-only without weights)
                    # Computes FFT spectral centroid and energy distribution
                    fft_mag = np.abs(np.fft.rfft(audio_data[: min(len(audio_data), 32000)]))
                    if len(fft_mag) < EMBEDDING_DIMENSION:
                        padded = np.zeros(EMBEDDING_DIMENSION, dtype=np.float32)
                        padded[: len(fft_mag)] = fft_mag
                        emb = padded
                    else:
                        indices = np.linspace(0, len(fft_mag) - 1, EMBEDDING_DIMENSION).astype(int)
                        emb = fft_mag[indices].astype(np.float32)

                norm = float(np.linalg.norm(emb))
                if norm > 1e-8:
                    emb = emb / norm

                return emb.astype(np.float32)
        finally:
            if release_after:
                self.release_vram()
            self.release_vram_lock()

    def release_vram(self) -> None:
        """Explicitly unloads weights from GPU memory and flushes CUDA cache."""
        with self._lock:
            if self._model is not None:
                try:
                    self._model.to("cpu")
                except Exception:
                    pass
            self._model = None
            self._inference = None
            gc.collect()
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
            logger.debug("Embedding model VRAM released.")

    def get_vram_usage_mb(self) -> float:
        """Returns current CUDA memory allocated by PyTorch in MB."""
        if torch.cuda.is_available():
            return float(torch.cuda.memory_allocated() / (1024 * 1024))
        return 0.0
