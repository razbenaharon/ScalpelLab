# Data Model: Manual Camera Synchronization

**Feature**: 1-multimpv-db-controls
**Model Version**: 1.0
**Last Updated**: 2026-01-12

---

## Overview

This document defines the data entities, relationships, and validation rules for the manual camera synchronization feature. The model follows a layered architecture:

- **Domain Layer**: Core entities (Camera, Case, SyncAdjustment)
- **Persistence Layer**: Database tables (mp4_status, recording_details)
- **Presentation Layer**: UI state (selection, display formatting)

---

## Domain Entities

### Entity: Camera

**Purpose**: Represents a single video camera/angle in a multi-camera surgical case recording.

**Lifecycle**: Created when videos are loaded, destroyed when application closes.

**Attributes**:

| Attribute | Type | Constraints | Description |
|-----------|------|-------------|-------------|
| `name` | `str` | NOT NULL, max 50 chars | Camera identifier (e.g., "Cart_Center", "Monitor") |
| `file_path` | `str` | NOT NULL, valid file path | Absolute path to video file |
| `case_id` | `tuple[str, int]` | NOT NULL | Foreign key: (recording_date, case_no) |
| `mpv_process` | `subprocess.Popen` | NOT NULL | Handle to MPV subprocess |
| `ipc_pipe_path` | `str` | NOT NULL, unique | Named pipe path for IPC (e.g., `\\.\pipe\mpv_socket_1_123456`) |
| `current_timestamp` | `float` | ≥ 0.0 | Current playback position in seconds |
| `is_paused` | `bool` | NOT NULL | Individual pause state (True = paused) |
| `offset_seconds` | `float` | -300.0 to +300.0 | Applied sync offset in seconds (±5 min max) |
| `offset_modified` | `bool` | NOT NULL | True if offset changed since last DB save |
| `sync_delta` | `float` | N/A (calculated) | Delta from reference camera (seconds) |
| `sync_status` | `str` | "synced" \| "out_of_sync" | Calculated sync status |
| `sync_status_text` | `str` | N/A | Display text: "✓ Synced" \| "+1.5s ahead" \| "-0.8s behind" |
| `is_selected` | `bool` | NOT NULL | Currently selected in sync panel (for nudge controls) |
| `is_reference` | `bool` | NOT NULL | Designated as reference camera (only one per session) |

**Relationships**:
- **Belongs to** one Case (via `case_id` foreign key)
- **Has one** MPV Process (one-to-one)
- **Has one** IPC Pipe (one-to-one)

**Business Rules**:

1. **Offset Range**: `-300.0 ≤ offset_seconds ≤ +300.0` (±5 minutes max)
   - *Rationale*: Prevents accidental massive offsets; surgical cases rarely need >5min sync adjustment
   - *Validation*: Enforced in nudge button handler before applying

2. **Current Timestamp**: `current_timestamp ≥ 0.0` (no negative timestamps)
   - *Rationale*: MPV never reports negative time-pos
   - *Validation*: Enforced by MPV; sanity-checked in polling loop

3. **Reference Camera Uniqueness**: Only one camera can have `is_reference = True` per session
   - *Rationale*: Sync deltas calculated relative to single reference
   - *Validation*: Enforced in `set_reference_camera()` method (clears other cameras' reference flags)

4. **Offset Modified Flag**: Resets to `False` after successful DB save OR when loading from DB
   - *Rationale*: Tracks whether "Save to DB" button should be enabled
   - *State Transitions*:
     - `False → True`: When user clicks nudge button
     - `True → False`: When "Save to DB" succeeds OR when `load_offset_from_db()` called

5. **Sync Status Classification**:
   ```python
   if abs(sync_delta) <= 0.3:  # ±300ms tolerance
       sync_status = "synced"
       sync_status_text = "✓ Synced"
   elif sync_delta > 0.3:
       sync_status = "out_of_sync"
       sync_status_text = f"+{sync_delta:.1f}s ahead"
   else:  # sync_delta < -0.3
       sync_status = "out_of_sync"
       sync_status_text = f"{sync_delta:.1f}s behind"
   ```
   - *Rationale*: ±0.3s tolerance per REQ-1.1 and success criteria
   - *Recalculation*: Every 100ms during timestamp polling

**State Diagram**:

```
offset_modified:
  ┌─────────┐  nudge button   ┌─────────┐
  │ False   │ ─────────────>  │ True    │
  │ (clean) │                 │ (dirty) │
  └─────────┘  <─────────────  └─────────┘
           save to DB OR load from DB

is_paused:
  ┌──────────┐  pause btn   ┌──────────┐
  │ False    │ ──────────>  │ True     │
  │ (playing)│              │ (paused) │
  └──────────┘  <──────────  └──────────┘
           play btn OR "Play All"

sync_status:
  ┌─────────┐  delta ≤ ±0.3s  ┌─────────────┐
  │ synced  │ ←──────────────→ │ out_of_sync │
  └─────────┘                  └─────────────┘
           (recalculated every 100ms)
```

**Python Dataclass Definition**:

```python
from dataclasses import dataclass, field
import subprocess

@dataclass
class Camera:
    # Identity
    name: str
    file_path: str
    case_id: tuple[str, int]  # (recording_date, case_no)

    # Playback State
    mpv_process: subprocess.Popen
    ipc_pipe_path: str
    current_timestamp: float = 0.0
    is_paused: bool = False

    # Sync State
    offset_seconds: float = 0.0
    offset_modified: bool = False
    sync_delta: float = 0.0
    sync_status: str = "synced"
    sync_status_text: str = "✓ Synced"

    # UI State
    is_selected: bool = False
    is_reference: bool = False

    def __post_init__(self):
        # Validate offset range
        if not (-300.0 <= self.offset_seconds <= 300.0):
            raise ValueError(f"Offset {self.offset_seconds}s outside allowed range (±300s)")

        # Validate timestamp
        if self.current_timestamp < 0:
            raise ValueError(f"Timestamp cannot be negative: {self.current_timestamp}")
```

---

### Entity: Case

**Purpose**: Represents a surgical case with metadata and associated camera recordings.

**Lifecycle**: Loaded from database when user selects case in browser; exists for session duration.

**Attributes**:

| Attribute | Type | Constraints | Description |
|-----------|------|-------------|-------------|
| `recording_date` | `str` | NOT NULL, ISO format `YYYY-MM-DD` | Date of case recording |
| `case_no` | `int` | NOT NULL, > 0 | Case number for that date |
| `room` | `str` | max 20 chars | Operating room identifier (e.g., "Room 8") |
| `anesthesiologist_name` | `str` | max 100 chars | Full name of attending anesthesiologist |
| `camera_count` | `int` | 1-9 | Number of available cameras for this case |
| `total_duration` | `float` | ≥ 0.0 | Longest camera duration in seconds |
| `cameras` | `List[CameraMetadata]` | 1-9 items | Available camera files |

**Relationships**:
- **Has many** CameraMetadata (one-to-many)
- **Referenced by** Camera entities via `case_id`

**Business Rules**:

1. **Primary Key**: `(recording_date, case_no)` forms unique identifier
   - *Rationale*: Matches database schema; supports multiple cases per day
   - *Validation*: Enforced at database level (PRIMARY KEY constraint)

2. **Camera Count Consistency**: `camera_count == len(cameras)`
   - *Rationale*: Redundant field for display purposes; must match actual count
   - *Validation*: Enforced when loading from database

3. **Maximum Cameras**: `1 ≤ camera_count ≤ 9`
   - *Rationale*: Per REQ-2.4, system supports 1-9 cameras
   - *Validation*: Enforced in camera selection dialog

4. **Valid Date Format**: `recording_date` matches regex `^\d{4}-\d{2}-\d{2}$`
   - *Rationale*: ISO format ensures sortability and clarity
   - *Validation*: Enforced by database schema (TEXT format)

**Python Dataclass Definition**:

```python
from dataclasses import dataclass
from typing import List

@dataclass
class Case:
    # Identity
    recording_date: str  # ISO format: "2023-05-15"
    case_no: int

    # Metadata
    room: str = ""
    anesthesiologist_name: str = ""
    camera_count: int = 0
    total_duration: float = 0.0

    # Relationships
    cameras: List['CameraMetadata'] = field(default_factory=list)

    def __post_init__(self):
        # Validate date format
        import re
        if not re.match(r'^\d{4}-\d{2}-\d{2}$', self.recording_date):
            raise ValueError(f"Invalid date format: {self.recording_date} (expected YYYY-MM-DD)")

        # Validate case number
        if self.case_no <= 0:
            raise ValueError(f"Case number must be positive: {self.case_no}")

        # Validate camera count consistency
        if self.camera_count != len(self.cameras):
            raise ValueError(f"Camera count mismatch: {self.camera_count} != {len(self.cameras)}")

        # Validate camera count range
        if not (1 <= self.camera_count <= 9):
            raise ValueError(f"Camera count {self.camera_count} outside allowed range (1-9)")
```

---

### Entity: CameraMetadata

**Purpose**: Metadata for a camera file associated with a case (loaded from database before video initialization).

**Lifecycle**: Loaded from `mp4_status` table when case is selected; used to initialize Camera entities.

**Attributes**:

| Attribute | Type | Constraints | Description |
|-----------|------|-------------|-------------|
| `camera_name` | `str` | NOT NULL, max 50 chars | Camera identifier (e.g., "Cart_Center") |
| `file_path` | `str` | NOT NULL, absolute path | Full path to video file |
| `duration` | `float` | ≥ 0.0 | Video duration in seconds |
| `file_size` | `int` | ≥ 0 | File size in bytes |
| `offset_seconds` | `float` | -300.0 to +300.0 | Saved sync offset from database (or 0.0) |
| `file_exists` | `bool` | NOT NULL | True if file path validation passed |

**Relationships**:
- **Belongs to** one Case
- **Used to initialize** Camera entity

**Business Rules**:

1. **File Format**: `file_path` must end with `.mp4`, `.mkv`, or `.avi`
   - *Rationale*: MPV supports these formats; other formats may fail
   - *Validation*: Checked in camera selection dialog

2. **File Existence**: `file_exists` calculated by `os.path.exists(file_path)`
   - *Rationale*: Prevents launching MPV with missing files
   - *Validation*: Performed when loading case from database
   - *Display*: Warning icon shown in camera selection dialog if False

3. **Offset Default**: If database `offset_seconds` is NULL, use `0.0`
   - *Rationale*: Graceful handling of missing column or new records
   - *Validation*: Enforced with SQL `COALESCE(offset_seconds, 0.0)`

**Python Dataclass Definition**:

```python
@dataclass
class CameraMetadata:
    camera_name: str
    file_path: str
    duration: float
    file_size: int
    offset_seconds: float = 0.0
    file_exists: bool = False

    def __post_init__(self):
        # Validate file format
        valid_extensions = ('.mp4', '.MP4', '.mkv', '.MKV', '.avi', '.AVI')
        if not self.file_path.endswith(valid_extensions):
            raise ValueError(f"Unsupported video format: {self.file_path}")

        # Check file existence
        import os
        self.file_exists = os.path.exists(self.file_path)

        # Validate offset range
        if not (-300.0 <= self.offset_seconds <= 300.0):
            raise ValueError(f"Offset {self.offset_seconds}s outside allowed range (±300s)")
```

---

### Entity: SyncAdjustment (In-Memory Only)

**Purpose**: Tracks pending sync offset changes before database commit. Used for confirmation dialog display.

**Lifecycle**: Created when user clicks nudge button; cleared after successful DB save or user cancels.

**Attributes**:

| Attribute | Type | Constraints | Description |
|-----------|------|-------------|-------------|
| `camera_name` | `str` | NOT NULL | Camera identifier |
| `case_id` | `tuple[str, int]` | NOT NULL | (recording_date, case_no) |
| `old_offset` | `float` | -300.0 to +300.0 | Original offset from database |
| `new_offset` | `float` | -300.0 to +300.0 | User-adjusted offset (current value) |
| `timestamp` | `datetime` | NOT NULL | When adjustment was made |

**Relationships**:
- **Associated with** one Camera (via camera_name)
- **Does NOT persist to database** until user confirms

**Business Rules**:

1. **Adjustment Tracking**: Only cameras with `offset_modified = True` have SyncAdjustment entries
   - *Rationale*: Confirmation dialog only shows modified cameras
   - *Validation*: Enforced when building confirmation message

2. **Cleared on Save**: All SyncAdjustment entries discarded after successful DB commit
   - *Rationale*: Adjustments become "committed" state; no longer "pending"
   - *Implementation*: List cleared after `conn.commit()`

3. **Cleared on Cancel**: All SyncAdjustment entries discarded if user cancels confirmation dialog
   - *Rationale*: User rejected changes; revert to database values
   - *Implementation*: Restore `camera.offset_seconds = adjustment.old_offset`

**Python Dataclass Definition**:

```python
from datetime import datetime

@dataclass
class SyncAdjustment:
    camera_name: str
    case_id: tuple[str, int]
    old_offset: float
    new_offset: float
    timestamp: datetime = field(default_factory=datetime.now)

    @property
    def delta(self) -> float:
        """Calculate offset change"""
        return self.new_offset - self.old_offset

    def __str__(self) -> str:
        """Format for display in confirmation dialog"""
        return f"{self.camera_name}: {self.new_offset:+.1f}s (was {self.old_offset:+.1f}s)"
```

---

## Persistence Layer (Database Schema)

### Table: mp4_status

**Purpose**: Stores video file metadata and sync offsets for each camera in each case.

**Schema**:

```sql
CREATE TABLE IF NOT EXISTS mp4_status (
    recording_date TEXT NOT NULL,
    case_no INTEGER NOT NULL,
    camera_name TEXT NOT NULL,
    path TEXT NOT NULL,
    file_size INTEGER,
    duration REAL,
    offset_seconds REAL DEFAULT 0.0,  -- SYNC OFFSET COLUMN
    PRIMARY KEY (recording_date, case_no, camera_name),
    FOREIGN KEY (recording_date, case_no) REFERENCES recording_details(recording_date, case_no)
);
```

**Indexes** (recommended for performance):

```sql
CREATE INDEX IF NOT EXISTS idx_mp4_status_case
ON mp4_status(recording_date, case_no);

CREATE INDEX IF NOT EXISTS idx_mp4_status_path
ON mp4_status(path);
```

**Queries**:

**Load Camera Metadata for Case**:
```sql
SELECT
    camera_name,
    path,
    duration,
    file_size,
    COALESCE(offset_seconds, 0.0) AS offset_seconds
FROM mp4_status
WHERE recording_date = ? AND case_no = ?
ORDER BY camera_name;
```

**Update Sync Offset**:
```sql
UPDATE mp4_status
SET offset_seconds = ?
WHERE recording_date = ? AND case_no = ? AND camera_name = ?;
```

**Migration** (if column missing):
```sql
ALTER TABLE mp4_status ADD COLUMN offset_seconds REAL DEFAULT 0.0;
```

---

### Table: recording_details

**Purpose**: Stores case-level metadata (date, case number, room, provider).

**Schema** (existing table, no changes required):

```sql
CREATE TABLE IF NOT EXISTS recording_details (
    recording_date TEXT NOT NULL,
    case_no INTEGER NOT NULL,
    room TEXT,
    anesthesiologist_key TEXT,
    PRIMARY KEY (recording_date, case_no)
);
```

**Queries**:

**Load Cases for Browser**:
```sql
SELECT
    rd.recording_date,
    rd.case_no,
    rd.room,
    a.first_name || ' ' || a.last_name AS anesthesiologist_name,
    COUNT(ms.camera_name) AS camera_count,
    MAX(ms.duration) AS total_duration
FROM recording_details rd
LEFT JOIN anesthesiology a ON rd.anesthesiologist_key = a.key
LEFT JOIN mp4_status ms ON rd.recording_date = ms.recording_date AND rd.case_no = ms.case_no
WHERE ms.path IS NOT NULL
GROUP BY rd.recording_date, rd.case_no
ORDER BY rd.recording_date DESC
LIMIT 20;
```

---

### Table: anesthesiology

**Purpose**: Lookup table for anesthesiologist names (existing table, no changes).

**Schema**:

```sql
CREATE TABLE IF NOT EXISTS anesthesiology (
    key TEXT PRIMARY KEY,
    first_name TEXT,
    last_name TEXT
);
```

---

## Validation Rules Summary

### Camera Entity Validations

| Field | Validation Rule | Error Message |
|-------|----------------|---------------|
| `offset_seconds` | `-300.0 ≤ value ≤ +300.0` | "Offset {value}s outside allowed range (±300s)" |
| `current_timestamp` | `value ≥ 0.0` | "Timestamp cannot be negative: {value}" |
| `is_reference` | Only one camera per session | *(Enforced programmatically, not user-facing error)* |
| `file_path` | `os.path.exists(path) == True` | "Video file not found: {path}" |

### Case Entity Validations

| Field | Validation Rule | Error Message |
|-------|----------------|---------------|
| `recording_date` | Matches `^\d{4}-\d{2}-\d{2}$` | "Invalid date format: {value} (expected YYYY-MM-DD)" |
| `case_no` | `value > 0` | "Case number must be positive: {value}" |
| `camera_count` | `1 ≤ value ≤ 9` | "Camera count {value} outside allowed range (1-9)" |
| `camera_count` | `value == len(cameras)` | "Camera count mismatch: {value} != {len(cameras)}" |

### Database Validations

| Field | Validation Rule | Enforcement |
|-------|----------------|-------------|
| `mp4_status.offset_seconds` | `-300.0 ≤ value ≤ +300.0` | Application-level (before UPDATE) |
| `mp4_status.path` | Must be absolute path | Application-level (file path validation) |
| `recording_details.recording_date` | Must be TEXT | Database-level (schema) |
| PRIMARY KEYS | Uniqueness constraints | Database-level (PRIMARY KEY constraint) |

---

## Entity Relationships Diagram

```
┌─────────────────────────────────────────────────────────────────┐
│                      PERSISTENCE LAYER                          │
│                                                                  │
│  ┌──────────────────┐         ┌────────────────────┐            │
│  │ recording_details│ 1     * │   mp4_status       │            │
│  ├──────────────────┤─────────├────────────────────┤            │
│  │ recording_date PK│         │ recording_date PK  │            │
│  │ case_no        PK│         │ case_no        PK  │            │
│  │ room             │         │ camera_name    PK  │            │
│  │ anesthesiologist │         │ path               │            │
│  └────────┬─────────┘         │ file_size          │            │
│           │                   │ duration           │            │
│           │                   │ offset_seconds ◄───╋── SYNC    │
│           │                   └────────────────────┘    STORAGE │
│           │                                                      │
│           │ FK: anesthesiologist_key                            │
│           │                                                      │
│           ▼                                                      │
│  ┌──────────────────┐                                          │
│  │ anesthesiology   │                                          │
│  ├──────────────────┤                                          │
│  │ key           PK │                                          │
│  │ first_name       │                                          │
│  │ last_name        │                                          │
│  └──────────────────┘                                          │
└─────────────────────────────────────────────────────────────────┘
                           │
                           │ Loads into
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│                       DOMAIN LAYER                              │
│                                                                  │
│  ┌──────────────────┐         ┌────────────────────┐            │
│  │ Case             │ 1     * │ CameraMetadata     │            │
│  ├──────────────────┤─────────├────────────────────┤            │
│  │ recording_date   │         │ camera_name        │            │
│  │ case_no          │         │ file_path          │            │
│  │ room             │         │ duration           │            │
│  │ anesthesiologist │         │ file_size          │            │
│  │ camera_count     │         │ offset_seconds     │            │
│  │ total_duration   │         │ file_exists        │            │
│  └──────────────────┘         └────────┬───────────┘            │
│                                        │                         │
│                                        │ Initializes             │
│                                        ▼                         │
│  ┌────────────────────────────────────────────────┐             │
│  │ Camera                                         │             │
│  ├────────────────────────────────────────────────┤             │
│  │ Identity: name, file_path, case_id             │             │
│  │ Playback: mpv_process, ipc_pipe_path,          │             │
│  │           current_timestamp, is_paused         │             │
│  │ Sync: offset_seconds, offset_modified,         │◄─── PRIMARY │
│  │       sync_delta, sync_status                  │     ENTITY  │
│  │ UI: is_selected, is_reference                  │             │
│  └────────────────┬───────────────────────────────┘             │
│                   │                                              │
│                   │ Tracked by (in-memory)                       │
│                   ▼                                              │
│  ┌────────────────────────────────┐                             │
│  │ SyncAdjustment                 │                             │
│  ├────────────────────────────────┤                             │
│  │ camera_name                    │                             │
│  │ case_id                        │                             │
│  │ old_offset                     │                             │
│  │ new_offset                     │                             │
│  │ timestamp                      │                             │
│  └────────────────────────────────┘                             │
│           (Used for confirmation dialog)                         │
└─────────────────────────────────────────────────────────────────┘
```

---

## Data Flow Diagrams

### Flow 1: Loading Case from Database

```
User selects case in DB browser
         │
         ▼
┌────────────────────────────┐
│ Query recording_details    │
│ + mp4_status (JOIN)        │
└────────┬───────────────────┘
         │
         ▼
┌────────────────────────────┐
│ Create Case entity         │
│ with List[CameraMetadata]  │
└────────┬───────────────────┘
         │
         ▼
User selects cameras to load
         │
         ▼
┌────────────────────────────┐
│ For each CameraMetadata:   │
│   1. Launch MPV process    │
│      with --start=+offset  │
│   2. Create Camera entity  │
│   3. Store in cameras list │
└────────┬───────────────────┘
         │
         ▼
┌────────────────────────────┐
│ Display Sync Panel with    │
│ all Camera entities        │
└────────────────────────────┘
```

### Flow 2: Manual Sync Adjustment

```
User selects camera in list
         │
         ▼
Camera.is_selected = True
         │
         ▼
User clicks "Nudge +0.1s"
         │
         ▼
┌────────────────────────────┐
│ camera.offset_seconds += 0.1│
│ camera.offset_modified = True│
└────────┬───────────────────┘
         │
         ▼
┌────────────────────────────┐
│ Send MPV IPC command:      │
│ "seek +0.1 relative+exact" │
└────────┬───────────────────┘
         │
         ▼
┌────────────────────────────┐
│ Recalculate sync_delta     │
│ Update sync_status         │
│ Refresh UI display         │
└────────┬───────────────────┘
         │
         ▼
Enable "Save to DB" button
```

### Flow 3: Saving Sync Offsets (WITH Confirmation)

```
User clicks "Save to DB"
         │
         ▼
┌────────────────────────────┐
│ Collect cameras where      │
│ offset_modified == True    │
└────────┬───────────────────┘
         │
         ▼
┌────────────────────────────┐
│ Build confirmation message:│
│ "Update mp4_status for N   │
│  cameras: [list of cameras]│
│  Do you want to proceed?"  │
└────────┬───────────────────┘
         │
         ▼
┌────────────────────────────┐
│ Show messagebox.askyesno() │
│ default="no" (ESC = Cancel)│
└────────┬───────────────────┘
         │
         ├─ User clicks "No"  ──> Return (no DB changes)
         │                         CRITICAL: Offsets preserved
         │                         in memory but not saved
         │
         └─ User clicks "Yes"
                   │
                   ▼
         ┌────────────────────────────┐
         │ For each modified camera:  │
         │   UPDATE mp4_status        │
         │   SET offset_seconds = ?   │
         │   WHERE recording_date = ? │
         │     AND case_no = ?        │
         │     AND camera_name = ?    │
         └────────┬───────────────────┘
                  │
                  ▼
         ┌────────────────────────────┐
         │ conn.commit()              │
         └────────┬───────────────────┘
                  │
                  ▼
         ┌────────────────────────────┐
         │ For each camera:           │
         │   camera.offset_modified   │
         │     = False                │
         └────────┬───────────────────┘
                  │
                  ▼
         Disable "Save to DB" button
                  │
                  ▼
         Show success message
```

---

## Summary

**Total Entities**: 4 (Camera, Case, CameraMetadata, SyncAdjustment)

**Persistence Tables**: 3 (mp4_status + offset_seconds column, recording_details, anesthesiology)

**Primary Entity**: Camera (tracks playback state, sync offsets, and UI state)

**Key Relationships**:
- Case → CameraMetadata (1:many)
- CameraMetadata → Camera (initializes)
- Camera → SyncAdjustment (in-memory tracking)
- mp4_status → Camera (loads offset_seconds)

**Critical Validation**:
- Offset range: ±300 seconds (±5 minutes)
- Camera count: 1-9 per case
- Sync tolerance: ±0.3 seconds for "synced" status
- Reference camera uniqueness: One per session

**Next Steps**:
- Implement dataclasses in `models.py`
- Create database migration script for offset_seconds column
- Define IPC contracts for MPV commands/queries
