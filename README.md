# ScalpelLab Database Manager

A Streamlit-based database management system for managing and monitoring surgical recording data, including MP4 video files and SEQ sequence files from multiple camera sources.

## Installation

### Requirements
- Python 3.7 or higher
- Required Python packages (install via pip):

```bash
pip install streamlit
pip install pandas
pip install sqlite3  # Usually included with Python
pip install pathlib  # Usually included with Python
```

### Quick Setup
1. Clone or download the project
2. Navigate to the project directory
3. Install requirements:
   ```bash
   pip install -r requirements.txt
   ```
   *Note: If requirements.txt doesn't exist, install packages individually as shown above*

4. Ensure the `ScalpelDatabase.sqlite` file is in the project root directory
5. Run the application:
   ```bash
   streamlit run app.py
   ```

## Features

### 📊 Streamlit Web Interface
- **Database Management**: Browse tables, insert new records, and delete existing rows with an intuitive interface
- **Status Summary**: View MP4/SEQ files statistics per camera and distributions
- **Views**: Access and query database views for specialized data perspectives


### Database Configuration
Set the database path in the sidebar to the ScalpelDatabase.sqlite file in the project directory. The app will open in your browser at `http://localhost:8501`


### 🎥 Video File Management
- **MP4 Status Tracking**: Monitor exported MP4 files per camera
- **SEQ Status Tracking**: Track original sequence files per camera
- **Automatic Status Updates**: Scripts to scan directories and update database status
- **Smart File Cleanup**: Delete small/incomplete MP4 files to free up space

### 🗄️ Database Schema
- **recording_details**: Core table for recording metadata
- **anesthesiology**: Anesthesiology resident roster and career progression
- **mp4_status**: Normalized table tracking MP4 file status per camera
- **seq_status**: Normalized table tracking SEQ file status per camera
- **analysis_information**: Per case labeling metadata


## Project Structure

```
ScalpeLab/
├── app.py                          # Main Streamlit application
├── run_app.py                      # Quick launcher for the app
├── utils.py                        # Database utility functions
├── pages/                          # Streamlit pages
│   ├── 1_Database.py              # Browse, insert, and delete database records
│   ├── 2_Status_Summary.py        # MP4/SEQ status dashboard
│   └── 3_Views.py                 # Database views browser
├── scripts/                        # Command-line utilities
│   ├── batch_export.py            # Batch export SEQ files to MP4
│   ├── update_status.py           # Update MP4/SEQ file status
│   ├── sqlite_to_dbdiagram.py     # Generate DB diagram
│   ├── migrate_anesthetic_to_anesthesiology.py  # Database migration script
│   └── migrate_anesthetic_start_date.py         # Database migration script
├── docs/                           # Documentation
│   ├── ERD.pdf                    # Entity relationship diagram
│   └── scalpel_dbdiagram.txt      # Database schema definition
├── run_batch_export.py            # Quick launcher for batch export
├── BATCH_EXPORT_GUIDE.md          # Guide for batch export operations
└── ScalpelDatabase.sqlite         # SQLite database file
```

## Database Tables

### recording_details
Core table storing metadata for each surgical recording session.

| Column | Type | Required | Description                                                                               |
|--------|------|----------|-------------------------------------------------------------------------------------------|
| `recording_date` | TEXT | ✓ | Date of recording (YYYY-MM-DD format)                                                     |
| `signature_time` | TEXT | | Time when recording was signed/validated                                                  |
| `case_no` | INTEGER | ✓ | Case number for the recording date (1, 2, 3, etc.)                                        |
| `code` | TEXT | | Anesthesiology resident code                                                              |
| `anesthesiology_key` | INTEGER | ✓| Foreign key linking to anesthesiology table                                               |
| `months_anesthetic_recording` | INTEGER | | Months of anesthesiology experience at time of recording - Auto inserted                  |
| `anesthetic_attending` | TEXT | | Anesthetist level at time of recording ('A' = Attending, 'R' = Resident) - Auto inserted  |

**Primary Key**: `(recording_date, case_no)`

### anesthesiology
Table storing information about anesthesiology residents and their career progression.

| Column | Type | Required | Description |
|--------|------|----------|-------------|
| `anesthesiology_key` | INTEGER | ✓ | Primary key, auto-increment |
| `name` | TEXT | ✓ | Full name of the anesthesiology resident |
| `code` | TEXT | | Short code/identifier (auto-generated: FirstInitial + LastInitial + YYMM) |
| `anesthesiology_start_date` | TEXT | | Date when anesthesiology training started (YYYY-MM-DD) |
| `grade_a_date` | TEXT | | Date when promoted to Grade A/Attending level |

### mp4_status
Normalized table tracking the status of exported MP4 video files for each camera per recording.

| Column | Type | Required | Description |
|--------|------|----------|-------------|
| `recording_date` | TEXT | ✓ | Date of recording (YYYY-MM-DD format) |
| `case_no` | INTEGER | ✓ | Case number for the recording date |
| `camera_name` | TEXT | ✓ | Name of the camera (see Camera Configuration) |
| `value` | INTEGER | | Status code (1=Complete, 2=Incomplete, 3=Missing) |
| `comments` | TEXT | | Additional notes about the MP4 status |
| `size_mb` | INTEGER | | Total size of MP4 files in megabytes |

**Primary Key**: `(recording_date, case_no, camera_name)`

#### MP4 value
- **1**: At least one MP4 file >= 200MB (complete)
- **2**: MP4 files exist but all < 200MB (incomplete)
- **3**: No MP4 files found (missing)
- 
### seq_status
Normalized table tracking the status of original SEQ sequence files for each camera per recording.

| Column | Type | Required | Description |
|--------|------|----------|-------------|
| `recording_date` | TEXT | ✓ | Date of recording (YYYY-MM-DD format) |
| `case_no` | INTEGER | ✓ | Case number for the recording date |
| `camera_name` | TEXT | ✓ | Name of the camera (see Camera Configuration) |
| `value` | INTEGER | | Status code (1=Complete, 2=Incomplete, 3=Missing) |
| `comments` | TEXT | | Additional notes about the SEQ status |
| `size_mb` | INTEGER | | Total size of SEQ files in megabytes |

**Primary Key**: `(recording_date, case_no, camera_name)`

#### SEQ value
- **1**: At least one SEQ file > 200MB (complete)
- **2**: SEQ files exist but all < 200MB (incomplete)
- **3**: No SEQ files found (missing)



### analysis_information
Table for storing analysis and labeling information.

| Column | Type | Required | Description |
|--------|------|----------|-------------|
| `recording_date` | TEXT | | Date of recording being analyzed |
| `case_no` | INTEGER | | Case number being analyzed |
| `label_by` | TEXT | | Person/system who performed the labeling |

**Primary Key**: `(recording_date, case_no)`

## Database Relationships

- `recording_details.anesthesiology_key` → `anesthesiology.anesthesiology_key` (Foreign Key)
- `mp4_status.(recording_date, case_no)` → `recording_details.(recording_date, case_no)` (Logical relationship)
- `seq_status.(recording_date, case_no)` → `recording_details.(recording_date, case_no)` (Logical relationship)
- `analysis_information.(recording_date, case_no)` → `recording_details.(recording_date, case_no)` (Logical relationship)

## Camera Configuration

The system tracks 8 camera sources:
- Cart_Center_2
- Cart_LT_4
- Cart_RT_1
- General_3
- Monitor
- Patient_Monitor
- Ventilator_Monitor
- Injection_Port


## Directory Structure Expected

### Recordings (MP4 files)
```
F:\Room_8_Data\Recordings\
└── DATA_YY-MM-DD\
    └── CaseN\
        └── <CameraName>\
            └── *.mp4
```

### Sequence Backups (SEQ files)
```
F:\Room_8_Data\Sequence_Backup\
└── DATA_YY-MM-DD\
    └── CaseN\
        └── <CameraName>\
            └── *.seq
```


## Database Views

The system provides predefined views to simplify complex queries and highlight important conditions.

### cur_mp4_missing 
Checks if any MP4 file is **missing** (`status = 3`) **while the corresponding SEQ file exists** with status `1` (complete) or `2` (incomplete).  
This view helps quickly identify recordings where the original SEQ file is present but the MP4 export is missing or failed.

### cur_seq_missing
Checks if any SEQ file is **missing** (`status = 3`) **while the corresponding MP4 file exists** with status `1` (complete) or `2` (incomplete).

### cur_seniority
**Purpose**: Calculates current seniority and attending status for each anesthesiology resident based on their start date.

**Key Columns**:
- `seniority_month_cur`: Months of experience from `anesthesiology_start_date` until now
- `anesthetic_attending_cur`: Current level ('A' = Attending if >60 months, 'R' = Resident if ≤60 months)

**Business Logic**:
- Residents with >60 months (5 years) of experience are considered Attending level
- Those with ≤60 months are considered Resident level
- This view is used to dynamically determine current status without manual updates

## Batch Export

### Command-Line Batch Export
For large-scale conversions without the web interface overhead, use the batch export script:

```bash
python run_batch_export.py
```

**Features**:
- Export all or selected SEQ files to MP4 format
- Choose between CLExport (with FFmpeg fallback) or FFmpeg only
- Real-time conversion progress output
- File size monitoring to detect stuck conversions
- Automatic fallback if primary converter fails

See `BATCH_EXPORT_GUIDE.md` for detailed usage instructions.

### Database Migrations

Migration scripts are provided in the `scripts/` directory for database schema updates:
- `migrate_anesthetic_to_anesthesiology.py` - Rename anesthetic table to anesthesiology
- `migrate_anesthetic_start_date.py` - Rename anesthetic_start_date column

### Utility Scripts

#### Update Status (update_status.py)
Scan directories and update both `mp4_status` and `seq_status` tables

#### Generate Database Diagram (sqlite_to_dbdiagram.py)
Generate dbdiagram.io format file from the database schema
