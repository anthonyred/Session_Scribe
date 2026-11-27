"""
scripts/verify_retranscribe_result.py
Inspects run_history, attempts.csv, runs.csv, and ledger.jsonl.
Complies with AGENTS.md (no secrets, no personal names, no voice data).
"""

import json
import os

event_root = r"D:\Events\Sample_test"
history_file = os.path.join(event_root, "scribe", "state", "run_history", "VID20261104114955_runs.json")

print("==================================================================")
print("1. RUN HISTORY VERIFICATION")
print(f"File: {history_file}")
if os.path.isfile(history_file):
    with open(history_file, "r", encoding="utf-8") as fh:
        runs = json.load(fh)
    print(f"Total runs: {len(runs)}")
    for r in runs:
        print(f"  Run #{r.get('run_id')}:")
        print(f"    Model:       {r.get('model_name')} ({r.get('model_id')})")
        print(f"    Provider:    {r.get('provider')}")
        print(f"    Duration:    {r.get('duration_sec')}s")
        print(f"    Time Taken:  {r.get('time_taken_sec')}s")
        print(f"    Speedup:     {r.get('speedup_ratio')}x")
        print(f"    Cost USD:    ${r.get('cost_usd')}")
        print(f"    WER vs Run1: {r.get('wer_vs_baseline') * 100:.2f}%")
        print(f"    Char Diff:   {r.get('char_diff_pct')}%")
        print(f"    Is Primary:  {r.get('is_primary')}")
        print(f"    Timestamp:   {r.get('timestamp')}")
else:
    print("  File not found!")

print("\n==================================================================")
print("2. ATTEMPTS.CSV VERIFICATION")
attempts_csv = os.path.join(event_root, "scribe", "usage", "attempts.csv")
print(f"File: {attempts_csv}")
if os.path.isfile(attempts_csv):
    with open(attempts_csv, "r", encoding="utf-8") as fh:
        lines = fh.readlines()
    print(f"Total rows: {len(lines)}")
    for line in lines[-5:]:
        print("  " + line.strip())
else:
    print("  File not found!")

print("\n==================================================================")
print("3. RUNS.CSV VERIFICATION")
runs_csv = os.path.join(event_root, "scribe", "usage", "runs.csv")
print(f"File: {runs_csv}")
if os.path.isfile(runs_csv):
    with open(runs_csv, "r", encoding="utf-8") as fh:
        lines = fh.readlines()
    print(f"Total rows: {len(lines)}")
    for line in lines[-5:]:
        print("  " + line.strip())
else:
    print("  File not found!")

print("\n==================================================================")
print("4. LEDGER.JSONL VERIFICATION")
ledger_file = os.path.join(event_root, "scribe", "state", "usage", "ledger.jsonl")
print(f"File: {ledger_file}")
if os.path.isfile(ledger_file):
    with open(ledger_file, "r", encoding="utf-8") as fh:
        lines = fh.readlines()
    print(f"Total entries: {len(lines)}")
    for line in lines[-3:]:
        entry = json.loads(line)
        print(f"  Attempt: clip={entry.get('clip_name')}, model={entry.get('model')}, dur={entry.get('duration_sec')}s, time={entry.get('processing_time_sec')}s, speedup={entry.get('speedup_ratio')}x, uploaded_bytes={entry.get('uploaded_bytes')}, reqs={entry.get('requests_count')}, cost=${entry.get('cost_usd')}, status={entry.get('status')}")
else:
    print("  File not found!")

print("\n==================================================================")
print("5. ACCOUNT ATTEMPTS VERIFICATION")
account_csv = r"D:\Events\_usage\account_attempts.csv"
print(f"File: {account_csv}")
if os.path.isfile(account_csv):
    with open(account_csv, "r", encoding="utf-8") as fh:
        lines = fh.readlines()
    print(f"Total rows: {len(lines)}")
    for line in lines[-3:]:
        print("  " + line.strip())
else:
    print("  File not found!")
print("==================================================================")
