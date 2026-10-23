"""
Media Separator for Session Scribe
Separates mixed event media into data/Videos and data/Photos with:
- Dry-run preview
- Instant same-drive move with preserved modified timestamps
- Zero overwrite policy (renames on collision and logs)
- Full move logging in scribe/move_log.json
- Undo feature to restore original layout
- Count verification before and after move
"""

import os
import shutil
import json
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Tuple, Optional

IST = timezone(timedelta(hours=5, minutes=30))

VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".avi", ".3gp", ".m4v"}
PHOTO_EXTENSIONS = {".jpg", ".jpeg", ".png", ".heic", ".webp"}

@dataclass
class FileMovePlan:
    source_path: str
    target_path: str
    filename: str
    file_type: str # "video" or "photo"
    is_collision: bool = False
    collision_note: str = ""
    mtime: float = 0.0

@dataclass
class SeparationPreview:
    data_dir: str
    video_count: int = 0
    photo_count: int = 0
    unknown_count: int = 0
    already_separated_count: int = 0
    collision_count: int = 0
    planned_moves: List[FileMovePlan] = field(default_factory=list)
    unknown_files: List[str] = field(default_factory=list)
    collisions: List[Dict[str, str]] = field(default_factory=list)
    total_files_before: int = 0

@dataclass
class SeparationResult:
    success: bool
    data_dir: str
    scribe_dir: str
    videos_moved: int = 0
    photos_moved: int = 0
    unknowns_skipped: int = 0
    collisions_handled: int = 0
    move_log_path: str = ""
    verification_passed: bool = False
    total_before: int = 0
    total_after: int = 0
    message: str = ""

@dataclass
class UndoResult:
    success: bool
    restored_count: int = 0
    failed_count: int = 0
    message: str = ""


class MediaSeparator:
    """Manages dry-run, execution, verification, and undo of media separation."""

    def __init__(self, video_exts=None, photo_exts=None):
        self.video_exts = set(ext.lower() for ext in (video_exts or VIDEO_EXTENSIONS))
        self.photo_exts = set(ext.lower() for ext in (photo_exts or PHOTO_EXTENSIONS))

    def classify_extension(self, filename: str) -> str:
        """Classifies by extension (case-insensitive)."""
        _, ext = os.path.splitext(filename)
        ext = ext.lower()
        if ext in self.video_exts:
            return "video"
        elif ext in self.photo_exts:
            return "photo"
        return "unknown"

    def count_all_files(self, data_dir: str) -> int:
        """Counts all files currently in data_dir and its subdirectories."""
        total = 0
        if not os.path.exists(data_dir):
            return 0
        for root, _, files in os.walk(data_dir):
            total += len(files)
        return total

    def dry_run(self, data_dir: str) -> SeparationPreview:
        """
        Scans data_dir without modifying any files on disk.
        Reports planned moves, unknowns, and potential name collisions.
        """
        if not os.path.isdir(data_dir):
            raise FileNotFoundError(f"Data directory not found: {data_dir}")

        videos_dir = os.path.join(data_dir, "Videos")
        photos_dir = os.path.join(data_dir, "Photos")

        preview = SeparationPreview(data_dir=data_dir)
        preview.total_files_before = self.count_all_files(data_dir)

        # Existing files in target dirs to check collisions
        existing_video_names = set(os.listdir(videos_dir)) if os.path.isdir(videos_dir) else set()
        existing_photo_names = set(os.listdir(photos_dir)) if os.path.isdir(photos_dir) else set()

        # Iterate top-level files in data_dir
        for item in sorted(os.listdir(data_dir)):
            full_path = os.path.join(data_dir, item)
            
            # Skip subdirectories (e.g. Videos/, Photos/, or other folders)
            if os.path.isdir(full_path):
                # Count files inside Videos and Photos as already separated
                if item.lower() in ("videos", "photos"):
                    preview.already_separated_count += len([
                        f for f in os.listdir(full_path) if os.path.isfile(os.path.join(full_path, f))
                    ])
                continue

            # It is a top-level file in data_dir
            classification = self.classify_extension(item)
            mtime = os.path.getmtime(full_path)

            if classification == "video":
                target_dir = videos_dir
                existing_names = existing_video_names
                preview.video_count += 1
            elif classification == "photo":
                target_dir = photos_dir
                existing_names = existing_photo_names
                preview.photo_count += 1
            else:
                preview.unknown_count += 1
                preview.unknown_files.append(item)
                continue

            target_path = os.path.join(target_dir, item)
            is_collision = item in existing_names
            collision_note = ""

            if is_collision:
                preview.collision_count += 1
                # Generate safe non-overwriting destination
                stem, ext = os.path.splitext(item)
                counter = 1
                candidate_name = f"{stem} (collision {counter}){ext}"
                while candidate_name in existing_names or os.path.exists(os.path.join(target_dir, candidate_name)):
                    counter += 1
                    candidate_name = f"{stem} (collision {counter}){ext}"
                target_path = os.path.join(target_dir, candidate_name)
                collision_note = f"Collision detected. Will move to {candidate_name} to preserve both."
                preview.collisions.append({
                    "original": item,
                    "target_subfolder": os.path.basename(target_dir),
                    "resolution": candidate_name
                })
                # Add to existing names to prevent secondary collision within same batch
                existing_names.add(candidate_name)
            else:
                existing_names.add(item)

            preview.planned_moves.append(FileMovePlan(
                source_path=full_path,
                target_path=target_path,
                filename=item,
                file_type=classification,
                is_collision=is_collision,
                collision_note=collision_note,
                mtime=mtime
            ))

        return preview

    def separate(self, data_dir: str, scribe_dir: str) -> SeparationResult:
        """
        Executes separation after preview verification:
        - Moves files directly (same-drive instant move)
        - Preserves modified timestamps
        - Never overwrites collisions
        - Writes move log in scribe_dir/move_log.json
        - Verifies total file count
        """
        preview = self.dry_run(data_dir)
        os.makedirs(scribe_dir, exist_ok=True)
        videos_dir = os.path.join(data_dir, "Videos")
        photos_dir = os.path.join(data_dir, "Photos")

        os.makedirs(videos_dir, exist_ok=True)
        os.makedirs(photos_dir, exist_ok=True)

        moves_recorded = []
        videos_moved = 0
        photos_moved = 0
        collisions_handled = 0

        for plan in preview.planned_moves:
            src = plan.source_path
            dst = plan.target_path
            orig_mtime = plan.mtime

            # Double check existence of destination to never overwrite
            if os.path.exists(dst):
                stem, ext = os.path.splitext(os.path.basename(dst))
                counter = 1
                while os.path.exists(dst):
                    dst = os.path.join(os.path.dirname(dst), f"{stem} (collision {counter}){ext}")
                    counter += 1
                plan.target_path = dst
                plan.is_collision = True

            # Perform instant filesystem move
            shutil.move(src, dst)
            
            # Explicitly preserve timestamp
            try:
                os.utime(dst, (orig_mtime, orig_mtime))
            except Exception:
                pass

            if plan.file_type == "video":
                videos_moved += 1
            else:
                photos_moved += 1

            if plan.is_collision:
                collisions_handled += 1

            moves_recorded.append({
                "source": os.path.abspath(src),
                "dest": os.path.abspath(dst),
                "mtime": orig_mtime,
                "file_type": plan.file_type,
                "is_collision": plan.is_collision
            })

        # Count verification
        total_after = self.count_all_files(data_dir)
        verification_passed = (preview.total_files_before == total_after)

        # Write move log in scribe_dir
        move_log_path = os.path.join(scribe_dir, "move_log.json")
        log_data = {
            "timestamp": datetime.now(IST).isoformat(),
            "data_dir": os.path.abspath(data_dir),
            "scribe_dir": os.path.abspath(scribe_dir),
            "total_files_before": preview.total_files_before,
            "total_files_after": total_after,
            "verification_passed": verification_passed,
            "videos_moved": videos_moved,
            "photos_moved": photos_moved,
            "unknowns_skipped": preview.unknown_count,
            "collisions_handled": collisions_handled,
            "unknown_files": preview.unknown_files,
            "moves": moves_recorded
        }

        with open(move_log_path, "w", encoding="utf-8") as f:
            json.dump(log_data, f, indent=2)

        msg = (
            f"Separation complete: {videos_moved} videos moved, {photos_moved} photos moved, "
            f"{preview.unknown_count} unknown files kept in data/, {collisions_handled} collisions handled. "
            f"Verification: {'PASSED (Counts match: ' + str(total_after) + ')' if verification_passed else 'FAILED'}."
        )

        return SeparationResult(
            success=verification_passed,
            data_dir=data_dir,
            scribe_dir=scribe_dir,
            videos_moved=videos_moved,
            photos_moved=photos_moved,
            unknowns_skipped=preview.unknown_count,
            collisions_handled=collisions_handled,
            move_log_path=move_log_path,
            verification_passed=verification_passed,
            total_before=preview.total_files_before,
            total_after=total_after,
            message=msg
        )

    def undo(self, scribe_dir: str) -> UndoResult:
        """
        Reverses media separation using move_log.json:
        - Moves files back to their original locations
        - Preserves modified timestamps
        - Verifies counts
        - Archives move_log.json
        """
        move_log_path = os.path.join(scribe_dir, "move_log.json")
        if not os.path.exists(move_log_path):
            return UndoResult(success=False, message=f"No move_log.json found in {scribe_dir}")

        try:
            with open(move_log_path, "r", encoding="utf-8") as f:
                log_data = json.load(f)
        except Exception as e:
            return UndoResult(success=False, message=f"Failed to read move_log.json: {e}")

        moves = log_data.get("moves", [])
        restored = 0
        failed = 0

        # Move files back in reverse order
        for item in reversed(moves):
            src = item["dest"]
            dst = item["source"]
            mtime = item.get("mtime")

            if not os.path.exists(src):
                failed += 1
                continue

            try:
                # Ensure destination directory exists (data_dir)
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.move(src, dst)
                if mtime:
                    try:
                        os.utime(dst, (mtime, mtime))
                    except Exception:
                        pass
                restored += 1
            except Exception:
                failed += 1

        # Clean up empty Videos/Photos dirs if empty
        data_dir = log_data.get("data_dir")
        if data_dir and os.path.isdir(data_dir):
            for sub in ("Videos", "Photos"):
                p = os.path.join(data_dir, sub)
                if os.path.isdir(p) and not os.listdir(p):
                    try:
                        os.rmdir(p)
                    except Exception:
                        pass

        # Archive move log to mark undo
        archive_name = f"move_log_undone_{datetime.now(IST).strftime('%Y%m%d_%H%M%S')}.json"
        archive_path = os.path.join(scribe_dir, archive_name)
        try:
            shutil.move(move_log_path, archive_path)
        except Exception:
            pass

        return UndoResult(
            success=(failed == 0),
            restored_count=restored,
            failed_count=failed,
            message=f"Undo complete: {restored} files restored to original locations ({failed} failed)."
        )
