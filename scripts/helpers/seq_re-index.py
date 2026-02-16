#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SEQ Re-indexer - Rebuilds .idx index files for SEQ video files.

This script:
  - Deletes old/potentially corrupt .idx files
  - Launches SequenceViewer.exe to force creation of fresh index
  - Waits until .idx is created and stable (no changes for N seconds)
  - Can process all SEQ files from the database or a single file

Usage:
  # Re-index all SEQ files from database
  python seq_re-index.py --all

  # Re-index a single file
  python seq_re-index.py --file "path/to/file.seq"

  # Re-index with custom stability wait time
  python seq_re-index.py --all --stable-seconds 3

  # Dry run - show what would be processed
  python seq_re-index.py --all --dry-run
"""

import argparse
import os
import sys
import time
import subprocess
import sqlite3
from pathlib import Path

# Add parent directories to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from config import get_db_path, get_seq_root

# ==========================================
# Configuration
# ==========================================

VIEWER_PATH = r"C:\Program Files\Common Files\NorPix\SequenceViewer.exe"
DEFAULT_TIMEOUT = 120  # Max seconds to wait for index creation
DEFAULT_STABLE_SECONDS = 3  # Seconds the file must remain unchanged
MIN_IDX_SIZE = 1024  # Minimum valid index size (1KB)


# ==========================================
# Core Functions
# ==========================================

def kill_process_safely(process):
    """Safely terminate a process and its children."""
    try:
        import psutil
        parent = psutil.Process(process.pid)
        for child in parent.children(recursive=True):
            child.kill()
        parent.kill()
    except ImportError:
        # Fallback without psutil
        process.terminate()
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()
    except Exception:
        pass


def wait_for_stable_file(file_path: Path, stable_seconds: int = 3, check_interval: float = 0.5) -> bool:
    """
    Wait until file exists and its size remains stable for stable_seconds.

    Returns True if file is stable, False if it doesn't exist or keeps changing.
    """
    if not file_path.exists():
        return False

    last_size = -1
    stable_start = None

    while True:
        try:
            current_size = file_path.stat().st_size
        except OSError:
            return False

        if current_size < MIN_IDX_SIZE:
            # File too small, keep waiting
            time.sleep(check_interval)
            continue

        if current_size == last_size:
            # Size unchanged
            if stable_start is None:
                stable_start = time.time()
            elif time.time() - stable_start >= stable_seconds:
                # File has been stable for required duration
                return True
        else:
            # Size changed, reset stability timer
            last_size = current_size
            stable_start = None

        time.sleep(check_interval)

    return False


def ensure_fresh_index_with_viewer(seq_path, timeout=DEFAULT_TIMEOUT, stable_seconds=DEFAULT_STABLE_SECONDS):
    """
    Create a fresh index by opening SequenceViewer and waiting for .idx creation.

    Args:
        seq_path: Path to the .seq file
        timeout: Maximum seconds to wait for index creation
        stable_seconds: Seconds the .idx file must remain unchanged before considered complete

    Returns:
        True if index was successfully created, False otherwise
    """
    seq_path = Path(seq_path)
    idx_path = Path(str(seq_path) + '.idx')  # e.g., file.seq -> file.seq.idx

    print(f"  Re-indexing: {seq_path.name}")

    # 1. Delete old index file
    if idx_path.exists():
        try:
            os.remove(idx_path)
            print(f"    Deleted old IDX")
        except OSError as e:
            print(f"    [ERROR] Cannot delete old IDX: {e}")
            return False

    # 2. Check if SequenceViewer exists
    if not os.path.exists(VIEWER_PATH):
        print(f"    [ERROR] SequenceViewer not found at: {VIEWER_PATH}")
        return False

    # 3. Launch SequenceViewer in background
    process = None
    try:
        process = subprocess.Popen(
            [VIEWER_PATH, str(seq_path)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
        print(f"    Launched Viewer (PID: {process.pid})")

        start_time = time.time()
        idx_created = False
        last_size = -1
        stable_start = None

        # 4. Wait for index file creation and stability
        while time.time() - start_time < timeout:
            # Check if process crashed
            if process.poll() is not None:
                print("    [WARN] Viewer closed unexpectedly")
                # Check if idx was created anyway
                if idx_path.exists() and idx_path.stat().st_size >= MIN_IDX_SIZE:
                    idx_created = True
                break

            if idx_path.exists():
                try:
                    current_size = idx_path.stat().st_size
                except OSError:
                    time.sleep(0.5)
                    continue

                if current_size >= MIN_IDX_SIZE:
                    if current_size == last_size:
                        # Size unchanged
                        if stable_start is None:
                            stable_start = time.time()
                        elif time.time() - stable_start >= stable_seconds:
                            # File stable for required duration
                            idx_created = True
                            print(f"    [OK] IDX created: {current_size / 1024:.1f} KB (stable for {stable_seconds}s)")
                            break
                    else:
                        # Size changed, reset stability timer
                        last_size = current_size
                        stable_start = None

            time.sleep(0.5)

        if not idx_created and time.time() - start_time >= timeout:
            print(f"    [ERROR] Timeout after {timeout}s")

        return idx_created

    except Exception as e:
        print(f"    [ERROR] {e}")
        return False

    finally:
        # 5. Close the viewer
        if process is not None and process.poll() is None:
            kill_process_safely(process)
            print("    Closed Viewer")


def get_seq_files_from_database(db_path: str, seq_root: str) -> list[Path]:
    """
    Get all SEQ file paths from the database.

    Returns list of absolute paths to SEQ files that exist on disk.
    """
    seq_files = []
    seq_root_path = Path(seq_root)

    conn = sqlite3.connect(db_path)
    try:
        cur = conn.cursor()
        cur.execute('SELECT path FROM seq_status WHERE path IS NOT NULL')

        for (rel_path,) in cur.fetchall():
            if rel_path:
                # Path in DB is like "Sequence_Backup\DATA_...\..."
                # We need to construct full path from seq_root
                # Remove "Sequence_Backup\" prefix if present
                clean_path = rel_path
                if clean_path.startswith("Sequence_Backup\\") or clean_path.startswith("Sequence_Backup/"):
                    clean_path = clean_path[len("Sequence_Backup\\"):]

                full_path = seq_root_path / clean_path
                if full_path.exists():
                    seq_files.append(full_path)
    finally:
        conn.close()

    return seq_files


def scan_seq_files(seq_root: str) -> list[Path]:
    """
    Scan filesystem for all SEQ files.

    Returns list of paths to all .seq files found.
    """
    seq_root_path = Path(seq_root)
    return list(seq_root_path.rglob("*.seq"))


def reindex_all(db_path: str, seq_root: str, timeout: int, stable_seconds: int,
                dry_run: bool = False, use_db: bool = True) -> dict:
    """
    Re-index all SEQ files.

    Args:
        db_path: Path to SQLite database
        seq_root: Root directory for SEQ files
        timeout: Max seconds per file
        stable_seconds: Stability wait time
        dry_run: If True, only show what would be done
        use_db: If True, get files from database; if False, scan filesystem

    Returns:
        Dictionary with statistics
    """
    print("\n" + "="*60)
    print("SEQ RE-INDEXER")
    print("="*60)
    print(f"Database: {db_path}")
    print(f"SEQ Root: {seq_root}")
    print(f"Timeout: {timeout}s per file")
    print(f"Stability: {stable_seconds}s")
    if dry_run:
        print("[DRY RUN - No changes will be made]")
    print("="*60)

    # Get list of SEQ files
    if use_db:
        print("\n[INFO] Getting SEQ files from database...")
        seq_files = get_seq_files_from_database(db_path, seq_root)
    else:
        print(f"\n[INFO] Scanning for SEQ files in {seq_root}...")
        seq_files = scan_seq_files(seq_root)

    print(f"[INFO] Found {len(seq_files)} SEQ files")

    if not seq_files:
        print("[WARN] No SEQ files found")
        return {'total': 0, 'success': 0, 'failed': 0, 'skipped': 0}

    if dry_run:
        print("\n[DRY RUN] Would process these files:")
        for i, f in enumerate(seq_files[:20], 1):
            print(f"  {i}. {f.name}")
        if len(seq_files) > 20:
            print(f"  ... and {len(seq_files) - 20} more")
        return {'total': len(seq_files), 'success': 0, 'failed': 0, 'skipped': 0}

    # Process each file
    stats = {'total': len(seq_files), 'success': 0, 'failed': 0, 'skipped': 0}

    print(f"\n[INFO] Processing {len(seq_files)} files...\n")

    for i, seq_file in enumerate(seq_files, 1):
        print(f"[{i}/{len(seq_files)}] {seq_file.parent.name}/{seq_file.name}")

        success = ensure_fresh_index_with_viewer(
            seq_file,
            timeout=timeout,
            stable_seconds=stable_seconds
        )

        if success:
            stats['success'] += 1
        else:
            stats['failed'] += 1

        print()  # Blank line between files

    # Summary
    print("="*60)
    print("SUMMARY")
    print("="*60)
    print(f"Total files: {stats['total']}")
    print(f"Success: {stats['success']}")
    print(f"Failed: {stats['failed']}")
    print("="*60)

    return stats


# ==========================================
# Interactive Mode
# ==========================================

def interactive_mode():
    """Run script in interactive mode, asking user for input."""
    print("\n" + "="*60)
    print("SEQ RE-INDEXER - Interactive Mode")
    print("="*60)

    db_path = get_db_path()
    seq_root = get_seq_root()

    print(f"\nDatabase: {db_path}")
    print(f"SEQ Root: {seq_root}")

    # Ask what to do
    print("\nWhat would you like to do?")
    print("  1. Re-index ALL SEQ files (scan filesystem)")
    print("  2. Re-index ALL SEQ files (from database)")
    print("  3. Re-index a SINGLE file")
    print("  4. Dry run (show what would be processed)")
    print("  5. Exit")

    while True:
        choice = input("\nEnter choice (1-5): ").strip()
        if choice in ['1', '2', '3', '4', '5']:
            break
        print("Invalid choice. Please enter 1-5.")

    if choice == '5':
        print("Exiting.")
        sys.exit(0)

    # Get timeout setting
    timeout_input = input(f"\nTimeout per file in seconds (default {DEFAULT_TIMEOUT}): ").strip()
    timeout = int(timeout_input) if timeout_input.isdigit() else DEFAULT_TIMEOUT

    # Get stability setting
    stable_input = input(f"Stability wait in seconds (default {DEFAULT_STABLE_SECONDS}): ").strip()
    stable_seconds = int(stable_input) if stable_input.isdigit() else DEFAULT_STABLE_SECONDS

    if choice == '1':
        # Re-index all (scan filesystem)
        stats = reindex_all(
            db_path=db_path,
            seq_root=seq_root,
            timeout=timeout,
            stable_seconds=stable_seconds,
            dry_run=False,
            use_db=False
        )
        sys.exit(0 if stats['failed'] == 0 else 1)

    elif choice == '2':
        # Re-index all (from database)
        stats = reindex_all(
            db_path=db_path,
            seq_root=seq_root,
            timeout=timeout,
            stable_seconds=stable_seconds,
            dry_run=False,
            use_db=True
        )
        sys.exit(0 if stats['failed'] == 0 else 1)

    elif choice == '3':
        # Single file
        file_path = input("\nEnter full path to SEQ file: ").strip().strip('"')
        seq_path = Path(file_path)

        if not seq_path.exists():
            print(f"[ERROR] File not found: {seq_path}")
            sys.exit(1)

        print(f"\nRe-indexing: {seq_path}\n")

        success = ensure_fresh_index_with_viewer(
            seq_path,
            timeout=timeout,
            stable_seconds=stable_seconds
        )

        if success:
            print("\n[SUCCESS] Index rebuilt successfully!")
            sys.exit(0)
        else:
            print("\n[FAILED] Could not rebuild index")
            sys.exit(1)

    elif choice == '4':
        # Dry run
        print("\nDry run mode - scanning filesystem...")
        stats = reindex_all(
            db_path=db_path,
            seq_root=seq_root,
            timeout=timeout,
            stable_seconds=stable_seconds,
            dry_run=True,
            use_db=False
        )
        sys.exit(0)


# ==========================================
# Main
# ==========================================
if __name__ == "__main__":
    # If no arguments provided, run interactive mode
    if len(sys.argv) == 1:
        interactive_mode()
        sys.exit(0)

    # Otherwise, parse command-line arguments
    parser = argparse.ArgumentParser(
        description="Re-index SEQ files by creating fresh .idx files",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Interactive mode (no arguments)
  python seq_re-index.py

  # Re-index all SEQ files from database
  python seq_re-index.py --all

  # Re-index by scanning filesystem (not using database)
  python seq_re-index.py --all --scan

  # Re-index a single file
  python seq_re-index.py --file "F:\\path\\to\\file.seq"

  # Dry run - show what would be processed
  python seq_re-index.py --all --dry-run

  # Custom timeout and stability settings
  python seq_re-index.py --all --timeout 180 --stable-seconds 5
        """
    )

    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--all", action="store_true", help="Re-index all SEQ files")
    group.add_argument("--file", type=str, help="Re-index a single SEQ file")

    parser.add_argument("--db", default=None, help="SQLite database path")
    parser.add_argument("--seq-root", default=None, help="Root directory for SEQ files")
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT,
                        help=f"Max seconds to wait per file (default: {DEFAULT_TIMEOUT})")
    parser.add_argument("--stable-seconds", type=int, default=DEFAULT_STABLE_SECONDS,
                        help=f"Seconds file must be stable (default: {DEFAULT_STABLE_SECONDS})")
    parser.add_argument("--scan", action="store_true",
                        help="Scan filesystem instead of using database")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would be done without making changes")

    args = parser.parse_args()

    if args.all:
        # Get paths from config or arguments
        db_path = args.db or get_db_path()
        seq_root = args.seq_root or get_seq_root()

        stats = reindex_all(
            db_path=db_path,
            seq_root=seq_root,
            timeout=args.timeout,
            stable_seconds=args.stable_seconds,
            dry_run=args.dry_run,
            use_db=not args.scan
        )

        # Exit with error code if any failures
        sys.exit(0 if stats['failed'] == 0 else 1)

    elif args.file:
        seq_path = Path(args.file)

        if not seq_path.exists():
            print(f"[ERROR] File not found: {seq_path}")
            sys.exit(1)

        print(f"\nRe-indexing single file: {seq_path}\n")

        success = ensure_fresh_index_with_viewer(
            seq_path,
            timeout=args.timeout,
            stable_seconds=args.stable_seconds
        )

        if success:
            print("\n[SUCCESS] Index rebuilt successfully!")
            sys.exit(0)
        else:
            print("\n[FAILED] Could not rebuild index")
            sys.exit(1)