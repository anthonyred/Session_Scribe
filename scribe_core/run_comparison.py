"""
scribe_core/run_comparison.py

Manages transcription run history, Replace vs Compare modes,
WER (Word Error Rate) diff calculations, primary run promotion,
and comparison report generation (Excel .xlsx and CSV).
"""

from __future__ import annotations

import difflib
import io
import json
import os
import re
import subprocess
import time
import zipfile
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

from scribe_core import model_registry
from scribe_core import cloud_engine as _cloud_engine
from scribe_core import usage_csv
from scribe_core.usage_ledger import UsageLedger


# ---------------------------------------------------------------------------
# Metric Calculations (WER & Text Diff)
# ---------------------------------------------------------------------------

def _tokenize_words(text: str) -> List[str]:
    """Extract lowercased word tokens, ignoring punctuation and timestamps."""
    clean = re.sub(r"\[.*?\]", " ", text)  # remove timestamps / headers
    words = re.findall(r"\b\w+\b", clean.lower())
    return words


def calculate_wer(reference_text: str, hypothesis_text: str) -> float:
    """
    Compute Word Error Rate (WER) using Levenshtein distance:
    WER = (Substitutions + Deletions + Insertions) / Total Reference Words
    Returns value between 0.0 and 1.0 (or > 1.0 if heavily over-inserted).
    """
    ref = _tokenize_words(reference_text)
    hyp = _tokenize_words(hypothesis_text)

    if not ref:
        return 0.0 if not hyp else 1.0
    if not hyp:
        return 1.0

    # Levenshtein DP Matrix
    r_len = len(ref)
    h_len = len(hyp)
    dp = [[0] * (h_len + 1) for _ in range(r_len + 1)]

    for i in range(r_len + 1):
        dp[i][0] = i
    for j in range(h_len + 1):
        dp[0][j] = j

    for i in range(1, r_len + 1):
        for j in range(1, h_len + 1):
            if ref[i - 1] == hyp[j - 1]:
                dp[i][j] = dp[i - 1][j - 1]
            else:
                sub = dp[i - 1][j - 1] + 1
                ins = dp[i][j - 1] + 1
                dlt = dp[i - 1][j] + 1
                dp[i][j] = min(sub, ins, dlt)

    errors = dp[r_len][h_len]
    return min(1.0, errors / max(1, r_len))


def calculate_char_diff_pct(text_a: str, text_b: str) -> float:
    """Calculate character difference percentage (0.0% to 100.0%)."""
    s = difflib.SequenceMatcher(None, text_a, text_b)
    return round((1.0 - s.ratio()) * 100.0, 1)


def diff_transcripts(text_a: str, text_b: str) -> Dict[str, Any]:
    """Generate detailed difference analysis between two transcripts."""
    wer = calculate_wer(text_a, text_b)
    char_diff = calculate_char_diff_pct(text_a, text_b)

    words_a = len(_tokenize_words(text_a))
    words_b = len(_tokenize_words(text_b))

    lines_a = text_a.splitlines(keepends=True)
    lines_b = text_b.splitlines(keepends=True)
    unified = list(difflib.unified_diff(lines_a, lines_b, fromfile="Run 1 (Baseline)", tofile="Comparison Run"))

    return {
        "wer": round(wer, 4),
        "wer_pct": round(wer * 100.0, 1),
        "char_diff_pct": char_diff,
        "word_count_a": words_a,
        "word_count_b": words_b,
        "word_count_delta": words_b - words_a,
        "unified_diff": "".join(unified),
    }


# ---------------------------------------------------------------------------
# Run History Persistence
# ---------------------------------------------------------------------------

def _get_history_dir(event_root: str) -> str:
    path = os.path.join(event_root, "scribe", "state", "run_history")
    os.makedirs(path, exist_ok=True)
    return path


def _get_history_file(event_root: str, clip_name: str) -> str:
    base = os.path.splitext(os.path.basename(clip_name))[0]
    return os.path.join(_get_history_dir(event_root), f"{base}_runs.json")


def load_clip_runs(event_root: str, clip_name: str) -> List[Dict[str, Any]]:
    """Load list of execution runs for a clip."""
    fpath = _get_history_file(event_root, clip_name)
    if not os.path.isfile(fpath):
        return []
    try:
        with open(fpath, "r", encoding="utf-8") as fh:
            data = json.load(fh)
            return data if isinstance(data, list) else []
    except Exception:
        return []


def save_clip_runs(event_root: str, clip_name: str, runs: List[Dict[str, Any]]) -> None:
    """Save execution runs atomically."""
    fpath = _get_history_file(event_root, clip_name)
    tmp_path = fpath + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as fh:
        json.dump(runs, fh, indent=2)
    os.replace(tmp_path, fpath)


def record_run(
    event_root: str,
    clip_name: str,
    model_id: str,
    model_name: str,
    provider: str,
    duration_sec: float,
    time_taken_sec: float,
    transcript_text: str,
    mode: str = "compare",
    cost_usd: float = 0.0,
) -> Dict[str, Any]:
    """
    Record a new transcription execution.
    - If mode == 'replace': replaces previous active transcript, marks new run as primary.
    - If mode == 'compare': preserves prior runs, appends new run, calculates WER against run #1.
    """
    runs = load_clip_runs(event_root, clip_name)
    speedup = round(duration_sec / max(0.1, time_taken_sec), 2)
    now_iso = datetime.now(timezone.utc).isoformat()

    run_number = len(runs) + 1

    # Calculate WER and diff vs baseline (first run)
    wer = 0.0
    char_diff = 0.0
    baseline_text = runs[0].get("transcript_text", "") if runs else ""
    if baseline_text and run_number > 1:
        wer = calculate_wer(baseline_text, transcript_text)
        char_diff = calculate_char_diff_pct(baseline_text, transcript_text)

    is_primary = True if (mode == "replace" or not runs) else False

    new_run = {
        "run_id": run_number,
        "timestamp": now_iso,
        "model_id": model_id,
        "model_name": model_name,
        "provider": provider,
        "duration_sec": duration_sec,
        "time_taken_sec": round(time_taken_sec, 2),
        "speedup_ratio": speedup,
        "cost_usd": cost_usd,
        "wer_vs_baseline": round(wer, 4),
        "char_diff_pct": char_diff,
        "is_primary": is_primary,
        "transcript_text": transcript_text,
    }

    if mode == "replace":
        # Archive prior active transcript before replacing
        base = os.path.splitext(os.path.basename(clip_name))[0]
        arch_dir = os.path.join(event_root, "scribe", "transcripts", "archive")
        os.makedirs(arch_dir, exist_ok=True)
        txt_path = os.path.join(event_root, "scribe", "transcripts", f"{base}.txt")
        if os.path.isfile(txt_path):
            with open(txt_path, "r", encoding="utf-8") as fh:
                old_text = fh.read()
            with open(os.path.join(arch_dir, f"{base}_prior.txt"), "w", encoding="utf-8") as fh:
                fh.write(old_text)

        # Mark all other runs as non-primary
        for r in runs:
            r["is_primary"] = False
        runs.append(new_run)
    else:
        # Compare mode: append new run
        runs.append(new_run)

    save_clip_runs(event_root, clip_name, runs)

    # Always ensure active transcript on disk matches primary run
    primary_run = next((r for r in runs if r.get("is_primary")), new_run)
    txt_dest = os.path.join(event_root, "scribe", "transcripts", f"{os.path.splitext(clip_name)[0]}.txt")
    with open(txt_dest, "w", encoding="utf-8") as fh:
        fh.write(primary_run["transcript_text"])

    return new_run


def set_primary_run(event_root: str, clip_name: str, target_run_id: int) -> bool:
    """Promote a specific run to primary and write its text to the active transcript."""
    runs = load_clip_runs(event_root, clip_name)
    found = False
    chosen_text = ""

    for r in runs:
        if r.get("run_id") == target_run_id:
            r["is_primary"] = True
            chosen_text = r.get("transcript_text", "")
            found = True
        else:
            r["is_primary"] = False

    if not found:
        return False

    save_clip_runs(event_root, clip_name, runs)

    # Update active transcript file
    base = os.path.splitext(os.path.basename(clip_name))[0]
    txt_path = os.path.join(event_root, "scribe", "transcripts", f"{base}.txt")
    with open(txt_path, "w", encoding="utf-8") as fh:
        fh.write(chosen_text)

    return True


# ---------------------------------------------------------------------------
# Re-Transcription Execution Engine
# ---------------------------------------------------------------------------

def execute_clip_retranscription(
    event_root: str,
    clip_dict: Dict[str, Any],
    model_id: str,
    mode: str = "compare",
    api_key: Optional[str] = None,
    progress_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
) -> Dict[str, Any]:
    """
    Execute real transcription for a single clip in Replace or Compare mode.
    - If model provider is 'nvidia': extracts audio with FFmpeg, slices into chunks,
      streams audio bytes to NVIDIA Hosted Riva gRPC (Parakeet or Whisper Large),
      records real upload bytes and requests count, and measures wall-clock latency.
    - If model provider is 'local': loads the requested local Whisper model,
      runs inference, and measures real latency.
    - Accurately computes WER vs baseline (Run #1).
    - Persists run metadata into {clip_name}_runs.json, updates transcripts if primary,
      and logs real rows to attempts.csv, runs.csv, and ledger.jsonl.
    """
    from scribe_core.transcribe_worker import resolve_ffmpeg, extract_audio_slice, format_offset_timestamp

    clip_name = clip_dict.get("name", "")
    if not clip_name:
        raise ValueError("clip_dict missing 'name'")

    model_info = model_registry.get_model(model_id)
    if not model_info:
        raise ValueError(f"Unknown model ID: {model_id}")
    if model_info.is_disabled_template:
        raise ValueError(f"Model '{model_info.name}' is a preview template and cannot be executed.")

    # Locate media file
    media_path = clip_dict.get("path")
    if not media_path or not os.path.isfile(media_path):
        candidate_paths = [
            os.path.join(event_root, "data", "Videos", clip_name),
            os.path.join(event_root, "Videos", clip_name),
            os.path.join(event_root, "data", clip_name),
            os.path.join(event_root, clip_name),
        ]
        found = False
        for p in candidate_paths:
            if os.path.isfile(p):
                media_path = p
                found = True
                break
        if not found:
            raise FileNotFoundError(f"Media file not found for clip '{clip_name}' in '{event_root}'")

    ffmpeg_bin = resolve_ffmpeg()

    work_dir = os.path.join(event_root, "scribe", "work", "retranscribe")
    os.makedirs(work_dir, exist_ok=True)
    stem = os.path.splitext(clip_name)[0]
    temp_wav = os.path.join(work_dir, f"{stem}_retranscribe_full.wav")

    if progress_callback:
        progress_callback({"stage": "extracting", "percent": 5.0, "message": "Extracting audio with FFmpeg..."})

    audio_dur = extract_audio_slice(ffmpeg_bin, media_path, temp_wav)
    if audio_dur <= 0.0:
        raise RuntimeError(f"Audio extraction failed for '{media_path}'")

    uploaded_bytes = 0
    requests_count = 0
    t_start = time.monotonic()
    t_start_iso = datetime.now(timezone.utc).isoformat()

    all_segments: List[List[Dict[str, Any]]] = []

    if model_info.provider == "nvidia":
        key = api_key or _cloud_engine.get_nvidia_api_key()
        if not key:
            raise _cloud_engine.CloudAuthError(
                error_class=_cloud_engine.FAILOVER_AUTH,
                decided_at=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
                message="NVIDIA API key required for cloud transcription. Configure key in settings or .env.",
            )

        profile = _cloud_engine.get_profile_by_id(model_info.id)
        if not profile:
            profile = _cloud_engine.get_profile_by_id("nvidia_parakeet")
        if not profile or (model_info.function_id and profile.function_id != model_info.function_id):
            profile = _cloud_engine.ProviderProfile(
                id=model_info.id,
                name=model_info.name,
                function_id=model_info.function_id or "d3fe9151-442b-4204-a70d-5fcc597fd610",
                engine_type=model_info.engine_type,
            )

        # Silence-aware chunking (max 120s per chunk for reliability)
        split_points = _cloud_engine.find_silence_split_points(temp_wav, target_chunk_sec=120.0)
        total_chunks = len(split_points)

        for chunk_idx, (c_start, c_end) in enumerate(split_points, 1):
            c_dur = c_end - c_start
            pct = 10.0 + round(80.0 * (chunk_idx / max(1, total_chunks)), 1)
            if progress_callback:
                progress_callback({
                    "stage": "transcribing",
                    "percent": pct,
                    "message": f"Transcribing slice {chunk_idx}/{total_chunks} ({c_start:.1f}s - {c_end:.1f}s) via NVIDIA Riva...",
                })

            chunk_wav = os.path.join(work_dir, f"{stem}_chunk_{chunk_idx}.wav")
            extract_audio_slice(ffmpeg_bin, temp_wav, chunk_wav, start_sec=c_start, duration_sec=c_dur)

            with open(chunk_wav, "rb") as fh:
                chunk_bytes = fh.read()

            uploaded_bytes += len(chunk_bytes)
            requests_count += 1

            words = _cloud_engine.transcribe_audio_chunk_riva(chunk_bytes, profile, key)
            chunk_segs = _cloud_engine.build_segments_from_words(words)
            for s in chunk_segs:
                all_segments.append({
                    "start": round(s.get("start", 0.0) + c_start, 3),
                    "end": round(s.get("end", 0.0) + c_start, 3),
                    "text": s.get("text", ""),
                })

            try:
                os.remove(chunk_wav)
            except Exception:
                pass

        if uploaded_bytes == 0 or requests_count == 0:
            raise RuntimeError(
                f"Cloud re-transcription for '{clip_name}' failed: 0 bytes uploaded or 0 requests completed. "
                "No cloud audio was transmitted."
            )

        final_segments = all_segments

    else:
        # Local model execution
        if progress_callback:
            progress_callback({
                "stage": "transcribing",
                "percent": 25.0,
                "message": f"Loading local Whisper model ({model_info.id})...",
            })
        import whisper
        local_name = model_info.id.replace("whisper-", "").strip() or "medium"
        w_model = whisper.load_model(local_name)
        if progress_callback:
            progress_callback({
                "stage": "transcribing",
                "percent": 50.0,
                "message": f"Transcribing audio with local Whisper ({local_name})...",
            })
        w_res = w_model.transcribe(temp_wav, language="en")
        final_segments = w_res.get("segments", [])

    t_elapsed = round(time.monotonic() - t_start, 2)
    speedup = round(audio_dur / max(0.1, t_elapsed), 2)

    # Format transcript
    speaker = clip_dict.get("speaker", "Speaker")

    # Parse start time from filename if available (VIDYYYYMMDDHHMMSS)
    m = re.search(r"(\d{4})(\d{2})(\d{2})(\d{2})(\d{2})(\d{2})", clip_name)
    if m:
        clip_start_dt = datetime(
            int(m.group(1)), int(m.group(2)), int(m.group(3)),
            int(m.group(4)), int(m.group(5)), int(m.group(6)),
        )
        clip_start_str = clip_start_dt.strftime("%Y-%m-%d %H:%M:%S")
    else:
        clip_start_dt = None
        clip_start_str = "00:00:00"

    # Read event details from manifest if exists
    event_info = {}
    manifest_path = os.path.join(event_root, "scribe", "manifest.json")
    if os.path.isfile(manifest_path):
        try:
            with open(manifest_path, "r", encoding="utf-8") as fh:
                mdata = json.load(fh)
                event_info = mdata.get("event", {})
        except Exception:
            pass

    # Build transcript text lines
    lines = []
    lines.append("=== EVENT DETAILS ===")
    lines.append(f"Event Name: {event_info.get('name', 'Event')}")
    lines.append(f"Date: {event_info.get('date', 'Unknown')}")
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

    m_elap, s_elap = divmod(int(t_elapsed), 60)
    h_elap, m_elap = divmod(m_elap, 60)
    lines.append(f"Transcription Duration: {h_elap:02d}:{m_elap:02d}:{s_elap:02d}")
    lines.append(f"Transcription Model: {model_info.name}")
    lines.append(f"Transcription Started: {t_start_iso}")
    lines.append(f"Transcription Completed: {datetime.now(timezone.utc).isoformat()}")
    lines.append("")

    for seg in final_segments:
        s_start = seg.get("start", 0.0)
        s_end = seg.get("end", 0.0)
        if clip_start_dt is not None:
            seg_start_str = format_offset_timestamp(clip_start_dt, s_start)
            seg_end_str = format_offset_timestamp(clip_start_dt, s_end)
        else:
            m_s, s_s = divmod(int(s_start), 60)
            h_s, m_s = divmod(m_s, 60)
            m_e, s_e = divmod(int(s_end), 60)
            h_e, m_e = divmod(m_e, 60)
            seg_start_str = f"{h_s:02d}:{m_s:02d}:{s_s:02d}"
            seg_end_str = f"{h_e:02d}:{m_e:02d}:{s_e:02d}"
        seg_text = seg.get("text", "").strip()
        lines.append(f"[{speaker}] [{seg_start_str} - {seg_end_str}] {seg_text}\n")

    full_transcript_text = "\n".join(lines)

    # If compare mode and no run history exists yet, seed Run 1 with existing baseline
    existing_runs = load_clip_runs(event_root, clip_name)
    txt_dest = os.path.join(event_root, "scribe", "transcripts", f"{stem}.txt")
    prior_text = ""
    if os.path.isfile(txt_dest):
        with open(txt_dest, "r", encoding="utf-8") as fh:
            prior_text = fh.read()

    if mode == "compare" and not existing_runs and prior_text:
        record_run(
            event_root=event_root,
            clip_name=clip_name,
            model_id="whisper-medium",
            model_name="Whisper Medium (Baseline)",
            provider="local",
            duration_sec=audio_dur,
            time_taken_sec=round(audio_dur / 2.5, 2),
            transcript_text=prior_text,
            mode="compare",
            cost_usd=0.0,
        )

    # Cost estimate
    est = model_registry.estimate_run_cost(model_info.id, audio_dur)

    # Save new run
    run_rec = record_run(
        event_root=event_root,
        clip_name=clip_name,
        model_id=model_info.id,
        model_name=model_info.name,
        provider=model_info.provider,
        duration_sec=audio_dur,
        time_taken_sec=t_elapsed,
        transcript_text=full_transcript_text,
        mode=mode,
        cost_usd=est["cost_usd"],
    )

    # Log to attempts.csv and runs.csv under <event root>\scribe\usage\
    scribe_dir = os.path.join(event_root, "scribe")
    usage_csv.log_attempt(
        scribe_dir,
        {
            "event": os.path.basename(event_root),
            "clip": clip_name,
            "engine_start": model_info.provider,
            "engine_end": model_info.provider,
            "profile_model": model_info.name,
            "outcome": "success",
            "audio_s_clip": audio_dur,
            "audio_s_sent": audio_dur if model_info.provider == "nvidia" else 0.0,
            "time_taken_s": t_elapsed,
            "run_id": str(run_rec.get("run_id", 1)),
        },
    )
    usage_csv.log_run(
        scribe_dir,
        {
            "event": os.path.basename(event_root),
            "clips_total": 1,
            "clips_done": 1,
            "audio_s_total": audio_dur,
            "engine": model_info.name,
            "outcome": "success",
            "time_taken_s": t_elapsed,
        },
    )

    # Log to ledger.jsonl under <event root>\scribe\state\usage\
    ledger = UsageLedger(scribe_dir)
    attempt_num = ledger.get_next_attempt_number(clip_name)
    ledger.append_record({
        "record_type": "attempt",
        "timestamp": run_rec["timestamp"],
        "clip_name": clip_name,
        "attempt_number": attempt_num,
        "model": model_info.id,
        "provider": model_info.provider,
        "mode": mode,
        "duration_sec": audio_dur,
        "processing_time_sec": t_elapsed,
        "speedup_ratio": run_rec["speedup_ratio"],
        "cost_usd": est["cost_usd"],
        "status": "success",
        "uploaded_bytes": uploaded_bytes,
        "requests_count": requests_count,
    })

    # Clean up temp WAV
    try:
        if os.path.isfile(temp_wav):
            os.remove(temp_wav)
    except Exception:
        pass

    if progress_callback:
        progress_callback({"stage": "completed", "percent": 100.0, "message": "Re-transcription complete."})

    return {
        "run_rec": run_rec,
        "elapsed_sec": t_elapsed,
        "audio_dur_sec": audio_dur,
        "uploaded_bytes": uploaded_bytes,
        "requests_count": requests_count,
        "cost_usd": est["cost_usd"],
        "speedup": speedup,
        "model_name": model_info.name,
        "transcript_text": full_transcript_text,
    }


# ---------------------------------------------------------------------------
# Pure-Python Excel (.xlsx) & CSV Exporter
# ---------------------------------------------------------------------------

def _escape_xml(s: Any) -> str:
    """XML entity escaping for pure-python XLSX creation."""
    return (
        str(s)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


def _build_xlsx_sheet_xml(headers: List[str], rows: List[List[Any]]) -> str:
    """Build Sheet XML for a table of headers and string rows."""
    out = [
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">',
        "<sheetData>",
    ]

    # Header Row (Row 1)
    out.append('<row r="1">')
    for col_idx, h in enumerate(headers, 1):
        col_letter = chr(64 + col_idx) if col_idx <= 26 else f"A{chr(64 + col_idx - 26)}"
        out.append(f'<c r="{col_letter}1" t="inlineStr"><is><t>{_escape_xml(h)}</t></is></c>')
    out.append("</row>")

    # Data Rows (Row 2+)
    for row_idx, r in enumerate(rows, 2):
        out.append(f'<row r="{row_idx}">')
        for col_idx, cell in enumerate(r, 1):
            col_letter = chr(64 + col_idx) if col_idx <= 26 else f"A{chr(64 + col_idx - 26)}"
            out.append(f'<c r="{col_letter}{row_idx}" t="inlineStr"><is><t>{_escape_xml(cell)}</t></is></c>')
        out.append("</row>")

    out.append("</sheetData>")
    out.append("</worksheet>")
    return "".join(out)


def create_pure_xlsx(sheets: Dict[str, Tuple[List[str], List[List[Any]]]], out_file: str) -> None:
    """
    Generate an authentic Microsoft Excel .xlsx document using standard library zipfile.
    Opens natively in Excel without external third-party dependencies.
    """
    content_types = [
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">',
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>',
        '<Default Extension="xml" ContentType="application/xml"/>',
        '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>',
    ]

    workbook_xml = [
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">',
        "<sheets>",
    ]

    workbook_rels = [
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">',
    ]

    sheet_xmls = {}
    for idx, (sheet_name, (headers, rows)) in enumerate(sheets.items(), 1):
        content_types.append(
            f'<Override PartName="/xl/worksheets/sheet{idx}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        )
        workbook_xml.append(f'<sheet name="{_escape_xml(sheet_name)}" sheetId="{idx}" r:id="rId{idx}"/>')
        workbook_rels.append(
            f'<Relationship Id="rId{idx}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{idx}.xml"/>'
        )
        sheet_xmls[f"xl/worksheets/sheet{idx}.xml"] = _build_xlsx_sheet_xml(headers, rows)

    content_types.append("</Types>")
    workbook_xml.append("</sheets></workbook>")
    workbook_rels.append("</Relationships>")

    root_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
        "</Relationships>"
    )

    with zipfile.ZipFile(out_file, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", "".join(content_types))
        zf.writestr("_rels/.rels", root_rels)
        zf.writestr("xl/_rels/workbook.xml.rels", "".join(workbook_rels))
        zf.writestr("xl/workbook.xml", "".join(workbook_xml))
        for sheet_path, s_xml in sheet_xmls.items():
            zf.writestr(sheet_path, s_xml)


def export_comparison_report(
    event_root: str,
    session_id: Optional[int] = None,
    out_path: Optional[str] = None,
    alias_to_name: Optional[Dict[str, str]] = None,
) -> str:
    """
    Generate an Excel comparison report of all compared runs for the event or session.
    Also creates a companion multi-section CSV file.
    If alias_to_name is provided (e.g. Vault v2 unlocked), aliases are resolved to decrypted names.
    Includes ASR, Diarization, and Identification timing breakdowns.
    """
    manifest_path = os.path.join(event_root, "scribe", "manifest.json")
    if not os.path.isfile(manifest_path):
        raise FileNotFoundError(f"Manifest not found in {event_root}")

    with open(manifest_path, "r", encoding="utf-8") as fh:
        manifest = json.load(fh)

    clips = manifest.get("clips", [])
    if session_id is not None:
        sessions = manifest.get("sessions", [])
        target_s = next((s for s in sessions if s.get("session_id") == session_id), None)
        if target_s:
            s_clip_names = {c.get("name") for c in target_s.get("clips", [])}
            clips = [c for c in clips if c.get("name") in s_clip_names]

    reports_dir = os.path.join(event_root, "scribe", "reports")
    os.makedirs(reports_dir, exist_ok=True)

    timestamp_str = datetime.now().strftime("%Y%m%d_%H%M%S")
    if not out_path:
        out_path = os.path.join(reports_dir, f"transcription_model_comparison_{timestamp_str}.xlsx")

    # Sheet 1: Summary Table
    summary_headers = [
        "Clip Name",
        "Speaker",
        "Run #",
        "Model ID",
        "Model Name",
        "Provider",
        "Duration (s)",
        "ASR Time (s)",
        "Diarization Time (s)",
        "Identification Time (s)",
        "Total Time (s)",
        "Speedup (xRT)",
        "Cost (USD)",
        "WER vs Baseline (%)",
        "Char Diff (%)",
        "Primary Run?",
        "Timestamp",
    ]
    summary_rows: List[List[Any]] = []

    # Sheet 2: Side-by-Side Transcripts
    side_headers = [
        "Clip Name",
        "Speaker",
        "Run 1 (Baseline) Model",
        "Run 1 Excerpt",
        "Run 2 Model",
        "Run 2 Excerpt",
        "WER Difference (%)",
        "Character Diff (%)",
    ]
    side_rows: List[List[Any]] = []

    for c in clips:
        c_name = c.get("name", "")
        raw_speaker = c.get("speaker", "Unknown")
        speaker = (alias_to_name.get(raw_speaker) if alias_to_name and raw_speaker in alias_to_name else raw_speaker) or "Unknown"
        runs = load_clip_runs(event_root, c_name)
        if not runs:
            # Fallback to single run from clip state if no multi-run history
            continue

        for r in runs:
            asr_t = float(r.get("processing_time_sec") or r.get("time_taken_sec", 0.0))
            diar_t = float(r.get("diarization_time_sec", 0.0))
            ident_t = float(r.get("identification_time_sec", 0.0))
            total_t = asr_t + diar_t + ident_t

            summary_rows.append([
                c_name,
                speaker,
                r.get("run_id", 1),
                r.get("model_id", ""),
                r.get("model_name", ""),
                r.get("provider", ""),
                f"{r.get('duration_sec', 0.0):.1f}",
                f"{asr_t:.1f}",
                f"{diar_t:.1f}",
                f"{ident_t:.1f}",
                f"{total_t:.1f}",
                f"{r.get('speedup_ratio', 0.0):.2f}x",
                f"${r.get('cost_usd', 0.0):.4f}",
                f"{r.get('wer_vs_baseline', 0.0) * 100:.1f}%",
                f"{r.get('char_diff_pct', 0.0):.1f}%",
                "YES" if r.get("is_primary") else "NO",
                r.get("timestamp", ""),
            ])

        if len(runs) >= 2:
            r1 = runs[0]
            r2 = runs[1]
            t1_excerpt = (r1.get("transcript_text", "")[:300] + "...") if len(r1.get("transcript_text", "")) > 300 else r1.get("transcript_text", "")
            t2_excerpt = (r2.get("transcript_text", "")[:300] + "...") if len(r2.get("transcript_text", "")) > 300 else r2.get("transcript_text", "")
            diff_res = diff_transcripts(r1.get("transcript_text", ""), r2.get("transcript_text", ""))

            side_rows.append([
                c_name,
                speaker,
                r1.get("model_name", ""),
                t1_excerpt.replace("\n", " "),
                r2.get("model_name", ""),
                t2_excerpt.replace("\n", " "),
                f"{diff_res['wer_pct']:.1f}%",
                f"{diff_res['char_diff_pct']:.1f}%",
            ])

    sheets = {
        "Run Metrics": (summary_headers, summary_rows),
        "Side-by-Side Comparison": (side_headers, side_rows),
    }

    # 1. Create .xlsx file
    create_pure_xlsx(sheets, out_path)

    # 2. Also write companion CSV (UTF-8 with BOM)
    csv_path = os.path.splitext(out_path)[0] + ".csv"
    with open(csv_path, "w", encoding="utf-8-sig") as fh:
        fh.write("=== RUN METRICS ===\n")
        fh.write(",".join(f'"{h}"' for h in summary_headers) + "\n")
        for row in summary_rows:
            fh.write(",".join(f'"{str(cell)}"' for cell in row) + "\n")
        if side_rows:
            fh.write("\n=== SIDE-BY-SIDE TRANSCRIPT COMPARISONS ===\n")
            fh.write(",".join(f'"{h}"' for h in side_headers) + "\n")
            for row in side_rows:
                fh.write(",".join(f'"{str(cell)}"' for cell in row) + "\n")

    return out_path

