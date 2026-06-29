# scripts/helpers/ — utilities used by the pipeline and ad-hoc workflows

## Layout

```
scripts/helpers/
├── analyze_seq_fields.py     # SEQ header + IDX parsing → seq_enriched table
├── repair_seq_idx.py         # audit/rebuild .seq.idx files from SEQ bodies
├── batch_black_squere.py     # batch redact MP4 regions from mp4_times timing data
├── cut_video.py              # FFmpeg stream-copy segment cuts
├── backup_dir.py             # mirror copy preserving structure
├── sqlite_to_dbdiagram.py    # export current SQLite schema to dbdiagram.io
└── compare/
    ├── compare_databases.py
    ├── compare_mp4.py
    └── compare_seq.py
```

## Per-helper contracts

### `analyze_seq_fields.py`
The producer of `seq_enriched`. Imported by `scripts/2_update_db.py` via
`from helpers.analyze_seq_fields import analyze_directory, write_to_db,
_load_existing_keys`. **Keep these three names stable**, or update the
matching `try/except ImportError` shim in `2_update_db.py`.

Parses raw NorPix SEQ headers and IDX records:
- SEQ header fields: description, width, height, allocated_frames, fps,
  compression_fmt, rec_timestamp, exposure_ns + several "unknown" fields
  (`unk_640`, `unk_656`, `unk_660`, `unk_664`, `delta_664`).
- IDX record (32 bytes per frame): byte offset, frame size, `ts_sec`,
  packed `ts_sub` (ms in low 16 bits, µs in high 16), reserved, flags,
  `frame_number`.
- Derived metrics: `dropped_frames`, `drop_rate`, `frame_span`,
  `n_duplicates`, `n_counter_resets`, `actual_duration`,
  `expected_duration`, `time_drift_ms`, `max_time_gap_ms`.
- Date sanity metrics: first/last IDX frame datetimes are compared against the
  path-derived `recording_date`; mismatches are written as explicit flags in
  `seq_enriched` and excluded by `cur_sync_status`.

Primary key in `seq_enriched`: `(recording_date, case_no, camera_name)`.

### IDX creation helper
The primary IDX creation workflow opens registered `.seq` files with NorPix
SequenceViewer, configured by `config.NORPIX_SEQUENCE_VIEWER_PATH` and editable
from the NiceGUI Home page.

Contract:
- Resolve candidate SEQ files from the DB after `seq_status` has been updated.
- Skip files that already have a valid companion `.seq.idx` file.
- Open one SEQ at a time in SequenceViewer, wait for the `.idx` to appear, then
  poll until the file size is stable.
- Treat nonzero size divisible by 32 bytes as the basic IDX validity check.
- Enforce a per-file timeout and report failures without stopping the whole
  batch.
- Close the SequenceViewer process after each file; force-kill only the tracked
  process tree if graceful close times out.
- Log processed, skipped, failed, and timed-out files under
  `logs/idx_creation/`.

### `repair_seq_idx.py`
Audits and rebuilds `.seq.idx` files by walking the SEQ body. Uses a
checkpoint file at `docs/seq_idx_repair_tracking.json` so long runs can
resume. Run with `--dry-run` first. This is an audit/repair fallback, not the
canonical SequenceViewer-based IDX creation path.

### `batch_black_squere.py` (note the typo — keep it)
Reads timing ranges from `mp4_times` and applies a black-rectangle redaction
to the MP4 frames. Driven entirely from the DB; no GUI. Tracking state goes
to `docs/redaction_tracking.json`. It creates `mp4_status.pre_black_segment`
and `mp4_status.post_black_segment` on demand when older DB snapshots do not
yet have those columns.

### `cut_video.py`
Thin wrapper around FFmpeg stream-copy (`-c copy`) for fast lossless cuts.

### `backup_dir.py`
Pure copy utility, structure-preserving. Used by ad-hoc backup workflows.

### `sqlite_to_dbdiagram.py`
Exports the live SQLite schema to dbdiagram.io DBML. Output goes to
`docs/scalpel_dbdiagram.txt`. Run after schema changes.

### `compare/`
Three independent comparison tools — DB-vs-DB, MP4-tree-vs-tree, SEQ-tree-vs-tree.
Useful for verifying backups and migrations.

## Conventions

- Helpers may be invoked standalone or imported. When importing from
  `2_update_db.py`, do so inside a `try/except ImportError` so the helper
  remains optional.
- Use `config.get_db_path()` / `get_seq_root()` / `get_mp4_root()` — never
  hardcode paths. Use `get_norpix_sequence_viewer_path()` for the canonical
  NorPix SequenceViewer executable.
- Tracking JSON files (`docs/*_tracking.json`) are checkpoint state; do not
  delete them mid-run.
