"""
build_dataset.py - Person Re-ID Dataset Builder for SimCLR Training

================================================================================
PURPOSE
================================================================================
This script builds a person re-identification dataset from surgical room videos
(General_3 camera) for training SimCLR-based contrastive learning models.

SimCLR (Simple Contrastive Learning of Representations) learns visual
representations by maximizing agreement between differently augmented views
of the same image. For person Re-ID, we need:
  1. Multiple crops of the SAME person (positive pairs)
  2. Quality-filtered crops (sharp, well-lit, proper aspect ratio)
  3. Temporal diversity (avoid near-duplicate frames)
  4. Identity isolation per case (prevent cross-case ID collisions)

================================================================================
OUTPUT STRUCTURE (Per-Date Folders)
================================================================================
OUTPUT_ROOT/
├── 2024-01-15/                            # Per-date directory
│   ├── Gen3_F000100_T005.jpg              # Flat structure within date
│   ├── Gen3_F000200_T005.jpg
│   ├── Gen3_F000300_T012.jpg
│   └── checkpoints/
│       └── progress.json
├── 2024-01-16/
│   └── ...
├── global_progress.json                   # Global checkpoint
└── dataset_stats.json                     # Final dataset statistics

Filename format: Gen3_F{frame:06d}_T{track:03d}.jpg
  - Track ID is included for analysis purposes, NOT for pairing
  - ByteTrack IDs are NOT reliable across videos (resets per video)
  - SimCLR learns to group visually similar people via contrastive loss

This structure enables SimCLR training by:
  - Providing diverse crops for self-supervised contrastive learning
  - Isolating dates into separate directories for organized processing
  - Maintaining quality through multi-stage filtering (blur, brightness, etc.)
  - SimCLR creates its own positive pairs via augmentation (not pre-labeled)

================================================================================
QUALITY FILTERS
================================================================================
  • Confidence: Detection confidence threshold (default: 0.80)
  • Area: Minimum bounding box area in pixels (default: 4500)
  • Blur: Laplacian variance threshold (default: 50)
  • Brightness: Valid range [20, 240] to avoid under/overexposure
  • Aspect Ratio: Height/Width ratio [1.3, 4.0] for standing persons

================================================================================
CUDA MEMORY MANAGEMENT
================================================================================
Script monitors and reports GPU memory usage throughout processing.
Use CUDA_VISIBLE_DEVICES environment variable to select specific GPU.

================================================================================
CHECKPOINT SYSTEM
================================================================================
Processing state is saved every 5 videos to enable resumption after interruption.
Checkpoints are stored per-case in case_XXX/checkpoints/progress.json

================================================================================
USAGE
================================================================================
    python build_dataset.py

Configuration is done via constants at the top of the file.
The script queries the SQLite database for General_3 camera videos.

================================================================================
CHANGES FROM V1
================================================================================
- Relaxed Confidence (0.92 -> 0.80) for more detections
- Relaxed Blur (110 -> 50) to allow slightly softer images
- Disabled Movement Filter (static standing is OK for Re-ID)
- Increased Frame Skip (speed up processing)
- Lowered Area Threshold (capture people further from camera)
- Added per-case output directories (prevents ID collision)
- Added per-ID subfolders (SimCLR positive pair grouping)
- Added CUDA memory monitoring

================================================================================
"""

import sys
import sqlite3
from pathlib import Path
from collections import defaultdict
import json
from datetime import datetime

import cv2
import numpy as np
from tqdm import tqdm
from ultralytics import YOLO
import torch

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config import DB_PATH, MP4_ROOT


def print_cuda_usage(prefix: str = "") -> None:
    """
    Print current CUDA memory usage.

    Args:
        prefix: Optional prefix string to identify the context of the measurement.

    Displays:
        - Allocated memory: Currently in-use GPU memory
        - Reserved memory: Total memory reserved by PyTorch (includes cached)
        - Max allocated: Peak memory usage since last reset
    """
    if not torch.cuda.is_available():
        print(f"{prefix}CUDA: Not available (using CPU)")
        return

    allocated = torch.cuda.memory_allocated() / 1024**3  # GB
    reserved = torch.cuda.memory_reserved() / 1024**3    # GB
    max_allocated = torch.cuda.max_memory_allocated() / 1024**3  # GB

    device_name = torch.cuda.get_device_name(0)
    total_memory = torch.cuda.get_device_properties(0).total_memory / 1024**3

    print(f"{prefix}CUDA [{device_name}]: "
          f"Alloc: {allocated:.2f}GB | "
          f"Reserved: {reserved:.2f}GB | "
          f"Peak: {max_allocated:.2f}GB | "
          f"Total: {total_memory:.1f}GB")

# ============================================================================
# CONFIGURATION - RELAXED FOR HIGH YIELD
# ============================================================================
MODEL_PATH = r"F:\YOLO_Models\yolo26m-pose.pt"
OUTPUT_ROOT = Path("F:/Room_8_Data/SIMCLR/dataset/simclr_reid_60k_v2")  # Root directory for all cases

# Dataset targets
TARGET_TOTAL_IMAGES = 10000000
MIN_IMAGES_PER_CLASS = 150
MAX_IMAGES_PER_CLASS = 1500  # Allow more variety per ID

# Quality filters - RELAXED
CONFIDENCE_THRESHOLD = 0.80     # Changed from 0.92
MIN_AREA_PIXELS = 4500          # Changed from 9000
BLUR_THRESHOLD = 50             # Changed from 110
BRIGHTNESS_MIN = 20
BRIGHTNESS_MAX = 240
ASPECT_RATIO_MIN = 1.3
ASPECT_RATIO_MAX = 4.0          # Allow taller crops

# Temporal diversity - IMPORTANT for SimCLR (avoid near-duplicates)
MIN_FRAME_GAP = 90              # ~3 seconds between saves (was 15)
MOVEMENT_THRESHOLD_PX = 0       # DISABLED - Static is OK

# Processing optimization
FRAME_SKIP = 25                 # Check every ~1 second
MAX_SAMPLES_PER_TRACK_PER_VIDEO = 50  # Reduced from 200 to limit per-person duplicates

# Visual diversity threshold for SimCLR
# Skip crops that are too similar to the last saved crop (histogram correlation)
SIMILARITY_THRESHOLD = 0.92     # Skip if correlation > this (1.0 = identical)

# Crop settings
PADDING_PIXELS = 30

# Device configuration
DEVICE = 'cuda' if torch.cuda.is_available() else 'cpu'

PERSON_CLASS_ID = 0


def get_general3_videos() -> list[dict]:
    """
    Query database for General_3 camera videos, sorted by case number.

    Returns:
        List of dictionaries with keys:
            - path: Absolute path to video file
            - recording_date: Date string of recording
            - case_no: Surgical case number

    Note:
        Only returns videos that exist on disk.
        Handles both 'General_3' and 'General 3' camera naming variants.
    """
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    query = """
        SELECT path, recording_date, case_no
        FROM mp4_status
        WHERE camera_name = 'General_3' OR camera_name = 'General 3'
        ORDER BY case_no, recording_date
    """
    cursor.execute(query)
    rows = cursor.fetchall()
    conn.close()

    videos = []
    for row in rows:
        if row["path"] is None:
            continue

        video_path = Path(row["path"])
        if not video_path.is_absolute():
            path_str = row["path"]
            if path_str.startswith("Recordings\\") or path_str.startswith("Recordings/"):
                path_str = path_str[len("Recordings") + 1:]
            video_path = Path(MP4_ROOT) / path_str

        if video_path.exists():
            videos.append({
                "path": str(video_path),
                "recording_date": row["recording_date"],
                "case_no": row["case_no"]
            })

    return videos


def sanitize_date(date_str: str) -> str:
    """
    Sanitize date string for safe use in filenames.

    Replaces slashes with dashes and spaces with underscores.
    Example: "2024/01/15 10:30" -> "2024-01-15_10:30"
    """
    return date_str.replace("/", "-").replace("\\", "-").replace(" ", "_")


def crop_with_padding(frame: np.ndarray, bbox: tuple, padding: int = PADDING_PIXELS) -> np.ndarray:
    """
    Extract a crop from frame with additional padding around bounding box.

    Args:
        frame: Full video frame (H, W, C)
        bbox: Bounding box as (x1, y1, x2, y2)
        padding: Pixels to add around the box (clamped to frame bounds)

    Returns:
        Cropped image region with padding applied
    """
    h, w = frame.shape[:2]
    x1, y1, x2, y2 = map(int, bbox)

    x1 = max(0, x1 - padding)
    y1 = max(0, y1 - padding)
    x2 = min(w, x2 + padding)
    y2 = min(h, y2 + padding)

    return frame[y1:y2, x1:x2]


def compute_blur_score(image: np.ndarray) -> float:
    """
    Compute blur score using Variance of Laplacian.

    Higher values indicate sharper images. Blurry images have low variance
    because the Laplacian (edge detector) responds weakly to smooth regions.

    Args:
        image: BGR or grayscale image

    Returns:
        Laplacian variance (higher = sharper). Typical threshold: 50-100
    """
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if len(image.shape) == 3 else image
    return cv2.Laplacian(gray, cv2.CV_64F).var()


def compute_brightness_score(image: np.ndarray) -> float:
    """
    Compute mean brightness of image.

    Args:
        image: BGR or grayscale image

    Returns:
        Mean pixel intensity [0-255]. Ideal range: 20-240 to avoid
        under/overexposed images.
    """
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if len(image.shape) == 3 else image
    return gray.mean()


def compute_sharpness_score(image: np.ndarray) -> float:
    """
    Compute sharpness using Sobel gradient magnitude.

    Measures edge strength across the image. Higher values indicate
    more defined edges (sharper image).

    Args:
        image: BGR or grayscale image

    Returns:
        Mean gradient magnitude (higher = sharper)
    """
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if len(image.shape) == 3 else image
    gx = cv2.Sobel(gray, cv2.CV_64F, 1, 0, ksize=3)
    gy = cv2.Sobel(gray, cv2.CV_64F, 0, 1, ksize=3)
    magnitude = np.sqrt(gx**2 + gy**2)
    return magnitude.mean()


def compute_histogram_similarity(img1: np.ndarray, img2: np.ndarray) -> float:
    """
    Compute histogram correlation between two images.

    Used to detect near-duplicate crops that should be skipped for SimCLR.
    High correlation (>0.92) indicates very similar images.

    Args:
        img1: First BGR image
        img2: Second BGR image

    Returns:
        Correlation coefficient [-1, 1]. Values >0.9 indicate high similarity.
    """
    # Resize to same size for comparison
    size = (64, 128)  # Width x Height (person aspect ratio)
    img1_resized = cv2.resize(img1, size)
    img2_resized = cv2.resize(img2, size)

    # Convert to HSV for better color comparison
    hsv1 = cv2.cvtColor(img1_resized, cv2.COLOR_BGR2HSV)
    hsv2 = cv2.cvtColor(img2_resized, cv2.COLOR_BGR2HSV)

    # Compute histograms (H and S channels, ignore V for lighting invariance)
    hist1 = cv2.calcHist([hsv1], [0, 1], None, [30, 32], [0, 180, 0, 256])
    hist2 = cv2.calcHist([hsv2], [0, 1], None, [30, 32], [0, 180, 0, 256])

    # Normalize
    cv2.normalize(hist1, hist1, 0, 1, cv2.NORM_MINMAX)
    cv2.normalize(hist2, hist2, 0, 1, cv2.NORM_MINMAX)

    # Compare using correlation
    return cv2.compareHist(hist1, hist2, cv2.HISTCMP_CORREL)


def save_checkpoint(checkpoint_path: Path, data: dict) -> None:
    """
    Save processing checkpoint to JSON file.

    Used for resumable processing - saves progress after each video/case
    to allow continuation after interruption.
    """
    with open(checkpoint_path, 'w') as f:
        json.dump(data, f, indent=2)


def load_checkpoint(checkpoint_path: Path) -> dict | None:
    """
    Load processing checkpoint from JSON file.

    Returns:
        Checkpoint dictionary if file exists, None otherwise
    """
    if checkpoint_path.exists():
        with open(checkpoint_path, 'r') as f:
            return json.load(f)
    return None


def process_video(
        video_info: dict,
        model: YOLO,
        output_dir: Path,
        group_track_stats: dict,
        target_remaining: int
) -> dict:
    """
    Process a single video and extract quality-filtered person crops.

    Uses cap.grab()/cap.retrieve() optimization to skip frames efficiently
    without decoding every frame. Saves crops to flat output directory.

    Args:
        video_info: Dictionary containing 'path', 'recording_date', 'case_no'
        model: Loaded YOLO model with tracking capability
        output_dir: Output directory for this group (e.g., OUTPUT_ROOT/2024-01-15/)
        group_track_stats: Track statistics dictionary for this group
                           (used to limit crops per track ID within the group)
        target_remaining: Number of images still needed to reach target

    Returns:
        dict with keys:
            - total_crops: Number of crops saved from this video
            - crops_per_track: Dict mapping track_id -> count for this video
            - frames_processed: Number of frames actually processed
            - avg_quality_per_track: Dict mapping track_id -> mean quality score
            - skipped: True if video was skipped (target reached)

    Output:
        Crops saved directly to output_dir with filename format:
        Gen3_F{frame:06d}_T{track:03d}.jpg
    """
    video_path = video_info["path"]
    date_str = sanitize_date(video_info["recording_date"])
    case_no = video_info["case_no"]

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise IOError(f"Cannot open video: {video_path}")

    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))

    if target_remaining <= 0:
        cap.release()
        return {'total_crops': 0, 'crops_per_track': {}, 'frames_processed': 0, 'skipped': True}

    crops_saved = 0
    frame_num = 0
    frames_processed = 0

    # Track statistics (includes last_crop for similarity checking)
    track_stats = defaultdict(lambda: {
        'count': 0,
        'last_saved_frame': -1000,
        'last_position': None,
        'last_crop': None,  # Store last saved crop for similarity check
        'quality_scores': [],
        'skipped_similar': 0  # Count of skipped due to similarity
    })

    pbar = tqdm(
        total=total_frames // FRAME_SKIP,
        desc=f"  Case{case_no:3d}",
        unit="frm",
        leave=False,
        ncols=100
    )

    while True:
        # =========================================================================
        # 1. ALWAYS GRAB: חייבים "לתפוס" את הפריים כדי לקדם את הסרט
        # =========================================================================
        grabbed = cap.grab()
        if not grabbed:
            break  # נגמר הסרטון או שיש תקלה

        # =========================================================================
        # 2. CHECK SKIP: האם זה פריים שצריך לעבד?
        # =========================================================================
        if frame_num % FRAME_SKIP != 0:
            frame_num += 1
            continue

        # =========================================================================
        # 3. RETRIEVE: פענוח מלא רק לפריים הנבחר
        # =========================================================================
        ret, frame = cap.retrieve()
        if not ret:
            break

        frames_processed += 1

        # מכאן הלוגיקה ממשיכה רגיל...

        # Early stopping
        if crops_saved > 0 and target_remaining - crops_saved <= 0:
            break

        # Run tracking
        results = model.track(
            frame,
            persist=True,
            classes=[PERSON_CLASS_ID],
            verbose=False,
            tracker="bytetrack.yaml",
            device=DEVICE
        )

        if results and len(results) > 0 and results[0].boxes is not None:
            boxes = results[0].boxes

            for i, box in enumerate(boxes):
                track_id = int(box.id[0]) if box.id is not None else i

                # Use group-specific track stats (prevents cross-date ID collision)
                group_count = group_track_stats.get(track_id, 0)
                local_count = track_stats[track_id]['count']

                # Limits
                if group_count >= MAX_IMAGES_PER_CLASS: continue
                if local_count >= MAX_SAMPLES_PER_TRACK_PER_VIDEO: continue

                # Temporal diversity
                frames_since_last = frame_num - track_stats[track_id]['last_saved_frame']
                if frames_since_last < MIN_FRAME_GAP: continue

                # Confidence
                conf = float(box.conf[0]) if box.conf is not None else 0.0
                if conf <= CONFIDENCE_THRESHOLD: continue

                # Box extraction
                xyxy = box.xyxy[0].cpu().numpy()
                x1, y1, x2, y2 = xyxy
                width = x2 - x1
                height = y2 - y1
                area = width * height

                if area < MIN_AREA_PIXELS: continue

                aspect_ratio = height / width if width > 0 else 0
                if aspect_ratio < ASPECT_RATIO_MIN or aspect_ratio > ASPECT_RATIO_MAX: continue

                # Crop
                crop = crop_with_padding(frame, (x1, y1, x2, y2), PADDING_PIXELS)

                if crop.size == 0 or crop.shape[0] < 10 or crop.shape[1] < 10: continue

                # Quality
                blur_score = compute_blur_score(crop)
                if blur_score < BLUR_THRESHOLD: continue

                brightness = compute_brightness_score(crop)
                if brightness < BRIGHTNESS_MIN or brightness > BRIGHTNESS_MAX: continue

                # Similarity check - skip if too similar to last saved crop for this track
                last_crop = track_stats[track_id]['last_crop']
                if last_crop is not None:
                    similarity = compute_histogram_similarity(crop, last_crop)
                    if similarity > SIMILARITY_THRESHOLD:
                        track_stats[track_id]['skipped_similar'] += 1
                        continue  # Skip near-duplicate

                sharpness = compute_sharpness_score(crop)
                quality_score = (blur_score * 0.5 + sharpness * 0.3 + (area / 10000) * 0.2)

                # Save directly to date folder (flat structure)
                # Track ID included in filename for potential analysis, not for SimCLR pairing
                # SimCLR will learn to group similar people via contrastive learning
                filename = f"Gen3_F{frame_num:06d}_T{track_id:03d}.jpg"
                save_path = output_dir / filename

                cv2.imwrite(str(save_path), crop)
                crops_saved += 1

                track_stats[track_id]['count'] += 1
                track_stats[track_id]['last_saved_frame'] = frame_num
                track_stats[track_id]['last_position'] = ((x1 + x2) / 2, (y1 + y2) / 2)
                track_stats[track_id]['last_crop'] = crop.copy()  # Store for similarity check
                track_stats[track_id]['quality_scores'].append(quality_score)

        frame_num += 1
        pbar.update(1)

    pbar.close()
    cap.release()

    avg_qualities = {}
    total_skipped_similar = 0
    for tid, stats in track_stats.items():
        if stats['quality_scores']:
            avg_qualities[tid] = np.mean(stats['quality_scores'])
        total_skipped_similar += stats['skipped_similar']

    return {
        'total_crops': crops_saved,
        'crops_per_track': {tid: stats['count'] for tid, stats in track_stats.items()},
        'frames_processed': frames_processed,
        'avg_quality_per_track': avg_qualities,
        'skipped_similar': total_skipped_similar,  # How many near-duplicates were skipped
        'skipped': False
    }




def main():
    """
    Main entry point for the Re-ID dataset builder.

    Workflow:
        1. Query database for General_3 camera videos
        2. Group videos by recording date
        3. Process each date independently (separate output directory)
        4. For each video: extract quality-filtered person crops
        5. Save crops to flat per-date directories for SimCLR training

    Output Structure:
        OUTPUT_ROOT/
        ├── 2024-01-15/
        │   ├── Gen3_F000100_T005.jpg
        │   └── checkpoints/
        ├── 2024-01-16/
        │   └── ...
        ├── global_progress.json
        └── dataset_stats.json

    SimCLR Note:
        Track IDs in filenames are NOT reliable identities.
        SimCLR will learn representations via self-supervised contrastive
        learning, creating positive pairs through augmentation.
    """
    print("=" * 80)
    print("PERSON RE-ID DATASET BUILDER V2 - SimCLR READY")
    print("=" * 80)
    print(f"Configuration:")
    print(f"  - Frame Skip: {FRAME_SKIP} (process every ~1 second)")
    print(f"  - Min Frame Gap: {MIN_FRAME_GAP} (~{MIN_FRAME_GAP/30:.1f}s between saves)")
    print(f"  - Similarity Threshold: {SIMILARITY_THRESHOLD} (skip near-duplicates)")
    print(f"  - Max Per Track/Video: {MAX_SAMPLES_PER_TRACK_PER_VIDEO}")
    print(f"  - Confidence: >{CONFIDENCE_THRESHOLD} | Area: >{MIN_AREA_PIXELS}px | Blur: >{BLUR_THRESHOLD}")
    print(f"  - Output Root: {OUTPUT_ROOT}")

    # Print CUDA info at startup
    print("\n" + "-" * 40)
    print_cuda_usage("[STARTUP] ")
    print("-" * 40)

    # Create root output directory
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    global_checkpoint_path = OUTPUT_ROOT / "global_progress.json"

    # Load global checkpoint
    global_checkpoint = load_checkpoint(global_checkpoint_path)
    if global_checkpoint:
        print(f"\n[INFO] Found global checkpoint:")
        print(f"  - Dates processed: {len(global_checkpoint.get('completed_dates', []))}")
        print(f"  - Total crops: {global_checkpoint.get('total_crops', 0):,}")
        resume = input("\nResume from checkpoint? (y/n): ").lower()
        if resume != 'y':
            global_checkpoint = None

    # Get videos and group by date
    print("\n[INFO] Querying General_3 videos from database...")
    videos = get_general3_videos()

    if not videos:
        print("[ERROR] No videos found in database.")
        sys.exit(1)

    # Group videos by recording date (extract date part only)
    dates_dict = defaultdict(list)
    for video in videos:
        # Extract just the date portion (handle various formats)
        date_str = video['recording_date']
        # Get just YYYY-MM-DD part
        date_key = sanitize_date(date_str.split()[0] if ' ' in date_str else date_str)
        dates_dict[date_key].append(video)

    date_keys = sorted(dates_dict.keys())
    print(f"[INFO] Found {len(videos)} videos across {len(date_keys)} dates")

    # Load model
    print(f"\n[INFO] Loading YOLO model: {MODEL_PATH}")
    model = YOLO(MODEL_PATH)
    model.to(DEVICE)
    print_cuda_usage("[MODEL LOADED] ")

    # Initialize global stats
    if global_checkpoint:
        total_crops = global_checkpoint.get('total_crops', 0)
        completed_dates = set(global_checkpoint.get('completed_dates', []))
    else:
        total_crops = 0
        completed_dates = set()

    failed_videos = []
    start_time = datetime.now()
    date_stats_all = {}

    # Process each date independently
    for date_idx, date_key in enumerate(date_keys):
        date_videos = dates_dict[date_key]

        # Skip already completed dates
        if date_key in completed_dates:
            print(f"[SKIP] {date_key} already completed")
            continue

        target_remaining = TARGET_TOTAL_IMAGES - total_crops
        if target_remaining <= 0:
            print("\n[INFO] Global target reached!")
            break

        # Create date-specific output directory
        date_output_dir = OUTPUT_ROOT / date_key
        date_output_dir.mkdir(parents=True, exist_ok=True)
        date_checkpoint_dir = date_output_dir / "checkpoints"
        date_checkpoint_dir.mkdir(parents=True, exist_ok=True)
        date_checkpoint_path = date_checkpoint_dir / "progress.json"

        print(f"\n{'='*60}")
        print(f"PROCESSING {date_key} ({date_idx+1}/{len(date_keys)})")
        print(f"  Output: {date_output_dir}")
        print(f"  Videos: {len(date_videos)}")
        print_cuda_usage("  ")
        print(f"{'='*60}")

        # Load date checkpoint
        date_checkpoint = load_checkpoint(date_checkpoint_path)
        if date_checkpoint:
            date_track_stats = defaultdict(int, date_checkpoint.get('track_stats', {}))
            date_crops = date_checkpoint.get('total_crops', 0)
            start_video_idx = date_checkpoint.get('last_video_idx', -1) + 1
        else:
            date_track_stats = defaultdict(int)
            date_crops = 0
            start_video_idx = 0

        # Process each video for this date
        for video_idx in range(start_video_idx, len(date_videos)):
            video_info = date_videos[video_idx]

            try:
                stats = process_video(
                    video_info,
                    model,
                    date_output_dir,
                    date_track_stats,
                    target_remaining - date_crops
                )

                if stats['skipped']:
                    continue

                date_crops += stats['total_crops']
                total_crops += stats['total_crops']

                # Update date track stats
                for track_id, count in stats['crops_per_track'].items():
                    date_track_stats[track_id] += count

                progress = (total_crops / TARGET_TOTAL_IMAGES) * 100
                elapsed = (datetime.now() - start_time).total_seconds() / 3600

                skipped_sim = stats.get('skipped_similar', 0)
                print(
                    f"  [{video_idx+1}/{len(date_videos)}] "
                    f"+{stats['total_crops']} crops "
                    f"(-{skipped_sim} dupes) | "
                    f"Date: {date_crops:,} | "
                    f"Global: {total_crops:,} ({progress:.1f}%) | "
                    f"{elapsed:.1f}h"
                )

                # Save date checkpoint every 3 videos
                if video_idx % 3 == 0:
                    save_checkpoint(date_checkpoint_path, {
                        'date': date_key,
                        'total_crops': date_crops,
                        'track_stats': dict(date_track_stats),
                        'last_video_idx': video_idx,
                        'timestamp': datetime.now().isoformat()
                    })

            except Exception as e:
                failed_videos.append((video_info['path'], str(e)))
                print(f"  [FAIL] {video_info['path']}: {e}")

        # Mark date as complete
        completed_dates.add(date_key)
        date_stats_all[date_key] = {
            'total_crops': date_crops,
            'unique_ids': len(date_track_stats),
            'track_stats': dict(date_track_stats)
        }

        # Save date final stats
        save_checkpoint(date_checkpoint_path, {
            'date': date_key,
            'total_crops': date_crops,
            'track_stats': dict(date_track_stats),
            'completed': True,
            'timestamp': datetime.now().isoformat()
        })

        # Save global checkpoint after each date
        save_checkpoint(global_checkpoint_path, {
            'total_crops': total_crops,
            'completed_dates': list(completed_dates),
            'date_stats': date_stats_all,
            'timestamp': datetime.now().isoformat()
        })

        # Print CUDA usage after each date
        print_cuda_usage(f"  [{date_key} DONE] ")

    # Final summary
    print("\n" + "=" * 80)
    print("PROCESSING COMPLETE")
    print("=" * 80)
    total_time = (datetime.now() - start_time).total_seconds() / 3600
    print(f"Total crops: {total_crops:,}")
    print(f"Dates processed: {len(completed_dates)}")
    print(f"Failed videos: {len(failed_videos)}")
    print(f"Total time: {total_time:.2f} hours")
    print_cuda_usage("[FINAL] ")

    # Save final dataset stats
    final_stats_path = OUTPUT_ROOT / "dataset_stats.json"
    save_checkpoint(final_stats_path, {
        'total_crops': total_crops,
        'dates_completed': len(completed_dates),
        'date_stats': date_stats_all,
        'failed_videos': failed_videos,
        'processing_time_hours': total_time,
        'config': {
            'frame_skip': FRAME_SKIP,
            'confidence_threshold': CONFIDENCE_THRESHOLD,
            'min_area': MIN_AREA_PIXELS,
            'blur_threshold': BLUR_THRESHOLD,
            'min_frame_gap': MIN_FRAME_GAP,
            'similarity_threshold': SIMILARITY_THRESHOLD,
            'max_per_class': MAX_IMAGES_PER_CLASS,
            'max_per_track_per_video': MAX_SAMPLES_PER_TRACK_PER_VIDEO
        },
        'timestamp': datetime.now().isoformat()
    })
    print(f"\nDataset stats saved to: {final_stats_path}")

if __name__ == "__main__":
    main()