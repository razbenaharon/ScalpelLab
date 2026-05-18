# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## How to use this file

This is a **router**. It contains only the minimum needed to orient yourself
and decide which per-directory context file to load next. Each major directory
has its own `<dirname>.md` with the deep details. Read those on demand based
on the user's task — do not preemptively load them all.

| Task involves…                                              | Read this file                                |
|-------------------------------------------------------------|-----------------------------------------------|
| The NiceGUI dashboard / pages / charts / theme              | [app/app.md](app/app.md)                      |
| The `1_… 2_… 3_…` SEQ/MP4 pipeline scripts or BORIS import  | [scripts/scripts.md](scripts/scripts.md)      |
| Helpers (SEQ field analysis, IDX repair, redaction, compare)| [scripts/helpers/helpers.md](scripts/helpers/helpers.md) |
| The Tkinter+MPV multi-camera viewer                         | [MPV_Multiviewer/mpv_multiviewer.md](MPV_Multiviewer/mpv_multiviewer.md) |
| YOLO pose / SimCLR ReID experiments                         | [CV/cv.md](CV/cv.md)                          |
| SQLite schema migrations                                    | [migrations/migrations.md](migrations/migrations.md) |
| Schema references, NorPix format docs, ERD                  | [docs/docs.md](docs/docs.md)                  |

When the user's request spans multiple areas, load the relevant context files
in parallel before editing.

## Repository purpose (one-paragraph orientation)

Windows-focused Python workspace for managing surgical video recordings from an
8-camera operating room. Raw NorPix StreamPix `.seq` files are organized,
catalogued in `ScalpelDatabase.sqlite`, optionally converted to MP4, and served
through a NiceGUI desktop dashboard. Side workflows include BORIS behavioral
labeling, batch redaction, multi-camera synchronized playback, and CV
experiments. The SQLite database at the repo root is the single source of
truth — every other component is a producer or consumer of it.

## Top-level commands

```bash
python config.py                                 # validate paths
python run_app.py                                # launch NiceGUI dashboard
python scripts/1_seq_curation.py                 # curate raw SEQ into SEQ_ROOT
python scripts/2_update_db.py --dry-run          # refresh DB
python scripts/3_seq_to_mp4_convert.py           # SEQ → MP4 (NVENC)
python MPV_Multiviewer/run_viewer.py             # multi-camera viewer
```

There are no automated tests, linters, or CI. Validate by running scripts with
`--dry-run` and inspecting the database.

## Cross-cutting facts (always relevant)

- **DB path** comes from `config.py::DB_PATH`, overridable per-process by the
  `SCALPEL_DB` env var, or per-session via the dashboard's left drawer.
- **External tools on PATH**: `ffmpeg`, `ffprobe`, `mkvmerge`, and `mpv.exe`.
  GPU paths assume NVIDIA NVENC; some workflows fall back to NorPix `CLExport`.
- **Windows console** is CP1252. Scripts that print non-ASCII must reconfigure
  stdout to UTF-8 early (snippet in the auto-memory `MEMORY.md`). NiceGUI
  itself doesn't need this.
- **JUNK rows**: any `camera_name` ending in `_JUNK` / `_Junk` is a failed or
  undersized recording — filter out for stats and visualizations.
- **`seq_enriched.time_drift_ms`** has corrupt outliers (~2e12 ms) from broken
  IDX timestamps — always clip to ±5–10 s before plotting.
- The README still says "Streamlit" in places; the dashboard has been migrated
  to **NiceGUI** (native pywebview window). Trust the code over the README.

## Conventions when editing

- Prefer editing existing files; don't create new top-level scripts unless
  asked. New helpers go under `scripts/helpers/`.
- When a directory's `<dirname>.md` documents a contract (e.g. the page
  contract in `app/app.md`, the managed-columns contract in
  `scripts/scripts.md`), follow it exactly — these contracts encode prior
  bugs.
- Use raw strings (`r"…"`) for any new Windows path constants in `config.py`.
- Do not open `sqlite3.connect()` directly inside dashboard pages — go through
  `app.charts.query_df` or `app.utils.connect`.
