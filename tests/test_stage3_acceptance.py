"""
Stage 3 Acceptance Test Script
Validates:
1. Test Gate locking behavior (Run all locked until approved).
2. Two Elevate test clips transcribed into scribe\\test\\:
   - Output layout matches old format exactly (header, absolute timestamps, speaker).
   - Per-clip time and real-time speed factor reported.
   - No temporary WAV files left behind in scribe\\work\\.
3. Test approval gate unlocks full run.
4. Resume capability: Clips with existing transcripts are skipped instantly.
5. Full Elevate run: All 16 clips succeed in single-speaker mode.
6. Worker process isolation ensures VRAM returns to idle after execution.
"""

import os
import sys
import json
import shutil
import time
from datetime import datetime, timedelta
import pytest

# Skip legacy suite touching real D:\Events directly per AGENTS.md §4
pytestmark = pytest.mark.skip(
    reason="Legacy test touches real D:\\Events directly. See AGENTS.md §4."
)

# Ensure project root is on path regardless of where the test is invoked from.
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import torch
from scribe_core.time_parser import TimeParser, IST
from scribe_core.clip_probe import ClipProbe
from scribe_core.session_grouper import SessionGrouper, ClipInfo, PhotoInfo
from scribe_core.manifest import ManifestManager, EventDetails
from scribe_core.transcription_manager import TranscriptionManager


def setup_elevate_manifest(event_dir: str, scribe_dir: str) -> dict:
    """Prepares and saves manifest.json for 17.ElevateUai_cybersecurity if not already present."""
    manifest_path = os.path.join(scribe_dir, "manifest.json")
    if os.path.exists(manifest_path):
        return ManifestManager.load_manifest(manifest_path)

    videos_dir = os.path.join(event_dir, "Photos-1-001", "Videos")
    photos_dir = os.path.join(event_dir, "Photos-1-001", "Photos")

    time_parser = TimeParser()
    clip_probe = ClipProbe()

    video_files = [f for f in os.listdir(videos_dir) if f.lower().endswith(".mp4")]
    clips = []
    for f in video_files:
        path = os.path.join(videos_dir, f)
        dt, src = time_parser.get_timestamp(path, is_photo=False)
        dur = clip_probe.get_duration(path)
        clips.append(ClipInfo(
            filename=f,
            filepath=path,
            start_time=dt,
            duration=dur,
            end_time=dt + timedelta(seconds=dur),
            time_source=src
        ))

    grouper = SessionGrouper(gap_threshold_seconds=600.0, photo_grace_seconds=180.0)
    sessions = grouper.group_clips(clips)

    photos = []
    if os.path.exists(photos_dir):
        photo_files = [f for f in os.listdir(photos_dir) if f.lower().endswith((".jpg", ".png"))]
        for f in photo_files:
            path = os.path.join(photos_dir, f)
            dt, src = time_parser.get_timestamp(path, is_photo=True)
            photos.append(PhotoInfo(filename=f, filepath=path, timestamp=dt, time_source=src))

    sessions, unassigned = grouper.assign_photos(photos, sessions)

    event = EventDetails(
        event_name="CYBER & BEYOND 2026, organised by elevateU.ai in collaboration with Quality Thought",
        date="5 September 2026",
        organiser="elevateU.ai in collaboration with Quality Thought",
        venue="N-Heights building 5th floor, Hyderabad",
        sponsors="",
        speaker_mode="single"
    )

    path, _ = ManifestManager.save_manifest(
        scribe_dir=scribe_dir,
        event=event,
        sessions=sessions,
        unassigned_photos=unassigned,
        settings_used={"session_gap_threshold_seconds": 600, "photo_grace_seconds": 180}
    )
    return ManifestManager.load_manifest(path)

def test_stage3():
    print("=" * 70)
    print("STAGE 3 ACCEPTANCE TEST SUITE")
    print("=" * 70)

    event_dir = r"D:\Events\17.ElevateUai_cybersecurity"
    scribe_dir = os.path.join(event_dir, "scribe")
    test_dir = os.path.join(scribe_dir, "test")
    full_dir = os.path.join(scribe_dir, "transcripts")
    work_dir = os.path.join(scribe_dir, "work")
    log_dir = os.path.join(scribe_dir, "logs")

    os.makedirs(scribe_dir, exist_ok=True)
    os.makedirs(work_dir, exist_ok=True)

    manifest_data = setup_elevate_manifest(event_dir, scribe_dir)
    print(f"Loaded manifest for: {manifest_data['event']['name']}")

    # Extract all clips with speaker name
    all_clips = []
    for s in manifest_data["sessions"]:
        speaker = s["speaker"]
        for c in s["clips"]:
            c["speaker"] = speaker
            all_clips.append(c)

    print(f"Total clips available: {len(all_clips)}")
    assert len(all_clips) == 16, f"Expected 16 clips, got {len(all_clips)}"

    mgr = TranscriptionManager(python_executable=sys.executable)

    # ---------------------------------------------------------
    # 1. TEST GATE LOCK CHECK
    # ---------------------------------------------------------
    print("\n--- 1. Testing Test Gate Locking ---")
    status_file = TranscriptionManager.get_test_gate_status_file(scribe_dir)
    if os.path.exists(status_file):
        os.remove(status_file)

    assert not TranscriptionManager.is_test_approved(scribe_dir), "Test gate should initially be locked!"
    print("[PASS] Test gate initially locked (Full run cannot start without test approval).")

    # ---------------------------------------------------------
    # 2. TRANSCRIBE 2 TEST CLIPS INTO scribe\test
    # ---------------------------------------------------------
    print("\n--- 2. Transcribing 2 Elevate Test Clips into scribe\\test ---")
    if os.path.exists(test_dir):
        shutil.rmtree(test_dir)
    os.makedirs(test_dir)

    test_clips = all_clips[:2] # VID20260905114130.mp4 and VID20260905114405.mp4
    print(f"Test clip 1: {test_clips[0]['name']} ({test_clips[0]['speaker']})")
    print(f"Test clip 2: {test_clips[1]['name']} ({test_clips[1]['speaker']})")

    glossary = "cybersecurity, SOC, attacks, elevateU, Quality Thought, SIEM"
    cfg_test = mgr.prepare_job_config(
        event_dict=manifest_data["event"],
        clips_to_run=test_clips,
        output_dir=test_dir,
        work_dir=work_dir,
        log_dir=log_dir,
        language="en",
        initial_prompt=glossary,
        model="medium"
    )

    test_events = []
    def on_progress(ev):
        test_events.append(ev)
        if ev.get("type") == "clip_complete":
            print(f"  [DONE] {ev['clip']}: Audio {ev['audio_duration']}s | Transcribe {ev['transcribe_time']}s | Speed {ev['speed_factor']}x")

    summary_test = mgr.run_worker_blocking(cfg_test, progress_callback=on_progress)
    print(f"Test run completed: {summary_test.get('processed_count')} clips processed in {summary_test.get('total_run_time_str')}.")
    print(f"Overall speed factor: {summary_test.get('overall_speed_factor')}x real-time speed.")

    # Check that output files exist
    t1_path = os.path.join(test_dir, "VID20260905114130.txt")
    t2_path = os.path.join(test_dir, "VID20260905114405.txt")
    assert os.path.exists(t1_path), f"Test transcript 1 missing: {t1_path}"
    assert os.path.exists(t2_path), f"Test transcript 2 missing: {t2_path}"

    # Verify format of VID20260905114130.txt
    with open(t1_path, "r", encoding="utf-8") as f:
        content1 = f.read()

    assert "=== EVENT DETAILS ===" in content1
    assert "Event Name: CYBER & BEYOND 2026" in content1
    assert "=== TRANSCRIPT ===" in content1
    assert "Video File: VID20260905114130.mp4" in content1
    assert "Start Time: 2026-09-05 11:41:30" in content1
    assert "Speaker: Speaker 1" in content1
    assert "Transcription Duration:" in content1
    assert "Transcription Started:" in content1
    assert "Transcription Completed:" in content1
    assert "[Speaker 1] [11:41:30.000 - " in content1

    print("[PASS] Two Elevate test transcripts match exact layout, absolute timestamps, and speaker name.")

    # Verify no temp audio files remain
    work_files = [f for f in os.listdir(work_dir) if f.lower().endswith(".wav")]
    assert len(work_files) == 0, f"Temp WAV files left behind in work directory: {work_files}"
    print("[PASS] Zero temp WAV files left behind in work directory.")

    # ---------------------------------------------------------
    # 3. APPROVE TEST GATE
    # ---------------------------------------------------------
    print("\n--- 3. Approving Test Gate ---")
    TranscriptionManager.set_test_approved(scribe_dir, approved=True, notes="Verified format in test suite")
    assert TranscriptionManager.is_test_approved(scribe_dir) is True
    print("[PASS] Test Gate Approved successfully.")

    # ---------------------------------------------------------
    # 4. FULL ELEVATE RUN WITH RESUME VERIFICATION
    # ---------------------------------------------------------
    print("\n--- 4. Full Elevate Run (16 Clips) with Resume Verification ---")
    os.makedirs(full_dir, exist_ok=True)

    # Copy the 2 test transcripts into full_dir to simulate a resumed run
    shutil.copy(t1_path, os.path.join(full_dir, "VID20260905114130.txt"))
    shutil.copy(t2_path, os.path.join(full_dir, "VID20260905114405.txt"))
    print(f"Pre-populated 2 existing transcripts into {full_dir} to verify resume.")

    cfg_full = mgr.prepare_job_config(
        event_dict=manifest_data["event"],
        clips_to_run=all_clips,
        output_dir=full_dir,
        work_dir=work_dir,
        log_dir=log_dir,
        language="en",
        initial_prompt=glossary,
        model="medium"
    )

    full_events = []
    skipped_clips = []
    completed_clips = []

    def on_full_progress(ev):
        full_events.append(ev)
        if ev.get("type") == "clip_skipped":
            skipped_clips.append(ev["clip"])
            print(f"  [RESUME SKIP] {ev['clip']} already has completed transcript.")
        elif ev.get("type") == "clip_complete":
            completed_clips.append(ev["clip"])
            print(f"  [DONE] {ev['clip']} ({ev['speaker']}): Audio {ev['audio_duration']}s in {ev['transcribe_time']}s ({ev['speed_factor']}x)")

    summary_full = mgr.run_worker_blocking(cfg_full, progress_callback=on_full_progress)

    print(f"\nFull Run Summary:")
    print(f"  Processed: {summary_full['processed_count']}")
    print(f"  Skipped (Resumed): {summary_full['skipped_count']}")
    print(f"  Total Run Time: {summary_full['total_run_time_str']}")
    print(f"  Total Audio Transcribed: {summary_full['total_audio_duration_sec']}s")
    print(f"  Overall Speed: {summary_full['overall_speed_factor']}x real-time")

    assert summary_full["skipped_count"] == 2, f"Expected 2 skipped clips on resume, got {summary_full['skipped_count']}"
    assert summary_full["processed_count"] == 14, f"Expected 14 newly processed clips, got {summary_full['processed_count']}"
    assert len(skipped_clips) == 2
    print("[PASS] Resume verified: Finished clips were skipped, remaining clips processed without error.")

    # Verify all 16 transcript files exist in transcripts/
    all_out_transcripts = [f for f in os.listdir(full_dir) if f.lower().endswith(".txt") and f != "low_confidence_report.txt"]
    print(f"\nTotal completed transcripts in {full_dir}: {len(all_out_transcripts)} of 16")
    assert len(all_out_transcripts) == 16, f"Expected 16 transcripts, found {len(all_out_transcripts)}"

    for c in all_clips:
        stem, _ = os.path.splitext(c["name"])
        t_path = os.path.join(full_dir, f"{stem}.txt")
        assert os.path.exists(t_path), f"Missing transcript for clip: {c['name']}"
        assert os.path.getsize(t_path) > 100, f"Transcript file {t_path} is empty or incomplete"

    print("[PASS] Full Elevate run complete: 16 of 16 clips have valid completed transcripts.")

    # ---------------------------------------------------------
    # 5. NO TEMP WAVS & GPU VRAM IDLE VERIFICATION
    # ---------------------------------------------------------
    print("\n--- 5. Verifying Clean Disk & GPU Memory Release ---")
    work_files = [f for f in os.listdir(work_dir) if f.lower().endswith(".wav")]
    assert len(work_files) == 0, f"Temp WAV files left behind: {work_files}"
    print("[PASS] No temp WAV files left behind in work directory.")

    # Check GPU VRAM
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
        free_mb, total_mb = [m / (1024**2) for m in torch.cuda.mem_get_info()]
        print(f"GPU VRAM Free after worker exit: {free_mb:.1f} MB / {total_mb:.1f} MB")
        assert free_mb > 2000.0, f"VRAM leaked! Expected >2000 MB free, got {free_mb:.1f} MB"
        print("[PASS] GPU VRAM fully released back to idle (>2 GB free).")

    print("\n" + "=" * 70)
    print("ALL STAGE 3 ACCEPTANCE TESTS PASSED SUCCESSFULLY!")
    print("=" * 70)

if __name__ == "__main__":
    test_stage3()
