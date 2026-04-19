# `seq_enriched` Table Reference

This document explains every column currently written to the `seq_enriched` table in `ScalpelDatabase.sqlite`.

The definitions below are based on:

- [`norpix_seq_format_reference.md`](./norpix_seq_format_reference.md)
- [`norpix_idx_format_reference.md`](./norpix_idx_format_reference.md)
- [`scripts/helpers/analyze_seq_fields.py`](../scripts/helpers/analyze_seq_fields.py)
- [`scripts/3_seq_to_mp4_convert.py`](../scripts/3_seq_to_mp4_convert.py)

## Row granularity

`seq_enriched` stores one row per camera recording, keyed by:

- `recording_date`
- `case_no`
- `camera_name`

In practice this corresponds to one `.seq` file discovered under a path shaped like:

```text
<root>/DATA_YY-MM-DD/CaseN/<CameraName>/<file>.seq
```

## Important behavior

- SEQ header fields come from the `.seq` file header.
- IDX fields come from the companion `.seq.idx` file.
- If the IDX file is missing, IDX-derived columns are `NULL`.
- If the SEQ header is unreadable or fails the magic check, header-derived columns are `NULL` and `header_ok = 0`.
- `3_seq_to_mp4_convert.py` can insert partial rows containing only the key fields plus IDX cache fields. Those rows are later backfilled by `analyze_seq_fields.py`.

## Key and path columns

| Column | SQLite type | Source | Meaning |
|---|---|---|---|
| `recording_date` | `TEXT` | Parsed from directory name `DATA_YY-MM-DD` | Normalized recording date in `YYYY-MM-DD` form. Part of the primary key. |
| `case_no` | `INTEGER` | Parsed from directory name `CaseN` | Case number extracted from the folder name. Part of the primary key. |
| `camera_name` | `TEXT` | Parsed from the camera folder name | Camera/channel identifier. Part of the primary key. |
| `file` | `TEXT` | Filesystem path | Relative path of the `.seq` file under the scan root. |
| `size_mb` | `REAL` | Filesystem metadata | Size of the `.seq` file in MiB, computed as `st_size / 1024^2`. The name says `mb`, but the calculation is binary MiB. |
| `has_idx` | `INTEGER` | Filesystem metadata | Boolean-like flag: `1` if `<file>.seq.idx` exists, else `0`. |
| `header_ok` | `INTEGER` | SEQ parse result | Boolean-like flag: `1` if the SEQ header was read successfully and passed the `0x0000FEED` magic check, else `0`. |

## SEQ header columns

These columns come from fixed offsets in the 1024-byte SEQ header.

| Column | SQLite type | SEQ field | Meaning |
|---|---|---|---|
| `description` | `TEXT` | Header bytes `36-547` | UTF-16 LE description string from the fixed 512-byte description field. Typically a StreamPix version string such as `StreamPix 9.1.1.0 (x64)`. |
| `width` | `INTEGER` | Header offset `548` | Frame width in pixels. |
| `height` | `INTEGER` | Header offset `552` | Frame height in pixels. |
| `allocated_frames` | `INTEGER` | Header offset `572` | Total frames recorded according to the SEQ header. In the NorPix reference this matches the IDX frame count for valid research files. |
| `fps` | `REAL` | Header offset `584` | Suggested / measured frame rate from the SEQ header. Stored rounded to 6 decimal places by `analyze_seq_fields.py`. |
| `compression_fmt` | `INTEGER` | Header offset `620` | Compression format code from the SEQ header. The NorPix reference notes that research files usually have `8` for StreamPix 9.x H.264. |
| `rec_timestamp` | `INTEGER` | Header offset `624` | Recording start time in Unix epoch seconds, UTC. This stores only the whole-second part (`reference_time_s`) and does not include the header's millisecond or microsecond subfields. |
| `exposure_ns` | `INTEGER` | Header offset `636` | Camera exposure time in nanoseconds. |

## IDX-derived columns

These columns come from parsing the companion `.seq.idx` file. Each IDX record is 32 bytes and includes `offset`, `size`, timestamp fields, `flags`, and `frame_number`.

| Column | SQLite type | IDX source | Meaning |
|---|---|---|---|
| `idx_frames` | `INTEGER` | Record count | Number of IDX records, computed as `idx_file_size / 32`. This is the number of indexed frames. |
| `dropped_frames` | `INTEGER` | `frame_number` diffs | Estimated count of dropped source frames. Computed by summing `(diff - 1)` for every positive jump in `frame_number` larger than 1. |
| `drop_rate` | `REAL` | `dropped_frames`, `idx_frames` | Fraction of expected frames that were dropped, computed as `dropped_frames / (idx_frames + dropped_frames)`. |
| `first_frame_no` | `INTEGER` | First IDX `frame_number` | The first monotonic frame counter value seen in the IDX. |
| `last_frame_no` | `INTEGER` | Last IDX `frame_number` | The last monotonic frame counter value seen in the IDX. |
| `frame_span` | `INTEGER` | `last_frame_no - first_frame_no` | Span covered by the frame counter between the first and last indexed frames. This is a simple difference, not an inclusive count. |
| `n_duplicates` | `INTEGER` | `frame_number` diffs | Number of zero-delta steps in the IDX frame counter. A zero diff means the same `frame_number` appeared twice in a row. |
| `n_counter_resets` | `INTEGER` | `frame_number` diffs | Number of negative jumps in the IDX frame counter. These are treated as counter resets or wraparounds. |
| `first_frame_time` | `REAL` | First IDX timestamp | Unix timestamp of the first frame, in UTC seconds including fractional milliseconds and microseconds decoded from `ts_sec` and `ts_sub`. |
| `last_frame_time` | `REAL` | Last IDX timestamp | Unix timestamp of the last frame, in UTC seconds including fractional milliseconds and microseconds. |

## Derived datetime and duration columns

These columns are computed from the parsed IDX timestamps and the SEQ header FPS.

| Column | SQLite type | Formula | Meaning |
|---|---|---|---|
| `first_frame_datetime` | `TEXT` | UTC formatting of `first_frame_time` | Human-readable UTC timestamp string in the form `YYYY-MM-DD HH:MM:SS.ffffff`. The code does not append a timezone suffix even though the value is generated in UTC. |
| `last_frame_datetime` | `TEXT` | UTC formatting of `last_frame_time` | Human-readable UTC timestamp string for the last frame, using the same format as `first_frame_datetime`. |
| `actual_duration` | `REAL` | `last_frame_time - first_frame_time` | Observed elapsed time across the indexed recording, in seconds. |
| `expected_duration` | `REAL` | `idx_frames / fps` | Expected duration in seconds using the number of IDX records and the SEQ header FPS. This reflects the script's implementation exactly. |
| `time_drift_ms` | `REAL` | `(actual_duration - expected_duration) * 1000` | Difference between observed duration and FPS-derived expected duration, in milliseconds. Positive means the timestamp span is longer than expected. |
| `max_time_gap_ms` | `REAL` | `max(diff(timestamps)) * 1000` | Largest inter-frame timestamp gap in milliseconds between any two consecutive IDX frames. |

## IDX cache and provenance columns

These columns are used so other scripts can reuse IDX parse results without reparsing unchanged files.

| Column | SQLite type | Source | Meaning |
|---|---|---|---|
| `idx_file_size` | `INTEGER` | Filesystem metadata on `.seq.idx` | Size in bytes of the IDX file at the time metadata was cached. `3_seq_to_mp4_convert.py` compares this value with the current on-disk file size to decide whether the cache is still valid. |
| `idx_cached_at` | `TEXT` | Cache write time | ISO-8601 timestamp recording when IDX-derived values were last cached. `analyze_seq_fields.py` writes this in UTC with timezone information; `3_seq_to_mp4_convert.py` writes a local `datetime.now().isoformat()` string. |

## Null patterns to expect

| Situation | Columns commonly `NULL` |
|---|---|
| No `.idx` file exists | All IDX-derived, duration, datetime, and cache columns |
| Invalid / corrupt SEQ header | `description`, `width`, `height`, `allocated_frames`, `fps`, `compression_fmt`, `rec_timestamp`, `exposure_ns` |
| Partial row created by `3_seq_to_mp4_convert.py` before full enrichment | Most non-key columns except `idx_frames`, `first_frame_time`, `last_frame_time`, `idx_file_size`, `idx_cached_at` |

## Quick source map

- Path-derived keys: `_parse_path_key(...)`
- SEQ header parse: `parse_seq_header(...)`
- IDX parse and timing metrics: `parse_idx(...)`
- UTC string formatting: `_unix_to_iso(...)`
- Table definition / column order: `_DB_COLUMNS`
