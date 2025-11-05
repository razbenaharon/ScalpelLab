#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SQL to Path - Query database and get file paths

Python API for querying the database and resolving file paths.

Usage:
    from scripts.sql_to_path import get_paths

    sql = "SELECT * FROM mp4_status WHERE size_mb >= 200"
    paths = get_paths(sql)

    for date, case, camera, path, size_mb in paths:
        print(f"{date} Case{case} {camera}: {path} ({size_mb}MB)")

Required SQL columns:
  - recording_date
  - case_no
  - camera_name
  - size_mb

Filter by size in your SQL:
  - Large files: WHERE size_mb >= 200
  - Small files: WHERE size_mb < 200
  - Missing files: WHERE size_mb IS NULL
  - Size range: WHERE size_mb BETWEEN 100 AND 500
"""

import argparse
import csv
import os
import re
import sqlite3
import sys
from pathlib import Path
from typing import Iterable, List, Tuple, Union, Optional

# Add parent directory to path to import config
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import get_db_path, get_mp4_root, DEFAULT_CAMERAS

# ------------ Defaults (from config.py) ------------
DEFAULT_DB_PATH = get_db_path()
DEFAULT_ROOT    = get_mp4_root()
CAMERAS: List[str] = DEFAULT_CAMERAS
# ---------------------------------------------------


def read_sql_from_args(args: argparse.Namespace) -> str:
    """Load SQL from --sql or --sql-file (mutually exclusive)."""
    if bool(args.sql) == bool(args.sql_file):
        raise SystemExit("[ERROR] Provide exactly one of --sql or --sql-file.")
    if args.sql_file:
        p = Path(args.sql_file)
        if not p.exists():
            raise SystemExit(f"[ERROR] SQL file not found: {p}")
        return p.read_text(encoding="utf-8")
    return args.sql


def data_dir_from_recording_date_and_case(recording_date: str, case_no: int) -> Tuple[str, str]:
    """'2023-02-05', 1 -> ('DATA_23-02-05', 'Case1')."""
    m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", recording_date)
    if not m:
        raise ValueError(f"Bad recording_date format: {recording_date}")
    yyyy, mm, dd = m.groups()
    yy = yyyy[2:]
    return f"DATA_{yy}-{mm}-{dd}", f"Case{case_no}"


def list_files_for_camera(root: Path, recording_date: str, case_no: int, camera: str, file_ext: str = "mp4") -> List[Path]:
    """Return list of files under <root>/DATA_YY-MM-DD/CaseN/<camera>/ recursively."""
    data_dir, case_dir = data_dir_from_recording_date_and_case(recording_date, case_no)
    cam_dir = root / data_dir / case_dir / camera
    if not cam_dir.exists():
        return []
    return [p for p in cam_dir.rglob(f"*.{file_ext}") if p.is_file()]


def pick_largest(paths: Iterable[Path]) -> List[Path]:
    """Return a 1-element list containing the largest path (or [] if none)."""
    paths = list(paths)
    if not paths:
        return []
    try:
        return [max(paths, key=lambda p: p.stat().st_size)]
    except OSError:
        readable = []
        for p in paths:
            try:
                _ = p.stat().st_size
                readable.append(p)
            except OSError:
                pass
        return [max(readable, key=lambda p: p.stat().st_size)] if readable else []


def run_sql(conn: sqlite3.Connection, sql_query: str) -> Tuple[List[str], List[tuple]]:
    """Execute full SQL and return (column_names, rows)."""
    cur = conn.cursor()
    rows = cur.execute(sql_query).fetchall()
    colnames = [d[0] for d in cur.description] if cur.description else []
    return colnames, rows

def get_paths(sql_query: str,
              db_path: str = DEFAULT_DB_PATH,
              root_path: str = DEFAULT_ROOT) -> List[Tuple[str, int, str, str, float]]:
    """
    Run SQL query and return list of (recording_date, case_no, camera, file_path, size_mb).

    Args:
        sql_query: SQL query that must SELECT: recording_date, case_no, camera_name, size_mb
        db_path: Path to SQLite database
        root_path: Root directory for files (auto-detects .mp4 or .seq based on path)

    Returns:
        List of tuples: (recording_date, case_no, camera_name, file_path, size_mb)

    Example:
        sql = "SELECT * FROM mp4_status WHERE size_mb >= 200"
        paths = get_paths(sql)
    """
    root = Path(root_path)
    conn = sqlite3.connect(db_path)
    try:
        colnames, rows = run_sql(conn, sql_query)
        required_cols = ["recording_date", "case_no", "camera_name", "size_mb"]
        if not rows or not all(col in colnames for col in required_cols):
            return []

        out_rows = []
        for row in rows:
            row_map = dict(zip(colnames, row))
            recording_date = row_map["recording_date"]
            case_no = row_map["case_no"]
            camera_name = row_map["camera_name"]
            size_mb_db = row_map["size_mb"]

            # Auto-detect file extension based on root path
            file_ext = "seq" if "Sequence_Backup" in str(root) else "mp4"

            # Try to find actual files
            files = list_files_for_camera(root, recording_date, case_no, camera_name, file_ext)

            if files:
                # Files exist - return all found files
                for p in files:
                    try:
                        size_mb = round(p.stat().st_size / (1024 * 1024), 2)
                    except OSError:
                        size_mb = -1.0
                    out_rows.append((recording_date, case_no, camera_name, str(p), size_mb))
            else:
                # Files don't exist - return expected path
                data_dir, case_dir = data_dir_from_recording_date_and_case(recording_date, case_no)
                expected_path = root / data_dir / case_dir / camera_name
                expected_file_path = expected_path / f"*.{file_ext}"
                out_rows.append((recording_date, case_no, camera_name, str(expected_file_path), 0.0))
        return out_rows
    finally:
        conn.close()


def main():
    """Command-line interface (primarily for testing - use Python API for integration)"""
    print("SQL to Path - Python API")
    print("=" * 60)
    print("This script is designed for Python API usage.")
    print("\nExample:")
    print("  from scripts.sql_to_path import get_paths")
    print("  sql = 'SELECT * FROM mp4_status WHERE size_mb >= 200'")
    print("  paths = get_paths(sql)")
    print("\nFor examples, run: python main.py")
    print("=" * 60)


if __name__ == "__main__":
    main()
