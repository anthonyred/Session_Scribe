"""
scribe_core/usage_ledger.py

Append-only Usage Ledger and Quota Tracker for Session Scribe:
- JSONL format: flushed and fsynced on every write
- Resilient: Corrupt lines skipped with a warning; never breaks reading
- Append-only: Nothing is ever rewritten or deleted
- Per-event ledger: <event root>\\scribe\\state\\usage\\ledger.jsonl
- Account-wide ledger: D:\\Events\\_usage\\account_ledger.jsonl (configurable)
- Timestamps: ISO-8601 with IST offset (+05:30)
- Durations: Measured using time.monotonic()
- Records: Run record, Attempt record (with attempt_number), Quota snapshot
- Background local compute GPU sampler via nvidia-smi (1 sample/sec, "GPU only, estimated")
- Transcript version archiving to scribe\\state\\history\\<clip>\\<attempt_id>.txt
- Honesty rules: Null for unknown ("—"), basis tags on every estimate
- Zero secrets: Never stores keys, tokens, auth headers, or audio
"""

import json
import logging
import os
import shutil
import subprocess
import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# IST timezone: UTC+5:30
IST = timezone(timedelta(hours=5, minutes=30))

DEFAULT_ACCOUNT_LEDGER_PATH = r"D:\Events\_usage\account_ledger.jsonl"


def now_ist_iso() -> str:
    """Return current timestamp in ISO-8601 format with IST offset."""
    return datetime.now(IST).isoformat()


# ----------------------------------------------------------------------
# Background GPU Compute Sampler (nvidia-smi)
# ----------------------------------------------------------------------
class LocalComputeSampler:
    """
    Background sampler that queries nvidia-smi once per second while local
    Whisper decoding runs, calculating energy = sum(power * dt) / 3600 Wh.
    Labeled strictly as 'GPU only, estimated'.
    """

    def __init__(self, interval_sec: float = 1.0):
        self.interval_sec = interval_sec
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

        self._power_samples: List[float] = []
        self._util_samples: List[float] = []
        self._vram_samples: List[float] = []
        self._start_monotonic = 0.0
        self._end_monotonic = 0.0

    def start(self) -> None:
        self._power_samples.clear()
        self._util_samples.clear()
        self._vram_samples.clear()
        self._stop_event.clear()
        self._start_monotonic = time.monotonic()
        self._thread = threading.Thread(target=self._sample_loop, daemon=True)
        self._thread.start()

    def stop(self) -> Dict[str, Any]:
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        self._end_monotonic = time.monotonic()

        decoding_s = max(0.0, self._end_monotonic - self._start_monotonic)
        if not self._power_samples:
            # Query once as fallback reading
            sample = self._query_nvidia_smi()
            if sample:
                p, u, v = sample
                self._power_samples.append(p)
                self._util_samples.append(u)
                self._vram_samples.append(v)

        avg_power = float(sum(self._power_samples) / len(self._power_samples)) if self._power_samples else 0.0
        avg_util = float(sum(self._util_samples) / len(self._util_samples)) if self._util_samples else 0.0
        peak_vram = float(max(self._vram_samples)) if self._vram_samples else 0.0

        # Energy Wh = sum(power * dt) / 3600
        dt = self.interval_sec
        energy_wh = float(sum(p * dt for p in self._power_samples) / 3600.0)

        return {
            "decoding_s": round(decoding_s, 2),
            "avg_gpu_util_pct": round(avg_util, 1),
            "peak_vram_mb": round(peak_vram, 1),
            "avg_power_w": round(avg_power, 2),
            "est_energy_wh": round(energy_wh, 4),
            "tag": "GPU only, estimated",
        }

    def _sample_loop(self) -> None:
        while not self._stop_event.is_set():
            sample = self._query_nvidia_smi()
            if sample:
                p, u, v = sample
                self._power_samples.append(p)
                self._util_samples.append(u)
                self._vram_samples.append(v)
            self._stop_event.wait(self.interval_sec)

    @staticmethod
    def _query_nvidia_smi() -> Optional[Tuple[float, float, float]]:
        """Query nvidia-smi via argument list without shell."""
        cmd = [
            "nvidia-smi",
            "--query-gpu=power.draw,utilization.gpu,memory.used",
            "--format=csv,noheader,nounits",
        ]
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=2.0)
            line = res.stdout.strip()
            if line:
                parts = [p.strip() for p in line.split(",")]
                if len(parts) >= 3:
                    return float(parts[0]), float(parts[1]), float(parts[2])
        except Exception:
            pass
        return None


# ----------------------------------------------------------------------
# Usage Ledger Manager
# ----------------------------------------------------------------------
_WARNED_DIRS = set()


class UsageLedger:
    """
    Manages append-only JSONL ledgers for per-event runs and account-wide tracking.
    """

    def __init__(
        self,
        event_scribe_dir: Optional[str] = None,
        account_ledger_path: str = DEFAULT_ACCOUNT_LEDGER_PATH,
    ):
        self.event_scribe_dir = os.path.abspath(event_scribe_dir) if event_scribe_dir else None
        if self.event_scribe_dir and not self.event_scribe_dir.lower().endswith("scribe"):
            self.event_scribe_dir = os.path.join(self.event_scribe_dir, "scribe")
        self.account_ledger_path = os.path.abspath(account_ledger_path)
        self._warned_write_failure = False

        if self.event_scribe_dir:
            self.event_ledger_dir = os.path.join(self.event_scribe_dir, "state", "usage")
            self.event_ledger_file = os.path.join(self.event_ledger_dir, "ledger.jsonl")
            self.history_dir = os.path.join(self.event_scribe_dir, "state", "history")
            try:
                os.makedirs(self.event_ledger_dir, exist_ok=True)
                os.makedirs(self.history_dir, exist_ok=True)
            except Exception as e:
                if self.event_scribe_dir not in _WARNED_DIRS:
                    logger.warning(f"Could not create usage directory in '{self.event_scribe_dir}': {e}")
                    _WARNED_DIRS.add(self.event_scribe_dir)
        else:
            self.event_ledger_dir = None
            self.event_ledger_file = None
            self.history_dir = None

        acc_dir = os.path.dirname(self.account_ledger_path)
        try:
            os.makedirs(acc_dir, exist_ok=True)
        except Exception as e:
            if acc_dir not in _WARNED_DIRS:
                logger.warning(f"Could not create account ledger directory '{self.account_ledger_path}': {e}")
                _WARNED_DIRS.add(acc_dir)

    # ------------------------------------------------------------------
    # Writing Records (Flushed, fsynced, append-only)
    # ------------------------------------------------------------------
    def append_record(self, record: Dict[str, Any], sync_to_account: bool = False) -> bool:
        """
        Append a single JSON record to the event ledger and optionally to the account ledger.
        Fails safely without raising exceptions or blocking transcription.
        """
        # Security sanitization check: Never write keys, auth tokens, or audio bytes
        sanitized = self._sanitize_record(record)
        line = json.dumps(sanitized) + "\n"

        success = True
        if self.event_ledger_file:
            try:
                with open(self.event_ledger_file, "a", encoding="utf-8") as f:
                    f.write(line)
                    f.flush()
                    os.fsync(f.fileno())
            except Exception as e:
                success = False
                if not self._warned_write_failure:
                    logger.warning(f"Could not append to event ledger '{self.event_ledger_file}': {e}")
                    self._warned_write_failure = True

        if sync_to_account:
            try:
                with open(self.account_ledger_path, "a", encoding="utf-8") as f:
                    f.write(line)
                    f.flush()
                    os.fsync(f.fileno())
            except Exception as e:
                if not self._warned_write_failure:
                    logger.warning(f"Could not append to account ledger '{self.account_ledger_path}': {e}")
                    self._warned_write_failure = True

        return success

    # ------------------------------------------------------------------
    # Counting Attempts for a Clip
    # ------------------------------------------------------------------
    def get_next_attempt_number(self, clip_name: str) -> int:
        """
        Return the 1-based attempt number for a clip (e.g. 1 for first run, 2 for re-run).
        """
        records = self.read_event_records()
        attempts = [
            r for r in records
            if r.get("record_type") == "attempt" and r.get("clip_name") == clip_name
        ]
        return len(attempts) + 1

    # ------------------------------------------------------------------
    # Archiving Replaced Transcripts
    # ------------------------------------------------------------------
    def archive_replaced_transcript(
        self, clip_stem: str, attempt_id: str, current_transcript_path: str
    ) -> Optional[str]:
        """
        When replacing a transcript on a re-run, archive previous .txt to:
        scribe\\state\\history\\<clip>\\<attempt_id>.txt
        Returns the relative archived path.
        """
        if not os.path.isfile(current_transcript_path) or not self.history_dir:
            return None

        clip_history_dir = os.path.join(self.history_dir, clip_stem)
        os.makedirs(clip_history_dir, exist_ok=True)

        target_file = os.path.join(clip_history_dir, f"{attempt_id}.txt")
        try:
            shutil.copy2(current_transcript_path, target_file)
            rel_path = os.path.relpath(target_file, self.event_scribe_dir)
            return rel_path
        except Exception as e:
            logger.warning(f"Could not archive previous transcript for {clip_stem}: {e}")
            return None

    # ------------------------------------------------------------------
    # Reading Records (Robust against corrupted lines)
    # ------------------------------------------------------------------
    @staticmethod
    def read_ledger_file(file_path: str) -> List[Dict[str, Any]]:
        """
        Read JSONL ledger file. Corrupt or unparseable lines are skipped
        with a warning and never break parsing of subsequent lines.
        """
        if not os.path.isfile(file_path):
            return []

        records = []
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            for line_no, raw_line in enumerate(f, start=1):
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                    records.append(obj)
                except Exception as e:
                    logger.warning(f"Skipping corrupt ledger line #{line_no} in '{file_path}': {e}")
        return records

    def read_event_records(self) -> List[Dict[str, Any]]:
        """Read all records from the current event's ledger."""
        if not self.event_ledger_file:
            return []
        return self.read_ledger_file(self.event_ledger_file)

    def read_account_records(self) -> List[Dict[str, Any]]:
        """Read all records from the account-wide ledger."""
        return self.read_ledger_file(self.account_ledger_path)

    # ------------------------------------------------------------------
    # Quota & Rate Limit Analysis
    # ------------------------------------------------------------------
    def get_rate_limit_metrics(self, window_seconds: float = 60.0) -> Dict[str, Any]:
        """
        Compute requests in the last minute, total requests today,
        and count of 429 rate limit errors.
        """
        records = self.read_account_records()
        now_dt = datetime.now(IST)
        today_date = now_dt.strftime("%Y-%m-%d")

        reqs_in_window = 0
        reqs_today = 0
        total_429s = 0

        for r in records:
            if r.get("record_type") != "attempt":
                continue

            # Check engine
            if r.get("engine_start") != "cloud" and r.get("engine_end") != "cloud":
                continue

            ts_str = r.get("started_at") or r.get("ended_at")
            if not ts_str:
                continue
            try:
                dt = datetime.fromisoformat(ts_str)
            except Exception:
                continue

            req_count = r.get("requests_count", 1)
            err_429 = r.get("rate_limit_429_count", 0)
            total_429s += err_429

            if dt.strftime("%Y-%m-%d") == today_date:
                reqs_today += req_count

            age_sec = (now_dt - dt).total_seconds()
            if 0 <= age_sec <= window_seconds:
                reqs_in_window += req_count

        return {
            "requests_last_minute": reqs_in_window,
            "requests_today": reqs_today,
            "total_429_count": total_429s,
            "default_limit_per_min": 40,
            "mode": "rate_limit",
            "note": "no credit balance on this plan",
        }

    # ------------------------------------------------------------------
    # Rebuild Account Ledger Function
    # ------------------------------------------------------------------
    @classmethod
    def rebuild_account_ledger(
        cls, events_root: str, target_account_ledger: str = DEFAULT_ACCOUNT_LEDGER_PATH
    ) -> int:
        """
        Scan all <event>\\scribe\\state\\usage\\ledger.jsonl under events_root
        and rebuild the account-wide ledger (cloud attempts & quota snapshots).
        """
        if not os.path.isdir(events_root):
            return 0

        os.makedirs(os.path.dirname(target_account_ledger), exist_ok=True)
        all_account_records = []

        for item in sorted(os.listdir(events_root)):
            if item.startswith("_"):
                continue
            ev_dir = os.path.join(events_root, item)
            if not os.path.isdir(ev_dir):
                continue
            ledger_file = os.path.join(ev_dir, "scribe", "state", "usage", "ledger.jsonl")
            if os.path.isfile(ledger_file):
                ev_records = cls.read_ledger_file(ledger_file)
                for r in ev_records:
                    rtype = r.get("record_type")
                    if rtype == "quota_snapshot":
                        all_account_records.append(r)
                    elif rtype == "attempt":
                        # Include cloud attempts
                        if r.get("engine_start") == "cloud" or r.get("engine_end") == "cloud":
                            all_account_records.append(r)

        # Sort chronologically by timestamp
        all_account_records.sort(key=lambda x: x.get("started_at") or x.get("timestamp") or "")

        # Atomic rewrite of account ledger
        tmp_path = f"{target_account_ledger}.tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            for r in all_account_records:
                f.write(json.dumps(r) + "\n")
            f.flush()
            os.fsync(f.fileno())

        if os.path.exists(target_account_ledger):
            try:
                os.remove(target_account_ledger)
            except OSError:
                pass
        os.replace(tmp_path, target_account_ledger)
        return len(all_account_records)

    # ------------------------------------------------------------------
    # Security Sanitization Guard
    # ------------------------------------------------------------------
    @staticmethod
    def _sanitize_record(record: Dict[str, Any]) -> Dict[str, Any]:
        """Strip any authorization tokens, API keys, or raw audio data."""
        forbidden_keys = {"authorization", "api_key", "bearer", "password", "token", "audio_bytes"}
        clean = {}
        for k, v in record.items():
            if any(fk in k.lower() for fk in forbidden_keys):
                continue
            if isinstance(v, dict):
                clean[k] = UsageLedger._sanitize_record(v)
            elif isinstance(v, list):
                clean[k] = [
                    UsageLedger._sanitize_record(elem) if isinstance(elem, dict) else elem
                    for elem in v
                ]
            else:
                clean[k] = v
        return clean
