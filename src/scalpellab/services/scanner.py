"""
Scanner Service
Handles filesystem scanning and database synchronization.
"""

from pathlib import Path
from typing import Dict, List, Tuple, Optional
from scalpellab.core.config import settings
from scalpellab.core.utils import parse_recording_date_and_case, get_video_duration
from scalpellab.db.repository import Repository

class ScannerService:
    """Service for scanning filesystem and updating database."""

    def __init__(self, repo: Optional[Repository] = None):
        self.repo = repo or Repository()
        self.cameras = settings.DEFAULT_CAMERAS
        self.threshold_bytes = settings.THRESHOLD_MB * 1024 * 1024

    def scan_seq(self, seq_root: Optional[Path] = None) -> Dict[Tuple[str, int, str], int]:
        """Scan SEQ files and return a map of (date, case, cam) -> size_mb."""
        root = seq_root or settings.SEQ_ROOT
        updates = {}

        if not root.exists():
            return updates

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

                for cam in self.cameras:
                    cam_path = case_dir / cam
                    if not cam_path.is_dir():
                        continue
                    
                    max_size = 0
                    found = False
                    for p in cam_path.rglob("*.seq"):
                        if p.is_file():
                            found = True
                            sz = p.stat().st_size
                            if sz > max_size:
                                max_size = sz
                    
                    if found:
                        size_mb = int(max_size / (1024 * 1024))
                        updates[(recording_date, case_no, cam)] = size_mb
        
        return updates

    def scan_mp4(self, mp4_root: Optional[Path] = None, calculate_duration: bool = True) -> Dict[Tuple[str, int, str], Tuple[int, Optional[float]]]:
        """Scan MP4 files and return a map of (date, case, cam) -> (size_mb, duration)."""
        root = mp4_root or settings.MP4_ROOT
        updates = {}

        if not root.exists():
            return updates

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

                for cam in self.cameras:
                    cam_path = case_dir / cam
                    if not cam_path.is_dir():
                        continue
                    
                    max_size = 0
                    largest_file = None
                    found = False
                    for p in cam_path.rglob("*.mp4"):
                        if p.is_file():
                            found = True
                            sz = p.stat().st_size
                            if sz > max_size:
                                max_size = sz
                                largest_file = p
                    
                    if found:
                        size_mb = int(max_size / (1024 * 1024))
                        duration = None
                        if calculate_duration and largest_file:
                            duration = get_video_duration(largest_file)
                        updates[(recording_date, case_no, cam)] = (size_mb, duration)
        
        return updates

    def sync_to_db(self, seq_updates: Dict, mp4_updates: Dict):
        """Write scan results to database."""
        # SEQ
        for (date, case, cam), size in seq_updates.items():
            self.repo.insert_row("seq_status", {
                "recording_date": date,
                "case_no": case,
                "camera_name": cam,
                "size_mb": size
            })
        
        # MP4
        for (date, case, cam), (size, duration) in mp4_updates.items():
            self.repo.insert_row("mp4_status", {
                "recording_date": date,
                "case_no": case,
                "camera_name": cam,
                "size_mb": size,
                "duration_minutes": duration
            })
