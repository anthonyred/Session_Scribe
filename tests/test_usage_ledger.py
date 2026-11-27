"""
tests/test_usage_ledger.py

Unit tests for Usage Ledger, Quota Tracking, Compute Sampling, and History Archiving:
- Strictly isolated in tempfile.mkdtemp() (never touches real D:\\Events)
- Validates append-only JSONL writing, line flushing, and recovery from corrupt lines
- Verifies attempt numbering (1, 2, 3), failover metrics, and time splits
- Verifies transcript archiving to scribe\\state\\history\\<clip>\\<attempt_id>.txt
- Verifies local compute sampling with 'GPU only, estimated' label
- Verifies secret sanitization: zero keys, tokens, or auth headers written
- Verifies rebuild_account_ledger function
"""

import json
import os
import shutil
import sys
import tempfile
import unittest

# Ensure project root is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from scribe_core.usage_ledger import LocalComputeSampler, UsageLedger, now_ist_iso


class TestUsageLedgerCore(unittest.TestCase):
    """Core ledger tests: append-only, corruption resilience, attempt counting."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="test_ledger_")
        self.event_dir = os.path.join(self.temp_dir, "Test_Event_2026")
        self.scribe_dir = os.path.join(self.event_dir, "scribe")
        os.makedirs(os.path.join(self.scribe_dir, "transcripts"), exist_ok=True)
        self.account_ledger_file = os.path.join(self.temp_dir, "_usage", "account_ledger.jsonl")
        self.ledger = UsageLedger(
            event_scribe_dir=self.scribe_dir,
            account_ledger_path=self.account_ledger_file,
        )

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_append_and_corruption_resilience(self):
        """Valid JSON records are read back; corrupt lines are skipped without error."""
        r1 = {"record_type": "run", "run_id": "run-001", "trigger": "manual"}
        r2 = {"record_type": "attempt", "attempt_id": "att-001", "clip_name": "VID_01.mp4"}
        self.ledger.append_record(r1)
        self.ledger.append_record(r2)

        # Inject a corrupt line directly into the ledger file
        with open(self.ledger.event_ledger_file, "a", encoding="utf-8") as f:
            f.write("{THIS_IS_CORRUPTED_JSON_LINE\n")

        r3 = {"record_type": "run", "run_id": "run-002", "trigger": "resume"}
        self.ledger.append_record(r3)

        records = self.ledger.read_event_records()
        self.assertEqual(len(records), 3)
        self.assertEqual(records[0]["run_id"], "run-001")
        self.assertEqual(records[1]["attempt_id"], "att-001")
        self.assertEqual(records[2]["run_id"], "run-002")

    def test_multiple_attempts_and_transcript_archiving(self):
        """Transcribing the same clip 3 times increments attempt numbers and archives earlier .txt."""
        clip_name = "VID20261031_001.mp4"
        clip_stem = "VID20261031_001"
        transcript_file = os.path.join(self.scribe_dir, "transcripts", f"{clip_stem}.txt")

        # Attempt 1 (Test run)
        with open(transcript_file, "w", encoding="utf-8") as f:
            f.write("Transcript v1 (test run)")

        att1_num = self.ledger.get_next_attempt_number(clip_name)
        self.assertEqual(att1_num, 1)
        r_att1 = {
            "record_type": "attempt",
            "attempt_id": "att-001",
            "clip_name": clip_name,
            "attempt_number": att1_num,
            "outcome": "completed",
            "time_taken_s": 4.2,
        }
        self.ledger.append_record(r_att1)

        # Attempt 2 (Full run): archive v1 before replacing
        archived1 = self.ledger.archive_replaced_transcript(clip_stem, "att-001", transcript_file)
        self.assertIsNotNone(archived1)
        self.assertTrue(os.path.isfile(os.path.join(self.scribe_dir, archived1)))

        with open(transcript_file, "w", encoding="utf-8") as f:
            f.write("Transcript v2 (full run)")

        att2_num = self.ledger.get_next_attempt_number(clip_name)
        self.assertEqual(att2_num, 2)
        r_att2 = {
            "record_type": "attempt",
            "attempt_id": "att-002",
            "clip_name": clip_name,
            "attempt_number": att2_num,
            "archived_transcript": archived1,
            "outcome": "completed",
            "time_taken_s": 18.5,
        }
        self.ledger.append_record(r_att2)

        # Attempt 3 (Force re-run): archive v2 before replacing
        archived2 = self.ledger.archive_replaced_transcript(clip_stem, "att-002", transcript_file)
        self.assertIsNotNone(archived2)
        self.assertTrue(os.path.isfile(os.path.join(self.scribe_dir, archived2)))

        with open(transcript_file, "w", encoding="utf-8") as f:
            f.write("Transcript v3 (force re-run)")

        att3_num = self.ledger.get_next_attempt_number(clip_name)
        self.assertEqual(att3_num, 3)
        r_att3 = {
            "record_type": "attempt",
            "attempt_id": "att-003",
            "clip_name": clip_name,
            "attempt_number": att3_num,
            "archived_transcript": archived2,
            "outcome": "completed",
            "time_taken_s": 17.9,
        }
        self.ledger.append_record(r_att3)

        # Verify archived contents remain intact and separate
        with open(os.path.join(self.scribe_dir, archived1), "r", encoding="utf-8") as f:
            self.assertEqual(f.read(), "Transcript v1 (test run)")
        with open(os.path.join(self.scribe_dir, archived2), "r", encoding="utf-8") as f:
            self.assertEqual(f.read(), "Transcript v2 (full run)")
        with open(transcript_file, "r", encoding="utf-8") as f:
            self.assertEqual(f.read(), "Transcript v3 (force re-run)")

    def test_failover_and_time_split_record(self):
        """Simulate cloud failover after N segments and verify split times and basis tags."""
        att = {
            "record_type": "attempt",
            "attempt_id": "att-failover-01",
            "clip_name": "VID_failover.mp4",
            "engine_start": "cloud",
            "engine_end": "local",
            "outcome": "completed",
            "time_taken_s": 45.2,
            "time_split": {
                "cloud_s": 12.0,
                "retry_wait_s": 3.0,
                "model_load_s": 4.5,
                "local_s": 25.7,
            },
            "failover": {
                "first_error_time": now_ist_iso(),
                "local_start_time": now_ist_iso(),
                "reason": "gRPC UNAVAILABLE: connection timeout",
                "resume_offset_s": 70.0,
            },
            "local_compute": {
                "decoding_s": 25.7,
                "avg_gpu_util_pct": 45.0,
                "peak_vram_mb": 1250.0,
                "avg_power_w": 28.5,
                "est_energy_wh": 0.203,
                "tag": "GPU only, estimated",
            },
        }
        self.ledger.append_record(att, sync_to_account=True)

        records = self.ledger.read_event_records()
        self.assertEqual(len(records), 1)
        r = records[0]
        self.assertEqual(r["failover"]["resume_offset_s"], 70.0)
        self.assertEqual(r["local_compute"]["tag"], "GPU only, estimated")

    def test_security_sanitization_guards(self):
        """Verifies that no keys, auth tokens, bearer tokens, or audio bytes are written."""
        poisoned = {
            "record_type": "attempt",
            "attempt_id": "att-leak-test",
            "clip_name": "VID_safe.mp4",
            "authorization": "Bearer secret-token-xyz-123",
            "api_key": "nvapi-secret-key-abc",
            "nested": {
                "bearer_token": "bearer-456",
                "safe_field": 42,
            },
            "audio_bytes": b"RIFF....binary_audio",
        }
        self.ledger.append_record(poisoned)

        records = self.ledger.read_event_records()
        self.assertEqual(len(records), 1)
        rec = records[0]
        self.assertNotIn("authorization", rec)
        self.assertNotIn("api_key", rec)
        self.assertNotIn("audio_bytes", rec)
        self.assertNotIn("bearer_token", rec.get("nested", {}))
        self.assertEqual(rec["nested"]["safe_field"], 42)

    def test_rebuild_account_ledger(self):
        """rebuild_account_ledger correctly collates all event ledgers."""
        ev1 = os.path.join(self.temp_dir, "Event1", "scribe", "state", "usage")
        os.makedirs(ev1, exist_ok=True)
        with open(os.path.join(ev1, "ledger.jsonl"), "w", encoding="utf-8") as f:
            f.write(json.dumps({"record_type": "attempt", "engine_start": "cloud", "requests_count": 1, "started_at": "2026-11-07T10:00:00+05:30"}) + "\n")

        ev2 = os.path.join(self.temp_dir, "Event2", "scribe", "state", "usage")
        os.makedirs(ev2, exist_ok=True)
        with open(os.path.join(ev2, "ledger.jsonl"), "w", encoding="utf-8") as f:
            f.write(json.dumps({"record_type": "attempt", "engine_start": "cloud", "requests_count": 2, "started_at": "2026-11-07T10:05:00+05:30"}) + "\n")

        # Ignore folder starting with underscore
        ev_ignored = os.path.join(self.temp_dir, "_ignored_event", "scribe", "state", "usage")
        os.makedirs(ev_ignored, exist_ok=True)
        with open(os.path.join(ev_ignored, "ledger.jsonl"), "w", encoding="utf-8") as f:
            f.write(json.dumps({"record_type": "attempt", "engine_start": "cloud", "requests_count": 99, "started_at": "2026-11-07T10:10:00+05:30"}) + "\n")

        target_acc = os.path.join(self.temp_dir, "_usage", "rebuilt_account.jsonl")
        count = UsageLedger.rebuild_account_ledger(self.temp_dir, target_acc)
        self.assertEqual(count, 2)

        acc_records = UsageLedger.read_ledger_file(target_acc)
        self.assertEqual(len(acc_records), 2)
        self.assertEqual(acc_records[0]["requests_count"], 1)
        self.assertEqual(acc_records[1]["requests_count"], 2)


class TestLocalComputeSampler(unittest.TestCase):
    """Test LocalComputeSampler logic and formatting."""

    def test_sampler_start_stop_structure(self):
        sampler = LocalComputeSampler(interval_sec=0.1)
        sampler.start()
        # Sleep briefly to collect or simulate
        import time
        time.sleep(0.25)
        stats = sampler.stop()

        self.assertIn("decoding_s", stats)
        self.assertIn("avg_power_w", stats)
        self.assertIn("est_energy_wh", stats)
        self.assertEqual(stats["tag"], "GPU only, estimated")


if __name__ == "__main__":
    unittest.main()

