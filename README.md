# ScalpelLab

ScalpelLab is a Windows-focused Python workspace for managing surgical video
recordings, tracking SEQ and MP4 assets in SQLite, and running privacy redaction
and review workflows.

## What Is In This Repo

- A NiceGUI desktop dashboard for browsing, editing, and summarizing the SQLite
  database.
- File-system pipelines for SEQ ingestion, MP4 status updates, SEQ metadata
  enrichment, and SEQ-to-MP4 conversion.
- BORIS behavioral-tag import tooling.
- Batch redaction tooling driven from database timing tables.
- A multi-video MPV review tool for synchronized case playback.
- Helper utilities for database comparison, backup validation, video cutting,
  SEQ IDX repair, and schema export.
- Remapping of BORIS event timing from the OLD MP4 timeline onto the
  converted NEW MP4 timeline (`scripts/helpers/boris_remap_to_new_mp4.py`).

## Quick Start

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure local paths

Edit [`config.py`](config.py) and set:

- `SEQ_ROOT` to your organized SEQ root
- `MP4_ROOT` to your MP4 recordings root
- `ANALYSES_ROOT` to your finalized per-case analysis root
  (`Analyses\Case_Analyses_synced`)
- `NORPIX_SEQUENCE_VIEWER_PATH` to the NorPix SequenceViewer executable used
  to create `.seq.idx` files

Expected layout:

```text
Sequence_Backup/                    Recordings/
└── DATA_YY-MM-DD/                  └── DATA_YY-MM-DD/
    └── CaseN/                          └── CaseN/
        └── CameraName/                     └── CameraName/
            ├── *.seq                           └── *.mp4
            └── *.seq.idx
```

`Analyses/Case_Analyses_synced/` holds the finalized, per-case analysis
artifacts (behavioral labels and monitor vitals) whose BORIS events have been
remapped onto the NEW MP4 timeline. Older, un-remapped analysis exports live
under `Analyses/un_synced/`.

```text
Analyses/
├── Case_Analyses_synced/
│   └── DATA_YY-MM-DD/
│       └── CaseN/
│           ├── Boris/
│           │   ├── <date>-caseN_raw.csv           # raw BORIS export (OLD MP4 timeline)
│           │   ├── <date>-caseN_standardized.csv  # cleaned/standardized events
│           │   └── <date>-caseN_*_new_mp4.csv     # events remapped to the NEW MP4 timeline
│           └── Monitor/
│               └── motior_data.csv                # per-case monitor vitals time series
└── un_synced/                                     # older analysis exports (not remapped)
    ├── Analyses_1/
    ├── Analyses_2/
    └── Analyses_2026-03-18/
```

File types:

- `*_raw.csv` — direct BORIS observation export; `Time`/`Image index` are on the
  OLD per-camera MP4 timeline (one frame per SEQ frame).
- `*_standardized.csv` — the same events after cleanup/normalization, used for
  downstream analysis.
- `*_new_mp4.csv` — produced by `scripts/helpers/boris_remap_to_new_mp4.py`;
  adds NEW-MP4 frame/time columns via `docs/new recordings formula.md`.
- `motior_data.csv` — exported patient-monitor vital signs for the case
  (imported by `scripts/helpers/import_analysis_finale.py`).

### 3. Validate configuration

```bash
python config.py
```

### 4. Launch the desktop app

```bash
python run_app.py
```

`run_app.py` starts the NiceGUI app via `python -m app.app`, which opens a
native pywebview window. The Home page Configuration panel can also edit and
persist the DB, SEQ root, MP4 root, and NorPix SequenceViewer paths.

## Main Components

### NiceGUI App

The dashboard lives under [`app/`](app/) and currently includes:

- [`app/app.py`](app/app.py): entry point, Home dashboard, DB selector, and
  the Configuration panel for editing/persisting paths.
- [`app/pages/database.py`](app/pages/database.py): browse tables, inspect
  schema, insert rows, delete rows, and view the ERD from `docs/ERD.pdf`.
- [`app/pages/anesthesiology.py`](app/pages/anesthesiology.py): roster and
  seniority view over `cur_seniority` + `recording_details`.
- [`app/pages/mp4.py`](app/pages/mp4.py): MP4 coverage and statistics over
  `mp4_status` and the `cur_mp4_*` views.
- [`app/pages/seq.py`](app/pages/seq.py): SEQ inventory plus `seq_enriched`
  time-drift analysis.
- [`app/pages/boris.py`](app/pages/boris.py): BORIS event START/STOP pairing
  and interval analysis.
- Launcher pages for the processing pipeline:
  [`nuk_export.py`](app/pages/nuk_export.py) (SEQ Curation),
  [`update_db.py`](app/pages/update_db.py) (Update DB + IDX), and
  [`seq_to_mp4.py`](app/pages/seq_to_mp4.py) (SEQ → MP4).

The sidebar is organized into **Dashboards & Monitoring** for read/inspection
pages and **Processing Pipeline** for operational actions.

### Database And Schema

- [`ScalpelDatabase.sqlite`](ScalpelDatabase.sqlite) is the local working
  database expected by default in the project root.
- There is no migration framework. Schema changes are applied directly with
  `sqlite3` against the live database — back up first, then re-export the
  schema with `scripts/helpers/sqlite_to_dbdiagram.py`.
- [`docs/scalpel_dbdiagram.txt`](docs/scalpel_dbdiagram.txt) is a dbdiagram.io
  schema export.
- [`docs/project_context/`](docs/project_context/) contains deeper notes on the
  SQLite schema and NorPix SEQ / IDX formats.

### Main Scripts

- [`scripts/1_seq_curation.py`](scripts/1_seq_curation.py): curate raw
  SEQ exports into `DATA_YY-MM-DD/CaseN/CameraName`, copy companion files,
  verify hashes, and flag undersized files as junk.
- [`scripts/2_update_db.py`](scripts/2_update_db.py): scan SEQ and MP4 trees,
  update `seq_status` and `mp4_status`, optionally calculate durations with
  `ffprobe`, enrich SEQ metadata, and preserve unmanaged DB columns.
- [`scripts/3_seq_to_mp4_convert.py`](scripts/3_seq_to_mp4_convert.py): convert
  missing SEQ recordings to MP4, with GPU-first workflow and fallback behavior.
- [`scripts/helpers/import_boris_tags.py`](scripts/helpers/import_boris_tags.py): import BORIS
  TSV exports into `boris_events` and maintain BORIS-derived views.
- [`scripts/helpers/batch_black_squere.py`](scripts/helpers/batch_black_squere.py):
  batch-redact videos from database timing data in `mp4_times`.

Processing Pipeline order:

1. SEQ Curation.
2. Update DB + Create IDX files.
3. Analyze SEQ Fields.
4. SEQ to MP4, followed by a DB refresh so MP4 status reflects the latest files.

IDX creation uses NorPix SequenceViewer, configured by
`config.NORPIX_SEQUENCE_VIEWER_PATH` and editable from the Home page. The
existing `repair_seq_idx.py` helper remains an audit/repair path, not the
primary creation flow.

Pipeline logs are written under `logs/`, grouped by step:

```text
logs/
├── seq_curation/
├── db_update/
├── idx_creation/
├── seq_analysis/
└── seq_to_mp4/
```

### Helper Utilities

- [`scripts/helpers/analyze_seq_fields.py`](scripts/helpers/analyze_seq_fields.py):
  optional SEQ field inspection used by the DB updater.
- [`scripts/helpers/repair_seq_idx.py`](scripts/helpers/repair_seq_idx.py):
  audit and rebuild NorPix `.seq.idx` files from SEQ bodies, with checkpointing
  in `docs/seq_idx_repair_tracking.json`.
- [`scripts/helpers/backup_dir.py`](scripts/helpers/backup_dir.py): copy files
  while preserving source structure.
- [`scripts/helpers/cut_video.py`](scripts/helpers/cut_video.py): cut video
  segments with FFmpeg stream copy.
- [`scripts/helpers/boris_remap_to_new_mp4.py`](scripts/helpers/boris_remap_to_new_mp4.py):
  remap BORIS event frame/time from the OLD MP4 to the NEW MP4 timeline, writing
  `*_new_mp4.csv` next to each source CSV (single `--csv` or `--all`).
- [`scripts/helpers/sqlite_to_dbdiagram.py`](scripts/helpers/sqlite_to_dbdiagram.py):
  export the SQLite schema to dbdiagram.io format.
- [`scripts/helpers/compare/compare_databases.py`](scripts/helpers/compare/compare_databases.py):
  compare two SQLite databases.
- [`scripts/helpers/compare/compare_mp4.py`](scripts/helpers/compare/compare_mp4.py):
  compare MP4 backups.
- [`scripts/helpers/compare/compare_seq.py`](scripts/helpers/compare/compare_seq.py):
  compare SEQ backups.

### MPV Multiviewer

[`MPV_Multiviewer/`](MPV_Multiviewer/) contains a Tkinter + MPV tool for loading
multiple camera angles, synchronizing playback offsets, and saving sync
corrections back to the database.

```bash
python MPV_Multiviewer/run_viewer.py
```

See [`MPV_Multiviewer/docs/user-guide.md`](MPV_Multiviewer/docs/user-guide.md)
for usage notes.

## Common Commands

```bash
python config.py
python run_app.py
python scripts/1_seq_curation.py
python scripts/2_update_db.py --dry-run
python scripts/2_update_db.py --skip-duration
python scripts/3_seq_to_mp4_convert.py
python scripts/helpers/import_boris_tags.py --dry-run
python scripts/helpers/batch_black_squere.py
python scripts/helpers/repair_seq_idx.py --dry-run
python scripts/helpers/cut_video.py
python scripts/helpers/sqlite_to_dbdiagram.py
python MPV_Multiviewer/run_viewer.py
```

## Database Overview

### Core Tables

- `recording_details`: case-level recording metadata.
- `analysis_information`: labeling metadata and optional BORIS event linkage.
- `anesthesiology`: anesthesiology roster and career dates.
- `seq_status`: SEQ presence, size, and path.
- `seq_enriched`: parsed SEQ header and IDX metadata cache.
- `mp4_status`: MP4 presence, size, duration, redaction metadata, sync offset,
  and path.
- `mp4_times`: case timing ranges used by redaction workflows.

Importer-created optional tables:

- `boris_events`: imported BORIS behavioral event rows.
- `monitor_samples`: imported per-sample monitor vitals.
- `monitor_case_summary`: per-case monitor import summary.

### Common Views

- `cur_mp4_missing`: cases where SEQ exists but MP4 is missing.
- `cur_sync_status`: per-camera `is_syncable` flag derived from `seq_enriched`,
  encoding the `3_seq_to_mp4_convert.py` planning gates (IDX present, header
  ok, valid frame timestamps, sane duration, ≥ 50 MB SEQ).
- `cur_seniority`: anesthesiology experience / status summary.
- `cur_mp4_status_statistics`: aggregated recording statistics used by the MP4
  dashboard.

### Camera Set

Default camera names from [`config.py`](config.py):

- `Cart_Center_2`
- `Cart_LT_4`
- `Cart_RT_1`
- `General_3`
- `Monitor`
- `Patient_Monitor`
- `Ventilator_Monitor`
- `Injection_Port`

## Project Layout

```text
ScalpelLab/
├── app/
│   ├── app.py
│   ├── utils.py
│   └── pages/
├── docs/
│   ├── project_context/
│   ├── ERD.pdf
│   ├── mp4_statistics.pdf
│   ├── scalpel_dbdiagram.txt
│   ├── redaction_tracking.json
│   └── seq_idx_repair_tracking.json
├── MPV_Multiviewer/
│   ├── docs/
│   ├── lib/
│   ├── config.ini
│   └── run_viewer.py
├── scripts/
│   ├── 1_seq_curation.py
│   ├── 2_update_db.py
│   ├── 3_seq_to_mp4_convert.py
│   └── helpers/
│       ├── import_boris_tags.py
│       └── import_analysis_finale.py
├── config.py
├── run_app.py
├── requirements.txt
└── ScalpelDatabase.sqlite
```

## External Tools

Some functionality depends on tools outside Python:

- `ffmpeg` and `ffprobe` for conversion, probing, redaction, and cutting.
- NVIDIA NVENC for GPU-accelerated video workflows where available.
- NorPix SequenceViewer at `C:\Program Files\Common Files\NorPix\SequenceViewer.exe`
  by default for opening `.seq` files and creating `.seq.idx` companions.
- CLExport as a fallback SEQ export path in some legacy workflows.
- `mpv.exe` for synchronized multi-video playback in `MPV_Multiviewer`.

## Notes

- The repo is Windows-oriented; paths and examples assume Windows drive letters.
- The database file defaults to `ScalpelDatabase.sqlite` in the project root.
- `scripts/2_update_db.py` is designed to preserve columns it does not manage.
- The NiceGUI app can point at different configured paths through the Home page
  Configuration panel or the relevant `SCALPEL_*` environment variables.
