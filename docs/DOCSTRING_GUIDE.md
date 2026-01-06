# Google-Style Docstring Guide for ScalpelLab

This guide demonstrates how to add comprehensive Google-style docstrings to all Python modules, classes, and functions in the ScalpelLab project.

---

## Table of Contents

1. [Module-Level Docstrings](#module-level-docstrings)
2. [Function Docstrings](#function-docstrings)
3. [Class Docstrings](#class-docstrings)
4. [Examples by Module](#examples-by-module)
   - [yolo/ Module Examples](#yolo-module-examples)
   - [scripts/ Module Examples](#scripts-module-examples)
   - [app/ Module Examples](#app-module-examples)

---

## Module-Level Docstrings

Every Python file should start with a module-level docstring explaining:
- What the module does
- How it fits into the larger system
- Key dependencies
- Usage examples (if applicable)

### Template

```python
"""Brief one-line summary of the module.

Extended description explaining the module's purpose, how it integrates
with other components, and key functionality it provides.

Key Features:
    - Feature 1: Description
    - Feature 2: Description
    - Feature 3: Description

Dependencies:
    - External package 1 (what it's used for)
    - External package 2 (what it's used for)

Example:
    Basic usage example::

        $ python module_name.py input_file.ext
        # Or
        from module_name import function_name
        result = function_name(arg1, arg2)

Notes:
    Any important notes about configuration, performance, or limitations.

See Also:
    Related modules or documentation references.
"""
```

---

## Function Docstrings

All functions should have docstrings with:
- Brief description
- Args (parameters)
- Returns (return values)
- Raises (exceptions)
- Examples (optional but recommended)

### Template

```python
def function_name(param1: type1, param2: type2, optional_param: type3 = None) -> return_type:
    """Brief one-line summary of what the function does.

    Extended description providing more context about the function's purpose,
    algorithm, or important implementation details.

    Args:
        param1: Description of param1. Explain what it represents,
            valid ranges, or expected format.
        param2: Description of param2.
        optional_param: Description of optional parameter. Explain
            default behavior if not provided. Defaults to None.

    Returns:
        Description of the return value. Include type information
        and explain what the value represents. For complex returns,
        describe the structure.

    Raises:
        ValueError: When param1 is out of valid range.
        FileNotFoundError: When specified file doesn't exist.
        RuntimeError: When operation fails for specific reason.

    Example:
        Basic usage example::

            result = function_name("value1", 42)
            print(f"Result: {result}")

        Advanced usage example::

            result = function_name(
                param1="complex_value",
                param2=100,
                optional_param={"key": "value"}
            )

    Note:
        Any important notes about performance, side effects,
        or special behavior.

    Warning:
        Any warnings about usage, limitations, or potential issues.
    """
    pass
```

---

## Class Docstrings

Classes should have docstrings describing:
- Class purpose
- Attributes
- Methods (brief overview)
- Usage examples

### Template

```python
class ClassName:
    """Brief one-line summary of the class.

    Extended description explaining what the class represents, its role
    in the system, and how it should be used.

    Attributes:
        attribute1: Description of public attribute.
        attribute2: Description of public attribute.

    Example:
        Basic usage::

            obj = ClassName(arg1, arg2)
            obj.method_name()
            result = obj.get_result()

    Note:
        Important implementation details or usage notes.
    """

    def __init__(self, param1: type1, param2: type2):
        """Initialize the ClassName instance.

        Args:
            param1: Description of initialization parameter.
            param2: Description of initialization parameter.

        Raises:
            ValueError: When parameters are invalid.
        """
        self.attribute1 = param1
        self.attribute2 = param2

    def method_name(self, arg1: type1) -> return_type:
        """Brief description of the method.

        Args:
            arg1: Description of argument.

        Returns:
            Description of return value.
        """
        pass
```

---

## Examples by Module

### yolo/ Module Examples

#### yolo/1_pose_anesthesiologist.py - Module-Level Docstring

```python
"""Multi-person pose detection and tracking using YOLOv8 with BoT-SORT.

This module provides GPU-accelerated pose estimation for surgical videos,
tracking multiple persons (surgeons, anesthesiologists, nurses) across
entire video sequences. It integrates with the ScalpelLab database and
outputs keypoint data in Parquet format for downstream analysis.

Key Features:
    - YOLOv8-Pose: 17 COCO keypoint detection per person
    - BoT-SORT Tracking: Persistent track IDs with re-identification
    - GPU Acceleration: CUDA-powered inference with CPU fallback
    - Every-Frame Processing: Ensures tracking consistency
    - Smart Sampling: Saves data at target FPS to manage file size
    - Video Repair: Automatic FFmpeg repair for corrupted videos

Architecture:
    1. Video Integrity Check: repair_video() fixes corrupted videos
    2. Model Setup: YOLO model loaded with specified configuration
    3. Frame-by-Frame Processing: Tracks persons across ALL frames
    4. Selective Saving: Saves keypoint data at target FPS
    5. Parquet Export: Outputs structured keypoint data

Data Flow:
    MP4 Video → repair_video() → YOLO + BoT-SORT → Keypoint DataFrame →
    Parquet File (17 keypoints × 3 values per person per frame)

Performance:
    - GPU (RTX 3080): ~30 FPS for 1920x1080 video with yolov8x-pose
    - CPU (i7-10700K): ~3 FPS for same configuration
    - Memory: ~2GB GPU VRAM for yolov8x-pose at 1280px input

Configuration:
    See yolo/0_yolo_config.json for model, tracker, and processing settings.

Dependencies:
    - ultralytics: YOLO model and tracking framework
    - torch: PyTorch for GPU acceleration
    - opencv-python (cv2): Video I/O operations
    - pandas: DataFrame creation for keypoint data
    - pyarrow: Parquet file serialization
    - ffmpeg: Video repair (external tool)

Example:
    Command-line usage::

        $ python yolo/1_pose_anesthesiologist.py video.mp4
        # Outputs: video_mask.mp4_keypoints.parquet

    Programmatic usage::

        from yolo import pose_anesthesiologist_yolo

        parquet_path = pose_anesthesiologist_yolo(
            video_path="path/to/video.mp4",
            output_path="path/to/output.mp4"
        )

Output Format:
    Parquet file with columns:
        - Frame_ID (int): Frame number (0-indexed)
        - Timestamp (float): Time in seconds
        - Track_ID (int): Unique person identifier
        - {Keypoint}_x (float): X coordinate in pixels (17 keypoints)
        - {Keypoint}_y (float): Y coordinate in pixels (17 keypoints)
        - {Keypoint}_conf (float): Confidence score 0-1 (17 keypoints)

COCO 17 Keypoints:
    Nose, Left_Eye, Right_Eye, Left_Ear, Right_Ear,
    Left_Shoulder, Right_Shoulder, Left_Elbow, Right_Elbow,
    Left_Wrist, Right_Wrist, Left_Hip, Right_Hip,
    Left_Knee, Right_Knee, Left_Ankle, Right_Ankle

Notes:
    - Processes EVERY frame for tracking consistency (fixes "too many IDs" issue)
    - Saves data only at TARGET_FPS to keep file size manageable
    - BoT-SORT tracking with reid=True handles occlusions and re-entry
    - Half-precision (FP16) inference enabled by default on GPU

See Also:
    - yolo/2_inspect_parquet.py: Parquet file analysis and visualization
    - yolo/3_process_tracks.py: Track filtering and merging
    - yolo/visualize_overlay.py: Generate skeleton overlay videos
    - yolo/0_yolo_config.json: Configuration file documentation

Author:
    ScalpelLab Development Team

Version:
    2.0.0 (2026-01-06)
"""
```

#### Example Function: `load_config()`

```python
def load_config() -> dict:
    """Load YOLOv8 configuration from JSON file.

    Reads configuration from `0_yolo_config.json` in the same directory
    as this module. If keys are missing, populates with default values
    for yolo, tracking, device, and video settings.

    Returns:
        dict: Configuration dictionary with keys:
            - yolo (dict): Model settings (model, conf, iou, imgsz, etc.)
            - tracking (dict): Tracker settings (tracker, persist, verbose)
            - device (dict): Device settings (use_cuda)
            - video (dict): Video settings (auto_repair, paths)

    Raises:
        FileNotFoundError: If 0_yolo_config.json not found.
        json.JSONDecodeError: If config file contains invalid JSON.

    Example:
        ::

            config = load_config()
            model_name = config['yolo']['model']
            use_gpu = config['device']['use_cuda']

    Note:
        Missing configuration keys are automatically populated with defaults:
        - yolo.model: "yolov8m-pose.pt"
        - yolo.confidence_threshold: 0.15
        - tracking.tracker: "botsort.yaml"
        - device.use_cuda: True (if available)
    """
    config_path = os.path.join(os.path.dirname(__file__), "0_yolo_config.json")

    with open(config_path, 'r') as f:
        config = json.load(f)

    # Ensure keys exist with defaults
    if "yolo" not in config:
        config["yolo"] = {
            "model": "yolov8m-pose.pt",
            "confidence_threshold": 0.15,
            "iou_threshold": 0.7,
            "brightness_boost": 1.0,
            "use_half_precision": True,
            "imgsz": 640
        }

    if "tracking" not in config:
        config["tracking"] = {
            "tracker": "botsort.yaml",
            "persist": True,
            "verbose": False
        }

    return config
```

#### Example Function: `pose_anesthesiologist_yolo()`

```python
def pose_anesthesiologist_yolo(video_path: str, output_path: str = None) -> str:
    """Detect and track all persons in video using YOLOv8-Pose with BoT-SORT tracking.

    Performs multi-person pose estimation across entire video sequence,
    maintaining persistent track IDs even through occlusions. Processes
    every frame for tracking consistency but saves keypoint data only
    at target FPS to manage file size.

    The function:
        1. Repairs video if corrupted (optional, configurable)
        2. Initializes YOLOv8-Pose model with BoT-SORT tracker
        3. Processes every frame to maintain tracking consistency
        4. Saves keypoint data at intervals (default: every frame if 30 FPS)
        5. Exports Parquet file with 17 COCO keypoints per person

    Args:
        video_path: Path to input MP4 video file. Can be absolute or relative.
            Video will be auto-repaired if corrupted (using FFmpeg).
        output_path: Path for output files. If None, creates output
            in same directory as input with "_mask.mp4" suffix.
            Parquet file will have "_keypoints.parquet" suffix.
            Defaults to None.

    Returns:
        str: Path to generated Parquet keypoint file
            (e.g., "/path/to/video_mask.mp4_keypoints.parquet").

    Raises:
        FileNotFoundError: If video_path doesn't exist.
        ValueError: If video cannot be opened or is invalid format.
        RuntimeError: If YOLO model fails to load or inference fails.
        MemoryError: If insufficient GPU/RAM for video processing.

    Example:
        Basic usage::

            parquet_path = pose_anesthesiologist_yolo(
                "F:/Recordings/case1.mp4"
            )
            print(f"Keypoints saved to: {parquet_path}")

        With custom output path::

            parquet_path = pose_anesthesiologist_yolo(
                video_path="input_video.mp4",
                output_path="output/processed_video.mp4"
            )

    Output Format:
        Parquet file with columns:
            - Frame_ID (int64): Frame number (0-indexed)
            - Timestamp (float64): Time in seconds
            - Track_ID (int64): Unique person ID (1, 2, 3, ...)
            - {Keypoint}_x (float64): X pixel coordinate (17 keypoints)
            - {Keypoint}_y (float64): Y pixel coordinate (17 keypoints)
            - {Keypoint}_conf (float64): Confidence 0-1 (17 keypoints)

        Example row:
            Frame_ID=0, Timestamp=0.0, Track_ID=1,
            Nose_x=960.5, Nose_y=540.2, Nose_conf=0.95, ...

    Performance:
        Typical processing speeds (1920x1080 @ 30 FPS):
            - GPU (RTX 3080) + yolov8x-pose: ~30 FPS
            - GPU (RTX 3060) + yolov8m-pose: ~45 FPS
            - CPU (i7-10700K) + yolov8m-pose: ~3 FPS

        Memory requirements:
            - GPU VRAM: 2-4 GB (depending on model and imgsz)
            - System RAM: 4-8 GB

    Notes:
        - Processes ALL frames (not just sampled) to ensure tracking consistency
        - This solves the "too many unique track IDs" problem
        - Saves data only at frame_interval to keep output size manageable
        - Frame interval = max(1, int(video_fps / TARGET_FPS))
        - BoT-SORT with reid=True handles occlusions and person re-entry

    See Also:
        - yolo/2_inspect_parquet.py: Inspect generated Parquet files
        - yolo/3_process_tracks.py: Post-process tracks (filter, merge)
        - yolo/visualize_overlay.py: Generate debug videos with overlays

    Warning:
        Large videos may generate large Parquet files:
            - 60-minute video @ 30 FPS: ~2-5 GB Parquet file
            - Reduce TARGET_FPS in code to decrease file size
    """
    # Implementation here...
    pass
```

---

### scripts/ Module Examples

#### scripts/5_batch_blacken.py - Module-Level Docstring

```python
"""GPU-accelerated batch video redaction with database integration.

This module provides HIPAA-compliant video redaction for surgical recordings,
automatically reading case time ranges from the ScalpelDatabase and applying
privacy masks based on whether footage is during or outside case times.

Key Features:
    - Database Integration: Reads case times from mp4_times table
    - GPU Parallel Processing: Up to 8 concurrent workers with NVENC
    - Smart Tracking: JSON-based tracking prevents re-processing
    - Case-Based Redaction: Different masks for during/outside cases
    - Auto-Trimming: Removes footage > 1 hour after last case
    - Real-Time DB Updates: Updates mp4_status after each video
    - Comprehensive Reporting: Detailed timing and statistics
    - Resume Capability: Can continue interrupted batches

Redaction Logic:
    - During Case Times: Small corner box (1/3 width × 1/2 height, bottom-right)
    - Outside Case Times: Full screen black
    - Pre-Segment: From video start to first case start
    - Between Cases: Gap time split equally (post for case N, pre for case N+1)
    - Post-Segment: From last case end to min(video end, case_end + 1 hour)

Architecture:
    1. Database Loading: Query mp4_times joined with mp4_status
    2. Tracking Check: Load JSON tracking file, skip processed videos
    3. File Selection: Interactive selection (all, first N, or range)
    4. Parallel Processing: ProcessPoolExecutor with NUM_WORKERS
    5. FFmpeg Redaction: GPU NVENC encoding with CPU fallback
    6. Database Updates: Update pre/post black segments per video
    7. Tracking Updates: Mark videos as processed in JSON file
    8. Report Generation: Export summary to text file

Data Flow:
    ScalpelDatabase.sqlite (mp4_times + mp4_status) →
    load_data_from_database() → DataFrame with paths + case times →
    redact_videos_from_df() → Parallel workers → FFmpeg NVENC →
    Redacted MP4s → update_mp4_status_black_segments() →
    Database updated

Performance:
    - GPU (RTX 3080) with 8 workers: ~8 videos per minute
    - GPU (RTX 3060) with 6 workers: ~6 videos per minute
    - CPU fallback: ~0.5-1 video per minute (per core)
    - Speedup: ~6-8x compared to sequential processing

Configuration:
    CONFIG dictionary (top of module):
        - OUTPUT_DIR: Output directory path
        - NUM_WORKERS: Parallel workers (2-8 recommended)
        - TRACKING_FILE: Path to JSON tracking file

Dependencies:
    - ffmpeg: Video processing with NVENC GPU encoding
    - pandas: DataFrame operations for case time data
    - sqlite3: Database queries and updates
    - concurrent.futures: Parallel processing framework

Example:
    Command-line usage::

        $ python scripts/5_batch_blacken.py
        # Interactive mode: select files to process

        $ python scripts/5_batch_blacken.py D:\\Output 6
        # Custom output dir and 6 workers

    Programmatic usage::

        from scripts.batch_blacken import redact_videos_from_df

        df = load_data_from_database()
        output_files, success, failed, statuses, report = redact_videos_from_df(
            df,
            output_dir="D:/Output",
            num_workers=8
        )

Database Schema:
    Input tables:
        - mp4_times: (recording_date, case_no, start_1-3, end_1-3)
        - mp4_status: (recording_date, case_no, camera_name, path)

    Output updates:
        - mp4_status.pre_black_segment: Minutes before first case
        - mp4_status.post_black_segment: Minutes after last case

FFmpeg Settings:
    GPU encoding (NVENC):
        -hwaccel cuda
        -c:v h264_nvenc
        -preset p1 (fastest)
        -b:v {original_bitrate}
        -c:a copy (audio stream copy)

    CPU fallback:
        -c:v libx264
        -preset faster
        -crf 23 (good quality)

Notes:
    - Tracking file location must be writable
    - Output directory created automatically if doesn't exist
    - Database must have mp4_times populated for case times
    - Workers should match GPU count (1-8 typical)
    - Each worker uses ~1-2 GB GPU VRAM

Security & Privacy:
    - Implements HIPAA-compliant video redaction
    - Redacts all non-case time periods with full black screen
    - Case time masking uses minimal corner box (allows surgical view)
    - Recommended: Store redacted videos separately from originals
    - Audit: Track all processing via tracking file timestamps

See Also:
    - scripts/2_4_update_db.py: Update database with video metadata
    - scripts/helpers/sql_to_path.py: Query database for file paths
    - docs/DATABASE_SCHEMA.md: Database schema documentation

Warning:
    - Parallel GPU processing can max out GPU memory
    - Reduce NUM_WORKERS if encountering CUDA out of memory errors
    - Ensure sufficient disk space (output ≈ same size as input)

Author:
    ScalpelLab Development Team

Version:
    3.0.0 (2026-01-06) - Added database integration and parallel processing
"""
```

#### Example Function: `load_data_from_database()`

```python
def load_data_from_database(db_path: str = None) -> pd.DataFrame:
    """Load video paths and case time ranges from ScalpelDatabase.

    Queries the mp4_times table joined with mp4_status to get video paths
    and their associated case time ranges. Reshapes the data from the
    database format (start_1, end_1, start_2, end_2) to the expected
    format for the redaction pipeline (start time - case 1, end time - case 1).

    Args:
        db_path: Path to SQLite database file. If None, uses default
            path from config.get_db_path(). Defaults to None.

    Returns:
        pd.DataFrame: DataFrame with columns:
            - path (str): Full filesystem path to MP4 file
            - start time - case 1 (str/float): Case 1 start time
            - end time - case 1 (str/float): Case 1 end time
            - start time - case 2 (str/float): Case 2 start time (if exists)
            - end time - case 2 (str/float): Case 2 end time (if exists)
            - start time - case 3 (str/float): Case 3 start time (if exists)
            - end time - case 3 (str/float): Case 3 end time (if exists)

        Returns empty DataFrame if no data found or database error occurs.

    Raises:
        sqlite3.OperationalError: If database file is locked or corrupted.
        sqlite3.DatabaseError: If SQL query is invalid.

    Example:
        Basic usage::

            df = load_data_from_database()
            print(f"Loaded {len(df)} videos from database")
            print(df[['path', 'start time - case 1']].head())

        With custom database path::

            df = load_data_from_database(db_path="backup/ScalpelDatabase.sqlite")

        Access case times::

            for idx, row in df.iterrows():
                video_path = row['path']
                case1_start = row['start time - case 1']
                case1_end = row['end time - case 1']
                print(f"{video_path}: Case 1 from {case1_start} to {case1_end}")

    SQL Query:
        ::

            SELECT
                ms.path,
                ms.recording_date,
                ms.case_no,
                ms.camera_name,
                mt.start_1, mt.end_1,
                mt.start_2, mt.end_2,
                mt.start_3, mt.end_3
            FROM mp4_status ms
            INNER JOIN mp4_times mt
                ON ms.recording_date = mt.recording_date
                AND ms.case_no = mt.case_no
            WHERE ms.path IS NOT NULL
            ORDER BY ms.recording_date, ms.case_no, ms.camera_name

    Data Transformation:
        - Removes rows where all case times are NULL/empty
        - Preserves rows with at least one valid case time pair
        - Maintains NULL values for missing case 2 and case 3 times

    Notes:
        - Time format can be HH:MM:SS (string) or seconds (float)
        - Database must have mp4_times populated with case times
        - Empty result indicates no videos with case time data
        - Use time_to_seconds() to convert string times to float

    See Also:
        - config.get_db_path(): Get default database path
        - update_mp4_status_black_segments(): Update database after redaction
        - parse_video_path(): Extract metadata from video path

    Warning:
        Database must not be locked by other processes (e.g., Streamlit app).
        Close all database connections before calling this function.
    """
    if db_path is None:
        db_path = get_db_path()

    print(f"Loading data from database: {db_path}")

    try:
        conn = sqlite3.connect(db_path)

        # Query to get all relevant data
        query = """
        SELECT
            ms.path,
            ms.recording_date,
            ms.case_no,
            ms.camera_name,
            mt.start_1, mt.end_1,
            mt.start_2, mt.end_2,
            mt.start_3, mt.end_3
        FROM mp4_status ms
        INNER JOIN mp4_times mt
            ON ms.recording_date = mt.recording_date
            AND ms.case_no = mt.case_no
        WHERE ms.path IS NOT NULL
        ORDER BY ms.recording_date, ms.case_no, ms.camera_name
        """

        df = pd.read_sql_query(query, conn)
        conn.close()

        if df.empty:
            print("Warning: No data found in database")
            return pd.DataFrame()

        print(f"Loaded {len(df)} rows from database")

        # Reshape the data to match expected format
        result_df = pd.DataFrame()
        result_df['path'] = df['path']

        # Map the case times
        for case_num in range(1, 4):  # Handle up to 3 cases
            start_col = f'start_{case_num}'
            end_col = f'end_{case_num}'

            if start_col in df.columns and end_col in df.columns:
                result_df[f'start time - case {case_num}'] = df[start_col]
                result_df[f'end time - case {case_num}'] = df[end_col]

        # Remove rows where all case times are null/empty
        case_columns = [col for col in result_df.columns if col.startswith('start time') or col.startswith('end time')]
        result_df = result_df.dropna(how='all', subset=case_columns)

        print(f"Processed into {len(result_df)} video entries")
        print("\nSample of loaded data:")
        print(result_df.head())

        return result_df

    except Exception as e:
        print(f"Error loading data from database: {e}")
        import traceback
        traceback.print_exc()
        return pd.DataFrame()
```

---

### app/ Module Examples

#### app/utils.py - Module-Level Docstring

```python
"""Database utility functions for ScalpelLab Streamlit application.

This module provides safe database connection management, schema inspection,
table/view operations, and Graphviz ERD generation for the Streamlit web
interface. All database operations use context managers for automatic
resource cleanup.

Key Features:
    - Safe Connections: Context manager ensures automatic close
    - Schema Inspection: Extract table structure with PRAGMA
    - Foreign Key Detection: Smart FK inference from naming patterns
    - ERD Generation: Graphviz diagram with crow's foot notation
    - View Support: List and query database views
    - Type-Safe: Returns pandas DataFrames for all data operations

Architecture:
    connect() → Context manager for safe SQLite connections
    ├── list_tables() → Get all user tables
    ├── list_views() → Get all database views
    ├── get_table_schema() → Extract table structure (PRAGMA table_info)
    ├── load_table() → Load entire table to DataFrame
    └── get_database_schema_graphviz() → Generate ERD

Foreign Key Detection:
    Automatically detects relationships based on naming conventions:
        - anesthesiology_key → anesthesiology.anesthesiology_key
        - recording_date + case_no → recording_details(recording_date, case_no)

    Detection patterns:
        1. Direct FK: column name matches {table}_key → table.{table}_key
        2. Composite FK: (recording_date, case_no) → recording_details
        3. Implicit FK: column name contains table name (heuristic)

Graphviz ERD Styling:
    - PyCharm-inspired color scheme
    - Crow's foot notation for relationships
    - Entity boxes with rounded corners
    - Hierarchical layout (top to bottom)
    - Distinct colors for PKs, FKs, and regular columns

Dependencies:
    - sqlite3: Database connections and queries
    - pandas: DataFrame operations and SQL integration
    - graphviz: ERD diagram generation (requires Graphviz installed)

Example:
    Database connection::

        with connect(db_path) as conn:
            tables = list_tables(db_path)
            df = load_table(db_path, "mp4_status")

    ERD generation::

        schema_data = get_database_schema_graphviz(db_path)
        dot_source = schema_data['dot']
        # Render with graphviz

    Schema inspection::

        schema = get_table_schema(db_path, "mp4_status")
        for col in schema:
            print(f"{col['name']}: {col['type']}")

Notes:
    - All operations are read-only except via external SQL
    - Context manager automatically commits and closes connections
    - Foreign keys must be enabled: PRAGMA foreign_keys = ON
    - View support requires SQLite 3.8.3+ (views in sqlite_master)

Security:
    - No user input is directly interpolated into SQL
    - All queries use parameterized statements where applicable
    - Database path must be validated before passing to functions

See Also:
    - app/app.py: Main Streamlit application using these utilities
    - app/pages/1_Database.py: Table browser and editor
    - docs/DATABASE_SCHEMA.md: Complete schema documentation

Author:
    ScalpelLab Development Team

Version:
    2.0.0 (2026-01-06)
"""
```

---

## Best Practices

### 1. Be Specific

**Bad**:
```python
def process(data):
    """Processes data."""
    pass
```

**Good**:
```python
def process_video_keypoints(keypoint_df: pd.DataFrame) -> pd.DataFrame:
    """Filter and smooth YOLO keypoint data for pose analysis.

    Removes low-confidence keypoints, interpolates missing values,
    and applies rolling average smoothing to reduce jitter.

    Args:
        keypoint_df: DataFrame with columns Frame_ID, Track_ID,
            {Keypoint}_x, {Keypoint}_y, {Keypoint}_conf.

    Returns:
        pd.DataFrame: Processed keypoint data with same schema.
            Low-confidence keypoints set to NaN, gaps interpolated.

    Example:
        ::

            df = pd.read_parquet("keypoints.parquet")
            processed_df = process_video_keypoints(df)
    """
    pass
```

### 2. Document Exceptions

Always document exceptions that callers should handle:

```python
def load_video(path: str) -> cv2.VideoCapture:
    """Load video file for processing.

    Args:
        path: Path to video file (MP4, AVI, or SEQ format).

    Returns:
        cv2.VideoCapture: Opened video capture object.

    Raises:
        FileNotFoundError: If video file doesn't exist.
        ValueError: If video format is unsupported.
        RuntimeError: If OpenCV cannot open the video.

    Example:
        ::

            try:
                cap = load_video("video.mp4")
                # Process video
            except FileNotFoundError:
                print("Video not found")
    """
    pass
```

### 3. Provide Examples

Include realistic examples showing actual usage:

```python
def query_videos(db_path: str, min_size_mb: int = 200) -> List[str]:
    """Query database for complete videos matching criteria.

    Args:
        db_path: Path to SQLite database.
        min_size_mb: Minimum file size in MB. Defaults to 200.

    Returns:
        List[str]: List of full file paths to matching videos.

    Example:
        Find all large Monitor videos::

            videos = query_videos(
                "ScalpelDatabase.sqlite",
                min_size_mb=300
            )
            print(f"Found {len(videos)} videos")
            for path in videos[:5]:
                print(f"  {path}")

        Query with custom size::

            incomplete_videos = query_videos(
                db_path="ScalpelDatabase.sqlite",
                min_size_mb=50  # Find incomplete videos
            )
    """
    pass
```

### 4. Document Return Types Clearly

For complex return types, describe the structure:

```python
def analyze_tracks(parquet_path: str) -> Dict[int, Dict[str, Any]]:
    """Analyze pose tracks for motion and position statistics.

    Args:
        parquet_path: Path to YOLO keypoint Parquet file.

    Returns:
        Dict[int, Dict[str, Any]]: Track statistics indexed by Track_ID.
            Each track dict contains:
                - 'frame_count' (int): Number of frames tracked
                - 'duration_sec' (float): Total duration in seconds
                - 'avg_confidence' (float): Average keypoint confidence
                - 'centroid' (Tuple[float, float]): Average (x, y) position
                - 'movement_px' (float): Total movement in pixels

    Example:
        ::

            stats = analyze_tracks("keypoints.parquet")
            for track_id, data in stats.items():
                print(f"Track {track_id}:")
                print(f"  Duration: {data['duration_sec']:.1f}s")
                print(f"  Confidence: {data['avg_confidence']:.2f}")
                print(f"  Movement: {data['movement_px']:.0f}px")
    """
    pass
```

### 5. Include Performance Notes

Document performance characteristics for expensive operations:

```python
def convert_all_videos(seq_root: str, mp4_root: str) -> None:
    """Convert all SEQ files to MP4 using GPU acceleration.

    Args:
        seq_root: Root directory containing SEQ files.
        mp4_root: Output directory for MP4 files.

    Performance:
        Typical conversion rates (per file):
            - GPU (RTX 3080): ~35 seconds for 60-min video
            - CPU (i7-10700K): ~10 minutes for 60-min video

        Batch performance (100 files):
            - GPU parallel (8 workers): ~1 hour total
            - GPU sequential: ~6 hours total
            - CPU: ~16 hours total

    Note:
        Uses all available CPU cores for parallel encoding.
        GPU memory: ~2 GB per worker.

    Warning:
        May generate hundreds of gigabytes of output.
        Ensure sufficient disk space before running.
    """
    pass
```

---

## Checklist for Complete Documentation

### Module Level
- [ ] One-line summary at top
- [ ] Extended description of module purpose
- [ ] Key features listed
- [ ] Dependencies listed with purpose
- [ ] Usage example provided
- [ ] Integration notes (how it fits in system)
- [ ] Author and version information

### Function Level
- [ ] One-line summary
- [ ] Extended description if complex
- [ ] All parameters documented (Args)
- [ ] Return value documented (Returns)
- [ ] Exceptions documented (Raises)
- [ ] At least one example provided
- [ ] Performance notes if applicable
- [ ] Type hints in function signature

### Class Level
- [ ] One-line summary
- [ ] Extended description of purpose
- [ ] Public attributes documented
- [ ] Usage example provided
- [ ] `__init__` method documented

---

## Tools for Validation

### Check Documentation Coverage

```bash
# Using interrogate
pip install interrogate
interrogate -v scripts/ yolo/ app/

# Using pydocstyle
pip install pydocstyle
pydocstyle scripts/ yolo/ app/
```

### Generate Documentation

```bash
# Using pdoc3
pip install pdoc3
pdoc3 --html --output-dir docs/ scripts/ yolo/ app/

# Using sphinx
pip install sphinx
sphinx-quickstart
sphinx-apidoc -o docs/source/ .
make html
```

---

## Summary

**Key Takeaways**:
1. Every module, class, and function needs a docstring
2. Use Google-style format for consistency
3. Include type hints in function signatures
4. Provide realistic examples
5. Document exceptions and edge cases
6. Add performance notes for expensive operations
7. Keep one-line summaries under 80 characters
8. Use active voice ("Returns X" not "X is returned")

**Benefits of Complete Documentation**:
- Easier onboarding for new developers
- Reduced need to read implementation code
- Better IDE autocomplete and hints
- Professional codebase appearance
- Easier to generate API documentation
- Improved maintainability

---

**Last Updated**: 2026-01-06
**Author**: ScalpelLab Documentation Team
