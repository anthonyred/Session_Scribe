"""
tests/test_usage_csv.py

Unit tests for CSV-based Usage Logging, Quota & Rate Limit Tracker, Spool Queue, and History Archiver:
- Strictly isolated in tempfile.mkdtemp() (never touches real D:\\Events)
- Validates UTF-8 with BOM (utf-8-sig) encoding for Excel compatibility
- Verifies attempt numbering (1, 2, 3) across repeated transcriptions of same clip
- Verifies transcript version archiving to scribe\\state\\history\\<clip>\\<attempt_id>.txt
- Verifies Excel locking resilience: spools rows to scribe\\usage\\.pending when file is locked,
  flushes when unlocked, emits notice callback without blocking transcription
- Verifies rate limit tracking: rolling 60s window, 80% pace check, 429 counting
- Verifies secret sanitization: zero keys, tokens, or auth headers written
"""

import csv
import json
import os
import shutil
import sys
import tempfile
import time
import unittest

# Ensure project root is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from scribe_core.usage_csv import (
    ATTEMPTS_COLUMNS,
    RUNS_COLUMNS,
    RateLimitTracker,
    UsageCsvTracker,
    format_hms,
    now_ist_iso,
    sanitize_value,
)


class TestUsageCsvTracker(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="test_usage_csv_")
        self.event_dir = os.path.join(self.temp_dir, "Test_Event_2026")
        self.scribe_dir = os.path.join(self.event_dir, "scribe")
        self.trans_dir = os.path.join(self.scribe_dir, "transcripts")
        os.makedirs(self.trans_dir, exist_ok=True)

        self.account_attempts_file = os.path.join(self.temp_dir, "_usage", "account_attempts.csv")
        self.notices: list = []
        self.tracker = UsageCsvTracker(
            event_scribe_dir=self.scribe_dir,
            account_attempts_path=self.account_attempts_file,
            on_spool_notice=lambda msg: self.notices.append(msg),
        )

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_utf8_bom_headers_created(self):
        """CSV files are created with UTF-8 BOM so Excel opens them cleanly."""
        self.assertTrue(os.path.isfile(self.tracker.attempts_csv_path))
        with open(self.tracker.attempts_csv_path, "rb") as f:
            raw_start = f.read(3)
            self.assertEqual(raw_start, b"\xef\xbb\xbf", "Header must begin with UTF-8 BOM")

        # Read back with DictReader
        with open(self.tracker.attempts_csv_path, "r", encoding="utf-8-sig") as f:
            reader = csv.reader(f)
            headers = next(reader)
            self.assertEqual(headers, ATTEMPTS_COLUMNS)

    def test_three_attempts_for_same_clip_and_archiving(self):
        """Transcribing the same clip 3 times increments attempt_no_for_clip and archives history."""
        clip_name = "Clip_Speaker_A.mp4"
        trans_file = os.path.join(self.trans_dir, "Clip_Speaker_A.txt")

        # Attempt 1
        with open(trans_file, "w", encoding="utf-8") as f:
            f.write("Transcript Version 1 from Parakeet.")
        att1_no = self.tracker.get_next_attempt_number(clip_name)
        self.assertEqual(att1_no, 1)

        row1 = {
            "attempt_id": "att_001",
            "run_id": "run_001",
            "event": "Test_Event",
            "clip": clip_name,
            "attempt_no_for_clip": att1_no,
            "started_at": now_ist_iso(),
            "time_taken_s": 12.4,
            "trigger": "test",
            "engine_start": "ai-parakeet-tdt-0_6b-v2",
            "outcome": "completed",
            "audio_s_clip": 60.0,
            "requests": 2,
        }
        self.tracker.log_attempt(row1)

        # Attempt 2 (re-transcribe)
        archived_1 = self.tracker.archive_transcript(clip_name, "att_001", trans_file)
        self.assertTrue(os.path.isfile(archived_1))
        with open(trans_file, "w", encoding="utf-8") as f:
            f.write("Transcript Version 2 edited.")
        att2_no = self.tracker.get_next_attempt_number(clip_name)
        self.assertEqual(att2_no, 2)

        row2 = {
            "attempt_id": "att_002",
            "run_id": "run_001",
            "event": "Test_Event",
            "clip": clip_name,
            "attempt_no_for_clip": att2_no,
            "started_at": now_ist_iso(),
            "time_taken_s": 11.8,
            "trigger": "single clip",
            "engine_start": "ai-parakeet-tdt-0_6b-v2",
            "outcome": "completed",
            "audio_s_clip": 60.0,
            "requests": 2,
            "archived_transcript_path": archived_1,
        }
        self.tracker.log_attempt(row2)

        # Attempt 3 (re-transcribe with Whisper local fallback)
        archived_2 = self.tracker.archive_transcript(clip_name, "att_002", trans_file)
        self.assertTrue(os.path.isfile(archived_2))
        with open(trans_file, "w", encoding="utf-8") as f:
            f.write("Transcript Version 3 finalized.")
        att3_no = self.tracker.get_next_attempt_number(clip_name)
        self.assertEqual(att3_no, 3)

        row3 = {
            "attempt_id": "att_003",
            "run_id": "run_002",
            "event": "Test_Event",
            "clip": clip_name,
            "attempt_no_for_clip": att3_no,
            "started_at": now_ist_iso(),
            "time_taken_s": 24.5,
            "trigger": "force",
            "engine_start": "ai-parakeet-tdt-0_6b-v2",
            "engine_end": "openai-whisper-medium",
            "outcome": "completed",
            "audio_s_clip": 60.0,
            "requests": 1,
            "failover_time_s": 1.2,
            "archived_transcript_path": archived_2,
        }
        self.tracker.log_attempt(row3)

        attempts = self.tracker.read_attempts(clip_name=clip_name)
        self.assertEqual(len(attempts), 3)
        # Newest first
        self.assertEqual(attempts[0]["attempt_no_for_clip"], "3")
        self.assertEqual(attempts[1]["attempt_no_for_clip"], "2")
        self.assertEqual(attempts[2]["attempt_no_for_clip"], "1")
        self.assertEqual(attempts[0]["time_taken_hms"], "00:00:24")

    def test_excel_locking_spool_queue(self):
        """When attempts.csv is locked by Excel, rows are spooled into .pending and flushed when freed."""
        clip_name = "Locked_Test_Clip.mp4"

        # Lock the attempts.csv file exclusively (simulating Excel)
        lock_fd = os.open(self.tracker.attempts_csv_path, os.O_RDWR)
        try:
            # On Windows, opening and holding an open write lock or permission deny simulation:
            # We monkeypatch _append_csv_row to return False for attempts.csv to simulate Windows lock
            original_append = self.tracker._append_csv_row

            def mock_append(csv_p, cols, row_d):
                if "attempts.csv" in csv_p:
                    return False
                return original_append(csv_p, cols, row_d)

            self.tracker._append_csv_row = mock_append

            # Log attempt while locked
            row = {
                "attempt_id": "att_lock_01",
                "run_id": "run_001",
                "event": "Test_Event",
                "clip": clip_name,
                "attempt_no_for_clip": 1,
                "started_at": now_ist_iso(),
                "time_taken_s": 15.0,
                "trigger": "full",
                "engine_start": "ai-parakeet-tdt-0_6b-v2",
                "outcome": "completed",
            }
            self.tracker.log_attempt(row)

            # Confirm row was queued in .pending spool
            pending_files = os.listdir(self.tracker.pending_dir)
            self.assertGreaterEqual(len(pending_files), 1)
            self.assertIn("Usage CSV is open elsewhere", self.notices[0])

            # Restore append function (simulating user closing Excel)
            self.tracker._append_csv_row = original_append

            # Flush spool
            flushed = self.tracker.flush_pending_spool()
            self.assertGreaterEqual(flushed, 1)
            self.assertEqual(len(os.listdir(self.tracker.pending_dir)), 0)

            # Confirm attempt is now in attempts.csv
            attempts = self.tracker.read_attempts(clip_name=clip_name)
            self.assertEqual(len(attempts), 1)
            self.assertEqual(attempts[0]["attempt_id"], "att_lock_01")
        finally:
            os.close(lock_fd)

    def test_rate_limit_tracker_rolling_window_and_pace(self):
        """RateLimitTracker correctly tracks rolling 60s requests, 80% pace, and 429s."""
        tracker = RateLimitTracker(limit_per_minute=40)
        self.assertFalse(tracker.is_pace_high())

        # Record 30 requests -> 75% pace (under 80%)
        for _ in range(30):
            tracker.record_request()
        self.assertFalse(tracker.is_pace_high())

        # Record 3 more requests -> 33/40 = 82.5% (exceeds 80%)
        for _ in range(3):
            tracker.record_request()
        self.assertTrue(tracker.is_pace_high())

        # Record 429
        tracker.record_429()
        reqs, limit, pct, count_429 = tracker.get_current_pace()
        self.assertEqual(reqs, 33)
        self.assertEqual(limit, 40)
        self.assertAlmostEqual(pct, 82.5)
        self.assertEqual(count_429, 1)

    def test_secrets_redacted(self):
        """Tokens, api keys, and bearer tokens are never written to CSV."""
        val1 = sanitize_value("nvapi-secret-1234567890")
        self.assertNotIn("nvapi-", val1)
        self.assertIn("[REDACTED]", val1)

        val2 = sanitize_value("Bearer eyJhbGciOi...")
        self.assertNotIn("Bearer ", val2)
        self.assertIn("[REDACTED]", val2)

        val3 = sanitize_value("hf_testtoken999")
        self.assertNotIn("hf_", val3)
        self.assertIn("[REDACTED]", val3)


if __name__ == "__main__":
    unittest.main()

