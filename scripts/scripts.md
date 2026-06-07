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
├── import_analysis_finale.py   # Analyses_Finale BORIS CSV + monitor vitals import
└── helpers/                    # see scripts/helpers/helpers.md
```

## Pipeline contract — read before editing

The dashboard presents the operational workflow as **Processing Pipeline**:

1. **SEQ Curation** — copy new source `.seq` files into the organized SEQ root.
2. **Update DB + Create IDX Files** — register SEQ rows, then create missing
   `.seq.idx` companions.
3. **Analyze SEQ Fields** — parse registered SEQ headers and IDX metadata once
   per file.
4. **SEQ to MP4** — convert eligible SEQ files, then refresh DB MP4 status.

Run dry-runs/previews before any real file operation.

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

### IDX creation
The canonical IDX creation flow opens each registered `.seq` file with NorPix
SequenceViewer and waits for the companion `.seq.idx` file to appear and reach
a stable size before moving to the next SEQ. The executable path comes from
`config.NORPIX_SEQUENCE_VIEWER_PATH`, defaulting to:

```text
C:\Program Files\Common Files\NorPix\SequenceViewer.exe
```

This path is editable in the NiceGUI Home page Configuration panel. Existing
valid IDX files are skipped. Failures, timeouts, skipped files, and processed
files should be logged under `logs/idx_creation/`.

`scripts/helpers/repair_seq_idx.py` is an audit/repair helper that rebuilds IDX
files from SEQ bytes. Keep it available, but do not treat it as the primary IDX
creation workflow unless a task explicitly asks for repair or fallback behavior.

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

Default fallback: missing-IDX SEQ files are converted directly with FFmpeg
when it can decode the embedded H.264 stream. These outputs are named
`*_NOT_SYNCABLE.mp4`, logged as `NO-SYNC`, and excluded from sync validation
because there are no trusted per-frame timestamps. Use
`--no-include-not-syncable` for a syncable-only run.

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

### `import_analysis_finale.py` — finalized analyses importer
- Reads `ANALYSES_ROOT/DATA_YY-MM-DD/CaseN/Boris/*_standardized.csv` and
  `ANALYSES_ROOT/DATA_YY-MM-DD/CaseN/Monitor/motior_data.csv`.
- BORIS import replaces existing `boris_events` rows and relinks
  `analysis_information.event_id` to the first imported event per case.
- Monitor import is idempotent per case, writing `monitor_samples` plus
  `monitor_case_summary` for the dashboard.
- Always run with `--dry-run` first; the app launcher is `/analysis-import`.

## Conventions

- All scripts import `config` via `sys.path.insert(0, parent)` shim and read
  paths through `get_db_path()` / `get_seq_root()` / `get_mp4_root()` /
  `get_norpix_sequence_viewer_path()`. Don't hardcode paths.
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

## Logs

Operational scripts should write timestamped logs under `logs/`, grouped by
pipeline step:

```text
logs/
├── seq_curation/
├── db_update/
├── idx_creation/
├── seq_analysis/
└── seq_to_mp4/
```
