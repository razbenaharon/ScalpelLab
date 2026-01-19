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

### Main Pipeline Scripts

#### `1_nuk_seq_export.py` - Batch SEQ File Export and Organization
```bash
python scripts/1_nuk_seq_export.py
```
Automates the organization and export of raw SEQ video files from source to structured destination. Features:
- **Multi-threaded file copying** (configurable workers, default 8)
- **Automatic date/case grouping** (30-minute time windows)
- **Camera channel mapping** (auto-detects and maps to standard names)
- **SHA256 hash verification** for copy integrity
- **Atomic copy operations** with retry logic
- **Orphaned companion file detection** (.metadata, .idx, .xml, .aud)
- **Disk space validation** before copying
- **JUNK suffix** for undersized files (<200MB)

#### `2_4_update_db.py` - Combined Database Status Updater
```bash
python scripts/2_4_update_db.py                    # Full update with duration
python scripts/2_4_update_db.py --skip-duration    # Fast update, no duration
python scripts/2_4_update_db.py --dry-run          # Preview changes
python scripts/2_4_update_db.py --skip-seq         # Skip SEQ status update
python scripts/2_4_update_db.py --skip-mp4         # Skip MP4 status update
```
Scans file directories and updates both `seq_status` and `mp4_status` tables. Features:
- **Future-proof design** - Only manages specific columns, preserves all others
- **Smart mode** - Only recalculates duration for new/changed files
- **Auto-deletes** small MP4 files (<10MB by default)
- **Detailed diff** before applying changes
- **Single confirmation** for both updates

#### `3_seq_to_mp4_convert.py` - GPU-Accelerated Batch Video Conversion
```bash
python scripts/3_seq_to_mp4_convert.py
```
Exports SEQ files to MP4 using GPU (NVIDIA NVENC) with CLExport fallback. Features:
- **Interactive file selection** (all, first N, or specific files)
- **Real-time progress monitoring** with file size tracking
- **Automatic stuck conversion detection** and kill
- **Dual-mode**: FFmpeg GPU primary, CLExport fallback
- **Smart output path resolution** preserving directory structure
- **Database-driven** - Queries missing MP4s from database

#### `5_batch_blacken.py` - GPU-Accelerated Batch Video Redaction
```bash
python scripts/5_batch_blacken.py
python scripts/5_batch_blacken.py D:\Output 8      # Custom output dir, 8 workers
```
Processes multiple videos based on database `mp4_times` table with case time ranges. Features:
- **GPU-accelerated parallel processing** (NVIDIA NVENC with CPU fallback)
- **Smart tracking system** - Automatically skips already processed files
- **Real-time database updates** - Updates `mp4_status` table after each video
- **Interactive file selection** - Process all, first N, or specific files by range
- **Case-based redaction**:
  - During case times: Small corner box (1/3 width × 1/2 height, bottom-right)
  - Between/outside cases: Full screen black
- **Auto-trimming** - Removes footage > 1 hour after last case
- **Black segment calculation** - Stores pre/post black times in database
- **Resume capability** - Interrupted batches continue from where they left off

---

### Helper Scripts (`scripts/helpers/`)

#### `compare_databases.py` - Database Diff Tool
```bash
python scripts/helpers/compare_databases.py
```
Interactive tool to compare two SQLite databases. Features:
- **Summary statistics** for SEQ and MP4 files
- **Table-by-table comparison** with primary key detection
- **Detailed change reports** showing new, changed, and missing records
- **Column-aware** comparison with proper formatting

#### `compare_mp4.py` - MP4 File Backup Checker
```bash
python scripts/helpers/compare_mp4.py                           # Use default paths
python scripts/helpers/compare_mp4.py <source> <destination>    # Custom paths
```
Compares MP4 files between source and backup directories by filename and size. Generates missing file report.

#### `compare_seq.py` - SEQ File Backup Checker
```bash
python scripts/helpers/compare_seq.py
```
Compares SEQ files between directories. Shows missing files with sizes.

#### `copy_files.py` - Simple File Copier
```bash
python scripts/helpers/copy_files.py file1.mp4 file2.mp4 /destination/dir
```
Copies a list of files to a destination directory. Creates directory if needed.

#### `copy_with_structure.py` - Structured File Copier
```bash
python scripts/helpers/copy_with_structure.py -d D:\backup file1.mp4 file2.mp4
```
Copies files while preserving directory structure from "Recordings" folder onward.

#### `cut_video.py` - Video Segment Extractor
```bash
# Interactive mode
python scripts/helpers/cut_video.py

# Command-line mode (single video)
python scripts/helpers/cut_video.py video.mp4 10 30

# Batch mode (multiple videos, same time range)
python scripts/helpers/cut_video.py video1.mp4 video2.mp4 video3.mp4 00:01:00 00:02:00
```
Extracts video segments using FFmpeg stream copy (fast, no re-encoding). Supports:
- Time formats: HH:MM:SS or seconds (e.g., "90" or "00:01:30")
- Batch processing with progress tracking
- Auto-incrementing output filenames to avoid overwrites

#### `extract_multi_case_dates.py` - Multi-Case Date Extractor
```bash
python scripts/helpers/extract_multi_case_dates.py [times.xlsx]
```
Creates CSV from `cur_mp4_status` view showing which cases are handled in times.xlsx. Features:
- Cross-references database with Excel time ranges
- Marks case 2/3 presence with 'V' indicator
- Copies camera values from case 1 to marked cases

#### `fast_video_formula.py` - FPS Ratio Calculator
```python
from scripts.helpers.fast_video_formula import calculate_fps_ratio, time_to_minutes

# Convert time to minutes
time_to_minutes("01:40:00")  # Returns 100
time_to_minutes(100)         # Returns 100 (pass-through for numbers)

# Calculate FPS ratio for speed-adjusted videos
# Fast video (>30fps compressed to 30fps): smalltime/bigtime * 30
calculate_fps_ratio("01:40:00", "02:00:00", is_fast_video=True)   # (100/120)*30 = 25
calculate_fps_ratio(100, 120, is_fast_video=True)                  # Same result

# Slow video: bigtime/smalltime * 30
calculate_fps_ratio(100, 120, is_fast_video=False)                 # (120/100)*30 = 36
```

#### `run_bulk_copy.py` - Bulk Monitor Video Copier
Copies a predefined list of Monitor camera videos to a destination with renamed filenames (`monitor_date_XX-XX-XX_CASE_N.mp4`). Edit `SOURCE_FILES` list in script.

#### `sqlite_to_dbdiagram.py` - Database Diagram Generator
```bash
python scripts/helpers/sqlite_to_dbdiagram.py
```
Exports database schema to dbdiagram.io format. Output saved to `docs/scalpel_dbdiagram.txt`. Features:
- Auto-detects foreign key relationships
- Generates table notes
- Ready for paste into https://dbdiagram.io

---

### Multi-Video Playback

**`multiMPV/multiMPV.py`** - Synchronized Multi-Camera Viewer
```bash
python multiMPV/run_viewer.py
# or double-click multiMPV.exe (if compiled)
```
Displays up to 9 videos in synchronized grid layout using MPV player. Features:
- Interactive file picker with folder memory
- Supports .mmpv and .txt playlist files
- Grid layouts: 1x1, 2x1, 3x1, 2x2, 3x2, 3x3
- Synchronized playback with shared controls

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
│   ├── 1_nuk_seq_export.py         # Batch SEQ file organization and export
│   ├── 2_4_update_db.py            # Combined SEQ/MP4 database status updater
│   ├── 3_seq_to_mp4_convert.py     # GPU-accelerated SEQ to MP4 converter
│   ├── 5_batch_blacken.py          # GPU batch video redaction
│   └── helpers/
│       ├── __init__.py             # Package initializer (exports utilities)
│       ├── handle_xlsx.py          # Excel file processor
│       ├── sql_to_path.py          # Query to file path resolver
│       ├── fast_video_formula.py   # FPS ratio calculator for speed conversions
│       ├── compare_databases.py    # Database diff tool
│       ├── compare_mp4.py          # MP4 backup checker
│       ├── compare_seq.py          # SEQ backup checker
│       ├── copy_files.py           # Simple file copier
│       ├── copy_with_structure.py  # Structured file copier
│       ├── cut_video.py            # Video segment extractor
│       ├── extract_multi_case_dates.py  # Multi-case date extractor
│       ├── run_bulk_copy.py        # Bulk monitor video copier
│       └── sqlite_to_dbdiagram.py  # Database diagram generator
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
