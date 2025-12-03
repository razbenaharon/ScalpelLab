#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SQL to Path - Query database and get file paths

Python API for querying the database and resolving file paths.

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
              root_path: str = DEFAULT_ROOT) -> List[str]:
    """
    Run SQL query and return list of file paths.

    Args:
        sql_query: SQL query that must SELECT: recording_date, case_no, camera_name
        db_path: Path to SQLite database
        root_path: Root directory for files (auto-detects .mp4 or .seq based on path)

    Returns:
        List of file paths (strings)

    Example:
        sql = "SELECT recording_date, case_no, camera_name FROM mp4_status WHERE size_mb >= 200"
        paths = get_paths(sql)
    """
    root = Path(root_path)
    conn = sqlite3.connect(db_path)
    try:
        colnames, rows = run_sql(conn, sql_query)
        required_cols = ["recording_date", "case_no", "camera_name"]
        if not rows or not all(col in colnames for col in required_cols):
            return []

        out_paths = []
        for row in rows:
            row_map = dict(zip(colnames, row))
            recording_date = row_map["recording_date"]
            case_no = row_map["case_no"]
            camera_name = row_map["camera_name"]

            # Auto-detect file extension based on root path
            file_ext = "seq" if "Sequence_Backup" in str(root) else "mp4"

            # Try to find actual files
            files = list_files_for_camera(root, recording_date, case_no, camera_name, file_ext)

            if files:
                # Files exist - return all found files
                for p in files:
                    out_paths.append(str(p))
            else:
                # Files don't exist - return expected path
                data_dir, case_dir = data_dir_from_recording_date_and_case(recording_date, case_no)
                expected_path = root / data_dir / case_dir / camera_name
                expected_file_path = expected_path / f"*.{file_ext}"
                out_paths.append(str(expected_file_path))
        return out_paths
    finally:
        conn.close()



