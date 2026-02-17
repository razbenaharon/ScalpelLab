"""
Smart SEQ Sync Converter — VFR-to-CFR Multi-Camera Synchronization
====================================================================
Converts NorPix SEQ files to MP4 with precise multi-camera time
synchronization using IDX binary index files.

Cameras in the same group, date, and case are synchronized to a shared
global timeline (union strategy), ensuring all output videos have
identical duration with black frames for pre-roll/post-roll.

Pipeline (VFR → CFR):
  1. Python extracts raw H.264 frames from SEQ using IDX byte offsets
     → saves to a temporary .h264 file
  2. Simultaneously generates a 'timecode format v2' text file with
     exact per-frame timestamps (ms) from IDX records
  3. mkvmerge muxes .h264 + timecodes → temporary .mkv (VFR container)
  4. FFmpeg reads the MKV, applies fps=30 (nearest-neighbor resampling:
     duplicates frames on gaps, drops on bursts), tpad for pre/post-roll,
     and hard-cuts at -t for exact global duration → final .mp4

This preserves the original capture timing through the MKV stage, then
normalizes to exactly 30 CFR for perfect multi-camera sync.

Features:
  • Parallel processing (configurable concurrent cameras)
  • HEVC/H.265 encoding (hevc_nvenc)
  • IDX-based raw H.264 extraction (no container timestamp drift)
  • mkvmerge VFR packaging with per-frame timecodes
  • FFmpeg fps=30 nearest-neighbor CFR normalization
  • Duration & sync validation via ffprobe after encoding
  • Automatic cleanup of all temporary files

Author: Raz (Technion)
"""

import sqlite3
import subprocess
import struct
import sys
import os
import re
import time
import threading
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Tuple, Optional, NamedTuple
from dataclasses import dataclass, field
from concurrent.futures import ThreadPoolExecutor, as_completed

# Add parent directory to path to import config
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import get_db_path, get_seq_root, get_mp4_root, DEFAULT_CAMERAS

# =========================
# Configuration
# =========================
DB_PATH = get_db_path()
SEQ_ROOT = get_seq_root()
OUT_ROOT = get_mp4_root()

TARGET_FPS = 30
MAX_PARALLEL = 3           # concurrent FFmpeg processes

# Camera synchronization groups
GROUP_A = ["Cart_Center_2", "Cart_LT_4", "Cart_RT_1", "General_3"]
GROUP_B = ["Monitor", "Patient_Monitor", "Ventilator_Monitor"]
ALL_GROUPS = {"A": GROUP_A, "B": GROUP_B}

# Executable search paths
FFMPEG_PATHS = [
    r"C:\Program Files\ffmpeg\bin\ffmpeg.exe",
    r"C:\ffmpeg\bin\ffmpeg.exe",
    r"C:\Program Files (x86)\ffmpeg\bin\ffmpeg.exe",
    "ffmpeg",
]

FFPROBE_PATHS = [
    r"C:\Program Files\ffmpeg\bin\ffprobe.exe",
    r"C:\ffmpeg\bin\ffprobe.exe",
    r"C:\Program Files (x86)\ffmpeg\bin\ffprobe.exe",
    "ffprobe",
]

MKVMERGE_PATHS = [
    r"C:\Program Files\MKVToolNix\mkvmerge.exe",
    r"C:\Program Files (x86)\MKVToolNix\mkvmerge.exe",
    r"C:\mkvtoolnix\mkvmerge.exe",
    "mkvmerge",
]

MIN_VALID_FILE_SIZE_MB = 1.0

# H.264 Annex B start code
ANNEX_B_START = b'\x00\x00\x00\x01'


# =========================
# Data Structures
# =========================
class IdxRecord(NamedTuple):
    """Single parsed IDX record (32 bytes)."""
    offset: int       # uint64 - byte position in SEQ file
    size: int         # uint32 - frame data size
    timestamp: float  # decoded full timestamp (seconds since epoch)
    frame_number: int # uint32 - sequential frame counter


@dataclass
class CameraTimeline:
    """Holds parsed IDX data and metadata for a single camera."""
    camera_name: str
    seq_path: Path
    idx_path: Path
    records: List[IdxRecord] = field(default_factory=list)
    width: int = 0
    height: int = 0
    pix_fmt: str = "yuv420p"
    t_start: float = 0.0
    t_end: float = 0.0

    @property
    def duration(self) -> float:
        return self.t_end - self.t_start

    @property
    def frame_count(self) -> int:
        return len(self.records)

    @property
    def source_fps(self) -> float:
        """Calculate actual source FPS from IDX timestamps."""
        if self.duration > 0 and self.frame_count > 1:
            return (self.frame_count - 1) / self.duration
        return TARGET_FPS


@dataclass
class SessionGroup:
    """A group of cameras for a specific date+case that must be synchronized."""
    recording_date: str
    case_no: int
    group_name: str
    cameras: Dict[str, CameraTimeline] = field(default_factory=dict)
    t_global_start: float = float('inf')
    t_global_end: float = float('-inf')

    @property
    def global_duration(self) -> float:
        if self.t_global_start == float('inf'):
            return 0.0
        return self.t_global_end - self.t_global_start

    @property
    def total_output_frames(self) -> int:
        return int(round(self.global_duration * TARGET_FPS))


# =========================
# Utility Functions
# =========================
def find_executable(paths: List[str]) -> Optional[str]:
    """Find an executable in common locations or PATH."""
    for path in paths:
        if path in ("ffmpeg", "ffprobe", "mkvmerge"):
            try:
                which_cmd = "where" if os.name == 'nt' else "which"
                result = subprocess.run(
                    [which_cmd, path],
                    capture_output=True, text=True, timeout=5
                )
                if result.returncode == 0:
                    found = result.stdout.strip().split('\n')[0]
                    if os.path.exists(found):
                        return found
            except Exception:
                pass
        else:
            if os.path.exists(path):
                return path
    return None


def find_ffmpeg() -> Optional[str]:
    return find_executable(FFMPEG_PATHS)


def find_ffprobe() -> Optional[str]:
    return find_executable(FFPROBE_PATHS)


def find_mkvmerge() -> Optional[str]:
    return find_executable(MKVMERGE_PATHS)


def is_valid_video_file(file_path: Path, min_size_mb: float = MIN_VALID_FILE_SIZE_MB) -> bool:
    """Check if video file exists and has reasonable size."""
    if not file_path.exists():
        return False
    try:
        return file_path.stat().st_size / (1024 * 1024) > min_size_mb
    except Exception:
        return False


def get_video_duration(filepath: Path, ffprobe_path: str) -> Optional[float]:
    """Get video duration in seconds via ffprobe."""
    try:
        cmd = [
            ffprobe_path, "-v", "error",
            "-show_entries", "format=duration",
            "-of", "csv=p=0",
            str(filepath),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        if result.returncode == 0 and result.stdout.strip():
            return float(result.stdout.strip())
    except Exception:
        pass
    return None


def get_video_frame_count(filepath: Path, ffprobe_path: str) -> Optional[int]:
    """Get video frame count via ffprobe."""
    try:
        cmd = [
            ffprobe_path, "-v", "error",
            "-select_streams", "v:0",
            "-count_packets",
            "-show_entries", "stream=nb_read_packets",
            "-of", "csv=p=0",
            str(filepath),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if result.returncode == 0 and result.stdout.strip():
            return int(result.stdout.strip())
    except Exception:
        pass
    return None


def fmt_seconds(s: float) -> str:
    """Format seconds as HH:MM:SS."""
    h = int(s) // 3600
    m = (int(s) % 3600) // 60
    sec = int(s) % 60
    return f"{h:02d}:{m:02d}:{sec:02d}"


def cleanup_temp_files(*paths: Path):
    """Silently remove temporary files."""
    for p in paths:
        try:
            if p and p.exists():
                p.unlink()
        except Exception:
            pass


# =========================
# IDX Binary Parser
# =========================
IDX_RECORD_SIZE = 32
IDX_STRUCT = struct.Struct('<QIIIIIi')


def decode_timestamp(ts_seconds: int, ts_sub: int) -> float:
    """
    Decode NorPix IDX packed timestamp.

    ts_sub layout:
        bits 15-0  (low word):  milliseconds (0-999)
        bits 31-16 (high word): microseconds within ms (0-999)
    """
    ms = ts_sub & 0xFFFF
    us = (ts_sub >> 16) & 0xFFFF
    return ts_seconds + ms / 1000.0 + us / 1000000.0


def parse_idx_file(idx_path: Path) -> List[IdxRecord]:
    """
    Parse a NorPix IDX binary index file into a list of IdxRecords.
    Format: 32-byte records, no header, little-endian.
    """
    file_size = idx_path.stat().st_size
    if file_size == 0:
        return []

    if file_size % IDX_RECORD_SIZE != 0:
        print(f"  ⚠️  IDX file size ({file_size}) not divisible by {IDX_RECORD_SIZE}, "
              f"truncating to {file_size // IDX_RECORD_SIZE} records")

    num_records = file_size // IDX_RECORD_SIZE
    records = []

    with open(idx_path, 'rb') as f:
        raw = f.read(num_records * IDX_RECORD_SIZE)

    for i in range(num_records):
        chunk = raw[i * IDX_RECORD_SIZE : (i + 1) * IDX_RECORD_SIZE]
        offset, size, ts_sec, ts_sub, _reserved, _flags, frame_no = IDX_STRUCT.unpack(chunk)

        if size == 0:
            continue

        timestamp = decode_timestamp(ts_sec, ts_sub)
        frame_no_unsigned = frame_no & 0xFFFFFFFF

        records.append(IdxRecord(
            offset=offset,
            size=size,
            timestamp=timestamp,
            frame_number=frame_no_unsigned,
        ))

    return records


# =========================
# Resolution Detection (ffprobe)
# =========================
def detect_resolution(seq_path: Path, ffprobe_path: str) -> Tuple[int, int, str]:
    """Use ffprobe on the SEQ file to detect width, height, pixel format."""
    cmd = [
        ffprobe_path,
        "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height,pix_fmt",
        "-of", "csv=p=0",
        str(seq_path),
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode == 0 and result.stdout.strip():
            parts = result.stdout.strip().split(',')
            if len(parts) >= 3:
                return int(parts[0]), int(parts[1]), parts[2].strip()
            elif len(parts) == 2:
                return int(parts[0]), int(parts[1]), "yuv420p"
    except Exception as e:
        print(f"  ⚠️  ffprobe failed: {e}")

    print(f"  ⚠️  Could not detect resolution, using defaults (1920x1080, yuv420p)")
    return 1920, 1080, "yuv420p"


# =========================
# Database Functions
# =========================
def connect_db(db_path: str) -> sqlite3.Connection:
    return sqlite3.connect(db_path)


def get_all_sessions(db_path: str, cameras: Optional[List[str]] = None) -> List[Dict]:
    """Get all SEQ files that need MP4 conversion."""
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
        AND s.size_mb >= 200
        AND (m.size_mb IS NULL OR m.size_mb < 1)
    ORDER BY s.recording_date DESC, s.case_no, s.camera_name
    """.format(','.join(['?'] * len(cameras)))

    cursor.execute(query, cameras)

    files = []
    for row in cursor.fetchall():
        files.append({
            'recording_date': row[0],
            'case_no': row[1],
            'camera_name': row[2],
            'seq_size_mb': row[3],
        })

    conn.close()
    return files


def build_seq_path(recording_date: str, case_no: int, camera_name: str) -> Optional[Path]:
    """Build path to the .seq file and return first match."""
    yy = recording_date[2:4]
    mm = recording_date[5:7]
    dd = recording_date[8:10]
    data_folder = f"DATA_{yy}-{mm}-{dd}"
    case_folder = f"Case{case_no}"

    seq_dir = Path(SEQ_ROOT) / data_folder / case_folder / camera_name
    seq_files = list(seq_dir.glob("*.seq"))
    return seq_files[0] if seq_files else None


def build_idx_path(seq_path: Path) -> Optional[Path]:
    """Find the .idx file corresponding to a .seq file (X.seq.idx)."""
    idx_path = Path(str(seq_path) + '.idx')
    if idx_path.exists():
        return idx_path

    # Case-insensitive fallback
    parent = seq_path.parent
    seq_name = seq_path.name
    for f in parent.iterdir():
        if f.name.lower() == (seq_name + '.idx').lower():
            return f

    return None


def compute_out_dir(seq_path: Path, out_root_path: Path) -> Path:
    """Mirror directory structure under output root."""
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
        date = "DATA_Unknown"
        if seq_path.parent and seq_path.parent.parent and seq_path.parent.parent.parent:
            d = seq_path.parent.parent.parent.name
            if d.upper().startswith("DATA_"):
                date = d
        out_dir = out_root_path / date / case / channel

    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir


def get_next_available_filename(out_dir: Path, base_stem: str, extension: str) -> Tuple[str, Path]:
    """Get next available filename that doesn't exist."""
    filename = f"{base_stem}{extension}"
    file_path = out_dir / filename
    if not file_path.exists():
        return filename, file_path

    counter = 1
    while counter < 1000:
        filename = f"{base_stem}_{counter}{extension}"
        file_path = out_dir / filename
        if not file_path.exists():
            return filename, file_path
        counter += 1

    raise ValueError(f"Could not find available filename for {base_stem} after 1000 attempts")


# =========================
# Camera Group Discovery
# =========================
def get_camera_group(camera_name: str) -> Optional[str]:
    """Return the group name ('A' or 'B') for a camera, or None if ungrouped."""
    for group_name, members in ALL_GROUPS.items():
        if camera_name in members:
            return group_name
    return None


def build_session_groups(files: List[Dict], ffprobe_path: str) -> List[SessionGroup]:
    """Organize files into synchronized session groups."""
    group_map: Dict[Tuple[str, int, str], SessionGroup] = {}

    for file_info in files:
        date = file_info['recording_date']
        case = file_info['case_no']
        camera = file_info['camera_name']

        grp = get_camera_group(camera)
        if grp is None:
            grp = f"solo_{camera}"

        key = (date, case, grp)
        if key not in group_map:
            group_map[key] = SessionGroup(
                recording_date=date,
                case_no=case,
                group_name=grp,
            )

        session = group_map[key]

        # Build paths
        seq_path = build_seq_path(date, case, camera)
        if seq_path is None or not seq_path.exists():
            print(f"  ⚠️  SEQ not found: {date} Case{case} {camera} — skipping")
            continue

        idx_path = build_idx_path(seq_path)
        if idx_path is None:
            print(f"  ⚠️  IDX not found for: {seq_path.name} — skipping camera")
            continue

        # Parse IDX
        print(f"  Parsing IDX: {camera} ...", end="", flush=True)
        records = parse_idx_file(idx_path)
        if not records:
            print(f" ⚠️  0 records, skipping")
            continue
        print(f" {len(records)} frames")

        # Detect resolution
        w, h, pix_fmt = detect_resolution(seq_path, ffprobe_path)

        cam_timeline = CameraTimeline(
            camera_name=camera,
            seq_path=seq_path,
            idx_path=idx_path,
            records=records,
            width=w,
            height=h,
            pix_fmt=pix_fmt,
            t_start=records[0].timestamp,
            t_end=records[-1].timestamp,
        )

        session.cameras[camera] = cam_timeline

        if cam_timeline.t_start < session.t_global_start:
            session.t_global_start = cam_timeline.t_start
        if cam_timeline.t_end > session.t_global_end:
            session.t_global_end = cam_timeline.t_end

    return [sg for sg in group_map.values() if sg.cameras]


# =========================
# Raw H.264 Extraction from SEQ via IDX
# =========================
def _extract_sps_pps(seq_f, records, max_search=500) -> Optional[bytes]:
    """
    Extract SPS and PPS NAL units from the beginning of the stream.

    Some cameras (like Patient_Monitor) only emit SPS/PPS once at the start
    of recording, not with every IDR. We extract these once and can prepend
    them to the stream to ensure it's always decodable.

    Returns bytes containing the SPS+PPS NAL units (with Annex B start codes),
    or None if not found.
    """
    sps_data = None
    pps_data = None

    for i in range(min(max_search, len(records))):
        rec = records[i]
        seq_f.seek(rec.offset)
        raw = seq_f.read(min(rec.size, 8192))  # SPS+PPS are tiny, first 8KB is plenty

        # Find all NAL units in this frame
        pos = 0
        while True:
            idx = raw.find(ANNEX_B_START, pos)
            if idx < 0 or idx + 4 >= len(raw):
                break
            nal_type = raw[idx + 4] & 0x1F

            # Find the end of this NAL (next start code or end of data)
            next_start = raw.find(ANNEX_B_START, idx + 4)
            if next_start < 0:
                nal_bytes = raw[idx:]
            else:
                nal_bytes = raw[idx:next_start]

            if nal_type == 7 and sps_data is None:  # SPS
                sps_data = nal_bytes
            elif nal_type == 8 and pps_data is None:  # PPS
                pps_data = nal_bytes

            if sps_data and pps_data:
                return sps_data + pps_data

            pos = idx + 4

    # Return whatever we found
    if sps_data:
        return sps_data + (pps_data or b'')
    return None


# =========================
# VFR-to-CFR Pipeline Steps
# =========================
def step1_extract_h264_and_timecodes(
    cam: CameraTimeline,
    temp_h264_path: Path,
    temp_timecodes_path: Path,
) -> Tuple[bool, int]:
    """
    Step 1: Extract raw H.264 bitstream and generate timecodes v2 file.

    Reads each frame from the SEQ file using IDX byte offsets, writes the
    raw Annex B data to a .h264 file, and simultaneously writes a timecodes
    v2 text file with the exact timestamp (in ms) for each frame.

    Returns (success, frames_written).
    """
    cam_name = cam.camera_name
    frames_written = 0

    # Timecodes v2 format: header line + one timestamp (ms) per frame
    # Timestamps are relative to the first frame (frame 0 = 0ms)
    t_base = cam.records[0].timestamp

    with open(cam.seq_path, 'rb') as seq_f:
        # Extract SPS+PPS header
        sps_pps = _extract_sps_pps(seq_f, cam.records)
        if sps_pps:
            print(f"  [{cam_name}] Wrote SPS+PPS header ({len(sps_pps)} bytes)")
        else:
            print(f"  [{cam_name}] ⚠️  Could not find SPS/PPS in stream!")

        with open(temp_h264_path, 'wb') as h264_f, \
             open(temp_timecodes_path, 'w') as tc_f:

            # Write timecodes v2 header
            tc_f.write("# timecode format v2\n")

            # Write SPS+PPS as prologue (not counted as a frame)
            if sps_pps:
                h264_f.write(sps_pps)

            # Write all frames
            for rec in cam.records:
                seq_f.seek(rec.offset)
                raw = seq_f.read(rec.size)

                # Find Annex B start code and write H.264 data
                pos = raw.find(ANNEX_B_START)
                if pos >= 0:
                    h264_f.write(raw[pos:])

                    # Write timecode in milliseconds relative to first frame
                    ts_ms = (rec.timestamp - t_base) * 1000.0
                    tc_f.write(f"{ts_ms:.3f}\n")

                    frames_written += 1

    if frames_written == 0:
        return False, 0

    h264_size_mb = temp_h264_path.stat().st_size / (1024 * 1024)
    print(f"  [{cam_name}] Extracted {frames_written} frames → "
          f"{temp_h264_path.name} ({h264_size_mb:.1f} MB) + "
          f"{temp_timecodes_path.name}")

    return True, frames_written


def step2_mkvmerge_vfr(
    cam_name: str,
    mkvmerge_path: str,
    temp_h264_path: Path,
    temp_timecodes_path: Path,
    temp_mkv_path: Path,
) -> Tuple[bool, str]:
    """
    Step 2: Use mkvmerge to mux raw H.264 + timecodes into a VFR MKV.

    mkvmerge reads the .h264 as a raw bitstream and applies the timecodes
    v2 file to assign exact timestamps to each frame, preserving the
    original variable capture timing in the container.

    Returns (success, error_message).
    """
    cmd = [
        mkvmerge_path,
        "-o", str(temp_mkv_path),
        "--timecodes", f"0:{temp_timecodes_path}",
        str(temp_h264_path),
    ]

    print(f"  [{cam_name}] mkvmerge: muxing VFR MKV ...")

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=600,
        )
        # mkvmerge returns 0 for success, 1 for warnings, 2 for errors
        if result.returncode >= 2:
            err = result.stderr[-500:] if result.stderr else result.stdout[-500:]
            return False, f"mkvmerge failed (code {result.returncode}): {err}"

        if result.returncode == 1:
            print(f"  [{cam_name}] mkvmerge: completed with warnings")

        mkv_size_mb = temp_mkv_path.stat().st_size / (1024 * 1024)
        print(f"  [{cam_name}] mkvmerge: {temp_mkv_path.name} ({mkv_size_mb:.1f} MB)")
        return True, ""

    except subprocess.TimeoutExpired:
        return False, "mkvmerge timed out after 600s"
    except Exception as e:
        return False, f"mkvmerge error: {e}"


def step3_ffmpeg_cfr_encode(
    cam: CameraTimeline,
    session: SessionGroup,
    cam_name: str,
    ffmpeg_path: str,
    temp_mkv_path: Path,
    out_path: Path,
) -> Tuple[bool, str]:
    """
    Step 3: FFmpeg reads VFR MKV, normalizes to CFR 30fps, adds sync
    padding, and encodes to HEVC MP4.

    The fps=30 filter performs nearest-neighbor sampling:
      - Gaps (camera stutter): duplicates the last available frame
      - Bursts (frames too close): drops excess frames
    This ensures exactly 30 frames per second in the output.

    The tpad filter adds black frames at the start to align this camera
    with the global session start time. The -t flag hard-cuts the output
    at the exact global session duration.

    Returns (success, error_or_info_message).
    """
    total_frames = session.total_output_frames
    if total_frames <= 0:
        return False, "Zero output frames"

    # Calculate sync padding from IDX timestamps
    pre_roll_sec = cam.t_start - session.t_global_start
    target_duration = session.global_duration

    # Convert pre-roll from seconds to discrete frame count
    pre_roll_frames = int(round(pre_roll_sec * TARGET_FPS))

    print(f"  [{cam_name}] Pre-roll: {pre_roll_sec:.3f}s → {pre_roll_frames} frames | "
          f"Target duration: {target_duration:.3f}s")

    # Build filter chain
    # fps=30 does the VFR→CFR conversion (nearest neighbor resampling)
    filters = [f"fps={TARGET_FPS}"]

    tpad_parts = []
    if pre_roll_frames > 0:
        tpad_parts.append(f"start={pre_roll_frames}")   # discrete frame count
    tpad_parts.append("stop=-1")                         # infinite black post-roll
    tpad_parts.append("color=black")
    filters.append(f"tpad={':'.join(tpad_parts)}")

    vf = ','.join(filters)

    # Build FFmpeg command — read from VFR MKV
    ffmpeg_cmd = [
        ffmpeg_path,
        "-y",
        "-hwaccel", "cuda",
        "-i", str(temp_mkv_path),           # VFR MKV with true timecodes
        "-vf", vf,
        "-c:v", "hevc_nvenc",               # HEVC/H.265 encoder
        "-preset", "p1",
        "-rc", "vbr",
        "-cq", "35",
        "-pix_fmt", "yuv420p",
        "-r", str(TARGET_FPS),
        "-t", f"{target_duration:.6f}",      # hard-cut at exact global duration
        "-movflags", "+faststart",
        str(out_path),
    ]

    print(f"  [{cam_name}] Filter: {vf}")
    print(f"  [{cam_name}] Hard-cut: -t {target_duration:.6f}s | Encoder: hevc_nvenc (H.265)")
    print(f"  [{cam_name}] Output: {out_path}")

    # Launch FFmpeg
    try:
        process = subprocess.Popen(
            ffmpeg_cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            bufsize=0,
        )
    except Exception as e:
        return False, f"FFmpeg launch failed: {e}"

    # Start stderr reader in background thread (prevents pipe deadlock)
    stderr_lines = []
    last_progress_holder = [time.time()]

    def stderr_reader():
        try:
            while True:
                raw_line = process.stderr.readline()
                if not raw_line:
                    break
                line = raw_line.decode('utf-8', errors='replace')
                stderr_lines.append(line)
                m = re.search(r'frame=\s*(\d+)', line)
                if m:
                    now = time.time()
                    if now - last_progress_holder[0] >= 5.0:
                        current_frame = int(m.group(1))
                        pct = min(100.0, current_frame / max(1, total_frames) * 100)
                        fps_match = re.search(r'fps=\s*([\d.]+)', line)
                        fps_str = f" @ {fps_match.group(1)} fps" if fps_match else ""
                        speed_match = re.search(r'speed=\s*([\d.]+)x', line)
                        speed_str = f" ({speed_match.group(1)}x)" if speed_match else ""
                        print(f"  [{cam_name}] ⏳ {pct:5.1f}% — "
                              f"frame {current_frame}/{total_frames}{fps_str}{speed_str}")
                        last_progress_holder[0] = now
        except Exception:
            pass

    stderr_thread = threading.Thread(target=stderr_reader, daemon=True)
    stderr_thread.start()

    # Wait for FFmpeg to finish (2 hours max for very long recordings)
    try:
        process.wait(timeout=7200)
    except subprocess.TimeoutExpired:
        process.kill()
        stderr_thread.join(timeout=5)
        return False, "FFmpeg timed out after 7200s"

    stderr_thread.join(timeout=10)

    if process.returncode != 0:
        err_tail = ''.join(stderr_lines[-10:])
        return False, f"FFmpeg exited with code {process.returncode}:\n{err_tail}"

    return True, ""


# =========================
# Full VFR→CFR Conversion (runs in thread)
# =========================
def process_camera_sync(
    cam: CameraTimeline,
    session: SessionGroup,
    ffmpeg_path: str,
    ffprobe_path: str,
    mkvmerge_path: str,
    out_path: Path,
) -> Dict:
    """
    Convert a single camera's SEQ to MP4 using the VFR→CFR pipeline.
    Returns a result dict for the summary.

    Pipeline:
      1. Extract raw H.264 + timecodes v2 from SEQ (using IDX offsets)
      2. mkvmerge: mux into VFR MKV (preserves original capture timing)
      3. FFmpeg: fps=30 nearest-neighbor → tpad → -t → hevc_nvenc → MP4
      4. Verify output and clean up all temporary files
    """
    cam_name = cam.camera_name
    result = {
        'camera': cam_name,
        'success': False,
        'message': '',
        'duration': None,
        'frames': None,
        'output_path': None,
    }

    # Temporary file paths (all in the same directory as the output)
    temp_dir = out_path.parent
    temp_h264 = temp_dir / f"{cam_name}_temp.h264"
    temp_timecodes = temp_dir / f"{cam_name}_temp_timecodes.txt"
    temp_mkv = temp_dir / f"{cam_name}_temp_vfr.mkv"

    try:
        # === Step 1: Extract H.264 + timecodes ===
        print(f"  [{cam_name}] Step 1/3: Extracting H.264 bitstream + timecodes ...")
        ok, frames_written = step1_extract_h264_and_timecodes(
            cam, temp_h264, temp_timecodes,
        )
        if not ok:
            result['message'] = "No valid H.264 frames extracted"
            print(f"  [{cam_name}] ❌ No H.264 frames extracted")
            return result

        # === Step 2: mkvmerge VFR packaging ===
        print(f"  [{cam_name}] Step 2/3: VFR packaging with mkvmerge ...")
        ok, err_msg = step2_mkvmerge_vfr(
            cam_name, mkvmerge_path,
            temp_h264, temp_timecodes, temp_mkv,
        )
        if not ok:
            result['message'] = err_msg
            print(f"  [{cam_name}] ❌ mkvmerge failed: {err_msg}")
            return result

        # H.264 and timecodes no longer needed after mkvmerge
        cleanup_temp_files(temp_h264, temp_timecodes)

        # === Step 3: FFmpeg CFR encode ===
        print(f"  [{cam_name}] Step 3/3: CFR normalization + HEVC encode ...")
        ok, err_msg = step3_ffmpeg_cfr_encode(
            cam, session, cam_name, ffmpeg_path, temp_mkv, out_path,
        )
        if not ok:
            result['message'] = err_msg
            print(f"  [{cam_name}] ❌ FFmpeg failed: {err_msg}")
            return result

        # MKV no longer needed after FFmpeg
        cleanup_temp_files(temp_mkv)

        # === Verify output ===
        if not is_valid_video_file(out_path):
            result['message'] = "Output file is too small or missing"
            print(f"  [{cam_name}] ❌ Output file too small or missing")
            return result

        size_mb = out_path.stat().st_size / (1024 * 1024)

        # Verify output duration and frame count with ffprobe
        dur = get_video_duration(out_path, ffprobe_path)
        frames = get_video_frame_count(out_path, ffprobe_path)

        duration_str = ""
        if dur is not None:
            expected_dur = session.global_duration
            drift = abs(dur - expected_dur)
            duration_str = f" | {dur:.1f}s (expected {expected_dur:.1f}s, drift={drift:.2f}s)"

        result['success'] = True
        result['duration'] = dur
        result['frames'] = frames
        result['output_path'] = out_path
        result['message'] = f"{size_mb:.1f} MB{duration_str}"

        print(f"  [{cam_name}] ✅ {result['message']}")
        return result

    except Exception as e:
        result['message'] = f"Error: {e}"
        print(f"  [{cam_name}] ❌ Error: {e}")
        return result

    finally:
        # Always clean up any remaining temp files
        cleanup_temp_files(temp_h264, temp_timecodes, temp_mkv)


# =========================
# Main Pipeline
# =========================
def display_session_plan(groups: List[SessionGroup]):
    """Print a summary of what will be processed."""
    print("\n" + "=" * 90)
    print("SYNCHRONIZATION PLAN")
    print("=" * 90)

    for sg in groups:
        dur = sg.global_duration
        n_frames = sg.total_output_frames

        print(f"\n📁 {sg.recording_date} Case{sg.case_no} — Group {sg.group_name}")
        print(f"   Global timeline: {dur:.3f}s ({fmt_seconds(dur)}) → {n_frames} frames @ {TARGET_FPS} FPS")

        for cam_name, cam in sg.cameras.items():
            offset_start = cam.t_start - sg.t_global_start
            offset_end = sg.t_global_end - cam.t_end
            pre_frames = int(round(offset_start * TARGET_FPS))
            print(f"   📷 {cam_name:25s} | {cam.width}x{cam.height} {cam.pix_fmt:12s} | "
                  f"{cam.frame_count:7d} src frames @ {cam.source_fps:.2f}fps | "
                  f"pre={offset_start:.3f}s ({pre_frames} frames)  post={offset_end:.3f}s")

    print("\n" + "=" * 90)


def main():
    print("=" * 90)
    print("SMART SEQ SYNC CONVERTER — VFR-to-CFR Multi-Camera Synchronization")
    print("=" * 90)
    print(f"Database:    {DB_PATH}")
    print(f"SEQ Root:    {SEQ_ROOT}")
    print(f"Output Root: {OUT_ROOT}")
    print(f"Target FPS:  {TARGET_FPS}")
    print(f"Parallel:    {MAX_PARALLEL} cameras")
    print(f"Encoder:     hevc_nvenc (H.265)")
    print(f"Pipeline:    SEQ→H.264+timecodes→mkvmerge(VFR MKV)→FFmpeg fps={TARGET_FPS}(CFR)→MP4")
    print()

    # Check tool availability
    ffmpeg_path = find_ffmpeg()
    if not ffmpeg_path:
        print("❌ FFmpeg not found! Install FFmpeg or add it to PATH.")
        return
    print(f"✓ FFmpeg:    {ffmpeg_path}")

    ffprobe_path = find_ffprobe()
    if not ffprobe_path:
        print("❌ FFprobe not found! It should come with FFmpeg.")
        return
    print(f"✓ FFprobe:   {ffprobe_path}")

    mkvmerge_path = find_mkvmerge()
    if not mkvmerge_path:
        print("❌ mkvmerge not found! Install MKVToolNix or add it to PATH.")
        print("   Download: https://mkvtoolnix.download/downloads.html")
        return
    print(f"✓ mkvmerge:  {mkvmerge_path}")

    all_cameras = list(set(GROUP_A + GROUP_B))
    print(f"\nCamera groups:")
    print(f"  Group A: {', '.join(GROUP_A)}")
    print(f"  Group B: {', '.join(GROUP_B)}")
    print()

    print("Querying database for pending conversions...")
    files = get_all_sessions(DB_PATH, cameras=all_cameras)

    if not files:
        print("✓ No files need converting!")
        return

    print(f"Found {len(files)} camera files pending conversion")

    print("\nScanning IDX files and detecting resolutions...")
    print("-" * 90)
    session_groups = build_session_groups(files, ffprobe_path)

    if not session_groups:
        print("❌ No valid session groups found (missing IDX files?)")
        return

    display_session_plan(session_groups)

    total_cameras = sum(len(sg.cameras) for sg in session_groups)
    total_frames = sum(
        sg.total_output_frames * len(sg.cameras)
        for sg in session_groups
    )

    print(f"\nTotal: {total_cameras} cameras across {len(session_groups)} groups")
    print(f"Estimated total output frames: {total_frames:,}")
    print()

    response = input("Proceed with synchronized conversion? (y/n): ").strip().lower()
    if response != 'y':
        print("Cancelled.")
        return

    print("\n" + "=" * 90)
    print(f"STARTING VFR→CFR SYNCHRONIZED CONVERSION (up to {MAX_PARALLEL} cameras in parallel)")
    print("=" * 90)

    all_results = []
    global_start_time = time.time()

    group_idx = 0
    for sg in session_groups:
        group_idx += 1
        print(f"\n{'─' * 90}")
        print(f"[Group {group_idx}/{len(session_groups)}] "
              f"{sg.recording_date} Case{sg.case_no} — Group {sg.group_name} "
              f"({len(sg.cameras)} cameras, {sg.global_duration:.1f}s / {fmt_seconds(sg.global_duration)})")
        print(f"{'─' * 90}")

        # Prepare tasks: resolve output paths before launching threads
        tasks = []
        for cam_name, cam in sg.cameras.items():
            out_root_path = Path(OUT_ROOT).resolve()
            out_dir = compute_out_dir(cam.seq_path, out_root_path)
            _, mp4_path = get_next_available_filename(out_dir, cam_name, ".mp4")

            if is_valid_video_file(mp4_path):
                print(f"\n  📷 {cam_name} — ⏭️  SKIP: MP4 already exists ({mp4_path.name})")
                all_results.append({
                    'camera': cam_name, 'success': True, 'skipped': True,
                    'message': 'Already exists', 'duration': None, 'frames': None,
                })
                continue

            tasks.append((cam_name, cam, mp4_path))

        if not tasks:
            print("  All cameras already converted, skipping group.")
            continue

        # Launch parallel processing
        with ThreadPoolExecutor(max_workers=MAX_PARALLEL) as executor:
            futures = {}
            for cam_name, cam, mp4_path in tasks:
                future = executor.submit(
                    process_camera_sync,
                    cam, sg, ffmpeg_path, ffprobe_path, mkvmerge_path, mp4_path,
                )
                futures[future] = (cam_name, mp4_path)

            for future in as_completed(futures):
                cam_name, mp4_path = futures[future]
                try:
                    result = future.result()
                    result['skipped'] = False
                    all_results.append(result)

                    # Clean up failed output files
                    if not result['success'] and mp4_path.exists():
                        try:
                            mp4_path.unlink()
                        except Exception:
                            pass

                except Exception as e:
                    print(f"  [{cam_name}] ❌ Exception: {e}")
                    all_results.append({
                        'camera': cam_name, 'success': False, 'skipped': False,
                        'message': str(e), 'duration': None, 'frames': None,
                    })
                    if mp4_path.exists():
                        try:
                            mp4_path.unlink()
                        except Exception:
                            pass

    # =========================
    # Summary & Sync Validation
    # =========================
    total_time = time.time() - global_start_time
    print()
    print("=" * 90)
    print("CONVERSION COMPLETE")
    print("=" * 90)

    successes = [r for r in all_results if r['success'] and not r.get('skipped')]
    failures = [r for r in all_results if not r['success']]
    skipped = [r for r in all_results if r.get('skipped')]

    print(f"\n  Processed: {len(all_results)} cameras in {total_time:.1f}s ({fmt_seconds(total_time)})")
    print(f"  Success:   {len(successes)}")
    print(f"  Skipped:   {len(skipped)}")
    print(f"  Failed:    {len(failures)}")
    print(f"  Encoder:   hevc_nvenc (H.265)")
    print(f"  Strategy:  VFR→CFR via mkvmerge+FFmpeg @ {TARGET_FPS} FPS")
    print(f"  Pipeline:  SEQ→H.264+timecodes→MKV(VFR)→MP4(CFR)")
    print(f"  Parallel:  {MAX_PARALLEL} cameras")

    if failures:
        print(f"\n  ❌ FAILURES:")
        for r in failures:
            print(f"     {r['camera']}: {r['message']}")

    # Sync validation across groups
    if len(successes) >= 2:
        print(f"\n  ⏱️  SYNC VALIDATION (durations must match within group):")
        durations = [(r['camera'], r['duration'], r['frames']) for r in successes if r['duration']]
        if durations:
            ref_dur = durations[0][1]
            ref_frames = durations[0][2]
            max_drift = 0.0

            for cam, dur, frames in durations:
                drift = abs(dur - ref_dur) if ref_dur and dur else 0
                max_drift = max(max_drift, drift)
                frame_diff = abs(frames - ref_frames) if ref_frames and frames else "?"
                status = "✓" if drift < 0.05 else "⚠️  DRIFT"
                print(f"     {cam:25s}: {dur:.3f}s ({frames} frames) — "
                      f"Δ={drift:.3f}s ({frame_diff} frames) {status}")

            print()
            if max_drift < 0.034:  # less than 1 frame at 30fps
                print(f"  ✅ SYNC OK — max drift {max_drift:.3f}s (< 1 frame)")
            elif max_drift < 0.1:
                print(f"  ⚠️  SYNC MARGINAL — max drift {max_drift:.3f}s ({int(max_drift * TARGET_FPS)} frames)")
            else:
                print(f"  ❌ SYNC FAILED — max drift {max_drift:.3f}s ({int(max_drift * TARGET_FPS)} frames)")

    print("=" * 90)


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
