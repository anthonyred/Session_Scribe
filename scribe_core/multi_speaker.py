"""
scribe_core/multi_speaker.py

Multi-speaker roster data model, voice sample extraction, diarization alignment,
and speaker attribution.
Constraints:
- Roles: Host, Speaker, Panelist, Moderator, Guest.
- Line format for multi-speaker sessions:
    [Name (Role)] [HH:MM:SS.mmm - HH:MM:SS.mmm] text
- One-speaker sessions keep:
    [Name] [HH:MM:SS.mmm - HH:MM:SS.mmm] text
- Header: 'Speakers: Name (Role), ...'
- Overlap handling:
    [Overlapping: Name A (Role), Name B (Role)] [HH:MM:SS.mmm - HH:MM:SS.mmm] text
- Low confidence:
    [Unidentified] [HH:MM:SS.mmm - HH:MM:SS.mmm] text
- Voice samples stored locally in <event root>\\scribe\\voice_samples\\, never uploaded.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

ALLOWED_ROLES = ["Speaker", "Host", "Panelist", "Moderator", "Guest"]
DEFAULT_MIN_ISLAND_DURATION_SEC: float = 0.7
BIOMETRIC_PRIVACY_NOTICE = (
    "Privacy Notice: Voice samples and biometric fingerprints are processed entirely locally "
    "and stored under <event root>\\scribe\\voice_samples\\. They are never transmitted, "
    "uploaded, or shared with any cloud provider or external service."
)


@dataclass
class RosterMember:
    name: str
    role: str = "Speaker"
    sample_path: Optional[str] = None
    embedding: Optional[List[float]] = None

    def __post_init__(self):
        if self.role not in ALLOWED_ROLES:
            self.role = "Speaker"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name.strip(),
            "role": self.role,
            "sample_path": self.sample_path,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> RosterMember:
        return cls(
            name=data.get("name", "").strip(),
            role=data.get("role", "Speaker"),
            sample_path=data.get("sample_path"),
        )


def sanitize_filename(name: str) -> str:
    """Sanitize participant name for safe filesystem usage."""
    s = re.sub(r'[\\/*?:"<>|]', "", name).strip()
    return re.sub(r"\s+", "_", s) or "speaker"


def format_hhmmss_mmm(seconds: float) -> str:
    """Format float seconds as HH:MM:SS.mmm."""
    if seconds < 0:
        seconds = 0.0
    total_ms = int(round(seconds * 1000))
    ms = total_ms % 1000
    total_sec = total_ms // 1000
    s = total_sec % 60
    total_min = total_sec // 60
    m = total_min % 60
    h = total_min // 60
    return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"


def extract_voice_sample(
    media_path: str,
    output_wav_path: str,
    start_sec: float = 0.0,
    duration_sec: float = 10.0,
) -> str:
    """
    Extract a mono 16kHz WAV voice sample from media_path using ffmpeg.
    Stored locally in <event root>\\scribe\\voice_samples\\.
    """
    os.makedirs(os.path.dirname(os.path.abspath(output_wav_path)), exist_ok=True)
    cmd = [
        "ffmpeg",
        "-y",
        "-ss",
        f"{start_sec:.2f}",
        "-i",
        media_path,
        "-t",
        f"{duration_sec:.2f}",
        "-vn",
        "-acodec",
        "pcm_s16le",
        "-ar",
        "16000",
        "-ac",
        "1",
        output_wav_path,
    ]
    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"ffmpeg voice sample extraction failed: {res.stderr}")
    return output_wav_path


def play_voice_sample(wav_path: str) -> None:
    """Play WAV voice sample using Windows winsound asynchronously."""
    if not os.path.isfile(wav_path):
        raise FileNotFoundError(f"Sample file not found: {wav_path}")
    if sys.platform == "win32":
        import winsound
        winsound.PlaySound(wav_path, winsound.SND_FILENAME | winsound.SND_ASYNC)


def stop_voice_sample() -> None:
    """Stop any currently playing audio sample."""
    if sys.platform == "win32":
        import winsound
        winsound.PlaySound(None, winsound.SND_PURGE)


def format_speakers_header(roster: List[Dict[str, Any]]) -> str:
    """
    Format the transcript header 'Speakers: Name (Role), ...'.
    """
    if not roster:
        return "Speakers: (None specified)"
    items = []
    for m in roster:
        name = m.get("name", "").strip() or "Unknown"
        role = m.get("role", "Speaker")
        items.append(f"{name} ({role})")
    return "Speakers: " + ", ".join(items)


def format_speaker_line(
    speaker_name: str,
    role: Optional[str],
    start_sec: float,
    end_sec: float,
    text: str,
    is_multi_speaker: bool = False,
    is_overlapping: bool = False,
    overlapping_speakers: Optional[List[Tuple[str, str]]] = None,
) -> str:
    """
    Format a single line of speech:
      - Multi-speaker normal: [Name (Role)] [HH:MM:SS.mmm - HH:MM:SS.mmm] text
      - Multi-speaker overlap: [Overlapping: Name A (Role), Name B (Role)] [HH:MM:SS.mmm - HH:MM:SS.mmm] text
      - Multi-speaker unidentified: [Unidentified] [HH:MM:SS.mmm - HH:MM:SS.mmm] text
      - Single-speaker: [Name] [HH:MM:SS.mmm - HH:MM:SS.mmm] text
    """
    ts_str = f"[{format_hhmmss_mmm(start_sec)} - {format_hhmmss_mmm(end_sec)}]"
    cleaned_text = text.strip()

    if not is_multi_speaker:
        label = speaker_name.strip() if speaker_name.strip() else "Speaker"
        return f"[{label}] {ts_str} {cleaned_text}"

    # Multi-speaker session
    if is_overlapping:
        if overlapping_speakers:
            spk_parts = [
                f"{n} ({r})" if r else n
                for n, r in overlapping_speakers
            ]
            return f"[Overlapping: {', '.join(spk_parts)}] {ts_str} {cleaned_text}"
        return f"[Overlapping] {ts_str} {cleaned_text}"

    if not speaker_name or speaker_name.strip() in ("[Unidentified]", "Unidentified"):
        return f"[Unidentified] {ts_str} {cleaned_text}"

    role_part = f" ({role})" if role and role.strip() else ""
    return f"[{speaker_name.strip()}{role_part}] {ts_str} {cleaned_text}"


def assign_diarization_to_words(
    words: List[Dict[str, Any]],
    turns: List[Dict[str, Any]],
    roster: List[Dict[str, Any]],
    similarity_map: Optional[Dict[str, str]] = None,
    min_island_sec: float = DEFAULT_MIN_ISLAND_DURATION_SEC,
) -> List[Dict[str, Any]]:
    """
    Align transcribed words [start, end, word] with pyannote speaker turns [start, end, speaker].
    Splits continuous stream into coherent speaker segments.
    """
    if not words:
        return []

    # Map raw turn labels (e.g. SPEAKER_00) to roster name/role
    label_to_member: Dict[str, Dict[str, str]] = {}
    if similarity_map:
        for turn_spk, roster_name in similarity_map.items():
            for m in roster:
                if m.get("name") == roster_name or m.get("alias") == roster_name:
                    label_to_member[turn_spk] = {
                        "name": m.get("alias") or m.get("name", roster_name),
                        "role": m.get("role", "Speaker"),
                    }
                    break

    # Removed order-of-appearance assignment (roster[idx]).
    # Unmapped turns must never be assigned to roster members based on chronological appearance order.
    # If a turn speaker is not verified via similarity_map, it remains an unidentified cluster.

    # Assign speaker turn to each word
    assigned_words: List[Dict[str, Any]] = []
    for w in words:
        w_mid = (float(w.get("start", 0.0)) + float(w.get("end", 0.0))) / 2.0
        active_turn = None
        overlap = False
        overlapping_turns = []

        for t in turns:
            if float(t.get("start", 0.0)) <= w_mid <= float(t.get("end", 0.0)):
                overlapping_turns.append(t)

        if len(overlapping_turns) > 1:
            overlap = True
            active_turn = overlapping_turns[0]
        elif len(overlapping_turns) == 1:
            active_turn = overlapping_turns[0]

        spk_name = "[Unidentified]"
        role = ""
        overlap_members = []

        if overlap:
            for ot in overlapping_turns:
                raw = ot.get("speaker", "")
                mem = label_to_member.get(raw)
                if mem:
                    overlap_members.append((mem["name"], mem["role"]))
                else:
                    overlap_members.append((raw, ""))

        if active_turn:
            raw_spk = active_turn.get("speaker", "")
            mem = label_to_member.get(raw_spk)
            if mem:
                spk_name = mem["name"]
                role = mem["role"]

        assigned_words.append({
            "start": float(w.get("start", 0.0)),
            "end": float(w.get("end", 0.0)),
            "word": w.get("word", "").strip(),
            "speaker": spk_name,
            "role": role,
            "is_overlapping": overlap,
            "overlapping_speakers": overlap_members if overlap else None,
        })

    # Group adjacent words by speaker and overlap state
    segments: List[Dict[str, Any]] = []
    current_seg: Optional[Dict[str, Any]] = None

    for item in assigned_words:
        key = (item["speaker"], item["role"], item["is_overlapping"])
        if current_seg is None or (current_seg["speaker"], current_seg["role"], current_seg["is_overlapping"]) != key:
            if current_seg:
                segments.append(current_seg)
            current_seg = {
                "start": item["start"],
                "end": item["end"],
                "speaker": item["speaker"],
                "role": item["role"],
                "is_overlapping": item["is_overlapping"],
                "overlapping_speakers": item["overlapping_speakers"],
                "words": [item["word"]],
            }
        else:
            current_seg["end"] = item["end"]
            current_seg["words"].append(item["word"])

    if current_seg:
        segments.append(current_seg)

    # Format text for each segment
    for seg in segments:
        seg["text"] = " ".join(seg["words"])

    # Turn smoothing: merge short speaker islands (< min_island_sec) between identical neighbours
    if min_island_sec > 0.0:
        segments = smooth_speaker_turns(segments, min_island_sec=min_island_sec)

    return segments


def smooth_speaker_turns(
    segments: List[Dict[str, Any]],
    min_island_sec: float = DEFAULT_MIN_ISLAND_DURATION_SEC,
) -> List[Dict[str, Any]]:
    """
    Merge speaker islands shorter than min_island_sec (default 0.7s) into
    the surrounding turn when both neighbours are the same speaker.
    Never smooths across or into an [Overlapping] line.

    Parameters
    ----------
    segments : List[Dict[str, Any]]
        List of turn segment dicts with keys 'start', 'end', 'speaker', 'role',
        'is_overlapping', 'overlapping_speakers', 'words', 'text'.
    min_island_sec : float
        Minimum duration in seconds for a solitary speaker island between identical
        neighbours. Segments with duration strictly less than this threshold are merged.
        If <= 0.0, smoothing is disabled and segments are returned as-is.

    Returns
    -------
    List[Dict[str, Any]]
        Smoothed and collapsed list of speaker segments.
    """
    if not segments or len(segments) < 3 or min_island_sec <= 0.0:
        return segments

    def _is_overlap(seg: Dict[str, Any]) -> bool:
        return bool(
            seg.get("is_overlapping")
            or seg.get("speaker") == "Overlapping"
            or seg.get("overlapping_speakers")
        )

    current_segs = [dict(s) for s in segments]
    changed = True
    iteration = 0
    max_iterations = 20

    while changed and iteration < max_iterations:
        changed = False
        iteration += 1
        n = len(current_segs)
        if n < 3:
            break

        island_idx = None
        for i in range(1, n - 1):
            prev_seg = current_segs[i - 1]
            curr_seg = current_segs[i]
            next_seg = current_segs[i + 1]

            # Never smooth across or into overlapping lines
            if _is_overlap(curr_seg) or _is_overlap(prev_seg) or _is_overlap(next_seg):
                continue

            # Must be surrounded by identical speaker and role on both sides
            if prev_seg.get("speaker") != next_seg.get("speaker"):
                continue
            if prev_seg.get("role") != next_seg.get("role"):
                continue

            # Current segment must be a different speaker (an actual island)
            if curr_seg.get("speaker") == prev_seg.get("speaker"):
                continue

            dur = float(curr_seg.get("end", 0.0)) - float(curr_seg.get("start", 0.0))
            if dur < min_island_sec:
                island_idx = i
                break

        if island_idx is not None:
            curr = current_segs[island_idx]
            surrounding_spk = current_segs[island_idx - 1]["speaker"]
            surrounding_role = current_segs[island_idx - 1].get("role", "")
            curr["speaker"] = surrounding_spk
            curr["role"] = surrounding_role
            curr["is_overlapping"] = False
            curr["overlapping_speakers"] = None

            collapsed: List[Dict[str, Any]] = []
            for seg in current_segs:
                if not collapsed:
                    c = dict(seg)
                    c["words"] = list(seg.get("words", []))
                    collapsed.append(c)
                else:
                    last = collapsed[-1]
                    same_key = (
                        last.get("speaker") == seg.get("speaker")
                        and last.get("role") == seg.get("role")
                        and not _is_overlap(last)
                        and not _is_overlap(seg)
                    )
                    if same_key:
                        last["end"] = seg.get("end", last["end"])
                        last["words"].extend(seg.get("words", []))
                        last["text"] = " ".join(last["words"])
                    else:
                        c = dict(seg)
                        c["words"] = list(seg.get("words", []))
                        collapsed.append(c)

            current_segs = collapsed
            changed = True

    for seg in current_segs:
        if "words" in seg and not seg.get("text"):
            seg["text"] = " ".join(seg["words"])

    return current_segs


_TURN_LINE_RE = re.compile(
    r"^(?P<prefix>\[)(?P<speaker_tag>[^\]]+)(?P<middle>\]\s*\[\d\d:\d\d:\d\d(?:\.\d+)?\s*-\s*\d\d:\d\d:\d\d(?:\.\d+)?\]\s*)(?P<dialogue>.*)$"
)
_HEADER_SPEAKER_RE = re.compile(
    r"^(?P<key>Speaker|Speakers|Host|Panelist|Moderator|Guest):\s*(?P<val>.*)$"
)


def substitute_line_start_speaker_tags(text: str, tag_map: Dict[str, str]) -> Tuple[str, int]:
    """
    Substitutes speaker tags and aliases strictly at the beginning of transcript lines
    (turn prefixes and header keys). NEVER touches spoken dialogue or body text.

    Args:
        text: Full transcript file content.
        tag_map: Mapping from old tag/alias to new tag/name (or reverse).

    Returns:
        (transformed_text, replacement_count)
    """
    if not tag_map:
        return text, 0

    sorted_pairs: List[Tuple[str, str]] = []
    for k in sorted(tag_map.keys(), key=len, reverse=True):
        if not k or not tag_map[k] or k == tag_map[k]:
            continue
        sorted_pairs.append((k.strip("[]"), tag_map[k].strip("[]")))

    if not sorted_pairs:
        return text, 0

    def _replace_in_token(token: str) -> Tuple[str, int]:
        modified = token
        rep_count = 0
        for old_k, new_v in sorted_pairs:
            pattern = r"(?<!\w)" + re.escape(old_k) + r"(?!\w)"
            subbed, n = re.subn(pattern, new_v, modified)
            if n > 0:
                rep_count += n
                modified = subbed
        return modified, rep_count

    lines = text.splitlines(keepends=True)
    out_lines: List[str] = []
    total_replacements = 0

    for line in lines:
        line_ending = line[len(line.rstrip("\r\n")):]
        stripped_line = line.rstrip("\r\n")

        m_turn = _TURN_LINE_RE.match(stripped_line)
        if m_turn:
            prefix = m_turn.group("prefix")
            spk_tag = m_turn.group("speaker_tag")
            middle = m_turn.group("middle")
            dialogue = m_turn.group("dialogue")

            new_spk, cnt = _replace_in_token(spk_tag)
            if cnt > 0:
                total_replacements += cnt
                out_lines.append(f"{prefix}{new_spk}{middle}{dialogue}{line_ending}")
            else:
                out_lines.append(line)
            continue

        m_hdr = _HEADER_SPEAKER_RE.match(stripped_line)
        if m_hdr:
            key = m_hdr.group("key")
            val = m_hdr.group("val")

            new_val, cnt = _replace_in_token(val)
            if cnt > 0:
                total_replacements += cnt
                out_lines.append(f"{key}: {new_val}{line_ending}")
            else:
                out_lines.append(line)
            continue

        out_lines.append(line)

    return "".join(out_lines), total_replacements


