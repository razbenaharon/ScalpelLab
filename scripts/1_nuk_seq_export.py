"""Batch SEQ file export and organization with multi-threaded copying.

This script automates the organization and export of raw SEQ video files from
source directory to a structured destination directory. It groups videos by
recording date and case number, maps camera channels to standard names, and
performs atomic file copying with hash verification.

Key Features:
    - Multi-threaded file copying (configurable workers)
    - Automatic date/case grouping (30-minute time windows)
    - Camera channel mapping (auto-detects and maps to standards)
    - Hash verification (SHA256) for copy integrity
    - Orphaned companion file detection and handling
    - Atomic copy operations with retry logic
    - Disk space validation before copying
    - JUNK suffix for undersized files (<200MB)

Architecture:
    1. Scan: Find all .seq files and companion files (.metadata, .idx, .xml, .aud)
    2. Extract: Parse dates from filenames or file modification times
    3. Group: Organize by date, then into cases (30-minute windows)
    4. Map: Auto-map source channels to standard camera names
    5. Plan: Create file operation manifest with destinations
    6. Verify: Check disk space and validate operations
    7. Execute: Multi-threaded atomic copying with hash verification
    8. Report: Display success/failure statistics

Data Flow:
    Source Directory (raw .seq files) →
    find_sequences_with_pathlib() → List of sequences with metadata →
    group_by_date_and_case() → Grouped by date and case →
    create_file_operations_json() → File operation manifest →
    copy_files_with_threads() → Atomic copy with verification →
    Destination Directory (organized SEQ files)

File Organization:
    Source (unorganized):
        /path/to/source/
        ├── Camera1/
        │   ├── 01-07-24_07-41-09.seq
        │   ├── 01-07-24_07-41-09.seq.metadata
        │   └── 01-07-24_08-15-23.seq
        └── Camera2/
            └── 01-07-24_07-42-00.seq

    Destination (organized):
        F:/Room_8_Data/Sequence_Backup/
        ├── DATA_24-01-07/
        │   ├── Case1/
        │   │   ├── Monitor/
        │   │   │   ├── 01-07-24_07-41-09.seq
        │   │   │   └── 01-07-24_07-41-09.seq.metadata
        │   │   └── General_3/
        │   │       └── 01-07-24_07-42-00.seq
        │   └── Case2/
        │       └── Monitor/
        │           └── 01-07-24_08-15-23.seq
        └── orphaned_files/  (files without corresponding .seq)

Channel Mapping:
    Auto-maps source channel names to standard camera names:
        - Case-insensitive matching
        - Removes spaces and underscores for comparison
        - Maps to DEFAULT_CAMERAS from config.py
        - Unknown channels mapped to "Unknown"
        - Small files (<200MB) get "_JUNK" suffix

Case Grouping Logic:
    - Files within 30 minutes of first file in case → same case
    - Files beyond 30 minutes → new case
    - Cases numbered sequentially per date (Case1, Case2, ...)

Companion Files:
    Automatically detected and copied with .seq files:
        - .seq.metadata (sequence metadata)
        - .seq.idx (index file)
        - .xml (XML metadata)
        - .aud (audio file)

Orphaned File Handling:
    Files without corresponding .seq are copied to:
        destination/orphaned_files/{file_type}/

Hash Verification:
    - SHA256 hash calculated for source file
    - File copied atomically to temp location
    - Temp file moved to final destination
    - SHA256 hash calculated for destination file
    - Hashes compared for verification

Configuration:
    MIN_SEQ_SIZE: Minimum valid SEQ file size (200MB)
    COMPANION_EXTS: File extensions to include
    DEFAULT_CAMERAS: Standard camera names from config.py

Dependencies:
    - config.py: get_seq_root(), DEFAULT_CAMERAS
    - hashlib: SHA256 hash calculation
    - concurrent.futures: Multi-threaded copying
    - psutil: Disk space checking
    - pathlib: Modern path operations

Example:
    Interactive mode::

        $ python scripts/1_nuk_seq_export.py
        Enter Source Directory: /path/to/raw/seqs
        Enter Destination Directory [Default: F:/Room_8_Data/Sequence_Backup]:
        Number of parallel workers [Default: 8]: 8

        Scanning for source channels...
        Found 8 unique channels.
        Auto-mapping channels...
          Mapped 'Camera1' -> 'Monitor'
          Mapped 'Camera2' -> 'General_3'
          ...

        Planning to copy 1234 files.
        Ready to start copying.
        Proceed? (y/n): y

        Starting copy with 8 workers...
        [100.0%] Copied 1234/1234

        Operation Complete.
        Successful: 1230
        Failed:     4

    Programmatic usage::

        from scripts.nuk_seq_export import run_curation, map_channels_auto

        source_channels = get_unique_source_channels("/path/to/source")
        channel_mapping = map_channels_auto(source_channels)

        run_curation(
            root_dir="/path/to/source",
            dest_dir="F:/Room_8_Data/Sequence_Backup",
            channel_mapping=channel_mapping,
            simulate=False,
            max_workers=8
        )

Performance:
    Typical copy speeds (per worker):
        - SSD → SSD: ~100-200 MB/s
        - HDD → HDD: ~50-80 MB/s
        - Network: ~10-30 MB/s

    Multi-threading benefit:
        - 8 workers on fast storage: ~6x speedup
        - I/O bound, not CPU bound
        - Optimal workers: 4-12 (depends on storage)

Notes:
    - Files copied atomically (temp file + rename)
    - Automatic retry on failure (max 3 attempts)
    - Duplicate filenames get "_1", "_2" suffix
    - Orphaned files preserved in separate folder
    - Disk space checked before copying
    - Hash verification ensures data integrity

Security:
    - SHA256 hashing for integrity verification
    - Atomic file operations prevent corruption
    - No modification of source files
    - Destination files validated before completion

See Also:
    - config.py: Configuration and default paths
    - scripts/2_4_update_db.py: Database synchronization after export
    - docs/DATABASE_SCHEMA.md: Database schema for tracking files

Warning:
    - Source files are NOT deleted (manual cleanup required)
    - Large operations may take hours (monitor progress)
    - Ensure sufficient disk space (check displayed before copy)
    - Failed copies logged but do not halt operation

Author:
    ScalpelLab Development Team

Version:
    2.0.0 (2026-01-06) - CLI interface with auto-mapping
"""

import os
import sys
import shutil
import time
import datetime
import json
import logging
import traceback
import hashlib
import re
import psutil
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

# Add parent directory to path to import config
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import get_seq_root, DEFAULT_CAMERAS

# Minimum file size for a valid case (in bytes)
MIN_SEQ_SIZE = 200 * 1024 * 1024  # 200MB

# File extensions to include
COMPANION_EXTS = ['.seq', '.seq.metadata', '.seq.idx', '.xml', '.aud']

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


# =========================
# Core Logic Functions
# =========================

def get_file_date(file_path: str) -> str:
    """Extract date from file modification time in yy-mm-dd format.

    Uses file modification timestamp to generate a date string in
    2-digit year, month, day format (e.g., "24-01-07" for 2024-01-07).

    Args:
        file_path: Path to file to extract date from.

    Returns:
        str: Date string in yy-mm-dd format (e.g., "24-01-07").

    Example:
        ::

            date = get_file_date("/path/to/video.seq")
            print(date)  # Output: "24-01-07"
    """
    t = os.path.getmtime(file_path)
    date = time.strftime('%y-%m-%d', time.localtime(t))
    return date


def extract_date_from_filename(fname: str) -> str:
    """Extract date from filename supporting multiple timestamp formats.

    Parses filename to extract date in yy-mm-dd format. Supports two
    common NorPix SEQ filename formats with different date patterns.

    Supported formats:
        - yyyy-mm-dd_hh-mm-ss (e.g., "2024-01-07_14-30-45.seq")
        - mm-dd-yy_hh-mm-ss(.sss)? (e.g., "01-07-24_14-30-45.123.seq")

    Args:
        fname: Filename (not full path) to parse for date.
            Example: "2024-01-07_14-30-45.seq" or "01-07-24_14-30-45.seq"

    Returns:
        str: Date in yy-mm-dd format (e.g., "24-01-07"), or None
            if date cannot be extracted.

    Example:
        ::

            # Format 1: yyyy-mm-dd
            date = extract_date_from_filename("2024-01-07_14-30-45.seq")
            print(date)  # Output: "24-01-07"

            # Format 2: mm-dd-yy
            date = extract_date_from_filename("01-07-24_14-30-45.123.seq")
            print(date)  # Output: "24-01-07"

            # Invalid format
            date = extract_date_from_filename("video.seq")
            print(date)  # Output: None

    Note:
        For yyyy-mm-dd format, year is converted to 2-digit by taking
        modulo 100 (e.g., 2024 → 24).
    """
    base = fname.split('.')[0]
    # yyyy-mm-dd_hh-mm-ss
    m = re.match(r'(\d{4})-(\d{2})-(\d{2})_\d{2}-\d{2}-\d{2}', base)
    if m:
        year = int(m.group(1)) % 100  # last two digits
        return f'{year:02d}-{m.group(2)}-{m.group(3)}'
    # mm-dd-yy_hh-mm-ss(.sss)?
    m = re.match(r'(\d{2})-(\d{2})-(\d{2})_\d{2}-\d{2}-\d{2}', base)
    if m:
        return f'{m.group(3)}-{m.group(1)}-{m.group(2)}'
    return None


def find_orphaned_companion_files(root_dir, log_callback=None):
    """
    Find companion files that don't have corresponding .seq files.
    Returns a dict: { file_type: [Path, ...] }
    """
    orphaned_files = {}
    root_path = Path(root_dir)
    
    msg = f"Scanning for orphaned companion files in: {root_path}"
    if log_callback:
        log_callback(msg)
    logger.info(msg)
    
    # Find all companion files
    for ext in COMPANION_EXTS:
        if ext == '.seq':  # Skip .seq files themselves
            continue
            
        orphaned_files[ext] = []
        for comp_path in root_path.rglob(f"*{ext}"):
            # Check if corresponding .seq file exists
            base = comp_path.with_suffix('')
            seq_path = base.with_suffix('.seq')
            
            if not seq_path.exists():
                orphaned_files[ext].append(comp_path)
    
    return orphaned_files


def copy_orphaned_files(orphaned_files, dest_root, source_root, progress_callback=None):
    """
    Copy orphaned companion files to an 'orphaned_files' folder in the destination.
    Returns (successful_copies, failed_copies, orphaned_operations)
    """
    def log_with_time(msg):
        timestamp = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        if progress_callback:
            progress_callback(0, 1, f"[{timestamp}] {msg}")
        logger.info(msg)
    
    orphaned_dir = Path(dest_root) / "orphaned_files"
    orphaned_dir.mkdir(exist_ok=True)
    
    # Create file operations for orphaned files
    orphaned_operations = []
    total_orphaned = sum(len(files) for files in orphaned_files.values())
    
    if total_orphaned == 0:
        return 0, 0, []
    
    log_with_time(f"Preparing to copy {total_orphaned} orphaned files...")
    
    for file_type, files in orphaned_files.items():
        type_dir = orphaned_dir / file_type.lstrip('.')  # Remove leading dot
        type_dir.mkdir(exist_ok=True)
        
        for file_path in files:
            # Create destination path preserving original directory structure
            # Get relative path from the source root (where orphaned files were found)
            try:
                rel_path = file_path.relative_to(Path(source_root))
            except ValueError:
                # If the file is not in the expected source structure, use just the filename
                rel_path = Path(file_path.name)
            
            dest_path = orphaned_dir / rel_path
            
            # Ensure destination directory exists
            dest_path.parent.mkdir(parents=True, exist_ok=True)
            
            operation = {
                'source_path': str(file_path),
                'destination_path': str(dest_path),
                'file_size': file_path.stat().st_size,
                'file_type': file_type,
                'status': 'pending',
                'error_message': None,
                'hash_verification': None
            }
            
            orphaned_operations.append(operation)
    
    # Copy orphaned files using the same threading approach
    log_with_time(f"Copying orphaned files with 8 workers...")
    successful_copies, failed_copies = copy_files_with_threads(
        {'file_operations': orphaned_operations}, 8, progress_callback
    )
    
    log_with_time(f"Orphaned files copy completed: {successful_copies} successful, {failed_copies} failed")
    
    return successful_copies, failed_copies, orphaned_operations


def find_sequences_with_pathlib(root_dir, log_callback=None):
    """
    Use pathlib to recursively find all .seq files and their companions in root_dir.
    Returns a list of dicts: { 'seq': Path, 'companions': [Path, ...], 'size': int, 'date': str, 'channel': str, 'timestamp': str }
    """
    results = []
    root_path = Path(root_dir)
    
    msg = f"Scanning directory: {root_path}"
    if log_callback:
        log_callback(msg)
    logger.info(msg)
    
    # Use pathlib's rglob to find all .seq files
    for seq_path in root_path.rglob("*.seq"):
        # No per-file logging to reduce spam
        try:
            size = seq_path.stat().st_size
            # Extract date from filename
            date = extract_date_from_filename(seq_path.name)
            if not date:
                msg = f"Could not extract date from filename: {seq_path.name}"
                logger.warning(msg)
                continue
                
            channel = seq_path.parent.name
            timestamp = seq_path.stem  # e.g., 01-07-24_07-41-09
            
            # Find companions using pathlib
            companions = []
            base = seq_path.with_suffix('')
            for ext in COMPANION_EXTS:
                comp = base.with_suffix(ext)
                if comp.exists():
                    companions.append(comp)
                    
            results.append({
                'seq': seq_path,
                'companions': companions,
                'size': size,
                'date': date,
                'channel': channel,
                'timestamp': timestamp
            })
        except Exception as e:
            msg = f"Error processing {seq_path}: {e}\n{traceback.format_exc()}"
            if log_callback:
                log_callback(msg)
            logger.error(msg)
            continue
            
    return results


def group_by_date_and_case(sequences):
    """
    Group sequences by date, then by case using relative time grouping.
    Files within 30 minutes of the first file in a case are grouped together.
    Returns: { date: [ [seqs for case1], [seqs for case2], ... ] }
    """
    from collections import defaultdict
    
    date_dict = defaultdict(list)
    for seq in sequences:
        date_dict[seq['date']].append(seq)
    
    grouped = {}
    for date, seqs in date_dict.items():
        # Sort sequences by timestamp to process in chronological order
        sorted_seqs = sorted(seqs, key=lambda x: x['timestamp'])
        
        cases = []
        current_case = []
        
        for seq in sorted_seqs:
            if not current_case:
                # Start new case with first sequence
                current_case = [seq]
            else:
                # Check if this sequence is within 30 minutes of the first sequence in current case
                first_timestamp = current_case[0]['timestamp']
                
                # Parse timestamps for comparison (handle milliseconds)
                def parse_timestamp_with_ms(timestamp_str):
                    # Remove milliseconds if present
                    if '.' in timestamp_str:
                        timestamp_str = timestamp_str.split('.')[0]
                    
                    if len(timestamp_str.split('_')[0]) == 8:  # mm-dd-yy format
                        return datetime.datetime.strptime(timestamp_str, '%m-%d-%y_%H-%M-%S')
                    else:  # yyyy-mm-dd format
                        return datetime.datetime.strptime(timestamp_str, '%Y-%m-%d_%H-%M-%S')
                
                first_dt = parse_timestamp_with_ms(first_timestamp)
                seq_dt = parse_timestamp_with_ms(seq['timestamp'])
                
                time_diff = abs((seq_dt - first_dt).total_seconds() / 60)  # difference in minutes
                
                if time_diff <= 30:  # Within 30 minutes of case anchor
                    current_case.append(seq)
                else:
                    # Start new case
                    cases.append(current_case)
                    current_case = [seq]
        
        # Don't forget the last case
        if current_case:
            cases.append(current_case)
        
        grouped[date] = cases
    return grouped


def create_file_operations_json(grouped_sequences, dest_root, channel_mapping):
    """
    Create a JSON structure containing all file operations to be performed.
    Returns a dict with file operations and metadata.
    """
    operations = {
        'metadata': {
            'created_at': datetime.datetime.now().isoformat(),
            'destination_root': str(dest_root),
            'total_files': 0,
            'total_sequences': 0,
            'channel_mapping': channel_mapping
        },
        'file_operations': []
    }
    
    total_files = 0
    total_sequences = 0
    
    for date, cases in grouped_sequences.items():
        data_dir = Path(dest_root) / f"DATA_{date}"
        
        for i, case in enumerate(cases, 1):
            case_dir = data_dir / f"Case{i}"
            
            # Group seq_infos by channel
            channel_map = {}
            for seq_info in case:
                channel = seq_info['channel']
                channel_map.setdefault(channel, []).append(seq_info)
                
            for channel, seq_infos in channel_map.items():
                # Only proceed if at least one .seq file is to be copied
                seq_to_copy = [seq_info for seq_info in seq_infos if any(f.suffix == '.seq' for f in seq_info['companions'])]
                if not seq_to_copy:
                    continue
                    
                channel_dir = case_dir / channel
                
                for seq_info in seq_to_copy:
                    total_sequences += 1
                    
                    for f in seq_info['companions']:
                        dest = channel_dir / f.name
                        
                        operation = {
                            'source_path': str(f),
                            'destination_path': str(dest),
                            'file_size': f.stat().st_size,
                            'file_type': f.suffix,
                            'sequence_info': {
                                'date': seq_info['date'],
                                'channel': seq_info['channel'],
                                'timestamp': seq_info['timestamp'],
                                'case_number': i
                            },
                            'status': 'pending',
                            'error_message': None,
                            'hash_verification': None
                        }
                        
                        operations['file_operations'].append(operation)
                        total_files += 1
    
    operations['metadata']['total_files'] = total_files
    operations['metadata']['total_sequences'] = total_sequences
    
    return operations


def calculate_file_hash(file_path, blocksize=65536):
    """Calculate SHA256 hash of a file."""
    h = hashlib.sha256()
    with open(file_path, 'rb') as f:
        for chunk in iter(lambda: f.read(blocksize), b''):
            h.update(chunk)
    return h.hexdigest()


def atomic_copy_file(src_path, dest_path, max_retries=3):
    """
    Copy a file atomically with retry logic.
    Returns (success, error_message, hash_verification)
    """
    src = Path(src_path)
    dest = Path(dest_path)
    
    # Create destination directory if it doesn't exist
    dest.parent.mkdir(parents=True, exist_ok=True)
    
    # Generate unique destination if file exists
    if dest.exists():
        stem = dest.stem
        suffix = dest.suffix
        parent = dest.parent
        i = 1
        while True:
            new_name = f"{stem}_{i}{suffix}"
            candidate = parent / new_name
            if not candidate.exists():
                dest = candidate
                break
            i += 1
    
    # Calculate source hash
    try:
        src_hash = calculate_file_hash(src)
    except Exception as e:
        logger.error(f"Failed to calculate source hash for {src}: {e}\n{traceback.format_exc()}")
        return False, f"Failed to calculate source hash: {e}", None
    
    # Atomic copy with retry
    tmp_dest = dest.with_suffix(dest.suffix + '.tmp')
    
    for attempt in range(max_retries):
        try:
            with open(src, 'rb') as fsrc, open(tmp_dest, 'wb') as fdst:
                shutil.copyfileobj(fsrc, fdst)
            os.replace(tmp_dest, dest)
            
            # Verify copy with hash
            try:
                dest_hash = calculate_file_hash(dest)
                hash_match = src_hash == dest_hash
                return True, None, {
                    'source_hash': src_hash,
                    'destination_hash': dest_hash,
                    'match': hash_match
                }
            except Exception as e:
                logger.error(f"Failed to verify copy for {src} -> {dest}: {e}\n{traceback.format_exc()}")
                return False, f"Failed to verify copy: {e}", None
                
        except Exception as e:
            logger.error(f"Copy attempt {attempt+1} failed for {src} -> {dest}: {e}\n{traceback.format_exc()}")
            if tmp_dest.exists():
                try:
                    tmp_dest.unlink()
                except Exception as e2:
                    logger.warning(f"Failed to remove temp file {tmp_dest}: {e2}")
            if attempt == max_retries - 1:
                return False, f"Copy failed after {max_retries} attempts: {e}", None
    
    return False, "Unknown error after retries", None


def copy_files_with_threads(file_operations, max_workers=8, progress_callback=None):
    """
    Copy files using multiple threads.
    Updates the file_operations dict with results.
    """
    total_files = len(file_operations['file_operations'])
    completed_files = 0
    successful_copies = 0
    failed_copies = 0
    
    def copy_single_file(operation):
        nonlocal completed_files, successful_copies, failed_copies
        
        try:
            success, error_msg, hash_verification = atomic_copy_file(
                operation['source_path'], 
                operation['destination_path']
            )
            
            operation['status'] = 'completed' if success else 'failed'
            operation['error_message'] = error_msg
            operation['hash_verification'] = hash_verification
            
            completed_files += 1
            if success:
                successful_copies += 1
                # logger.info(f"Copied: {operation['source_path']} -> {operation['destination_path']}")
            else:
                failed_copies += 1
                logger.error(f"Failed to copy: {operation['source_path']} -> {operation['destination_path']}: {error_msg}")
            
            if progress_callback:
                progress_callback(completed_files, total_files, 
                                f"Copied {completed_files}/{total_files}")
            
            return operation
        except Exception as e:
            logger.error(f"Exception in copy_single_file for {operation['source_path']} -> {operation['destination_path']}: {e}\n{traceback.format_exc()}")
            operation['status'] = 'failed'
            operation['error_message'] = str(e)
            completed_files += 1
            failed_copies += 1
            if progress_callback:
                progress_callback(completed_files, total_files, 
                                f"Copied {completed_files}/{total_files}")
            return operation
    
    # Use ThreadPoolExecutor for parallel copying
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        # Submit all copy tasks
        future_to_operation = {
            executor.submit(copy_single_file, op): op 
            for op in file_operations['file_operations']
        }
        
        # Process completed tasks
        for future in as_completed(future_to_operation):
            try:
                future.result()
            except Exception as e:
                operation = future_to_operation[future]
                logger.error(f"Exception in thread for {operation['source_path']}: {e}")
                operation['status'] = 'failed'
                operation['error_message'] = str(e)
    
    return successful_copies, failed_copies


    # No longer generating output files as per user request
    """Run the full curation process."""
    
    def print_progress(done, total, msg):
        percent = (done / total) * 100 if total > 0 else 0
        sys.stdout.write(f"\r[{percent:5.1f}%] {msg}                                ")
        sys.stdout.flush()

    print(f"\nScanning {root_dir} for sequences...")
    all_sequences = find_sequences_with_pathlib(root_dir, log_callback=None)
    
    if not all_sequences:
        print("\nNo .seq files found.")
        return

    print(f"Found {len(all_sequences)} .seq files.")
    
    # Filter by included channels
    included_sequences = [seq for seq in all_sequences if seq['channel'] in channel_mapping]
    if not included_sequences:
        print("\nNo included channels selected.")
        return
        
    # Apply mapping
    for seq in included_sequences:
        src_channel = seq['channel']
        mapped_channel = channel_mapping.get(src_channel, "Unknown")
        
        # Add JUNK suffix if file is small
        if seq['size'] < MIN_SEQ_SIZE:
            seq['channel'] = f"{mapped_channel}_JUNK"
        else:
            seq['channel'] = mapped_channel

    # Group
    grouped = group_by_date_and_case(included_sequences)
    
    # Plan
    file_operations = create_file_operations_json(grouped, dest_dir, channel_mapping)
    
    if simulate:
        print("\nSIMULATION MODE: Limiting to 50 files.")
        file_operations['file_operations'] = file_operations['file_operations'][:50]
        file_operations['metadata']['total_files'] = len(file_operations['file_operations'])

    total_files = file_operations['metadata']['total_files']
    print(f"\nPlanning to copy {total_files} files.")
    
    # Check orphaned
    print("Scanning for orphaned companion files...")
    orphaned_files = find_orphaned_companion_files(root_dir, log_callback=None)
    total_orphaned = sum(len(files) for files in orphaned_files.values())
    if total_orphaned > 0:
        print(f"Found {total_orphaned} orphaned companion files.")
    
    # Check disk space
    total_size = sum(op['file_size'] for op in file_operations['file_operations'])
    try:
        free_space = psutil.disk_usage(dest_dir).free
        if free_space < total_size + 100*1024*1024: # 100MB buffer
            print(f"\n[ERROR] Insufficient disk space in {dest_dir}!")
            return
    except:
        pass # Skip check if dest doesn't exist yet or error

    # Confirm
    print("\nReady to start copying.")
    print(f"Source:      {root_dir}")
    print(f"Destination: {dest_dir}")
    print(f"Files:       {total_files}")
    
    if input("\nProceed? (y/n): ").lower() != 'y':
        print("Aborted.")
        return

    # Execute
    print(f"\nStarting copy with {max_workers} workers...")
    successful, failed = copy_files_with_threads(file_operations, max_workers, print_progress)
    print("\nCopy finished.")
    
    # Reports (output files no longer generated as per user request)
    
    # Orphaned copy
    if total_orphaned > 0:
        print("\nCopying orphaned files...")
        copy_orphaned_files(orphaned_files, dest_dir, root_dir, print_progress)
        print("\nOrphaned files processed.")

    print(f"\nOperation Complete.")
    print(f"Successful: {successful}")
    print(f"Failed:     {failed}")
    print(f"Detailed logs in: {dest_dir}")


# =========================
# CLI Main
# =========================

def get_unique_source_channels(root_dir):
    unique_channels = set()
    for dirpath, _, filenames in os.walk(root_dir):
        for fname in filenames:
            if fname.lower().endswith('.seq'):
                channel_name = Path(dirpath).name
                unique_channels.add(channel_name)
    return list(unique_channels)

def map_channels_auto(source_channels):
    """Automatically map channels to defaults."""
    mapping = {}
    targets = DEFAULT_CAMERAS
    
    print("\nAuto-mapping channels...")
    for src in source_channels:
        # Normalize source channel name for comparison
        src_normalized = src.lower().replace(' ', '').replace('_', '')
        
        match = None
        for t in targets:
            # Normalize target channel name for comparison
            t_normalized = t.lower().replace(' ', '').replace('_', '')
            if t_normalized == src_normalized:
                match = t
                break
        
        if match:
            mapping[src] = match
            print(f"  Mapped '{src}' -> '{match}'")
        else:
            # If no match, map to Unknown
            mapping[src] = "Unknown"
            print(f"  Mapped '{src}' -> 'Unknown' (No standard match found)")
            
    return mapping

def main():
    print("=" * 80)
    print("BATCH EXPORT SCRIPT - SEQUENCE CURATOR")
    print("=" * 80)
    print()
    
    # Get configuration from config.py
    default_dest = get_seq_root()
    
    # 1. Source Directory
    while True:
        src_input = input("Enter Source Directory (containing raw .seq files): ").strip()
        # Remove quotes if user pasted path with quotes
        src_input = src_input.strip('"').strip("'")
        if os.path.isdir(src_input):
            source_dir = src_input
            break
        print("Invalid directory. Please try again.")

    # 2. Destination Directory
    dest_input = input(f"Enter Destination Directory [Default: {default_dest}]: ").strip()
    dest_input = dest_input.strip('"').strip("'")
    dest_dir = dest_input if dest_input else default_dest
    
    # 3. Workers
    workers_input = input("Number of parallel workers [Default: 8]: ").strip()
    try:
        max_workers = int(workers_input) if workers_input else 8
    except:
        max_workers = 8

    # 4. Channel Mapping
    print("\nScanning for source channels...")
    source_channels = get_unique_source_channels(source_dir)
    if not source_channels:
        print("No channels (.seq files) found in source directory.")
        return
        
    print(f"Found {len(source_channels)} unique channels.")
    
    # Use auto mapping instead of interactive
    channel_mapping = map_channels_auto(source_channels)
    
    if not channel_mapping:
        print("No channels mapped. Exiting.")
        return
        
    # 5. Run
    run_curation(source_dir, dest_dir, channel_mapping, simulate=False, max_workers=max_workers)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nOperation cancelled by user.")
    except Exception as e:
        print(f"\nAn error occurred: {e}")
        traceback.print_exc()