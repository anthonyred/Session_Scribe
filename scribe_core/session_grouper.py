"""
Session Grouper for Session Scribe
- Auto-groups video clips by gap between start times (default 10 minutes)
- Assigns default speaker names ("Speaker 1", "Speaker 2", ...)
- Assigns photos to sessions based on session time window [start - grace, end + grace] (default 3 minutes grace)
- Leaves photos outside windows as unassigned without dropping them
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Tuple

@dataclass
class ClipInfo:
    filename: str
    filepath: str
    start_time: datetime
    duration: float
    end_time: datetime
    time_source: str = "filename"

    def to_dict(self) -> Dict:
        return {
            "name": self.filename,
            "path": self.filepath,
            "start_time": self.start_time.isoformat(),
            "duration": round(self.duration, 2),
            "end_time": self.end_time.isoformat(),
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
            "timestamp": self.timestamp.isoformat(),
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

    def calculate_bounds(self):
        if self.clips:
            self.start_time = min(c.start_time for c in self.clips)
            self.end_time = max(c.end_time for c in self.clips)

    def to_dict(self) -> Dict:
        self.calculate_bounds()
        return {
            "session_id": self.session_id,
            "speaker": self.speaker_name,
            "clip_count": len(self.clips),
            "photo_count": len(self.photos),
            "start_time": self.start_time.isoformat() if self.start_time else None,
            "end_time": self.end_time.isoformat() if self.end_time else None,
            "clips": [c.to_dict() for c in self.clips],
            "photos": [p.to_dict() for p in self.photos]
        }


class SessionGrouper:
    """Groups clips into sessions and assigns photos to sessions by time window."""

    def __init__(self, gap_threshold_seconds: float = 600.0, photo_grace_seconds: float = 180.0):
        self.gap_threshold_seconds = gap_threshold_seconds # 10 minutes
        self.photo_grace_seconds = photo_grace_seconds       # 3 minutes

    def group_clips(self, clips: List[ClipInfo], custom_speakers: Optional[Dict[int, str]] = None) -> List[SessionInfo]:
        """
        Groups clips by gap between consecutive start times.
        Clips must be sorted by start_time.
        """
        if not clips:
            return []

        sorted_clips = sorted(clips, key=lambda c: c.start_time)
        sessions: List[SessionInfo] = []
        current_clips: List[ClipInfo] = []
        current_session_id = 1

        for clip in sorted_clips:
            if not current_clips:
                current_clips.append(clip)
            else:
                gap = (clip.start_time - current_clips[-1].start_time).total_seconds()
                if gap > self.gap_threshold_seconds:
                    # Finalize current session
                    speaker_name = (
                        custom_speakers.get(current_session_id)
                        if custom_speakers and current_session_id in custom_speakers
                        else f"Speaker {current_session_id}"
                    )
                    sess = SessionInfo(
                        session_id=current_session_id,
                        speaker_name=speaker_name,
                        clips=list(current_clips)
                    )
                    sess.calculate_bounds()
                    sessions.append(sess)

                    # Start new session
                    current_session_id += 1
                    current_clips = [clip]
                else:
                    current_clips.append(clip)

        if current_clips:
            speaker_name = (
                custom_speakers.get(current_session_id)
                if custom_speakers and current_session_id in custom_speakers
                else f"Speaker {current_session_id}"
            )
            sess = SessionInfo(
                session_id=current_session_id,
                speaker_name=speaker_name,
                clips=list(current_clips)
            )
            sess.calculate_bounds()
            sessions.append(sess)

        return sessions

    def assign_photos(self, photos: List[PhotoInfo], sessions: List[SessionInfo]) -> Tuple[List[SessionInfo], List[PhotoInfo]]:
        """
        Assigns photos to sessions based on session time window [start - grace, end + grace].
        If a photo falls into multiple overlapping windows, it is assigned to the session
        whose clips are closest in absolute time difference.
        Photos outside all windows remain marked as Unassigned.
        """
        grace = timedelta(seconds=self.photo_grace_seconds)
        unassigned_photos: List[PhotoInfo] = []

        # Clear existing photo lists in sessions
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
                    # Calculate proximity to session center/closest clip
                    mid = s.start_time + (s.end_time - s.start_time) / 2
                    distance = abs((photo.timestamp - mid).total_seconds())
                    candidates.append((s, distance))

            if candidates:
                # Pick the closest candidate session
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
