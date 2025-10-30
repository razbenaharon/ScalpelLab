import os
import sys
import sqlite3
import subprocess
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import argparse

# ============================================
# Cameras (columns expected in your seq_status)
# ============================================
CAMERAS = [
    "Cart_Center_2", "Cart_LT_4", "Cart_RT_1",
    "General_3", "Monitor", "Patient_Monitor",
    "Ventilator_Monitor", "Injection_Port"
]

# ============================================
# FFmpeg possible locations (Windows)
# ============================================
FFMPEG_PATHS = [
    r"C:\Program Files\ffmpeg\bin\ffmpeg.exe",
    r"C:\ffmpeg\bin\ffmpeg.exe",
    r"C:\Program Files (x86)\ffmpeg\bin\ffmpeg.exe",
    "ffmpeg",  # Try from PATH
]

# ============================================
# FFmpeg encoding parameters
# ============================================
FFMPEG_FRAMERATE = 30  # Frame rate
FFMPEG_CODEC = "libx264"  # Video codec
FFMPEG_PRESET = "medium"  # Encoding speed
FFMPEG_CRF = 23  # Quality (18-28, lower = better quality)
FFMPEG_PIX_FMT = "yuv420p"  # Pixel format for compatibility
MIN_VALID_FILE_SIZE_MB = 1.0  # Minimum size for valid MP4 file


# =========================
# FFmpeg helpers
# =========================
def find_ffmpeg() -> Optional[str]:
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


def export_seq_with_ffmpeg(seq_path: Path, out_path: Path, debug: bool = False, show_progress: bool = True) -> Tuple[int, str]:
    """
    Export SEQ file to MP4 using FFmpeg.
    Returns (exitcode, message). 0 = success.
    """
    ffmpeg_path = find_ffmpeg()
    if not ffmpeg_path:
        return 1, "ffmpeg.exe not found"

    # Build FFmpeg command
    cmd = [
        ffmpeg_path,
        "-y",  # Overwrite output file without asking
        "-r", str(FFMPEG_FRAMERATE),
        "-i", str(seq_path),
        "-c:v", FFMPEG_CODEC,
        "-preset", FFMPEG_PRESET,
        "-crf", str(FFMPEG_CRF),
        "-pix_fmt", FFMPEG_PIX_FMT,
        str(out_path)
    ]

    if debug:
        print(f"[DEBUG] Running: {' '.join(cmd)}")

    try:
        if show_progress:
            # Show FFmpeg output in real-time so user knows it's working
            print(f"  -> Converting with FFmpeg...")
            result = subprocess.run(cmd, text=True)
        else:
            # Capture output silently
            result = subprocess.run(cmd, capture_output=True, text=True)

        if result.returncode == 0 and is_valid_video_file(out_path):
            return 0, "Export successful"
        else:
            error_msg = result.stderr[:200] if hasattr(result, 'stderr') and result.stderr else "Unknown error"
            return result.returncode or 1, f"FFmpeg failed: {error_msg}"
    except Exception as e:
        return 1, f"Error running FFmpeg: {str(e)}"


def is_valid_video_file(file_path: Path, min_size_mb: float = MIN_VALID_FILE_SIZE_MB) -> bool:
    """Check if video file exists and has reasonable size."""
    if not file_path.exists():
        return False
    try:
        size_mb = file_path.stat().st_size / (1024 * 1024)
        return size_mb > min_size_mb
    except:
        return False


def find_existing_export(out_dir: Path, base_stem: str, extensions: List[str] = ['.mp4']) -> Optional[Path]:
    """Find any existing export of this file (with or without counter suffix)."""
    for ext in extensions:
        # Check base name
        base_path = out_dir / f"{base_stem}{ext}"
        if is_valid_video_file(base_path):
            return base_path

        # Check numbered variants
        for i in range(1, 100):  # Check up to _99
            numbered_path = out_dir / f"{base_stem}_{i}{ext}"
            if is_valid_video_file(numbered_path):
                return numbered_path

    return None


def clean_invalid_exports(out_dir: Path, base_stem: str, debug: bool = False) -> int:
    """Remove invalid/incomplete export files. Returns count of removed files."""
    removed = 0
    patterns = [f"{base_stem}.mp4", f"{base_stem}_*.mp4"]

    for pattern in patterns:
        for file in out_dir.glob(pattern):
            if not is_valid_video_file(file):
                try:
                    file.unlink()
                    removed += 1
                    if debug:
                        print(f"[CLEAN] Removed invalid file: {file.name} (size: {file.stat().st_size / 1024:.1f}KB)")
                except Exception as e:
                    if debug:
                        print(f"[CLEAN] Could not remove {file}: {e}")

    return removed


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


# =========================
# Path utilities
# =========================
def expand_seq_paths(paths: List[str], debug: bool = False) -> List[Path]:
    """
    Each input may be:
      - a directory that contains one or more .seq files (we'll pick the first),
      - a direct .seq file.
    """
    expanded: List[Path] = []
    for p in paths:
        Pobj = Path(p)
        if Pobj.is_dir():
            seqs = sorted(Pobj.glob("*.seq"))
            if not seqs:
                if debug:
                    print(f"[WARN] No .seq file found in directory: {Pobj}")
                continue
            if len(seqs) > 1 and debug:
                print(f"[WARN] Multiple .seq files in {Pobj}, taking first: {seqs[0].name}")
            expanded.append(seqs[0])
        else:
            if Pobj.suffix.lower() == ".seq":
                expanded.append(Pobj)
            else:
                if debug:
                    print(f"[WARN] Skipping non-seq path: {Pobj}")
    return expanded


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


def dedupe_preserve_order(items: List[str]) -> List[str]:
    seen = set()
    out: List[str] = []
    for x in items:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


# =========================
# Output directory computation
# =========================
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


def query_channel_dirs_from_db(db_path: str, table: str, cameras: List[str], debug: bool = False) -> List[str]:
    """
    Reads rows from table and returns list of relative channel dirs like 'DATA_22-12-04\\Case1\\General_3'
    """
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    query = f"SELECT recording_date, case_no, camera_name FROM {table}"
    rows = cur.execute(query).fetchall()
    conn.close()

    all_rel_dirs: List[str] = []
    for row in rows:
        recording_date, case_no, camera_name = row
        if not isinstance(recording_date, str) or not isinstance(case_no, int):
            continue

        # Filter by cameras list if provided
        if cameras and camera_name not in cameras:
            continue

        # recording_date: 'YYYY-MM-DD' -> 'DATA_YY-MM-DD'
        yy = recording_date[2:4]
        mm = recording_date[5:7]
        dd = recording_date[8:10]
        data_folder = f"DATA_{yy}-{mm}-{dd}"
        case_folder = f"Case{case_no}"

        all_rel_dirs.append(f"{data_folder}\\{case_folder}\\{camera_name}")

    return list(set(all_rel_dirs))  # Remove duplicates


# =========================
# Single file export
# =========================
def export_single_seq_file(seq_file_path: str, out_root: str, debug: bool = False, skip_existing: bool = True) -> Tuple[bool, str]:
    """
    Export a single SEQ file to MP4 format using FFmpeg.
    """
    try:
        seq_path = Path(seq_file_path).resolve()

        if not seq_path.exists():
            return False, f"File not found: {seq_path}"

        if seq_path.is_dir():
            # Find first .seq in directory
            seqs = list(seq_path.glob("*.seq"))
            if not seqs:
                return False, f"No .seq file found in directory: {seq_path}"
            seq_path = seqs[0]

        if seq_path.suffix.lower() != ".seq":
            return False, f"Not a .seq file: {seq_path}"

        out_root_path = Path(out_root).resolve()
        out_root_path.mkdir(parents=True, exist_ok=True)

        # Compute output directory
        out_dir = compute_out_dir(seq_path, out_root_path)

        # Get output filename (use parent folder name or stem)
        base_name = seq_path.parent.name if seq_path.parent else seq_path.stem
        out_path = out_dir / f"{base_name}.mp4"

        # Check if already exists
        if skip_existing and is_valid_video_file(out_path):
            return True, f"Already exists: {out_path}"

        # Show file info
        file_size_mb = seq_path.stat().st_size / (1024 * 1024)
        print(f"  File size: {file_size_mb:.1f} MB")
        print(f"  Output: {out_path}")

        # Export
        exitcode, message = export_seq_with_ffmpeg(seq_path, out_path, debug, show_progress=True)

        if exitcode == 0:
            return True, f"Success: {out_path}"
        else:
            # Clean up failed file
            if out_path.exists():
                out_path.unlink()
            return False, f"Failed: {message}"

    except Exception as e:
        return False, f"Error: {str(e)}"


# =========================
# Batch export pipeline
# =========================
def run_pipeline(db_path: str, table: str, seq_root: str, out_root: str,
                 debug: bool = False, skip_existing: bool = True,
                 specific_files: Optional[List[str]] = None) -> None:
    """
    Export SEQ files to MP4 format using FFmpeg.
    """
    print("\n" + "="*60)
    print("FFmpeg SEQ Exporter - Starting...")
    print("="*60)

    # Check FFmpeg availability
    ffmpeg_path = find_ffmpeg()
    if ffmpeg_path:
        print(f"[INFO] FFmpeg found: {ffmpeg_path}")
    else:
        print("[ERROR] FFmpeg not found! Please install FFmpeg.")
        return

    seq_root_path = Path(seq_root).resolve()
    out_root_path = Path(out_root).resolve()
    out_root_path.mkdir(parents=True, exist_ok=True)

    print(f"[INFO] Input directory: {seq_root_path}")
    print(f"[INFO] Output directory: {out_root_path}")

    # Statistics
    stats = {'total': 0, 'skipped': 0, 'success': 0, 'failed': 0}

    # Determine which files to process
    seq_files = []

    if specific_files:
        # Process specific files
        print(f"[INFO] Processing {len(specific_files)} specific file(s)...")

        for file_path in specific_files:
            path_obj = Path(file_path)
            if not path_obj.is_absolute():
                path_obj = seq_root_path / path_obj
            path_obj = path_obj.resolve()

            if path_obj.exists():
                if path_obj.is_dir():
                    seqs = list(path_obj.glob("*.seq"))
                    if seqs:
                        seq_files.append(seqs[0])
                elif path_obj.suffix.lower() == ".seq":
                    seq_files.append(path_obj)
    else:
        # Query database
        print(f"[INFO] Querying database: {db_path}")
        print(f"[INFO] Looking for recordings in table: {table}")

        rel_dirs = query_channel_dirs_from_db(db_path, table, CAMERAS, debug)

        print(f"[INFO] Found {len(rel_dirs)} channel directories from database")
        print(f"[INFO] Searching for .seq files in each directory...")

        # Find .seq files in each directory
        for rel_dir in rel_dirs:
            channel_dir = seq_root_path / rel_dir
            if channel_dir.exists():
                seqs = list(channel_dir.glob("*.seq"))
                if seqs:
                    seq_files.append(seqs[0])  # Take first .seq file

    stats['total'] = len(seq_files)
    print(f"\n[INFO] Found {stats['total']} files to process\n")

    # Export each file
    for idx, seq_path in enumerate(seq_files, 1):
        try:
            print(f"\n{'='*60}")
            print(f"[{idx}/{stats['total']}] Processing: {seq_path.name}")
            print(f"{'='*60}")

            # Compute output directory
            out_dir = compute_out_dir(seq_path, out_root_path)

            # Output filename
            base_name = seq_path.parent.name if seq_path.parent else seq_path.stem
            out_path = out_dir / f"{base_name}.mp4"

            # Show file info
            file_size_mb = seq_path.stat().st_size / (1024 * 1024)
            print(f"  Source: {seq_path}")
            print(f"  Size: {file_size_mb:.1f} MB")
            print(f"  Output: {out_path}")

            # Check if already exists
            if skip_existing and is_valid_video_file(out_path):
                print(f"\n  -> SKIPPED (already exists)")
                stats['skipped'] += 1
                continue

            # Export
            print("")  # Blank line before FFmpeg output
            exitcode, message = export_seq_with_ffmpeg(seq_path, out_path, debug, show_progress=True)

            if exitcode == 0:
                out_size_mb = out_path.stat().st_size / (1024 * 1024)
                print(f"\n  -> SUCCESS! Created {out_path.name} ({out_size_mb:.1f} MB)")
                stats['success'] += 1
            else:
                print(f"\n  -> FAILED: {message}")
                stats['failed'] += 1
                # Clean up failed file
                if out_path.exists():
                    out_path.unlink()

        except Exception as e:
            print(f"\n  -> ERROR: {e}")
            stats['failed'] += 1

    # Print summary
    print("=" * 60)
    print("EXPORT SUMMARY")
    print("=" * 60)
    print(f"Total files:       {stats['total']}")
    print(f"Skipped (exists):  {stats['skipped']}")
    print(f"Success:           {stats['success']}")
    print(f"Failed:            {stats['failed']}")
    print("=" * 60)


# =========================
# Main entry point
# =========================
if __name__ == "__main__":
    # Hard-coded defaults
    DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "ScalpelDatabase.sqlite")
    SEQ_ROOT = r"F:\Room_8_Data\Sequence_Backup"
    OUT_ROOT = r"F:\Room_8_Data\Recordings"
    TABLE = "seq_status"

    # Parse command-line arguments
    parser = argparse.ArgumentParser(
        description="Export SEQ files to MP4 format using FFmpeg",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Export all files from database:
  python ffmpeg_exporter.py

  # Export a specific file:
  python ffmpeg_exporter.py --single "F:\\path\\to\\file.seq"

  # Export multiple specific files:
  python ffmpeg_exporter.py --file "file1.seq" --file "file2.seq"

  # Enable debug output:
  python ffmpeg_exporter.py --debug
        """
    )

    parser.add_argument(
        '--file', '-f',
        action='append',
        dest='specific_files',
        metavar='PATH',
        help='Export a specific .seq file (can be used multiple times)'
    )

    parser.add_argument(
        '--single', '-s',
        metavar='PATH',
        help='Quick single file export'
    )

    parser.add_argument(
        '--debug', '-d',
        action='store_true',
        help='Enable debug output'
    )

    args = parser.parse_args()

    # Handle single file quick export
    if args.single:
        print(f"[INFO] Exporting: {args.single}\n")
        success, message = export_single_seq_file(
            seq_file_path=args.single,
            out_root=OUT_ROOT,
            debug=args.debug,
            skip_existing=True
        )
        if success:
            print(f"[SUCCESS] {message}")
            sys.exit(0)
        else:
            print(f"[FAILED] {message}")
            sys.exit(1)

    # Handle pipeline mode
    run_pipeline(
        db_path=DB_PATH,
        table=TABLE,
        seq_root=SEQ_ROOT,
        out_root=OUT_ROOT,
        debug=args.debug,
        skip_existing=True,
        specific_files=args.specific_files
    )
