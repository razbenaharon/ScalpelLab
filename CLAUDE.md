# CLAUDE.md - ScalpelLab Development Guide

## Project Overview

ScalpelLab is a medical video analysis system for surgical recording management. It handles multi-camera surgical recordings through a pipeline of extraction, conversion, redaction, and computer vision analysis, with a Streamlit web dashboard and SQLite database backend.

## Repository Structure

```
ScalpelLab/
├── app/                    # Streamlit web application
│   ├── app.py              # Main app entry point
│   ├── utils.py            # Database utilities
│   └── pages/              # Dashboard pages (Database, Status, Views, MP4 Stats)
├── scripts/                # Main processing pipeline (numbered 1-5)
│   ├── 1_nuk_seq_export.py     # Multi-threaded SEQ file organization
│   ├── 2_update_db.py          # Database status updater (SEQ/MP4 scanning)
│   ├── 3_seq_to_mp4_convert.py # GPU-accelerated SEQ→MP4 conversion
│   ├── 5_batch_blacken.py      # GPU batch video redaction
│   └── helpers/                # 15+ utility scripts
├── yolo/                   # YOLO v8 pose detection and tracking
│   ├── pose_detect_botsort.py  # BoT-SORT tracker variant
│   ├── pose_detect_strongsort.py # StrongSORT tracker variant
│   └── osnet_ain_x1_0_msmt17.pt # Pre-trained ReID model (17MB)
├── SimCLR_reid/            # Person re-identification (SimCLR contrastive learning)
├── MPV_DB/                 # Multi-camera video player (Tkinter + MPV)
│   ├── run_viewer.py       # Player launcher
│   └── config.ini          # MPV player path and scaling config
├── docs/                   # Documentation and diagrams
│   ├── DOCSTRING_GUIDE.md  # Google-style docstring conventions
│   ├── ERD.pdf             # Entity-Relationship Diagram
│   └── scalpel_dbdiagram.txt # Database schema definition
├── config.py               # Central path configuration (SEQ_ROOT, MP4_ROOT, DB_PATH)
├── run_app.py              # Web app launcher
├── requirements.txt        # Python dependencies (~55 packages)
└── ScalpelDatabase.sqlite  # SQLite database
```

## Tech Stack

- **Language**: Python 3.7+
- **Web Framework**: Streamlit (>=1.49.0)
- **Database**: SQLite3 (built-in)
- **ML/CV**: PyTorch (>=2.0), Ultralytics YOLOv8, torchreid/OsNet
- **Video**: OpenCV, FFmpeg (external), MPV player (external)
- **Data**: Pandas, NumPy, PyArrow, SciPy
- **Visualization**: Plotly, Graphviz, Pillow

## Key Commands

```bash
# Run web dashboard
streamlit run app/app.py
# or
python run_app.py

# Validate configuration
python config.py

# Install dependencies
pip install -r requirements.txt

# Pipeline scripts (run in order)
python scripts/1_nuk_seq_export.py      # Organize SEQ files
python scripts/2_update_db.py           # Update database status
python scripts/3_seq_to_mp4_convert.py  # Convert SEQ → MP4
python scripts/5_batch_blacken.py       # Redact videos

# YOLO pose detection
python yolo/pose_detect_botsort.py
python yolo/pose_detect_strongsort.py
```

## Configuration

All paths are centralized in `config.py`:
- `SEQ_ROOT`: Root directory for raw SEQ files (default: `F:\Room_8_Data\Sequence_Backup`)
- `MP4_ROOT`: Root directory for MP4 files (default: `F:\Room_8_Data\Recordings`)
- `DB_PATH`: SQLite database location (always at project root)
- `DEFAULT_CAMERAS`: 8 standard camera names (Cart_Center_2, Cart_LT_4, Cart_RT_1, General_3, Monitor, Patient_Monitor, Ventilator_Monitor, Injection_Port)

**External tool requirements**: FFmpeg and MPV player must be installed separately.

## Database Schema

**7 tables** in `ScalpelDatabase.sqlite`:
- `recording_details` — Surgical recording metadata with anesthesiologist assignment
- `anesthesiology` — Resident/staff information and career progression
- `mp4_status` — Exported MP4 file tracking (size, duration, redaction)
- `seq_status` — Original SEQ sequence file tracking
- `mp4_times` — Case time ranges for video redaction
- `analysis_information` — Labeling and analysis metadata
- `sqlite_sequence` — Auto-increment tracking

**3 views**: `cur_mp4_missing`, `cur_seq_missing`, `cur_seniority`

Schema definition: `docs/scalpel_dbdiagram.txt`

## File Organization Conventions

Video files follow this directory structure:
```
ROOT/DATA_YY-MM-DD/CaseN/CameraName/*.{seq,mp4}
```

Example: `F:\Room_8_Data\Recordings\DATA_25-01-15\Case1\General_3\video.mp4`

## Code Conventions

- **Docstrings**: Google-style (see `docs/DOCSTRING_GUIDE.md`)
- **Path handling**: Use `pathlib.Path` for cross-platform compatibility; use raw strings (`r"..."`) for Windows paths
- **Database operations**: Scripts only manage their specific columns to maintain future-proof design
- **File operations**: Use atomic operations with SHA256 hash verification
- **Concurrency**: `concurrent.futures` for parallel file operations (default 8 workers)
- **GPU acceleration**: NVIDIA NVENC via FFmpeg when available, with CPU fallback

## Testing

No formal test framework is in place. Validation is done through:
- `scripts/helpers/compare_databases.py` — Database diff tool
- `scripts/helpers/detect_corrupt_frames.py` — Frame corruption detection
- `python config.py` — Path validation
- Manual testing of pipeline scripts

## Important Notes

- The database file (`ScalpelDatabase.sqlite`) must always remain in the project root
- The pre-trained model file `yolo/osnet_ain_x1_0_msmt17.pt` (17MB) is tracked in git
- Pipeline scripts are numbered (1→2→3→5) indicating execution order
- Scripts use 30-minute time windows for automatic case grouping
- Video redaction is privacy-compliant (blacks out sensitive areas based on case times)
- The project was originally developed on Windows; paths in config.py reflect this
