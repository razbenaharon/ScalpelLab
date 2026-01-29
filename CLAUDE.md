# CLAUDE.md - ScalpelLab

## Project Overview

ScalpelLab is a Python-based medical/surgical video management system. It handles the full pipeline for multi-camera surgical recordings: ingestion of SEQ files, conversion to MP4, privacy-compliant redaction, database tracking, computer vision analysis (YOLOv8 pose estimation), and synchronized multi-camera playback.

## Tech Stack

- **Language**: Python 3.7+
- **Web UI**: Streamlit (`run_app.py` entry point)
- **Database**: SQLite3 (`ScalpelDatabase.sqlite` at project root)
- **CV/ML**: YOLOv8 (Ultralytics), PyTorch, OpenCV, BoT-SORT/StrongSort tracking
- **Video**: FFmpeg (GPU/NVENC), MPV player
- **Data**: Pandas, NumPy, PyArrow (Parquet)
- **Platform**: Windows (paths use backslashes, NVENC GPU acceleration)

## Project Structure

```
app/                    Streamlit web app (database browser, stats, views)
scripts/                Batch processing (SEQ export, DB sync, video convert, redaction)
scripts/helpers/        Utility scripts (backup compare, video cutting, DB tools)
yolo/                   YOLOv8 pose estimation and tracking pipelines
SimCLR_reid/            Self-supervised person re-identification
MPV_DB/                 Multi-camera synchronized playback (Tkinter + MPV)
docs/                   Architecture docs, schema reference, ERD
speckit_prompts/        AI prompt templates (TOML)
config.py               Path configuration (DB_PATH, SEQ_ROOT, MP4_ROOT)
```

## Key Entry Points

- `run_app.py` - Launch Streamlit web interface
- `config.py` - Path configuration and validation
- `scripts/1_nuk_seq_export.py` - SEQ file organization (multi-threaded)
- `scripts/2_4_update_db.py` - Database/filesystem synchronization
- `scripts/3_seq_to_mp4_convert.py` - GPU video conversion
- `scripts/5_batch_blacken.py` - Privacy redaction pipeline (GPU parallel)
- `yolo/1_pose_anesthesiologist.py` - Pose detection entry point
- `MPV_DB/run_viewer.py` - Multi-camera viewer

## Database

SQLite database with composite primary keys (`recording_date`, `case_no`, optionally `camera_name`).

**Core tables**: `recording_details`, `anesthesiology`, `mp4_status`, `seq_status`, `mp4_times`, `analysis_information`

**Views**: `cur_mp4_missing`, `cur_seq_missing`, `cur_seniority`

Schema details in `docs/DATABASE_SCHEMA.md`.

## File System Layout

Videos follow the pattern: `{ROOT}/DATA_YY-MM-DD/CaseN/CameraName/*.{seq,mp4}`

Paths configured in `config.py`:
- `SEQ_ROOT` = `F:\Room_8_Data\Sequence_Backup`
- `MP4_ROOT` = `F:\Room_8_Data\Recordings`

## Conventions

- Google-style docstrings (see `docs/DOCSTRING_GUIDE.md`)
- No test suite - manual verification
- No CI/CD pipeline
- Dependencies managed via `requirements.txt` (pip)
- External tools (FFmpeg, MPV, ffprobe) installed separately
- 8 standard camera names defined in `config.py` (`DEFAULT_CAMERAS`)

## Common Commands

```bash
# Launch web interface
python run_app.py

# Validate configuration
python config.py

# Sync database with filesystem
python scripts/2_4_update_db.py

# Batch redaction
python scripts/5_batch_blacken.py [times.xlsx] [output_dir] [workers]
```

## Architecture Notes

- `scripts/5_batch_blacken.py` is the largest script (~1231 lines) handling GPU-parallel privacy redaction with case-aware timing, auto-trimming, and batch resumption
- `scripts/2_4_update_db.py` supports `--skip-duration`, `--dry-run`, `--skip-seq`, `--skip-mp4` flags
- The Streamlit app pages are in `app/pages/` and auto-discovered by Streamlit
- YOLO tracking outputs Parquet files with per-frame keypoint data (17-keypoint COCO format)
