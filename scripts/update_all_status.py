#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
Combined status updater - Updates both seq_status and mp4_status tables in one run.

This script:
  - Updates seq_status: Scans SEQ files in Sequence_Backup
  - Updates mp4_status: Scans MP4 files in Recordings (with optional duration)
  - Shows combined statistics and changes
  - Requires single confirmation for both updates

Performance:
  - First run with duration: ~5-10 minutes (ffprobe for all MP4s)
  - Subsequent runs: ~1 minute (smart mode - only new/changed files)
  - Use --skip-duration for fastest updates (~30 seconds)
"""

import argparse
import os
import re
import sqlite3
import time
import subprocess
import json
from pathlib import Path

# ============================================
# Defaults
# ============================================
DEFAULT_DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "ScalpelDatabase.sqlite")
DEFAULT_SEQ_ROOT = r"F:\Room_8_Data\Sequence_Backup"
DEFAULT_MP4_ROOT = r"F:\Room_8_Data\Recordings"
DEFAULT_THRESHOLD_MB = 200
DEFAULT_DELETE_SMALL_MB = 10

CAMERAS = [
    "Cart_Center_2", "Cart_LT_4", "Cart_RT_1",
    "General_3", "Monitor", "Patient_Monitor",
    "Ventilator_Monitor", "Injection_Port"
]

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


def ensure_seq_table_exists(conn: sqlite3.Connection) -> None:
    """Ensure seq_status table exists."""
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS "seq_status" (
            recording_date TEXT NOT NULL,
            case_no INTEGER NOT NULL,
            camera_name TEXT NOT NULL,
            size_mb INTEGER,
            PRIMARY KEY (recording_date, case_no, camera_name)
        );
    """)
    conn.commit()


def ensure_mp4_table_exists(conn: sqlite3.Connection) -> None:
    """Ensure mp4_status table exists with duration column."""
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS "mp4_status" (
            recording_date TEXT NOT NULL,
            case_no INTEGER NOT NULL,
            camera_name TEXT NOT NULL,
            size_mb INTEGER,
            duration_seconds REAL,
            PRIMARY KEY (recording_date, case_no, camera_name)
        );
    """)

    # Add duration_seconds column if it doesn't exist
    try:
        cur.execute('ALTER TABLE "mp4_status" ADD COLUMN duration_seconds REAL')
        conn.commit()
        print("[INFO] Added duration_seconds column to mp4_status table")
    except sqlite3.OperationalError:
        pass

    conn.commit()


# ============================================
# SEQ status functions
# ============================================
def compute_seq_status(camera_dir: Path, threshold_bytes: int) -> tuple[int, int | None]:
    """Return (status, size_mb) for SEQ files in camera directory."""
    if not camera_dir.is_dir():
        return 3, None
    max_size = 0
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
    if not found_any:
        return 3, None
    status = 1 if max_size >= threshold_bytes else 2
    size_mb = int(max_size / (1024 * 1024))
    return status, size_mb


def update_seq_status(db_path: str, seq_root: Path, threshold_mb: int, dry_run: bool = False) -> dict:
    """Update seq_status table and return statistics."""
    print("\n" + "="*60)
    print("UPDATING SEQ STATUS")
    print("="*60)

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
                status, size_mb = compute_seq_status(cam_path, threshold_bytes)
                updates[(recording_date, case_no, cam)] = (status, size_mb)

    if not updates:
        print("[WARN] No SEQ files found")
        return {'total': 0, 'new': 0, 'changed': 0}

    print(f"[INFO] Found {len(updates)} camera entries")

    if dry_run:
        return {'total': len(updates), 'new': 0, 'changed': 0}

    # Check for changes
    conn = sqlite3.connect(db_path)
    try:
        ensure_seq_table_exists(conn)
        cur = conn.cursor()

        existing = {}
        try:
            cur.execute('SELECT recording_date, case_no, camera_name, size_mb FROM "seq_status"')
            for row in cur.fetchall():
                existing[(row[0], row[1], row[2])] = row[3]
        except sqlite3.OperationalError:
            pass

        new_entries = []
        changed_entries = []

        for key, (status, size_mb) in updates.items():
            recording_date, case_no, camera_name = key
            if key not in existing:
                new_entries.append((recording_date, case_no, camera_name, status, size_mb))
            elif existing[key] != size_mb:
                old_size = existing[key]
                changed_entries.append((recording_date, case_no, camera_name, status, old_size, size_mb))

        # Show detailed changes
        if new_entries:
            print(f"\n  [NEW] {len(new_entries)} new entries:")
            for recording_date, case_no, camera_name, status, size_mb in new_entries[:10]:
                status_label = {1: ">=200MB", 2: "<200MB", 3: "Missing"}.get(status, str(status))
                size_str = f"{size_mb}MB" if size_mb is not None else "NULL"
                print(f"    {recording_date} Case{case_no} {camera_name}: {status_label} ({size_str})")
            if len(new_entries) > 10:
                print(f"    ... and {len(new_entries) - 10} more")

        if changed_entries:
            print(f"\n  [CHANGED] {len(changed_entries)} changed entries:")
            for recording_date, case_no, camera_name, status, old_size, new_size in changed_entries[:10]:
                status_label = {1: ">=200MB", 2: "<200MB", 3: "Missing"}.get(status, str(status))
                old_str = f"{old_size}MB" if old_size is not None else "NULL"
                new_str = f"{new_size}MB" if new_size is not None else "NULL"
                print(f"    {recording_date} Case{case_no} {camera_name}: {status_label} ({old_str} -> {new_str})")
            if len(changed_entries) > 10:
                print(f"    ... and {len(changed_entries) - 10} more")

        unchanged = len(updates) - len(new_entries) - len(changed_entries)
        if unchanged > 0:
            print(f"\n  [UNCHANGED] {unchanged} entries")

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
# MP4 status functions (with duration)
# ============================================
def get_video_duration(video_path: Path) -> float | None:
    """Get video duration in seconds using ffprobe."""
    try:
        ffprobe_paths = [
            r"C:\Program Files\ffmpeg\bin\ffprobe.exe",
            r"C:\ffmpeg\bin\ffprobe.exe",
            r"C:\Program Files (x86)\ffmpeg\bin\ffprobe.exe",
            "ffprobe"
        ]

        ffprobe_cmd = None
        for path in ffprobe_paths:
            if path == "ffprobe":
                try:
                    result = subprocess.run(
                        ["where" if os.name == 'nt' else "which", "ffprobe"],
                        capture_output=True, text=True, timeout=5
                    )
                    if result.returncode == 0:
                        ffprobe_cmd = result.stdout.strip().split('\n')[0]
                        break
                except Exception:
                    continue
            else:
                if os.path.exists(path):
                    ffprobe_cmd = path
                    break

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
                return float(duration_str)
    except Exception:
        pass
    return None


def compute_mp4_status(camera_dir: Path, threshold_bytes: int, calculate_duration: bool = True) -> tuple[int, int | None, float | None]:
    """Return (status, size_mb, duration_seconds) for MP4 files in camera directory."""
    if not camera_dir.is_dir():
        return 3, None, None
    max_size = 0
    largest_file = None
    found_any = False

    for p in camera_dir.rglob("*.mp4"):
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

    duration = None
    if calculate_duration and largest_file:
        duration = get_video_duration(largest_file)

    return status, size_mb, duration


def delete_small_mp4s(root: Path, threshold_mb: int) -> tuple[int, float]:
    """Delete MP4 files smaller than threshold_mb."""
    threshold_bytes = threshold_mb * 1024 * 1024
    deleted_count = 0
    total_size_mb = 0.0

    print(f"[INFO] Scanning for MP4 files < {threshold_mb}MB to delete...")

    for mp4_file in root.rglob("*.mp4"):
        if mp4_file.is_file():
            try:
                size_bytes = mp4_file.stat().st_size
                if size_bytes < threshold_bytes:
                    size_mb = size_bytes / (1024 * 1024)
                    for attempt in range(3):
                        try:
                            mp4_file.unlink()
                            print(f"[DELETED] {mp4_file.name} ({size_mb:.1f}MB)")
                            deleted_count += 1
                            total_size_mb += size_mb
                            break
                        except PermissionError:
                            if attempt < 2:
                                time.sleep(0.1)
                        except Exception:
                            break
            except OSError:
                continue

    if deleted_count > 0:
        print(f"[INFO] Deleted {deleted_count} small MP4 files, freed {total_size_mb:.1f}MB")
    else:
        print(f"[INFO] No MP4 files smaller than {threshold_mb}MB found")

    return deleted_count, total_size_mb


def update_mp4_status(db_path: str, mp4_root: Path, threshold_mb: int,
                      skip_duration: bool = False, skip_delete: bool = False,
                      delete_small_mb: int = 10, dry_run: bool = False) -> dict:
    """Update mp4_status table and return statistics."""
    print("\n" + "="*60)
    print("UPDATING MP4 STATUS")
    print("="*60)

    threshold_bytes = threshold_mb * 1024 * 1024

    # Delete small files first
    if not skip_delete and not dry_run:
        delete_small_mp4s(mp4_root, delete_small_mb)
        print()

    # Check ffprobe availability
    ffprobe_available = False
    if not skip_duration:
        try:
            result = subprocess.run(
                ["where" if os.name == 'nt' else "which", "ffprobe"],
                capture_output=True, text=True, timeout=5
            )
            ffprobe_available = result.returncode == 0
        except Exception:
            pass

        if ffprobe_available:
            print("[INFO] ffprobe found - duration calculation enabled")
        else:
            print("[WARN] ffprobe not found - skipping duration calculation")
            skip_duration = True

    # Pre-fetch existing data for smart mode
    existing_all = {}
    if not dry_run and not skip_duration:
        conn = sqlite3.connect(db_path)
        try:
            ensure_mp4_table_exists(conn)
            cur = conn.cursor()
            try:
                cur.execute('SELECT recording_date, case_no, camera_name, size_mb, duration_seconds FROM "mp4_status"')
                for row in cur.fetchall():
                    existing_all[(row[0], row[1], row[2])] = (row[3], row[4])
                if existing_all:
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
                    status_quick, size_mb_quick, _ = compute_mp4_status(cam_path, threshold_bytes, calculate_duration=False)
                    old_size, old_duration = existing_all[key]

                    if size_mb_quick == old_size and old_duration is not None:
                        status, size_mb, duration = status_quick, size_mb_quick, old_duration
                    else:
                        status, size_mb, duration = compute_mp4_status(cam_path, threshold_bytes, calculate_duration=True)
                        if duration is not None:
                            duration_calculated += 1
                else:
                    status, size_mb, duration = compute_mp4_status(cam_path, threshold_bytes, calculate_duration=should_calc_duration)
                    if duration is not None:
                        duration_calculated += 1

                updates[key] = (status, size_mb, duration)
                total_processed += 1

                if total_processed % 20 == 0:
                    print(f"  Processed {total_processed} cameras...", end='\r')

    print(f"  Processed {total_processed} cameras.    ")
    if not skip_duration:
        print(f"[INFO] Calculated duration for {duration_calculated} files")

    if not updates:
        print("[WARN] No MP4 files found")
        return {'total': 0, 'new': 0, 'changed': 0}

    print(f"[INFO] Found {len(updates)} camera entries")

    if dry_run:
        return {'total': len(updates), 'new': 0, 'changed': 0}

    # Check for changes
    conn = sqlite3.connect(db_path)
    try:
        if not existing_all:
            ensure_mp4_table_exists(conn)
            cur = conn.cursor()
            try:
                cur.execute('SELECT recording_date, case_no, camera_name, size_mb, duration_seconds FROM "mp4_status"')
                for row in cur.fetchall():
                    existing_all[(row[0], row[1], row[2])] = (row[3], row[4])
            except sqlite3.OperationalError:
                pass

        new_entries = []
        changed_entries = []

        for key, (status, size_mb, duration) in updates.items():
            recording_date, case_no, camera_name = key
            if key not in existing_all:
                new_entries.append((recording_date, case_no, camera_name, status, size_mb, duration))
            else:
                old_size, old_duration = existing_all[key]
                if old_size != size_mb or old_duration != duration:
                    changed_entries.append((recording_date, case_no, camera_name, status, old_size, size_mb, old_duration, duration))

        # Show detailed changes
        if new_entries:
            print(f"\n  [NEW] {len(new_entries)} new entries:")
            for recording_date, case_no, camera_name, status, size_mb, duration in new_entries[:10]:
                status_label = {1: ">=200MB", 2: "<200MB", 3: "Missing"}.get(status, str(status))
                size_str = f"{size_mb}MB" if size_mb is not None else "NULL"
                duration_str = f"{duration:.1f}s" if duration is not None else "N/A"
                print(f"    {recording_date} Case{case_no} {camera_name}: {status_label} ({size_str}, {duration_str})")
            if len(new_entries) > 10:
                print(f"    ... and {len(new_entries) - 10} more")

        if changed_entries:
            print(f"\n  [CHANGED] {len(changed_entries)} changed entries:")
            for recording_date, case_no, camera_name, status, old_size, new_size, old_duration, new_duration in changed_entries[:10]:
                status_label = {1: ">=200MB", 2: "<200MB", 3: "Missing"}.get(status, str(status))
                old_size_str = f"{old_size}MB" if old_size is not None else "NULL"
                new_size_str = f"{new_size}MB" if new_size is not None else "NULL"
                old_dur_str = f"{old_duration:.1f}s" if old_duration is not None else "N/A"
                new_dur_str = f"{new_duration:.1f}s" if new_duration is not None else "N/A"
                print(f"    {recording_date} Case{case_no} {camera_name}: {status_label}")
                print(f"      Size: {old_size_str} -> {new_size_str}, Duration: {old_dur_str} -> {new_dur_str}")
            if len(changed_entries) > 10:
                print(f"    ... and {len(changed_entries) - 10} more")

        unchanged = len(updates) - len(new_entries) - len(changed_entries)
        if unchanged > 0:
            print(f"\n  [UNCHANGED] {unchanged} entries")

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
  python update_all_status.py

  # Skip duration calculation (faster)
  python update_all_status.py --skip-duration

  # Dry run to see what would change
  python update_all_status.py --dry-run

  # Skip SEQ or MP4 update
  python update_all_status.py --skip-seq
  python update_all_status.py --skip-mp4
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
    ap.add_argument("--dry-run", action="store_true", help="Scan and print changes without writing to DB")
    ap.add_argument("--auto-confirm", action="store_true", help="Skip confirmation prompt")

    args = ap.parse_args()

    print("\n" + "="*60)
    print("COMBINED STATUS UPDATER")
    print("="*60)
    print(f"Database: {args.db}")
    print(f"SEQ Root: {args.seq_root}")
    print(f"MP4 Root: {args.mp4_root}")
    print(f"Threshold: {args.threshold_mb}MB")
    if args.dry_run:
        print("[DRY RUN MODE - No changes will be made]")
    print("="*60)

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

    # Summary
    print("\n" + "="*60)
    print("SUMMARY")
    print("="*60)

    total_changes = 0

    if seq_stats:
        print(f"\nSEQ Status:")
        print(f"  Total: {seq_stats['total']}")
        print(f"  New: {seq_stats.get('new', 0)}")
        print(f"  Changed: {seq_stats.get('changed', 0)}")
        print(f"  Unchanged: {seq_stats['total'] - seq_stats.get('new', 0) - seq_stats.get('changed', 0)}")
        total_changes += seq_stats.get('new', 0) + seq_stats.get('changed', 0)

        # Show some changes
        if seq_stats.get('new_entries'):
            print(f"\n  Recent new SEQ entries (showing up to 5):")
            for recording_date, case_no, camera_name, status, size_mb in seq_stats['new_entries'][:5]:
                status_label = {1: ">=200MB", 2: "<200MB", 3: "Missing"}.get(status, str(status))
                size_str = f"{size_mb}MB" if size_mb is not None else "NULL"
                print(f"    + {recording_date} Case{case_no} {camera_name}: {status_label} ({size_str})")

        if seq_stats.get('changed_entries'):
            print(f"\n  Recent changed SEQ entries (showing up to 5):")
            for recording_date, case_no, camera_name, status, old_size, new_size in seq_stats['changed_entries'][:5]:
                status_label = {1: ">=200MB", 2: "<200MB", 3: "Missing"}.get(status, str(status))
                old_str = f"{old_size}MB" if old_size is not None else "NULL"
                new_str = f"{new_size}MB" if new_size is not None else "NULL"
                print(f"    ~ {recording_date} Case{case_no} {camera_name}: {old_str} -> {new_str}")

    if mp4_stats:
        print(f"\nMP4 Status:")
        print(f"  Total: {mp4_stats['total']}")
        print(f"  New: {mp4_stats.get('new', 0)}")
        print(f"  Changed: {mp4_stats.get('changed', 0)}")
        print(f"  Unchanged: {mp4_stats['total'] - mp4_stats.get('new', 0) - mp4_stats.get('changed', 0)}")
        total_changes += mp4_stats.get('new', 0) + mp4_stats.get('changed', 0)

        # Show some changes
        if mp4_stats.get('new_entries'):
            print(f"\n  Recent new MP4 entries (showing up to 5):")
            for recording_date, case_no, camera_name, status, size_mb, duration in mp4_stats['new_entries'][:5]:
                status_label = {1: ">=200MB", 2: "<200MB", 3: "Missing"}.get(status, str(status))
                size_str = f"{size_mb}MB" if size_mb is not None else "NULL"
                duration_str = f"{duration:.1f}s" if duration is not None else "N/A"
                print(f"    + {recording_date} Case{case_no} {camera_name}: {status_label} ({size_str}, {duration_str})")

        if mp4_stats.get('changed_entries'):
            print(f"\n  Recent changed MP4 entries (showing up to 5):")
            for recording_date, case_no, camera_name, status, old_size, new_size, old_duration, new_duration in mp4_stats['changed_entries'][:5]:
                old_size_str = f"{old_size}MB" if old_size is not None else "NULL"
                new_size_str = f"{new_size}MB" if new_size is not None else "NULL"
                old_dur_str = f"{old_duration:.1f}s" if old_duration is not None else "N/A"
                new_dur_str = f"{new_duration:.1f}s" if new_duration is not None else "N/A"
                print(f"    ~ {recording_date} Case{case_no} {camera_name}")
                print(f"      Size: {old_size_str} -> {new_size_str}, Duration: {old_dur_str} -> {new_dur_str}")

    elapsed = time.time() - start_time
    print(f"\nTotal changes to apply: {total_changes}")
    print(f"Time elapsed: {elapsed:.1f} seconds")
    print("="*60)

    # Apply updates if not dry run
    if not args.dry_run and (seq_stats or mp4_stats):
        if total_changes == 0:
            print("\n[INFO] No changes detected. Database is already up to date.")
            return

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
            cur = conn.cursor()

            # Write SEQ updates
            if seq_stats and 'updates' in seq_stats:
                for (recording_date, case_no, camera_name), (status, size_mb) in seq_stats['updates'].items():
                    cur.execute('''
                        INSERT OR REPLACE INTO "seq_status"
                        (recording_date, case_no, camera_name, size_mb)
                        VALUES (?, ?, ?, ?)
                    ''', (recording_date, case_no, camera_name, size_mb))

            # Write MP4 updates
            if mp4_stats and 'updates' in mp4_stats:
                for (recording_date, case_no, camera_name), (status, size_mb, duration) in mp4_stats['updates'].items():
                    cur.execute('''
                        INSERT OR REPLACE INTO "mp4_status"
                        (recording_date, case_no, camera_name, size_mb, duration_seconds)
                        VALUES (?, ?, ?, ?, ?)
                    ''', (recording_date, case_no, camera_name, size_mb, duration))

            conn.commit()

            print(f"\n{'='*60}")
            print("[SUCCESS] Database updated!")
            print("="*60)
            if seq_stats and seq_stats.get('updates'):
                seq_new = seq_stats.get('new', 0)
                seq_changed = seq_stats.get('changed', 0)
                print(f"SEQ: {seq_new} new + {seq_changed} changed = {seq_new + seq_changed} updated")
            if mp4_stats and mp4_stats.get('updates'):
                mp4_new = mp4_stats.get('new', 0)
                mp4_changed = mp4_stats.get('changed', 0)
                print(f"MP4: {mp4_new} new + {mp4_changed} changed = {mp4_new + mp4_changed} updated")
            print(f"Total: {total_changes} entries written to database")
            print("="*60)
        finally:
            conn.close()


if __name__ == "__main__":
    main()
