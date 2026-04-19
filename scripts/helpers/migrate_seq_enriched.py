"""
One-shot migration: combine `seq_field_analysis` + `idx_cache` → `seq_enriched`.

Target schema = all 28 data columns of seq_field_analysis + two new columns
merged in from idx_cache: `idx_file_size` and `idx_cached_at`.

Join: `idx_cache.idx_path` is an absolute Windows path ending with the
relative `seq_field_analysis.file` + ".idx", so we match on suffix.

Idempotent: skipped if `seq_enriched` already exists.

Usage:
    python scripts/helpers/migrate_seq_enriched.py [db_path]
"""

import sys
import sqlite3
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _SCRIPT_DIR.parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

try:
    from config import get_db_path as _get_db_path
    _DEFAULT_DB: str | None = _get_db_path()
except ImportError:
    _DEFAULT_DB = None


CREATE_SEQ_ENRICHED_SQL = """
CREATE TABLE seq_enriched (
    recording_date       TEXT    NOT NULL,
    case_no              INTEGER NOT NULL,
    camera_name          TEXT    NOT NULL,
    file                 TEXT,
    size_mb              REAL,
    has_idx              INTEGER,
    header_ok            INTEGER,
    description          TEXT,
    width                INTEGER,
    height               INTEGER,
    allocated_frames     INTEGER,
    fps                  REAL,
    compression_fmt      INTEGER,
    rec_timestamp        INTEGER,
    exposure_ns          INTEGER,
    idx_frames           INTEGER,
    dropped_frames       INTEGER,
    drop_rate            REAL,
    first_frame_no       INTEGER,
    last_frame_no        INTEGER,
    frame_span           INTEGER,
    n_duplicates         INTEGER,
    n_counter_resets     INTEGER,
    first_frame_time     REAL,
    last_frame_time      REAL,
    first_frame_datetime TEXT,
    last_frame_datetime  TEXT,
    actual_duration      REAL,
    expected_duration    REAL,
    time_drift_ms        REAL,
    max_time_gap_ms      REAL,
    idx_file_size        INTEGER,
    idx_cached_at        TEXT,
    PRIMARY KEY (recording_date, case_no, camera_name)
)
"""

COPY_COLUMNS = [
    "recording_date", "case_no", "camera_name",
    "file", "size_mb", "has_idx", "header_ok", "description",
    "width", "height", "allocated_frames", "fps", "compression_fmt",
    "rec_timestamp", "exposure_ns", "idx_frames", "dropped_frames",
    "drop_rate", "first_frame_no", "last_frame_no", "frame_span",
    "n_duplicates", "n_counter_resets", "first_frame_time", "last_frame_time",
    "first_frame_datetime", "last_frame_datetime", "actual_duration",
    "expected_duration", "time_drift_ms", "max_time_gap_ms",
]


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    return row is not None


def migrate(db_path: str) -> None:
    conn = sqlite3.connect(db_path)
    try:
        has_enriched = _table_exists(conn, "seq_enriched")
        has_sfa = _table_exists(conn, "seq_field_analysis")
        has_ic = _table_exists(conn, "idx_cache")

        if has_enriched and not has_sfa and not has_ic:
            n = conn.execute("SELECT COUNT(*) FROM seq_enriched").fetchone()[0]
            print(f"[OK] Already migrated - seq_enriched has {n} rows, old tables gone.")
            return

        if has_enriched:
            print("[ERR] seq_enriched already exists alongside old tables - refusing to "
                  "overwrite. Inspect manually or drop seq_enriched first.")
            sys.exit(1)

        if not has_sfa:
            print("[ERR] seq_field_analysis not found - nothing to migrate.")
            sys.exit(1)

        sfa_count = conn.execute("SELECT COUNT(*) FROM seq_field_analysis").fetchone()[0]
        ic_count = conn.execute("SELECT COUNT(*) FROM idx_cache").fetchone()[0] if has_ic else 0
        print(f"[i] Source row counts: seq_field_analysis={sfa_count}, idx_cache={ic_count}")

        conn.execute("BEGIN")

        conn.execute(CREATE_SEQ_ENRICHED_SQL)

        cols_csv = ", ".join(COPY_COLUMNS)
        conn.execute(f"""
            INSERT INTO seq_enriched ({cols_csv})
            SELECT {cols_csv} FROM seq_field_analysis
        """)

        if has_ic:
            # Join idx_cache → seq_enriched by path suffix. Both use backslash
            # separators on this box, so a single suffix match is enough.
            conn.execute("""
                UPDATE seq_enriched
                SET idx_file_size = (
                        SELECT ic.idx_file_size FROM idx_cache ic
                        WHERE ic.idx_path LIKE '%' || seq_enriched.file || '.idx'
                        LIMIT 1
                    ),
                    idx_cached_at = (
                        SELECT ic.cached_at FROM idx_cache ic
                        WHERE ic.idx_path LIKE '%' || seq_enriched.file || '.idx'
                        LIMIT 1
                    )
                WHERE file IS NOT NULL
            """)

        conn.execute("DROP TABLE IF EXISTS idx_cache")
        conn.execute("DROP TABLE seq_field_analysis")
        conn.commit()

        n_total = conn.execute("SELECT COUNT(*) FROM seq_enriched").fetchone()[0]
        n_cached = conn.execute(
            "SELECT COUNT(*) FROM seq_enriched WHERE idx_file_size IS NOT NULL"
        ).fetchone()[0]
        print(f"[OK] Migration complete: seq_enriched has {n_total} rows "
              f"({n_cached} with idx_file_size populated).")
        if has_ic and n_cached != ic_count:
            print(f"[WARN] Expected {ic_count} cached rows from idx_cache, matched {n_cached}. "
                  f"Likely path-format mismatch - inspect rows where idx_file_size IS NULL "
                  f"but has_idx=1.")
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    db_path = sys.argv[1] if len(sys.argv) > 1 else _DEFAULT_DB
    if not db_path:
        print("Usage: python migrate_seq_enriched.py [db_path]")
        sys.exit(1)
    if not Path(db_path).exists():
        print(f"[X] DB not found: {db_path}")
        sys.exit(1)
    migrate(db_path)
