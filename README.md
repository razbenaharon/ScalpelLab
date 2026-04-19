# ScalpelLab

ScalpelLab is a Windows-focused Python workspace for managing surgical video recordings, tracking SEQ and MP4 assets in SQLite, running privacy redaction workflows, and reviewing recording coverage through a Streamlit dashboard.

## What Is In This Repo

- A Streamlit app for browsing and editing the SQLite database.
- File-system pipelines for SEQ ingestion, MP4 status updates, and SEQ-to-MP4 conversion.
- Batch redaction tooling driven from database timing tables.
- Helper utilities for database comparison, backup validation, video cutting, and schema export.
- Research / model directories such as `yolo/` and `SimCLR_reid/`.

## Quick Start

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure local paths

Edit [`config.py`](/F:/Projects/ScalpelLab/config.py) and set:

- `SEQ_ROOT` to your organized SEQ root
- `MP4_ROOT` to your MP4 recordings root

Expected layout:

```text
Sequence_Backup/                    Recordings/
└── DATA_YY-MM-DD/                 └── DATA_YY-MM-DD/
    └── CaseN/                         └── CaseN/
        └── CameraName/                    └── CameraName/
            └── *.seq                          └── *.mp4
```

### 3. Validate configuration

```bash
python config.py
```

### 4. Launch the Streamlit app

```bash
python run_app.py
```

The app starts with `streamlit run app/app.py`.

## Main Components

### Streamlit App

The dashboard lives under [`app/`](/F:/Projects/ScalpelLab/app) and currently includes:

- `app.py`: landing page, DB selector, and ERD preview from `docs/ERD.pdf`
- `pages/1_Database.py`: browse tables, inspect schema, insert rows, delete rows
- `pages/2_Status_Summary.py`: per-camera MP4 / SEQ presence summaries
- `pages/3_Views.py`: browse database views and export results
- `pages/4_MP4_Statistics.py`: interactive analytics for `cur_mp4_status_statistics`

### Main Scripts

- [`scripts/1_nuk_seq_export.py`](/F:/Projects/ScalpelLab/scripts/1_nuk_seq_export.py): organize raw SEQ exports into `DATA_YY-MM-DD/CaseN/CameraName`, copy companion files, verify hashes, and flag undersized files as junk.
- [`scripts/2_update_db.py`](/F:/Projects/ScalpelLab/scripts/2_update_db.py): scan SEQ and MP4 trees, update `seq_status` and `mp4_status`, optionally calculate durations with `ffprobe`, and preserve unmanaged DB columns.
- [`scripts/3_seq_to_mp4_convert.py`](/F:/Projects/ScalpelLab/scripts/3_seq_to_mp4_convert.py): convert missing SEQ recordings to MP4, with GPU-first workflow and fallback behavior.
- [`scripts/5_batch_blacken.py`](/F:/Projects/ScalpelLab/scripts/5_batch_blacken.py): batch-redact videos from database timing data in `mp4_times`.

### Helper Utilities

- [`scripts/helpers/analyze_seq_fields.py`](/F:/Projects/ScalpelLab/scripts/helpers/analyze_seq_fields.py): optional SEQ field inspection used by the DB updater.
- [`scripts/helpers/backup_dir.py`](/F:/Projects/ScalpelLab/scripts/helpers/backup_dir.py): copy files while preserving source structure.
- [`scripts/helpers/cut_video.py`](/F:/Projects/ScalpelLab/scripts/helpers/cut_video.py): cut video segments with FFmpeg stream copy.
- [`scripts/helpers/sqlite_to_dbdiagram.py`](/F:/Projects/ScalpelLab/scripts/helpers/sqlite_to_dbdiagram.py): export the SQLite schema to dbdiagram.io format.
- [`scripts/helpers/compare/compare_databases.py`](/F:/Projects/ScalpelLab/scripts/helpers/compare/compare_databases.py): compare two SQLite databases.
- [`scripts/helpers/compare/compare_mp4.py`](/F:/Projects/ScalpelLab/scripts/helpers/compare/compare_mp4.py): compare MP4 backups.
- [`scripts/helpers/compare/compare_seq.py`](/F:/Projects/ScalpelLab/scripts/helpers/compare/compare_seq.py): compare SEQ backups.

## Common Commands

```bash
python config.py
python run_app.py
python scripts/1_nuk_seq_export.py
python scripts/2_update_db.py --dry-run
python scripts/2_update_db.py --skip-duration
python scripts/3_seq_to_mp4_convert.py
python scripts/5_batch_blacken.py
python scripts/helpers/cut_video.py
python scripts/helpers/sqlite_to_dbdiagram.py
```

## Database Overview

### Core Tables

- `recording_details`: case-level recording metadata
- `anesthesiology`: anesthesiology roster and career dates
- `seq_status`: SEQ presence, size, and path
- `mp4_status`: MP4 presence, size, duration, and path
- `analysis_information`: labeling metadata

### Common Views

- `cur_mp4_missing`: cases where SEQ exists but MP4 is missing
- `cur_seq_missing`: cases where MP4 exists but SEQ is missing
- `cur_seniority`: anesthesiology experience / status summary
- `cur_mp4_status_statistics`: aggregated recording statistics used by the MP4 dashboard

### Camera Set

Default camera names from [`config.py`](/F:/Projects/ScalpelLab/config.py):

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
│   ├── ERD.pdf
│   ├── mp4_statistics.pdf
│   ├── scalpel_dbdiagram.txt
│   └── redaction_tracking.json
├── scripts/
│   ├── 1_nuk_seq_export.py
│   ├── 2_update_db.py
│   ├── 3_seq_to_mp4_convert.py
│   ├── 5_batch_blacken.py
│   └── helpers/
├── yolo/
├── SimCLR_reid/
├── config.py
├── run_app.py
├── requirements.txt
└── ScalpelDatabase.sqlite
```

## External Tools

Some functionality depends on tools outside Python:

- `ffmpeg` and `ffprobe` for conversion, probing, and cutting
- NVIDIA NVENC for GPU-accelerated video workflows where available
- CLExport as a fallback SEQ export path in some workflows
- MPV if you use separate local playback tooling

## Notes

- The repo is clearly Windows-oriented; paths and examples assume Windows drive letters.
- The database file defaults to `ScalpelDatabase.sqlite` in the project root.
- `scripts/2_update_db.py` is designed to preserve columns it does not manage.
- The Streamlit app can point at a different database path through the sidebar or `SCALPEL_DB`.
