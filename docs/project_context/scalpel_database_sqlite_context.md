# `ScalpelDatabase.sqlite` Context

This document describes the project-root SQLite database used by ScalpelLab:
[`ScalpelDatabase.sqlite`](../../ScalpelDatabase.sqlite).

It complements the higher-level project overview in [`README.md`](../../README.md)
and the SEQ-specific references in this directory.

## Role in the Project

`ScalpelDatabase.sqlite` is the local working database for the Streamlit dashboard,
file-system status scans, SEQ-to-MP4 conversion workflow, and batch redaction
scripts.

The database is expected to live in the project root. `config.py` defines:

- `DB_PATH = PROJECT_ROOT / "ScalpelDatabase.sqlite"`
- `SEQ_ROOT = F:\Room_8_Data\Sequence_Backup`
- `MP4_ROOT = F:\Room_8_Data\Recordings`

The Streamlit app can also point at another database path through its sidebar or
the `SCALPEL_DB` environment variable.

## Current Snapshot

Inspected on 2026-04-28.

| Property | Value |
|---|---:|
| File path | `F:\Projects\ScalpelLab\ScalpelDatabase.sqlite` |
| File size | 1,437,696 bytes |
| Last modified | 2026-04-26 10:44:47 |
| Integrity check | `ok` |
| User tables | 7 |
| User views | 4 |
| Recording date range | 2022-12-04 to 2025-09-09 |
| Distinct recording dates | 126 |
| Distinct recording cases | 176 |

`PRAGMA foreign_key_check` currently reports 168 rows. Treat declared foreign
keys as useful schema intent, not as proof that all existing rows are clean.

| Table | Foreign-key check rows |
|---|---:|
| `recording_details` | 97 |
| `mp4_status` | 36 |
| `seq_status` | 32 |
| `mp4_times` | 3 |

## Tables

| Table | Rows | Primary key | Purpose |
|---|---:|---|---|
| `recording_details` | 176 | `recording_date`, `case_no` | Case-level metadata, signature time, code, anesthesiology link, seniority snapshot. |
| `analysis_information` | 82 | `recording_date`, `case_no` | Labeling / tagging metadata for analyzed cases. |
| `anesthesiology` | 73 | `anesthesiology_key` | Anesthesiology roster and career milestone dates. |
| `seq_status` | 1,440 | `recording_date`, `case_no`, `camera_name` | SEQ file presence, size, and relative path. |
| `mp4_status` | 1,444 | `recording_date`, `case_no`, `camera_name` | MP4 file presence, size, duration, redaction segment metadata, offset, and relative path. |
| `mp4_times` | 125 | `case_no`, `recording_date` | Manual or curated case timing ranges used by redaction workflows. |
| `seq_enriched` | 985 | `recording_date`, `case_no`, `camera_name` | Parsed SEQ header and IDX metadata cache. See [`seq_enriched_table_reference.md`](./seq_enriched_table_reference.md). |

## Views

| View | Rows | Meaning |
|---|---:|---|
| `cur_mp4_missing` | 185 | Per-case camera matrix showing MP4 rows with both `size_mb` and `duration_minutes`. |
| `cur_seq_missing` | 0 | Camera rows where an MP4 exists but the matching SEQ status row has no size. |
| `cur_seniority` | 73 | Current anesthesiology seniority and attending/resident status derived from dates. |
| `cur_mp4_status_statistics` | 162 | Recording-level camera counts used by the MP4 statistics dashboard. |

## Core Relationships

The database uses `recording_date` plus `case_no` as the central case identity.
Camera-level tables add `camera_name`.

Intended relationships:

- `recording_details(recording_date, case_no)` is the case metadata anchor.
- `analysis_information(recording_date, case_no)` stores analysis labels for a
  subset of cases.
- `seq_status`, `mp4_status`, `mp4_times`, and `seq_enriched` are keyed by the
  same date/case pattern.
- `recording_details.anesthesiology_key` points to
  `anesthesiology.anesthesiology_key`.

SQLite foreign keys are declared in several tables, but the current data contains
foreign-key check rows. Scripts generally operate by explicit key matching and
upserts, so check assumptions before making migrations that depend on strict FK
validity.

## Important Columns

### `recording_details`

| Column | Type | Notes |
|---|---|---|
| `recording_date` | `TEXT` | Normalized date, primary key part. |
| `case_no` | `INTEGER` | Case number for the date, primary key part. |
| `signature_time` | `TEXT` | Case signature or timing marker. |
| `code` | `TEXT` | Case code. |
| `anesthesiology_key` | `INTEGER` | Optional link to `anesthesiology`. |
| `months_anesthetic_recording` | `INTEGER` | Seniority at recording time. |
| `anesthetic_attending` | `TEXT` | Stored attending/resident-style classification. |

### `seq_status`

| Column | Type | Notes |
|---|---|---|
| `recording_date`, `case_no`, `camera_name` | mixed | Camera recording key. |
| `size_mb` | `INTEGER` | Present when an acceptable SEQ file was found. |
| `path` | `TEXT` | Relative SEQ path under `SEQ_ROOT`, written by `scripts/2_update_db.py`. |

### `mp4_status`

| Column | Type | Notes |
|---|---|---|
| `recording_date`, `case_no`, `camera_name` | mixed | Camera recording key. |
| `size_mb` | `INTEGER` | Present when an acceptable MP4 file was found. |
| `duration_minutes` | `REAL` | Usually populated by `ffprobe` through `scripts/2_update_db.py`. |
| `pre_black_segment`, `post_black_segment` | `REAL` | Written by redaction tooling. |
| `path` | `TEXT` | Relative MP4 path under `MP4_ROOT`. |
| `offset_seconds` | `REAL` | Timing offset used by downstream video workflows. |

### `mp4_times`

`mp4_times` stores up to three redaction/case segments:

- `start_1`, `end_1`
- `start_2`, `end_2`
- `start_3`, `end_3`

Current rows: 125 total, 24 with a second segment, and 3 with a third segment.

## Camera Coverage

Default camera names are defined in `config.py`:

- `Cart_Center_2`
- `Cart_LT_4`
- `Cart_RT_1`
- `General_3`
- `Monitor`
- `Patient_Monitor`
- `Ventilator_Monitor`
- `Injection_Port`

### `seq_status`

| Camera | Rows | Rows with `size_mb` |
|---|---:|---:|
| `Cart_Center_2` | 180 | 116 |
| `Cart_LT_4` | 180 | 113 |
| `Cart_RT_1` | 180 | 118 |
| `General_3` | 180 | 117 |
| `Injection_Port` | 180 | 11 |
| `Monitor` | 180 | 101 |
| `Patient_Monitor` | 180 | 118 |
| `Ventilator_Monitor` | 180 | 118 |

### `mp4_status`

| Camera | Rows | Rows with `size_mb` | Rows with duration |
|---|---:|---:|---:|
| `Cart_Center_2` | 179 | 115 | 115 |
| `Cart_LT_4` | 179 | 112 | 112 |
| `Cart_RT_1` | 179 | 115 | 114 |
| `General_3` | 179 | 114 | 113 |
| `Injection_Port` | 179 | 5 | 5 |
| `Monitor` | 179 | 91 | 90 |
| `Patient_Monitor` | 185 | 113 | 112 |
| `Ventilator_Monitor` | 185 | 108 | 107 |

## `seq_enriched` Notes

`seq_enriched` is a richer cache built from SEQ headers and companion IDX files.
It stores one row per camera recording when enrichment has been run. The table
contains standard cameras plus junk/variant camera-name rows such as
`*_JUNK`, `*_Junk`, and uppercase `CART_LT_4`.

Current high-level state:

| Metric | Value |
|---|---:|
| Rows | 985 |
| Rows with `has_idx = 1` | 949 |
| Rows with `header_ok = 1` | 975 |
| Rows with parsed `idx_frames` | 951 |
| Average `drop_rate` | 0.001739 |

For column-by-column definitions, use
[`seq_enriched_table_reference.md`](./seq_enriched_table_reference.md).

## Script Ownership

| Area | Main files |
|---|---|
| Database location and camera list | [`config.py`](../../config.py) |
| Streamlit DB browsing and loading | [`app/utils.py`](../../app/utils.py), [`app/app.py`](../../app/app.py), `app/pages/*` |
| SEQ / MP4 status refresh | [`scripts/2_update_db.py`](../../scripts/2_update_db.py) |
| SEQ metadata enrichment | [`scripts/helpers/analyze_seq_fields.py`](../../scripts/helpers/analyze_seq_fields.py) |
| SEQ-to-MP4 conversion and IDX cache reuse | [`scripts/3_seq_to_mp4_convert.py`](../../scripts/3_seq_to_mp4_convert.py) |
| Redaction timing and black-segment writes | [`scripts/5_batch_blacken.py`](../../scripts/5_batch_blacken.py) |
| Schema export | [`scripts/helpers/sqlite_to_dbdiagram.py`](../../scripts/helpers/sqlite_to_dbdiagram.py) |
| Database comparison | [`scripts/helpers/compare/compare_databases.py`](../../scripts/helpers/compare/compare_databases.py) |

## Operational Notes

- `scripts/2_update_db.py` is designed to preserve columns it does not manage.
  It manages `seq_status.size_mb/path` and
  `mp4_status.size_mb/duration_minutes/path`.
- `scripts/5_batch_blacken.py` reads `mp4_times` joined to `mp4_status`, then
  can update `mp4_status.pre_black_segment` and `post_black_segment`.
- `scripts/3_seq_to_mp4_convert.py` prefers `seq_enriched` for resolution,
  timestamp, and cached IDX metadata when available.
- Paths in `seq_status.path` and `mp4_status.path` should be treated as paths
  relative to the configured SEQ/MP4 roots, not necessarily absolute paths.
- SQLite writes should be kept narrow and backed up before bulk updates. This
  database is a project artifact, not a disposable cache.

## Useful Inspection Commands

```powershell
sqlite3 -readonly ScalpelDatabase.sqlite ".tables"
sqlite3 -readonly ScalpelDatabase.sqlite ".schema"
sqlite3 -readonly ScalpelDatabase.sqlite "PRAGMA integrity_check;"
sqlite3 -readonly ScalpelDatabase.sqlite "PRAGMA foreign_key_check;"
sqlite3 -readonly ScalpelDatabase.sqlite "SELECT type, name FROM sqlite_schema WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name;"
```
