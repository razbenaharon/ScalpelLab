# ScalpelLab

> **Medical Video Analysis System**
> A comprehensive Python-based platform for surgical video recording management, privacy-compliant redaction, computer vision analysis, and multi-camera synchronized playback.

## Quick Start

### Installation
1. **Install Python 3.7+** and required packages:
   ```bash
   pip install streamlit pandas PyMuPDF pillow
   ```

2. **Configure paths** in `config.py`:
   ```python
   SEQ_ROOT = r"F:\Room_8_Data\Sequence_Backup"  # SEQ files location
   MP4_ROOT = r"F:\Room_8_Data\Recordings"       # MP4 files location
   ```

3. **Verify setup**:
   ```bash
   python config.py  # Validates paths and shows configuration
   ```

4. **Launch the web interface**:
   ```bash
   python run_app.py
   # Opens at http://localhost:8501
   ```

### Expected Directory Structure
```
Sequence_Backup/                    Recordings/
└── DATA_YY-MM-DD/                 └── DATA_YY-MM-DD/
    └── CaseN/                         └── CaseN/
        └── CameraName/                    └── CameraName/
            └── *.seq                          └── *.mp4
```

---

## Database Schema

### Core Tables

#### `recording_details` - Recording Metadata
Primary recording information with auto-calculated anesthesiology experience.

| Column | Type | Description |
|--------|------|-------------|
| `recording_date` | TEXT | Recording date (YYYY-MM-DD) - **Primary Key** |
| `case_no` | INTEGER | Case number (1, 2, 3...) - **Primary Key** |
| `signature_time` | TEXT | When recording was signed/validated |
| `anesthesiology_key` | INTEGER | Foreign key to anesthesiology table |
| `months_anesthetic_recording` | INTEGER | Experience months at recording (auto-calculated) |
| `anesthetic_attending` | TEXT | Level: 'A' (Attending) or 'R' (Resident) (auto-calculated) |

**Triggers**: Automatically calculates experience months and attending status on insert/update.

#### `anesthesiology` - Resident Information
Tracks anesthesiology residents and career progression.

| Column | Type | Description |
|--------|------|-------------|
| `anesthesiology_key` | INTEGER | Primary key (auto-increment) |
| `name` | TEXT | Full name |
| `code` | TEXT | Short identifier code |
| `anesthesiology_start_date` | TEXT | Training start date |
| `grade_a_date` | TEXT | Promotion to Attending date |

#### `mp4_status` - MP4 File Tracking
Tracks exported MP4 video files with size, duration, and black segment information.

| Column | Type | Description |
|--------|------|-------------|
| `recording_date` | TEXT | Recording date - **Primary Key** |
| `case_no` | INTEGER | Case number - **Primary Key** |
| `camera_name` | TEXT | Camera identifier - **Primary Key** |
| `size_mb` | INTEGER | Largest file size in MB |
| `duration_minutes` | REAL | Video duration in minutes |
| `pre_black_segment` | REAL | Black time before case (minutes) |
| `post_black_segment` | REAL | Black time after case (minutes) |

**Status Logic**: `size_mb >= 200` = Complete, `< 200` = Incomplete, `NULL` = Missing

**Black Segments**: Automatically calculated during batch redaction to track non-case time periods.

#### `seq_status` - SEQ File Tracking
Tracks original SEQ sequence files.

| Column | Type | Description |
|--------|------|-------------|
| `recording_date` | TEXT | Recording date - **Primary Key** |
| `case_no` | INTEGER | Case number - **Primary Key** |
| `camera_name` | TEXT | Camera identifier - **Primary Key** |
| `size_mb` | INTEGER | Largest file size in MB |

**Status Logic**: `size_mb >= 200` = Complete, `< 200` = Incomplete, `NULL` = Missing

#### `analysis_information` - Analysis Metadata
Stores labeling and analysis information per case.

| Column | Type | Description |
|--------|------|-------------|
| `recording_date` | TEXT | Recording date - **Primary Key** |
| `case_no` | INTEGER | Case number - **Primary Key** |
| `label_by` | TEXT | Who performed the labeling |

### Database Views

**`cur_mp4_missing`**: Lists cases where SEQ exists but MP4 is missing - useful for identifying videos needing export.

**`cur_seq_missing`**: Lists cases where MP4 exists but SEQ is missing - indicates potential data loss.

**`cur_seniority`**: Calculates current experience and status for all residents. Experience >60 months = Attending ('A'), otherwise Resident ('R').

### Camera Configuration
8 camera sources tracked: `Cart_Center_2`, `Cart_LT_4`, `Cart_RT_1`, `General_3`, `Monitor`, `Patient_Monitor`, `Ventilator_Monitor`, `Injection_Port`

---

## Scripts

### Video Conversion

**`batch_convert.py`** - GPU-Accelerated Batch Video Conversion
```bash
python scripts/3_seq_to_mp4_convert.py
```
Exports SEQ files to MP4 using GPU (NVIDIA NVENC) with CLExport fallback. Features:
- Interactive file selection (all, first N, or specific files)
- Real-time progress monitoring with file size tracking
- Automatic stuck conversion detection and kill
- Dual-mode: FFmpeg GPU primary, CLExport fallback
- Smart output path resolution preserving directory structure

### Database Management

**`update_status.py`** - File Status Scanner
```bash
python scripts/2_4_update_db.py                    # Full update with duration
python scripts/2_4_update_db.py --skip-duration    # Fast update, no duration
python scripts/2_4_update_db.py --dry-run          # Preview changes
```
Scans file directories and updates database with current file status. Features:
- Scans both SEQ and MP4 directories
- Calculates file sizes and video durations (using ffprobe)
- Smart mode: only recalculates duration for new/changed files
- Auto-deletes small MP4 files (<10MB by default)
- Shows detailed diff before applying changes

**`sql_to_path.py`** - Database Query to File Paths

```python
from scripts.helpers.sql_to_path import get_paths

# Query database and get actual file paths
paths = get_paths("SELECT * FROM mp4_status WHERE size_mb >= 200 AND camera_name='Monitor'")
```
Converts database queries into actual filesystem paths. Useful for:
- Batch operations on specific recordings
- Exporting file lists for external tools
- Finding specific recordings by criteria

### Video Editing

**`batch_redact.py`** - GPU-Accelerated Batch Video Redaction
```bash
python scripts/5_batch_blacken.py
```
Processes multiple videos based on Excel file with case time ranges. Features:
- **GPU-accelerated parallel processing** (NVIDIA NVENC with CPU fallback)
- **Smart tracking system** - Automatically skips already processed files
- **Real-time database updates** - Updates `mp4_status` table after each video completes
- **Interactive file selection** - Process all, first N, or specific files by range
- **Case-based redaction**:
  - During case times: Small corner box (1/3 width × 1/2 height, bottom-right)
  - Between/outside cases: Full screen black
- **Auto-trimming** - Removes footage > 1 hour after last case
- **Black segment calculation** - Automatically calculates and stores pre/post black times:
  - **First case pre**: Time from 00:00:00 to case start
  - **Between cases**: Gap time split equally (e.g., 30min gap = 15min post + 15min pre)
  - **Last case post**: Time from case end to min(video end, case end + 1 hour)
- **Comprehensive reporting** - Detailed summary with timing, statistics, and case breakdowns
- **Resume capability** - Interrupted batches can continue from where they left off

**Excel Format**: Columns for video path, `start time - case N`, `end time - case N`

**`cut_video.py`** - Video Segment Extractor
```bash
# Interactive mode
python scripts/cut_video.py

# Command-line mode (single video)
python scripts/cut_video.py video.mp4 10 30

# Batch mode (multiple videos, same time range)
python scripts/cut_video.py video1.mp4 video2.mp4 video3.mp4 00:01:00 00:02:00
```
Extracts video segments using FFmpeg stream copy (fast, no re-encoding). Supports:
- Time formats: HH:MM:SS or seconds (e.g., "90" or "00:01:30")
- Batch processing with progress tracking
- Auto-incrementing output filenames to avoid overwrites

**`copy_with_structure.py`** - Structured File Copier
```bash
python scripts/copy_with_structure.py -d D:\backup file1.mp4 file2.mp4
```
Copies files while preserving directory structure from "Recordings" folder onward.

### Multi-Video Playback

**`multiMPV/multiMPV.py`** - Synchronized Multi-Camera Viewer
```bash
python multiMPV/multiMPV.py
# or double-click multiMPV.exe (if compiled)
```
Displays up to 9 videos in synchronized grid layout using MPV player. Features:
- Interactive file picker with folder memory
- Supports .mmpv and .txt playlist files
- Grid layouts: 1x1, 2x1, 3x1, 2x2, 3x2, 3x3
- Synchronized playback with shared controls

### Utilities

**`sqlite_to_dbdiagram.py`** - Database Diagram Generator
Exports database schema to dbdiagram.io format for visualization.

**`compare_databases.py`** - Database Diff Tool
Compares two database instances to find differences.

---

## Streamlit Web Interface

Launch with `python run_app.py` to access the web dashboard at `http://localhost:8501`.

### Pages

**Database** (`1_Database.py`)
- Browse all tables with sorting and filtering
- Insert new records with validation
- Delete records with confirmation
- View record counts and statistics

**Status Summary** (`2_Status_Summary.py`)
- MP4/SEQ file statistics by camera
- Distribution charts and visualizations
- Missing file reports
- File size summaries

**Views** (`3_Views.py`)
- Query predefined database views
- Export results to CSV
- Custom SQL query interface

---

## Project Structure

```
ScalpelLab/
├── app/
│   ├── app.py                      # Main Streamlit app with ERD display
│   ├── utils.py                    # Database utility functions
│   └── pages/
│       ├── 1_Database.py           # Table browser and editor
│       ├── 2_Status_Summary.py     # Statistics dashboard
│       └── 3_Views.py              # Database views interface
├── scripts/
│   ├── batch_redact.py             # GPU batch video redaction (all-in-one)
│   ├── batch_convert.py            # GPU video converter
│   ├── update_status.py            # File status scanner
│   ├── cut_video.py                # Video segment extractor
│   ├── copy_with_structure.py      # Structured file copier
│   ├── sqlite_to_dbdiagram.py      # Schema diagram generator
│   ├── compare_databases.py        # Database diff tool
│   └── helpers/
│       ├── __init__.py             # Package initializer
│       ├── handle_xlsx.py          # Excel file processor
│       └── sql_to_path.py          # Query to file path resolver
├── multiMPV/
│   └── multiMPV.py                 # Multi-camera video player
├── docs/
│   ├── ERD.pdf                     # Entity relationship diagram
│   ├── scalpel_dbdiagram.txt       # Database schema definition
│   └── redaction_tracking.json     # Batch redaction tracking file
├── config.py                       # Path configuration (EDIT THIS)
├── run_app.py                      # Streamlit launcher
├── main.py                         # sql_to_path usage examples
└── ScalpelDatabase.sqlite          # SQLite database file
```

---

## Common Workflows

### Adding New Recordings
1. Place SEQ files in `Sequence_Backup/DATA_YY-MM-DD/CaseN/CameraName/`
2. Run `python scripts/update_status.py` to scan and update database
3. Insert recording metadata via web interface or SQL

### Exporting Videos
1. Run `python scripts/batch_convert.py`
2. Select files to export (all, range, or specific)
3. Monitor GPU conversion progress
4. Run `python scripts/update_status.py` to update MP4 status

### Reviewing Multi-Camera Recordings
1. Query database to find recordings: `python main.py` (see examples)
2. Copy file paths to text file or use multiMPV file picker
3. Run `python multiMPV/multiMPV.py` and select videos
4. Use synchronized playback to review all camera angles

### Batch Video Redaction
1. Create Excel file with columns: `path`, `start time - case 1`, `end time - case 1`, etc.
2. Configure paths in `scripts/batch_redact.py` CONFIG section (or use command-line args)
3. Run `python scripts/batch_redact.py`
4. Select files to process (all, first N, or specific ranges)
5. Monitor parallel GPU processing - database updates happen after each video
6. Review summary report with timing statistics and black segment analysis
7. **Automatic tracking** - Next run will skip already processed videos
8. Check `mp4_status` table for updated `pre_black_segment` and `post_black_segment` values

### Extracting Video Segments
1. Identify target recordings via database query
2. Run `python scripts/cut_video.py` (interactive or batch mode)
3. Specify time range (same for all videos in batch)
4. Cut videos are saved in same directory with "_cut" suffix

---

## Requirements

- **Python**: 3.7 or higher
- **Required packages**:
  - Core: `streamlit`, `pandas`, `PyMuPDF`, `pillow`
  - Video processing: `openpyxl` (for Excel file handling)
  - Install all: `pip install streamlit pandas PyMuPDF pillow openpyxl`
- **Optional tools**:
  - FFmpeg with NVENC support (for GPU video export and redaction)
  - CLExport (NorPix) (fallback converter)
  - MPV player (for multi-camera playback)
  - ffprobe (for video duration calculation)

---

## Notes

- Database triggers automatically calculate anesthesiology experience and attending status
- File sizes are stored in MB (largest file per camera/case)
- 200MB threshold distinguishes complete vs incomplete recordings
- Smart update mode prevents redundant duration calculations
- All paths are configurable via `config.py` for easy deployment
- **Batch redaction**:
  - Processes videos in parallel using GPU acceleration (up to 8 concurrent workers)
  - Automatically calculates and stores black segment times in database
  - Tracking system prevents re-processing and enables resume capability
  - Database updates occur immediately after each video (not after entire batch)
