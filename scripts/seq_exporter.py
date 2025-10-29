import os
import sys
import json
import sqlite3
import time
import signal
import subprocess
import select
from subprocess import Popen, CREATE_NEW_CONSOLE, PIPE, STDOUT
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Iterable, Set
import argparse

# ============================================
# Load damaged paths from file
# ============================================
def load_damaged_paths() -> Set[str]:
    """Load damaged SEQ paths from damaged_seq_path.py"""
    try:
        damaged_file = Path(__file__).parent / "damaged_seq_path.py"
        if not damaged_file.exists():
            return set()

        # Import the damaged_seq_path variable
        with open(damaged_file, 'r') as f:
            content = f.read()

        # Extract paths from the multiline string
        import re
        match = re.search(r'damaged_seq_path\s*=\s*"""(.+?)"""', content, re.DOTALL)
        if not match:
            return set()

        paths_text = match.group(1)
        # Parse paths and normalize them
        damaged_paths = set()
        for line in paths_text.strip().split('\n'):
            line = line.strip()
            if line and line.startswith('F:'):
                # Normalize path (remove trailing slashes, convert to Path for consistency)
                normalized = str(Path(line).resolve())
                damaged_paths.add(normalized)

        return damaged_paths
    except Exception as e:
        print(f"[WARN] Could not load damaged paths: {e}")
        return set()

# ============================================
# Aggressive process killing for Windows
# ============================================
def force_kill_process(proc: Popen, process_name: str = "CLExport.exe") -> bool:
    """
    Aggressively kill a process using multiple methods.
    Returns True if process was successfully killed.
    """
    if proc.poll() is not None:
        return True  # Already dead

    try:
        # Method 1: Standard terminate
        proc.terminate()
        try:
            proc.wait(timeout=2)
            return True
        except subprocess.TimeoutExpired:
            pass
    except Exception:
        pass

    try:
        # Method 2: Kill signal
        proc.kill()
        try:
            proc.wait(timeout=2)
            return True
        except subprocess.TimeoutExpired:
            pass
    except Exception:
        pass

    # Method 3: Windows taskkill command (most aggressive)
    try:
        if os.name == 'nt':  # Windows
            subprocess.run(['taskkill', '/F', '/IM', process_name],
                         capture_output=True, timeout=5)
            # Also kill by PID if we have it
            if hasattr(proc, 'pid') and proc.pid:
                subprocess.run(['taskkill', '/F', '/PID', str(proc.pid)],
                             capture_output=True, timeout=5)
        else:  # Unix-like
            if hasattr(proc, 'pid') and proc.pid:
                os.kill(proc.pid, signal.SIGKILL)

        # Check if it's really dead
        try:
            proc.wait(timeout=1)
            return True
        except subprocess.TimeoutExpired:
            return False
    except Exception:
        return False

# ============================================
# Cameras (columns expected in your seq_status)
# ============================================
CAMERAS = [
    "Cart_Center_2", "Cart_LT_4", "Cart_RT_1",
    "General_3", "Monitor", "Patient_Monitor",
    "Ventilator_Monitor", "Injection_Port"
]

# ============================================
# NorPix CLExport possible locations (Windows)
# ============================================
CLEXPORT_PATHS = [
    r"C:\Program Files\NorPix\BatchProcessor\CLExport.exe",
    r"C:\Program Files (x86)\NorPix\BatchProcessor\CLExport.exe",
    r"C:\NorPix\BatchProcessor\CLExport.exe",
]

# ============================================
# Tuning knobs (adjusted for better success rate)
# ============================================
MAX_RETRIES_MP4 = 2  # attempts for MP4
MAX_RETRIES_AVI = 1  # attempts for AVI fallback
BASE_TIMEOUT_SECS = 60  # base timeout for small files (increased from 30)
TIMEOUT_PER_GB = 120  # additional seconds per GB of file size (2 min per GB - increased from 60)
MIN_TIMEOUT_SECS = 30  # minimum timeout regardless of file size (increased from 15)
MAX_TIMEOUT_SECS = 900  # maximum timeout (15 minutes) for huge files (increased from 600)
SILENT_TIMEOUT_SECS = 300  # kill if no output/progress for this long (5 minutes - CLExport doesn't output while encoding)
KILL_AFTER_ERROR_LINES = 6  # slightly more tolerant
SUPPRESS_CLEXPORT_OUTPUT = True  # keep console clean
MIN_VALID_FILE_SIZE_MB = 1.0  # Minimum size for valid MP4/AVI file

# Broader phrase so we catch both MP4/AVI variants
ERROR_LINE_SIGNATURE = "Error writing video"


# =========================
# Dynamic timeout calculation
# =========================
def calculate_timeout(file_path: Path) -> int:
    """
    Calculate timeout based on file size.
    Small files: 15-30 seconds
    Large files: up to 5 minutes
    """
    try:
        file_size_bytes = file_path.stat().st_size
        file_size_gb = file_size_bytes / (1024 * 1024 * 1024)

        # Calculate timeout: base + additional time per GB
        timeout = BASE_TIMEOUT_SECS + (file_size_gb * TIMEOUT_PER_GB)

        # Apply min/max limits
        timeout = max(MIN_TIMEOUT_SECS, min(MAX_TIMEOUT_SECS, timeout))

        return int(timeout)
    except Exception:
        # If we can't get file size, use base timeout
        return BASE_TIMEOUT_SECS

# =========================
# CLExport helpers
# =========================
def find_clexport() -> Optional[str]:
    for path in CLEXPORT_PATHS:
        if os.path.exists(path):
            return path
    return None


def _build_cmd(clexport_path: str, seq_path: Path, out_dir: Path, exported_name: str, container: str) -> List[str]:
    """
    Build CLExport command. DO NOT pass '-cmp' (it causes 'No value found for parameter -cmp' on some installs).
    """
    return [
        clexport_path,
        "-i", str(seq_path),
        "-o", str(out_dir),
        "-of", exported_name,
        "-f", container,
    ]


def is_valid_video_file(file_path: Path, min_size_mb: float = MIN_VALID_FILE_SIZE_MB) -> bool:
    """Check if video file exists and has reasonable size."""
    if not file_path.exists():
        return False
    try:
        size_mb = file_path.stat().st_size / (1024 * 1024)
        return size_mb > min_size_mb
    except:
        return False


def find_existing_export(out_dir: Path, base_stem: str, extensions: List[str] = ['.mp4', '.avi']) -> Optional[Path]:
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
    patterns = [f"{base_stem}.mp4", f"{base_stem}_*.mp4", f"{base_stem}.avi", f"{base_stem}_*.avi"]

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
    exported_name = f"{base_stem}{extension}"
    file_path = out_dir / exported_name

    if not file_path.exists():
        return exported_name, file_path

    # Try with counter
    counter = 1
    while counter < 1000:  # Safety limit
        exported_name = f"{base_stem}_{counter}{extension}"
        file_path = out_dir / exported_name
        if not file_path.exists():
            return exported_name, file_path
        counter += 1

    raise ValueError(f"Could not find available filename for {base_stem} after 1000 attempts")


def export_seq_once_streaming(
        seq_path: Path,
        out_dir: Path,
        exported_name: str,
        container: str,
        simulate: bool,
        spawn_console: bool = False,
        timeout_secs: Optional[int] = None,
        kill_after_error_lines: Optional[int] = None,
        suppress_console_output: bool = True,
        debug: bool = False
) -> Tuple[int, str]:
    """
    Run a single CLExport attempt while *stream-reading* its stdout.
    - If we see >= kill_after_error_lines of the known error line, kill the process and fail fast.
    - Also hard-timeout after timeout_secs.
    Returns (exitcode, message). 0 = success.
    """
    if simulate:
        return 0, f"Simulated export: {seq_path} -> {out_dir / exported_name} ({container})"

    clexport_path = find_clexport()
    if not clexport_path:
        searched = "\n".join([f"  - {p}" for p in CLEXPORT_PATHS])
        return 1, f"CLExport.exe not found. Searched:\n{searched}"

    cmd = _build_cmd(clexport_path, seq_path, out_dir, exported_name, container)
    creationflags = CREATE_NEW_CONSOLE if spawn_console else 0

    if spawn_console:
        # Can't capture stdout; just enforce timeout loop.
        try:
            proc = Popen(cmd, universal_newlines=True, creationflags=creationflags)
            start = time.time()
            while True:
                ret = proc.poll()
                if ret is not None:
                    return (0, f"Exported successfully ({container})") if ret == 0 else (ret,
                                                                                         f"CLExport failed with exit code {ret} ({container})")
                if timeout_secs is not None and (time.time() - start) > timeout_secs:
                    killed = force_kill_process(proc, "CLExport.exe")
                    if killed:
                        return 1, f"CLExport timed out and killed after {timeout_secs}s ({container})"
                    else:
                        return 1, f"CLExport timed out after {timeout_secs}s - WARNING: Process may still be running ({container})"
                time.sleep(0.2)
        except Exception as e:
            return 1, f"Exception running CLExport: {str(e)} ({container})"

    # Not spawning a console: capture merged stdout/stderr to detect spam and enforce fast-kill.
    try:
        if debug:
            print(f"[DEBUG] Starting CLExport with command: {' '.join(cmd)}")
        proc = Popen(
            cmd,
            text=True,
            bufsize=1,  # line-buffered
            stdout=PIPE,
            stderr=STDOUT,
            creationflags=creationflags
        )
        if debug:
            print(f"[DEBUG] CLExport process started with PID: {proc.pid}")
    except Exception as e:
        return 1, f"Exception starting CLExport: {str(e)} ({container})"

    error_count = 0
    start_time = time.time()

    def mirror(line: str):
        if not SUPPRESS_CLEXPORT_OUTPUT and not suppress_console_output:
            print(line, end="")

    try:
        loop_count = 0
        last_output_time = start_time

        while True:
            loop_count += 1
            elapsed = time.time() - start_time
            silent_time = time.time() - last_output_time

            # Debug every 100 loops (about 5 seconds)
            if debug and loop_count % 100 == 0:
                print(f"[DEBUG] Loop {loop_count}, elapsed: {elapsed:.1f}s, process status: {proc.poll()}")

            # Early kill for silent processes (running but no output)
            if proc.poll() is None and silent_time > SILENT_TIMEOUT_SECS:
                if debug:
                    print(f"[DEBUG] Process silent for {silent_time:.1f}s, likely stuck - killing PID {proc.pid}")
                killed = force_kill_process(proc, "CLExport.exe")
                if killed:
                    return 1, f"CLExport killed after {silent_time:.0f}s of silence - likely corrupted file ({container})"
                else:
                    return 1, f"CLExport silent for {silent_time:.0f}s - WARNING: Process may still be running ({container})"

            # Normal timeout (longer for large files)
            if timeout_secs is not None and elapsed > timeout_secs:
                if debug:
                    print(f"[DEBUG] Full timeout reached after {elapsed:.1f}s, killing process PID {proc.pid}")
                killed = force_kill_process(proc, "CLExport.exe")
                if killed:
                    return 1, f"CLExport timed out and killed after {timeout_secs}s ({container})"
                else:
                    return 1, f"CLExport timed out after {timeout_secs}s - WARNING: Process may still be running ({container})"

            ret = proc.poll()
            if ret is not None:
                if debug:
                    print(f"[DEBUG] Process finished with exit code: {ret}")
                if proc.stdout:
                    for _ in proc.stdout:
                        pass
                return (0, f"Exported successfully ({container})") if ret == 0 else (ret,
                                                                                     f"CLExport failed with exit code {ret} ({container})")

            # Check if there's output available (non-blocking approach)
            line = ""
            if proc.stdout:
                try:
                    # Try to read with a very short timeout
                    import msvcrt
                    if os.name == 'nt':  # Windows
                        # For Windows, we'll just do a quick poll and timeout approach
                        if proc.poll() is None:  # Process still running
                            # Don't block on readline if process is running
                            pass
                        else:
                            # Process finished, safe to read remaining output
                            line = proc.stdout.readline()
                    else:
                        # Unix systems can use select
                        import select
                        ready, _, _ = select.select([proc.stdout], [], [], 0.05)
                        if ready:
                            line = proc.stdout.readline()
                except Exception as e:
                    if debug:
                        print(f"[DEBUG] Error reading stdout: {e}")
                    line = ""

            if not line:
                time.sleep(0.05)
                continue

            # Reset silent timer when we get actual output
            last_output_time = time.time()
            mirror(line)

            if ERROR_LINE_SIGNATURE in line:
                error_count += 1
                if debug:
                    print(f"[DEBUG] Error line detected, count: {error_count}")
                if kill_after_error_lines is not None and error_count >= kill_after_error_lines:
                    force_kill_process(proc, "CLExport.exe")
                    return 1, f"Killed after {error_count} repeated errors ({container})"

    except Exception as e:
        force_kill_process(proc, "CLExport.exe")
        return 1, f"Exception while streaming CLExport output: {str(e)} ({container})"


# =========================
# Path utilities
# =========================
def expand_seq_paths(paths: Iterable[str], debug: bool = False) -> List[Path]:
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


def dedupe_preserve_order(items: Iterable[str]) -> List[str]:
    seen = set()
    out: List[str] = []
    for x in items:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


# =========================
# Out dir computation (robust)
# =========================
def compute_out_dir(seq_path: Path, out_root_path: Path) -> Path:
    """
    Decide where to write the output.
    - If the input path includes a DATA_* anchor, mirror from there.
    - Otherwise, fall back to DATA_Unknown/CaseUnknown/<Channel>.
    Always mkdir(parents=True, exist_ok=True).
    """
    parts = seq_path.parts
    anchor_idx = None
    for i, part in enumerate(parts):
        if part.upper().startswith("DATA_"):
            anchor_idx = i
            break

    if anchor_idx is not None:
        rel_from_data = Path(*parts[anchor_idx:])  # e.g., DATA_22-12-04/Case1/General_3/file.seq
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
# DB -> channel dir list
# =========================
def query_channel_dirs_from_db(db_path: str,
                               table: str,
                               cameras: List[str],
                               only_value: int = 1,
                               debug: bool = False,
                               include_all: bool = False,
                               threshold_mb: int = 200) -> List[str]:
    """
    Reads rows from `table` (expects normalized structure: recording_date, case_no, camera_name, size_mb),
    returns a list of relative channel dirs like 'DATA_22-12-04\\Case1\\General_3'
    for all cameras where derived status == only_value, or all cameras if include_all=True.
    Status is derived from size_mb: 1=>=threshold_mb, 2=<threshold_mb, 3=NULL
    """
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    # Build query for normalized structure - derive status from size_mb
    if include_all:
        query = f"SELECT recording_date, case_no, camera_name, size_mb FROM {table}"
        rows = cur.execute(query).fetchall()
        # Filter by derived status
        filtered_rows = []
        for row in rows:
            if only_value == 3 and row[3] is None:
                filtered_rows.append(row[:3])
            elif only_value == 1 and row[3] is not None and row[3] >= threshold_mb:
                filtered_rows.append(row[:3])
            elif only_value == 2 and row[3] is not None and row[3] < threshold_mb:
                filtered_rows.append(row[:3])
            else:
                # include_all is True, so add all
                filtered_rows.append(row[:3])
        rows = filtered_rows
    else:
        # Filter by derived status in SQL
        if only_value == 3:
            query = f"SELECT recording_date, case_no, camera_name FROM {table} WHERE size_mb IS NULL"
        elif only_value == 1:
            query = f"SELECT recording_date, case_no, camera_name FROM {table} WHERE size_mb >= ?"
            rows = cur.execute(query, (threshold_mb,)).fetchall()
            conn.close()
            return dedupe_preserve_order(_process_rows_to_dirs(rows, cameras, debug))
        elif only_value == 2:
            query = f"SELECT recording_date, case_no, camera_name FROM {table} WHERE size_mb < ?"
            rows = cur.execute(query, (threshold_mb,)).fetchall()
            conn.close()
            return dedupe_preserve_order(_process_rows_to_dirs(rows, cameras, debug))
        else:
            query = f"SELECT recording_date, case_no, camera_name FROM {table}"
        rows = cur.execute(query).fetchall()
    conn.close()
    return dedupe_preserve_order(_process_rows_to_dirs(rows, cameras, debug))


def _process_rows_to_dirs(rows: List[tuple], cameras: List[str], debug: bool) -> List[str]:
    """Helper to convert database rows to relative directory paths."""
    all_rel_dirs: List[str] = []
    for row in rows:
        recording_date, case_no, camera_name = row
        if not isinstance(recording_date, str) or not isinstance(case_no, int):
            if debug:
                print(f"[WARN] Bad recording_date/case_no format: {recording_date}, {case_no}")
            continue

        # Filter by cameras list if provided
        if cameras and camera_name not in cameras:
            continue

        # recording_date: 'YYYY-MM-DD' -> 'DATA_YY-MM-DD'
        yy = recording_date[2:4];
        mm = recording_date[5:7];
        dd = recording_date[8:10]
        data_folder = f"DATA_{yy}-{mm}-{dd}"
        case_folder = f"Case{case_no}"

        # build relative dirs
        all_rel_dirs.append(f"{data_folder}\\{case_folder}\\{camera_name}")

    return all_rel_dirs


# =========================
# Convenience function for single file export
# =========================
def export_single_seq_file(seq_file_path: str,
                           out_root: str,
                           simulate: bool = False,
                           debug: bool = False,
                           skip_existing: bool = True,
                           clean_invalid: bool = True,
                           fallback_avi: bool = False) -> Tuple[bool, str]:
    r"""
    Export a single SEQ file to MP4/AVI format.

    Args:
        seq_file_path: Path to the .seq file to export
        out_root: Root directory for output files
        simulate: If True, don't actually export (dry run)
        debug: Enable debug output
        skip_existing: Skip if valid MP4/AVI already exists
        clean_invalid: Remove invalid/incomplete exports before starting
        fallback_avi: Try AVI if MP4 fails

    Returns:
        Tuple of (success: bool, message: str)

    Example:
        success, msg = export_single_seq_file(
            r"F:\Room_8_Data\Sequence_Backup\DATA_22-12-04\Case1\General_3\file.seq",
            r"F:\Room_8_Data\Recordings"
        )
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

        # Get base filename
        ch_label = resolve_channel_label(seq_path, {})
        base_stem = ch_label

        # Clean invalid files if requested
        if clean_invalid:
            clean_invalid_exports(out_dir, base_stem, debug)

        # Check if valid export already exists
        if skip_existing:
            existing = find_existing_export(out_dir, base_stem)
            if existing:
                return True, f"Valid export already exists: {existing}"

        # Calculate timeout
        dynamic_timeout = calculate_timeout(seq_path)

        # Try MP4 first
        exported_name, mp4_path = get_next_available_filename(out_dir, base_stem, ".mp4")

        if debug:
            file_size_mb = seq_path.stat().st_size / (1024 * 1024)
            print(f"[DEBUG] Exporting {seq_path.name} ({file_size_mb:.1f}MB, timeout: {dynamic_timeout}s)")

        for attempt in range(1, MAX_RETRIES_MP4 + 1):
            exitcode, reason = export_seq_once_streaming(
                seq_path=seq_path,
                out_dir=out_dir,
                exported_name=exported_name[:-4],  # Remove .mp4 extension
                container="mp4",
                simulate=simulate,
                spawn_console=False,
                timeout_secs=dynamic_timeout,
                kill_after_error_lines=KILL_AFTER_ERROR_LINES,
                suppress_console_output=SUPPRESS_CLEXPORT_OUTPUT,
                debug=debug
            )

            if exitcode == 0 and is_valid_video_file(mp4_path):
                return True, f"Export successful: {mp4_path}"

            # Remove invalid file
            if mp4_path.exists() and not is_valid_video_file(mp4_path):
                try:
                    mp4_path.unlink()
                except:
                    pass

        # Try AVI fallback if enabled
        if fallback_avi:
            exported_name, avi_path = get_next_available_filename(out_dir, base_stem, ".avi")

            for attempt in range(1, MAX_RETRIES_AVI + 1):
                exitcode, reason = export_seq_once_streaming(
                    seq_path=seq_path,
                    out_dir=out_dir,
                    exported_name=exported_name[:-4],
                    container="avi",
                    simulate=simulate,
                    spawn_console=False,
                    timeout_secs=dynamic_timeout * 2,
                    kill_after_error_lines=KILL_AFTER_ERROR_LINES * 2,
                    suppress_console_output=SUPPRESS_CLEXPORT_OUTPUT,
                    debug=debug
                )

                if exitcode == 0 and is_valid_video_file(avi_path):
                    return True, f"Export successful (AVI): {avi_path}"

                if avi_path.exists() and not is_valid_video_file(avi_path):
                    try:
                        avi_path.unlink()
                    except:
                        pass

        return False, f"Export failed: {reason}"

    except Exception as e:
        return False, f"Error during export: {str(e)}"


# =========================
# Full pipeline with improved handling
# =========================
def run_pipeline(db_path: str,
                 table: str,
                 seq_root: str,
                 out_root: str,
                 channel_names: Dict[str, str],
                 only_value: int,
                 simulate: bool,
                 debug: bool,
                 spawn_console: bool,
                 skip_existing: bool,
                 clean_invalid: bool,
                 fallback_avi: bool,
                 include_all: bool = False,
                 skip_damaged: bool = False,
                 specific_files: Optional[List[str]] = None) -> None:
    """
    Export SEQ files to MP4/AVI format.

    Args:
        specific_files: Optional list of specific .seq file paths to export.
                       If provided, skips database query and only processes these files.
                       Can be absolute paths or paths relative to seq_root.
    """
    seq_root_path = Path(seq_root).resolve()
    out_root_path = Path(out_root).resolve()
    out_root_path.mkdir(parents=True, exist_ok=True)

    # Load damaged paths if skip_damaged is enabled
    damaged_paths: Set[str] = set()
    if skip_damaged:
        damaged_paths = load_damaged_paths()
        if damaged_paths:
            print(f"[INFO] Loaded {len(damaged_paths)} damaged paths to skip")
        else:
            print("[WARN] skip_damaged=True but no damaged paths loaded")

    # Statistics
    stats = {
        'total': 0,
        'skipped_existing': 0,
        'skipped_damaged': 0,
        'success_mp4': 0,
        'success_avi': 0,
        'failed': 0,
        'cleaned': 0
    }

    # Determine which files to process
    if specific_files is not None:
        # Process specific files provided by user
        if debug:
            print(f"[DEBUG] Processing {len(specific_files)} specific file(s)")

        seq_files = []
        for file_path in specific_files:
            path_obj = Path(file_path)
            # If relative path, resolve against seq_root
            if not path_obj.is_absolute():
                path_obj = seq_root_path / path_obj

            path_obj = path_obj.resolve()

            if path_obj.exists():
                if path_obj.is_dir():
                    # If directory provided, find first .seq file
                    seqs = list(path_obj.glob("*.seq"))
                    if seqs:
                        seq_files.append(seqs[0])
                    elif debug:
                        print(f"[WARN] No .seq file in directory: {path_obj}")
                elif path_obj.suffix.lower() == ".seq":
                    seq_files.append(path_obj)
                else:
                    if debug:
                        print(f"[WARN] Not a .seq file: {path_obj}")
            else:
                if debug:
                    print(f"[WARN] File not found: {path_obj}")

        if debug:
            print(f"[DEBUG] Found {len(seq_files)} valid .seq file(s) to process")
    else:
        # Original behavior: query database for files to process
        # 1) Build relative channel dirs from DB rows
        rel_dirs = query_channel_dirs_from_db(
            db_path=db_path,
            table=table,
            cameras=CAMERAS,
            only_value=only_value,
            debug=debug,
            include_all=include_all
        )

        if debug:
            print(f"[DEBUG] Relative channel dirs from DB (count={len(rel_dirs)}). Example:")
            for p in rel_dirs[:5]:
                print("        ", p)

        # 2) Convert to absolute channel directories in SEQ tree
        channel_dirs = [str(seq_root_path / rd) for rd in rel_dirs]

        # 3) Expand channel dirs into actual .seq files
        seq_files = expand_seq_paths(channel_dirs, debug=debug)

        if debug:
            print(f"[DEBUG] Discovered .seq files to process: {len(seq_files)}")

    # 4) Export loop with improved handling
    log_path = out_root_path / "export_log.txt"
    total = len(seq_files)
    stats['total'] = total

    with log_path.open('a', encoding='utf-8') as log_file:
        log_file.write(f"\n{'=' * 60}\n")
        log_file.write(f"Export session started: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
        log_file.write(f"{'=' * 60}\n")

        for idx, seq_path in enumerate(seq_files, 1):
            try:
                seq_path = seq_path.resolve()
                if debug:
                    print(f"\n[{idx}/{total}] START {seq_path}")

                # Check if this path should be skipped (damaged)
                if skip_damaged:
                    seq_parent = str(seq_path.parent.resolve())
                    if seq_parent in damaged_paths:
                        status = "SKIPPED_DAMAGED"
                        reason = f"Path is in damaged list"
                        stats['skipped_damaged'] += 1

                        if debug:
                            print(f"[{idx}/{total}] {status}: {reason}")

                        log_file.write(f"{seq_path} -> None: {status} | {reason}\n")
                        log_file.flush()
                        continue

                # Decide destination dir robustly (creates it if needed)
                out_dir = compute_out_dir(seq_path, out_root_path)

                # Label and base filename
                ch_label = resolve_channel_label(seq_path, channel_names)
                base_stem = ch_label

                # Clean invalid files if requested
                if clean_invalid:
                    removed = clean_invalid_exports(out_dir, base_stem, debug)
                    stats['cleaned'] += removed

                # Check if valid export already exists
                if skip_existing:
                    existing = find_existing_export(out_dir, base_stem)
                    if existing:
                        status = "SKIPPED"
                        reason = f"Valid export already exists: {existing.name}"
                        stats['skipped_existing'] += 1

                        if debug:
                            print(f"[{idx}/{total}] {status}: {reason}")

                        log_file.write(f"{seq_path} -> {existing}: {status} | {reason}\n")
                        log_file.flush()
                        continue

                status, reason = "PENDING", ""
                final_path = None

                # Pre-checks
                if not seq_path.exists():
                    status, reason = "FAILED", "File does not exist"
                    stats['failed'] += 1
                elif seq_path.stat().st_size == 0:
                    status, reason = "FAILED", "File is empty"
                    stats['failed'] += 1

                # Attempt MP4 with retries
                if status == "PENDING":
                    # Calculate dynamic timeout based on file size
                    dynamic_timeout = calculate_timeout(seq_path)

                    # Get next available filename for MP4
                    exported_name, mp4_path = get_next_available_filename(out_dir, base_stem, ".mp4")

                    if debug:
                        file_size_mb = seq_path.stat().st_size / (1024 * 1024)
                        print(f"[{idx}/{total}] TRY MP4 -> {mp4_path} (size: {file_size_mb:.1f}MB, timeout: {dynamic_timeout}s)")

                    for attempt in range(1, MAX_RETRIES_MP4 + 1):
                        if debug:
                            print(f"[{idx}/{total}]  MP4 attempt {attempt}/{MAX_RETRIES_MP4}")

                        exitcode, reason = export_seq_once_streaming(
                            seq_path=seq_path,
                            out_dir=out_dir,
                            exported_name=exported_name[:-4],  # Remove .mp4 extension
                            container="mp4",
                            simulate=simulate,
                            spawn_console=spawn_console,
                            timeout_secs=dynamic_timeout,
                            kill_after_error_lines=KILL_AFTER_ERROR_LINES,
                            suppress_console_output=SUPPRESS_CLEXPORT_OUTPUT,
                            debug=debug
                        )

                        if exitcode == 0 and is_valid_video_file(mp4_path):
                            status = "SUCCESS_MP4"
                            stats['success_mp4'] += 1
                            final_path = mp4_path
                            break
                        else:
                            # Remove potentially invalid file
                            if mp4_path.exists() and not is_valid_video_file(mp4_path):
                                try:
                                    mp4_path.unlink()
                                    if debug:
                                        print(f"[{idx}/{total}]  Removed invalid MP4 after attempt {attempt}")
                                except:
                                    pass

                            if debug and attempt == MAX_RETRIES_MP4:
                                print(f"[{idx}/{total}]  MP4 failed after {MAX_RETRIES_MP4} attempts")

                # Fallback to AVI if MP4 failed and fallback is enabled
                if status == "PENDING" and fallback_avi:
                    # Get next available filename for AVI
                    exported_name, avi_path = get_next_available_filename(out_dir, base_stem, ".avi")

                    if debug:
                        print(f"[{idx}/{total}] FALLBACK AVI -> {avi_path}")

                    for attempt in range(1, MAX_RETRIES_AVI + 1):
                        if debug:
                            print(f"[{idx}/{total}]  AVI attempt {attempt}/{MAX_RETRIES_AVI}")

                        exitcode, reason = export_seq_once_streaming(
                            seq_path=seq_path,
                            out_dir=out_dir,
                            exported_name=exported_name[:-4],  # Remove .avi extension
                            container="avi",
                            simulate=simulate,
                            spawn_console=spawn_console,
                            timeout_secs=dynamic_timeout * 2,  # Give AVI more time
                            kill_after_error_lines=KILL_AFTER_ERROR_LINES * 2,  # More tolerant for AVI
                            suppress_console_output=SUPPRESS_CLEXPORT_OUTPUT,
                            debug=debug
                        )

                        if exitcode == 0 and is_valid_video_file(avi_path):
                            status = "SUCCESS_AVI"
                            stats['success_avi'] += 1
                            final_path = avi_path
                            break
                        else:
                            # Remove potentially invalid file
                            if avi_path.exists() and not is_valid_video_file(avi_path):
                                try:
                                    avi_path.unlink()
                                    if debug:
                                        print(f"[{idx}/{total}]  Removed invalid AVI after attempt {attempt}")
                                except:
                                    pass

                # Final status update
                if status == "PENDING":
                    status = "FAILED"
                    stats['failed'] += 1

                # Per-folder mapping + root log
                try:
                    with (out_dir / "_seq_mapping.txt").open('a', encoding='utf-8') as mapfile:
                        mapfile.write(f"{ch_label} = {seq_path} | {status} | {reason}\n")
                except Exception as e:
                    if debug:
                        print(f"[WARN] Could not write mapping file in {out_dir}: {e}")

                try:
                    output_file = final_path if final_path else "None"
                    log_file.write(f"{seq_path} -> {output_file}: {status} | {reason}\n")
                    log_file.flush()
                except Exception as e:
                    if debug:
                        print(f"[WARN] Could not write export_log: {e}")

                if debug:
                    print(f"[{idx}/{total}] [{status}] {seq_path} -> {final_path if final_path else 'FAILED'}")

            except Exception as e:
                if debug:
                    print(f"[{idx}/{total}] [HARD-FAIL] {seq_path} | {e}. Skipping.")
                stats['failed'] += 1
                continue

    # Print summary
    print("\n" + "=" * 60)
    print("EXPORT SUMMARY")
    print("=" * 60)
    print(f"Total files:       {stats['total']}")
    print(f"Skipped existing:  {stats['skipped_existing']}")
    if skip_damaged:
        print(f"Skipped damaged:   {stats['skipped_damaged']}")
    print(f"Success (MP4):     {stats['success_mp4']}")
    print(f"Success (AVI):     {stats['success_avi']}")
    print(f"Failed:            {stats['failed']}")
    print(f"Cleaned invalid:   {stats['cleaned']}")
    print("=" * 60)


# =========================
# Main entry point
# =========================
if __name__ == "__main__":
    # Hard-coded defaults – just hit Run in PyCharm
    DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "ScalpelDatabase.sqlite")
    SEQ_ROOT = r"F:\Room_8_Data\Sequence_Backup"
    OUT_ROOT = r"F:\Room_8_Data\Recordings"
    TABLE = "seq_status"

    # Parse command-line arguments
    parser = argparse.ArgumentParser(
        description="Export SEQ files to MP4/AVI format",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Export all files from database (default behavior):
  python seq_exporter.py

  # Export a specific file:
  python seq_exporter.py --file "F:\\path\\to\\file.seq"

  # Export multiple specific files:
  python seq_exporter.py --file "file1.seq" --file "file2.seq" --file "file3.seq"

  # Quick single file export:
  python seq_exporter.py --single "F:\\path\\to\\file.seq"
        """
    )

    parser.add_argument(
        '--file', '-f',
        action='append',
        dest='specific_files',
        metavar='PATH',
        help='Export a specific .seq file (can be used multiple times). Skips database query.'
    )

    parser.add_argument(
        '--single', '-s',
        metavar='PATH',
        help='Quick single file export using export_single_seq_file() (simpler, faster)'
    )

    parser.add_argument(
        '--debug', '-d',
        action='store_true',
        help='Enable debug output'
    )

    parser.add_argument(
        '--simulate',
        action='store_true',
        help='Dry run - don\'t actually export files'
    )

    args = parser.parse_args()

    # Handle single file quick export
    if args.single:
        print(f"[INFO] Quick export mode: {args.single}")
        success, message = export_single_seq_file(
            seq_file_path=args.single,
            out_root=OUT_ROOT,
            simulate=args.simulate,
            debug=args.debug,
            skip_existing=True,
            clean_invalid=True,
            fallback_avi=False
        )
        if success:
            print(f"[SUCCESS] {message}")
            sys.exit(0)
        else:
            print(f"[FAILED] {message}")
            sys.exit(1)

    # Handle pipeline mode (original behavior or with specific files)
    run_pipeline(
        db_path=DB_PATH,
        table=TABLE,
        seq_root=SEQ_ROOT,
        out_root=OUT_ROOT,
        channel_names={},  # add mapping if you want different names
        only_value=1,  # which DB flag to export (ignored when include_all=True)
        simulate=args.simulate,
        debug=args.debug,
        spawn_console=False,  # True = open a window per export
        skip_existing=True,  # skip files already exported
        clean_invalid=True,  # remove partial files first
        fallback_avi=False,  # try AVI if MP4 fails
        include_all=True if not args.specific_files else False,  # process all if no specific files
        skip_damaged=True,  # skip paths listed in damaged_seq_path.py
        specific_files=args.specific_files  # None = query DB, List = specific files
    )

