"""
Inline runner for the three MediaSeparator layout cases.
Equivalent to test_stage2_layout_acceptance.py but written as a compact
assertion script (originally records/2026-10-25/layout_test_runner.py).
Kept for historical reference; prefer test_stage2_layout_acceptance.py
for new test runs.
"""

import sys
import os
import shutil
import tempfile

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from scribe_core.media_separator import MediaSeparator

sep = MediaSeparator()


def dvh(data_dir):
    if not data_dir or not os.path.isdir(data_dir):
        return False
    for f in os.listdir(data_dir):
        if os.path.isfile(os.path.join(data_dir, f)) and sep.classify_extension(f) == "video":
            return True
    vdir = os.path.join(data_dir, "Videos")
    if os.path.isdir(vdir):
        for f in os.listdir(vdir):
            if os.path.isfile(os.path.join(vdir, f)) and sep.classify_extension(f) == "video":
                return True
    return False


def run():
    tmp = tempfile.mkdtemp()
    try:
        # Case 1: flat mixed
        d1 = os.path.join(tmp, "A", "data")
        for n in ["VID001.mp4", "VID002.mp4", "IMG001.jpg", "IMG002.jpg", "notes.txt"]:
            os.makedirs(d1, exist_ok=True)
            open(os.path.join(d1, n), "wb").write(b"\x00" * 16)
        p = sep.dry_run(d1)
        assert p.video_count == 2 and p.photo_count == 2 and p.unknown_count == 1 and dvh(d1) is True
        print("[PASS] Case1 flat: v=%d ph=%d unk=%d has_vids=%s" % (
            p.video_count, p.photo_count, p.unknown_count, dvh(d1)))

        # Case 2: pre-separated
        d2 = os.path.join(tmp, "B", "data")
        for sub, n in [("Videos", "VID001.mp4"), ("Videos", "VID002.mp4"), ("Photos", "IMG001.jpg")]:
            fp = os.path.join(d2, sub, n)
            os.makedirs(os.path.dirname(fp), exist_ok=True)
            open(fp, "wb").write(b"\x00" * 16)
        pr = sep.dry_run(d2)
        assert pr.video_count == 0 and pr.photo_count == 0 and pr.already_separated_count == 3 and dvh(d2) is True
        print("[PASS] Case2 separated: to_move=%d already=%d has_vids=%s" % (
            pr.video_count + pr.photo_count, pr.already_separated_count, dvh(d2)))

        # Case 3: empty data
        d3 = os.path.join(tmp, "C", "data")
        os.makedirs(d3, exist_ok=True)
        pr3 = sep.dry_run(d3)
        assert pr3.video_count == 0 and pr3.photo_count == 0 and dvh(d3) is False
        print("[PASS] Case3 empty: to_move=%d has_vids=%s" % (
            pr3.video_count + pr3.photo_count, dvh(d3)))

        print("ALL 3 TESTS PASSED")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    run()
