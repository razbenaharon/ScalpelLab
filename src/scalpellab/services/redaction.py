"""
Redaction Service
Handles video redaction based on time ranges from Excel.
"""

import os
import json
import subprocess
import pandas as pd
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple
from scalpellab.core.config import settings
from scalpellab.core.utils import find_ffmpeg, is_valid_video_file

class RedactionService:
    """Service for redacting videos."""

    def __init__(self, tracking_file: Optional[Path] = None):
        self.tracking_file = tracking_file or settings.PROJECT_ROOT / "docs" / "redaction_tracking.json"
        self.tracking_data = self._load_tracking()

    def _load_tracking(self) -> Dict:
        if self.tracking_file.exists():
            with open(self.tracking_file, 'r') as f:
                return json.load(f)
        return {"processed_files": {}}

    def save_tracking(self):
        self.tracking_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self.tracking_file, 'w') as f:
            json.dump(self.tracking_data, f, indent=2)

    def is_processed(self, file_path: Path) -> bool:
        return str(file_path.absolute()) in self.tracking_data["processed_files"]

    def mark_processed(self, input_path: Path, output_path: Path):
        self.tracking_data["processed_files"][str(input_path.absolute())] = {
            "output": str(output_path.absolute()),
            "timestamp": pd.Timestamp.now().isoformat()
        }
        self.save_tracking()

    def calculate_black_segments(self, case_ranges: List[Dict], duration_sec: float) -> Dict[int, Dict[str, float]]:
        """Calculate pre/post black segments in minutes."""
        if not case_ranges:
            return {}
        
        sorted_ranges = sorted(case_ranges, key=lambda x: x['start'])
        result = {}

        for i, r in enumerate(sorted_ranges):
            case_no = r['case']
            start = r['start']
            end = r['end']

            # Pre
            if i == 0:
                pre = start
            else:
                pre = (start - sorted_ranges[i-1]['end']) / 2.0
            
            # Post
            if i == len(sorted_ranges) - 1:
                post = min(duration_sec, end + 3600) - end
            else:
                post = (sorted_ranges[i+1]['start'] - end) / 2.0
            
            result[case_no] = {
                "pre": pre / 60.0,
                "post": post / 60.0
            }
        
        return result

    def redact_video(self, input_path: Path, output_path: Path, case_ranges: List[Dict], bitrate: int = 2000000) -> Tuple[bool, str]:
        """Apply redaction filters using FFmpeg GPU."""
        ffmpeg = find_ffmpeg()
        if not ffmpeg:
            return False, "FFmpeg not found"

        # Build filter string
        case_cond = "+".join([f"between(t,{r['start']},{r['end']})" for r in case_ranges])
        filter_str = (
            f"drawbox=x=0:y=0:w=iw:h=ih:color=black:t=fill:enable='not({case_cond})',"
            f"drawbox=x=2*iw/3:y=ih/2:w=iw/3:h=ih/2:color=black:t=fill:enable='{case_cond}'"
        )

        # Trim logic (1 hour after last case)
        last_end = max([r['end'] for r in case_ranges])
        duration = last_end + 3600 # Simplified trim

        cmd = [
            ffmpeg, "-y", "-hwaccel", "cuda",
            "-i", str(input_path),
            "-t", str(duration),
            "-vf", filter_str,
            "-c:v", "h264_nvenc",
            "-preset", "p1",
            "-b:v", str(bitrate),
            "-c:a", "copy",
            str(output_path)
        ]

        try:
            subprocess.run(cmd, check=True, capture_output=True)
            if is_valid_video_file(output_path):
                self.mark_processed(input_path, output_path)
                return True, "Success"
            return False, "Output file invalid"
        except Exception as e:
            return False, str(e)
