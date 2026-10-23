"""
Transcription Worker for Session Scribe
Runs as an independent subprocess to transcribe audio using Whisper medium on GPU.
Isolates VRAM usage so memory is 100% released on exit or cancellation.
Communicates progress via JSON lines over stdout.
"""

import os
import sys
import json
import time
import argparse
import shutil
import subprocess
from datetime import datetime, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30))

def parse_args():
    parser = argparse.ArgumentParser(description="Session Scribe Transcription Worker")
    parser.add_argument("--job-config", required=True, help="Path to JSON file with job configuration")
    return parser.parse_args()

def resolve_ffmpeg():
    """Resolve ffmpeg binary dynamically from PATH."""
    bin_path = shutil.which("ffmpeg") or shutil.which("ffmpeg.exe")
    if bin_path:
        return bin_path
    candidates = [
        r"C:\ProgramData\chocolatey\bin\ffmpeg.exe",
        r"C:\Users\antho\scoop\shims\ffmpeg.exe"
    ]
    for c in candidates:
        if os.path.exists(c):
            return c
    raise RuntimeError("ffmpeg executable not found on PATH.")

def format_timestamp(dt: datetime) -> str:
    """Format datetime as YYYY-MM-DD HH:MM:SS."""
    return dt.strftime("%Y-%m-%d %H:%M:%S")

def format_offset_timestamp(clip_start: datetime, offset_seconds: float) -> str:
    """Calculates absolute segment timestamp formatted as HH:MM:SS.mmm."""
    seg_dt = clip_start + timedelta(seconds=offset_seconds)
    return seg_dt.strftime("%H:%M:%S") + f".{int(seg_dt.microsecond / 1000):03d}"

def format_duration(seconds: float) -> str:
    """Format duration in seconds to HH:MM:SS."""
    td = timedelta(seconds=int(round(seconds)))
    total_seconds = int(td.total_seconds())
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    secs = total_seconds % 60
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"

def extract_audio(ffmpeg_bin: str, video_path: str, wav_path: str):
    """Extract mono 16 kHz WAV audio from video using ffmpeg."""
    os.makedirs(os.path.dirname(wav_path), exist_ok=True)
    cmd = [
        ffmpeg_bin,
        "-y",
        "-i", video_path,
        "-vn",
        "-acodec", "pcm_s16le",
        "-ar", "16000",
        "-ac", "1",
        wav_path
    ]
    subprocess.run(cmd, check=True, capture_output=True)

def emit_json(data: dict):
    """Write JSON line to stdout and flush."""
    sys.stdout.write(json.dumps(data) + "\n")
    sys.stdout.flush()

def main():
    args = parse_args()
    if not os.path.exists(args.job_config):
        emit_json({"type": "error", "message": f"Job config file not found: {args.job_config}"})
        sys.exit(1)

    with open(args.job_config, "r", encoding="utf-8") as f:
        job = json.load(f)

    event_info = job.get("event", {})
    clips = job.get("clips", [])
    output_dir = job.get("output_dir")
    work_dir = job.get("work_dir")
    log_dir = job.get("log_dir")
    language = job.get("language", "en")
    initial_prompt = job.get("initial_prompt", "").strip() or None
    model_name = job.get("model", "medium")
    device = job.get("device", "cuda")

    os.makedirs(output_dir, exist_ok=True)
    os.makedirs(work_dir, exist_ok=True)
    if log_dir:
        os.makedirs(log_dir, exist_ok=True)

    ffmpeg_bin = resolve_ffmpeg()

    emit_json({
        "type": "init",
        "total_clips": len(clips),
        "output_dir": output_dir,
        "language": language,
        "initial_prompt": initial_prompt
    })

    # Log setup
    log_file_path = os.path.join(log_dir or output_dir, "transcription.log")
    low_conf_file_path = os.path.join(output_dir, "low_confidence_report.txt")

    def log(msg: str):
        timestamp = datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S IST")
        with open(log_file_path, "a", encoding="utf-8") as lf:
            lf.write(f"[{timestamp}] {msg}\n")

    log(f"--- Transcription Run Started. Total Clips: {len(clips)}, Model: {model_name}, Language: {language} ---")

    # Load Whisper Model ONCE
    t_model_start = time.time()
    emit_json({"type": "status", "message": f"Loading Whisper model '{model_name}' onto {device}..."})
    import torch
    import whisper

    try:
        model = whisper.load_model(model_name, device=device)
        model_load_time = time.time() - t_model_start
        emit_json({
            "type": "model_loaded",
            "model": model_name,
            "device": device,
            "load_time": round(model_load_time, 2)
        })
        log(f"Whisper model '{model_name}' loaded in {model_load_time:.2f}s on {device}.")
    except Exception as e:
        emit_json({"type": "error", "message": f"Failed to load Whisper model: {e}"})
        log(f"ERROR: Failed to load model: {e}")
        sys.exit(1)

    overall_start_time = datetime.now(IST)
    total_audio_seconds = 0.0
    total_transcribe_seconds = 0.0
    processed_count = 0
    skipped_count = 0
    failed_count = 0
    low_confidence_items = []

    try:
        for idx, clip in enumerate(clips, 1):
            clip_path = clip["path"]
            clip_name = clip["name"]
            speaker = clip.get("speaker", "Speaker")
            start_time_iso = clip.get("start_time")
            try:
                clip_start_dt = datetime.fromisoformat(start_time_iso)
            except Exception:
                clip_start_dt = datetime.now(IST)

            stem, _ = os.path.splitext(clip_name)
            transcript_txt_path = os.path.join(output_dir, f"{stem}.txt")

            # Resume Check: Skip if completed transcript already exists
            if os.path.exists(transcript_txt_path) and os.path.getsize(transcript_txt_path) > 100:
                skipped_count += 1
                emit_json({
                    "type": "clip_skipped",
                    "index": idx,
                    "total": len(clips),
                    "clip": clip_name,
                    "speaker": speaker,
                    "reason": "Transcript file already exists"
                })
                log(f"Clip {idx}/{len(clips)} '{clip_name}' skipped (already exists: {transcript_txt_path}).")
                continue

            emit_json({
                "type": "clip_start",
                "index": idx,
                "total": len(clips),
                "clip": clip_name,
                "speaker": speaker
            })
            log(f"Starting Clip {idx}/{len(clips)}: '{clip_name}' (Speaker: {speaker})")

            wav_path = os.path.join(work_dir, f"{stem}_temp.wav")
            clip_transcribe_start = datetime.now(IST)
            t0 = time.time()

            try:
                # 1. Extract audio with ffmpeg
                extract_audio(ffmpeg_bin, clip_path, wav_path)

                # 2. Transcribe with Whisper
                transcribe_kwargs = {
                    "fp16": (device == "cuda"),
                }
                if language:
                    transcribe_kwargs["language"] = language
                if initial_prompt:
                    transcribe_kwargs["initial_prompt"] = initial_prompt

                result = model.transcribe(wav_path, **transcribe_kwargs)
                t1 = time.time()
                clip_transcribe_end = datetime.now(IST)

                transcribe_dur_sec = t1 - t0
                total_transcribe_seconds += transcribe_dur_sec

                # Calculate audio duration
                segments = result.get("segments", [])
                audio_dur = segments[-1]["end"] if segments else 0.0
                total_audio_seconds += audio_dur

                speed_factor = (audio_dur / transcribe_dur_sec) if transcribe_dur_sec > 0 else 0.0

                # 3. Format Transcript File (matching user's exact format)
                transcription_duration_str = format_duration(transcribe_dur_sec)
                started_str = format_timestamp(clip_transcribe_start) + " IST"
                completed_str = format_timestamp(clip_transcribe_end) + " IST"
                clip_start_str = format_timestamp(clip_start_dt)

                lines = []
                lines.append("=== EVENT DETAILS ===")
                lines.append(f"Event Name: {event_info.get('name', '')}")
                lines.append(f"Date: {event_info.get('date', '')}")
                if event_info.get("organiser"):
                    lines.append(f"Organiser: {event_info.get('organiser')}")
                if event_info.get("venue"):
                    lines.append(f"Venue: {event_info.get('venue')}")
                if event_info.get("sponsors"):
                    lines.append(f"Sponsors: {event_info.get('sponsors')}")

                lines.append("")
                lines.append("=== TRANSCRIPT ===")
                lines.append(f"Video File: {clip_name}")
                lines.append(f"Start Time: {clip_start_str}")
                lines.append(f"Speaker: {speaker}")
                lines.append(f"Transcription Duration: {transcription_duration_str}")
                lines.append(f"Transcription Started: {started_str}")
                lines.append(f"Transcription Completed: {completed_str}")
                lines.append("")

                for seg in segments:
                    seg_start_str = format_offset_timestamp(clip_start_dt, seg["start"])
                    seg_end_str = format_offset_timestamp(clip_start_dt, seg["end"])
                    seg_text = seg["text"].strip()
                    lines.append(f"[{speaker}] [{seg_start_str} - {seg_end_str}] {seg_text}\n")

                    # Confidence signals check
                    avg_logprob = seg.get("avg_logprob", 0.0)
                    no_speech_prob = seg.get("no_speech_prob", 0.0)
                    compression_ratio = seg.get("compression_ratio", 1.0)

                    is_doubtful = (
                        avg_logprob < -1.0 or
                        no_speech_prob > 0.6 or
                        compression_ratio > 2.4
                    )
                    if is_doubtful:
                        reasons = []
                        if avg_logprob < -1.0:
                            reasons.append(f"low logprob ({avg_logprob:.2f})")
                        if no_speech_prob > 0.6:
                            reasons.append(f"high no-speech prob ({no_speech_prob:.2f})")
                        if compression_ratio > 2.4:
                            reasons.append(f"high repetition ratio ({compression_ratio:.2f})")

                        low_conf_entry = (
                            f"Clip: {clip_name} | Time: [{seg_start_str} - {seg_end_str}] | "
                            f"Speaker: {speaker} | Flags: {', '.join(reasons)}\n"
                            f"  Text: {seg_text}\n"
                        )
                        low_confidence_items.append(low_conf_entry)

                # Write transcript file atomically
                temp_out = transcript_txt_path + ".tmp"
                with open(temp_out, "w", encoding="utf-8") as out_f:
                    out_f.write("\n".join(lines))
                os.replace(temp_out, transcript_txt_path)

                processed_count += 1
                emit_json({
                    "type": "clip_complete",
                    "index": idx,
                    "total": len(clips),
                    "clip": clip_name,
                    "speaker": speaker,
                    "audio_duration": round(audio_dur, 2),
                    "transcribe_time": round(transcribe_dur_sec, 2),
                    "speed_factor": round(speed_factor, 1),
                    "segments_count": len(segments),
                    "output_file": transcript_txt_path
                })
                log(f"Completed Clip {idx}/{len(clips)}: '{clip_name}' in {transcribe_dur_sec:.2f}s (Audio: {audio_dur:.1f}s, Speed: {speed_factor:.1f}x)")

            except Exception as clip_err:
                failed_count += 1
                emit_json({
                    "type": "clip_error",
                    "index": idx,
                    "total": len(clips),
                    "clip": clip_name,
                    "error": str(clip_err)
                })
                log(f"ERROR on Clip {idx}/{len(clips)} '{clip_name}': {clip_err}")
            finally:
                # Always delete temp audio file immediately
                if os.path.exists(wav_path):
                    try:
                        os.remove(wav_path)
                    except Exception:
                        pass

    finally:
        # Release model from GPU
        del model
        import gc
        gc.collect()
        if device == "cuda":
            torch.cuda.empty_cache()

    overall_end_time = datetime.now(IST)
    total_run_seconds = (overall_end_time - overall_start_time).total_seconds()
    overall_speed = (total_audio_seconds / total_transcribe_seconds) if total_transcribe_seconds > 0 else 0.0

    # Write low confidence report if any
    if low_confidence_items:
        with open(low_conf_file_path, "w", encoding="utf-8") as lcf:
            lcf.write(f"=== LOW CONFIDENCE SEGMENTS REPORT ===\n")
            lcf.write(f"Generated: {format_timestamp(overall_end_time)} IST\n")
            lcf.write(f"Total Flagged Segments: {len(low_confidence_items)}\n\n")
            lcf.writelines(low_confidence_items)

    summary = {
        "type": "finished",
        "processed_count": processed_count,
        "skipped_count": skipped_count,
        "failed_count": failed_count,
        "total_clips": len(clips),
        "total_audio_duration_sec": round(total_audio_seconds, 2),
        "total_transcribe_time_sec": round(total_transcribe_seconds, 2),
        "overall_speed_factor": round(overall_speed, 1),
        "started_at": format_timestamp(overall_start_time) + " IST",
        "ended_at": format_timestamp(overall_end_time) + " IST",
        "total_run_time_str": format_duration(total_run_seconds),
        "low_confidence_flags_count": len(low_confidence_items),
        "low_confidence_report": low_conf_file_path if low_confidence_items else None
    }

    emit_json(summary)
    log(f"--- Run Finished: {processed_count} processed, {skipped_count} skipped, {failed_count} failed in {format_duration(total_run_seconds)} (Speed: {overall_speed:.1f}x real-time) ---")

if __name__ == "__main__":
    main()
