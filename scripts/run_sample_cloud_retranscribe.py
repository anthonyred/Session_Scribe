"""
scripts/run_sample_cloud_retranscribe.py

Executes a live cloud re-transcription of VID20261104114955.mp4 in D:\\Events\\Sample_test
using NVIDIA Riva Parakeet TDT 0.6B v2 in Compare mode.

Complies strictly with AGENTS.md:
- Zero credential printing (never print keys, tokens, or passphrases).
- Zero voice data / personal names printed.
- Verifies uploaded bytes, requests count, timing, WER, and usage logging.
"""

import json
import os
import sys
import time

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_SCRIPT_DIR)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from scribe_core import run_comparison, model_registry


def progress_cb(info):
    stage = info.get("stage", "")
    pct = info.get("percent", 0.0)
    msg = info.get("message", "")
    print(f"[{pct:5.1f}%] [{stage.upper()}] {msg}")


def main():
    event_root = r"D:\Events\Sample_test"
    clip_dict = {
        "name": "VID20261104114955.mp4",
        "path": os.path.join(event_root, "data", "Videos", "VID20261104114955.mp4"),
        "speaker": "speaker 1",
    }
    model_id = "nvidia/parakeet-tdt-0.6b-v2"

    print("==================================================================")
    print("SESSION SCRIBE: LIVE CLOUD RE-TRANSCRIPTION EXECUTION")
    print(f"Target Clip: {clip_dict['name']}")
    print(f"Event Root:  {event_root}")
    print(f"Model ID:    {model_id}")
    print("==================================================================")

    t0 = time.time()
    try:
        run_res = run_comparison.execute_clip_retranscription(
            event_root=event_root,
            clip_dict=clip_dict,
            model_id=model_id,
            mode="compare",
            progress_callback=progress_cb,
        )
    except Exception as exc:
        print(f"\nExecution FAILED with error: {type(exc).__name__}: {exc}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

    t_total = time.time() - t0
    run_rec = run_res.get("run_rec", {})
    print("\n==================================================================")
    print("RE-TRANSCRIPTION COMPLETED SUCCESSFULLY")
    print(f"Total wall time: {t_total:.2f}s")
    print(f"Run ID:         {run_rec.get('run_id')}")
    print(f"Model Name:     {run_res.get('model_name')}")
    print(f"Duration (sec): {run_res.get('audio_dur_sec'):.2f}s")
    print(f"Time Taken:     {run_res.get('elapsed_sec'):.2f}s")
    print(f"Uploaded Bytes: {run_res.get('uploaded_bytes'):,} bytes")
    print(f"Requests Count: {run_res.get('requests_count')}")
    print(f"Speedup:        {run_res.get('speedup')}x")
    print(f"WER vs Run 1:   {run_rec.get('wer_vs_baseline', 0.0) * 100:.2f}%")
    print(f"Char Diff %:    {run_rec.get('char_diff_pct', 0.0):.2f}%")
    print(f"Cost USD:       ${run_res.get('cost_usd', 0.0):.4f}")
    print("==================================================================")

    # 1. Verify runs.json
    history_file = os.path.join(event_root, "scribe", "state", "run_history", "VID20261104114955_runs.json")
    if os.path.isfile(history_file):
        with open(history_file, "r", encoding="utf-8") as fh:
            runs_data = json.load(fh)
        print(f"\n[runs.json Verification: {len(runs_data)} total runs recorded]")
        for r in runs_data:
            print(f"  Run #{r.get('run_id')}: {r.get('model_name')} | Duration: {r.get('duration_sec')}s | Taken: {r.get('time_taken_sec')}s | WER: {r.get('wer_vs_baseline')} | Primary: {r.get('is_primary')}")

    # 2. Verify attempts.csv
    attempts_csv = os.path.join(event_root, "scribe", "usage", "attempts.csv")
    if os.path.isfile(attempts_csv):
        print(f"\n[attempts.csv Verification: {attempts_csv}]")
        with open(attempts_csv, "r", encoding="utf-8") as fh:
            lines = fh.readlines()
            for line in lines[-5:]:
                print("  " + line.strip())

    # 3. Verify runs.csv
    runs_csv = os.path.join(event_root, "scribe", "usage", "runs.csv")
    if os.path.isfile(runs_csv):
        print(f"\n[runs.csv Verification: {runs_csv}]")
        with open(runs_csv, "r", encoding="utf-8") as fh:
            lines = fh.readlines()
            for line in lines[-5:]:
                print("  " + line.strip())

    # 4. Verify ledger.jsonl
    ledger_path = r"D:\Events\_usage\ledger.jsonl"
    if os.path.isfile(ledger_path):
        print(f"\n[ledger.jsonl Verification: {ledger_path}]")
        with open(ledger_path, "r", encoding="utf-8") as fh:
            lines = fh.readlines()
            for line in lines[-3:]:
                try:
                    entry = json.loads(line)
                    # scrub any sensitive fields
                    print(f"  Ledger entry: timestamp={entry.get('timestamp')}, audio_seconds={entry.get('audio_seconds')}, cost_usd=${entry.get('cost_usd')}, model={entry.get('model')}")
                except Exception:
                    pass


if __name__ == "__main__":
    main()
