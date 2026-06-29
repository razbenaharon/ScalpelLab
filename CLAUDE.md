# CLAUDE.md

Guidance for AI coding agents (Claude Code, Codex) working in this repository.
This file is a **router** — load it always, then pull in the relevant
`<dirname>.md` for the task at hand.

## 1. Project overview

ScalpelLab is a Windows-focused Python workspace for managing surgical video
recordings from an 8-camera operating room. Raw NorPix StreamPix `.seq` files
are organized on disk, catalogued in `ScalpelDatabase.sqlite`, optionally
converted to MP4, and surfaced through a NiceGUI desktop dashboard. Side
workflows: BORIS behavioral labeling, batch redaction, and multi-camera
synchronized playback.

The SQLite database at the repo root is the **single source of truth**. Every
other component is a producer or consumer of it.

## 2. Tech stack and environment

- **Language**: Python 3.10+ (uses `tuple[str, int] | None` syntax).
- **Env manager**: conda (see `.vscode/settings.json` —
  `python-envs.defaultEnvManager: ms-python.python:conda`).
- **Dashboard**: NiceGUI ≥ 2.0 + pywebview (native window). Charts via
  ECharts; legacy Plotly only on the `mp4-stats` page.
- **Data**: SQLite (stdlib `sqlite3`), pandas, numpy.
- **Video**: FFmpeg + ffprobe + mkvmerge (must be on PATH); NVIDIA NVENC for
  GPU encode; NorPix SequenceViewer for IDX creation; NorPix `CLExport` as
  fallback. `mpv.exe` for the multiviewer.
- **OS**: Windows 11. Console codepage is CP1252 — see §9 for the UTF-8
  stdout snippet required in any script that prints non-ASCII.

Install: `pip install -r requirements.txt`. Validate paths: `python config.py`.

## 3. Important paths and files

| Path                                | Role                                                     |
|-------------------------------------|----------------------------------------------------------|
| `ScalpelDatabase.sqlite`            | Source of truth. Tracked only through git-crypt.          |
| `config.py`                         | `DB_PATH`, `SEQ_ROOT`, `MP4_ROOT`, `NORPIX_SEQUENCE_VIEWER_PATH`, `DEFAULT_CAMERAS` |
| `run_app.py`                        | Launches NiceGUI dashboard via `python -m app.app`       |
| `app/`                              | NiceGUI dashboard — see [app/app.md](app/app.md)         |
| `scripts/1_…`, `2_…`, `3_…`         | SEQ→DB→MP4 pipeline — see [scripts/scripts.md](scripts/scripts.md) |
| `scripts/helpers/`                  | SEQ/IDX parsing, redaction, comparison — see [scripts/helpers/helpers.md](scripts/helpers/helpers.md) |
| `MPV_Multiviewer/`                  | Tkinter+libmpv viewer — see [MPV_Multiviewer/mpv_multiviewer.md](MPV_Multiviewer/mpv_multiviewer.md) |
| `docs/`                             | Schema/format refs, ERD, tracking JSONs — see [docs/docs.md](docs/docs.md) |

Routing — read the relevant context file before editing:

| Task involves…                                       | Read                                                  |
|------------------------------------------------------|-------------------------------------------------------|
| Dashboard pages, charts, theme                       | [app/app.md](app/app.md)                              |
| `1_…/2_…/3_…` pipeline or BORIS import               | [scripts/scripts.md](scripts/scripts.md)              |
| SEQ/IDX parsing, redaction, helpers                  | [scripts/helpers/helpers.md](scripts/helpers/helpers.md) |
| Multi-camera viewer, sync offsets                    | [MPV_Multiviewer/mpv_multiviewer.md](MPV_Multiviewer/mpv_multiviewer.md) |
| NorPix format spec, ERD, schema reference            | [docs/docs.md](docs/docs.md)                          |

## 4. Coding conventions

- **Edit, don't create.** Prefer modifying existing files. New helpers go
  under `scripts/helpers/`; do not create new top-level scripts unsolicited.
- **Paths**: use raw strings (`r"…"`) for any new Windows path literals.
- **Config**: import from `config.py` — never hardcode `F:\Room_8_Data\…`.
  The canonical NorPix SequenceViewer executable is configured as
  `NORPIX_SEQUENCE_VIEWER_PATH` and defaults to
  `C:\Program Files\Common Files\NorPix\SequenceViewer.exe`.
- **Docstrings**: Google style (template in `docs/DOCSTRING_GUIDE.md`).
  Module-level docstring is expected on every Python file.
- **Comments**: write only when the *why* is non-obvious. No restating *what*.
- **DB access from the dashboard**: always go through
  `app.charts.query_df` or `app.utils.connect`. Never open
  `sqlite3.connect()` directly inside a page.
- **Charts**: ECharts via `ui.echart(...)`. Use `chart_palette()` /
  `CHART_SEQ`; do not hardcode hex. Wrap axis text with
  `echart_axis_color()` so dark mode flips correctly.
- **No hand-rolled HTML** in NiceGUI pages — use Quasar components.
- **Windows console**: scripts that print non-ASCII must reconfigure stdout:

  ```python
  import io, sys
  if hasattr(sys.stdout, "buffer") and sys.stdout.encoding.lower() not in ("utf-8","utf8"):
      sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                                    errors="replace", line_buffering=True)
  ```

## 5. Database / schema rules

- **DB path resolution**: `config.DB_PATH` → overridable by `SCALPEL_DB`
  env var → overridable per-session via the dashboard's left drawer.
- **SequenceViewer path resolution**: `config.NORPIX_SEQUENCE_VIEWER_PATH` →
  overridable by `SCALPEL_NORPIX_SEQUENCE_VIEWER` → editable per-session and
  persistable from the NiceGUI Home page Configuration panel.
- **`PRAGMA foreign_keys = ON`** is set inside `app/utils.py::connect`.
  Preserve this — the schema relies on it.
- **Managed-columns contract** (`scripts/2_update_db.py`): only the
  documented columns are updated; everything else (user-added columns like
  `offset_seconds`, redaction flags) must be preserved. Implementation uses
  `INSERT … ON CONFLICT(pk) DO UPDATE SET <managed>=…`. Never replace this
  with `INSERT OR REPLACE` or full overwrites.
- **Views are read-only**: `cur_mp4_missing`, `cur_seniority`,
  `cur_mp4_status_statistics`, `cur_sync_status`. Don't write to them;
  rebuild via the producer script.
- **JUNK rows**: any `camera_name` ending in `_JUNK` / `_Junk` is a failed or
  undersized recording. Filter out for stats and visualizations.
- **Drift outliers**: `seq_enriched.time_drift_ms` has corrupt values (~2e12
  ms) from broken IDX timestamps. Always clip or bucket large `|drift|` before
  plotting (see the `DRIFT_SMALL_MS` / `DRIFT_MEDIUM_MS` bucketing in
  `app/pages/seq.py`).
- **`n_counter_resets > 0`** is normal ring-buffer behavior, not an overrun
  signal on its own.
- **Schema changes**: applied directly with `sqlite3` against the live DB
  (no migration framework). Back up first. After any change, re-run
  `scripts/helpers/sqlite_to_dbdiagram.py` to refresh
  `docs/scalpel_dbdiagram.txt` and update
  `docs/project_context/scalpel_database_sqlite_context.md`.

## 6. Security and privacy rules

This repo handles **medical/surgical recordings**. Treat all video and DB
content as sensitive PHI-equivalent data.

- **`ScalpelDatabase.sqlite` IS committed**, but only because it is
  encrypted in-tree by **git-crypt** (`.gitattributes` maps `*.sqlite` to
  `filter=git-crypt diff=git-crypt`). Before staging the DB, always run
  `git crypt status ScalpelDatabase.sqlite` — it must report `encrypted:`.
  If it reports `not encrypted`, **stop**: the working clone is missing
  the git-crypt key (`git-crypt unlock` first) and a raw push would leak
  the DB. Treat any other `*.sqlite` (e.g. backups like
  `ScalpelDatabase.sqlite.bak_*`) the same way.
- **Never commit**: any `.seq` / `.mp4` / `.idx` / `.aud` files,
  `docs/*_tracking.json` (may reference patient cases), or anything under
  `MPV_Multiviewer/` runtime config containing case paths. The
  `.gitignore` does not currently block all of these — be defensive when
  staging.
- **Never paste** patient names, case details, or video frames into web
  tools (pastebins, diagram renderers, public LLMs).
- **Redaction workflow** (`scripts/helpers/batch_black_squere.py`) reads
  timing ranges from `mp4_times`. If you change its output paths, ensure
  the redacted MP4 is the only artifact that leaves the secure volume.
- **No telemetry / analytics** dependencies. If a new package wants to
  phone home, it does not belong here.
- **Secrets**: there are none in this repo today. If you add an integration
  that needs credentials, load from environment variables; never hardcode.

## 7. Git and commit guidelines

- **Don't commit unless asked.** When asked, follow the convention shown by
  recent history (`git log --oneline -n 10`): short imperative subject,
  occasional one-line body. Examples:
  `Allow git-crypt unlock in Claude permissions`,
  `Replace ERD home with dashboard; add ERD zoom dialog`.
- **One logical change per commit.** Don't bundle unrelated edits.
- **Don't push, force-push, or rewrite history** without explicit user
  request. Pushing to `main` is allowed when the user explicitly asks
  ("commit and push", "push to main", etc.); never force-push or rewrite
  published history without explicit confirmation.
- **Never** use `--no-verify`, `--no-gpg-sign`, or otherwise skip hooks.
- **Stage explicitly** (`git add <file>`) — avoid `git add -A` so videos,
  tracking JSONs, and unencrypted DB backups don't slip in. The main
  `ScalpelDatabase.sqlite` is fine to stage (git-crypt encrypts it on
  commit), but verify with `git crypt status` first.
- **Branch hygiene**: work on a feature branch when the change is
  non-trivial. The repo's main branch is `main`.

## 8. Testing and validation

There is no CI or linter configured, but there are pytest suites under
[`tests/`](tests/). Install dependencies first with `pip install -r
requirements.txt`; dashboard smoke tests require `nicegui`.

- **Path config**: `python config.py` — confirms DB / SEQ_ROOT / MP4_ROOT
  exist.
- **Pipeline scripts**: every numbered script supports `--dry-run`. Run it
  before any write. For `2_update_db.py`, also try `--skip-duration` first
  for a fast scan.
- **Fast tests**: `pytest -q tests/test_progress_parser.py
  tests/test_pages_smoke.py tests/test_layout_nav.py tests/test_config_paths.py`.
- **E2E tests**: see [`tests/README.md`](tests/README.md); they require a
  scratch sample directory and may require FFmpeg, mkvmerge, and NVENC.
- **DB sanity**: `sqlite3 ScalpelDatabase.sqlite` and inspect counts on the
  affected tables / views before and after a script run.
- **Dashboard changes**: `python run_app.py` and click through the page —
  the user wants verification that real data renders, not just that the
  type checker is happy.
- **Migrations**: copy the DB to a backup first, then apply, then diff row
  counts against the backup.

## 9. Agent behavior guidelines

- **Use the router.** Don't preemptively read every `<dirname>.md` —
  load only the ones the current task touches. When a task spans
  directories, load the relevant context files in parallel.
- **Respect contracts.** The "managed columns" contract in
  `scripts/scripts.md`, the page contract in `app/app.md`, and the import
  shim in `2_update_db.py` all encode prior bugs. Don't refactor them away.
- **Trust code over stale docs.** The dashboard is NiceGUI. If you find an old
  Streamlit reference, treat it as legacy context and verify against the code.
- **Ask before destructive ops.** Confirm before: deleting / overwriting
  files, applying schema changes against `ScalpelDatabase.sqlite`, running
  scripts without `--dry-run`, force-pushing, or `rm`-ing tracking JSON
  checkpoint files.
- **Don't introduce new top-level files** (scripts, modules, docs) unless
  the user asks. Keep new helpers under `scripts/helpers/`; keep new
  dashboard pages under `app/pages/` and register them per the page
  contract.
- **Don't add features beyond what's asked.** No speculative abstractions,
  no unrequested error handling, no "while I'm here" cleanups.
- **Don't add new dependencies** without flagging it. The
  `requirements.txt` is already heavy with optional ML packages; small
  changes shouldn't drag in new ones.
