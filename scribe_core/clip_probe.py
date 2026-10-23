"""
Clip Probe using ffprobe
Dynamically resolves ffprobe from PATH and safely queries video clip duration and metadata.
"""

import os
import shutil
import subprocess
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any

IST = timezone(timedelta(hours=5, minutes=30))

class ClipProbe:
    """Safely executes ffprobe to retrieve duration and metadata without shell injection."""

    def __init__(self, ffprobe_path: Optional[str] = None):
        self.ffprobe_bin = ffprobe_path or self._resolve_ffprobe()

    @staticmethod
    def _resolve_ffprobe() -> Optional[str]:
        """Resolve ffprobe from PATH at runtime."""
        bin_path = shutil.which("ffprobe") or shutil.which("ffprobe.exe")
        if bin_path:
            return bin_path
        # Additional search in standard locations if not on standard PATH
        common_candidates = [
            r"C:\ProgramData\chocolatey\bin\ffprobe.exe",
            r"C:\Users\antho\scoop\shims\ffprobe.exe"
        ]
        for c in common_candidates:
            if os.path.exists(c):
                return c
        return None

    def is_available(self) -> bool:
        return bool(self.ffprobe_bin and os.path.exists(self.ffprobe_bin))

    def get_duration(self, video_path: str) -> float:
        """
        Returns duration in seconds.
        Uses argument list format to safely handle spaces and special characters.
        """
        if not self.is_available():
            raise RuntimeError(f"ffprobe executable not found on PATH. Current: {self.ffprobe_bin}")

        if not os.path.exists(video_path):
            raise FileNotFoundError(f"Video file not found: {video_path}")

        cmd = [
            self.ffprobe_bin,
            "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            video_path
        ]

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=True,
                timeout=30
            )
            raw = result.stdout.strip()
            if raw:
                return float(raw)
            return 0.0
        except (subprocess.CalledProcessError, ValueError) as e:
            # If duration cannot be read, log and return 0.0
            return 0.0

    def get_format_metadata(self, video_path: str) -> Dict[str, Any]:
        """
        Retrieves format tags, including creation_time if present.
        Caution: ffprobe creation_time is almost always UTC.
        """
        if not self.is_available():
            return {}

        cmd = [
            self.ffprobe_bin,
            "-v", "error",
            "-show_entries", "format_tags=creation_time",
            "-of", "default=noprint_wrappers=1:nokey=1",
            video_path
        ]

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            raw = result.stdout.strip()
            metadata = {}
            if raw:
                metadata["creation_time_raw"] = raw
                # Parse ISO UTC string e.g. "2026-09-05T06:11:30.000000Z"
                try:
                    clean_raw = raw.replace("Z", "+00:00")
                    dt_utc = datetime.fromisoformat(clean_raw)
                    metadata["creation_time_ist"] = dt_utc.astimezone(IST)
                except Exception:
                    pass
            return metadata
        except Exception:
            return {}
