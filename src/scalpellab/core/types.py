"""
Core Type Definitions for ScalpelLab
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional, List

class AssetStatus(Enum):
    COMPLETE = "Complete"
    INCOMPLETE = "Incomplete"
    MISSING = "Missing"

@dataclass(frozen=True)
class RecordingKey:
    """Unique identifier for a recording session."""
    recording_date: str  # YYYY-MM-DD
    case_no: int

@dataclass
class FileAsset:
    """Represents a physical file (SEQ or MP4) on disk."""
    recording_date: str
    case_no: int
    camera_name: str
    size_mb: Optional[int] = None
    status: AssetStatus = AssetStatus.MISSING
    duration_minutes: Optional[float] = None
    pre_black_segment: Optional[float] = None
    post_black_segment: Optional[float] = None

    @property
    def key(self) -> RecordingKey:
        return RecordingKey(self.recording_date, self.case_no)

@dataclass
class Recording:
    """Represents a unique surgical case session."""
    recording_date: str
    case_no: int
    signature_time: Optional[str] = None
    anesthesiology_key: Optional[int] = None
    months_anesthetic_recording: Optional[int] = None
    anesthetic_attending: Optional[str] = None  # 'A' or 'R'
    assets: List[FileAsset] = field(default_factory=list)

    @property
    def key(self) -> RecordingKey:
        return RecordingKey(self.recording_date, self.case_no)
