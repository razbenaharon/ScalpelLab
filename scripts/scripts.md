# scripts/ — SEQ/MP4 pipeline + BORIS importer

The numbered scripts form the **producer pipeline** that fills the SQLite DB.
Run in order; each is idempotent and can be re-run safely.

## Layout

```
scripts/
├── 1_seq_curation.py           # curate raw NorPix SEQ → DATA_YY-MM-DD/CaseN/CameraName/
├── 2_update_db.py              # walks SEQ_ROOT and MP4_ROOT, upserts seq_status / mp4_status / seq_enriched
├── 3_seq_to_mp4_convert.py     # SEQ → MP4 via raw H.264 + mkvmerge VFR + FFmpeg fps=30 CFR
├── import_boris_tags.py        # BORIS TSV → boris_events (+ link analysis_information.event_id)
└── helpers/                    # see scripts/helpers/helpers.md
```

## Pipeline contract — read before editing

### `1_seq_curation.py`
- Discovers `.seq` files plus companion files (`.metadata`, `.idx`, `.xml`,
  `.aud`) in a source directory.
- Groups by date, then into "cases" using a 30-minute time window.
- Auto-maps source channels (`Camera1`, `Camera2`, …) to standard camera names
  in `config.DEFAULT_CAMERAS`.
- Files smaller than 200 MB get a `_JUNK` / `_Junk` suffix on the camera-folder
  name. Downstream code filters these out.
- Multi-threaded copy with SHA-256 verification and atomic rename. Disk-space
  is checked before any copy.

### `2_update_db.py` — the "managed columns" contract
This script must **only update a fixed set of columns** and preserve everything
else, so users can add columns to the schema without breaking re-runs.

- `mp4_status` managed columns: `size_mb`, `duration_minutes`, `path`.
- `seq_status` managed columns: `size_mb`, `path`.
- Implementation uses `INSERT … ON CONFLICT(pk) DO UPDATE SET <managed>=…`.
  Never switch this to a `REPLACE` or full overwrite — it would clobber
  user-added columns like `sync_offset_ms`, redaction flags, etc.
- Optionally calls `helpers/analyze_seq_fields.py` to populate `seq_enriched`
  (parsed SEQ headers + IDX frame analysis). Skipped silently if the import
  fails — keep the import inside a `try/except ImportError`.
- Common flags: `--dry-run`, `--skip-duration` (skip `ffprobe`),
  `--threshold-mb` (junk threshold), `--delete-small-mb`.

### `3_seq_to_mp4_convert.py` — VFR→CFR sync pipeline
Multi-camera synchronization to a shared global timeline (union strategy).
Output videos for the same date/case have **identical duration** with black
pre-roll/post-roll padding so cameras stay in sync.

Per-file pipeline:
1. Extract raw H.264 NAL units from `.seq` using IDX byte offsets → temp
   `.h264`.
2. Generate a `mkvmerge` "timecode format v2" file with per-frame timestamps
   (ms) from the IDX records.
3. `mkvmerge` muxes `.h264` + timecodes → temp `.mkv` (VFR container).
4. `ffmpeg` reads the MKV, applies `fps=30` (nearest-neighbor — duplicates on
   gaps, drops on bursts), `tpad` for pre/post-roll, and hard-cuts at `-t` for
   exact global duration → final `.mp4`. Encoder is `hevc_nvenc`.

Reads the to-do list from the `cur_mp4_missing` view. Concurrent cameras are
configurable. Cleans up all temporary files on success or failure.

### `import_boris_tags.py` — strict importer
- Filenames **must** match `YY-MM-DD-caseN.tsv`. Mismatches are skipped and
  logged.
- Files must contain at least one BORIS event row.
- Required TSV columns include `Subject`, `Behavior`, `Behavior type`, `Time`.
- Stores per-case linkage in `analysis_information.event_id` (one event per
  case). START/STOP pairing into intervals is computed in the dashboard at
  [app/pages/boris.py](../app/pages/boris.py) — the old `cur_boris_intervals`
  view was removed because its join through `analysis_information.event_id`
  collapsed the result to ~one row per case.
- Always run with `--dry-run` first when working on this file.

## Conventions

- All scripts import `config` via `sys.path.insert(0, parent)` shim and read
  paths through `get_db_path()` / `get_seq_root()` / `get_mp4_root()`. Don't
  hardcode paths.
- UTF-8 stdout reconfiguration (see auto-memory `MEMORY.md`) is required at
  the top of any script that prints non-ASCII, since the Windows console is
  CP1252.
- Use raw strings (`r"…"`) for any new path literals.
- `--dry-run` is the standard flag for previewing destructive changes — keep
  this convention when adding new scripts.

## Pitfalls

- `n_counter_resets > 0` in `seq_enriched` is normal ring-buffer behavior, not
  an overrun indicator. Don't surface it as a quality flag without other
  corroborating signals.
- IDX timestamps occasionally produce nonsensical `time_drift_ms` (~2e12 ms).
  The producer here doesn't repair them; consumers must clip.
- `3_seq_to_mp4_convert.py` requires `mkvmerge` and NVENC-capable FFmpeg on
  PATH. There is a CPU fallback path; preserve it when refactoring.
