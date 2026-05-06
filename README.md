# ScalpelLab

ScalpelLab is a Windows-focused Python workspace for managing surgical video
recordings, tracking SEQ and MP4 assets in SQLite, running privacy redaction and
review workflows, and exploring computer-vision pipelines for room-camera data.

## What Is In This Repo

- A Streamlit dashboard for browsing, editing, and summarizing the SQLite
  database.
- File-system pipelines for SEQ ingestion, MP4 status updates, SEQ metadata
  enrichment, and SEQ-to-MP4 conversion.
- BORIS behavioral-tag import tooling.
- Batch redaction tooling driven from database timing tables.
- A multi-video MPV review tool for synchronized case playback.
- Helper utilities for database comparison, backup validation, video cutting,
  SEQ IDX repair, and schema export.
- Computer-vision research code under `CV/`, including YOLO and SimCLR/ReID
  experiments.

## Quick Start

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure local paths

Edit [`config.py`](config.py) and set:

- `SEQ_ROOT` to your organized SEQ root
- `MP4_ROOT` to your MP4 recordings root

Expected layout:

```text
Sequence_Backup/                    Recordings/
└── DATA_YY-MM-DD/                  └── DATA_YY-MM-DD/
    └── CaseN/                          └── CaseN/
        └── CameraName/                     └── CameraName/
            └── *.seq                           └── *.mp4
```

### 3. Validate configuration

```bash
python config.py
```

### 4. Launch the desktop app

```bash
python run_app.py
```

`run_app.py` starts the NiceGUI app via `python -m app.app`, which opens a
native pywebview window.

## Main Components

### NiceGUI App

The dashboard lives under [`app/`](app/) and currently includes:

- [`app/app.py`](app/app.py): entry point, landing page, DB selector, and ERD
  preview from `docs/ERD.pdf`.
- [`app/pages/database.py`](app/pages/database.py): browse tables, inspect
  schema, insert rows, delete rows.
- [`app/pages/status_summary.py`](app/pages/status_summary.py): per-camera
  MP4 / SEQ presence summaries.
- [`app/pages/views.py`](app/pages/views.py): browse database views.
- [`app/pages/mp4_statistics.py`](app/pages/mp4_statistics.py): interactive
  analytics for `cur_mp4_status_statistics`.

### Database And Migrations

- [`ScalpelDatabase.sqlite`](ScalpelDatabase.sqlite) is the local working
  database expected by default in the project root.
- [`migrations/`](migrations/) contains SQLite migration scripts. Back up the
  database before running migrations.
- [`docs/scalpel_dbdiagram.txt`](docs/scalpel_dbdiagram.txt) is a dbdiagram.io
  schema export.
- [`docs/project_context/`](docs/project_context/) contains deeper notes on the
  SQLite schema and NorPix SEQ / IDX formats.

### Main Scripts

- [`scripts/1_nuk_seq_export.py`](scripts/1_nuk_seq_export.py): organize raw
  SEQ exports into `DATA_YY-MM-DD/CaseN/CameraName`, copy companion files,
  verify hashes, and flag undersized files as junk.
- [`scripts/2_update_db.py`](scripts/2_update_db.py): scan SEQ and MP4 trees,
  update `seq_status` and `mp4_status`, optionally calculate durations with
  `ffprobe`, enrich SEQ metadata, and preserve unmanaged DB columns.
- [`scripts/3_seq_to_mp4_convert.py`](scripts/3_seq_to_mp4_convert.py): convert
  missing SEQ recordings to MP4, with GPU-first workflow and fallback behavior.
- [`scripts/import_boris_tags.py`](scripts/import_boris_tags.py): import BORIS
  TSV exports into `boris_events` and maintain BORIS-derived views.
- [`scripts/helpers/batch_black_squere.py`](scripts/helpers/batch_black_squere.py):
  batch-redact videos from database timing data in `mp4_times`.

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

### Computer Vision

Computer-vision experiments live under [`CV/`](CV/):

- `CV/yolo/`: pose, tracking, calibration, and overlay scripts.
- `CV/SimCLR_reid/`: SimCLR/ReID dataset, training, validation, and inspection
  scripts.

## Common Commands

```bash
python config.py
python run_app.py
python scripts/1_nuk_seq_export.py
python scripts/2_update_db.py --dry-run
python scripts/2_update_db.py --skip-duration
python scripts/3_seq_to_mp4_convert.py
python scripts/import_boris_tags.py --dry-run
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
- `boris_events`: imported BORIS behavioral event rows.
- `seq_status`: SEQ presence, size, and path.
- `seq_enriched`: parsed SEQ header and IDX metadata cache.
- `mp4_status`: MP4 presence, size, duration, redaction metadata, sync offset,
  and path.
- `mp4_times`: case timing ranges used by redaction workflows.

### Common Views

- `cur_mp4_missing`: cases where SEQ exists but MP4 is missing.
- `cur_seq_missing`: cases where MP4 exists but SEQ is missing.
- `cur_seniority`: anesthesiology experience / status summary.
- `cur_mp4_status_statistics`: aggregated recording statistics used by the MP4
  dashboard.
- `cur_boris_intervals`: START / STOP BORIS event intervals derived from
  imported tags.

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
├── CV/
│   ├── SimCLR_reid/
│   └── yolo/
├── docs/
│   ├── project_context/
│   ├── ERD.pdf
│   ├── mp4_statistics.pdf
│   ├── scalpel_dbdiagram.txt
│   ├── redaction_tracking.json
│   └── seq_idx_repair_tracking.json
├── migrations/
│   └── 001_fk_renames_cleanup.sql
├── MPV_Multiviewer/
│   ├── docs/
│   ├── lib/
│   ├── config.ini
│   └── run_viewer.py
├── scripts/
│   ├── 1_nuk_seq_export.py
│   ├── 2_update_db.py
│   ├── 3_seq_to_mp4_convert.py
│   ├── import_boris_tags.py
│   └── helpers/
├── config.py
├── run_app.py
├── requirements.txt
└── ScalpelDatabase.sqlite
```

## External Tools

Some functionality depends on tools outside Python:

- `ffmpeg` and `ffprobe` for conversion, probing, redaction, and cutting.
- NVIDIA NVENC for GPU-accelerated video workflows where available.
- CLExport as a fallback SEQ export path in some workflows.
- `mpv.exe` for synchronized multi-video playback in `MPV_Multiviewer`.

## Notes

- The repo is Windows-oriented; paths and examples assume Windows drive letters.
- The database file defaults to `ScalpelDatabase.sqlite` in the project root.
- `scripts/2_update_db.py` is designed to preserve columns it does not manage.
- The Streamlit app can point at a different database path through the sidebar or
  the `SCALPEL_DB` environment variable.
