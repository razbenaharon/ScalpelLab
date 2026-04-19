"""
Analyze SEQ header fields and correlate them with frame-drop data and
timing/synchronization metrics parsed from companion IDX files.

Usage:
    python analyze_seq_fields.py [directory]

    If directory is omitted, SEQ_ROOT from config.py is used.
    Recursively finds all *.seq files under the given root.

Output:
    - Console report with frame-drop and timing/synchronization analysis

SEQ header fields extracted (all little-endian):
    offset 36  : UTF-16 LE  description (512 bytes, null-terminated)
    offset 548 : uint32     width
    offset 552 : uint32     height
    offset 572 : uint32     allocated_frames
    offset 584 : float64    fps
    offset 620 : uint32     compression_fmt
    offset 624 : uint32     rec_timestamp  (Unix epoch, recording start)
    offset 636 : uint32     exposure_ns

IDX record layout (32 bytes, little-endian):
    offset 0  : uint64  file byte offset of frame
    offset 8  : uint32  frame size (bytes)
    offset 12 : uint32  ts_sec        <-- used for timing analysis
    offset 16 : uint32  ts_sub        <-- packed ms (bits 15-0) + µs (bits 31-16)
    offset 20 : uint32  reserved
    offset 24 : uint32  flags
    offset 28 : uint32  frame_number  <-- monotonic ring-buffer counter
"""

import re
import sys
import sqlite3
import struct
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Project imports
# ---------------------------------------------------------------------------
_SCRIPT_DIR  = Path(__file__).resolve().parent
_PROJECT_ROOT = _SCRIPT_DIR.parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

try:
    from config import SEQ_ROOT as _CFG_SEQ_ROOT, get_db_path as _get_db_path
    _CFG_DB_PATH: str | None = _get_db_path()
except ImportError:
    _CFG_SEQ_ROOT = None
    _CFG_DB_PATH  = None

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
SEQ_HEADER_SIZE = 1024
IDX_RECORD_SIZE = 32          # bytes per IDX record
IDX_UINT32_PER_RECORD = 8     # 32 / 4 — record viewed as uint32 array
IDX_FRAME_NO_U32_IDX  = 7    # frame_number is at offset 28 = index 7

SEQ_MAGIC = 0x0000FEED

# Header field offsets (uint32 unless noted)
OFF_DESCRIPTION      = 36     # UTF-16 LE, 512 bytes (256 chars), null-terminated
OFF_DESCRIPTION_LEN  = 512
OFF_WIDTH            = 548
OFF_HEIGHT           = 552
OFF_ALLOCATED_FRAMES = 572
OFF_FPS              = 584    # float64
OFF_COMPRESSION_FMT  = 620
OFF_REC_TIMESTAMP    = 624    # uint32, Unix epoch seconds (recording start)
OFF_EXPOSURE_NS      = 636

# IDX uint32 indices within each 8-uint32 record
IDX_U32_TS_SEC   = 3   # offset 12
IDX_U32_TS_SUB   = 4   # offset 16

# Database
SEQ_ANALYSIS_TABLE = "seq_enriched"

# Path-key regexes (same convention as 2_update_db.py)
_RE_DATA = re.compile(r"^DATA_(\d{2})-(\d{2})-(\d{2})$")
_RE_CASE = re.compile(r"^Case(\d+)$")


# ---------------------------------------------------------------------------
# Path and datetime helpers
# ---------------------------------------------------------------------------
def _parse_path_key(seq_path: Path, root: Path) -> tuple[str, int, str] | None:
    """
    Extract (recording_date, case_no, camera_name) from a SEQ file path.

    Expects: root / DATA_YY-MM-DD / CaseN / CameraName / <file>.seq
    Returns None if the structure does not match.
    """
    try:
        parts = seq_path.relative_to(root).parts
    except ValueError:
        return None
    if len(parts) < 3:
        return None
    data_m = _RE_DATA.match(parts[0])
    case_m = _RE_CASE.match(parts[1])
    if not data_m or not case_m:
        return None
    yy, mm, dd = data_m.groups()
    yyyy = f"20{yy}" if int(yy) <= 69 else f"19{yy}"
    return f"{yyyy}-{mm}-{dd}", int(case_m.group(1)), parts[2]


def _unix_to_iso(ts: float | None) -> str | None:
    """Convert a Unix float timestamp to an ISO-8601 UTC string, or None."""
    if ts is None or ts == 0:
        return None
    try:
        dt = datetime.fromtimestamp(ts, tz=timezone.utc)
        return dt.strftime("%Y-%m-%d %H:%M:%S.%f")
    except (OSError, OverflowError, ValueError):
        return None


# ---------------------------------------------------------------------------
# SEQ header parser
# ---------------------------------------------------------------------------
def parse_seq_header(seq_path: Path) -> dict | None:
    """
    Read and decode the 1024-byte SEQ header.

    Returns a dict of fields, or None if the file is too short, unreadable,
    or fails the magic check (corrupt / non-SEQ file).
    """
    try:
        with open(seq_path, "rb") as f:
            hdr = f.read(SEQ_HEADER_SIZE)
    except OSError:
        return None

    if len(hdr) < SEQ_HEADER_SIZE:
        return None

    magic = struct.unpack_from("<I", hdr, 0)[0]
    if magic != SEQ_MAGIC:
        return None

    # Description field: 512 bytes of UTF-16 LE, null-terminated.
    # Decode to Unicode, split on the null character, take the first segment.
    raw_desc  = hdr[OFF_DESCRIPTION : OFF_DESCRIPTION + OFF_DESCRIPTION_LEN]
    try:
        description = raw_desc.decode("utf-16-le", errors="replace").split("\x00")[0]
    except Exception:
        description = ""

    width             = struct.unpack_from("<I", hdr, OFF_WIDTH)[0]
    height            = struct.unpack_from("<I", hdr, OFF_HEIGHT)[0]
    allocated_frames  = struct.unpack_from("<I", hdr, OFF_ALLOCATED_FRAMES)[0]
    fps               = struct.unpack_from("<d", hdr, OFF_FPS)[0]
    compression_fmt   = struct.unpack_from("<I", hdr, OFF_COMPRESSION_FMT)[0]
    rec_timestamp     = struct.unpack_from("<I", hdr, OFF_REC_TIMESTAMP)[0]
    exposure_ns       = struct.unpack_from("<I", hdr, OFF_EXPOSURE_NS)[0]

    return {
        "description":      description,
        "width":            width,
        "height":           height,
        "allocated_frames": allocated_frames,
        "fps":              round(fps, 6),
        "compression_fmt":  compression_fmt,
        "rec_timestamp":    rec_timestamp,
        "exposure_ns":      exposure_ns,
    }


# ---------------------------------------------------------------------------
# IDX parser
# ---------------------------------------------------------------------------
def _decode_timestamps(u32: np.ndarray, n_records: int) -> np.ndarray:
    """
    Extract per-frame timestamps from a flat uint32 view of IDX data.

    Each record has ts_sec at index 3 and ts_sub at index 4 (within the 8-uint32
    stride).  ts_sub packs milliseconds in bits 15-0 and microseconds in bits
    31-16.

    Returns a float64 array of shape (n_records,) with full Unix timestamps.
    """
    ts_sec = u32[IDX_U32_TS_SEC :: IDX_UINT32_PER_RECORD][:n_records].astype(np.float64)
    ts_sub = u32[IDX_U32_TS_SUB :: IDX_UINT32_PER_RECORD][:n_records]
    ms     = (ts_sub & 0xFFFF).astype(np.float64)
    us     = ((ts_sub >> 16) & 0xFFFF).astype(np.float64)
    return ts_sec + ms / 1_000.0 + us / 1_000_000.0


def parse_idx(idx_path: Path, fps: float | None = None) -> dict | None:
    """
    Parse an IDX file and return drop statistics plus timing/sync metrics.

    Parameters
    ----------
    idx_path : Path
        Path to the .seq.idx file.
    fps : float | None
        Frame rate from the SEQ header, used to compute expected_duration.
        Pass None to skip expected_duration / time_drift_ms.

    Returns
    -------
    None
        IDX file does not exist.
    dict
        Always contains all output keys; timing keys are None on error/empty.
    """
    _TIMING_NONE = {
        "first_frame_time":  None,
        "last_frame_time":   None,
        "actual_duration":   None,
        "expected_duration": None,
        "time_drift_ms":     None,
        "max_time_gap_ms":   None,
    }

    if not idx_path.exists():
        return None

    try:
        raw = idx_path.read_bytes()
    except OSError as exc:
        return {"error": str(exc), "idx_frames": 0, "dropped_frames": 0,
                "drop_rate": 0.0, "first_frame_no": None, "last_frame_no": None,
                "n_duplicates": 0, "n_counter_resets": 0, "frame_span": None,
                **_TIMING_NONE}

    n_records = len(raw) // IDX_RECORD_SIZE
    if n_records == 0:
        return {"error": None, "idx_frames": 0, "dropped_frames": 0,
                "drop_rate": 0.0, "first_frame_no": None, "last_frame_no": None,
                "n_duplicates": 0, "n_counter_resets": 0, "frame_span": None,
                **_TIMING_NONE}

    # View the raw bytes as a flat uint32 array.
    # Each 32-byte record = 8 uint32s; fields are at fixed stride offsets.
    u32 = np.frombuffer(raw[: n_records * IDX_RECORD_SIZE], dtype="<u4")

    # --- frame_number (index 7) ---
    frame_numbers = u32[IDX_FRAME_NO_U32_IDX :: IDX_UINT32_PER_RECORD][:n_records]

    diffs = np.diff(frame_numbers.astype(np.int64))
    # diff == 0  → duplicate (should not happen)
    # diff == 1  → normal increment
    # diff >  1  → (diff - 1) frames were dropped
    # diff <  0  → counter reset or wrap-around
    n_duplicates     = int(np.sum(diffs == 0))
    n_counter_resets = int(np.sum(diffs < 0))
    drop_diffs       = diffs[diffs > 1]
    dropped_frames   = int(np.sum(drop_diffs - 1))

    expected_total = n_records + dropped_frames
    drop_rate      = dropped_frames / expected_total if expected_total > 0 else 0.0

    # --- timestamps (ts_sec at index 3, ts_sub at index 4) ---
    timestamps = _decode_timestamps(u32, n_records)

    first_frame_time = float(timestamps[0])
    last_frame_time  = float(timestamps[-1])
    actual_duration  = last_frame_time - first_frame_time

    if fps and fps > 0 and n_records > 0:
        expected_duration = n_records / fps
        time_drift_ms     = (actual_duration - expected_duration) * 1_000.0
    else:
        expected_duration = None
        time_drift_ms     = None

    if len(timestamps) > 1:
        time_diffs_ms    = np.diff(timestamps) * 1_000.0
        max_time_gap_ms  = float(np.max(time_diffs_ms))
    else:
        max_time_gap_ms  = None

    return {
        "error":             None,
        "idx_frames":        n_records,
        "dropped_frames":    dropped_frames,
        "drop_rate":         drop_rate,
        "first_frame_no":    int(frame_numbers[0]),
        "last_frame_no":     int(frame_numbers[-1]),
        "frame_span":        int(frame_numbers[-1]) - int(frame_numbers[0]),
        "n_duplicates":      n_duplicates,
        "n_counter_resets":  n_counter_resets,
        "first_frame_time":  first_frame_time,
        "last_frame_time":   last_frame_time,
        "actual_duration":   actual_duration,
        "expected_duration": expected_duration,
        "time_drift_ms":     time_drift_ms,
        "max_time_gap_ms":   max_time_gap_ms,
    }


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------
def _load_existing_keys(db_path: str) -> set[tuple[str, int, str]]:
    """
    Return the set of (recording_date, case_no, camera_name) tuples already
    present in seq_enriched.  Returns an empty set if the table does not
    exist or the DB is unreachable.
    """
    try:
        conn = sqlite3.connect(db_path)
        try:
            rows = conn.execute(
                f'SELECT recording_date, case_no, camera_name FROM "{SEQ_ANALYSIS_TABLE}"'
            ).fetchall()
            return {(r[0], int(r[1]), r[2]) for r in rows}
        except sqlite3.OperationalError:
            # Table does not exist yet
            return set()
        finally:
            conn.close()
    except Exception:
        return set()


# ---------------------------------------------------------------------------
# Directory scan
# ---------------------------------------------------------------------------
def analyze_directory(root: Path, skip_keys: set | None = None) -> pd.DataFrame:
    """
    Scan *root* recursively for .seq files; return one-row-per-file DataFrame.

    Files whose path contains a 'JUNK' component (case-insensitive) are skipped.
    Files whose (recording_date, case_no, camera_name) key already appears in
    *skip_keys* are also skipped (used to avoid re-processing known entries).
    Each row includes recording_date / case_no / camera_name parsed from the
    directory structure, and ISO-format datetime strings for the first and last
    frame timestamps.
    """
    all_seq = sorted(root.rglob("*.seq"))

    # Filter out JUNK paths before counting
    seq_files = [
        p for p in all_seq
        if "JUNK" not in (part.upper() for part in p.parts)
    ]
    skipped_junk = len(all_seq) - len(seq_files)

    # Filter out files already in the DB
    if skip_keys:
        new_seq_files = []
        for p in seq_files:
            key = _parse_path_key(p, root)
            if key is None or key not in skip_keys:
                new_seq_files.append(p)
        skipped_db = len(seq_files) - len(new_seq_files)
        seq_files = new_seq_files
    else:
        skipped_db = 0

    if not seq_files:
        msg = "[✓] No new .seq files to process"
        if skipped_db:
            msg += f" ({skipped_db} already in DB)"
        print(msg)
        return pd.DataFrame()

    total = len(seq_files)
    suffix_parts = []
    if skipped_junk:
        suffix_parts.append(f"{skipped_junk} JUNK skipped")
    if skipped_db:
        suffix_parts.append(f"{skipped_db} already in DB")
    suffix = f"  ({', '.join(suffix_parts)})" if suffix_parts else ""
    print(f"Found {total} new .seq files — scanning …{suffix}")

    rows = []
    for i, seq_path in enumerate(seq_files, 1):
        if i % 50 == 0 or i == total:
            print(f"  {i}/{total}\r", end="", flush=True)

        idx_path = Path(str(seq_path) + ".idx")
        hdr      = parse_seq_header(seq_path)
        fps      = hdr["fps"] if hdr else None
        idx      = parse_idx(idx_path, fps=fps)

        # Extract (recording_date, case_no, camera_name) from path
        key = _parse_path_key(seq_path, root)

        try:
            rel = seq_path.relative_to(root)
        except ValueError:
            rel = seq_path

        row: dict = {
            "recording_date": key[0] if key else None,
            "case_no":        key[1] if key else None,
            "camera_name":    key[2] if key else None,
            "file":           str(rel),
            "size_mb":        seq_path.stat().st_size / (1024 ** 2),
            "has_idx":        idx_path.exists(),
            "header_ok":      hdr is not None,
        }

        # Header fields (None when corrupt/unreadable)
        for field in ("description", "width", "height",
                      "allocated_frames", "fps", "compression_fmt",
                      "rec_timestamp", "exposure_ns"):
            row[field] = hdr[field] if hdr else None

        # IDX fields (None when IDX absent)
        for field in ("idx_frames", "dropped_frames", "drop_rate", "first_frame_no",
                      "last_frame_no", "frame_span", "n_duplicates", "n_counter_resets",
                      "first_frame_time", "last_frame_time", "actual_duration",
                      "expected_duration", "time_drift_ms", "max_time_gap_ms"):
            row[field] = idx[field] if idx else None

        # Human-readable datetime strings derived from first/last frame timestamps
        row["first_frame_datetime"] = _unix_to_iso(row["first_frame_time"])
        row["last_frame_datetime"]  = _unix_to_iso(row["last_frame_time"])

        # IDX cache provenance (used by 3_seq_to_mp4_convert.py for staleness check)
        if idx_path.exists():
            try:
                row["idx_file_size"] = idx_path.stat().st_size
                row["idx_cached_at"] = datetime.now(timezone.utc).isoformat() if idx else None
            except OSError:
                row["idx_file_size"] = None
                row["idx_cached_at"] = None
        else:
            row["idx_file_size"] = None
            row["idx_cached_at"] = None

        rows.append(row)

    print()  # clear \r progress line
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Database writer
# ---------------------------------------------------------------------------
# Ordered list of (column_name, sql_type) for every data column we persist.
# The three key columns (recording_date, case_no, camera_name) are handled
# separately as the PRIMARY KEY.
_DB_COLUMNS: list[tuple[str, str]] = [
    ("file",                 "TEXT"),
    ("size_mb",              "REAL"),
    ("has_idx",              "INTEGER"),   # 0/1 boolean
    ("header_ok",            "INTEGER"),
    ("description",          "TEXT"),
    ("width",                "INTEGER"),
    ("height",               "INTEGER"),
    ("allocated_frames",     "INTEGER"),
    ("fps",                  "REAL"),
    ("compression_fmt",      "INTEGER"),
    ("rec_timestamp",        "INTEGER"),
    ("exposure_ns",          "INTEGER"),
    ("idx_frames",           "INTEGER"),
    ("dropped_frames",       "INTEGER"),
    ("drop_rate",            "REAL"),
    ("first_frame_no",       "INTEGER"),
    ("last_frame_no",        "INTEGER"),
    ("frame_span",           "INTEGER"),
    ("n_duplicates",         "INTEGER"),
    ("n_counter_resets",     "INTEGER"),
    ("first_frame_time",     "REAL"),
    ("last_frame_time",      "REAL"),
    ("first_frame_datetime", "TEXT"),
    ("last_frame_datetime",  "TEXT"),
    ("actual_duration",      "REAL"),
    ("expected_duration",    "REAL"),
    ("time_drift_ms",        "REAL"),
    ("max_time_gap_ms",      "REAL"),
    ("idx_file_size",        "INTEGER"),
    ("idx_cached_at",        "TEXT"),
]


def _ensure_analysis_table(conn: sqlite3.Connection) -> None:
    """Create seq_enriched if absent, then add any missing columns."""
    cur = conn.cursor()
    cur.execute(f"""
        CREATE TABLE IF NOT EXISTS "{SEQ_ANALYSIS_TABLE}" (
            recording_date  TEXT    NOT NULL,
            case_no         INTEGER NOT NULL,
            camera_name     TEXT    NOT NULL,
            PRIMARY KEY (recording_date, case_no, camera_name)
        )
    """)
    conn.commit()

    existing_cols: set[str] = set()
    for row in cur.execute(f'PRAGMA table_info("{SEQ_ANALYSIS_TABLE}")'):
        existing_cols.add(row[1])

    _DROPPED_COLS = {"unk_640", "unk_656", "unk_660", "unk_664", "delta_664"}
    for col in _DROPPED_COLS & existing_cols:
        cur.execute(f'ALTER TABLE "{SEQ_ANALYSIS_TABLE}" DROP COLUMN {col}')

    for col_name, col_type in _DB_COLUMNS:
        if col_name not in existing_cols:
            cur.execute(
                f'ALTER TABLE "{SEQ_ANALYSIS_TABLE}" ADD COLUMN {col_name} {col_type}'
            )
    conn.commit()


def write_to_db(df: pd.DataFrame, db_path: str) -> None:
    """Upsert all rows from *df* into seq_enriched in ScalpelDatabase."""
    # Only write rows where we have a valid primary key
    writable = df[df["recording_date"].notna() & df["case_no"].notna() & df["camera_name"].notna()].copy()
    skipped  = len(df) - len(writable)
    if skipped:
        print(f"  [WARN] {skipped} row(s) skipped — could not parse recording_date/case_no/camera_name from path")

    if writable.empty:
        print("  Nothing to write.")
        return

    col_names  = [c for c, _ in _DB_COLUMNS]
    all_cols   = ["recording_date", "case_no", "camera_name"] + col_names
    placeholders = ", ".join("?" * len(all_cols))
    update_set   = ", ".join(f"{c} = excluded.{c}" for c in col_names)

    sql = f"""
        INSERT INTO "{SEQ_ANALYSIS_TABLE}"
        ({", ".join(all_cols)})
        VALUES ({placeholders})
        ON CONFLICT(recording_date, case_no, camera_name)
        DO UPDATE SET {update_set}
    """

    conn = sqlite3.connect(db_path)
    try:
        _ensure_analysis_table(conn)
        cur = conn.cursor()
        rows_written = 0
        for _, row in writable.iterrows():
            values = [row.get("recording_date"), row.get("case_no"), row.get("camera_name")]
            for col in col_names:
                val = row.get(col)
                # Convert numpy scalars / booleans to Python natives for sqlite3
                if isinstance(val, (np.integer,)):
                    val = int(val)
                elif isinstance(val, (np.floating,)):
                    val = None if np.isnan(val) else float(val)
                elif isinstance(val, bool):
                    val = int(val)
                elif pd.isna(val) if not isinstance(val, str) else False:
                    val = None
                values.append(val)
            cur.execute(sql, values)
            rows_written += 1
        conn.commit()
        print(f"  Wrote {rows_written} rows → {SEQ_ANALYSIS_TABLE} in {db_path}")
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Report helpers
# ---------------------------------------------------------------------------
_BAR_WIDTH = 30   # max length of ASCII bar in distribution tables


def _bar(fraction: float) -> str:
    filled = max(1, round(fraction * _BAR_WIDTH))
    return "█" * filled + "░" * (_BAR_WIDTH - filled)


def _section(title: str) -> None:
    print("\n" + "─" * 80)
    print(title)
    print("─" * 80)


def _print_distribution(series: pd.Series, label: str) -> None:
    """Print value counts with a horizontal bar chart."""
    counts = series.value_counts().sort_index()
    total  = counts.sum()
    print(f"\n  {label}  (N={total})")
    print(f"  {'Value':>14}  {'Count':>6}  {'%':>6}  Bar")
    print(f"  {'─'*14}  {'─'*6}  {'─'*6}  {'─'*_BAR_WIDTH}")
    for val, cnt in counts.items():
        pct = cnt / total
        print(f"  {val:>14}  {cnt:>6}  {100*pct:5.1f}%  {_bar(pct)}")


def _print_correlation_table(df: pd.DataFrame, group_col: str, label: str) -> None:
    """Group df by group_col and show drop statistics per group value."""
    grouped = (
        df.groupby(group_col)
        .agg(
            files        =("dropped_frames", "count"),
            total_drops  =("dropped_frames", "sum"),
            mean_drops   =("dropped_frames", "mean"),
            files_w_drops=("dropped_frames", lambda x: (x > 0).sum()),
        )
        .reset_index()
    )
    grouped["drop_pct"] = 100.0 * grouped["files_w_drops"] / grouped["files"]

    print(f"\n  {label}")
    hdr = (f"  {'Value':>14}  {'Files':>6}  {'w/drops':>7}  "
           f"{'drop_pct':>9}  {'mean_drops':>10}  {'total_drops':>12}")
    sep = (f"  {'─'*14}  {'─'*6}  {'─'*7}  "
           f"{'─'*9}  {'─'*10}  {'─'*12}")
    print(hdr)
    print(sep)
    for _, r in grouped.iterrows():
        print(
            f"  {r[group_col]:>14}  {r['files']:>6}  {r['files_w_drops']:>7}  "
            f"  {r['drop_pct']:>7.1f}%  {r['mean_drops']:>10.1f}  {r['total_drops']:>12.0f}"
        )


# ---------------------------------------------------------------------------
# Main report
# ---------------------------------------------------------------------------
def print_report(df: pd.DataFrame) -> None:
    if df.empty:
        print("No data to report.")
        return

    valid    = df[df["header_ok"]].copy()
    invalid  = df[~df["header_ok"]]
    with_idx = valid[valid["has_idx"] & valid["dropped_frames"].notna()].copy()

    print("\n" + "=" * 80)
    print("SEQ FIELD ANALYSIS — FRAME DROPS & TIMING REPORT")
    print("=" * 80)
    print(f"\n  Total .seq files scanned  : {len(df)}")
    print(f"  Valid headers             : {len(valid)}")
    print(f"  Corrupt / unreadable      : {len(invalid)}")
    print(f"  Files with IDX            : {valid['has_idx'].sum()}")
    print(f"  Files without IDX         : {(~valid['has_idx']).sum()}")

    if len(invalid) > 0:
        print(f"\n  Corrupt files:")
        for _, r in invalid.iterrows():
            print(f"    {r['file']}")

    # ------------------------------------------------------------------
    # 1. Frame-drop summary
    # ------------------------------------------------------------------
    _section("1. FRAME DROP SUMMARY (files with IDX)")

    if len(with_idx) == 0:
        print("  No IDX files found — cannot compute drop statistics.")
    else:
        has_drops = with_idx[with_idx["dropped_frames"] > 0]
        no_drops  = with_idx[with_idx["dropped_frames"] == 0]
        print(f"\n  Files analysed          : {len(with_idx)}")
        print(f"  Files with ≥1 drop      : {len(has_drops)}  ({100*len(has_drops)/len(with_idx):.1f}%)")
        print(f"  Files with no drops     : {len(no_drops)}")

        if len(has_drops) > 0:
            print(f"\n  Drop statistics across files with ≥1 drop:")
            print(f"    Min dropped frames  : {has_drops['dropped_frames'].min():.0f}")
            print(f"    Max dropped frames  : {has_drops['dropped_frames'].max():.0f}")
            print(f"    Mean dropped frames : {has_drops['dropped_frames'].mean():.1f}")
            print(f"    Median              : {has_drops['dropped_frames'].median():.0f}")
            print(f"    Max drop rate       : {has_drops['drop_rate'].max():.4%}")
            print(f"    Total dropped       : {has_drops['dropped_frames'].sum():.0f}")

        resets = with_idx[with_idx["n_counter_resets"] > 0]
        if len(resets) > 0:
            print(f"\n  [WARNING] {len(resets)} file(s) have frame_number counter resets:")
            for _, r in resets.iterrows():
                print(f"    resets={r['n_counter_resets']}  {r['file']}")

    # ------------------------------------------------------------------
    # 2. Anomaly table
    # ------------------------------------------------------------------
    _section("2. ANOMALY FILES (dropped_frames > 0 OR counter resets > 0)")

    if len(with_idx) == 0:
        print("  No IDX data.")
    else:
        anomalies = with_idx[
            (with_idx["dropped_frames"] > 0) |
            (with_idx["n_counter_resets"] > 0)
        ].sort_values("dropped_frames", ascending=False)

        if len(anomalies) == 0:
            print("  None — all IDX-verified files are clean.")
        else:
            show_cols = [
                "file", "allocated_frames", "idx_frames",
                "dropped_frames", "drop_rate", "n_counter_resets",
            ]
            display = anomalies[show_cols].copy()
            display["drop_rate"] = display["drop_rate"].map("{:.4%}".format)
            # Truncate long file paths for readability
            display["file"] = display["file"].str[-60:]
            pd.set_option("display.max_colwidth", 62)
            pd.set_option("display.width", 140)
            print(display.to_string(index=False))

    # ------------------------------------------------------------------
    # 3. Timing & synchronization analysis
    # ------------------------------------------------------------------
    _section("3. TIMING & SYNCHRONIZATION ANALYSIS (files with IDX)")

    timing_cols = ["time_drift_ms", "max_time_gap_ms", "actual_duration", "expected_duration"]
    has_timing  = with_idx[with_idx["time_drift_ms"].notna()] if len(with_idx) > 0 else pd.DataFrame()

    if has_timing.empty:
        print("  No timing data available (IDX files missing or fps=0).")
    else:
        print(f"\n  Files with timing data : {len(has_timing)}")

        for col, label, unit in [
            ("time_drift_ms",    "Time drift (actual − expected duration)", "ms"),
            ("max_time_gap_ms",  "Max inter-frame gap",                     "ms"),
            ("actual_duration",  "Actual duration",                         "s"),
        ]:
            s = has_timing[col].dropna()
            if s.empty:
                continue
            print(f"\n  {label} [{unit}]:")
            print(f"    Min     : {s.min():>12.3f}")
            print(f"    Max     : {s.max():>12.3f}")
            print(f"    Mean    : {s.mean():>12.3f}")
            print(f"    Median  : {s.median():>12.3f}")
            print(f"    Std dev : {s.std():>12.3f}")

        # Expected inter-frame gap at nominal fps (for reference)
        # Flag files where max_time_gap exceeds 3× the nominal frame interval
        def _nominal_gap_ms(row: pd.Series) -> float | None:
            return (1000.0 / row["fps"]) if row["fps"] and row["fps"] > 0 else None

        has_timing = has_timing.copy()
        has_timing["nominal_gap_ms"]  = has_timing.apply(_nominal_gap_ms, axis=1)
        has_timing["gap_ratio"]        = (
            has_timing["max_time_gap_ms"] / has_timing["nominal_gap_ms"]
        )

        large_gaps   = has_timing[has_timing["gap_ratio"] > 3].sort_values(
            "gap_ratio", ascending=False
        )
        large_drift  = has_timing[has_timing["time_drift_ms"].abs() > 1000].sort_values(
            "time_drift_ms", key=abs, ascending=False
        )

        print(f"\n  Files with max_time_gap > 3× nominal frame interval : {len(large_gaps)}")
        if not large_gaps.empty:
            show = large_gaps[["file", "fps", "nominal_gap_ms",
                               "max_time_gap_ms", "gap_ratio"]].head(20)
            show = show.copy()
            show["file"] = show["file"].str[-55:]
            print(show.to_string(index=False, float_format="{:.2f}".format))

        print(f"\n  Files with |time_drift| > 1 s : {len(large_drift)}")
        if not large_drift.empty:
            show = large_drift[["file", "fps", "actual_duration",
                                "expected_duration", "time_drift_ms"]].head(20)
            show = show.copy()
            show["file"] = show["file"].str[-55:]
            print(show.to_string(index=False, float_format="{:.3f}".format))

    print("\n" + "=" * 80)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main() -> None:
    if len(sys.argv) >= 2:
        root = Path(sys.argv[1])
    elif _CFG_SEQ_ROOT:
        root = Path(_CFG_SEQ_ROOT)
    else:
        print("Usage: python analyze_seq_fields.py <directory> [db_path]")
        print("       (or set SEQ_ROOT / DB_PATH in config.py)")
        sys.exit(1)

    db_path: str
    if len(sys.argv) >= 3:
        db_path = sys.argv[2]
    elif _CFG_DB_PATH:
        db_path = _CFG_DB_PATH
    else:
        db_path = str(_SCRIPT_DIR / "ScalpelDatabase.sqlite")
        print(f"[WARN] No DB path configured — using {db_path}")

    if not root.exists():
        print(f"[!] Directory not found: {root}")
        sys.exit(1)

    print(f"Loading existing DB entries from: {db_path}")
    skip_keys = _load_existing_keys(db_path)
    if skip_keys:
        print(f"  {len(skip_keys)} (recording_date, case_no, camera_name) entries already in DB — will skip")

    df = analyze_directory(root, skip_keys=skip_keys)
    if df.empty:
        sys.exit(0)

    print_report(df)

    print(f"\nWriting results to database: {db_path}")
    write_to_db(df, db_path)


if __name__ == "__main__":
    main()
