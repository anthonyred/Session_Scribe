"""
scribe_core/audio_extractor.py
In-memory audio extraction utility using FFmpeg pipes.
Converts any input video or audio format (MP4, MOV, MKV, MP3, WAV, M4A)
to standardized 16 kHz, 16-bit mono PCM WAV strictly in memory.
Zero unencrypted audio is ever written to disk.
"""

from __future__ import annotations

import io
import os
import subprocess
from typing import Optional, Tuple


class AudioExtractionError(Exception):
    """Raised when FFmpeg fails to decode or extract audio."""
    pass


def extract_audio_in_memory(
    source_path: str,
    time_range: Optional[Tuple[float, float]] = None,
) -> bytes:
    """
    Extracts audio from an audio or video file as 16 kHz, 16-bit mono PCM WAV bytes.
    Operates strictly in memory via standard input/output pipes without writing
    intermediate plaintext audio to the filesystem.

    Parameters:
    - source_path: Path to source audio/video file.
    - time_range: Optional tuple of (start_seconds, end_seconds).

    Returns:
    - bytes: Standard 16 kHz mono WAV binary data.
    """
    if not os.path.isfile(source_path):
        raise FileNotFoundError(f"Source media file not found: {source_path}")

    cmd = ["ffmpeg", "-v", "error", "-y"]

    if time_range:
        start_s, end_s = time_range
        if start_s < 0 or end_s <= start_s:
            raise ValueError(f"Invalid time range: ({start_s}, {end_s}). End must be greater than start.")
        cmd.extend(["-ss", f"{start_s:.3f}", "-to", f"{end_s:.3f}"])

    cmd.extend([
        "-i", os.path.abspath(source_path),
        "-vn",                   # Disable video stream
        "-acodec", "pcm_s16le",  # Standard 16-bit signed PCM
        "-ac", "1",              # Mono channel
        "-ar", "16000",          # 16 kHz sampling rate
        "-f", "wav",             # Standard WAV container
        "pipe:1",                # Stream output to stdout pipe
    ])

    try:
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
    except FileNotFoundError:
        raise AudioExtractionError("FFmpeg executable not found on system PATH.")
    except Exception as e:
        raise AudioExtractionError(f"Subprocess failure running FFmpeg: {e}")

    if proc.returncode != 0:
        err_msg = proc.stderr.decode("utf-8", errors="replace").strip()
        raise AudioExtractionError(f"FFmpeg extraction failed (code {proc.returncode}): {err_msg}")

    wav_bytes = proc.stdout
    if len(wav_bytes) < 44:  # 44 bytes is minimum standard RIFF WAV header
        raise AudioExtractionError("FFmpeg extracted 0 bytes of audio data.")

    return wav_bytes
