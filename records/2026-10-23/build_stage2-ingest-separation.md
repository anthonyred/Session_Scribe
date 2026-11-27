# Stage 2: Event Setup, Ingest & Separation Build Notes

**Date:** 2026-10-23  
**Workspace:** `D:\Session_Scribe\`  
**Module:** `scribe_core` & `gui`  

---

## 1. Overview & Architecture

Stage 2 replaces hardcoded event scripts with an automated, reproducible setup, media ingest, separation, and session alignment workflow.

### Component Structure
- `scribe_core.time_parser.TimeParser`: Extensible timestamp parser with regex pattern matching, EXIF capture time fallback, and filesystem modified time fallback. All timestamps are normalized to Indian Standard Time (IST, UTC+5:30).
- `scribe_core.clip_probe.ClipProbe`: Dynamically resolves `ffprobe` from PATH at runtime. Reads media duration and creation metadata using safe subprocess argument lists without shell interpretation.
- `scribe_core.media_separator.MediaSeparator`: Handles media classification, dry-run previews, instant same-drive moving, modified timestamp preservation, collision safety, move logging in `D:\Events\<event>\scribe\move_log.json`, count verification, and full undo functionality.
- `scribe_core.session_grouper.SessionGrouper`: Auto-groups video clips based on start time gaps (default 10 minutes), provides default speaker placeholders (`Speaker 1`, `Speaker 2`), and assigns photos to sessions based on session time windows `[start - grace, end + grace]` (default 3 minutes grace) while keeping photos outside windows marked as `Unassigned`.
- `scribe_core.manifest.ManifestManager`: Validates event details, generates `manifest.json` in `D:\Events\<event>\scribe\`, and enforces validation warnings when venue or organiser is missing.
- `gui.app.SessionScribeApp`: Standard Tkinter/ttk desktop application featuring notebook stage tabs, non-blocking threaded background execution, editable session tables, and hardware status display.
- `launch_session_scribe.bat`: Launcher batch script starting Session Scribe from the shared virtual environment.

---

## 2. Ingest & Separation Rules Verified

1. **Classification:**
   - Videos: `.mp4`, `.mov`, `.mkv`, `.avi`, `.3gp`, `.m4v` -> moved to `data\Videos`
   - Photos: `.jpg`, `.jpeg`, `.png`, `.heic`, `.webp` -> moved to `data\Photos`
   - Unknown/Other files: remain in `data\` and are recorded in the preview report.
2. **Dry-Run Preview:**
   - Reports video count, photo count, unknown files, and potential name collisions.
   - Strictly alters zero bytes on disk until confirmed.
3. **Execution & Collision Safety:**
   - Same-drive move is instant and preserves modified timestamps (`os.utime`).
   - On destination name collisions, the target is never overwritten; colliding files are moved with unique collision suffixes (`(collision N)`) and logged.
   - Writes `D:\Events\<event>\scribe\move_log.json`.
   - Verifies total file count before equals total files after.
4. **Undo Lifecycle:**
   - Reads `move_log.json`, restores all moved media to their exact original locations, restores timestamps, and archives the move log.

---

## 3. Acceptance Check Results

### A. Elevate Video Grouping (16 Videos)
With gap threshold = 10 minutes (600s):
- **Session 1 (Speaker 1):** 3 clips (11:41:30 to 11:45:22)
- **Session 2 (Speaker 2):** 3 clips (12:43:16 to 12:50:57)
- **Session 3 (Speaker 3):** 9 clips (15:17:47 to 15:31:45)
- **Session 4 (Speaker 4):** 1 clip  (15:47:17 to 15:48:14)
- **Total:** Exactly 4 sessions of **3, 3, 9, and 1 clips** (**PASSED**).

### B. Elevate Target Photos Assignment
With photo grace margin = 3 minutes (180s):
- `IMG20260905114522_01.jpg` (11:45:22) -> **Speaker 1** [Assigned]
- `IMG20260905151815_01.jpg` (15:18:15) -> **Speaker 3** [Assigned]
- `IMG20260905152136_01.jpg` (15:21:36) -> **Speaker 3** [Assigned]
- `IMG20260905152930_01.jpg` (15:29:30) -> **Speaker 3** [Assigned]
- `IMG20260905152951_01.jpg` (15:29:51) -> **Speaker 3** [Assigned]
- **Total Photos in Elevate:** 41 photos (30 assigned within session windows, 11 unassigned outside session windows). None dropped (**PASSED**).

### C. DevX Video Grouping (28 Videos)
With gap threshold = 10 minutes (600s):
- **Session 1 (Speaker 1):** 14 clips (10:48:45 to 11:20:06)
- **Session 2 (Speaker 2):** 14 clips (12:14:49 to 13:21:12)
- **Total:** Exactly 2 sessions of **14 and 14 clips** (**PASSED**).

### D. Media Separation & Undo Lifecycle
Tested with mixed media batch:
- Dry-run preview: 3 videos, 3 photos, 2 unknown files, 1 collision detected.
- Disk verification after dry-run: 0 changes on disk (**PASSED**).
- Execution: files moved instantly, timestamps preserved, collision preserved without overwrite, file counts verified (**PASSED**).
- Undo: all files restored to original locations, timestamps preserved, counts verified (**PASSED**).

### E. Manifest Generation
- Saved to `D:\Events\<event>\scribe\manifest.json`.
- Event details, sessions, clips, assigned photos, unassigned photos, and settings recorded.
- Optional sponsors omitted when blank (**PASSED**).
- Validation warnings triggered if venue or organiser is missing (**PASSED**).
