#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
Update SQLite table `mp4_status` based on presence/size/duration of MP4s per camera.

Status per camera:
  1 = at least one .mp4 >= THRESHOLD_MB
  2 = some .mp4 exist but all < THRESHOLD_MB
  3 = no .mp4 found

Expected directory layout:
  <ROOT>\DATA_YY-MM-DD\CaseN\<CameraName>\*.mp4
  e.g. F:\Room_8_Data\Recordings\DATA_23-02-05\Case1\Cart_Center_2\Cart_Center_2_4.mp4

The script:
  - Walks the tree and computes status/size/duration per camera for each case
  - Uses ffprobe to extract video duration (optional, can skip with --skip-duration)
  - Smart mode: Only calculates duration for new or changed files (fast!)
  - INSERT OR REPLACE camera data in the database

Performance:
  - First run with duration: May take time (ffprobe call per file)
  - Subsequent runs: Very fast (only processes new/changed files)
  - Use --skip-duration for fastest updates (size only)
"""

import argparse
import os
import re
import sqlite3
import time
import subprocess
import json
from pathlib import Path

# ---------- Defaults (edit if needed) ----------
DEFAULT_DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "ScalpelDatabase.sqlite")
DEFAULT_ROOT    = r"F:\Room_8_Data\Recordings"
DEFAULT_TABLE   = "mp4_status"
DEFAULT_THRESHOLD_MB = 200
DEFAULT_DELETE_SMALL_MB = 10  # Delete files smaller than this

CAMERAS = [
    "Cart_Center_2","Cart_LT_4","Cart_RT_1",
    "General_3","Monitor","Patient_Monitor",
    "Ventilator_Monitor","Injection_Port"
]
# ------------------------------------------------

def parse_recording_date_and_case(data_dir_name: str, case_dir_name: str) -> tuple[str, int] | None:
    """Convert DATA_YY-MM-DD + CaseN -> (YYYY-MM-DD, N) (e.g., DATA_23-02-05 + Case1 -> ('2023-02-05', 1))."""
    m = re.fullmatch(r"DATA_(\d{2})-(\d{2})-(\d{2})", data_dir_name)
    n = re.fullmatch(r"Case(\d+)", case_dir_name)
    if not m or not n:
        return None
    yy, mm, dd = m.groups()
    yyyy = f"20{yy}" if int(yy) <= 69 else f"19{yy}"
    case_no = int(n.group(1))
    return f"{yyyy}-{mm}-{dd}", case_no

def get_video_duration(video_path: Path) -> float | None:
    """
    Get video duration in seconds using ffprobe.
    Returns None if ffprobe is not available or if duration cannot be determined.
    """
    try:
        # Try to find ffprobe
        ffprobe_paths = [
            r"C:\Program Files\ffmpeg\bin\ffprobe.exe",
            r"C:\ffmpeg\bin\ffprobe.exe",
            r"C:\Program Files (x86)\ffmpeg\bin\ffprobe.exe",
            "ffprobe"  # Try from PATH
        ]

        ffprobe_cmd = None
        for path in ffprobe_paths:
            if path == "ffprobe":
                # Try from PATH
                try:
                    result = subprocess.run(
                        ["where" if os.name == 'nt' else "which", "ffprobe"],
                        capture_output=True,
                        text=True,
                        timeout=5
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

        # Get duration using ffprobe
        cmd = [
            ffprobe_cmd,
            "-v", "error",
            "-show_entries", "format=duration",
            "-of", "json",
            str(video_path)
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

def compute_camera_status(camera_dir: Path, threshold_bytes: int, calculate_duration: bool = True) -> tuple[int, int | None, float | None]:
    """
    Return (status, size_mb, duration_seconds) for a single camera directory:
      - status: 1 if any .mp4 >= threshold, 2 if .mp4 exist but all < threshold, 3 if no .mp4
      - size_mb: largest .mp4 file size in MB (None if no files found)
      - duration_seconds: duration of the largest .mp4 file (None if no files or ffprobe unavailable or skipped)
    """
    if not camera_dir.is_dir():
        return 3, None, None
    max_size = 0
    largest_file = None
    found_any = False
    # Search recursively (handles nested exports)
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

    # Get duration of the largest file (only if requested)
    duration = None
    if calculate_duration and largest_file:
        duration = get_video_duration(largest_file)

    return status, size_mb, duration

def ensure_table_exists(conn: sqlite3.Connection, table: str) -> None:
    """
    Ensure the table exists with the new normalized structure.
    """
    cur = conn.cursor()
    cur.execute(f"""
        CREATE TABLE IF NOT EXISTS "{table}" (
            recording_date TEXT NOT NULL,
            case_no INTEGER NOT NULL,
            camera_name TEXT NOT NULL,
            size_mb INTEGER,
            duration_seconds REAL,
            PRIMARY KEY (recording_date, case_no, camera_name)
        );
    """)

    # Add duration_seconds column if it doesn't exist (for existing tables)
    try:
        cur.execute(f'ALTER TABLE "{table}" ADD COLUMN duration_seconds REAL')
        conn.commit()
        print("[INFO] Added duration_seconds column to table")
    except sqlite3.OperationalError:
        # Column already exists
        pass

    conn.commit()

def get_existing_data(conn: sqlite3.Connection, table: str, recording_date: str, case_no: int) -> dict:
    """Get existing size_mb and duration values for a case, return dict {camera_name: (size_mb, duration_seconds)}."""
    cur = conn.cursor()
    try:
        cur.execute(f'SELECT camera_name, size_mb, duration_seconds FROM "{table}" WHERE recording_date = ? AND case_no = ?', (recording_date, case_no))
        rows = cur.fetchall()
        return {row[0]: (row[1], row[2]) for row in rows}
    except sqlite3.OperationalError:
        return {}

def delete_small_mp4s(root: Path, threshold_mb: int) -> tuple[int, float]:
    """Delete all MP4 files smaller than threshold_mb. Returns (count, total_size_mb)."""
    threshold_bytes = threshold_mb * 1024 * 1024
    deleted_count = 0
    total_size_mb = 0.0
    failed_deletions = []
    found_count = 0

    print(f"[INFO] Scanning for MP4 files < {threshold_mb}MB to delete...")

    for mp4_file in root.rglob("*.mp4"):
        if mp4_file.is_file():
            try:
                size_bytes = mp4_file.stat().st_size
                if size_bytes < threshold_bytes:
                    found_count += 1
                    size_mb = size_bytes / (1024 * 1024)
                    # Try up to 3 times with small delays (handles transient locks)
                    success = False
                    for attempt in range(3):
                        try:
                            mp4_file.unlink()
                            print(f"[DELETED] {mp4_file} ({size_mb:.1f}MB)")
                            deleted_count += 1
                            total_size_mb += size_mb
                            success = True
                            break
                        except PermissionError as e:
                            if attempt < 2:
                                time.sleep(0.1)  # Wait 100ms and retry
                            else:
                                failed_deletions.append((mp4_file, size_mb, str(e)))
                                print(f"[ERROR] Failed to delete {mp4_file}: {e}")
                        except Exception as e:
                            failed_deletions.append((mp4_file, size_mb, str(e)))
                            print(f"[ERROR] Failed to delete {mp4_file}: {e}")
                            break
            except OSError:
                continue

    if failed_deletions:
        print(f"\n[WARNING] {len(failed_deletions)} file(s) could not be deleted (may be in use):")
        for path, size, _ in failed_deletions:
            print(f"  {path} ({size:.1f}MB)")
        print(f"[TIP] Close any programs that may be using these files and run again.")

    if deleted_count > 0:
        print(f"[INFO] Deleted {deleted_count}/{found_count} small MP4 files, freed {total_size_mb:.1f}MB")
    elif found_count > 0:
        print(f"[INFO] Found {found_count} small MP4 files but none could be deleted")
    else:
        print(f"[INFO] No MP4 files smaller than {threshold_mb}MB found")

    return deleted_count, total_size_mb

def upsert_camera_data(conn: sqlite3.Connection, table: str, recording_date: str, case_no: int, camera_name: str, size_mb: int | None, duration_seconds: float | None) -> None:
    """Insert or update a single camera's data."""
    cur = conn.cursor()
    cur.execute(f'''
        INSERT OR REPLACE INTO "{table}"
        (recording_date, case_no, camera_name, size_mb, duration_seconds)
        VALUES (?, ?, ?, ?, ?)
    ''', (recording_date, case_no, camera_name, size_mb, duration_seconds))

def main():
    ap = argparse.ArgumentParser(description="Update mp4_status (1/2/3) based on mp4 sizes per camera.")
    ap.add_argument("--db", default=DEFAULT_DB_PATH, help="SQLite DB path")
    ap.add_argument("--root", default=DEFAULT_ROOT, help="Root Recordings directory")
    ap.add_argument("--table", default=DEFAULT_TABLE, help="Table name (default: mp4_status)")
    ap.add_argument("--threshold-mb", type=int, default=DEFAULT_THRESHOLD_MB, help="Size threshold in MB (default: 200)")
    ap.add_argument("--delete-small-mb", type=int, default=DEFAULT_DELETE_SMALL_MB, help="Delete files smaller than this MB (default: 5)")
    ap.add_argument("--skip-delete", action="store_true", help="Skip deleting small files")
    ap.add_argument("--skip-duration", action="store_true", help="Skip duration calculation (faster)")
    ap.add_argument("--dry-run", action="store_true", help="Scan and print changes without writing to DB")
    args = ap.parse_args()

    root = Path(args.root)
    if not root.exists():
        raise SystemExit(f"[ERROR] Root path not found: {root}")

    threshold_bytes = args.threshold_mb * 1024 * 1024

    # Check ffprobe availability if duration calculation is enabled
    if not args.skip_duration:
        # Quick check for ffprobe
        ffprobe_available = False
        try:
            result = subprocess.run(
                ["where" if os.name == 'nt' else "which", "ffprobe"],
                capture_output=True,
                text=True,
                timeout=5
            )
            if result.returncode == 0:
                ffprobe_available = True
        except Exception:
            pass

        if not ffprobe_available:
            # Check common paths
            for path in [r"C:\Program Files\ffmpeg\bin\ffprobe.exe", r"C:\ffmpeg\bin\ffprobe.exe"]:
                if os.path.exists(path):
                    ffprobe_available = True
                    break

        if ffprobe_available:
            print("[INFO] ffprobe found - duration calculation enabled")
        else:
            print("[WARN] ffprobe not found - duration will not be calculated")
            print("[WARN] Install ffmpeg to enable duration calculation")
            args.skip_duration = True  # Auto-skip if not available

    # Delete small MP4 files first (unless skipped or dry-run)
    if not args.skip_delete and not args.dry_run:
        deleted_count, freed_mb = delete_small_mp4s(root, args.delete_small_mb)
        if deleted_count > 0:
            print()
    elif args.dry_run:
        print(f"[DRY-RUN] Would delete MP4 files < {args.delete_small_mb}MB")

    # Pre-connect to DB if not dry-run, to fetch existing data for smart duration calculation
    existing_all = {}
    if not args.dry_run and not args.skip_duration:
        conn = sqlite3.connect(args.db)
        try:
            ensure_table_exists(conn, args.table)
            cur = conn.cursor()
            try:
                cur.execute(f'SELECT recording_date, case_no, camera_name, size_mb, duration_seconds FROM "{args.table}"')
                for row in cur.fetchall():
                    key = (row[0], row[1], row[2])
                    existing_all[key] = (row[3], row[4])
            except sqlite3.OperationalError:
                pass
        finally:
            conn.close()

    if args.skip_duration:
        print("[INFO] Skipping duration calculation (--skip-duration)")
    elif existing_all:
        print(f"[INFO] Smart duration mode: Will only calculate duration for new/changed files")

    # Collect statuses per (recording_date, case_no, camera_name)
    updates: dict[tuple[str, int, str], tuple[int, int, float | None]] = {}

    print("[INFO] Scanning MP4 files...")
    total_processed = 0
    duration_calculated = 0

    for data_dir in root.iterdir():
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

                # Determine if we should calculate duration
                should_calc_duration = not args.skip_duration
                key = (recording_date, case_no, cam)

                # If we have existing data, first check size to see if file changed
                if should_calc_duration and key in existing_all:
                    # Quick size check first (fast)
                    status_quick, size_mb_quick, _ = compute_camera_status(cam_path, threshold_bytes, calculate_duration=False)
                    old_size, old_duration = existing_all[key]

                    # Only calculate duration if size changed or duration missing
                    if size_mb_quick == old_size and old_duration is not None:
                        should_calc_duration = False
                        status, size_mb, duration = status_quick, size_mb_quick, old_duration
                    else:
                        status, size_mb, duration = compute_camera_status(cam_path, threshold_bytes, calculate_duration=True)
                        if duration is not None:
                            duration_calculated += 1
                else:
                    status, size_mb, duration = compute_camera_status(cam_path, threshold_bytes, calculate_duration=should_calc_duration)
                    if duration is not None:
                        duration_calculated += 1

                updates[key] = (status, size_mb, duration)
                total_processed += 1

                # Progress indicator every 10 files
                if total_processed % 10 == 0:
                    print(f"  Processed {total_processed} cameras...", end='\r')

    print(f"  Processed {total_processed} cameras.    ")
    if not args.skip_duration:
        print(f"[INFO] Calculated duration for {duration_calculated} files")

    if not updates:
        print("[WARN] Found no cases to update.")
        return

    # Show summary
    print(f"[INFO] Found {len(updates)} camera entries")

    if args.dry_run:
        for (recording_date, case_no, camera_name), (status, size_mb, duration) in sorted(updates.items()):
            duration_str = f"{duration:.1f}s" if duration else "N/A"
            print(f"  {recording_date} Case {case_no} {camera_name}: status={status}, size={size_mb}MB, duration={duration_str}")
        return

    # Connect to DB to check existing values and update
    conn = sqlite3.connect(args.db)
    try:
        if not existing_all:  # Only create table if we didn't already connect earlier
            ensure_table_exists(conn, args.table)

        # If we didn't fetch existing data earlier, fetch it now
        if not existing_all:
            cur = conn.cursor()
            try:
                cur.execute(f'SELECT recording_date, case_no, camera_name, size_mb, duration_seconds FROM "{args.table}"')
                for row in cur.fetchall():
                    key = (row[0], row[1], row[2])
                    existing_all[key] = (row[3], row[4])
            except sqlite3.OperationalError:
                pass

        # Check for changes
        changes = []
        new_cameras = []

        for (recording_date, case_no, camera_name), (new_status, new_size, new_duration) in updates.items():
            key = (recording_date, case_no, camera_name)
            if key not in existing_all:
                new_cameras.append((recording_date, case_no, camera_name, new_status, new_size, new_duration))
            else:
                old_size, old_duration = existing_all[key]
                if old_size != new_size or old_duration != new_duration:
                    changes.append((recording_date, case_no, camera_name, new_status, old_size, new_size, old_duration, new_duration))

        # Show what will be changed
        if new_cameras:
            print(f"\n[NEW] {len(new_cameras)} new camera entries will be added:")
            for recording_date, case_no, camera_name, status, size_mb, duration in new_cameras:
                duration_str = f"{duration:.1f}s" if duration else "N/A"
                print(f"  {recording_date} Case {case_no} {camera_name}: status={status}, size={size_mb}MB, duration={duration_str}")

        if changes:
            print(f"\n[CHANGES] {len(changes)} existing camera entries will be updated:")
            for recording_date, case_no, camera_name, new_status, old_size, new_size, old_duration, new_duration in changes:
                old_size_str = str(old_size) if old_size is not None else "NULL"
                new_size_str = str(new_size) if new_size is not None else "NULL"
                old_duration_str = f"{old_duration:.1f}s" if old_duration else "NULL"
                new_duration_str = f"{new_duration:.1f}s" if new_duration else "NULL"
                status_label = {1: ">=200MB", 2: "<200MB", 3: "Missing"}.get(new_status, str(new_status))
                print(f"  {recording_date} Case {case_no} {camera_name}: {status_label}, size {old_size_str}->{new_size_str}MB, duration {old_duration_str}->{new_duration_str}")

        if not new_cameras and not changes:
            print("\n[INFO] No changes detected. Database is already up to date.")
            return

        # Ask for confirmation
        print(f"\n[CONFIRM] This will update {len(new_cameras) + len(changes)} camera entries in the database.")
        response = input("Do you want to proceed? (y/N): ").strip().lower()

        if response not in ['y', 'yes']:
            print("[CANCELLED] Database update cancelled by user.")
            return

        # Write to DB
        for (recording_date, case_no, camera_name), (status, size_mb, duration) in updates.items():
            upsert_camera_data(conn, args.table, recording_date, case_no, camera_name, size_mb, duration)
        conn.commit()
        print(f"[OK] Updated '{args.table}' with {len(new_cameras) + len(changes)} camera entries (threshold {args.threshold_mb} MB).")
    finally:
        conn.close()

if __name__ == "__main__":
    main()
