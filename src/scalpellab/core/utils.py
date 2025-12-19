"""
Common Utilities for ScalpelLab
"""

import re
import os
import subprocess
import json
from pathlib import Path
from typing import Optional, Tuple

def parse_recording_date_and_case(data_dir_name: str, case_dir_name: str) -> Optional[Tuple[str, int]]:
    """Convert DATA_YY-MM-DD + CaseN -> (YYYY-MM-DD, N)."""
    m = re.fullmatch(r"DATA_(\d{2})-(\d{2})-(\d{2})", data_dir_name)
    n = re.fullmatch(r"Case(\d+)", case_dir_name)
    if not m or not n:
        return None
    yy, mm, dd = m.groups()
    yyyy = f"20{yy}" if int(yy) <= 69 else f"19{yy}"
    case_no = int(n.group(1))
    return f"{yyyy}-{mm}-{dd}", case_no

def get_video_duration(video_path: Path) -> Optional[float]:
    """Get video duration in minutes using ffprobe."""
    try:
        ffprobe_paths = [
            r"C:\\Program Files\\ffmpeg\\bin\\ffprobe.exe",
            r"C:\\ffmpeg\\bin\\ffprobe.exe",
            r"C:\\Program Files (x86)\\ffmpeg\\bin\\ffprobe.exe",
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
                return float(duration_str) / 60.0  # Convert to minutes
    except Exception:
        pass
    return None

def find_ffmpeg() -> Optional[str]:
    """Find ffmpeg executable in common locations or PATH."""
    ffmpeg_paths = [
        r"C:\\Program Files\\ffmpeg\\bin\\ffmpeg.exe",
        r"C:\\ffmpeg\\bin\\ffmpeg.exe",
        r"C:\\Program Files (x86)\\ffmpeg\\bin\\ffmpeg.exe",
        "ffmpeg"
    ]
    for path in ffmpeg_paths:
        if path == "ffmpeg":
            try:
                result = subprocess.run(
                    ["where" if os.name == 'nt' else "which", "ffmpeg"],
                    capture_output=True, text=True, timeout=5
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

def is_valid_video_file(file_path: Path, min_size_mb: float = 1.0) -> bool:

    """Check if video file exists and has reasonable size."""

    if not file_path.exists():

        return False

    try:

        size_mb = file_path.stat().st_size / (1024 * 1024)

        return size_mb > min_size_mb

    except Exception:

        return False



def generate_anesthesiology_code(name: str, start_date: str) -> str:

    """

    Generate anesthesiology code from name and start date.

    Format: FirstInitial + LastInitial + YYMM

    Example: Maria Kobzeva, 2015-10-01 -> MK1510

    """

    if not name or not start_date:

        return ""



    # Parse name - take first and last word

    parts = name.strip().split()

    if len(parts) < 2:

        return ""



    first_initial = parts[0][0].upper()

    last_initial = parts[-1][0].upper()



    # Parse date - extract YY and MM

    # Date format: YYYY-MM-DD

    date_str = str(start_date)

    if len(date_str) >= 10:

        year = date_str[2:4]  # YY

        month = date_str[5:7]  # MM

        return f"{first_initial}{last_initial}{year}{month}"



    return ""
