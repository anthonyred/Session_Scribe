# Session Scribe — Agent & Contributor Rules

These rules apply to every file created in this repository, by any contributor or AI agent.

---

## 1. Where files belong

| Type | Location |
|------|----------|
| App source code | `gui/` or `scribe_core/` (existing packages) |
| Tests | `tests/` |
| Helper / maintenance scripts | `scripts/` |
| Temporary or scratch files | `scratch/` — listed in `.gitignore`, never committed |
| Work-record documents | `records/YYYY-MM-DD/` dated folders — **markdown only** |
| Registers & indices | `records/registers/` — **markdown only** |

**Everything under `records/` lives inside a folder: either a dated folder (`records/YYYY-MM-DD/`) or `records/registers/`.**  
No loose files are allowed directly inside `records/`.  
**Every new dated folder `YYYY-MM-DD` is created inside `records/` and nowhere else.**  
Never create a dated folder at the project root.  
**Dated folders hold markdown only.**  
Never write `.py`, `.json`, `.log`, `.txt`, `.bat`, or any other non-markdown file into a dated folder.

---

## 2. Naming conventions & Work Records

Every change to the app needs a record in that day’s folder before the work counts as finished: `bug_...`, `feature_...`, `change_...` or `build_...`, named:

```
<type>_<short-title>.md
```

Where `<type>` is one of:

| Type | Use for |
|------|---------|
| `build` | New feature or component built from scratch |
| `bug` | Bug investigation and fix |
| `feature` | Enhancement to existing functionality |
| `change` | Refactor, housekeeping, or non-bug change |

Examples: `bug_stage3-manifest-refresh.md`, `build_environment-setup.md`, `change_housekeeping-file-layout.md`

### Record Templates:

#### Feature and Change Records:
- **Date**
- **What was asked**
- **Why**
- **What was built (user-visible behaviour)**
- **How to use it**
- **Settings and defaults**
- **Files changed**
- **Limitations and known issues**
- **How it was verified**
- **How to roll it back**

#### Bug Records:
- **Date**
- **What was asked / symptom**
- **Root cause**
- **What was changed / fix**
- **Files touched**
- **How it was verified**

#### Daily Index & Registers:
- Each dated folder gets an `INDEX.md` listing that day's records with one line each.
- `records/registers/README.md`: Explains how records work and provides an index of all dated folders.
- `records/registers/FEATURES.md`: A register table containing: Feature Name | Date Added | Status | Where Used in App | Link to Record.
- `records/registers/BUGS.md`: A register table containing: Date | Symptom | Root Cause | Fix | Link to Record.
- `records/registers/IDEAS.md`: Parked ideas and decoupled tooling proposals.

---

## 3. CHANGELOG

Keep `CHANGELOG.md` in the project root.  
One line per entry, most recent first:

```
YYYY-MM-DD | <type> | <short description> | <link to dated record in records/>
```

---

## 4. Tests

- Tests live in `tests/`.
- Tests **never** touch real event folders (e.g. `D:\Events\...`).  
  They use `tempfile.mkdtemp()` or equivalent for all I/O.
- Each test file imports project code via a `sys.path` guard derived from `__file__`,  
  so tests run correctly from any working directory.

---

## 5. Before creating any file — decision checklist

1. Is it app logic? → `gui/` or `scribe_core/`
2. Is it a test? → `tests/`
3. Is it a one-off helper script? → `scripts/`
4. Is it temporary scratch / throwaway? → `scratch/`
5. Is it a work record? → `records/YYYY-MM-DD/<type>_<short-title>.md`

If it doesn't fit any of the above, ask before creating it.

---

## 6. Commits

Do not commit unless explicitly asked.

---

## 7. Project assumptions

- Typical clips are 1 to 9 minutes and none is near an hour; design for this and support longer clips via chunking.
- The local engine is `openai-whisper` medium (not WhisperX).
- Per-event state lives in `<event root>\scribe\state\`.
- No database or server dependency is used (no MongoDB, SQLite server, or other external DB).
- Voice library location and rules: `D:\Events\_voice_library\`, with a "permission noted" tick and date per person. Must remain local only, never committed or uploaded, and fully deletable.
- User data directories: `D:\Events\_voice_library\` and `D:\Events\_usage\` are user data, never go into git, and "Scan Events" ignores any folders starting with an underscore (`_`).
- Roles are per session (`Host`, `Speaker`, `Panelist`, `Moderator`, `Guest`): one person can have different roles across different sessions of the same event.
- UI direction: Qt (PySide6) top tab navigation (Stage 2, Stage 3, Stage 4 (Soon), Stage 5 (Soon)) with adaptive column tables, dark mode support, and integrated Stage 3 CSV usage logging.

---

## 8. User Documentation & README Maintenance

- Any change that alters how the app is used updates `README.md` in the project root in the same piece of work.
- The "Last verified" date in `README.md` is refreshed whenever `README.md` is updated.
- Screenshots referenced in user documentation live under `docs/images/` and must be created using dummy data only (never real events, personal identities, or secrets).

