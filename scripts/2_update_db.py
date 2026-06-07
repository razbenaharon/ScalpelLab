#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
Combined status updater - Updates both seq_status and mp4_status tables in one run.

This script:
  - Updates seq_status: Scans SEQ files in Sequence_Backup
  - Updates mp4_status: Scans MP4 files in Recordings (with duration and path)
  - Shows combined statistics and changes
  - Requires single confirmation for both updates
  - FUTURE-PROOF: Only updates managed columns, preserves all other columns

Managed Columns (mp4_status):
  This script ONLY manages these columns:
  - size_mb: Size of largest MP4 file (updated)
  - duration_minutes: Duration from ffprobe (updated)
  - path: Full path to the MP4 file (updated)

  ALL OTHER COLUMNS are preserved (including future columns you add):
  - Uses INSERT ... ON CONFLICT DO UPDATE to only touch managed columns
  - Any new columns added to the table will be automatically preserved
  - No need to update this script when adding new columns!

SEQ Status Columns:
  This script manages:
  - size_mb: Size of largest SEQ file
  - path: Full path to the SEQ file (relative from Sequence_Backup)

Performance:
  - First run with duration: ~5-10 minutes (ffprobe for all MP4s)
  - Subsequent runs: ~1 minute (smart mode - only new/changed files)
  - Use --skip-duration for fastest updates (~30 seconds)
"""

from __future__ import annotations

import argparse
import os
import re
import sqlite3
import sys
import time
import subprocess
import json
from pathlib import Path
from typing import Any, Callable

# Add parent directory to path to import config
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import get_db_path, get_seq_root, get_mp4_root, DEFAULT_CAMERAS

# SEQ field analysis (optional - skipped gracefully if unavailable)
_analyze_seq_dir: Callable[..., Any] | None = None
_write_seq_analysis: Callable[..., Any] | None = None
_seq_existing_keys: Callable[[str], set[tuple[str, int, str]]] | None = None
_seq_canonical_map: Callable[[str], dict[tuple[str, int, str], str]] | None = None
_ensure_seq_analysis_table: Callable[[str], None] | None = None
seq_analysis_available = False

try:
    from helpers.analyze_seq_fields import analyze_directory as _analyze_seq_dir
    from helpers.analyze_seq_fields import write_to_db as _write_seq_analysis
    from helpers.analyze_seq_fields import load_existing_keys as _seq_existing_keys
    from helpers.analyze_seq_fields import load_camera_canonical_map as _seq_canonical_map
    from helpers.analyze_seq_fields import ensure_analysis_table as _ensure_seq_analysis_table
    seq_analysis_available = True
except ImportError:
    pass

# ============================================
# Defaults (from config.py)
# ============================================
DEFAULT_DB_PATH = get_db_path()
DEFAULT_SEQ_ROOT = get_seq_root()
DEFAULT_MP4_ROOT = get_mp4_root()
DEFAULT_THRESHOLD_MB = 200
DEFAULT_DELETE_SMALL_MB = 10

CAMERAS = DEFAULT_CAMERAS

# ============================================
# Common utilities
# ============================================
def parse_recording_date_and_case(data_dir_name: str, case_dir_name: str) -> tuple[str, int] | None:
    """Convert DATA_YY-MM-DD + CaseN -> (YYYY-MM-DD, N)."""
    m = re.fullmatch(r"DATA_(\d{2})-(\d{2})-(\d{2})", data_dir_name)
    n = re.fullmatch(r"Case(\d+)", case_dir_name)
    if not m or not n:
        return None
    yy, mm, dd = m.groups()
    yyyy = f"20{yy}" if int(yy) <= 69 else f"19{yy}"
    case_no = int(n.group(1))
    return f"{yyyy}-{mm}-{dd}", case_no


def _section(title: str) -> None:
    print(f"\n{title}")
    print("-" * len(title))


def _status_label(status: int) -> str:
    return {1: ">= threshold", 2: "< threshold", 3: "missing"}.get(status, str(status))


def _fmt_mb(size_mb: int | float | None) -> str:
    return "NULL" if size_mb is None else f"{size_mb:g} MB"


def _fmt_min(minutes: float | None) -> str:
    return "N/A" if minutes is None else f"{minutes:.1f} min"


def _entry_label(recording_date: str, case_no: int, camera_name: str) -> str:
    return f"{recording_date} Case{case_no} {camera_name}"


def _print_inventory_counts(label: str, total: int, new: int, changed: int) -> None:
    unchanged = total - new - changed
    print(f"[OK] {label}: total {total:,} | new {new:,} | changed {changed:,} | unchanged {unchanged:,}")


def _warn_missing_recording_details(
    conn: sqlite3.Connection,
    keys: set[tuple[str, int, str]],
) -> None:
    """Warn when status rows are not anchored in recording_details yet."""
    if not keys:
        return

    try:
        existing_cases = {
            (row[0], int(row[1]))
            for row in conn.execute("SELECT recording_date, case_no FROM recording_details")
        }
    except sqlite3.OperationalError:
        return

    update_cases = {(recording_date, case_no) for recording_date, case_no, _ in keys}
    missing_cases = sorted(update_cases - existing_cases)
    if not missing_cases:
        return

    print(
        f"[WARN] {len(missing_cases):,} case(s) are not present in recording_details; "
        "status rows will still be written."
    )
    for recording_date, case_no in missing_cases:
        print(f"       - {recording_date} Case{case_no}")


def _planned_update_keys(stats: dict | None) -> set[tuple[str, int, str]]:
    """Return keys that were reported as new or changed."""
    if not stats:
        return set()
    keys: set[tuple[str, int, str]] = set()
    for entry in stats.get('new_entries', []):
        keys.add((entry[0], entry[1], entry[2]))
    for entry in stats.get('changed_entries', []):
        keys.add((entry[0], entry[1], entry[2]))
    return keys


def _print_seq_changes(
    new_entries: list[tuple],
    changed_entries: list[tuple],
) -> None:
    if not new_entries and not changed_entries:
        print("[OK] No SEQ inventory changes.")
        return
    if new_entries:
        print(f"[NEW] SEQ entries ({len(new_entries):,})")
        for recording_date, case_no, camera_name, status, size_mb, _file_path in new_entries:
            print(
                f"  + {_entry_label(recording_date, case_no, camera_name):42s} | "
                f"size {_fmt_mb(size_mb):>10s} | status {_status_label(status)}"
            )
    if changed_entries:
        print(f"[CHANGED] SEQ entries ({len(changed_entries):,})")
        for recording_date, case_no, camera_name, status, old_size, new_size, old_path, new_path in changed_entries:
            path_note = " | path changed" if old_path != new_path else ""
            print(
                f"  ~ {_entry_label(recording_date, case_no, camera_name):42s} | "
                f"size {_fmt_mb(old_size):>10s} -> {_fmt_mb(new_size):>10s} | "
                f"status {_status_label(status)}{path_note}"
            )


def _print_mp4_changes(
    new_entries: list[tuple],
    changed_entries: list[tuple],
) -> None:
    if not new_entries and not changed_entries:
        print("[OK] No MP4 inventory changes.")
        return
    if new_entries:
        print(f"[NEW] MP4 entries ({len(new_entries):,})")
        for recording_date, case_no, camera_name, status, size_mb, duration, _file_path in new_entries:
            print(
                f"  + {_entry_label(recording_date, case_no, camera_name):42s} | "
                f"size {_fmt_mb(size_mb):>10s} | duration {_fmt_min(duration):>10s} | "
                f"status {_status_label(status)}"
            )
    if changed_entries:
        print(f"[CHANGED] MP4 entries ({len(changed_entries):,})")
        for (
            recording_date,
            case_no,
            camera_name,
            status,
            old_size,
            new_size,
            old_duration,
            new_duration,
            old_path,
            new_path,
        ) in changed_entries:
            path_note = " | path changed" if old_path != new_path else ""
            print(
                f"  ~ {_entry_label(recording_date, case_no, camera_name):42s} | "
                f"size {_fmt_mb(old_size):>10s} -> {_fmt_mb(new_size):>10s} | "
                f"duration {_fmt_min(old_duration):>10s} -> {_fmt_min(new_duration):>10s} | "
                f"status {_status_label(status)}{path_note}"
            )


def ensure_seq_table_exists(conn: sqlite3.Connection) -> None:
    """Ensure seq_status table exists. Only manages: size_mb, path."""
    cur = conn.cursor()

    # Create table with minimal required columns
    cur.execute("""
        CREATE TABLE IF NOT EXISTS "seq_status" (
            recording_date TEXT NOT NULL,
            case_no INTEGER NOT NULL,
            camera_name TEXT NOT NULL,
            PRIMARY KEY (recording_date, case_no, camera_name)
        );
    """)

    # Only add the columns this script manages
    managed_columns = [
        ('size_mb', 'INTEGER'),
        ('path', 'TEXT'),
    ]

    for col_name, col_type in managed_columns:
        try:
            cur.execute(f'ALTER TABLE "seq_status" ADD COLUMN {col_name} {col_type}')
            conn.commit()
            print(f"[INFO] Added {col_name} column to seq_status table")
        except sqlite3.OperationalError:
            # Column already exists
            pass

    conn.commit()


def ensure_mp4_table_exists(conn: sqlite3.Connection) -> None:
    """Ensure mp4_status table exists. Only manages: size_mb, duration_minutes, path."""
    cur = conn.cursor()

    # Create table with minimal required columns
    cur.execute("""
        CREATE TABLE IF NOT EXISTS "mp4_status" (
            recording_date TEXT NOT NULL,
            case_no INTEGER NOT NULL,
            camera_name TEXT NOT NULL,
            PRIMARY KEY (recording_date, case_no, camera_name)
        );
    """)

    # Only add the columns this script manages
    managed_columns = [
        ('size_mb', 'INTEGER'),
        ('duration_minutes', 'REAL'),
        ('path', 'TEXT')
    ]

    for col_name, col_type in managed_columns:
        try:
            cur.execute(f'ALTER TABLE "mp4_status" ADD COLUMN {col_name} {col_type}')
            conn.commit()
            print(f"[INFO] Added {col_name} column to mp4_status table")
        except sqlite3.OperationalError:
            # Column already exists
            pass

    conn.commit()


# ============================================
# SEQ status functions
# ============================================
def compute_seq_status(camera_dir: Path, threshold_bytes: int, seq_root: Path | None = None) -> tuple[int, int | None, str | None]:
    """Return (status, size_mb, path) for SEQ files in camera directory.

    Path will be relative starting from 'Sequence_Backup' if seq_root is provided.
    """
    if not camera_dir.is_dir():
        return 3, None, None
    max_size = 0
    largest_file = None
    found_any = False
    for p in camera_dir.rglob("*.seq"):
        if p.is_file():
            found_any = True
            try:
                sz = p.stat().st_size
            except OSError:
                continue
            if sz > max_size:
                max_size = sz
                largest_file = p
    if not found_any:
        return 3, None, None
    status = 1 if max_size >= threshold_bytes else 2
    size_mb = int(max_size / (1024 * 1024))

    # Store relative path starting from 'Sequence_Backup'
    file_path = None
    if largest_file and seq_root:
        try:
            rel_path = largest_file.relative_to(seq_root)
            file_path = str(Path("Sequence_Backup") / rel_path)
        except ValueError:
            file_path = str(largest_file)
    elif largest_file:
        file_path = str(largest_file)

    return status, size_mb, file_path


def update_seq_status(db_path: str, seq_root: Path, threshold_mb: int, dry_run: bool = False) -> dict:
    """Update seq_status table and return statistics."""
    _section("SEQ inventory")

    threshold_bytes = threshold_mb * 1024 * 1024
    updates = {}

    print(f"[INFO] Scanning SEQ files in {seq_root}...")
    for data_dir in seq_root.iterdir():
        if not data_dir.is_dir() or not data_dir.name.startswith("DATA_"):
            continue
        for case_dir in data_dir.iterdir():
            if not case_dir.is_dir() or not case_dir.name.startswith("Case"):
                continue
            parsed = parse_recording_date_and_case(data_dir.name, case_dir.name)
            if not parsed:
                continue
            recording_date, case_no = parsed

            for cam in CAMERAS:
                cam_path = case_dir / cam
                status, size_mb, file_path = compute_seq_status(cam_path, threshold_bytes, seq_root)
                updates[(recording_date, case_no, cam)] = (status, size_mb, file_path)

    if not updates:
        print("[WARN] No SEQ files found")
        return {'total': 0, 'new': 0, 'changed': 0}

    # Check for changes
    conn = sqlite3.connect(db_path)
    try:
        if not dry_run:
            ensure_seq_table_exists(conn)
        cur = conn.cursor()

        existing = {}
        try:
            cur.execute('SELECT recording_date, case_no, camera_name, size_mb, path FROM "seq_status"')
            for row in cur.fetchall():
                existing[(row[0], row[1], row[2])] = (row[3], row[4])
        except sqlite3.OperationalError:
            pass

        new_entries = []
        changed_entries = []

        for key, (status, size_mb, file_path) in updates.items():
            recording_date, case_no, camera_name = key
            if key not in existing:
                new_entries.append((recording_date, case_no, camera_name, status, size_mb, file_path))
            else:
                old_size, old_path = existing[key]
                if old_size != size_mb or old_path != file_path:
                    changed_entries.append((recording_date, case_no, camera_name, status, old_size, size_mb, old_path, file_path))

        unchanged = len(updates) - len(new_entries) - len(changed_entries)
        _print_inventory_counts("SEQ", len(updates), len(new_entries), len(changed_entries))
        _print_seq_changes(new_entries, changed_entries)

        return {
            'total': len(updates),
            'new': len(new_entries),
            'changed': len(changed_entries),
            'updates': updates,
            'new_entries': new_entries,
            'changed_entries': changed_entries,
        }
    finally:
        conn.close()


# ============================================
# MP4 status functions (with duration)
# ============================================
_ffprobe_cmd_cache = None
_ffprobe_cmd_resolved = False


def _find_ffprobe() -> str | None:
    """Find ffprobe executable path (cached)."""
    global _ffprobe_cmd_cache, _ffprobe_cmd_resolved
    if _ffprobe_cmd_resolved:
        return _ffprobe_cmd_cache

    ffprobe_paths = [
        r"C:\Program Files\ffmpeg\bin\ffprobe.exe",
        r"C:\ffmpeg\bin\ffprobe.exe",
        r"C:\Program Files (x86)\ffmpeg\bin\ffprobe.exe",
    ]

    for path in ffprobe_paths:
        if os.path.exists(path):
            _ffprobe_cmd_cache = path
            _ffprobe_cmd_resolved = True
            return _ffprobe_cmd_cache

    # Try system PATH
    try:
        result = subprocess.run(
            ["where" if os.name == 'nt' else "which", "ffprobe"],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            _ffprobe_cmd_cache = result.stdout.strip().split('\n')[0]
    except Exception:
        pass

    _ffprobe_cmd_resolved = True
    return _ffprobe_cmd_cache


def get_video_duration(video_path: Path) -> float | None:
    """Get video duration in minutes using ffprobe."""
    try:
        ffprobe_cmd = _find_ffprobe()
        if not ffprobe_cmd:
            return None

        cmd = [
            ffprobe_cmd, "-v", "error",
            "-show_entries", "format=duration",
            "-of", "json", str(video_path)
        ]

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode == 0:
            data = json.loads(result.stdout)
            duration_str = data.get("format", {}).get("duration")
            if duration_str:
                return float(duration_str) / 60.0  # Convert to minutes
    except Exception:
        pass
    return None


def _path_identity(path: Path) -> str:
    return os.path.normcase(os.path.abspath(str(path)))


def compute_mp4_status(
    camera_dir: Path,
    threshold_bytes: int,
    calculate_duration: bool = True,
    mp4_root: Path | None = None,
    ignored_files: set[str] | None = None,
) -> tuple[int, int | None, float | None, str | None]:
    """Return (status, size_mb, duration_minutes, path) for MP4 files in camera directory.

    Path will be relative starting from 'Recordings' if mp4_root is provided.
    """
    if not camera_dir.is_dir():
        return 3, None, None, None
    max_size = 0
    largest_file = None
    found_any = False

    for p in camera_dir.rglob("*.mp4"):
        if ignored_files and _path_identity(p) in ignored_files:
            continue
        if p.is_file():
            found_any = True
            try:
                sz = p.stat().st_size
            except OSError:
                continue
            if sz > max_size:
                max_size = sz
                largest_file = p

    if not found_any:
        return 3, None, None, None

    status = 1 if max_size >= threshold_bytes else 2
    size_mb = int(max_size / (1024 * 1024))

    duration = None
    if calculate_duration and largest_file:
        duration = get_video_duration(largest_file)

    # Store relative path starting from 'Recordings'
    file_path = None
    if largest_file and mp4_root:
        try:
            # Get path relative to mp4_root and prepend 'Recordings'
            rel_path = largest_file.relative_to(mp4_root)
            file_path = str(Path("Recordings") / rel_path)
        except ValueError:
            # If relative_to fails, store full path as fallback
            file_path = str(largest_file)
    elif largest_file:
        # Fallback: store full path if mp4_root not provided
        file_path = str(largest_file)

    return status, size_mb, duration, file_path


def delete_small_mp4s(root: Path, threshold_mb: int, dry_run: bool = False) -> tuple[int, float, set[str]]:
    """Delete or preview MP4 files smaller than threshold_mb."""
    threshold_bytes = threshold_mb * 1024 * 1024
    deleted_count = 0
    total_size_mb = 0.0
    affected_files: set[str] = set()

    if dry_run:
        print(f"[INFO] Scanning for MP4 files < {threshold_mb} MB that would be deleted...")
    else:
        print(f"[INFO] Scanning for MP4 files < {threshold_mb} MB to delete...")

    for mp4_file in root.rglob("*.mp4"):
        if mp4_file.is_file():
            try:
                size_bytes = mp4_file.stat().st_size
                if size_bytes < threshold_bytes:
                    size_mb = size_bytes / (1024 * 1024)
                    if dry_run:
                        print(f"[WOULD DELETE] {mp4_file.name} ({size_mb:.1f}MB)")
                        deleted_count += 1
                        total_size_mb += size_mb
                        affected_files.add(_path_identity(mp4_file))
                        continue
                    for attempt in range(3):
                        try:
                            mp4_file.unlink()
                            print(f"[DELETED] {mp4_file.name} ({size_mb:.1f}MB)")
                            deleted_count += 1
                            total_size_mb += size_mb
                            affected_files.add(_path_identity(mp4_file))
                            break
                        except PermissionError:
                            if attempt < 2:
                                time.sleep(0.1)
                        except Exception:
                            break
            except OSError:
                continue

    if deleted_count > 0 and dry_run:
        print(f"[INFO] Would delete {deleted_count} small MP4 files, freeing {total_size_mb:.1f}MB")
    elif deleted_count > 0:
        print(f"[INFO] Deleted {deleted_count} small MP4 files, freed {total_size_mb:.1f}MB")
    else:
        print(f"[INFO] No MP4 files smaller than {threshold_mb} MB found")

    return deleted_count, total_size_mb, affected_files


def update_mp4_status(db_path: str, mp4_root: Path, threshold_mb: int,
                      skip_duration: bool = False, skip_delete: bool = False,
                      delete_small_mb: int = 10, dry_run: bool = False) -> dict:
    """Update mp4_status table and return statistics."""
    _section("MP4 inventory")

    threshold_bytes = threshold_mb * 1024 * 1024

    # Delete small files first; dry-run previews deletion and scans as if applied.
    ignored_mp4s: set[str] = set()
    if not skip_delete:
        _, _, ignored_mp4s = delete_small_mp4s(mp4_root, delete_small_mb, dry_run=dry_run)
        print()

    # Check ffprobe availability
    ffprobe_available = False
    if not skip_duration:
        ffprobe_available = _find_ffprobe() is not None

        if ffprobe_available:
            print("[INFO] ffprobe found - duration calculation enabled")
        else:
            print("[WARN] ffprobe not found - skipping duration calculation")
            skip_duration = True

    # Pre-fetch existing data for comparison, smart duration mode, and
    # preserving duration values when --skip-duration is used.
    existing_all = {}
    conn = sqlite3.connect(db_path)
    try:
        if not dry_run:
            ensure_mp4_table_exists(conn)
        cur = conn.cursor()
        try:
            # Only read columns this script manages
            cur.execute('SELECT recording_date, case_no, camera_name, size_mb, duration_minutes, path FROM "mp4_status"')
            for row in cur.fetchall():
                existing_all[(row[0], row[1], row[2])] = (row[3], row[4], row[5])
            if existing_all and not skip_duration:
                print(f"[INFO] Smart mode: Will only calculate duration for new/changed files")
        except sqlite3.OperationalError:
            pass
    finally:
        conn.close()

    # Scan MP4 files
    updates = {}
    duration_calculated = 0
    total_processed = 0

    print(f"[INFO] Scanning MP4 files in {mp4_root}...")

    for data_dir in mp4_root.iterdir():
        if not data_dir.is_dir() or not data_dir.name.startswith("DATA_"):
            continue
        for case_dir in data_dir.iterdir():
            if not case_dir.is_dir() or not case_dir.name.startswith("Case"):
                continue
            parsed = parse_recording_date_and_case(data_dir.name, case_dir.name)
            if not parsed:
                continue
            recording_date, case_no = parsed

            for cam in CAMERAS:
                cam_path = case_dir / cam
                key = (recording_date, case_no, cam)

                # Smart duration mode
                should_calc_duration = not skip_duration
                if should_calc_duration and key in existing_all:
                    # Quick size check first
                    status_quick, size_mb_quick, _, path_quick = compute_mp4_status(
                        cam_path,
                        threshold_bytes,
                        calculate_duration=False,
                        mp4_root=mp4_root,
                        ignored_files=ignored_mp4s,
                    )
                    old_size, old_duration, old_path = existing_all[key]

                    if size_mb_quick == old_size and old_duration is not None:
                        status, size_mb, duration, file_path = status_quick, size_mb_quick, old_duration, path_quick
                    else:
                        status, size_mb, duration, file_path = compute_mp4_status(
                            cam_path,
                            threshold_bytes,
                            calculate_duration=True,
                            mp4_root=mp4_root,
                            ignored_files=ignored_mp4s,
                        )
                        if duration is not None:
                            duration_calculated += 1
                elif not should_calc_duration and key in existing_all:
                    status, size_mb, _, file_path = compute_mp4_status(
                        cam_path,
                        threshold_bytes,
                        calculate_duration=False,
                        mp4_root=mp4_root,
                        ignored_files=ignored_mp4s,
                    )
                    _, duration, _ = existing_all[key]
                else:
                    status, size_mb, duration, file_path = compute_mp4_status(
                        cam_path,
                        threshold_bytes,
                        calculate_duration=should_calc_duration,
                        mp4_root=mp4_root,
                        ignored_files=ignored_mp4s,
                    )
                    if duration is not None:
                        duration_calculated += 1

                updates[key] = (status, size_mb, duration, file_path)
                total_processed += 1

    print(f"[OK] Scanned {total_processed:,} MP4 camera entries.")
    if not skip_duration:
        print(f"[INFO] Calculated duration for {duration_calculated} files")

    if not updates:
        print("[WARN] No MP4 files found")
        return {'total': 0, 'new': 0, 'changed': 0}

    # Check for changes (only read managed columns)
    conn = sqlite3.connect(db_path)
    try:
        if not existing_all:
            if not dry_run:
                ensure_mp4_table_exists(conn)
            cur = conn.cursor()
            try:
                # Only read columns this script manages
                cur.execute('SELECT recording_date, case_no, camera_name, size_mb, duration_minutes, path FROM "mp4_status"')
                for row in cur.fetchall():
                    existing_all[(row[0], row[1], row[2])] = (row[3], row[4], row[5])
            except sqlite3.OperationalError:
                pass

        new_entries = []
        changed_entries = []

        for key, (status, size_mb, duration, file_path) in updates.items():
            recording_date, case_no, camera_name = key
            if key not in existing_all:
                new_entries.append((recording_date, case_no, camera_name, status, size_mb, duration, file_path))
            else:
                old_size, old_duration, old_path = existing_all[key]
                if old_size != size_mb or old_duration != duration or old_path != file_path:
                    changed_entries.append((recording_date, case_no, camera_name, status, old_size, size_mb, old_duration, duration, old_path, file_path))

        unchanged = len(updates) - len(new_entries) - len(changed_entries)
        _print_inventory_counts("MP4", len(updates), len(new_entries), len(changed_entries))
        _print_mp4_changes(new_entries, changed_entries)

        return {
            'total': len(updates),
            'new': len(new_entries),
            'changed': len(changed_entries),
            'updates': updates,
            'new_entries': new_entries,
            'changed_entries': changed_entries
        }
    finally:
        conn.close()


# ============================================
# Main combined update
# ============================================
def main():
    ap = argparse.ArgumentParser(
        description="Update both seq_status and mp4_status tables",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Update both SEQ and MP4 status (with duration)
  python 2_update_db.py

  # Skip duration calculation (faster)
  python 2_update_db.py --skip-duration

  # Dry run to see what would change
  python 2_update_db.py --dry-run

  # Skip SEQ or MP4 update
  python 2_update_db.py --skip-seq
  python 2_update_db.py --skip-mp4
        """
    )

    ap.add_argument("--db", default=DEFAULT_DB_PATH, help="SQLite DB path")
    ap.add_argument("--seq-root", default=DEFAULT_SEQ_ROOT, help="Root Sequence_Backup directory")
    ap.add_argument("--mp4-root", default=DEFAULT_MP4_ROOT, help="Root Recordings directory")
    ap.add_argument("--threshold-mb", type=int, default=DEFAULT_THRESHOLD_MB, help="Size threshold in MB (default: 200)")
    ap.add_argument("--delete-small-mb", type=int, default=DEFAULT_DELETE_SMALL_MB, help="Delete MP4s smaller than this MB (default: 10)")
    ap.add_argument("--skip-seq", action="store_true", help="Skip SEQ status update")
    ap.add_argument("--skip-mp4", action="store_true", help="Skip MP4 status update")
    ap.add_argument("--skip-duration", action="store_true", help="Skip duration calculation (faster)")
    ap.add_argument("--skip-delete", action="store_true", help="Skip deleting small MP4 files")
    ap.add_argument("--skip-analysis", action="store_true", help="Skip SEQ field analysis (analyze_seq_fields)")
    ap.add_argument("--dry-run", action="store_true", help="Scan and print changes without writing to DB")
    ap.add_argument("--auto-confirm", action="store_true", help="Skip confirmation prompt")

    args = ap.parse_args()

    _section("Plan")
    print("[DRY RUN] No database writes or file deletes will be performed." if args.dry_run else "[APPLY] Database writes are enabled.")
    print(f"DB:       {args.db}")
    print(f"SEQ root: {args.seq_root}")
    print(f"MP4 root: {args.mp4_root}")
    print(f"JUNK threshold:       {args.threshold_mb} MB")
    print(f"Small MP4 threshold:  {args.delete_small_mb} MB")
    if args.skip_seq:
        print("[SKIP] SEQ inventory disabled")
    if args.skip_mp4:
        print("[SKIP] MP4 inventory disabled")
    if args.skip_duration:
        print("[SKIP] MP4 duration probing disabled; existing durations are preserved")
    if args.skip_delete:
        print("[SKIP] Small MP4 delete step disabled")
    if args.skip_analysis:
        print("[SKIP] SEQ field analysis disabled")

    start_time = time.time()

    # Update SEQ status
    seq_stats = None
    if not args.skip_seq:
        seq_root = Path(args.seq_root)
        if seq_root.exists():
            seq_stats = update_seq_status(args.db, seq_root, args.threshold_mb, args.dry_run)
        else:
            print(f"[ERROR] SEQ root not found: {seq_root}")
    else:
        print("\n[SKIP] SEQ status update skipped")

    # Update MP4 status
    mp4_stats = None
    if not args.skip_mp4:
        mp4_root = Path(args.mp4_root)
        if mp4_root.exists():
            mp4_stats = update_mp4_status(
                args.db, mp4_root, args.threshold_mb,
                skip_duration=args.skip_duration,
                skip_delete=args.skip_delete,
                delete_small_mb=args.delete_small_mb,
                dry_run=args.dry_run
            )
        else:
            print(f"[ERROR] MP4 root not found: {mp4_root}")
    else:
        print("\n[SKIP] MP4 status update skipped")

    total_changes = 0

    if seq_stats:
        total_changes += seq_stats.get('new', 0) + seq_stats.get('changed', 0)

    if mp4_stats:
        total_changes += mp4_stats.get('new', 0) + mp4_stats.get('changed', 0)

    _section("Apply changes")
    if args.dry_run:
        print(f"[DRY RUN] Would apply {total_changes:,} database change(s).")
        print("[DRY RUN] No database writes performed.")
    else:
        print(f"[APPLY] Planned database changes: {total_changes:,}")

    # Apply updates if not dry run
    if not args.dry_run and (seq_stats or mp4_stats):
        if total_changes == 0:
            print("[OK] No inventory changes detected. Database is already up to date.")
        else:
            # Confirmation
            if not args.auto_confirm:
                print(f"\n[CONFIRM] This will update {total_changes} entries in the database.")
                response = input("Do you want to proceed? (y/N): ").strip().lower()
                if response not in ['y', 'yes']:
                    print("[CANCELLED] Database update cancelled by user.")
                    return

            # Write to database
            conn = sqlite3.connect(args.db)
            try:
                ensure_seq_table_exists(conn)
                ensure_mp4_table_exists(conn)
                # The live schema contains legacy FKs from status tables to
                # recording_details, but inventory discovery often sees SEQ/MP4
                # files before case metadata has been entered there.
                seq_update_keys = _planned_update_keys(seq_stats)
                mp4_update_keys = _planned_update_keys(mp4_stats)
                _warn_missing_recording_details(conn, seq_update_keys)
                _warn_missing_recording_details(conn, mp4_update_keys)
                cur = conn.cursor()

                # Write SEQ updates (only updates managed columns: size_mb, path)
                if seq_stats and 'updates' in seq_stats:
                    for (recording_date, case_no, camera_name), (status, size_mb, file_path) in seq_stats['updates'].items():
                        if (recording_date, case_no, camera_name) not in seq_update_keys:
                            continue
                        cur.execute('''
                            INSERT INTO "seq_status"
                            (recording_date, case_no, camera_name, size_mb, path)
                            VALUES (?, ?, ?, ?, ?)
                            ON CONFLICT(recording_date, case_no, camera_name)
                            DO UPDATE SET
                                size_mb = excluded.size_mb,
                                path = excluded.path
                        ''', (recording_date, case_no, camera_name, size_mb, file_path))

                # Write MP4 updates (only updates managed columns: size_mb, duration_minutes, path)
                if mp4_stats and 'updates' in mp4_stats:
                    for (recording_date, case_no, camera_name), (status, size_mb, duration, file_path) in mp4_stats['updates'].items():
                        if (recording_date, case_no, camera_name) not in mp4_update_keys:
                            continue
                        # Use INSERT ... ON CONFLICT DO UPDATE to only update managed columns
                        # This preserves all other columns (present and future)
                        cur.execute('''
                            INSERT INTO "mp4_status"
                            (recording_date, case_no, camera_name, size_mb, duration_minutes, path)
                            VALUES (?, ?, ?, ?, ?, ?)
                            ON CONFLICT(recording_date, case_no, camera_name)
                            DO UPDATE SET
                                size_mb = excluded.size_mb,
                                duration_minutes = excluded.duration_minutes,
                                path = excluded.path
                        ''', (recording_date, case_no, camera_name, size_mb, duration, file_path))

                conn.commit()

                print("[OK] Database updated.")
                if seq_stats and seq_stats.get('updates'):
                    seq_new = seq_stats.get('new', 0)
                    seq_changed = seq_stats.get('changed', 0)
                    print(f"     SEQ: {seq_new:,} new + {seq_changed:,} changed = {seq_new + seq_changed:,} updated")
                if mp4_stats and mp4_stats.get('updates'):
                    mp4_new = mp4_stats.get('new', 0)
                    mp4_changed = mp4_stats.get('changed', 0)
                    print(f"     MP4: {mp4_new:,} new + {mp4_changed:,} changed = {mp4_new + mp4_changed:,} updated")
                print(f"     Total written: {total_changes:,}")
            finally:
                conn.close()

    # ------------------------------------------------------------------
    # SEQ field analysis (incremental — only new files)
    # ------------------------------------------------------------------
    if not args.dry_run and not args.skip_analysis:
        _section("SEQ analysis")
        if not seq_analysis_available:
            print("[SKIP] analyze_seq_fields not found — skipping")
        else:
            seq_root_path = Path(args.seq_root)
            if not seq_root_path.exists():
                print(f"[SKIP] SEQ root not found: {seq_root_path}")
            else:
                assert _seq_existing_keys is not None
                assert _analyze_seq_dir is not None
                assert _write_seq_analysis is not None
                assert _seq_canonical_map is not None
                assert _ensure_seq_analysis_table is not None
                _ensure_seq_analysis_table(args.db)
                skip_keys = _seq_existing_keys(args.db)
                if skip_keys:
                    print(f"[INFO] {len(skip_keys)} entries already in DB - scanning only new files")
                canonical_map = _seq_canonical_map(args.db)
                df = _analyze_seq_dir(
                    seq_root_path,
                    skip_keys=skip_keys,
                    canonical_camera_map=canonical_map,
                )
                if not df.empty:
                    _write_seq_analysis(df, args.db)
                else:
                    print("[OK] No new SEQ files needed analysis.")
    elif args.dry_run:
        _section("SEQ analysis")
        print("[DRY RUN] Skipped; analysis writes to seq_enriched only during real runs.")
    else:
        _section("SEQ analysis")
        print("[SKIP] SEQ field analysis disabled.")

    elapsed = time.time() - start_time
    _section("Done")
    print(f"[OK] Finished in {elapsed:.1f} seconds.")


if __name__ == "__main__":
    main()
