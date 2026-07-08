# `ScalpelDatabase.sqlite` Context

This document describes the project-root SQLite database used by ScalpelLab:
[`ScalpelDatabase.sqlite`](../../ScalpelDatabase.sqlite).

It complements the higher-level project overview in [`README.md`](../../README.md)
and the SEQ-specific references in this directory. The current root DB is the
canonical schema snapshot for this context file.

## Role in the Project

`ScalpelDatabase.sqlite` is the local working database for the NiceGUI
dashboard, file-system status scans, SEQ-to-MP4 conversion workflow, MPV
multiviewer offsets, and batch redaction helpers.

The database defaults to the project root. `config.py` defines:

- `DB_PATH = PROJECT_ROOT / "ScalpelDatabase.sqlite"`
- `SEQ_ROOT = F:\Room_8_Data\Sequence_Backup`
- `MP4_ROOT = F:\Room_8_Data\Recordings`
- `ANALYSES_ROOT = F:\Room_8_Data\Analyses\Case_Analyses_synced`
- `NORPIX_SEQUENCE_VIEWER_PATH = C:\Program Files\Common Files\NorPix\SequenceViewer.exe`

The NiceGUI app can point at alternate paths through the Home page
Configuration panel or the relevant `SCALPEL_*` environment variables.

## Current Snapshot

Inspected on 2026-06-29 from the repository root DB.

| Property | Value |
|---|---:|
| File path | `ScalpelDatabase.sqlite` |
| Integrity check | `ok` |
| User tables | 7 |
| User views | 4 |
| `PRAGMA foreign_key_check` rows | 216 |

Foreign-key check rows are known in this working DB. Treat declared foreign
keys as schema intent, not as proof that all existing rows are clean.

## Tables

| Table | Rows | Primary key | Purpose |
|---|---:|---|---|
| `recording_details` | 176 | `recording_date`, `case_no` | Case-level metadata, signature time, code, anesthesiology link, seniority snapshot. |
| `analysis_information` | 82 | `recording_date`, `case_no` | Labeling / tagging metadata for analyzed cases, with optional `event_id` column for BORIS imports. |
| `anesthesiology` | 73 | `anesthesiology_key` | Anesthesiology roster and career milestone dates. |
| `seq_status` | 1,464 | `recording_date`, `case_no`, `camera_name` | SEQ file presence, size, and relative path. |
| `mp4_status` | 1,468 | `recording_date`, `case_no`, `camera_name` | MP4 file presence, size, duration, relative path, and manual sync offset. |
| `mp4_times` | 125 | `recording_date`, `case_no` | Manual or curated case timing ranges used by redaction workflows. |
| `seq_enriched` | 830 | `recording_date`, `case_no`, `camera_name` | Parsed SEQ header and IDX metadata cache. See [`seq_enriched_table_reference.md`](./seq_enriched_table_reference.md). |

Optional importer-created tables are not present in the current root DB until
the relevant importer runs:

- `boris_events`, created by [`scripts/helpers/import_boris_tags.py`](../../scripts/helpers/import_boris_tags.py) or [`scripts/helpers/import_analysis_finale.py`](../../scripts/helpers/import_analysis_finale.py).
- `monitor_samples` and `monitor_case_summary`, created by [`scripts/helpers/import_analysis_finale.py`](../../scripts/helpers/import_analysis_finale.py).
- `mp4_status.pre_black_segment` and `mp4_status.post_black_segment`, created on demand by [`scripts/helpers/batch_black_squere.py`](../../scripts/helpers/batch_black_squere.py) for older DB snapshots that do not yet have redaction columns.

## Views

| View | Rows | Meaning |
|---|---:|---|
| `cur_mp4_missing` | 188 | Per-case camera matrix showing missing MP4 exports for SEQ rows. |
| `cur_sync_status` | 830 | Per-(date, case, camera) `is_syncable` flag derived from `seq_enriched` using the converter planning gates. |
| `cur_seniority` | 73 | Current anesthesiology seniority and attending/resident status derived from dates. |
| `cur_mp4_status_statistics` | 167 | Recording-level camera counts used by the MP4 statistics dashboard. |

BORIS intervals are not exposed through a SQL view. The old
`cur_boris_intervals` view was removed because joining through
`analysis_information.event_id` collapsed intervals to roughly one row per
case. Interval pairing now happens in pandas inside
[`app/pages/boris.py`](../../app/pages/boris.py), partitioned by
`boris_events.source_file` when that table exists.

## Core Relationships

The database uses `recording_date` plus `case_no` as the central case identity.
Camera-level tables add `camera_name`.

- `recording_details(recording_date, case_no)` is the case metadata anchor.
- `analysis_information(recording_date, case_no)` stores analysis labels for a
  subset of cases.
- `analysis_information.event_id` is reserved for optional BORIS imports.
- `seq_status`, `mp4_status`, `mp4_times`, and `seq_enriched` are keyed by the
  same date/case pattern.
- `seq_enriched(recording_date, case_no, camera_name)` foreign-keys to
  `seq_status(recording_date, case_no, camera_name)` with `ON DELETE CASCADE`.
- `recording_details.anesthesiology_key` points to
  `anesthesiology.anesthesiology_key`.

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

### `analysis_information`

| Column | Type | Notes |
|---|---|---|
| `recording_date` | `TEXT` | Normalized date, primary key part. |
| `case_no` | `INTEGER` | Case number for the date, primary key part. |
| `label_by` | `TEXT` | Labeling / tagging attribution. |
| `event_id` | `INTEGER` | Optional link populated by BORIS importers when `boris_events` exists. |

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
| `path` | `TEXT` | Relative or absolute MP4 path used by the dashboard and MPV viewer. |
| `offset_seconds` | `REAL` | Manual sync offset saved by `MPV_Multiviewer`. |

### `mp4_times`

`mp4_times` stores up to three redaction/case segments:

- `start_1`, `end_1`
- `start_2`, `end_2`
- `start_3`, `end_3`

These timings are consumed by joining to `mp4_status` on `recording_date` and
`case_no`, then applying the same timing ranges to each matching camera row.

## Camera Coverage

Default camera names are defined in [`config.py`](../../config.py):

- `Cart_Center_2`
- `Cart_LT_4`
- `Cart_RT_1`
- `General_3`
- `Monitor`
- `Patient_Monitor`
- `Ventilator_Monitor`
- `Injection_Port`

`_JUNK` / `_Junk` camera-name suffixes mark failed or undersized recordings and
should be filtered out for statistics and visualizations.

## `seq_enriched` Notes

`seq_enriched` stores one row per camera recording when enrichment has been
run. It includes parsed SEQ header fields, IDX frame counts, drop metrics,
timestamp-derived duration/drift metrics, and frame-date sanity flags.

Consumers should clip or bucket large absolute `time_drift_ms` values before
plotting; broken IDX timestamps can produce outliers around `2e12` ms.

For column-by-column definitions, use
[`seq_enriched_table_reference.md`](./seq_enriched_table_reference.md).

## Script Ownership

| Area | Main files |
|---|---|
| Database location and camera list | [`config.py`](../../config.py) |
| NiceGUI DB browsing and loading | [`app/utils.py`](../../app/utils.py), [`app/app.py`](../../app/app.py), `app/pages/*` |
| SEQ / MP4 status refresh | [`scripts/2_update_db.py`](../../scripts/2_update_db.py) |
| SEQ metadata enrichment | [`scripts/helpers/analyze_seq_fields.py`](../../scripts/helpers/analyze_seq_fields.py) |
| IDX creation | NorPix SequenceViewer configured by `config.NORPIX_SEQUENCE_VIEWER_PATH` |
| SEQ-to-MP4 conversion and IDX cache reuse | [`scripts/3_seq_to_mp4_convert.py`](../../scripts/3_seq_to_mp4_convert.py) |
| BORIS / monitor import | [`scripts/helpers/import_boris_tags.py`](../../scripts/helpers/import_boris_tags.py), [`scripts/helpers/import_analysis_finale.py`](../../scripts/helpers/import_analysis_finale.py) |
| Redaction timing and black-segment writes | [`scripts/helpers/batch_black_squere.py`](../../scripts/helpers/batch_black_squere.py) |
| Schema export | [`scripts/helpers/sqlite_to_dbdiagram.py`](../../scripts/helpers/sqlite_to_dbdiagram.py) |
| Database comparison | [`scripts/helpers/compare/compare_databases.py`](../../scripts/helpers/compare/compare_databases.py) |

## Operational Notes

- `scripts/2_update_db.py` is designed to preserve columns it does not manage.
  It manages `seq_status.size_mb/path` and
  `mp4_status.size_mb/duration_minutes/path`.
- Missing IDX files are created by opening registered SEQ files with NorPix
  SequenceViewer. The executable path is configurable in `config.py` and from
  the NiceGUI Home page.
- `scripts/3_seq_to_mp4_convert.py` prefers `seq_enriched` for resolution,
  timestamp, and cached IDX metadata when available.
- Paths in `seq_status.path` and `mp4_status.path` should be treated as paths
  relative to the configured roots unless the producer wrote an absolute path.
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
