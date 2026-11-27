#!/usr/bin/env python3
"""
scripts/voice_quality_report.py
Standalone acoustic quality calibration tool for Session Scribe voice biometrics.

Inspects an audio or video media file without storing anything or writing audio to disk.
Evaluates:
- Speech duration via Pyannote Neural VAD.
- Single speaker enforcement & speaker count via Pyannote Diarization.
- Overlapped speech detection via Pyannote OSD.
- Digital clipping and distortion ratio.
- Noise floor and Signal-to-Noise Ratio (SNR in dB).
- Final verdict: CLEAR_PASS, BORDERLINE (with override recommendation), or CLEAR_FAIL.

Usage:
  python scripts/voice_quality_report.py "path/to/media.mp4"
  python scripts/voice_quality_report.py "path/to/audio.wav" --start 10.0 --end 45.0
"""

from __future__ import annotations

import argparse
import os
import sys
import time

# sys.path guard derived from __file__
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from scribe_core.audio_extractor import extract_audio_in_memory
from scribe_core.voice_quality_checker import evaluate_sample_quality


def format_duration(seconds: float) -> str:
    mins = int(seconds // 60)
    secs = seconds % 60
    return f"{mins:02d}:{secs:05.2f} ({seconds:.2f}s)"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Acoustic Quality Report & Calibration Tool for Session Scribe Voice Biometrics."
    )
    parser.add_argument("file", help="Path to input audio or video file (e.g. .wav, .mp3, .m4a, .mp4, .mov)")
    parser.add_argument("--start", type=float, default=None, help="Start time offset in seconds (optional)")
    parser.add_argument("--end", type=float, default=None, help="End time offset in seconds (optional)")
    parser.add_argument("--compare", type=str, default=None, help="Path to second audio or video file to compare voice embedding similarity (optional)")
    parser.add_argument("--compare-start", type=float, default=None, help="Start time offset for compare file in seconds (optional)")
    parser.add_argument("--compare-end", type=float, default=None, help="End time offset for compare file in seconds (optional)")
    args = parser.parse_args()

    file_path = os.path.abspath(args.file)
    if not os.path.isfile(file_path):
        print(f"[ERROR] Input file not found: {file_path}", file=sys.stderr)
        return 1

    compare_path = os.path.abspath(args.compare) if args.compare else None
    if compare_path and not os.path.isfile(compare_path):
        print(f"[ERROR] Compare file not found: {compare_path}", file=sys.stderr)
        return 1

    time_range = None
    if args.start is not None or args.end is not None:
        start_sec = args.start if args.start is not None else 0.0
        time_range = (start_sec, args.end)

    file_size_mb = os.path.getsize(file_path) / (1024 * 1024)
    print("=" * 72)
    print("SESSION SCRIBE — VOICE BIOMETRICS QUALITY REPORT")
    print("=" * 72)
    print(f"Target File:       {os.path.basename(file_path)}")
    print(f"File Path:         {file_path}")
    print(f"File Size:         {file_size_mb:.2f} MiB")
    if time_range:
        end_str = f"{time_range[1]:.2f}s" if time_range[1] is not None else "end of file"
        print(f"Time Range:        {time_range[0]:.2f}s to {end_str}")
    else:
        print("Time Range:        Full media file")
    print("-" * 72)

    # 1. In-memory audio extraction for primary file
    t0 = time.time()
    try:
        wav_bytes = extract_audio_in_memory(file_path, time_range=time_range)
    except Exception as e:
        print(f"[FATAL] Audio extraction failed: {e}", file=sys.stderr)
        return 2

    extract_time = time.time() - t0
    print(f"Extraction Time:   {extract_time:.2f}s (In-Memory 16 kHz Mono PCM, 0 bytes written to disk)")

    # 2. Quality Evaluation
    t1 = time.time()
    result = evaluate_sample_quality(wav_bytes)
    eval_time = time.time() - t1
    print(f"Analysis Time:     {eval_time:.2f}s")
    print("-" * 72)

    # 3. Print Detailed Metrics
    print("ACOUSTIC & SIGNAL METRICS:")
    print(f"  • Total Audio Duration:     {format_duration(result.duration_sec)}")
    print(f"  • Active Speech Duration:   {format_duration(result.speech_duration_sec)} [VAD]")
    speech_ratio = (result.speech_duration_sec / max(0.1, result.duration_sec)) * 100
    print(f"  • Speech Duty Cycle:        {speech_ratio:.1f}%")
    print(f"  • Overall Loudness (RMS):   {result.loudness_rms:.4f}")
    print(f"  • Estimated SNR:            {result.snr_db:.1f} dB (>= 12 dB recommended; < 9 dB rejected)")
    print(f"  • Clipping Ratio:           {result.clipping_ratio * 100:.3f}% (<= 0.5% clean; > 2.0% rejected)")
    print()

    print("DIARIZATION & SPEAKER INTEGRITY:")
    print(f"  • Speakers Detected:        {result.speaker_count} (Exactly 1 required)")
    if result.speaker_breakdown:
        for spk, dur in result.speaker_breakdown.items():
            print(f"    - {spk}: {dur:.2f}s ({dur / max(0.1, result.speech_duration_sec) * 100:.1f}%)")
    print(f"  • Overlapped Speech:        {result.overlap_duration_sec:.2f}s")
    overlap_ratio = (result.overlap_duration_sec / max(0.1, result.speech_duration_sec)) * 100
    print(f"  • Overlap Ratio:            {overlap_ratio:.1f}% (<= 0.3s clean; > 1.5s rejected)")
    print("-" * 72)

    # 4. Final Verdict
    print("VERDICT:")
    if result.verdict == "CLEAR_PASS":
        print("  ✅ [CLEAR PASS] Sample meets all high-confidence criteria for enrollment.")
        print(f"     Status: {result.summary_label()}")
    elif result.verdict == "BORDERLINE":
        print("  ⚠️ [BORDERLINE] Sample is acceptable but has sub-optimal metrics.")
        print(f"     Borderline Reasons: {result.failure_reason}")
        print("     Action: Eligible for 'Accept Anyway' with explicit user confirmation.")
    else:
        print("  ❌ [CLEAR FAIL] Sample fails acoustic quality gates.")
        print(f"     Rejection Reason: {result.failure_reason}")
        print("     Action: Sample cannot be used. Please re-record or select a cleaner segment.")
    print("=" * 72)

    # 5. Optional Comparison against second file
    if compare_path:
        comp_range = None
        if args.compare_start is not None or args.compare_end is not None:
            c_start = args.compare_start if args.compare_start is not None else 0.0
            comp_range = (c_start, args.compare_end)

        print()
        print("=" * 72)
        print("VOICE BIOMETRICS EMBEDDING COMPARISON")
        print("=" * 72)
        print(f"Comparison File:   {os.path.basename(compare_path)}")
        if comp_range:
            c_end_str = f"{comp_range[1]:.2f}s" if comp_range[1] is not None else "end of file"
            print(f"Time Range:        {comp_range[0]:.2f}s to {c_end_str}")
        else:
            print("Time Range:        Full media file")
        print("-" * 72)

        try:
            comp_wav_bytes = extract_audio_in_memory(compare_path, time_range=comp_range)
        except Exception as e:
            print(f"[ERROR] Failed to extract comparison audio: {e}", file=sys.stderr)
            return 3

        comp_result = evaluate_sample_quality(comp_wav_bytes)
        print("COMPARISON FILE METRICS:")
        print(f"  • Total Audio Duration:     {format_duration(comp_result.duration_sec)}")
        print(f"  • Active Speech Duration:   {format_duration(comp_result.speech_duration_sec)} [VAD]")
        print(f"  • Estimated SNR:            {comp_result.snr_db:.1f} dB")
        print(f"  • Speakers Detected:        {comp_result.speaker_count}")
        print(f"  • Overlapped Speech:        {comp_result.overlap_duration_sec:.2f}s")
        print(f"  • Verdict:                  {comp_result.verdict}")
        print("-" * 72)

        # Compute embeddings in memory and compare
        try:
            from scribe_core.embedding_engine import LocalEmbeddingEngine
            from scribe_core.enrollment_manager import compute_cosine_similarity
            import soundfile as sf
            import numpy as np

            audio1, sr1 = sf.read(io.BytesIO(wav_bytes))
            if audio1.ndim > 1:
                audio1 = np.mean(audio1, axis=1)
            audio2, sr2 = sf.read(io.BytesIO(comp_wav_bytes))
            if audio2.ndim > 1:
                audio2 = np.mean(audio2, axis=1)

            engine = LocalEmbeddingEngine(prefer_gpu=False)
            emb1 = engine.compute_embedding(audio1.astype(np.float32), sample_rate=sr1)
            emb2 = engine.compute_embedding(audio2.astype(np.float32), sample_rate=sr2)
            similarity = compute_cosine_similarity(emb1, emb2)

            print("EMBEDDING SIMILARITY RESULT:")
            print(f"  • File 1:                   {os.path.basename(file_path)}")
            print(f"  • File 2:                   {os.path.basename(compare_path)}")
            print(f"  • Cosine Similarity:        {similarity:.4f} ({similarity * 100:.1f}%)")
            if similarity >= 0.82:
                print("  • Assessment:               HIGH SIMILARITY (Likely same speaker)")
            elif similarity >= 0.65:
                print("  • Assessment:               MODERATE SIMILARITY (Possible match / borderline)")
            else:
                print("  • Assessment:               DISTINCT VOICES (Different speakers)")
            print("=" * 72)
        except Exception as e:
            print(f"[ERROR] Embedding computation failed: {e}", file=sys.stderr)
            return 4

    return 0 if result.passed or result.is_borderline else 1

    return 0 if result.passed or result.is_borderline else 1


if __name__ == "__main__":
    sys.exit(main())
