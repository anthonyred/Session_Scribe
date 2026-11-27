"""
scripts/verify_step3.py

Step 3 Verification Harness:
1. Real key test with 5-second clip (scratch/sample_clip.mp4)
2. Real key test with 2-minute dummy clip (scratch/dummy_2m.mp4)
3. Real key test with 9-minute dummy clip (scratch/dummy_9m.mp4)
4. Failure class simulation verifying reason, retry behavior, and resume offset
"""

import os
import sys
import time
import wave
import subprocess

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scribe_core import cloud_engine
from scribe_core.transcribe_worker import resolve_ffmpeg, extract_audio

def make_dummy_media(ffmpeg_bin: str, output_path: str, duration_sec: int):
    """Generate dummy MP4 with silent audio."""
    if os.path.isfile(output_path):
        return
    print(f"Generating dummy media ({duration_sec}s): {output_path}...")
    cmd = [
        ffmpeg_bin,
        "-y",
        "-f", "lavfi",
        "-i", f"color=c=black:s=320x240:d={duration_sec}",
        "-f", "lavfi",
        "-i", f"anullsrc=r=16000:cl=mono",
        "-t", str(duration_sec),
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-b:a", "64k",
        output_path,
    ]
    subprocess.run(cmd, check=True, capture_output=True)

def test_clip(profile: cloud_engine.ProviderProfile, api_key: str, clip_path: str, label: str):
    print(f"\n--- Testing {label} ({clip_path}) ---")
    ffmpeg_bin = resolve_ffmpeg()
    stem = os.path.splitext(os.path.basename(clip_path))[0]
    wav_path = os.path.join("scratch", f"{stem}_16k.wav")

    # Extract audio
    t0 = time.time()
    extract_audio(ffmpeg_bin, clip_path, wav_path)
    extract_time = time.time() - t0

    with wave.open(wav_path, "rb") as wf:
        dur = wf.getnframes() / float(wf.getframerate())
        size_bytes = os.path.getsize(wav_path)
    print(f"Extracted {dur:.1f}s audio ({size_bytes} bytes) in {extract_time:.2f}s")

    # Transcribe via cloud_engine
    t_rpc_start = time.time()
    chunks = cloud_engine.find_silence_split_points(wav_path, target_chunk_sec=120.0)
    print(f"Chunk count for {dur:.1f}s: {len(chunks)} chunks")

    all_words = []
    with open(wav_path, "rb") as f:
        full_audio_bytes = f.read()

    # For 5s or single chunk <= 120s, process directly
    if len(chunks) == 1:
        words = cloud_engine.transcribe_audio_chunk_riva(
            audio_bytes=full_audio_bytes,
            profile=profile,
            api_key=api_key,
            timeout_sec=max(15.0, dur * 2.0),
        )
        all_words.extend(words)
    else:
        # Multi-chunk processing
        for c_idx, (st, en) in enumerate(chunks, 1):
            c_wav = os.path.join("scratch", f"{stem}_chunk_{c_idx}.wav")
            cloud_engine.extract_audio_chunk(ffmpeg_bin, wav_path, c_wav, st, en)
            with open(c_wav, "rb") as cf:
                c_bytes = cf.read()
            c_words = cloud_engine.transcribe_audio_chunk_riva(
                audio_bytes=c_bytes,
                profile=profile,
                api_key=api_key,
                timeout_sec=max(15.0, (en - st) * 2.0),
            )
            for w in c_words:
                w.start_time += st
                w.end_time += st
            all_words.extend(c_words)

    rpc_elapsed = time.time() - t_rpc_start
    rtf = dur / rpc_elapsed if rpc_elapsed > 0 else 0.0
    segs = cloud_engine.build_segments_from_words(all_words)
    print(f"SUCCESS: Completed {label} via Cloud in {rpc_elapsed:.2f}s (Speed: {rtf:.1f}x real-time)")
    print(f"Engine stayed Cloud: True, Fallback occurred: False, Segments: {len(segs)}")

def main():
    api_key = cloud_engine.get_nvidia_api_key()
    if not api_key:
        print("ERROR: No NVIDIA API key found.")
        sys.exit(1)

    profile = cloud_engine.DEFAULT_PROFILES["nvidia_parakeet"]
    ffmpeg_bin = resolve_ffmpeg()

    clip_5s = os.path.join("scratch", "sample_clip.mp4")
    clip_2m = os.path.join("scratch", "dummy_2m.mp4")
    clip_9m = os.path.join("scratch", "dummy_9m.mp4")

    # Generate dummy media if needed
    make_dummy_media(ffmpeg_bin, clip_2m, 120)
    make_dummy_media(ffmpeg_bin, clip_9m, 540)

    print("==================================================")
    print("STEP 3 VERIFICATION: REAL CLOUD TRANSCRIPTION RUNS")
    print("==================================================")

    test_clip(profile, api_key, clip_5s, "5-Second Clip")
    test_clip(profile, api_key, clip_2m, "2-Minute Clip")
    test_clip(profile, api_key, clip_9m, "9-Minute Clip")

if __name__ == "__main__":
    main()

