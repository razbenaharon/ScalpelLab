"""
Batch Convert Script - FFmpeg GPU with CLExport Fallback
Exports SEQ files to MP4 using GPU-accelerated FFmpeg
Falls back to CLExport if GPU encoding fails
Much more stable for large batches
"""

import sqlite3
import subprocess
import sys
import os
import time
import threading
from pathlib import Path
from datetime import datetime
from typing import Dict, Tuple

# Add parent directory to path to import config
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import get_db_path, get_seq_root, get_mp4_root, DEFAULT_CAMERAS

# Configuration (from config.py)
DB_PATH = get_db_path()
SEQ_ROOT = get_seq_root()
OUT_ROOT = get_mp4_root()

# FFmpeg possible locations
FFMPEG_PATHS = [
    r"C:\\Program Files\\ffmpeg\\bin\\ffmpeg.exe",
    r"C:\\ffmpeg\\bin\\ffmpeg.exe",
    r"C:\\Program Files (x86)\\ffmpeg\\bin\\ffmpeg.exe",
    "ffmpeg",  # Try from PATH
]

MIN_VALID_FILE_SIZE_MB = 1.0  # Minimum size for valid MP4 file


# =========================
# Utility Functions
# =========================
def find_ffmpeg():
    """Find ffmpeg executable in common locations or PATH"""
    for path in FFMPEG_PATHS:
        if path == "ffmpeg":
            # Try from PATH
            try:
                result = subprocess.run(
                    ["where" if os.name == 'nt' else "which", "ffmpeg"],
                    capture_output=True,
                    text=True,
                    timeout=5
                )
                if result.returncode == 0:
                    ffmpeg_path = result.stdout.strip().split('\n')[0]
                    if os.path.exists(ffmpeg_path):
                        return ffmpeg_path
            except Exception:
                pass
        else:
            if os.path.exists(path):
                return path
    return None


def is_valid_video_file(file_path: Path, min_size_mb: float = MIN_VALID_FILE_SIZE_MB) -> bool:
    """Check if video file exists and has reasonable size."""
    if not file_path.exists():
        return False
    try:
        size_mb = file_path.stat().st_size / (1024 * 1024)
        return size_mb > min_size_mb
    except:
        return False


def get_next_available_filename(out_dir: Path, base_stem: str, extension: str) -> Tuple[str, Path]:
    """Get next available filename that doesn't exist."""
    # First try without counter
    filename = f"{base_stem}{extension}"
    file_path = out_dir / filename

    if not file_path.exists():
        return filename, file_path

    # Try with counter
    counter = 1
    while counter < 1000:  # Safety limit
        filename = f"{base_stem}_{counter}{extension}"
        file_path = out_dir / filename
        if not file_path.exists():
            return filename, file_path
        counter += 1

    raise ValueError(f"Could not find available filename for {base_stem} after 1000 attempts")


def resolve_channel_label(seq_path: Path, channel_names: Dict[str, str]) -> str:
    """
    Choose the output base name using mapping (stem -> filename -> fullpath -> parent name),
    falling back to parent folder name or stem.
    """
    stem = seq_path.stem
    name = seq_path.name
    full = str(seq_path.resolve())
    parent_name = seq_path.parent.name if seq_path.parent else ""

    return (
            channel_names.get(stem)
            or channel_names.get(name)
            or channel_names.get(full)
            or channel_names.get(parent_name)
            or parent_name
            or stem
    )

def compute_out_dir(seq_path: Path, out_root_path: Path) -> Path:
    """
    Decide where to write the output.
    - If the input path includes a DATA_* anchor, mirror from there.
    - Otherwise, fall back to DATA_Unknown/CaseUnknown/<Channel>.
    """
    parts = seq_path.parts
    anchor_idx = None
    for i, part in enumerate(parts):
        if part.upper().startswith("DATA_"):
            anchor_idx = i
            break

    if anchor_idx is not None:
        rel_from_data = Path(*parts[anchor_idx:])
        out_dir = out_root_path / rel_from_data.parent
    else:
        channel = seq_path.parent.name if seq_path.parent else "ChannelUnknown"
        case = seq_path.parent.parent.name if seq_path.parent and seq_path.parent.parent else "CaseUnknown"
        date = seq_path.parent.parent.parent.name if seq_path.parent and seq_path.parent.parent and seq_path.parent.parent.parent else "DATA_Unknown"
        if not str(date).upper().startswith("DATA_"):
            date = "DATA_Unknown"
        out_dir = out_root_path / str(date) / str(case) / str(channel)

    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir


# =========================
# Database Functions
# =========================
def connect_db(db_path):
    """Connect to SQLite database."""
    return sqlite3.connect(db_path)

def get_missing_mp4_files(db_path, cameras=None, limit=None):
    """
    Get all SEQ files that don't have MP4s.

    Args:
        db_path: Path to database
        cameras: List of camera names to filter (None = all)
        limit: Maximum number of files to return (None = all)

    Returns:
        List of dicts with file info
    """
    if cameras is None:
        cameras = DEFAULT_CAMERAS

    conn = connect_db(db_path)
    cursor = conn.cursor()

    query = """
    SELECT
        s.recording_date,
        s.case_no,
        s.camera_name,
        s.size_mb as seq_size_mb
    FROM seq_status s
    LEFT JOIN mp4_status m
        ON s.recording_date = m.recording_date
        AND s.case_no = m.case_no
        AND s.camera_name = m.camera_name
    WHERE
        s.camera_name IN ({})
        AND s.size_mb >= 200  -- Only valid SEQ files
        AND (m.size_mb IS NULL OR m.size_mb < 1)  -- Missing or invalid MP4
    ORDER BY s.recording_date DESC, s.case_no, s.camera_name
    """.format(','.join(['?'] * len(cameras)))

    if limit:
        query += f" LIMIT {limit}"

    cursor.execute(query, cameras)

    files = []
    for row in cursor.fetchall():
        files.append({
            'recording_date': row[0],
            'case_no': row[1],
            'camera_name': row[2],
            'seq_size_mb': row[3]
        })

    conn.close()
    return files

def build_seq_path(recording_date, case_no, camera_name):
    """Build the path to the .seq file."""
    yy = recording_date[2:4]
    mm = recording_date[5:7]
    dd = recording_date[8:10]
    data_folder = f"DATA_{yy}-{mm}-{dd}"
    case_folder = f"Case{case_no}"

    seq_path = Path(SEQ_ROOT) / data_folder / case_folder / camera_name

    # Find the first .seq file
    seq_files = list(seq_path.glob("*.seq"))
    if seq_files:
        return seq_files[0]
    return None

def find_clexport():
    """Find CLExport.exe in common locations."""
    CLEXPORT_PATHS = [
        r"C:\\Program Files\\NorPix\\BatchProcessor\\CLExport.exe",
        r"C:\\Program Files (x86)\\NorPix\\BatchProcessor\\CLExport.exe",
        r"C:\\NorPix\\BatchProcessor\\CLExport.exe",
    ]
    for path in CLEXPORT_PATHS:
        if os.path.exists(path):
            return path
    return None

def monitor_file_growth(out_path, process, timeout=15, check_interval=5):
    """
    Monitor if output file is growing. Kill process if stuck at any size for too long.

    Args:
        out_path: Path to output file
        process: Subprocess to monitor
        timeout: How long (seconds) to wait before killing if file size doesn't change
        check_interval: How often (seconds) to check file size

    Returns:
        True if file completed successfully, False if stuck
    """
    elapsed = 0
    last_size = 0
    stuck_time = 0
    file_created = False

    while process.poll() is None:  # While process is running
        time.sleep(check_interval)
        elapsed += check_interval

        # Check if file exists and get size
        if out_path.exists():
            current_size = out_path.stat().st_size
            size_mb = current_size / (1024*1024)

            if not file_created:
                print(f"    [Monitor] ✓ File created: {size_mb:.2f} MB")
                file_created = True
                last_size = current_size
                stuck_time = 0
            elif current_size == last_size:
                # File size hasn't changed
                stuck_time += check_interval
                print(f"    [Monitor] File size unchanged: {size_mb:.2f} MB (stuck for {stuck_time}s)")

                # If stuck for too long, kill the process
                if stuck_time >= timeout:
                    print(f"    [Monitor] ❌ File stuck at {size_mb:.2f} MB for {timeout}s - killing process")
                    process.kill()
                    return False
            else:
                # File is growing
                growth = (current_size - last_size) / (1024*1024)
                print(f"    [Monitor] Progress: {size_mb:.2f} MB (+{growth:.2f} MB)")
                last_size = current_size
                stuck_time = 0  # Reset stuck timer
        else:
            # File doesn't exist yet
            print(f"    [Monitor] Waiting for output file... ({elapsed}s)")
            if elapsed >= timeout:
                print(f"    [Monitor] ❌ Output file not created after {timeout}s - killing process")
                process.kill()
                return False

    # Process finished, check final result
    if out_path.exists() and out_path.stat().st_size > 0:
        final_size = out_path.stat().st_size / (1024*1024)
        print(f"    [Monitor] ✓ Conversion complete - Final size: {final_size:.2f} MB")
        return True
    return False

def export_file(seq_path, out_path, use_ffmpeg=True, codec="mp4"):
    """
    Export a single SEQ file to MP4.

    Args:
        seq_path: Path to .seq file
        out_path: Path to output .mp4 file
        use_ffmpeg: True for FFmpeg (GPU), False for CLExport
        codec: Codec for CLExport (mp4 or mjpeg)

    Returns:
        (success, message)
    """
    try:
        if use_ffmpeg:
            # GPU encoding using NVIDIA NVENC - optimized for smaller file size
            ffmpeg_path = find_ffmpeg()
            if not ffmpeg_path:
                return False, "ffmpeg.exe not found"

            cmd = [
                ffmpeg_path,
                "-y",
                "-hwaccel", "cuda",
                # --------------Set FOR FAST  VIDEO. the defult is 30 so find the right ratio by the correc tv
                "-r", "28.429918135379943",
                "-i", str(seq_path),
                "-c:v", "h264_nvenc",
                "-preset", "p6",  # Higher preset for better compression (p1=fastest, p7=slowest/best compression)
                "-rc", "vbr",  # Variable bitrate mode
                "-cq", "28",  # Quality level (higher = lower quality = smaller size)
                "-b:v", "2M",  # Target bitrate
                "-maxrate", "3M",  # Max bitrate
                "-bufsize", "3M",  # Buffer size
                "-profile:v", "high",
                "-pix_fmt", "yuv420p",
                str(out_path)
            ]

            # Run FFmpeg with real-time output
            print(f"  Running: {' '.join(cmd[:3])} ... (GPU-accelerated)")
            print(f"  --- Output from FFmpeg ---")

            # Use Popen for real-time output
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                universal_newlines=True,
                bufsize=1  # Line-buffered
            )

            # Print output in real-time
            for line in iter(process.stdout.readline, ''):
                if line:
                    print(f"  {line.rstrip()}")

            # Wait for process to complete
            process.wait()
            print(f"  --- End Output ---")

            # Check if successful
            if process.returncode == 0 and is_valid_video_file(out_path):
                size_mb = out_path.stat().st_size / (1024 * 1024)
                return True, f"Success - GPU ({size_mb:.1f} MB)"
            else:
                return False, f"GPU encoding failed with code {process.returncode}"

        else:
            # CLExport with file size monitoring
            clexport_path = find_clexport()
            if not clexport_path:
                return False, "CLExport.exe not found"

            out_dir = out_path.parent
            out_filename = out_path.stem

            cmd = [
                clexport_path,
                "-i", str(seq_path),
                "-o", str(out_dir),
                "-of", out_filename,
                "-f", codec
            ]

            # Run CLExport with monitoring
            print(f"  Running: {' '.join(cmd[:3])} ... {cmd[-1]}")
            print(f"  --- Output from CLExport ---")

            # Delete output file if it exists (to ensure clean start)
            if out_path.exists():
                out_path.unlink()

            # Start process
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                universal_newlines=True
            )

            # Start monitoring thread
            monitor_result = [None]  # Use list to share result between threads

            def monitor_thread():
                monitor_result[0] = monitor_file_growth(out_path, process, timeout=15, check_interval=5)

            monitor = threading.Thread(target=monitor_thread, daemon=True)
            monitor.start()

            # Wait for process and collect output
            stdout, _ = process.communicate()

            # Wait for monitor thread to finish
            monitor.join(timeout=2)

            # Print the output
            if stdout:
                for line in stdout.splitlines():
                    print(f"  {line}")
            print(f"  --- End Output ---")

            # Check results
            file_was_growing = monitor_result[0]

            if not file_was_growing:
                # File stuck at 0 bytes - monitoring already killed the process
                if out_path.exists():
                    out_path.unlink()  # Clean up the 0-byte file
                return False, "File stuck at 0 bytes - CLExport killed"

            # Check if successful
            if process.returncode == 0 and is_valid_video_file(out_path):
                size_mb = out_path.stat().st_size / (1024 * 1024)
                return True, f"Success ({size_mb:.1f} MB)"
            else:
                return False, f"Failed with code {process.returncode}"

    except Exception as e:
        return False, f"Error: {str(e)}"

def main():
    """Main batch convert function."""
    print("=" * 80)
    print("BATCH CONVERT SCRIPT - FFmpeg GPU with CLExport Fallback")
    print("=" * 80)
    print(f"Database: {DB_PATH}")
    print(f"SEQ Root: {SEQ_ROOT}")
    print(f"Output Root: {OUT_ROOT}")
    print()

    # Check FFmpeg availability
    ffmpeg_path = find_ffmpeg()
    if not ffmpeg_path:
        print("❌ FFmpeg not found!")
        print("Please install FFmpeg or add it to your PATH.")
        return

    # Check CLExport availability
    clexport_path = find_clexport()
    use_clexport_fallback = clexport_path is not None

    print(f"✓ Primary: FFmpeg with GPU acceleration: {ffmpeg_path}")
    if use_clexport_fallback:
        print(f"✓ Fallback: CLExport available: {clexport_path}")
    else:
        print("⚠️  CLExport not found - no fallback available")
    print()

    # Always use all cameras
    cameras = DEFAULT_CAMERAS
    print(f"Cameras: All ({len(cameras)} cameras)")
    print()

    print("Fetching files from database...")
    files = get_missing_mp4_files(DB_PATH, cameras, limit=None)

    if not files:
        print("✓ No files need converting!")
        return

    print(f"\nFound {len(files)} files with missing MP4s")
    print("=" * 80)

    # Display files and let user choose
    print("\nAVAILABLE FILES:")
    print("-" * 80)
    for i, file_info in enumerate(files, 1):
        recording_date = file_info['recording_date']
        case_no = file_info['case_no']
        camera_name = file_info['camera_name']
        size_mb = file_info['seq_size_mb']
        print(f"  {i:3d}. {recording_date} | Case{case_no:2d} | {camera_name:20s} | {size_mb:6.1f} MB")

    print("-" * 80)
    print("\nHow do you want to select files?")
    print("  1. Convert ALL files")
    print("  2. Convert first N files")
    print("  3. Choose specific files by number")

    selection_mode = input("\nChoice (1, 2, or 3): ").strip()

    selected_files = []

    if selection_mode == '1':
        # Export all
        selected_files = files
        print(f"✓ Selected all {len(files)} files")

    elif selection_mode == '2':
        # Export first N
        n_input = input("How many files (from the top)? ").strip()
        try:
            n = int(n_input)
            selected_files = files[:n]
            print(f"✓ Selected first {len(selected_files)} files")
        except ValueError:
            print("❌ Invalid number")
            return

    elif selection_mode == '3':
        # Choose specific files
        print("\nEnter file numbers (comma-separated or ranges)")
        print("  Examples:")
        print("    1,3,5         - Files 1, 3, and 5")
        print("    1-10          - Files 1 through 10")
        print("    1-5,8,10-15   - Files 1-5, 8, and 10-15")

        numbers_input = input("\nFile numbers: ").strip()

        try:
            # Parse input
            selected_indices = set()
            parts = numbers_input.split(',')

            for part in parts:
                part = part.strip()
                if '-' in part:
                    # Range
                    start, end = part.split('-')
                    start_idx = int(start.strip())
                    end_idx = int(end.strip())
                    selected_indices.update(range(start_idx, end_idx + 1))
                else:
                    # Single number
                    selected_indices.add(int(part))

            # Convert to file list
            for idx in sorted(selected_indices):
                if 1 <= idx <= len(files):
                    selected_files.append(files[idx - 1])

            print(f"✓ Selected {len(selected_files)} files")

        except Exception as e:
            print(f"❌ Invalid input: {e}")
            return
    else:
        print("❌ Invalid choice")
        return

    if not selected_files:
        print("No files selected!")
        return

    print()

    # Confirm
    response = input(f"Convert {len(selected_files)} files using FFmpeg GPU? (y/n): ").strip().lower()
    if response != 'y':
        print("Cancelled.")
        return

    print()
    print("=" * 80)
    print("STARTING CONVERSION")
    print("=" * 80)

    # Export files
    success_count = 0
    failed_count = 0
    skipped_count = 0
    fallback_count = 0

    start_time = datetime.now()
    codec = "mp4"  # Codec for CLExport

    for i, file_info in enumerate(selected_files, 1):
        recording_date = file_info['recording_date']
        case_no = file_info['case_no']
        camera_name = file_info['camera_name']

        print(f"\n[{i}/{len(selected_files)}] {recording_date} Case{case_no} - {camera_name}")
        print(f"  SEQ Size: {file_info['seq_size_mb']:.1f} MB")

        # Build paths
        seq_path = build_seq_path(recording_date, case_no, camera_name)

        if not seq_path or not seq_path.exists():
            print(f"  ❌ SKIP: SEQ file not found")
            skipped_count += 1
            continue

        # Compute output path
        out_root_path = Path(OUT_ROOT).resolve()
        out_dir = compute_out_dir(seq_path, out_root_path)
        ch_label = resolve_channel_label(seq_path, {})
        exported_name, mp4_path = get_next_available_filename(out_dir, ch_label, ".mp4")

        # Check if already exists
        if is_valid_video_file(mp4_path):
            print(f"  ⏭️  SKIP: MP4 already exists ({mp4_path.name})")
            skipped_count += 1
            continue

        # Export with GPU-accelerated FFmpeg
        success, message = export_file(seq_path, mp4_path, use_ffmpeg=True, codec=codec)

        if success:
            print(f"  ✅ {message}")
            success_count += 1
        else:
            # Try fallback to CLExport if GPU failed and CLExport is available
            if use_clexport_fallback:
                print(f"  ⚠️  GPU encoding failed: {message}")
                print(f"  🔄 Trying CLExport fallback...")

                # Try with CLExport
                success_fallback, message_fallback = export_file(seq_path, mp4_path, use_ffmpeg=False, codec=codec)

                if success_fallback:
                    print(f"  ✅ CLExport fallback succeeded: {message_fallback}")
                    success_count += 1
                    fallback_count += 1
                else:
                    print(f"  ❌ CLExport fallback also failed: {message_fallback}")
                    failed_count += 1
            else:
                print(f"  ❌ FAILED: {message}")
                failed_count += 1

    # Summary
    end_time = datetime.now()
    duration = end_time - start_time

    print()
    print("=" * 80)
    print("CONVERSION COMPLETE")
    print("=" * 80)
    print(f"Success:       {success_count}")
    if fallback_count > 0:
        print(f"  (CLExport):  {fallback_count}")
    print(f"Failed:        {failed_count}")
    print(f"Skipped:       {skipped_count}")
    print(f"Total:         {len(selected_files)}")
    print(f"Duration:      {duration}")
    print(f"Primary:       FFmpeg GPU (NVENC)")
    if use_clexport_fallback:
        print(f"Fallback:      CLExport (mp4/H.264)")
    print("=" * 80)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n⚠️  Conversion interrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
