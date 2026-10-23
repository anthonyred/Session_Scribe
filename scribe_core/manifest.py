"""
Manifest Manager for Session Scribe
Creates, validates, and serializes the complete event manifest to D:\\Events\\<event>\\scribe\\manifest.json.
"""

import os
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Optional, Tuple, Any

from .session_grouper import SessionInfo, PhotoInfo

IST = timezone(timedelta(hours=5, minutes=30))

@dataclass
class EventDetails:
    event_name: str
    date: str
    organiser: str
    venue: str
    sponsors: Optional[str] = None
    speaker_mode: str = "single" # "single" or "panel"

    def validate(self) -> List[str]:
        """Validates fields. Returns list of warning messages."""
        warnings = []
        if not self.event_name or not self.event_name.strip():
            warnings.append("Event name is missing.")
        if not self.date or not self.date.strip():
            warnings.append("Date is missing.")
        if not self.organiser or not self.organiser.strip():
            warnings.append("Organiser is missing (required for technical document generation).")
        if not self.venue or not self.venue.strip():
            warnings.append("Venue is missing (required for technical document generation).")
        return warnings


class ManifestManager:
    """Handles serialization and loading of manifest.json."""

    @staticmethod
    def build_manifest(
        event: EventDetails,
        sessions: List[SessionInfo],
        unassigned_photos: List[PhotoInfo],
        settings_used: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Constructs the JSON-serializable manifest dictionary."""
        data: Dict[str, Any] = {
            "version": "1.0",
            "created_at": datetime.now(IST).isoformat(),
            "event": {
                "name": event.event_name.strip(),
                "date": event.date.strip(),
                "organiser": event.organiser.strip(),
                "venue": event.venue.strip(),
                "speaker_mode": event.speaker_mode
            },
            "settings": settings_used,
            "sessions": [s.to_dict() for s in sessions],
            "unassigned_photos": [p.to_dict() for p in unassigned_photos],
            "summary": {
                "total_sessions": len(sessions),
                "total_clips": sum(len(s.clips) for s in sessions),
                "assigned_photos": sum(len(s.photos) for s in sessions),
                "unassigned_photos": len(unassigned_photos),
                "total_photos": sum(len(s.photos) for s in sessions) + len(unassigned_photos)
            }
        }

        # Sponsors: optional; omit if blank
        if event.sponsors and event.sponsors.strip():
            data["event"]["sponsors"] = event.sponsors.strip()

        return data

    @classmethod
    def save_manifest(
        cls,
        scribe_dir: str,
        event: EventDetails,
        sessions: List[SessionInfo],
        unassigned_photos: List[PhotoInfo],
        settings_used: Dict[str, Any]
    ) -> Tuple[str, List[str]]:
        """
        Validates event details and saves manifest.json to scribe_dir.
        Returns:
            (manifest_path, list_of_warnings)
        """
        warnings = event.validate()
        manifest_data = cls.build_manifest(event, sessions, unassigned_photos, settings_used)

        os.makedirs(scribe_dir, exist_ok=True)
        manifest_path = os.path.join(scribe_dir, "manifest.json")

        with open(manifest_path, "w", encoding="utf-8") as f:
            json.dump(manifest_data, f, indent=2)

        return manifest_path, warnings

    @staticmethod
    def load_manifest(manifest_path: str) -> Dict[str, Any]:
        """Loads and returns an existing manifest."""
        if not os.path.exists(manifest_path):
            raise FileNotFoundError(f"Manifest not found: {manifest_path}")
        with open(manifest_path, "r", encoding="utf-8") as f:
            return json.load(f)
