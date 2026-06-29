#!/usr/bin/env python3
"""Import BORIS behavioral tagging TSV exports into ScalpelDatabase.sqlite.

The importer is intentionally strict:
  - filenames must match YY-MM-DD-caseN.tsv
  - files must contain at least one BORIS event row
  - recording_date and case_no are taken from the filename for validation and
    analysis_information.event_id linking
  - suspicious files are skipped and logged

Run a dry run first:
    python scripts/helpers/import_boris_tags.py --dry-run
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
    from config import get_db_path
except ImportError:
    get_db_path = None


DEFAULT_INPUT_DIR = Path(r"C:\Users\user\Downloads\boris_tags")
DEFAULT_DB_PATH = Path(get_db_path()) if get_db_path else PROJECT_ROOT / "ScalpelDatabase.sqlite"
FILENAME_RE = re.compile(
    r"^(?P<yy>\d{2})-(?P<mm>\d{2})-(?P<dd>\d{2})-case(?P<case_no>\d+)\.tsv$",
    re.IGNORECASE,
)

REQUIRED_COLUMNS = {
    "Subject",
    "Behavior",
    "Behavior type",
    "Time",
}

CREATE_BORIS_EVENTS_SQL = """
CREATE TABLE IF NOT EXISTS boris_events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    subject TEXT,
    behavior TEXT,
    behavior_type TEXT
        CHECK (behavior_type IS NULL OR behavior_type IN ('START', 'STOP', 'POINT')),
    modifier_1 TEXT,
    modifier_2 TEXT,
    modifier_3 TEXT,
    time_s REAL
        CHECK (time_s IS NULL OR time_s >= 0),
    source_file TEXT NOT NULL,
    source_row_number INTEGER NOT NULL
        CHECK (source_row_number > 0),
    imported_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (source_file, source_row_number)
);
"""

@dataclass(frozen=True)
class ParsedFileName:
    recording_date: str
    case_no: int


@dataclass
class ImportFile:
    path: Path
    recording_date: str
    case_no: int
    rows: list[dict[str, object]]


@dataclass
class ScanStats:
    matched_files: int = 0
    skipped_files: int = 0
    event_rows: int = 0
    invalid_rows: int = 0


def configure_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(level=level, format="%(levelname)s: %(message)s")


def parse_filename(path: Path) -> ParsedFileName | None:
    match = FILENAME_RE.fullmatch(path.name)
    if not match:
        return None

    yy = int(match.group("yy"))
    month = int(match.group("mm"))
    day = int(match.group("dd"))
    case_no = int(match.group("case_no"))

    year = 2000 + yy
    try:
        recording_date = datetime(year, month, day).strftime("%Y-%m-%d")
    except ValueError:
        return None

    return ParsedFileName(recording_date=recording_date, case_no=case_no)


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


def get_known_recordings(conn: sqlite3.Connection) -> set[tuple[str, int]]:
    try:
        rows = conn.execute("SELECT recording_date, case_no FROM recording_details").fetchall()
    except sqlite3.Error as exc:
        logging.warning("Could not read recording_details for validation: %s", exc)
        return set()
    return {(str(date), int(case_no)) for date, case_no in rows}


def has_required_columns(fieldnames: Iterable[str] | None) -> bool:
    if fieldnames is None:
        return False
    return REQUIRED_COLUMNS.issubset(set(fieldnames))


def media_paths_disagree_with_filename(rows: list[dict[str, str]], parsed: ParsedFileName) -> str | None:
    expected_data_dir = parsed.recording_date[2:].replace("-", "-")
    expected_data_token = f"DATA_{expected_data_dir}"
    expected_case_token = f"Case{parsed.case_no}".lower()

    for row in rows[:25]:
        text = " ".join(
            clean_text(row.get(key)) or ""
            for key in ("Source", "Media file name")
        )

        data_match = re.search(r"DATA_(\d{2}-\d{2}-\d{2})", text, flags=re.IGNORECASE)
        if data_match and f"DATA_{data_match.group(1)}".upper() != expected_data_token.upper():
            return f"media DATA folder disagrees with filename ({data_match.group(0)})"

        case_matches = re.findall(r"(?:^|[\\/])Case(\d+)(?:[\\/]|$)", text, flags=re.IGNORECASE)
        for case_text in case_matches:
            if f"case{case_text}".lower() != expected_case_token:
                return f"media Case folder disagrees with filename (Case{case_text})"

    return None


def read_tsv(path: Path) -> tuple[list[dict[str, str]], str | None]:
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle, delimiter="\t")
            if not has_required_columns(reader.fieldnames):
                return [], "missing required BORIS columns"
            rows = [
                row
                for row in reader
                if any(clean_text(value) for value in row.values())
            ]
    except OSError as exc:
        return [], f"could not read file: {exc}"

    if not rows:
        return [], "empty export"

    return rows, None


def normalize_rows(
    source_file: str,
    parsed: ParsedFileName,
    rows: list[dict[str, str]],
) -> tuple[list[dict[str, object]], int]:
    normalized: list[dict[str, object]] = []
    invalid_rows = 0

    for row_number, row in enumerate(rows, start=2):
        try:
            time_s = parse_float(row.get("Time"))
        except ValueError:
            invalid_rows += 1
            logging.warning("%s row %s skipped: invalid Time value", source_file, row_number)
            continue

        behavior_type = clean_text(row.get("Behavior type"))
        if behavior_type is not None:
            behavior_type = behavior_type.upper()

        if behavior_type not in {"START", "STOP", "POINT"}:
            invalid_rows += 1
            logging.warning(
                "%s row %s skipped: unsupported Behavior type %r",
                source_file,
                row_number,
                behavior_type,
            )
            continue

        normalized.append(
            {
                "recording_date": parsed.recording_date,
                "case_no": parsed.case_no,
                "subject": clean_text(row.get("Subject")),
                "behavior": clean_text(row.get("Behavior")),
                "behavior_type": behavior_type,
                "modifier_1": clean_text(row.get("Modifier #1")),
                "modifier_2": clean_text(row.get("Modifier #2")),
                "modifier_3": clean_text(row.get("Modifier #3")),
                "time_s": time_s,
                "source_file": source_file,
                "source_row_number": row_number,
            }
        )

    return normalized, invalid_rows


def scan_files(
    input_dir: Path,
    known_recordings: set[tuple[str, int]],
    allow_unmatched_db: bool,
) -> tuple[list[ImportFile], ScanStats]:
    stats = ScanStats()
    import_files: list[ImportFile] = []

    for path in sorted(input_dir.glob("*.tsv")):
        parsed = parse_filename(path)
        if parsed is None:
            stats.skipped_files += 1
            logging.warning("Skipping %s: filename does not match YY-MM-DD-caseN.tsv", path.name)
            continue

        if known_recordings and not allow_unmatched_db:
            if (parsed.recording_date, parsed.case_no) not in known_recordings:
                stats.skipped_files += 1
                logging.warning(
                    "Skipping %s: %s case %s is not in recording_details",
                    path.name,
                    parsed.recording_date,
                    parsed.case_no,
                )
                continue

        raw_rows, skip_reason = read_tsv(path)
        if skip_reason:
            stats.skipped_files += 1
            logging.warning("Skipping %s: %s", path.name, skip_reason)
            continue

        suspicious_reason = media_paths_disagree_with_filename(raw_rows, parsed)
        if suspicious_reason:
            stats.skipped_files += 1
            logging.warning("Skipping %s: suspicious file: %s", path.name, suspicious_reason)
            continue

        normalized_rows, invalid_rows = normalize_rows(path.name, parsed, raw_rows)
        stats.invalid_rows += invalid_rows
        if not normalized_rows:
            stats.skipped_files += 1
            logging.warning("Skipping %s: no valid event rows after normalization", path.name)
            continue

        import_files.append(
            ImportFile(
                path=path,
                recording_date=parsed.recording_date,
                case_no=parsed.case_no,
                rows=normalized_rows,
            )
        )
        stats.matched_files += 1
        stats.event_rows += len(normalized_rows)
        logging.info(
            "Matched %-24s -> %s Case%s, %s rows",
            path.name,
            parsed.recording_date,
            parsed.case_no,
            len(normalized_rows),
        )

    return import_files, stats


def table_columns(conn: sqlite3.Connection, table_name: str) -> set[str]:
    return {
        row[1]
        for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    }


def migrate_legacy_boris_events(conn: sqlite3.Connection) -> None:
    boris_columns = table_columns(conn, "boris_events")
    if not {"recording_date", "case_no"}.issubset(boris_columns):
        return

    logging.info("Migrating boris_events to event_id-only case linkage")
    analysis_columns = table_columns(conn, "analysis_information")
    if "event_id" not in analysis_columns:
        conn.execute(
            "ALTER TABLE analysis_information "
            "ADD COLUMN event_id INTEGER REFERENCES boris_events(event_id)"
        )

    conn.execute(
        """
        UPDATE analysis_information
        SET event_id = (
            SELECT MIN(boris_events.event_id)
            FROM boris_events
            WHERE boris_events.recording_date = analysis_information.recording_date
              AND boris_events.case_no = analysis_information.case_no
        )
        WHERE event_id IS NULL
        """
    )

    conn.execute(
        """
        DROP TABLE IF EXISTS boris_events_new
        """
    )
    conn.execute(
        """
        CREATE TABLE boris_events_new (
            event_id INTEGER PRIMARY KEY AUTOINCREMENT,
            subject TEXT,
            behavior TEXT,
            behavior_type TEXT
                CHECK (behavior_type IS NULL OR behavior_type IN ('START', 'STOP', 'POINT')),
            modifier_1 TEXT,
            modifier_2 TEXT,
            modifier_3 TEXT,
            time_s REAL
                CHECK (time_s IS NULL OR time_s >= 0),
            source_file TEXT NOT NULL,
            source_row_number INTEGER NOT NULL
                CHECK (source_row_number > 0),
            imported_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (source_file, source_row_number)
        )
        """
    )
    conn.execute(
        """
        INSERT INTO boris_events_new (
            event_id,
            subject,
            behavior,
            behavior_type,
            modifier_1,
            modifier_2,
            modifier_3,
            time_s,
            source_file,
            source_row_number,
            imported_at
        )
        SELECT
            event_id,
            subject,
            behavior,
            behavior_type,
            modifier_1,
            modifier_2,
            modifier_3,
            time_s,
            source_file,
            source_row_number,
            imported_at
        FROM boris_events
        """
    )
    conn.execute("DROP TABLE boris_events")
    conn.execute("ALTER TABLE boris_events_new RENAME TO boris_events")


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.commit()
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute("DROP VIEW IF EXISTS cur_boris_intervals")
    conn.execute(CREATE_BORIS_EVENTS_SQL)
    migrate_legacy_boris_events(conn)
    columns = table_columns(conn, "analysis_information")
    if "event_id" not in columns:
        conn.execute(
            "ALTER TABLE analysis_information "
            "ADD COLUMN event_id INTEGER REFERENCES boris_events(event_id)"
        )
    conn.commit()
    conn.execute("PRAGMA foreign_keys = ON")


def import_rows(conn: sqlite3.Connection, import_files: list[ImportFile]) -> int:
    imported_at = datetime.now(timezone.utc).isoformat()
    inserted = 0
    insert_sql = """
        INSERT INTO boris_events (
            subject,
            behavior,
            behavior_type,
            modifier_1,
            modifier_2,
            modifier_3,
            time_s,
            source_file,
            source_row_number,
            imported_at
        )
        VALUES (
            :subject,
            :behavior,
            :behavior_type,
            :modifier_1,
            :modifier_2,
            :modifier_3,
            :time_s,
            :source_file,
            :source_row_number,
            :imported_at
        )
    """

    with conn:
        for import_file in import_files:
            source_file = import_file.path.name
            conn.execute(
                """
                UPDATE analysis_information
                SET event_id = NULL
                WHERE event_id IN (
                    SELECT event_id
                    FROM boris_events
                    WHERE source_file = ?
                )
                """,
                (source_file,),
            )
            conn.execute("DELETE FROM boris_events WHERE source_file = ?", (source_file,))
            rows = []
            for row in import_file.rows:
                row_with_time = dict(row)
                row_with_time["imported_at"] = imported_at
                rows.append(row_with_time)
            conn.executemany(insert_sql, rows)
            conn.execute(
                """
                UPDATE analysis_information
                SET event_id = (
                    SELECT MIN(event_id)
                    FROM boris_events
                    WHERE source_file = ?
                )
                WHERE recording_date = ?
                  AND case_no = ?
                """,
                (source_file, import_file.recording_date, import_file.case_no),
            )
            inserted += len(rows)

    return inserted


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Import BORIS TSV tags into ScalpelDatabase.sqlite")
    parser.add_argument("--input-dir", type=Path, default=DEFAULT_INPUT_DIR, help="Folder containing BORIS .tsv files")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH, help="SQLite database path")
    parser.add_argument("--dry-run", action="store_true", help="Scan and report only; do not modify the database")
    parser.add_argument(
        "--allow-unmatched-db",
        action="store_true",
        help="Import valid filename keys even when they are not present in recording_details",
    )
    parser.add_argument("--verbose", action="store_true", help="Enable debug logging")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    configure_logging(args.verbose)

    input_dir = args.input_dir.resolve()
    db_path = args.db.resolve()

    if not input_dir.exists():
        logging.error("Input directory does not exist: %s", input_dir)
        return 2
    if not db_path.exists():
        logging.error("Database does not exist: %s", db_path)
        return 2

    logging.info("Input directory: %s", input_dir)
    logging.info("Database: %s", db_path)
    logging.info("Mode: %s", "dry run" if args.dry_run else "import")

    with sqlite3.connect(db_path) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        known_recordings = get_known_recordings(conn)
        import_files, stats = scan_files(
            input_dir=input_dir,
            known_recordings=known_recordings,
            allow_unmatched_db=args.allow_unmatched_db,
        )

        logging.info("Matched files: %s", stats.matched_files)
        logging.info("Skipped files: %s", stats.skipped_files)
        logging.info("Valid event rows: %s", stats.event_rows)
        logging.info("Invalid event rows skipped: %s", stats.invalid_rows)

        if args.dry_run:
            logging.info("Dry run complete; database was not modified.")
            return 0

        ensure_schema(conn)
        inserted = import_rows(conn, import_files)
        logging.info("Imported %s rows into boris_events.", inserted)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
