"""
Time Parser for Media Files in Session Scribe
Extracts and normalizes timestamps from filenames, EXIF metadata, and file modified times.
All output timestamps are timezone-aware and normalized to Indian Standard Time (IST, UTC+5:30).
"""

import os
import re
from datetime import datetime, timezone, timedelta
from typing import Optional, Tuple, Callable
from PIL import Image
from PIL.ExifTags import TAGS

# IST Timezone (UTC +5:30)
IST = timezone(timedelta(hours=5, minutes=30))

class TimeParser:
    """
    Extensible time parser for photos and videos.
    Tries filename pattern matching first, falls back to EXIF for images,
    and finally to filesystem modified time (mtime).
    """

    def __init__(self):
        # List of regex patterns and converter functions
        # Pattern signature: (regex_pattern, parse_handler)
        self.patterns = [
            # 1. DevX style: VID_yyyymmdd_hhmmssmmm (e.g. VID_20260726_104845057.mp4)
            (
                re.compile(r"^VID_(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})(\d{2})(\d{3})", re.IGNORECASE),
                self._parse_devx_pattern
            ),
            # 2. Elevate video style: VIDyyyymmddhhmmss (e.g. VID20260905114130.mp4 or VID_...)
            (
                re.compile(r"^VID_?(\d{4})(\d{2})(\d{2})(\d{2})(\d{2})(\d{2})", re.IGNORECASE),
                self._parse_14digits
            ),
            # 3. Elevate photo style: IMGyyyymmddhhmmss_NN (e.g. IMG20260905114522_01.jpg or IMG_...)
            (
                re.compile(r"^IMG_?(\d{4})(\d{2})(\d{2})(\d{2})(\d{2})(\d{2})_(\d+)", re.IGNORECASE),
                self._parse_elevate_photo
            ),
            # 4. Photo with ms: IMG_yyyymmdd_hhmmssmmm (e.g. IMG_20260718_170505583.jpg)
            (
                re.compile(r"^IMG_(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})(\d{2})(\d{3})", re.IGNORECASE),
                self._parse_devx_pattern
            ),
            # 5. Standard photo: IMGyyyymmddhhmmss or IMG_yyyymmdd_hhmmss
            (
                re.compile(r"^IMG_?(\d{4})(\d{2})(\d{2})_?(\d{2})(\d{2})(\d{2})", re.IGNORECASE),
                self._parse_14digits
            ),
            # 6. General yyyymmdd_hhmmss pattern anywhere in filename
            (
                re.compile(r"(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})(\d{2})", re.IGNORECASE),
                self._parse_14digits
            ),
            # 7. General 14-digit yyyymmddhhmmss sequence
            (
                re.compile(r"(\d{4})(\d{2})(\d{2})(\d{2})(\d{2})(\d{2})", re.IGNORECASE),
                self._parse_14digits
            ),
        ]

    def register_pattern(self, pattern: re.Pattern, handler: Callable[[re.Match], datetime]):
        """Allows runtime extension with custom patterns."""
        self.patterns.insert(0, (pattern, handler))

    @staticmethod
    def _parse_devx_pattern(m: re.Match) -> datetime:
        year = int(m.group(1))
        month = int(m.group(2))
        day = int(m.group(3))
        hour = int(m.group(4))
        minute = int(m.group(5))
        second = int(m.group(6))
        ms = int(m.group(7))
        return datetime(year, month, day, hour, minute, second, ms * 1000, tzinfo=IST)

    @staticmethod
    def _parse_14digits(m: re.Match) -> datetime:
        year = int(m.group(1))
        month = int(m.group(2))
        day = int(m.group(3))
        hour = int(m.group(4))
        minute = int(m.group(5))
        second = int(m.group(6))
        return datetime(year, month, day, hour, minute, second, 0, tzinfo=IST)

    @staticmethod
    def _parse_elevate_photo(m: re.Match) -> datetime:
        year = int(m.group(1))
        month = int(m.group(2))
        day = int(m.group(3))
        hour = int(m.group(4))
        minute = int(m.group(5))
        second = int(m.group(6))
        # Note: _NN suffix (m.group(7)) is burst/index sequence, not milliseconds
        return datetime(year, month, day, hour, minute, second, 0, tzinfo=IST)

    def parse_filename(self, filename: str) -> Optional[datetime]:
        """Attempt to extract timestamp from filename alone."""
        basename = os.path.basename(filename)
        for pattern, handler in self.patterns:
            m = pattern.search(basename)
            if m:
                try:
                    dt = handler(m)
                    # Sanity check year
                    if 2000 <= dt.year <= 2100:
                        return dt
                except (ValueError, IndexError):
                    continue
        return None

    @staticmethod
    def parse_exif(image_path: str) -> Optional[datetime]:
        """Attempt to extract capture timestamp from image EXIF metadata."""
        if not os.path.exists(image_path):
            return None
        try:
            with Image.open(image_path) as img:
                exif_data = img._getexif()
                if not exif_data:
                    return None

                # Search prioritized EXIF date tags
                # 36867 = DateTimeOriginal
                # 36868 = DateTimeDigitized
                # 306   = DateTime
                date_str = (
                    exif_data.get(36867) or
                    exif_data.get(36868) or
                    exif_data.get(306)
                )

                if not date_str or not isinstance(date_str, str):
                    return None

                # Optional offset (tag 36881 = OffsetTimeOriginal, 36880 = OffsetTime)
                offset_str = exif_data.get(36881) or exif_data.get(36880)
                
                # Format: "YYYY:MM:DD HH:MM:SS"
                date_str = date_str.strip()
                dt_naive = datetime.strptime(date_str, "%Y:%m:%d %H:%M:%S")

                if offset_str and isinstance(offset_str, str):
                    # Parse e.g. "+05:30"
                    m = re.match(r"^([+-])(\d{2}):(\d{2})$", offset_str.strip())
                    if m:
                        sign = 1 if m.group(1) == "+" else -1
                        h = int(m.group(2))
                        mins = int(m.group(3))
                        tz = timezone(sign * timedelta(hours=h, minutes=mins))
                        dt_aware = dt_naive.replace(tzinfo=tz)
                        return dt_aware.astimezone(IST)

                # If no offset specified, assume IST
                return dt_naive.replace(tzinfo=IST)
        except Exception:
            return None

    @staticmethod
    def parse_mtime(file_path: str) -> Optional[datetime]:
        """Fallback to file system modified time, converted to IST."""
        if not os.path.exists(file_path):
            return None
        try:
            mtime = os.path.getmtime(file_path)
            # Convert timestamp to aware IST datetime
            return datetime.fromtimestamp(mtime, tz=IST)
        except Exception:
            return None

    def get_timestamp(self, file_path: str, is_photo: bool = False) -> Tuple[datetime, str]:
        """
        Determine timestamp using fallback chain:
        1. Filename pattern
        2. Photo EXIF (if photo)
        3. Modified time (mtime)
        
        Returns:
            (datetime_in_ist, source_name)
        """
        # 1. Filename
        dt = self.parse_filename(file_path)
        if dt:
            return dt, "filename"

        # 2. EXIF (photos only)
        if is_photo:
            dt = self.parse_exif(file_path)
            if dt:
                return dt, "exif"

        # 3. Modified time fallback
        dt = self.parse_mtime(file_path)
        if dt:
            return dt, "mtime"

        # Extreme fallback: current time in IST
        return datetime.now(IST), "now_fallback"
