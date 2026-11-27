# Change: Records Folder Restructure and Register Standardization

- **Date:** 2026-11-03
- **Type:** change
- **Status:** Complete

---

## 1. What was asked
1. Consolidate all dated folders (`2026-10-23`, `2026-10-25`, `2026-10-26`, `2026-10-28`, `2026-10-30`) into a single top-level `records/` directory (`records/YYYY-MM-DD/`).
2. Update all references in `CHANGELOG.md`, `AGENTS.md`, notes, and test scripts to reference the new paths.
3. Update `AGENTS.md` permanently so that all future dated folders are created inside `records/` and never at the project root.
4. Establish standardized templates for work records (features/changes vs bugs).
5. Add `INDEX.md` to every dated folder, create `records/README.md`, `records/FEATURES.md`, and `records/BUGS.md`, and backfill missing records from historical work.

## 2. Why
Having multiple dated folders cluttering the root project directory reduces readability, complicates file discovery, and risks scattered notes. Centralizing records into `records/` with dedicated feature and bug registers provides a single source of truth for all historical and ongoing architectural work.

## 3. What was built (user-visible behaviour)
- Single root `records/` folder containing all past and current daily folders.
- Clean root directory without historical date-stamped folder clutter.
- Dedicated `INDEX.md` inside every dated folder.
- Searchable, structured `records/FEATURES.md` and `records/BUGS.md` registers.
- Permanent rules in `AGENTS.md` enforcing this structure for all future contributors and AI agents.

## 4. How to use it
- To view features: Consult `records/FEATURES.md`.
- To view bug fixes: Consult `records/BUGS.md`.
- To inspect daily work: Browse `records/YYYY-MM-DD/INDEX.md`.
- Adding new work: Always create records inside `records/YYYY-MM-DD/` using the standard template.

## 5. Settings and defaults
- Location: Strictly `records/YYYY-MM-DD/`.
- File format: Markdown only (`.md`).
- Changelog: `CHANGELOG.md` at root linking directly into `records/`.

## 6. Files changed
- `records/` (Created and populated with all dated folders)
- `AGENTS.md` (Updated permanent file placement rules, templates, and registers)
- `CHANGELOG.md` (Updated links to point into `records/`)
- `tests/test_layout_runner.py` (Updated docstring references)
- `tests/test_worker_null_guard.py` (Updated docstring references)
- `records/registers/README.md` (Created in `records/registers/`)
- `records/registers/FEATURES.md` (Created in `records/registers/`)
- `records/registers/BUGS.md` (Created in `records/registers/`)
- `records/registers/IDEAS.md` (Created in `records/registers/`)
- `records/YYYY-MM-DD/INDEX.md` (Created for each dated folder)
- `records/2026-11-03/change_records-folder-restructure.md` (This document)

## 7. Limitations and known issues
- None. All file paths and references have been updated and verified.

## 8. How it was verified
- Verified directory listing: 0 dated folders at project root, all 5 dated folders located in `records/`.
- Verified `CHANGELOG.md` link targets exist on disk.
- Verified test suites run cleanly (`test_session_tracker.py`, `test_cloud_engine.py`, `test_stage2_stage3_fixes.py`, `test_scrolling_and_shortcuts.py`).
- Verified secret scan reports 0 leaks.

## 9. How to roll it back
- Move subdirectories from `records/` back to repository root, and revert changes in `AGENTS.md` and `CHANGELOG.md`.

