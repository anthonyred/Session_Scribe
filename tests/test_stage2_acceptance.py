"""
Stage 2 Acceptance Test Script
Validates:
1. Elevate data folder: 16 videos group into 4 sessions of 3, 3, 9, and 1 clips.
2. Specific photos assignment:
   - IMG20260905114522_01 (Expected: Speaker 1)
   - IMG20260905151815_01 (Expected: Speaker 3)
   - IMG20260905152136_01 (Expected: Speaker 3)
   - IMG20260905152930_01 (Expected: Speaker 3)
   - IMG20260905152951_01 (Expected: Speaker 3)
3. DevX data (28 videos) groups into 2 sessions of 14 and 14.
4. Media separation lifecycle:
   - Dry run changes nothing on disk
   - Execution moves files, preserves timestamps, creates move_log.json, verifies counts match
   - Undo restores original layout, preserves timestamps, and verifies counts
5. Event details validation & manifest serialization.
"""

import os
import sys
import shutil
import tempfile
from datetime import datetime, timedelta

# Ensure project root is on path regardless of where the test is invoked from.
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from scribe_core.time_parser import TimeParser, IST
from scribe_core.clip_probe import ClipProbe
from scribe_core.session_grouper import SessionGrouper, SessionInfo, ClipInfo, PhotoInfo
from scribe_core.media_separator import MediaSeparator
from scribe_core.manifest import ManifestManager, EventDetails

def test_elevate_and_devx():
    print("=" * 70)
    print("1. ELEVATE & DEVX SESSIONS & PHOTO ASSIGNMENT ACCEPTANCE CHECKS")
    print("=" * 70)

    time_parser = TimeParser()
    clip_probe = ClipProbe()

    # --- ELEVATE CHECK ---
    elevate_videos_dir = r"D:\Events\17.ElevateUai_cybersecurity\Photos-1-001\Videos"
    assert os.path.isdir(elevate_videos_dir), f"Elevate videos directory not found: {elevate_videos_dir}"

    video_files = [f for f in os.listdir(elevate_videos_dir) if f.lower().endswith(".mp4")]
    print(f"Found {len(video_files)} video clips in Elevate.")
    assert len(video_files) == 16, f"Expected 16 videos in Elevate, found {len(video_files)}"

    elevate_clips = []
    for f in video_files:
        path = os.path.join(elevate_videos_dir, f)
        dt, src = time_parser.get_timestamp(path, is_photo=False)
        dur = clip_probe.get_duration(path)
        end_dt = dt + timedelta(seconds=dur)
        elevate_clips.append(ClipInfo(
            filename=f,
            filepath=path,
            start_time=dt,
            duration=dur,
            end_time=end_dt,
            time_source=src
        ))

    grouper = SessionGrouper(gap_threshold_seconds=600.0, photo_grace_seconds=180.0) # 10m gap, 3m grace
    elevate_sessions = grouper.group_clips(elevate_clips)

    session_counts = [len(s.clips) for s in elevate_sessions]
    print(f"Elevate Sessions Detected: {len(elevate_sessions)}")
    for s in elevate_sessions:
        print(f"  {s.speaker_name}: {len(s.clips)} clips (from {s.start_time.strftime('%H:%M:%S')} to {s.end_time.strftime('%H:%M:%S')})")

    assert session_counts == [3, 3, 9, 1], f"Expected Elevate session counts [3, 3, 9, 1], got {session_counts}"
    print("[PASS] Elevate session grouping test PASSED: 16 videos grouped into 4 sessions of 3, 3, 9, 1 clips.")

    # --- ELEVATE PHOTOS CHECK ---
    elevate_photos_dir = r"D:\Events\17.ElevateUai_cybersecurity\Photos-1-001\Photos"
    all_photo_files = [f for f in os.listdir(elevate_photos_dir) if f.lower().endswith((".jpg", ".png"))]
    print(f"\nFound {len(all_photo_files)} photos in Elevate.")

    elevate_photos = []
    for f in all_photo_files:
        path = os.path.join(elevate_photos_dir, f)
        dt, src = time_parser.get_timestamp(path, is_photo=True)
        elevate_photos.append(PhotoInfo(
            filename=f,
            filepath=path,
            timestamp=dt,
            time_source=src
        ))

    elevate_sessions, unassigned = grouper.assign_photos(elevate_photos, elevate_sessions)
    print(f"Total Photos: {len(elevate_photos)} | Assigned: {sum(len(s.photos) for s in elevate_sessions)} | Unassigned: {len(unassigned)}")

    # Verify specific test photos from prompt
    target_photo_names = [
        "IMG20260905114522_01",
        "IMG20260905151815_01",
        "IMG20260905152136_01",
        "IMG20260905152930_01",
        "IMG20260905152951_01",
    ]

    print("\nTarget Photos Assignment Results:")
    photo_map = {os.path.splitext(p.filename)[0]: p for p in elevate_photos}

    for name in target_photo_names:
        p = photo_map.get(name)
        if p:
            print(f"  {p.filename} (Time: {p.timestamp.strftime('%H:%M:%S')}) -> {p.assigned_speaker} [Status: {p.assignment_status}]")
        else:
            print(f"  {name} NOT FOUND in photo directory!")

    p1 = photo_map.get("IMG20260905114522_01")
    assert p1 and p1.assigned_speaker == "Speaker 1", f"Expected IMG20260905114522_01 -> Speaker 1, got {p1.assigned_speaker if p1 else 'None'}"

    for p_name in ["IMG20260905151815_01", "IMG20260905152136_01", "IMG20260905152930_01", "IMG20260905152951_01"]:
        p = photo_map.get(p_name)
        assert p and p.assigned_speaker == "Speaker 3", f"Expected {p_name} -> Speaker 3, got {p.assigned_speaker if p else 'None'}"

    print("[PASS] Target photos assignment test PASSED: Speaker 1 for first photo, Speaker 3 for other four.")

    # --- DEVX CHECK ---
    print("\n" + "-" * 70)
    devx_videos_dir = r"D:\Events\13. DevX\Photos-1-001 (1)\Videos"
    assert os.path.isdir(devx_videos_dir), f"DevX videos directory not found: {devx_videos_dir}"

    devx_files = [f for f in os.listdir(devx_videos_dir) if f.lower().endswith(".mp4")]
    print(f"Found {len(devx_files)} videos in DevX.")
    assert len(devx_files) == 28, f"Expected 28 videos in DevX, found {len(devx_files)}"

    devx_clips = []
    for f in devx_files:
        path = os.path.join(devx_videos_dir, f)
        dt, src = time_parser.get_timestamp(path, is_photo=False)
        dur = clip_probe.get_duration(path)
        end_dt = dt + timedelta(seconds=dur)
        devx_clips.append(ClipInfo(
            filename=f,
            filepath=path,
            start_time=dt,
            duration=dur,
            end_time=end_dt,
            time_source=src
        ))

    devx_sessions = grouper.group_clips(devx_clips)
    devx_counts = [len(s.clips) for s in devx_sessions]
    print(f"DevX Sessions Detected: {len(devx_sessions)}")
    for s in devx_sessions:
        print(f"  {s.speaker_name}: {len(s.clips)} clips (from {s.start_time.strftime('%H:%M:%S')} to {s.end_time.strftime('%H:%M:%S')})")

    assert devx_counts == [14, 14], f"Expected DevX session counts [14, 14], got {devx_counts}"
    print("[PASS] DevX session grouping test PASSED: 28 videos grouped into 2 sessions of 14 and 14 clips.")


def test_media_separation_and_undo():
    print("\n" + "=" * 70)
    print("2. MEDIA SEPARATION DRY-RUN, EXECUTION, AND UNDO ACCEPTANCE CHECK")
    print("=" * 70)

    # Create temporary mock event using tempfile so nothing lands in the project tree.
    # NOTE: same-drive instant-move is tested on D: by default because tempfile.gettempdir()
    # resolves to a D: path on this machine; if it ever resolves to C: the test still passes
    # (os.rename falls back to copy+delete), but timestamps may vary by ~1 s.
    _tmp_root = tempfile.mkdtemp(prefix="session_scribe_sep_test_")
    test_event_dir = os.path.join(_tmp_root, "event")
    test_data_dir = os.path.join(test_event_dir, "data")
    test_scribe_dir = os.path.join(test_event_dir, "scribe")
    os.makedirs(test_data_dir)

    # Populate mock files:
    # 3 videos, 3 photos, 2 unknown files, plus 1 existing video in Videos/ to test collision
    mock_files = {
        "VID_20261023_100000.mp4": "video content 1",
        "VID_20261023_101000.mov": "video content 2",
        "VID_20261023_102000.mkv": "video content 3",
        "IMG_20261023_100001.jpg": "photo content 1",
        "IMG_20261023_100002.png": "photo content 2",
        "IMG_20261023_100003.webp": "photo content 3",
        "notes.txt": "unknown text file",
        "presentation.pdf": "unknown pdf file",
    }

    original_mtimes = {}
    for fname, content in mock_files.items():
        p = os.path.join(test_data_dir, fname)
        with open(p, "w") as f:
            f.write(content)
        # Set distinct mtime
        t = datetime(2026, 10, 23, 10, 0, 0, tzinfo=IST).timestamp()
        os.utime(p, (t, t))
        original_mtimes[fname] = t

    # Create pre-existing collision file in Videos
    pre_videos = os.path.join(test_data_dir, "Videos")
    os.makedirs(pre_videos)
    colliding_file = os.path.join(pre_videos, "VID_20261023_100000.mp4")
    with open(colliding_file, "w") as f:
        f.write("pre-existing destination file")

    initial_total_files = 8 + 1 # 8 in data/ + 1 in data/Videos/
    separator = MediaSeparator()

    # Step 1: Dry run
    print("Executing Dry-Run...")
    preview = separator.dry_run(test_data_dir)
    print(f"  Dry run report: {preview.video_count} videos, {preview.photo_count} photos, {preview.unknown_count} unknowns, {preview.collision_count} collisions.")
    assert preview.video_count == 3
    assert preview.photo_count == 3
    assert preview.unknown_count == 2
    assert preview.collision_count == 1
    assert preview.total_files_before == initial_total_files

    # Verify dry run changed nothing on disk
    files_in_data = [f for f in os.listdir(test_data_dir) if os.path.isfile(os.path.join(test_data_dir, f))]
    assert len(files_in_data) == 8, f"Dry run altered disk! Expected 8 files in data/, got {len(files_in_data)}"
    print("[PASS] Dry run verified: 0 changes made to disk.")

    # Step 2: Confirmation & Execution
    print("\nExecuting Move with Confirmation...")
    res = separator.separate(test_data_dir, test_scribe_dir)
    print(f"  Result: {res.message}")
    assert res.success is True
    assert res.verification_passed is True
    assert res.videos_moved == 3
    assert res.photos_moved == 3
    assert res.collisions_handled == 1
    assert os.path.exists(res.move_log_path)

    # Verify both collision files exist
    assert os.path.exists(colliding_file)
    with open(colliding_file) as f:
        assert f.read() == "pre-existing destination file", "Original target was overwritten! Overwrite policy violated."
    
    # Check preserved mtimes
    moved_photo = os.path.join(test_data_dir, "Photos", "IMG_20261023_100001.jpg")
    assert abs(os.path.getmtime(moved_photo) - original_mtimes["IMG_20261023_100001.jpg"]) < 1.0
    print("[PASS] Separation verified: All files moved correctly, timestamps preserved, collision preserved without overwrite, counts match.")

    # Step 3: Undo
    print("\nExecuting Undo...")
    undo_res = separator.undo(test_scribe_dir)
    print(f"  Undo Result: {undo_res.message}")
    assert undo_res.success is True
    assert undo_res.restored_count == 6 # 3 videos + 3 photos

    # Verify original files are back in data/
    restored_in_data = [f for f in os.listdir(test_data_dir) if os.path.isfile(os.path.join(test_data_dir, f))]
    assert len(restored_in_data) == 8
    for fname in mock_files:
        p = os.path.join(test_data_dir, fname)
        assert os.path.exists(p)
        assert abs(os.path.getmtime(p) - original_mtimes[fname]) < 1.0

    print("[PASS] Undo verified: All files restored to exact original layout with timestamps preserved.")

    # Clean up temp tree
    shutil.rmtree(_tmp_root, ignore_errors=True)
    print("[PASS] Cleaned up scratch test event folder.")


def test_manifest():
    print("\n" + "=" * 70)
    print("3. MANIFEST GENERATION AND VALIDATION CHECK")
    print("=" * 70)

    test_scribe_dir = tempfile.mkdtemp(prefix="session_scribe_manifest_test_")

    event = EventDetails(
        event_name="CYBER & BEYOND 2026",
        date="2026-09-05",
        organiser="elevateU.ai in collaboration with Quality Thought",
        venue="N-Heights building 5th floor, Hyderabad",
        sponsors="", # Blank sponsors should be omitted
        speaker_mode="single"
    )

    warnings = event.validate()
    print(f"Event validation warnings (should be empty): {warnings}")
    assert len(warnings) == 0

    dummy_clips = [
        ClipInfo("VID1.mp4", "path/VID1.mp4", datetime(2026, 9, 5, 11, 0, tzinfo=IST), 60.0, datetime(2026, 9, 5, 11, 1, tzinfo=IST)),
        ClipInfo("VID2.mp4", "path/VID2.mp4", datetime(2026, 9, 5, 11, 2, tzinfo=IST), 120.0, datetime(2026, 9, 5, 11, 4, tzinfo=IST))
    ]
    dummy_photos = [
        PhotoInfo("IMG1.jpg", "path/IMG1.jpg", datetime(2026, 9, 5, 11, 1, tzinfo=IST), 1, "Speaker 1", "Assigned")
    ]
    unassigned = [
        PhotoInfo("IMG_unassigned.jpg", "path/IMG_unassigned.jpg", datetime(2026, 9, 5, 9, 0, tzinfo=IST), None, None, "Unassigned")
    ]
    sess = SessionInfo(1, "Speaker 1", dummy_clips, dummy_photos)

    path, warn = ManifestManager.save_manifest(
        scribe_dir=test_scribe_dir,
        event=event,
        sessions=[sess],
        unassigned_photos=unassigned,
        settings_used={"session_gap_threshold_seconds": 600, "photo_grace_seconds": 180}
    )

    assert os.path.exists(path)
    loaded = ManifestManager.load_manifest(path)
    print("Loaded manifest successfully.")
    assert loaded["event"]["name"] == "CYBER & BEYOND 2026"
    assert "sponsors" not in loaded["event"], "Blank sponsors was not omitted!"
    assert loaded["summary"]["total_sessions"] == 1
    assert loaded["summary"]["total_clips"] == 2
    assert loaded["summary"]["assigned_photos"] == 1
    assert loaded["summary"]["unassigned_photos"] == 1

    shutil.rmtree(test_scribe_dir)
    print("[PASS] Manifest generation and validation test PASSED.")

if __name__ == "__main__":
    test_elevate_and_devx()
    test_media_separation_and_undo()
    test_manifest()
    print("\n" + "=" * 70)
    print("ALL STAGE 2 ACCEPTANCE TESTS PASSED SUCCESSFULLY!")
    print("=" * 70)
