"""
scribe_core/usage_csv.py

CSV-based Usage Logging, Quota & Rate Limit Tracker, Spool Queue, and History Archiver:
- Format: UTF-8 with BOM (utf-8-sig) so Microsoft Excel displays names correctly
- Files:
    * <event root>\\scribe\\usage\\runs.csv
    * <event root>\\scribe\\usage\\attempts.csv
    * D:\\Events\\_usage\\account_attempts.csv (cloud attempts only)
- Fixed schema columns with schema_version; new columns appended at end only
- Append-only: rows are only appended, never rewritten
- Timestamps: ISO-8601 in IST with offset (+05:30)
- Durations: provided both as seconds and as hh:mm:ss
- Excel locking resilience: Spools rows to scribe\\usage\\.pending if file is locked,
  flushes automatically when file is unlocked, emits notice callback.
- Rate limit tracking: Rolling 60-second window, editable limit (default 40/min),
  pacing, 429 counter, and 80% pace warning.
- Transcript history: archives replaced transcripts to scribe\\state\\history\\<clip>\\<attempt_id>.txt
- Security: zero keys, tokens, auth headers, or audio written.
"""

import collections
import csv
import json
import logging
import os
import shutil
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# IST Timezone: UTC+05:30
IST = timezone(timedelta(hours=5, minutes=30))

DEFAULT_ACCOUNT_ATTEMPTS_PATH = r"D:\Events\_usage\account_attempts.csv"
SCHEMA_VERSION = "1.0"
APP_VERSION = "0.9.2"

ATTEMPTS_COLUMNS = [
    "schema_version",
    "attempt_id",
    "run_id",
    "event",
    "clip",
    "attempt_no_for_clip",
    "started_at",
    "ended_at",
    "time_taken_s",
    "time_taken_hms",
    "trigger",
    "engine_start",
    "engine_end",
    "profile_model",
    "outcome",
    "audio_s_clip",
    "audio_s_sent",
    "requests",
    "retries",
    "http_429_count",
    "other_error_count",
    "bytes_uploaded",
    "tokens_in",
    "tokens_out",
    "tokens_source",
    "failover_first_error_at",
    "failover_local_started_at",
    "failover_time_s",
    "failover_reason",
    "resume_offset_s",
    "local_decode_s",
    "model_load_s",
    "archived_transcript_path",
    "notes",
]

RUNS_COLUMNS = [
    "schema_version",
    "run_id",
    "event",
    "started_at",
    "ended_at",
    "duration_s",
    "trigger",
    "engine",
    "profile",
    "clips_in_scope",
    "clips_completed",
    "clips_failed",
    "clips_skipped",
    "settings_summary",
    "app_version",
]


def now_ist_iso() -> str:
    """Return current timestamp in ISO-8601 format with IST offset."""
    return datetime.now(IST).strftime("%Y-%m-%dT%H:%M:%S+05:30")


def format_hms(seconds: float) -> str:
    """Format seconds into hh:mm:ss."""
    sec = max(0, int(round(seconds)))
    h = sec // 3600
    m = (sec % 3600) // 60
    s = sec % 60
    return f"{h:02d}:{m:02d}:{s:02d}"


def sanitize_value(val: Any) -> str:
    """Format value for CSV: empty/None becomes '—', strip secrets."""
    if val is None or val == "" or val == "—":
        return "—"
    s = str(val)
    # Defense-in-depth: Never persist keys or tokens
    for forbidden in ["nvapi-", "Bearer ", "hf_"]:
        if forbidden in s:
            s = s.replace(forbidden, "[REDACTED]")
    return s


# ----------------------------------------------------------------------
# Rate Limit Tracker (Rolling 60-Second Window)
# ----------------------------------------------------------------------
class RateLimitTracker:
    """
    Tracks request timestamps within a rolling 60-second window against
    an editable requests-per-minute limit (default: 40 rpm).
    """

    def __init__(self, limit_per_minute: int = 40):
        self.limit_per_minute = max(1, limit_per_minute)
        self._timestamps: collections.deque = collections.deque()
        self._429_count: int = 0
        self._lock = threading.Lock()

    def record_request(self) -> None:
        now = time.monotonic()
        with self._lock:
            self._timestamps.append(now)
            self._prune(now)

    def record_429(self) -> None:
        with self._lock:
            self._429_count += 1

    def _prune(self, now: float) -> None:
        cutoff = now - 60.0
        while self._timestamps and self._timestamps[0] < cutoff:
            self._timestamps.popleft()

    def get_current_pace(self) -> Tuple[int, int, float, int]:
        """Returns (requests_in_last_60s, limit_per_minute, pace_percentage, count_429)."""
        now = time.monotonic()
        with self._lock:
            self._prune(now)
            count = len(self._timestamps)
            pace_pct = (count / self.limit_per_minute) * 100.0
            return count, self.limit_per_minute, pace_pct, self._429_count

    def is_pace_high(self) -> bool:
        """Returns True if current pace is >= 80% of limit."""
        _, _, pct, _ = self.get_current_pace()
        return pct >= 80.0

    def compute_sleep_to_stay_under_pace(self, target_pct: float = 0.8) -> float:
        """Calculate required sleep in seconds to stay under target pace fraction."""
        now = time.monotonic()
        with self._lock:
            self._prune(now)
            max_allowed = int(self.limit_per_minute * target_pct)
            if len(self._timestamps) >= max_allowed and self._timestamps:
                oldest = self._timestamps[0]
                return max(0.0, 60.0 - (now - oldest) + 0.1)
            return 0.0


_WARNED_DIRS = set()


# ----------------------------------------------------------------------
# Usage CSV Tracker with Spool Queue Resilience
# ----------------------------------------------------------------------
class UsageCsvTracker:
    """
    Manages runs.csv, attempts.csv, account_attempts.csv, and local .pending spool.
    """

    def __init__(
        self,
        event_scribe_dir: str,
        account_attempts_path: str = DEFAULT_ACCOUNT_ATTEMPTS_PATH,
        on_spool_notice: Optional[Callable[[str], None]] = None,
    ):
        self.event_scribe_dir = os.path.abspath(event_scribe_dir)
        if not self.event_scribe_dir.lower().endswith("scribe"):
            self.event_scribe_dir = os.path.join(self.event_scribe_dir, "scribe")
        # If running in a test or non-D:\Events location, isolate to local parent folder
        if account_attempts_path == DEFAULT_ACCOUNT_ATTEMPTS_PATH:
            if not self.event_scribe_dir.lower().startswith(r"d:\events"):
                account_attempts_path = os.path.join(os.path.dirname(os.path.dirname(self.event_scribe_dir)), "_usage", "account_attempts.csv")
        self.usage_dir = os.path.join(self.event_scribe_dir, "usage")
        self.pending_dir = os.path.join(self.usage_dir, ".pending")
        self.history_dir = os.path.join(self.event_scribe_dir, "state", "history")
        self.account_attempts_path = account_attempts_path
        self.on_spool_notice = on_spool_notice

        self.runs_csv_path = os.path.join(self.usage_dir, "runs.csv")
        self.attempts_csv_path = os.path.join(self.usage_dir, "attempts.csv")

        self.rate_tracker = RateLimitTracker(limit_per_minute=40)
        self._lock = threading.Lock()
        self._spool_notified = False

        self._ensure_dirs()
        self._ensure_csv_headers()
        self.flush_pending_spool()

    def _ensure_dirs(self) -> None:
        for d in [self.usage_dir, self.pending_dir, self.history_dir]:
            try:
                os.makedirs(d, exist_ok=True)
            except OSError as e:
                if d not in _WARNED_DIRS:
                    logger.debug(f"Could not create usage directory: {d} ({e})")
                    _WARNED_DIRS.add(d)

        acc_dir = os.path.dirname(self.account_attempts_path)
        if acc_dir:
            try:
                os.makedirs(acc_dir, exist_ok=True)
            except OSError as e:
                if acc_dir not in _WARNED_DIRS:
                    logger.debug(f"Could not create account usage directory: {acc_dir} ({e})")
                    _WARNED_DIRS.add(acc_dir)

    def _ensure_csv_headers(self) -> None:
        """Write header row with utf-8-sig BOM if CSV does not exist."""
        self._init_csv_file(self.runs_csv_path, RUNS_COLUMNS)
        self._init_csv_file(self.attempts_csv_path, ATTEMPTS_COLUMNS)
        self._init_csv_file(self.account_attempts_path, ATTEMPTS_COLUMNS)

    def _init_csv_file(self, path: str, columns: List[str]) -> None:
        try:
            if not os.path.isfile(path) or os.path.getsize(path) == 0:
                parent = os.path.dirname(path)
                if parent:
                    os.makedirs(parent, exist_ok=True)
                with open(path, "w", encoding="utf-8-sig", newline="") as f:
                    writer = csv.writer(f)
                    writer.writerow(columns)
                    f.flush()
                    try:
                        os.fsync(f.fileno())
                    except OSError:
                        pass
        except OSError as e:
            if path not in _WARNED_DIRS:
                logger.debug(f"Could not initialize CSV header for {path}: {e}")
                _WARNED_DIRS.add(path)

    # ------------------------------------------------------------------
    # Transcript Version Archiving
    # ------------------------------------------------------------------
    def archive_transcript(self, clip_name: str, attempt_id: str, current_transcript_path: str) -> Optional[str]:
        """
        Archive previous transcript to scribe\\state\\history\\<clip>\\<attempt_id>.txt.
        Returns archive path or None.
        """
        if not os.path.isfile(current_transcript_path):
            return None
        try:
            stem = os.path.splitext(os.path.basename(clip_name))[0]
            clip_hist_dir = os.path.join(self.history_dir, stem)
            os.makedirs(clip_hist_dir, exist_ok=True)
            dest = os.path.join(clip_hist_dir, f"{attempt_id}.txt")
            shutil.copy2(current_transcript_path, dest)
            return dest
        except Exception as e:
            logger.warning(f"Failed to archive transcript for {clip_name}: {e}")
            return None

    # ------------------------------------------------------------------
    # Attempt Number Calculation
    # ------------------------------------------------------------------
    def get_next_attempt_number(self, clip_name: str) -> int:
        """Count existing attempts for this clip in attempts.csv + .pending spool + 1."""
        stem = os.path.splitext(os.path.basename(clip_name))[0]
        count = 0
        records = self.read_attempts(clip_name=stem)
        count = len(records)
        return count + 1

    # ------------------------------------------------------------------
    # Append Operations with Spooling Resilience
    # ------------------------------------------------------------------
    def log_attempt(self, attempt_data: Dict[str, Any]) -> None:
        """
        Log an attempt record to attempts.csv (and account_attempts.csv if cloud).
        If file is locked (e.g. by Excel), writes to .pending spool and notifies.
        """
        row_id = attempt_data.get("attempt_id") or str(uuid.uuid4())[:8]
        attempt_data["attempt_id"] = row_id
        attempt_data["schema_version"] = SCHEMA_VERSION

        # Normalize duration HMS
        time_s = float(attempt_data.get("time_taken_s") or 0.0)
        attempt_data["time_taken_hms"] = format_hms(time_s)

        row_dict = {col: sanitize_value(attempt_data.get(col, "—")) for col in ATTEMPTS_COLUMNS}

        with self._lock:
            # 1. Attempt to append to event attempts.csv
            appended = self._append_csv_row(self.attempts_csv_path, ATTEMPTS_COLUMNS, row_dict)
            if not appended:
                self._spool_pending_row("attempt", row_dict)

            # 2. If cloud, append to account_attempts.csv
            engine_str = str(attempt_data.get("engine_start", "")).lower()
            outcome_str = str(attempt_data.get("outcome", "")).lower()
            is_cloud = "cloud" in engine_str or "riva" in engine_str or "parakeet" in engine_str
            if is_cloud:
                acc_appended = self._append_csv_row(self.account_attempts_path, ATTEMPTS_COLUMNS, row_dict)
                if not acc_appended:
                    self._spool_pending_row("account_attempt", row_dict)

    def log_run(self, run_data: Dict[str, Any]) -> None:
        """Log a run record to runs.csv."""
        run_data["schema_version"] = SCHEMA_VERSION
        run_data["app_version"] = APP_VERSION
        row_dict = {col: sanitize_value(run_data.get(col, "—")) for col in RUNS_COLUMNS}

        with self._lock:
            appended = self._append_csv_row(self.runs_csv_path, RUNS_COLUMNS, row_dict)
            if not appended:
                self._spool_pending_row("run", row_dict)

    def _append_csv_row(self, csv_path: str, columns: List[str], row_dict: Dict[str, str]) -> bool:
        """Attempt to write a row directly to a CSV file. Returns False on lock/error."""
        try:
            parent = os.path.dirname(csv_path)
            if parent:
                os.makedirs(parent, exist_ok=True)
            file_exists = os.path.isfile(csv_path) and os.path.getsize(csv_path) > 0
            with open(csv_path, "a", encoding="utf-8-sig", newline="") as f:
                writer = csv.writer(f)
                if not file_exists:
                    writer.writerow(columns)
                row_vals = [row_dict.get(c, "—") for c in columns]
                writer.writerow(row_vals)
                f.flush()
                try:
                    os.fsync(f.fileno())
                except OSError:
                    pass
            return True
        except OSError as e:
            # File locked by Excel (PermissionError [WinError 32]) or disk error
            logger.warning(f"CSV file locked or inaccessible: {csv_path} ({e})")
            return False

    def _spool_pending_row(self, target_type: str, row_dict: Dict[str, str]) -> None:
        """Write pending row to .pending\\spool_<uuid>.json."""
        try:
            os.makedirs(self.pending_dir, exist_ok=True)
            spool_file = os.path.join(self.pending_dir, f"{target_type}_{uuid.uuid4().hex}.json")
            with open(spool_file, "w", encoding="utf-8") as f:
                json.dump({"target": target_type, "row": row_dict}, f)
                f.flush()

            # Trigger one-time notice
            pending_count = len(os.listdir(self.pending_dir))
            if not self._spool_notified and self.on_spool_notice:
                self.on_spool_notice(f"Usage CSV is open elsewhere. {pending_count} rows waiting")
                self._spool_notified = True
        except Exception as e:
            logger.error(f"Failed to spool pending row: {e}")

    def flush_pending_spool(self) -> int:
        """
        Attempt to flush any pending queued rows into their target CSV files.
        Returns the number of flushed rows.
        """
        if not os.path.isdir(self.pending_dir):
            return 0

        flushed = 0
        try:
            entries = os.listdir(self.pending_dir)
            if not entries:
                self._spool_notified = False
                return 0

            for fname in sorted(entries):
                if not fname.endswith(".json"):
                    continue
                fpath = os.path.join(self.pending_dir, fname)
                try:
                    with open(fpath, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    target = data.get("target")
                    row = data.get("row", {})

                    success = False
                    if target == "attempt":
                        success = self._append_csv_row(self.attempts_csv_path, ATTEMPTS_COLUMNS, row)
                    elif target == "account_attempt":
                        success = self._append_csv_row(self.account_attempts_path, ATTEMPTS_COLUMNS, row)
                    elif target == "run":
                        success = self._append_csv_row(self.runs_csv_path, RUNS_COLUMNS, row)

                    if success:
                        os.remove(fpath)
                        flushed += 1
                    else:
                        break  # File still locked; stop until next try
                except Exception as e:
                    logger.warning(f"Could not flush spool file {fname}: {e}")

            if len(os.listdir(self.pending_dir)) == 0:
                self._spool_notified = False
        except Exception as e:
            logger.warning(f"Error scanning pending spool: {e}")

        return flushed

    # ------------------------------------------------------------------
    # Query Operations
    # ------------------------------------------------------------------
    def read_attempts(self, clip_name: Optional[str] = None) -> List[Dict[str, str]]:
        """
        Read all attempt rows from attempts.csv plus any spool items.
        If clip_name is provided, filters for that clip. Newest first.
        """
        self.flush_pending_spool()
        results: List[Dict[str, str]] = []

        # 1. Read attempts.csv
        if os.path.isfile(self.attempts_csv_path):
            try:
                with open(self.attempts_csv_path, "r", encoding="utf-8-sig", newline="") as f:
                    reader = csv.DictReader(f)
                    for row in reader:
                        results.append(dict(row))
            except Exception as e:
                logger.warning(f"Error reading attempts CSV: {e}")

        # 2. Also check pending spool
        if os.path.isdir(self.pending_dir):
            try:
                for fname in os.listdir(self.pending_dir):
                    if fname.startswith("attempt_") and fname.endswith(".json"):
                        fpath = os.path.join(self.pending_dir, fname)
                        try:
                            with open(fpath, "r", encoding="utf-8") as f:
                                data = json.load(f)
                            results.append(data.get("row", {}))
                        except Exception:
                            pass
            except Exception:
                pass

        if clip_name:
            stem_target = os.path.splitext(os.path.basename(clip_name))[0].lower()
            results = [
                r for r in results
                if os.path.splitext(os.path.basename(r.get("clip", "")))[0].lower() == stem_target
            ]

        # Newest first based on started_at or attempt_no_for_clip
        try:
            results.sort(
                key=lambda r: (int(r.get("attempt_no_for_clip") or 0), r.get("started_at", "")),
                reverse=True,
            )
        except Exception:
            results.reverse()

        return results

    def read_runs(self) -> List[Dict[str, str]]:
        """Read all runs from runs.csv, newest first."""
        self.flush_pending_spool()
        results: List[Dict[str, str]] = []
        if os.path.isfile(self.runs_csv_path):
            try:
                with open(self.runs_csv_path, "r", encoding="utf-8-sig", newline="") as f:
                    reader = csv.DictReader(f)
                    for row in reader:
                        results.append(dict(row))
            except Exception as e:
                logger.warning(f"Error reading runs CSV: {e}")

        results.reverse()
        return results

    def get_latest_attempt_for_clip(self, clip_name: str) -> Optional[Dict[str, str]]:
        """Returns the most recent attempt for clip_name, or None."""
        attempts = self.read_attempts(clip_name=clip_name)
        return attempts[0] if attempts else None


def log_attempt(event_root: str, attempt_data: Dict[str, Any]) -> None:
    """Convenience helper to log an attempt to event usage CSV."""
    UsageCsvTracker(event_root).log_attempt(attempt_data)


def log_run(event_root: str, run_data: Dict[str, Any]) -> None:
    """Convenience helper to log a run to event usage CSV."""
    UsageCsvTracker(event_root).log_run(run_data)

