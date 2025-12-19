"""
Conversion Service
Handles SEQ to MP4 conversion using FFmpeg (GPU) and CLExport (fallback).
"""

import subprocess
import time
import threading
from pathlib import Path
from typing import Optional, Tuple, Dict
from scalpellab.core.config import settings
from scalpellab.core.utils import find_ffmpeg, is_valid_video_file

class ConversionService:
    """Service for converting SEQ files to MP4."""

    def __init__(self):
        self.ffmpeg_path = find_ffmpeg()
        self.clexport_path = self._find_clexport()

    def _find_clexport(self) -> Optional[str]:
        """Find CLExport.exe in common locations."""
        paths = [
            r"C:\Program Files\NorPix\BatchProcessor\CLExport.exe",
            r"C:\Program Files (x86)\NorPix\BatchProcessor\CLExport.exe",
            r"C:\NorPix\BatchProcessor\CLExport.exe",
        ]
        for path in paths:
            if os.path.exists(path):
                return path
        return None

    def get_output_path(self, seq_path: Path) -> Path:
        """Resolve output path for a given SEQ file."""
        parts = seq_path.parts
        anchor_idx = None
        for i, part in enumerate(parts):
            if part.upper().startswith("DATA_"):
                anchor_idx = i
                break

        if anchor_idx is not None:
            rel_from_data = Path(*parts[anchor_idx:])
            out_dir = settings.MP4_ROOT / rel_from_data.parent
        else:
            channel = seq_path.parent.name
            case = seq_path.parent.parent.name
            date = seq_path.parent.parent.parent.name
            if not date.upper().startswith("DATA_"):
                date = "DATA_Unknown"
            out_dir = settings.MP4_ROOT / date / case / channel

        out_dir.mkdir(parents=True, exist_ok=True)
        
        # Build base name
        base_name = seq_path.parent.name # Default to camera name
        out_path = out_dir / f"{base_name}.mp4"
        
        # Handle duplicates if necessary
        counter = 1
        while out_path.exists():
            out_path = out_dir / f"{base_name}_{counter}.mp4"
            counter += 1
            
        return out_path

    def export_ffmpeg_gpu(self, seq_path: Path, out_path: Path) -> Tuple[bool, str]:
        """Export using FFmpeg with NVIDIA GPU acceleration."""
        if not self.ffmpeg_path:
            return False, "FFmpeg not found"

        cmd = [
            self.ffmpeg_path,
            "-y",
            "-hwaccel", "cuda",
            "-r", "30",
            "-i", str(seq_path),
            "-c:v", "h264_nvenc",
            "-preset", "p6",
            "-rc", "vbr",
            "-cq", "28",
            "-b:v", "2M",
            "-maxrate", "3M",
            "-bufsize", "3M",
            "-profile:v", "high",
            "-pix_fmt", "yuv420p",
            str(out_path)
        ]

        try:
            process = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
            if process.returncode == 0 and is_valid_video_file(out_path):
                return True, "Success (GPU)"
            return False, f"FFmpeg failed: {process.stderr[-200:]}"
        except Exception as e:
            return False, str(e)

    def export_clexport(self, seq_path: Path, out_path: Path) -> Tuple[bool, str]:
        """Export using CLExport (NorPix)."""
        if not self.clexport_path:
            return False, "CLExport not found"

        out_dir = out_path.parent
        out_filename = out_path.stem

        cmd = [
            self.clexport_path,
            "-i", str(seq_path),
            "-o", str(out_dir),
            "-of", out_filename,
            "-f", "mp4"
        ]

        try:
            process = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
            if process.returncode == 0 and is_valid_video_file(out_path):
                return True, "Success (CLExport)"
            return False, f"CLExport failed: {process.stderr[-200:]}"
        except Exception as e:
            return False, str(e)
import os
