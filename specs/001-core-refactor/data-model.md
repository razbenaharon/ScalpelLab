# Data Model: Core Architecture Refactor

**Branch**: `001-core-refactor`
**Spec**: [specs/001-core-refactor/spec.md](specs/001-core-refactor/spec.md)

## Entities

### Recording
Represents a unique surgical case session.
- **Key**: (`recording_date` + `case_no`)
- **Fields**:
    - `recording_date` (str): YYYY-MM-DD
    - `case_no` (int): Case identifier
    - `signature_time` (str): Timestamp
    - `anesthesiology_key` (int): FK to Resident
    - `months_anesthetic_recording` (int): Calculated experience
    - `anesthetic_attending` (char): 'A' or 'R'

### FileAsset (SEQ/MP4)
Represents a physical video file on disk.
- **Key**: (`recording_date` + `case_no` + `camera_name`)
- **Fields**:
    - `camera_name` (str): Enum [Cart_Center_2, Monitor, etc.]
    - `size_mb` (int): File size in MB
    - `status` (enum): Complete (>=200MB), Incomplete (<200MB), Missing
    - `duration_minutes` (float, nullable): Video length (MP4 only)
    - `pre_black_segment` (float, nullable): Redaction metadata
    - `post_black_segment` (float, nullable): Redaction metadata

### Camera
- **Fields**:
    - `name` (str): Identifier
    - `is_monitor` (bool): True if camera records a screen

## Database Schema (Existing Preserved)

The SQLite schema remains unchanged to ensure backward compatibility.

```sql
CREATE TABLE IF NOT EXISTS "mp4_status" (
    recording_date TEXT NOT NULL,
    case_no INTEGER NOT NULL,
    camera_name TEXT NOT NULL,
    size_mb INTEGER,
    duration_minutes REAL,
    pre_black_segment REAL,
    post_black_segment REAL,
    PRIMARY KEY (recording_date, case_no, camera_name)
);

CREATE TABLE IF NOT EXISTS "seq_status" (
    recording_date TEXT NOT NULL,
    case_no INTEGER NOT NULL,
    camera_name TEXT NOT NULL,
    size_mb INTEGER,
    PRIMARY KEY (recording_date, case_no, camera_name)
);
```

## Data Flow

1.  **Scanner Service**: Reads Filesystem -> Calculates Status -> Upserts to DB.
2.  **App/CLI**: Reads DB -> Displays Status / Filters for Operations.
3.  **Redactor Service**: Reads Excel Config -> Updates Filesystem (Blackening) -> Updates DB (`pre/post_black_segment`).
