# ScalpelLab Architecture Documentation

## Table of Contents
1. [System Overview](#system-overview)
2. [Architecture Components](#architecture-components)
3. [Data Flow](#data-flow)
4. [Database Schema](#database-schema)
5. [Module Descriptions](#module-descriptions)
6. [Technology Stack](#technology-stack)
7. [Integration Points](#integration-points)
8. [Deployment Architecture](#deployment-architecture)

---

## System Overview

**ScalpelLab** is a comprehensive medical video analysis and database management system designed for surgical recording workflows. The system handles the complete lifecycle of multi-camera surgical recordings, from raw sequence files to processed, redacted videos with pose estimation analytics.

### Primary Use Cases

1. **Video Processing Pipeline**: Convert SEQ files to MP4, apply redaction, track file status
2. **Database Management**: Track recordings, residents, file locations, and processing status
3. **Pose Estimation**: YOLO-based anesthesiologist pose detection and tracking
4. **Multi-Camera Playback**: Synchronized viewing of up to 9 camera angles
5. **Web Interface**: Browser-based management and statistics dashboard

### System Boundaries

- **Input**: SEQ sequence files from 8 camera sources
- **Processing**: Video conversion, redaction, pose estimation
- **Storage**: SQLite database, MP4 files, Parquet pose data
- **Output**: Redacted videos, pose tracking data, reports
- **Interface**: Streamlit web app, command-line scripts

---

## Architecture Components

### High-Level Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────┐
│                        USER INTERFACES                          │
├─────────────────┬───────────────────────┬──────────────────────┤
│  Streamlit Web  │   CLI Scripts         │   MultiMPV Player    │
│  (Port 8501)    │   (Batch Processing)  │   (Video Review)     │
└────────┬────────┴───────────┬───────────┴──────────┬───────────┘
         │                    │                      │
         v                    v                      v
┌─────────────────────────────────────────────────────────────────┐
│                    BUSINESS LOGIC LAYER                         │
├──────────────┬──────────────┬──────────────┬───────────────────┤
│   Database   │    Video     │    YOLO      │   File System     │
│   Manager    │  Processor   │   Pipeline   │   Scanner         │
└──────┬───────┴──────┬───────┴──────┬───────┴───────┬───────────┘
       │              │              │               │
       v              v              v               v
┌─────────────────────────────────────────────────────────────────┐
│                      DATA LAYER                                 │
├──────────────┬──────────────┬──────────────┬───────────────────┤
│  SQLite DB   │  MP4 Files   │  Parquet     │   SEQ Files       │
│  (Metadata)  │  (Videos)    │  (Keypoints) │   (Raw Data)      │
└──────────────┴──────────────┴──────────────┴───────────────────┘
```

### Component Breakdown

#### 1. **Web Application Layer** (`app/`)
- **Framework**: Streamlit (Python)
- **Components**:
  - `app.py`: Main entry point with ERD visualization
  - `utils.py`: Database connection utilities
  - `pages/1_Database.py`: Table browser and editor
  - `pages/2_Status_Summary.py`: Statistics dashboard
  - `pages/3_Views.py`: Database view interface

#### 2. **Processing Scripts Layer** (`scripts/`)
- **Purpose**: Batch operations and file management
- **Key Scripts**:
  - `1_nuk_seq_export.py`: SEQ file export workflow
  - `2_4_update_db.py`: Database synchronization with filesystem
  - `3_seq_to_mp4_convert.py`: GPU-accelerated video conversion
  - `5_batch_blacken.py`: Privacy redaction pipeline

#### 3. **Computer Vision Layer** (`yolo/`)
- **Framework**: YOLOv8 (Ultralytics) + BoT-SORT tracking
- **Components**:
  - `1_pose_anesthesiologist.py`: Multi-person pose detection
  - `2_inspect_parquet.py`: Keypoint data viewer
  - `3_process_tracks.py`: Track filtering and analysis
  - `calibrate.py`: Camera calibration utilities
  - `visualize_overlay.py`: Video overlay generation

#### 4. **Video Playback Layer** (`multiMPV/`)
- **Purpose**: Synchronized multi-camera review
- **Technology**: MPV media player (external)
- **Features**: Grid layouts (1x1 to 3x3), synchronized controls

#### 5. **Configuration Layer** (`config.py`)
- **Role**: Centralized path management
- **Configurable**:
  - Database location
  - SEQ file root directory
  - MP4 file root directory
  - Camera definitions

---

## Data Flow

### 1. Video Ingestion and Processing Flow

```
┌──────────────┐
│  SEQ Files   │ (Raw camera recordings)
│  F:\...\     │
│  Sequence_   │
│  Backup\     │
└──────┬───────┘
       │
       │ 1. File System Scan
       v
┌──────────────────┐
│ 2_4_update_db.py │ ──┐
│ (Status Scanner) │   │ 2. Update seq_status table
└──────────────────┘   │
                       v
                ┌─────────────┐
                │  SQLite DB  │
                │             │
                │ seq_status  │
                └─────┬───────┘
                      │
       ┌──────────────┴──────────────┐
       │                             │
       │ 3. User triggers conversion │
       v                             │
┌─────────────────────┐              │
│ 3_seq_to_mp4_       │              │
│ convert.py          │              │
│                     │              │
│ - GPU NVENC         │              │
│ - FFmpeg/CLExport   │              │
└─────────┬───────────┘              │
          │                          │
          │ 4. Convert to MP4        │
          v                          │
┌──────────────┐                     │
│  MP4 Files   │                     │
│  F:\...\     │                     │
│  Recordings\ │                     │
└──────┬───────┘                     │
       │                             │
       │ 5. Scan MP4s                │
       v                             │
┌──────────────────┐                 │
│ 2_4_update_db.py │ ────────────────┘
│ (Re-scan)        │ 6. Update mp4_status
└──────────────────┘
       │
       │ 7. Optional: Apply redaction
       v
┌─────────────────────┐
│ 5_batch_blacken.py  │
│                     │
│ - GPU parallel      │
│ - Case-based masks  │
│ - Black segments    │
└─────────┬───────────┘
          │
          │ 8. Generate redacted MP4s
          v
┌──────────────────┐
│  Redacted MP4s   │
│  (Privacy safe)  │
└──────────────────┘
```

### 2. YOLO Pose Estimation Flow

```
┌──────────────┐
│  MP4 Video   │
└──────┬───────┘
       │
       │ 1. Load video
       v
┌──────────────────────────┐
│ 1_pose_anesthesiologist  │
│                          │
│ - YOLOv8-Pose model      │
│ - BoT-SORT tracking      │
│ - Process every frame    │
│ - Save data at 30 FPS    │
└─────────┬────────────────┘
          │
          │ 2. Detect & Track
          v
┌──────────────────────────┐
│  Parquet File            │
│  (Keypoint Data)         │
│                          │
│  Columns:                │
│  - Frame_ID              │
│  - Track_ID              │
│  - Keypoint coords (x,y) │
│  - Confidence scores     │
└─────────┬────────────────┘
          │
          │ 3. Process tracks
          v
┌──────────────────────────┐
│ 3_process_tracks.py      │
│                          │
│ - Filter by confidence   │
│ - Distance calculations  │
│ - Track analysis         │
└─────────┬────────────────┘
          │
          │ 4. Visualize
          v
┌──────────────────────────┐
│ visualize_overlay.py     │
│                          │
│ - Draw skeleton overlay  │
│ - Show track IDs         │
│ - Generate debug video   │
└──────────────────────────┘
```

### 3. Database Update Flow

```
┌────────────────┐
│  File System   │
│  (SEQ/MP4)     │
└────────┬───────┘
         │
         │ 1. Scan directories
         v
┌─────────────────────────┐
│ 2_4_update_db.py        │
│                         │
│ - Walk directory tree   │
│ - Extract metadata      │
│ - Calculate file sizes  │
│ - Run ffprobe (optional)│
└────────┬────────────────┘
         │
         │ 2. Build change set
         v
┌─────────────────────────┐
│  Comparison Engine      │
│                         │
│  Current DB State       │
│       vs                │
│  Filesystem State       │
└────────┬────────────────┘
         │
         │ 3. Show diff
         v
┌─────────────────────────┐
│   User Confirmation     │
│   (--dry-run option)    │
└────────┬────────────────┘
         │
         │ 4. Apply updates
         v
┌─────────────────────────┐
│   SQLite Database       │
│                         │
│   - seq_status (INSERT/ │
│     UPDATE)             │
│   - mp4_status (INSERT/ │
│     UPDATE)             │
└─────────────────────────┘
```

---

## Database Schema

### Entity-Relationship Model

```
┌───────────────────────────┐
│   anesthesiology          │
│ ────────────────────────  │
│ PK anesthesiology_key     │
│    name                   │
│    code                   │
│    anesthesiology_start_  │
│    date                   │
│    grade_a_date           │
└─────────┬─────────────────┘
          │
          │ 1:N
          │
          v
┌───────────────────────────┐
│   recording_details       │
│ ────────────────────────  │
│ PK recording_date         │
│ PK case_no                │
│    signature_time         │
│ FK anesthesiology_key     │
│    months_anesthetic_     │
│    recording              │
│    anesthetic_attending   │
└─────────┬─────────────────┘
          │
          ├──────────┬──────────┬──────────┐
          │          │          │          │
          │ 1:N      │ 1:N      │ 1:N      │ 1:N
          v          v          v          v
┌──────────────┐ ┌─────────┐ ┌─────────┐ ┌──────────────┐
│ mp4_status   │ │seq_     │ │analysis_│ │ mp4_times    │
│              │ │status   │ │infor-   │ │              │
│ PK recording_│ │         │ │mation   │ │ PK recording_│
│    date      │ │PK record│ │         │ │    date      │
│ PK case_no   │ │   _date │ │PK record│ │ PK case_no   │
│ PK camera_   │ │PK case_ │ │   _date │ │    start_1   │
│    name      │ │   no    │ │PK case_ │ │    end_1     │
│    size_mb   │ │PK camera│ │   no    │ │    start_2   │
│    duration_ │ │   _name │ │   label_│ │    end_2     │
│    minutes   │ │   size_ │ │   by    │ │    start_3   │
│    pre_black_│ │   mb    │ └─────────┘ │    end_3     │
│    segment   │ └─────────┘              └──────────────┘
│    post_     │
│    black_    │
│    segment   │
│    path      │
└──────────────┘
```

### Core Tables

#### `anesthesiology`
**Purpose**: Resident roster with training progression
- Tracks residents from start date to attending promotion
- Stores unique codes (e.g., "MK1510" = Maria Kobzeva, Oct 2015)

#### `recording_details`
**Purpose**: Authoritative case registry
- One row per case (not per camera)
- Links to resident via `anesthesiology_key`
- Auto-calculated fields via triggers:
  - `months_anesthetic_recording`: Experience at time of case
  - `anesthetic_attending`: 'A' if >60 months, else 'R'

#### `mp4_status`
**Purpose**: Exported video file tracking
- One row per camera per case (1:N relationship)
- Tracks file size, duration, redaction segments
- `path` field stores full filesystem path
- Status inferred from `size_mb`:
  - `>= 200 MB`: Complete
  - `< 200 MB`: Incomplete
  - `NULL`: Missing

#### `seq_status`
**Purpose**: Source sequence file tracking
- Mirrors structure of `mp4_status`
- Tracks raw SEQ files before conversion

#### `mp4_times`
**Purpose**: Case time ranges for redaction
- Stores start/end times for up to 3 cases per recording
- Used by `5_batch_blacken.py` to determine masking regions

#### `analysis_information`
**Purpose**: Labeling metadata
- Tracks who labeled each case
- 1:1 relationship with `recording_details`

### Database Triggers

The database includes triggers for automatic field calculation:

```sql
-- Example: Auto-calculate experience months
CREATE TRIGGER calculate_months_anesthetic_recording
AFTER INSERT ON recording_details
BEGIN
  UPDATE recording_details
  SET months_anesthetic_recording =
    (SELECT (julianday(NEW.recording_date) -
             julianday(a.anesthesiology_start_date)) / 30
     FROM anesthesiology a
     WHERE a.anesthesiology_key = NEW.anesthesiology_key)
  WHERE recording_date = NEW.recording_date
    AND case_no = NEW.case_no;
END;
```

### Database Views

#### `cur_mp4_missing`
Lists cases where SEQ exists but MP4 is missing (conversion needed)

#### `cur_seq_missing`
Lists cases where MP4 exists but SEQ is missing (data loss warning)

#### `cur_seniority`
Calculates current resident experience levels and attending status

---

## Module Descriptions

### `config.py` - Configuration Management
**Role**: Single source of truth for all path configurations

**Key Functions**:
- `get_db_path()`: Returns database location
- `get_seq_root()`: Returns SEQ file directory
- `get_mp4_root()`: Returns MP4 file directory
- `validate_paths()`: Checks if paths exist
- `print_config()`: Displays current configuration

**Configuration Points**:
```python
DB_PATH = PROJECT_ROOT / "ScalpelDatabase.sqlite"
SEQ_ROOT = r"F:\Room_8_Data\Sequence_Backup"
MP4_ROOT = r"F:\Room_8_Data\Recordings"
DEFAULT_CAMERAS = ["Cart_Center_2", "Cart_LT_4", ...]
```

### `app/` - Streamlit Web Application

#### `app.py` - Main Application
- Streamlit entry point
- ERD diagram generation using Graphviz
- PyCharm-style table visualization
- Auto-loads schema on first visit

#### `utils.py` - Database Utilities
- `connect()`: Context manager for SQLite connections
- `list_tables()`: Get all table names
- `list_views()`: Get all view names
- `get_table_schema()`: PRAGMA table_info wrapper
- `load_table()`: Load entire table to DataFrame

#### `pages/1_Database.py` - Table Browser
- Interactive table viewer with sorting/filtering
- Insert records with validation
- Delete records with confirmation
- Auto-generates codes for anesthesiology table

#### `pages/2_Status_Summary.py` - Statistics Dashboard
- MP4/SEQ file status by camera
- Plotly visualizations (bar charts, pie charts)
- Missing file reports
- File size distributions

#### `pages/3_Views.py` - Database Views
- Query predefined views
- CSV export functionality
- Real-time data display

### `scripts/` - Processing Pipeline

#### `1_nuk_seq_export.py` - SEQ Export Workflow
- Coordinates SEQ file export
- Multi-threaded operations
- Progress tracking with psutil

#### `2_4_update_db.py` - Database Synchronization
- Scans SEQ and MP4 directories
- Compares filesystem vs database
- Calculates file sizes and durations (ffprobe)
- Supports `--dry-run` and `--skip-duration` modes

**Algorithm**:
1. Walk directory tree matching pattern `DATA_YY-MM-DD/CaseN/CameraName/*.{seq,mp4}`
2. Extract metadata (date, case, camera)
3. Query current database state
4. Build INSERT/UPDATE statements for changes
5. Display diff for user confirmation
6. Apply changes to database

#### `3_seq_to_mp4_convert.py` - Video Conversion
- GPU-accelerated conversion (NVIDIA NVENC)
- Fallback to CLExport if FFmpeg fails
- Interactive file selection (all, first N, range)
- Real-time progress monitoring
- Stuck conversion detection

**Conversion Pipeline**:
```
SEQ → FFmpeg (NVENC) → MP4
                ↓ (fallback)
             CLExport → MP4
```

#### `5_batch_blacken.py` - Privacy Redaction
- GPU-parallel processing (up to 8 workers)
- Case-based redaction masks:
  - **During case**: Small corner box (1/3 width × 1/2 height)
  - **Outside case**: Full screen black
- Auto-trim footage >1 hour after last case
- Database integration: updates `pre_black_segment` and `post_black_segment`
- Tracking system: JSON file prevents re-processing
- Comprehensive reporting with timing statistics

**Black Segment Calculation**:
- **Pre-segment (first case)**: Time from 00:00:00 to case start
- **Between cases**: Gap time split equally
- **Post-segment (last case)**: Time from case end to min(video end, case end + 1 hour)

### `yolo/` - Pose Estimation Pipeline

#### `1_pose_anesthesiologist.py` - Multi-Person Pose Detection
- **Model**: YOLOv8-Pose (ultralytics)
- **Tracker**: BoT-SORT (handles occlusions, re-identification)
- **Key Fix**: Processes EVERY frame for tracking consistency, saves data at target FPS
- **Output**: Parquet file with 17 COCO keypoints per person

**Keypoints** (17 total):
```
Nose, Left_Eye, Right_Eye, Left_Ear, Right_Ear,
Left_Shoulder, Right_Shoulder, Left_Elbow, Right_Elbow,
Left_Wrist, Right_Wrist, Left_Hip, Right_Hip,
Left_Knee, Right_Knee, Left_Ankle, Right_Ankle
```

**Data Format** (Parquet):
```
Frame_ID | Timestamp | Track_ID | Nose_x | Nose_y | Nose_conf | ... | Right_Ankle_conf
```

#### `2_inspect_parquet.py` - Data Inspector
- Loads and displays Parquet files
- Shows track statistics
- Frame-by-frame breakdown

#### `3_process_tracks.py` - Track Filtering
- Filters tracks by confidence threshold
- Calculates spatial distances using scipy
- Identifies primary subjects

#### `calibrate.py` - Camera Calibration
- Chessboard-based calibration
- Computes intrinsic/extrinsic parameters

#### `visualize_overlay.py` - Overlay Generation
- Draws skeletal overlays on video
- Shows track IDs and bounding boxes
- Generates debug videos

### `multiMPV/` - Multi-Camera Playback

#### `multiMPV.py` - Synchronized Video Player
- Launches MPV instances in grid layout
- Supports .mmpv and .txt playlist files
- Grid configurations: 1x1, 2x1, 3x1, 2x2, 3x2, 3x3
- Synchronized playback controls
- Remembers last folder location

**Workflow**:
1. User selects video files or playlist
2. Script calculates grid layout based on file count
3. Launches MPV processes with synchronized timing
4. User controls all videos with single MPV instance

---

## Technology Stack

### Core Technologies

| Component | Technology | Version | Purpose |
|-----------|-----------|---------|---------|
| **Web Framework** | Streamlit | >=1.49.0 | Interactive web UI |
| **Database** | SQLite | 3.x (built-in) | Metadata storage |
| **Data Processing** | pandas | >=2.0.0 | DataFrame operations |
| **Data Visualization** | Plotly | >=5.0.0 | Interactive charts |
| **Diagram Generation** | Graphviz | >=0.20.0 | ERD visualization |
| **Video I/O** | OpenCV (cv2) | >=4.8.0 | Video reading/writing |
| **Deep Learning** | PyTorch | >=2.0.0 | YOLO backend |
| **YOLO** | Ultralytics | >=8.0.0 | Pose estimation |
| **Scientific Computing** | scipy | >=1.10.0 | Distance calculations |
| **Excel Processing** | openpyxl | >=3.1.0 | Read/write .xlsx |
| **Parquet** | pyarrow | >=12.0.0 | Fast serialization |
| **Progress Bars** | tqdm | >=4.65.0 | CLI progress tracking |
| **System Monitoring** | psutil | >=5.9.0 | Process management |

### External Tools

| Tool | Purpose | Installation |
|------|---------|--------------|
| **FFmpeg** | Video conversion (NVENC GPU encoding) | https://ffmpeg.org/ |
| **ffprobe** | Video metadata extraction | (included with FFmpeg) |
| **CLExport** | NorPix SEQ converter (fallback) | Proprietary |
| **MPV** | Media player for multi-camera sync | https://mpv.io/ |
| **CUDA Toolkit** | GPU acceleration (NVIDIA only) | https://developer.nvidia.com/cuda-downloads |

### Hardware Requirements

**Minimum**:
- CPU: 4-core processor
- RAM: 8 GB
- Storage: 500 GB (for video files)
- GPU: Not required (CPU fallback available)

**Recommended**:
- CPU: 8-core processor
- RAM: 16 GB
- Storage: 2 TB SSD
- GPU: NVIDIA RTX series (NVENC support)

---

## Integration Points

### 1. Database ↔ File System
- **Scripts**: `2_4_update_db.py`
- **Mechanism**: Directory walking + metadata extraction
- **Sync**: One-way (filesystem → database)

### 2. Database ↔ Streamlit App
- **Module**: `app/utils.py`
- **Mechanism**: SQLite connection context managers
- **Operations**: SELECT, INSERT, DELETE

### 3. Python Scripts ↔ FFmpeg
- **Scripts**: `3_seq_to_mp4_convert.py`, `5_batch_blacken.py`
- **Mechanism**: `subprocess.run()` with pipe communication
- **Encoding**: NVENC GPU acceleration

### 4. Python ↔ YOLO Models
- **Module**: `yolo/1_pose_anesthesiologist.py`
- **Mechanism**: Ultralytics YOLO API
- **Model Loading**: Auto-download from Ultralytics hub

### 5. Python ↔ MPV Player
- **Module**: `multiMPV/multiMPV.py`
- **Mechanism**: Launch external MPV processes
- **Communication**: IPC via file paths

### 6. Excel ↔ Batch Redaction
- **Script**: `5_batch_blacken.py`
- **Library**: openpyxl (via pandas)
- **Data**: Case time ranges for masking

---

## Deployment Architecture

### Development Setup

```bash
# 1. Clone repository
git clone <repository-url>
cd ScalpelLab

# 2. Install Python dependencies
pip install -r requirements.txt

# 3. Install external tools
# - FFmpeg with NVENC support
# - MPV player

# 4. Configure paths
# Edit config.py:
#   SEQ_ROOT = r"F:\Room_8_Data\Sequence_Backup"
#   MP4_ROOT = r"F:\Room_8_Data\Recordings"

# 5. Verify setup
python config.py

# 6. Initialize database (if needed)
# Database auto-created on first run

# 7. Launch web interface
python run_app.py
# or: streamlit run app/app.py
```

### Production Considerations

#### 1. **Database Backup**
```bash
# Regular backups of ScalpelDatabase.sqlite
cp ScalpelDatabase.sqlite "backups/ScalpelDatabase_$(date +%Y%m%d).sqlite"
```

#### 2. **GPU Configuration**
- Ensure NVIDIA drivers are up to date
- Verify CUDA toolkit installation
- Test NVENC support: `ffmpeg -encoders | grep nvenc`

#### 3. **Storage Planning**
- **SEQ files**: ~500 MB per camera per case
- **MP4 files**: ~300 MB per camera per case (after conversion)
- **Redacted MP4s**: ~300 MB per camera per case
- **Parquet files**: ~10 MB per 30-minute video

**Example Calculation** (100 cases, 8 cameras):
- SEQ: 100 × 8 × 500 MB = 400 GB
- MP4: 100 × 8 × 300 MB = 240 GB
- Total: ~650 GB

#### 4. **Performance Tuning**
- **Video Conversion**: Use GPU (10x faster than CPU)
- **Database Updates**: Use `--skip-duration` for fast scans
- **YOLO Processing**: Reduce `imgsz` for faster inference
- **Batch Redaction**: Limit workers based on VRAM (8 workers for 12GB GPU)

#### 5. **Security**
- Database contains PHI (Protected Health Information)
- Implement access controls on file directories
- Secure Streamlit app with authentication (not included by default)
- Use HTTPS for production deployment

---

## Data Pipeline Summary

### Complete Workflow (SEQ → Redacted MP4 with Pose Data)

1. **Ingestion**: Place SEQ files in `Sequence_Backup/`
2. **Scan**: Run `python scripts/2_4_update_db.py` to update `seq_status`
3. **Convert**: Run `python scripts/3_seq_to_mp4_convert.py` to generate MP4s
4. **Re-scan**: Run `python scripts/2_4_update_db.py` again to update `mp4_status`
5. **Redact**: Run `python scripts/5_batch_blacken.py` with Excel time ranges
6. **Pose**: Run `python yolo/1_pose_anesthesiologist.py <video_path>` for pose data
7. **Review**: Use `python multiMPV/multiMPV.py` for multi-camera playback
8. **Analyze**: View statistics in Streamlit app at `http://localhost:8501`

---

## Appendix: File Naming Conventions

### SEQ Files
```
F:\Room_8_Data\Sequence_Backup\DATA_2023-05-15\Case3\Monitor\recording_001.seq
                                     │        │      │
                                     │        │      └─ Camera name
                                     │        └──────── Case number (1-based)
                                     └───────────────── Date (YYYY-MM-DD)
```

### MP4 Files
```
F:\Room_8_Data\Recordings\DATA_2023-05-15\Case3\Monitor\recording_001.mp4
```

### Parquet Files (YOLO Output)
```
F:\Room_8_Data\Recordings\DATA_2023-05-15\Case3\Monitor\recording_001_keypoints.parquet
```

### Redacted MP4 Files
```
C:\Users\user\Desktop\blacken\output\DATA_2023-05-15_Case3_Monitor_redacted.mp4
```

---

## Change Log

| Date | Version | Changes |
|------|---------|---------|
| 2026-01-06 | 1.0 | Initial architecture documentation |

---

## Contact & Support

For questions about the architecture or implementation details, refer to:
- **README.md**: User-facing documentation
- **Code comments**: Inline Google-style docstrings
- **Database schema**: `docs/scalpel_dbdiagram.txt`
