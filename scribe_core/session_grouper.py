"""
Session Grouper for Session Scribe
- Derives sessions from authoritative speaker mapping and time gaps (default 10 minutes idle gap)
- In One speaker mode, a session never contains two people
- Unmapped clips form their own sessions named 'Unnamed 1', 'Unnamed 2', etc. (never 'Speaker N')
- Assigns photos to sessions based on session time window [start - grace, end + grace] (default 3 minutes grace)
- Overlaps go to the nearest clip
- Leaves photos outside windows as unassigned without dropping them
"""

import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Tuple, Any

@dataclass
class ClipInfo:
    filename: str
    filepath: str
    start_time: datetime
    duration: float
    end_time: datetime
    time_source: str = "filename"
    speaker_name: Optional[str] = None
    person_id: Optional[str] = None

    def to_dict(self) -> Dict:
        return {
            "name": self.filename,
            "path": self.filepath,
            "speaker_name": self.speaker_name,
            "person_id": self.person_id,
            "start_time": self.start_time.isoformat() if self.start_time else None,
            "duration": round(self.duration, 2),
            "end_time": self.end_time.isoformat() if self.end_time else None,
            "time_source": self.time_source
        }

@dataclass
class PhotoInfo:
    filename: str
    filepath: str
    timestamp: datetime
    assigned_session_id: Optional[int] = None
    assigned_speaker: Optional[str] = None
    assignment_status: str = "Unassigned" # "Assigned" or "Unassigned"
    time_source: str = "filename"

    def to_dict(self) -> Dict:
        return {
            "name": self.filename,
            "path": self.filepath,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
            "assigned_session_id": self.assigned_session_id,
            "assigned_speaker": self.assigned_speaker,
            "status": self.assignment_status,
            "time_source": self.time_source
        }

@dataclass
class SessionInfo:
    session_id: int
    speaker_name: str
    clips: List[ClipInfo] = field(default_factory=list)
    photos: List[PhotoInfo] = field(default_factory=list)
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    session_type: str = "solo"     # "solo" or "panel"
    speaker_mode: str = "single"   # "single" (One speaker) or "panel" (Multiple speakers)
    expected_speaker: Optional[Dict[str, Any]] = None  # For solo: {"alias": "S01", "role": "Speaker", ...}
    host: Optional[Dict[str, Any]] = None              # For panel: {"alias": "S01", "role": "Host", ...}
    panelists: List[Dict[str, Any]] = field(default_factory=list) # For panel: [{"alias": "S02", ...}]
    roster: List[Dict[str, Any]] = field(default_factory=list)

    def calculate_bounds(self):
        if self.clips:
            self.start_time = min(c.start_time for c in self.clips)
            self.end_time = max(c.end_time for c in self.clips)

    def sync_roster_fields(self):
        """Synchronizes expected_speaker, host, and panelists with session_type and roster."""
        if self.session_type == "panel" or self.speaker_mode == "panel":
            self.session_type = "panel"
            self.speaker_mode = "panel"
            if self.host or self.panelists:
                combined = []
                if self.host:
                    combined.append(dict(self.host))
                for p in self.panelists:
                    combined.append(dict(p))
                self.roster = combined
            elif self.roster:
                hosts = [m for m in self.roster if m.get("role") == "Host"]
                if hosts:
                    self.host = dict(hosts[0])
                    self.panelists = [dict(m) for m in self.roster if m != hosts[0]]
                else:
                    self.host = dict(self.roster[0])
                    self.host["role"] = "Host"
                    self.panelists = [dict(m) for m in self.roster[1:]]
        else:
            self.session_type = "solo"
            self.speaker_mode = "single"
            if self.expected_speaker:
                self.roster = [dict(self.expected_speaker)]
            elif self.roster:
                self.expected_speaker = dict(self.roster[0])
                self.roster = [dict(self.expected_speaker)]
            else:
                self.roster = []

    def to_dict(self, compute_zone_safe: bool = True) -> Dict:
        self.calculate_bounds()
        self.sync_roster_fields()

        def clean_member(m: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
            if not m:
                return None
            res = {
                "alias": m.get("alias", m.get("name", "Unknown")),
                "role": m.get("role", "Speaker"),
            }
            if not compute_zone_safe and "name" in m:
                res["name"] = m["name"]
            if not compute_zone_safe and "spk_id" in m:
                res["spk_id"] = m["spk_id"]
            return res

        safe_roster = []
        for r in self.roster:
            c = clean_member(r)
            if c:
                safe_roster.append(c)

        safe_speaker = self.speaker_name
        if compute_zone_safe:
            if self.session_type == "solo" and self.expected_speaker:
                safe_speaker = self.expected_speaker.get("alias", self.speaker_name)
            elif self.session_type == "panel":
                safe_speaker = f"Panel Session {self.session_id}"

        res = {
            "session_id": self.session_id,
            "session_type": self.session_type,
            "speaker": safe_speaker,
            "speaker_mode": self.speaker_mode,
            "clip_count": len(self.clips),
            "photo_count": len(self.photos),
            "start_time": self.start_time.isoformat() if self.start_time else None,
            "end_time": self.end_time.isoformat() if self.end_time else None,
            "clips": [c.to_dict() for c in self.clips],
            "photos": [p.to_dict() for p in self.photos],
            "roster": safe_roster,
        }

        if self.session_type == "solo":
            res["expected_speaker"] = clean_member(self.expected_speaker)
        else:
            res["host"] = clean_member(self.host)
            res["panelists"] = [clean_member(p) for p in self.panelists if p]

        return res


class SessionGrouper:
    """Groups clips into sessions and assigns photos to sessions by time window."""

    def __init__(self, gap_threshold_seconds: float = 600.0, photo_grace_seconds: float = 180.0):
        self.gap_threshold_seconds = gap_threshold_seconds # 10 minutes
        self.photo_grace_seconds = photo_grace_seconds       # 3 minutes

    def group_clips(
        self,
        clips: List[ClipInfo],
        custom_speakers: Optional[Dict[int, str]] = None,
        default_speaker_mode: str = "single",
        mapping_dict: Optional[Dict[str, str]] = None,
    ) -> List[SessionInfo]:
        """
        Derives sessions from authoritative speaker mapping and time gaps.
        - Clips are sorted by start_time.
        - A session is a run of consecutive clips of the same person.
        - Splits when speaker changes, or when gap between clip's start and previous clip's end > threshold.
        - In One speaker mode, a session never contains two people.
        - Unmapped clips form their own individual sessions named 'Unnamed 1', 'Unnamed 2', etc.
        """
        if not clips:
            return []

        # If mapping_dict is supplied, assign speaker_name to clips that lack it
        if mapping_dict:
            norm_map = {}
            for k, v in mapping_dict.items():
                norm_map[k.strip().upper()] = v.strip()
                stem = os.path.splitext(k.strip())[0].upper()
                norm_map[stem] = v.strip()

            for c in clips:
                if not c.speaker_name:
                    c_fn = c.filename.strip().upper()
                    c_stem = os.path.splitext(c.filename.strip())[0].upper()
                    if c_fn in norm_map:
                        c.speaker_name = norm_map[c_fn]
                    elif c_stem in norm_map:
                        c.speaker_name = norm_map[c_stem]

        sorted_clips = sorted(clips, key=lambda c: c.start_time)
        sessions: List[SessionInfo] = []
        current_session_clips: List[ClipInfo] = []
        current_speaker: Optional[str] = None
        session_id = 1
        unnamed_counter = 1

        def finalize_session(sess_clips: List[ClipInfo], spk: str):
            nonlocal session_id
            sess = SessionInfo(
                session_id=session_id,
                speaker_name=spk,
                clips=list(sess_clips),
                speaker_mode=default_speaker_mode,
            )
            sess.calculate_bounds()
            sessions.append(sess)
            session_id += 1

        for clip in sorted_clips:
            clip_speaker = (clip.speaker_name or "").strip()
            # If name is "Speaker N" from an old unmapped state, treat as unmapped
            is_unmapped = not clip_speaker or clip_speaker.startswith("Unnamed ")

            if is_unmapped:
                # Unmapped clips form their own individual sessions named 'Unnamed 1', 'Unnamed 2'...
                if current_session_clips:
                    finalize_session(current_session_clips, current_speaker or f"Unnamed {unnamed_counter}")
                    current_session_clips = []
                    current_speaker = None

                unnamed_name = f"Unnamed {unnamed_counter}"
                unnamed_counter += 1
                clip.speaker_name = unnamed_name
                finalize_session([clip], unnamed_name)
            else:
                # Mapped clip
                if not current_session_clips:
                    current_session_clips.append(clip)
                    current_speaker = clip_speaker
                else:
                    idle_gap = (clip.start_time - current_session_clips[-1].end_time).total_seconds()
                    # Split if different speaker OR gap exceeds threshold
                    if clip_speaker != current_speaker or idle_gap > self.gap_threshold_seconds:
                        finalize_session(current_session_clips, current_speaker)
                        current_session_clips = [clip]
                        current_speaker = clip_speaker
                    else:
                        current_session_clips.append(clip)

        if current_session_clips:
            finalize_session(current_session_clips, current_speaker or f"Unnamed {unnamed_counter}")

        # Backwards compatibility: if custom_speakers was provided and mapping_dict was not,
        # apply any custom names by session_id
        if custom_speakers and not mapping_dict:
            for s in sessions:
                if s.session_id in custom_speakers:
                    s.speaker_name = custom_speakers[s.session_id]
                    for c in s.clips:
                        c.speaker_name = s.speaker_name

        return sessions

    def assign_photos(
        self, photos: List[PhotoInfo], sessions: List[SessionInfo]
    ) -> Tuple[List[SessionInfo], List[PhotoInfo]]:
        """
        Assigns photos to sessions based on session time window [start - grace, end + grace].
        If a photo falls into multiple overlapping windows, it is assigned to the session
        whose clips are closest in absolute time difference (overlaps go to the nearest clip).
        Photos outside all windows remain marked as Unassigned.
        """
        grace = timedelta(seconds=self.photo_grace_seconds)
        unassigned_photos: List[PhotoInfo] = []

        # Clear existing photo lists in sessions and calculate bounds
        for s in sessions:
            s.photos.clear()
            s.calculate_bounds()

        for photo in photos:
            candidates: List[Tuple[SessionInfo, float]] = []

            for s in sessions:
                if not s.start_time or not s.end_time:
                    continue
                window_start = s.start_time - grace
                window_end = s.end_time + grace

                if window_start <= photo.timestamp <= window_end:
                    # Calculate distance to the nearest clip in this session
                    min_clip_dist = float("inf")
                    for c in s.clips:
                        if c.start_time <= photo.timestamp <= c.end_time:
                            dist = 0.0
                        else:
                            dist = min(
                                abs((photo.timestamp - c.start_time).total_seconds()),
                                abs((photo.timestamp - c.end_time).total_seconds()),
                            )
                        if dist < min_clip_dist:
                            min_clip_dist = dist
                    candidates.append((s, min_clip_dist))

            if candidates:
                # Overlaps go to the nearest clip: sort by min_clip_dist
                candidates.sort(key=lambda x: x[1])
                best_session = candidates[0][0]
                photo.assigned_session_id = best_session.session_id
                photo.assigned_speaker = best_session.speaker_name
                photo.assignment_status = "Assigned"
                best_session.photos.append(photo)
            else:
                photo.assigned_session_id = None
                photo.assigned_speaker = None
                photo.assignment_status = "Unassigned"
                unassigned_photos.append(photo)

        return sessions, unassigned_photos


def merge_sessions(
    sessions: List[SessionInfo],
    session_ids: List[int],
    target_session_type: Optional[str] = None,
) -> List[SessionInfo]:
    """
    Manually merges two or more sessions into a single session.
    - Combines all clips from the target sessions, sorted by start_time.
    - Combines and re-associates photos.
    - Sets session_type to target_session_type (or 'panel' if any merged session was panel).
    - Preserves the lowest session_id among merged sessions.
    - Recalculates time bounds.
    """
    if len(session_ids) < 2:
        raise ValueError("At least two sessions must be selected for merging.")

    id_set = set(session_ids)
    target_sessions = [s for s in sessions if s.session_id in id_set]
    if len(target_sessions) != len(id_set):
        raise ValueError(f"One or more session IDs not found: {session_ids}")

    # Determine primary session ID (lowest ID)
    primary_id = min(session_ids)

    # Collect all clips and photos
    all_clips: List[ClipInfo] = []
    all_photos: List[PhotoInfo] = []
    merged_roster: List[Dict[str, Any]] = []
    seen_aliases = set()

    is_panel = target_session_type == "panel" or any(s.session_type == "panel" for s in target_sessions)

    for s in target_sessions:
        all_clips.extend(s.clips)
        all_photos.extend(s.photos)
        for r in s.roster:
            alias = r.get("alias") or r.get("name")
            if alias and alias not in seen_aliases:
                seen_aliases.add(alias)
                merged_roster.append(dict(r))

    all_clips.sort(key=lambda c: c.start_time)

    # Create merged session
    merged = SessionInfo(
        session_id=primary_id,
        speaker_name=f"Merged Session {primary_id}",
        clips=all_clips,
        photos=all_photos,
        session_type="panel" if is_panel else "solo",
        speaker_mode="panel" if is_panel else "single",
        roster=merged_roster,
    )
    merged.calculate_bounds()
    merged.sync_roster_fields()

    # Build updated sessions list, replacing merged sessions with single merged session
    new_sessions: List[SessionInfo] = []
    merged_inserted = False

    for s in sessions:
        if s.session_id in id_set:
            if not merged_inserted:
                new_sessions.append(merged)
                merged_inserted = True
        else:
            new_sessions.append(s)

    return new_sessions


def split_session(
    sessions: List[SessionInfo],
    session_id: int,
    split_at_clip_index: int,
) -> List[SessionInfo]:
    """
    Manually splits a session into two separate sessions at a clip index boundary.
    - Part 1 retains session_id with clips[:split_at_clip_index].
    - Part 2 gets a new session_id with clips[split_at_clip_index:].
    - Both sessions recalculate time bounds and partition photos.
    """
    target: Optional[SessionInfo] = None
    target_idx = -1
    for idx, s in enumerate(sessions):
        if s.session_id == session_id:
            target = s
            target_idx = idx
            break

    if target is None:
        raise ValueError(f"Session ID {session_id} not found.")

    if split_at_clip_index <= 0 or split_at_clip_index >= len(target.clips):
        raise ValueError(
            f"Invalid split index {split_at_clip_index}: must be between 1 and {len(target.clips) - 1}."
        )

    # Next available session ID
    next_id = max(s.session_id for s in sessions) + 1

    part1_clips = list(target.clips[:split_at_clip_index])
    part2_clips = list(target.clips[split_at_clip_index:])

    part1 = SessionInfo(
        session_id=target.session_id,
        speaker_name=target.speaker_name,
        clips=part1_clips,
        session_type=target.session_type,
        speaker_mode=target.speaker_mode,
        expected_speaker=dict(target.expected_speaker) if target.expected_speaker else None,
        host=dict(target.host) if target.host else None,
        panelists=[dict(p) for p in target.panelists],
        roster=[dict(r) for r in target.roster],
    )
    part1.calculate_bounds()
    part1.sync_roster_fields()

    part2 = SessionInfo(
        session_id=next_id,
        speaker_name=f"{target.speaker_name} (Part 2)",
        clips=part2_clips,
        session_type=target.session_type,
        speaker_mode=target.speaker_mode,
        expected_speaker=dict(target.expected_speaker) if target.expected_speaker else None,
        host=dict(target.host) if target.host else None,
        panelists=[dict(p) for p in target.panelists],
        roster=[dict(r) for r in target.roster],
    )
    part2.calculate_bounds()
    part2.sync_roster_fields()

    # Re-partition photos
    for p in target.photos:
        if part1.start_time and part1.end_time and part1.start_time <= p.timestamp <= part1.end_time:
            p.assigned_session_id = part1.session_id
            part1.photos.append(p)
        elif part2.start_time and part2.end_time and part2.start_time <= p.timestamp <= part2.end_time:
            p.assigned_session_id = part2.session_id
            part2.photos.append(p)
        else:
            d1 = min(abs((p.timestamp - c.start_time).total_seconds()) for c in part1.clips)
            d2 = min(abs((p.timestamp - c.start_time).total_seconds()) for c in part2.clips)
            if d1 <= d2:
                p.assigned_session_id = part1.session_id
                part1.photos.append(p)
            else:
                p.assigned_session_id = part2.session_id
                part2.photos.append(p)

    new_sessions = list(sessions)
    new_sessions[target_idx] = part1
    new_sessions.insert(target_idx + 1, part2)

    return new_sessions
