"""
Transcription Manager for Session Scribe
Manages spawning the transcription worker process, tracking progress,
enforcing the Test Gate, and supporting graceful cancellation and resume.
"""

import hashlib
import os
import sys
import json
import time
import subprocess
from datetime import datetime
from typing import List, Dict, Callable, Optional, Any

from .session_grouper import SessionInfo, ClipInfo


def _glossary_hash(text: str) -> str:
    """Return first 8 hex chars of SHA-256 of the glossary string."""
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()[:8]


class TranscriptionManager:
    """Orchestrates worker subprocess execution and test gate status."""

    def __init__(self, python_executable: Optional[str] = None):
        self.python_bin = python_executable or sys.executable
        self.current_process: Optional[subprocess.Popen] = None
        self._is_cancelled = False

    @staticmethod
    def get_test_gate_status_file(event_scribe_dir: str) -> str:
        return os.path.join(event_scribe_dir, "test", "test_approval.json")

    @classmethod
    def is_test_approved(cls, event_scribe_dir: str) -> bool:
        status_file = cls.get_test_gate_status_file(event_scribe_dir)
        if not os.path.exists(status_file):
            return False
        try:
            with open(status_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            return bool(data.get("approved", False))
        except Exception:
            return False

    @classmethod
    def set_test_approved(cls, event_scribe_dir: str, approved: bool = True, notes: str = ""):
        status_file = cls.get_test_gate_status_file(event_scribe_dir)
        os.makedirs(os.path.dirname(status_file), exist_ok=True)
        data = {
            "approved": approved,
            "timestamp": datetime.now().isoformat(),
            "notes": notes
        }
        with open(status_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

    def prepare_job_config(
        self,
        event_dict: Dict[str, Any],
        clips_to_run: List[Dict[str, Any]],
        output_dir: str,
        work_dir: str,
        log_dir: str,
        language: str = "en",
        initial_prompt: Optional[str] = None,
        model: str = "medium",
        resolved_paths: Optional[Dict[str, Any]] = None,
        # Bug 2 additions: cross-directory skip context
        test_dir: str = "",
        transcripts_dir: str = "",
        force_clips: Optional[List[str]] = None,
        # State-file directory (replaces per-file .meta.json sidecars)
        state_dir: str = "",
        # Cloud Engine parameters
        engine: str = "local",
        cloud_profile: Optional[Dict[str, Any]] = None,
        single_request_limit_sec: float = 600.0,
        chunk_duration_sec: float = 120.0,
        checkpoint_every_clip: bool = False,
        timeout_base: float = 15.0,
        timeout_factor: float = 2.0,
    ) -> str:
        """Writes job configuration JSON file to work_dir."""
        os.makedirs(work_dir, exist_ok=True)
        config_path = os.path.join(work_dir, f"job_config_{int(time.time() * 1000)}.json")
        ghash = _glossary_hash(initial_prompt or "")
        job_data = {
            "event": event_dict,
            "clips": clips_to_run,
            "output_dir": os.path.abspath(output_dir),
            "work_dir": os.path.abspath(work_dir),
            "log_dir": os.path.abspath(log_dir),
            "language": language,
            "initial_prompt": initial_prompt,
            "model": model,
            "device": "cuda",
            "resolved_paths": resolved_paths or {},
            # Cross-dir skip context (Bug 2)
            "test_dir": os.path.abspath(test_dir) if test_dir else "",
            "transcripts_dir": os.path.abspath(transcripts_dir) if transcripts_dir else "",
            "force_clips": force_clips or [],
            "glossary_hash": ghash,
            # State directory (replaces .meta.json sidecars beside transcripts)
            "state_dir": os.path.abspath(state_dir) if state_dir else "",
            # Cloud Engine configuration
            "engine": engine,
            "cloud_profile": cloud_profile or {},
            "single_request_limit_sec": float(single_request_limit_sec),
            "chunk_duration_sec": float(chunk_duration_sec),
            "checkpoint_every_clip": bool(checkpoint_every_clip),
            "timeout_base": float(timeout_base),
            "timeout_factor": float(timeout_factor),
        }
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(job_data, f, indent=2)
        return config_path

    def run_worker_blocking(
        self,
        job_config_path: str,
        progress_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
        env: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """
        Executes worker subprocess synchronously, streaming stdout progress events.
        Can be called inside a background thread.
        """
        self._is_cancelled = False
        worker_script = os.path.join(os.path.dirname(__file__), "transcribe_worker.py")

        cmd = [
            self.python_bin,
            worker_script,
            "--job-config",
            job_config_path
        ]

        child_env = os.environ.copy()
        try:
            from . import hf_preflight
            tok = hf_preflight.get_hf_token()
            if tok and "HF_TOKEN" not in child_env:
                child_env["HF_TOKEN"] = tok
        except Exception:
            pass
        if env:
            child_env.update(env)

        self.current_process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            env=child_env,
        )

        last_summary = {}

        # Stream stdout lines
        if self.current_process.stdout:
            for line in iter(self.current_process.stdout.readline, ""):
                line_str = line.strip()
                if not line_str:
                    continue
                try:
                    event = json.loads(line_str)
                    if event.get("type") == "finished":
                        last_summary = event
                    if progress_callback:
                        progress_callback(event)
                except json.JSONDecodeError:
                    if progress_callback:
                        progress_callback({"type": "raw_log", "message": line_str})

        _, stderr = self.current_process.communicate()
        exit_code = self.current_process.returncode
        self.current_process = None

        if exit_code != 0 and not last_summary:
            raise RuntimeError(f"Transcription worker failed with exit code {exit_code}: {stderr}")

        # Clean up config file
        if os.path.exists(job_config_path):
            try:
                os.remove(job_config_path)
            except Exception:
                pass

        return last_summary

    def cancel(self):
        """Terminates worker process immediately."""
        self._is_cancelled = True
        if self.current_process and self.current_process.poll() is None:
            self.current_process.terminate()
            try:
                self.current_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.current_process.kill()
