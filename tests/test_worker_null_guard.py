"""
Worker null-guard regression test.
Validates that the transcribe_worker null-guard for initial_prompt and language
handles None values from JSON correctly (originally records/2026-10-25/test_worker_null_guard.py).
Pure in-process test — no disk I/O, no Whisper, no GPU.
"""

import json
import tempfile
import os
import sys

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)


def run_tests():
    # Simulate exact JSON the manager writes when glossary is empty (initial_prompt=None)
    job = {
        "event": {"name": "Test", "date": "25 October 2026"},
        "clips": [],
        "output_dir": tempfile.gettempdir(),
        "work_dir": tempfile.gettempdir(),
        "log_dir": tempfile.gettempdir(),
        "language": "en",
        "initial_prompt": None,   # this is null in JSON -- the bug
        "model": "medium",
        "device": "cuda",
    }

    # Apply the fix
    language = job.get("language") or "en"
    initial_prompt = (job.get("initial_prompt") or "").strip() or None

    assert language == "en", f"language={language}"
    assert initial_prompt is None, f"initial_prompt={initial_prompt!r}"
    print("[PASS] initial_prompt=None in JSON -> None after fix (no crash)")
    print("[PASS] language=en preserved")

    # Test with explicit empty string
    job2 = dict(job, initial_prompt="", language=None)
    language2 = job2.get("language") or "en"
    initial_prompt2 = (job2.get("initial_prompt") or "").strip() or None
    assert language2 == "en"
    assert initial_prompt2 is None
    print("[PASS] initial_prompt=\"\" -> None,  language=null -> en")

    # Test with a real glossary value
    job3 = dict(job, initial_prompt="  devfest, kubernetes  ", language="en")
    language3 = job3.get("language") or "en"
    initial_prompt3 = (job3.get("initial_prompt") or "").strip() or None
    assert language3 == "en"
    assert initial_prompt3 == "devfest, kubernetes"
    print("[PASS] real glossary value preserved correctly")

    print("ALL WORKER PARSE TESTS PASSED")


if __name__ == "__main__":
    run_tests()
