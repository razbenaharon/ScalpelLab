#!/usr/bin/env python3
"""Import finalized BORIS and monitor CSV analyses into ScalpelDatabase.sqlite.

The expected input layout is:

    ANALYSES_ROOT/DATA_YY-MM-DD/CaseN/Boris/*_standardized.csv
    ANALYSES_ROOT/DATA_YY-MM-DD/CaseN/Monitor/motior_data.csv

BORIS imports replace the existing ``boris_events`` contents. Monitor imports
are idempotent per case: existing monitor rows for an imported case are deleted
and inserted again from the source CSV.
"""

from __future__ import annotations

import argparse
import csv
import logging
import os
import re
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

try:
    from config import get_analyses_root, get_db_path
except ImportError:
    get_analyses_root = None
    get_db_path = None


DEFAULT_INPUT_ROOT = (
    Path(get_analyses_root()) if get_analyses_root else Path(r"F:\Room_8_Data\Analyses\Case_Analyses_synced")
)
DEFAULT_DB_PATH = Path(get_db_path()) if get_db_path else PROJECT_ROOT / "ScalpelDatabase.sqlite"

DATA_RE = re.compile(r"^DATA_(?P<yy>\d{2})-(?P<mm>\d{2})-(?P<dd>\d{2})$", re.IGNORECASE)
CASE_RE = re.compile(r"^Case(?P<case_no>\d+)$", re.IGNORECASE)
BORIS_FILE_RE = re.compile(
    r"^(?P<yy>\d{2})_(?P<mm>\d{2})_(?P<dd>\d{2})-case(?P<case_no>\d+)_standardized\.csv$",
    re.IGNORECASE,
)
FRAME_RE = re.compile(r"(\d+)")

BORIS_REQUIRED_COLUMNS = {"Subject", "Behavior", "Behavior type", "Time"}
VALID_BEHAVIOR_TYPES = {"START", "STOP", "POINT"}

VITAL_COLUMNS = {
    "HR_bpm": "hr_bpm",
    "SpO2_pct": "spo2_pct",
    "PulseIndex": "pulse_index",
    "PR_bpm": "pr_bpm",
    "EtCO2_mmHg": "etco2_mmhg",
    "RR_bpm": "rr_bpm",
    "FiCO2_mmHg": "fico2_mmhg",
    "NIBP_Sys_mmHg": "nibp_sys_mmhg",
    "NIBP_Dia_mmHg": "nibp_dia_mmhg",
    "NIBP_Mean_mmHg": "nibp_mean_mmhg",
    "Temp_C": "temp_c",
}
ALERT_COLUMNS = {
    "HR_alert": "hr_alert",
    "SpO2_alert": "spo2_alert",
    "PulseIndex_alert": "pulse_index_alert",
    "PR_alert": "pr_alert",
    "EtCO2_alert": "etco2_alert",
    "RR_alert": "rr_alert",
    "FiCO2_alert": "fico2_alert",
    "NIBP_Sys_alert": "nibp_sys_alert",
    "NIBP_Dia_alert": "nibp_dia_alert",
    "NIBP_Mean_alert": "nibp_mean_alert",
    "Temp_alert": "temp_alert",
}

CREATE_BORIS_EVENTS_SQL = """
CREATE TABLE IF NOT EXISTS boris_events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    recording_date TEXT,
    case_no INTEGER,
    subject TEXT,
    behavior TEXT,
    behavioral_category TEXT,
    behavior_type TEXT
        CHECK (behavior_type IS NULL OR behavior_type IN ('START', 'STOP', 'POINT')),
    modifier_1 TEXT,
    modifier_2 TEXT,
    modifier_3 TEXT,
    time_s REAL
        CHECK (time_s IS NULL OR time_s >= 0),
    absolute_timestamp TEXT,
    source_file TEXT NOT NULL,
    source_row_number INTEGER NOT NULL
        CHECK (source_row_number > 0),
    imported_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (source_file, source_row_number)
);
"""


def monitor_samples_sql() -> str:
    vital_defs = ",\n    ".join(f"{col} REAL" for col in VITAL_COLUMNS.values())
    alert_defs = ",\n    ".join(f"{col} TEXT" for col in ALERT_COLUMNS.values())
    return f"""
CREATE TABLE IF NOT EXISTS monitor_samples (
    recording_date TEXT NOT NULL,
    case_no INTEGER NOT NULL,
    sample_index INTEGER NOT NULL,
    frame TEXT,
    frame_no INTEGER,
    timestamp_text TEXT,
    elapsed_s REAL,
    source_file TEXT NOT NULL,
    imported_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    {vital_defs},
    {alert_defs},
    PRIMARY KEY (recording_date, case_no, sample_index)
);
"""


def monitor_summary_sql() -> str:
    vital_counts = ",\n    ".join(f"{col}_count INTEGER NOT NULL DEFAULT 0" for col in VITAL_COLUMNS.values())
    alert_counts = ",\n    ".join(f"{col}_count INTEGER NOT NULL DEFAULT 0" for col in ALERT_COLUMNS.values())
    return f"""
CREATE TABLE IF NOT EXISTS monitor_case_summary (
    recording_date TEXT NOT NULL,
    case_no INTEGER NOT NULL,
    source_file TEXT NOT NULL,
    sample_count INTEGER NOT NULL DEFAULT 0,
    duration_s REAL,
    imported_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    {vital_counts},
    {alert_counts},
    PRIMARY KEY (recording_date, case_no)
);
"""


@dataclass(frozen=True)
class CaseKey:
    recording_date: str
    case_no: int


@dataclass
class BorisFile:
    path: Path
    key: CaseKey
    source_file: str
    rows: list[dict[str, object]]


@dataclass
class MonitorFile:
    path: Path
    key: CaseKey
    source_file: str
    sample_count: int


@dataclass
class ScanStats:
    files: int = 0
    skipped_files: int = 0
    rows: int = 0
    invalid_rows: int = 0


def configure_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(level=level, format="%(levelname)s: %(message)s")


def clean_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.upper() == "NA":
        return None
    return text


def parse_float(value: object) -> float | None:
    text = clean_text(value)
    if text is None:
        return None
    return float(text)


def parse_frame_no(frame: str | None) -> int | None:
    text = clean_text(frame)
    if text is None:
        return None
    match = FRAME_RE.search(text)
    if not match:
        return None
    return int(match.group(1))


def table_columns(conn: sqlite3.Connection, table_name: str) -> set[str]:
    try:
        rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    except sqlite3.Error:
        return set()
    return {row[1] for row in rows}


def add_missing_columns(
    conn: sqlite3.Connection,
    table_name: str,
    columns: Iterable[tuple[str, str]],
) -> None:
    existing = table_columns(conn, table_name)
    for name, col_type in columns:
        if name not in existing:
            conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {name} {col_type}")


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute("DROP VIEW IF EXISTS cur_boris_intervals")
    conn.execute(CREATE_BORIS_EVENTS_SQL)
    add_missing_columns(
        conn,
        "boris_events",
        [
            ("recording_date", "TEXT"),
            ("case_no", "INTEGER"),
            ("behavioral_category", "TEXT"),
            ("absolute_timestamp", "TEXT"),
        ],
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS analysis_information (
            recording_date TEXT NOT NULL,
            case_no INTEGER NOT NULL,
            PRIMARY KEY (recording_date, case_no)
        )
        """
    )
    if "event_id" not in table_columns(conn, "analysis_information"):
        conn.execute(
            "ALTER TABLE analysis_information "
            "ADD COLUMN event_id INTEGER REFERENCES boris_events(event_id)"
        )
    conn.execute(monitor_samples_sql())
    conn.execute(monitor_summary_sql())
    add_missing_columns(
        conn,
        "monitor_samples",
        [
            ("frame", "TEXT"),
            ("frame_no", "INTEGER"),
            ("timestamp_text", "TEXT"),
            ("elapsed_s", "REAL"),
            ("source_file", "TEXT"),
            ("imported_at", "TEXT"),
            *[(col, "REAL") for col in VITAL_COLUMNS.values()],
            *[(col, "TEXT") for col in ALERT_COLUMNS.values()],
        ],
    )
    add_missing_columns(
        conn,
        "monitor_case_summary",
        [
            ("source_file", "TEXT"),
            ("sample_count", "INTEGER NOT NULL DEFAULT 0"),
            ("duration_s", "REAL"),
            ("imported_at", "TEXT"),
            *[(f"{col}_count", "INTEGER NOT NULL DEFAULT 0") for col in VITAL_COLUMNS.values()],
            *[(f"{col}_count", "INTEGER NOT NULL DEFAULT 0") for col in ALERT_COLUMNS.values()],
        ],
    )
    conn.commit()
    conn.execute("PRAGMA foreign_keys = ON")


def parse_case_from_path(path: Path, root: Path) -> CaseKey | None:
    try:
        rel = path.relative_to(root)
    except ValueError:
        return None
    parts = rel.parts
    if len(parts) < 4:
        return None
    data_match = DATA_RE.fullmatch(parts[0])
    case_match = CASE_RE.fullmatch(parts[1])
    if not data_match or not case_match:
        return None
    yy, mm, dd = data_match.group("yy"), data_match.group("mm"), data_match.group("dd")
    try:
        recording_date = datetime(2000 + int(yy), int(mm), int(dd)).strftime("%Y-%m-%d")
    except ValueError:
        return None
    return CaseKey(recording_date=recording_date, case_no=int(case_match.group("case_no")))


def source_name(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return path.name


def validate_boris_filename(path: Path, key: CaseKey) -> str | None:
    match = BORIS_FILE_RE.fullmatch(path.name)
    if not match:
        return "filename does not match *_standardized.csv"
    file_key = CaseKey(
        recording_date=f"20{match.group('yy')}-{match.group('mm')}-{match.group('dd')}",
        case_no=int(match.group("case_no")),
    )
    if file_key != key:
        return f"filename key {file_key.recording_date} Case{file_key.case_no} disagrees with path"
    return None


def read_boris_csv(path: Path) -> tuple[list[dict[str, str]], str | None]:
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames is None or not BORIS_REQUIRED_COLUMNS.issubset(reader.fieldnames):
                return [], "missing required BORIS columns"
            rows = [row for row in reader if any(clean_text(value) for value in row.values())]
    except OSError as exc:
        return [], f"could not read file: {exc}"
    if not rows:
        return [], "empty export"
    return rows, None


def normalize_boris_rows(
    key: CaseKey,
    source_file: str,
    raw_rows: list[dict[str, str]],
) -> tuple[list[dict[str, object]], int]:
    rows: list[dict[str, object]] = []
    invalid = 0
    for row_number, row in enumerate(raw_rows, start=2):
        try:
            time_s = parse_float(row.get("Time"))
        except ValueError:
            invalid += 1
            logging.warning("%s row %s skipped: invalid Time value", source_file, row_number)
            continue

        behavior_type = clean_text(row.get("Behavior type"))
        if behavior_type is not None:
            behavior_type = behavior_type.upper()
        if behavior_type not in VALID_BEHAVIOR_TYPES:
            invalid += 1
            logging.warning(
                "%s row %s skipped: unsupported Behavior type %r",
                source_file,
                row_number,
                behavior_type,
            )
            continue

        rows.append(
            {
                "recording_date": key.recording_date,
                "case_no": key.case_no,
                "subject": clean_text(row.get("Subject")),
                "behavior": clean_text(row.get("Behavior")),
                "behavioral_category": clean_text(row.get("Behavioral category")),
                "behavior_type": behavior_type,
                "modifier_1": clean_text(row.get("Modifier #1")),
                "modifier_2": clean_text(row.get("Modifier #2")),
                "modifier_3": clean_text(row.get("Modifier #3")),
                "time_s": time_s,
                "absolute_timestamp": clean_text(row.get("absolute_timestamp")),
                "source_file": source_file,
                "source_row_number": row_number,
            }
        )
    return rows, invalid


def scan_boris_files(root: Path) -> tuple[list[BorisFile], ScanStats]:
    stats = ScanStats()
    files: list[BorisFile] = []
    for path in sorted(root.rglob("*_standardized.csv")):
        if path.parent.name.lower() != "boris":
            continue
        key = parse_case_from_path(path, root)
        if key is None:
            stats.skipped_files += 1
            logging.warning("Skipping %s: cannot parse DATA/Case path", path)
            continue
        reason = validate_boris_filename(path, key)
        if reason:
            stats.skipped_files += 1
            logging.warning("Skipping %s: %s", path.name, reason)
            continue
        raw_rows, skip_reason = read_boris_csv(path)
        if skip_reason:
            stats.skipped_files += 1
            logging.warning("Skipping %s: %s", path.name, skip_reason)
            continue
        normalized, invalid = normalize_boris_rows(key, path.name, raw_rows)
        stats.invalid_rows += invalid
        if not normalized:
            stats.skipped_files += 1
            logging.warning("Skipping %s: no valid BORIS rows", path.name)
            continue
        files.append(BorisFile(path=path, key=key, source_file=path.name, rows=normalized))
        stats.files += 1
        stats.rows += len(normalized)
    return files, stats


def count_monitor_rows(path: Path) -> tuple[int, str | None]:
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames is None or "Frame" not in reader.fieldnames or "Timestamp" not in reader.fieldnames:
                return 0, "missing required monitor columns"
            return sum(1 for row in reader if any(clean_text(value) for value in row.values())), None
    except OSError as exc:
        return 0, f"could not read file: {exc}"


def scan_monitor_files(root: Path) -> tuple[list[MonitorFile], ScanStats]:
    stats = ScanStats()
    files: list[MonitorFile] = []
    for path in sorted(root.rglob("motior_data.csv")):
        if path.parent.name.lower() != "monitor":
            continue
        key = parse_case_from_path(path, root)
        if key is None:
            stats.skipped_files += 1
            logging.warning("Skipping %s: cannot parse DATA/Case path", path)
            continue
        rows, reason = count_monitor_rows(path)
        if reason:
            stats.skipped_files += 1
            logging.warning("Skipping %s: %s", path, reason)
            continue
        if rows == 0:
            stats.skipped_files += 1
            logging.warning("Skipping %s: no monitor rows", path)
            continue
        files.append(MonitorFile(path=path, key=key, source_file=source_name(path, root), sample_count=rows))
        stats.files += 1
        stats.rows += rows
    return files, stats


def import_boris(conn: sqlite3.Connection, files: list[BorisFile], imported_at: str) -> int:
    insert_sql = """
        INSERT INTO boris_events (
            recording_date, case_no, subject, behavior, behavioral_category,
            behavior_type, modifier_1, modifier_2, modifier_3, time_s,
            absolute_timestamp, source_file, source_row_number, imported_at
        )
        VALUES (
            :recording_date, :case_no, :subject, :behavior, :behavioral_category,
            :behavior_type, :modifier_1, :modifier_2, :modifier_3, :time_s,
            :absolute_timestamp, :source_file, :source_row_number, :imported_at
        )
    """
    conn.execute("UPDATE analysis_information SET event_id = NULL")
    conn.execute("DELETE FROM boris_events")

    inserted = 0
    for file in files:
        rows = []
        for row in file.rows:
            row = dict(row)
            row["imported_at"] = imported_at
            rows.append(row)
        conn.executemany(insert_sql, rows)
        conn.execute(
            """
            UPDATE analysis_information
            SET event_id = (
                SELECT MIN(event_id)
                FROM boris_events
                WHERE recording_date = ?
                  AND case_no = ?
            )
            WHERE recording_date = ?
              AND case_no = ?
            """,
            (file.key.recording_date, file.key.case_no, file.key.recording_date, file.key.case_no),
        )
        inserted += len(rows)
    return inserted


def alert_is_active(value: object) -> bool:
    text = clean_text(value)
    return text is not None and text.upper() not in {"N", "NORMAL", "FALSE", "0"}


def monitor_row_from_csv(
    key: CaseKey,
    sample_index: int,
    row: dict[str, str],
    source_file: str,
    imported_at: str,
) -> dict[str, object]:
    frame = clean_text(row.get("Frame"))
    frame_no = parse_frame_no(frame)
    elapsed_s = (frame_no / 60.0) if frame_no is not None else sample_index * 0.5
    out: dict[str, object] = {
        "recording_date": key.recording_date,
        "case_no": key.case_no,
        "sample_index": sample_index,
        "frame": frame,
        "frame_no": frame_no,
        "timestamp_text": clean_text(row.get("Timestamp")),
        "elapsed_s": elapsed_s,
        "source_file": source_file,
        "imported_at": imported_at,
    }
    for source_col, target_col in VITAL_COLUMNS.items():
        try:
            out[target_col] = parse_float(row.get(source_col))
        except ValueError:
            out[target_col] = None
    for source_col, target_col in ALERT_COLUMNS.items():
        out[target_col] = clean_text(row.get(source_col))
    return out


def insert_monitor_file(conn: sqlite3.Connection, file: MonitorFile, imported_at: str) -> int:
    sample_columns = [
        "recording_date",
        "case_no",
        "sample_index",
        "frame",
        "frame_no",
        "timestamp_text",
        "elapsed_s",
        "source_file",
        "imported_at",
        *VITAL_COLUMNS.values(),
        *ALERT_COLUMNS.values(),
    ]
    placeholders = ", ".join(f":{col}" for col in sample_columns)
    insert_sql = (
        f"INSERT INTO monitor_samples ({', '.join(sample_columns)}) "
        f"VALUES ({placeholders})"
    )

    conn.execute(
        "DELETE FROM monitor_samples WHERE recording_date = ? AND case_no = ?",
        (file.key.recording_date, file.key.case_no),
    )
    conn.execute(
        "DELETE FROM monitor_case_summary WHERE recording_date = ? AND case_no = ?",
        (file.key.recording_date, file.key.case_no),
    )

    summary: dict[str, object] = {
        "recording_date": file.key.recording_date,
        "case_no": file.key.case_no,
        "source_file": file.source_file,
        "sample_count": 0,
        "duration_s": None,
        "imported_at": imported_at,
    }
    for col in VITAL_COLUMNS.values():
        summary[f"{col}_count"] = 0
    for col in ALERT_COLUMNS.values():
        summary[f"{col}_count"] = 0

    inserted = 0
    batch: list[dict[str, object]] = []
    max_elapsed: float | None = None
    with file.path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        for sample_index, raw_row in enumerate(reader):
            if not any(clean_text(value) for value in raw_row.values()):
                continue
            row = monitor_row_from_csv(file.key, sample_index, raw_row, file.source_file, imported_at)
            batch.append(row)
            inserted += 1
            elapsed = row.get("elapsed_s")
            if elapsed is not None:
                max_elapsed = max(float(elapsed), max_elapsed or 0.0)
            for col in VITAL_COLUMNS.values():
                if row.get(col) is not None:
                    summary[f"{col}_count"] = int(summary[f"{col}_count"]) + 1
            for col in ALERT_COLUMNS.values():
                if alert_is_active(row.get(col)):
                    summary[f"{col}_count"] = int(summary[f"{col}_count"]) + 1
            if len(batch) >= 5000:
                conn.executemany(insert_sql, batch)
                batch.clear()
    if batch:
        conn.executemany(insert_sql, batch)

    summary["sample_count"] = inserted
    summary["duration_s"] = max_elapsed
    summary_columns = list(summary.keys())
    conn.execute(
        f"INSERT INTO monitor_case_summary ({', '.join(summary_columns)}) "
        f"VALUES ({', '.join(':' + col for col in summary_columns)})",
        summary,
    )
    return inserted


def import_monitor(conn: sqlite3.Connection, files: list[MonitorFile], imported_at: str) -> int:
    inserted = 0
    for index, file in enumerate(files, start=1):
        logging.info(
            "Importing monitor %s/%s: %s Case%s (%s rows)",
            index,
            len(files),
            file.key.recording_date,
            file.key.case_no,
            file.sample_count,
        )
        inserted += insert_monitor_file(conn, file, imported_at)
    return inserted


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Import finalized BORIS and monitor analysis CSVs")
    parser.add_argument("--input-root", type=Path, default=DEFAULT_INPUT_ROOT, help="Case_Analyses_synced root")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH, help="SQLite database path")
    parser.add_argument("--dry-run", action="store_true", help="Scan and report only; do not modify the database")
    parser.add_argument("--auto-confirm", action="store_true", help="Skip confirmation prompt for real imports")
    parser.add_argument("--skip-boris", action="store_true", help="Skip BORIS replacement")
    parser.add_argument("--skip-monitor", action="store_true", help="Skip monitor import")
    parser.add_argument("--verbose", action="store_true", help="Enable debug logging")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    configure_logging(args.verbose)

    input_root = args.input_root.resolve()
    db_path = args.db.resolve()
    if not input_root.is_dir():
        logging.error("Input root does not exist: %s", input_root)
        return 2
    if not db_path.is_file():
        logging.error("Database does not exist: %s", db_path)
        return 2

    logging.info("Input root: %s", input_root)
    logging.info("Database: %s", db_path)
    logging.info("Mode: %s", "dry run" if args.dry_run else "import")

    boris_files: list[BorisFile] = []
    monitor_files: list[MonitorFile] = []
    if args.skip_boris:
        logging.info("BORIS scan skipped")
        boris_stats = ScanStats()
    else:
        boris_files, boris_stats = scan_boris_files(input_root)
        logging.info(
            "BORIS: %s files, %s rows, %s skipped files, %s invalid rows",
            boris_stats.files,
            boris_stats.rows,
            boris_stats.skipped_files,
            boris_stats.invalid_rows,
        )

    if args.skip_monitor:
        logging.info("Monitor scan skipped")
        monitor_stats = ScanStats()
    else:
        monitor_files, monitor_stats = scan_monitor_files(input_root)
        logging.info(
            "Monitor: %s files, %s rows, %s skipped files",
            monitor_stats.files,
            monitor_stats.rows,
            monitor_stats.skipped_files,
        )

    if args.dry_run:
        logging.info("Dry run complete; database was not modified.")
        return 0

    if not args.auto_confirm:
        print()
        print("[CONFIRM] This will replace BORIS data and import monitor rows.")
        print(f"BORIS rows to import:   {boris_stats.rows:,}")
        print(f"Monitor rows to import: {monitor_stats.rows:,}")
        response = input("Do you want to proceed? (y/N): ").strip().lower()
        if response not in {"y", "yes"}:
            logging.info("Import cancelled by user.")
            return 1

    imported_at = datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(db_path) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        ensure_schema(conn)
        with conn:
            if not args.skip_boris:
                inserted = import_boris(conn, boris_files, imported_at)
                logging.info("Imported %s BORIS rows.", inserted)
            if not args.skip_monitor:
                inserted = import_monitor(conn, monitor_files, imported_at)
                logging.info("Imported %s monitor rows.", inserted)

    logging.info("Import complete.")
    return 0


if __name__ == "__main__":
    if os.name == "nt":
        try:
            sys.stdout.reconfigure(encoding="utf-8")
            sys.stderr.reconfigure(encoding="utf-8")
        except AttributeError:
            pass
    raise SystemExit(main())
