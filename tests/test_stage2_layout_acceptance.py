"""
Stage 2 layout acceptance test.
Tests three data\\ layouts without touching real event data:
  1. Mixed flat   — photos + videos directly in data\\ (Devfest-style)
  2. Pre-separated — data\\Videos\\ and data\\Photos\\ already exist
  3. Empty data\\ — no media files at all

Validates:
  - MediaSeparator.dry_run() classifies and counts correctly in each case
  - _data_dir_has_videos() logic (inlined here) returns the right result
  - Confirm & Move should only be enabled (video_count + photo_count > 0)
"""

import os
import sys
import shutil
import tempfile

# Ensure project root is on path regardless of where the test is invoked from.
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from scribe_core.media_separator import MediaSeparator

sep = MediaSeparator()


# ---------------------------------------------------------------------------
# Helper: mirror of _data_dir_has_videos from stage2_tab.py
# ---------------------------------------------------------------------------
def data_dir_has_videos(data_dir: str) -> bool:
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


def create_dummy_file(path: str, size: int = 16):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(b"\x00" * size)


def run_tests():
    print("=" * 70)
    print("STAGE 2 MEDIA LAYOUT ACCEPTANCE TESTS")
    print("=" * 70)

    tmp = tempfile.mkdtemp(prefix="session_scribe_test_")
    try:
        # ------------------------------------------------------------------
        # CASE 1: Mixed flat layout  (Devfest-style)
        # ------------------------------------------------------------------
        print("\n--- Case 1: Mixed flat layout (photos + videos in data\\) ---")
        event_a = os.path.join(tmp, "EventA")
        data_a = os.path.join(event_a, "data")

        dummy_files = [
            ("VID20261024121131.mp4", "video"),
            ("VID20261024131202.mp4", "video"),
            ("IMG20261024104304.jpg", "photo"),
            ("IMG20261024114355.jpg", "photo"),
            ("notes.txt", "unknown"),
        ]
        for name, _ in dummy_files:
            create_dummy_file(os.path.join(data_a, name))

        preview = sep.dry_run(data_a)
        assert preview.video_count == 2, f"Expected 2 videos, got {preview.video_count}"
        assert preview.photo_count == 2, f"Expected 2 photos, got {preview.photo_count}"
        assert preview.unknown_count == 1, f"Expected 1 unknown, got {preview.unknown_count}"
        assert preview.already_separated_count == 0
        assert (preview.video_count + preview.photo_count) > 0  # Confirm should be enabled
        assert data_dir_has_videos(data_a) is True
        print(f"  [PASS] dry_run: {preview.video_count} videos, {preview.photo_count} photos, "
              f"{preview.unknown_count} unknowns")
        print(f"  [PASS] data_dir_has_videos: True (flat)")

        # ------------------------------------------------------------------
        # CASE 2: Pre-separated layout  (data\\Videos\\ + data\\Photos\\ already exist)
        # ------------------------------------------------------------------
        print("\n--- Case 2: Pre-separated layout (data\\Videos\\ and data\\Photos\\ exist) ---")
        event_b = os.path.join(tmp, "EventB")
        data_b = os.path.join(event_b, "data")
        videos_b = os.path.join(data_b, "Videos")
        photos_b = os.path.join(data_b, "Photos")

        create_dummy_file(os.path.join(videos_b, "VID20261024121131.mp4"))
        create_dummy_file(os.path.join(videos_b, "VID20261024131202.mp4"))
        create_dummy_file(os.path.join(photos_b, "IMG20261024104304.jpg"))

        preview_b = sep.dry_run(data_b)
        assert preview_b.video_count == 0, f"Expected 0 to move (already in Videos/), got {preview_b.video_count}"
        assert preview_b.photo_count == 0, f"Expected 0 to move (already in Photos/), got {preview_b.photo_count}"
        assert preview_b.already_separated_count == 3
        total_to_move_b = preview_b.video_count + preview_b.photo_count
        assert total_to_move_b == 0, "Confirm should be disabled — nothing to move"
        assert data_dir_has_videos(data_b) is True  # Videos subfolder found
        print(f"  [PASS] dry_run: {preview_b.video_count} to move, {preview_b.already_separated_count} already separated")
        print(f"  [PASS] data_dir_has_videos: True (Videos subfolder)")
        print(f"  [PASS] total_to_move == 0 -> Confirm & Move stays disabled")

        # ------------------------------------------------------------------
        # CASE 3: Empty data\\ folder  (no media at all)
        # ------------------------------------------------------------------
        print("\n--- Case 3: Empty data\\ folder ---")
        event_c = os.path.join(tmp, "EventC")
        data_c = os.path.join(event_c, "data")
        os.makedirs(data_c, exist_ok=True)

        preview_c = sep.dry_run(data_c)
        assert preview_c.video_count == 0
        assert preview_c.photo_count == 0
        assert preview_c.already_separated_count == 0
        total_to_move_c = preview_c.video_count + preview_c.photo_count
        assert total_to_move_c == 0, "Confirm should be disabled — empty folder"
        assert data_dir_has_videos(data_c) is False
        print(f"  [PASS] dry_run: 0 videos, 0 photos (empty folder)")
        print(f"  [PASS] data_dir_has_videos: False")
        print(f"  [PASS] total_to_move == 0 -> Confirm & Move stays disabled")
        print(f"  [PASS] Detect Sessions button would be disabled (no videos found)")

    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n" + "=" * 70)
    print("ALL LAYOUT TESTS PASSED")
    print("=" * 70)


if __name__ == "__main__":
    run_tests()
