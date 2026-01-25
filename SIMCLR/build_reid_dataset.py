"""
build_reid_dataset.py - Ultimate Person Re-ID Dataset Builder for StrongSORT

Optimized for:
- 546 hours of surgical videos (123 cases)
- General_3 camera (consistent viewpoint)
- Person Re-Identification task
- RTX A2000 12GB GPU
- Multi-day processing capability
- YOLO26 (43% faster CPU inference with NMS-free architecture)

Target: 60,000 high-quality person crops with excellent temporal diversity
"""

import math
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

# ============================================================================
# CONFIGURATION - OPTIMIZED FOR RE-ID
# ============================================================================
# YOLO26 is 43% faster on CPU with better accuracy
# Use yolo26m-pose.pt for medium model (recommended)
# Or yolo26l-pose.pt for higher accuracy
MODEL_PATH = r"F:\YOLO_Models\yolo26m-pose.pt"
OUTPUT_DIR = Path("F:/Room_8_Data/SIMCLR/dataset/simclr_reid_60k")
CHECKPOINT_DIR = OUTPUT_DIR / "checkpoints"

# Dataset targets
TARGET_TOTAL_IMAGES = 9999999
MIN_IMAGES_PER_CLASS = 150
MAX_IMAGES_PER_CLASS = 800

# Quality filters - HIGH for Re-ID
CONFIDENCE_THRESHOLD = 0.92
MIN_AREA_PIXELS = 9000
BLUR_THRESHOLD = 110
BRIGHTNESS_MIN = 25
BRIGHTNESS_MAX = 230
ASPECT_RATIO_MIN = 1.3
ASPECT_RATIO_MAX = 3.3

# Temporal diversity - CRITICAL for Re-ID
MIN_FRAME_GAP = 60              # 2 seconds
MOVEMENT_THRESHOLD_PX = 70

# Processing optimization
FRAME_SKIP = 10                 # Every 10th frame
MAX_SAMPLES_PER_TRACK_PER_VIDEO = 80

# Crop settings
PADDING_PIXELS = 30

# Device configuration
DEVICE = 'cuda' if torch.cuda.is_available() else 'cpu'

PERSON_CLASS_ID = 0


def get_general3_videos() -> list[dict]:
    """Query database for General_3 videos, sorted by case."""
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
    """Sanitize date string for filenames."""
    return date_str.replace("/", "-").replace("\\", "-").replace(" ", "_")


def crop_with_padding(frame: np.ndarray, bbox: tuple, padding: int = PADDING_PIXELS) -> np.ndarray:
    """Crop with padding."""
    h, w = frame.shape[:2]
    x1, y1, x2, y2 = map(int, bbox)

    x1 = max(0, x1 - padding)
    y1 = max(0, y1 - padding)
    x2 = min(w, x2 + padding)
    y2 = min(h, y2 + padding)

    return frame[y1:y2, x1:x2]


def compute_blur_score(image: np.ndarray) -> float:
    """Compute blur score using Variance of Laplacian."""
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if len(image.shape) == 3 else image
    return cv2.Laplacian(gray, cv2.CV_64F).var()


def compute_brightness_score(image: np.ndarray) -> float:
    """Compute brightness score."""
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if len(image.shape) == 3 else image
    return gray.mean()


def compute_sharpness_score(image: np.ndarray) -> float:
    """Compute sharpness using gradient magnitude."""
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if len(image.shape) == 3 else image
    gx = cv2.Sobel(gray, cv2.CV_64F, 1, 0, ksize=3)
    gy = cv2.Sobel(gray, cv2.CV_64F, 0, 1, ksize=3)
    magnitude = np.sqrt(gx**2 + gy**2)
    return magnitude.mean()


def save_checkpoint(checkpoint_path: Path, data: dict):
    """Save processing checkpoint."""
    with open(checkpoint_path, 'w') as f:
        json.dump(data, f, indent=2)


def load_checkpoint(checkpoint_path: Path) -> dict:
    """Load processing checkpoint."""
    if checkpoint_path.exists():
        with open(checkpoint_path, 'r') as f:
            return json.load(f)
    return None


def process_video(
    video_info: dict,
    model: YOLO,
    output_dir: Path,
    global_track_stats: dict,
    target_remaining: int
) -> dict:
    """
    Process single video with Re-ID optimized sampling.
    
    Returns:
        dict with processing stats
    """
    video_path = video_info["path"]
    date_str = sanitize_date(video_info["recording_date"])
    case_no = video_info["case_no"]

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise IOError(f"Cannot open video: {video_path}")

    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    fps = cap.get(cv2.CAP_PROP_FPS)

    if target_remaining <= 0:
        cap.release()
        return {'total_crops': 0, 'crops_per_track': {}, 'frames_processed': 0, 'skipped': True}

    crops_saved = 0
    frame_num = 0
    frames_processed = 0

    # Track statistics
    track_stats = defaultdict(lambda: {
        'count': 0,
        'last_saved_frame': -1000,
        'last_position': None,
        'quality_scores': []
    })

    pbar = tqdm(
        total=total_frames // FRAME_SKIP,
        desc=f"  Case{case_no:3d}",
        unit="frm",
        leave=False,
        ncols=100
    )

    while True:
        ret, frame = cap.read()
        if not ret:
            break

        # Skip frames
        if frame_num % FRAME_SKIP != 0:
            frame_num += 1
            continue

        frames_processed += 1

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

                # Check global and local limits
                global_count = global_track_stats.get(track_id, 0)
                local_count = track_stats[track_id]['count']

                if global_count >= MAX_IMAGES_PER_CLASS:
                    continue

                if local_count >= MAX_SAMPLES_PER_TRACK_PER_VIDEO:
                    continue

                # Temporal diversity
                frames_since_last = frame_num - track_stats[track_id]['last_saved_frame']
                if frames_since_last < MIN_FRAME_GAP:
                    continue

                # Confidence filter
                conf = float(box.conf[0]) if box.conf is not None else 0.0
                if conf <= CONFIDENCE_THRESHOLD:
                    continue

                # Bounding box
                xyxy = box.xyxy[0].cpu().numpy()
                x1, y1, x2, y2 = xyxy
                width = x2 - x1
                height = y2 - y1
                area = width * height

                # Area filter
                if area < MIN_AREA_PIXELS:
                    continue

                # Aspect ratio filter
                aspect_ratio = height / width if width > 0 else 0
                if aspect_ratio < ASPECT_RATIO_MIN or aspect_ratio > ASPECT_RATIO_MAX:
                    continue

                # Center position
                center_x = (x1 + x2) / 2
                center_y = (y1 + y2) / 2

                # Movement filter
                if track_stats[track_id]['last_position'] is not None:
                    last_x, last_y = track_stats[track_id]['last_position']
                    distance = math.sqrt((center_x - last_x) ** 2 + (center_y - last_y) ** 2)
                    if distance < MOVEMENT_THRESHOLD_PX:
                        continue

                # Crop
                crop = crop_with_padding(frame, (x1, y1, x2, y2), PADDING_PIXELS)

                if crop.size == 0 or crop.shape[0] < 10 or crop.shape[1] < 10:
                    continue

                # Quality filters
                blur_score = compute_blur_score(crop)
                if blur_score < BLUR_THRESHOLD:
                    continue

                brightness = compute_brightness_score(crop)
                if brightness < BRIGHTNESS_MIN or brightness > BRIGHTNESS_MAX:
                    continue

                sharpness = compute_sharpness_score(crop)

                # Combined quality score (for Re-ID)
                # Blur is most important, then sharpness, then size
                quality_score = (
                    blur_score * 0.5 +
                    sharpness * 0.3 +
                    (area / 10000) * 0.2  # Normalize area
                )

                # Save crop
                filename = f"Gen3_{date_str}_Case{case_no}_Frame{frame_num}_ID{track_id}.jpg"
                save_path = output_dir / filename

                cv2.imwrite(str(save_path), crop)
                crops_saved += 1

                # Update statistics
                track_stats[track_id]['count'] += 1
                track_stats[track_id]['last_saved_frame'] = frame_num
                track_stats[track_id]['last_position'] = (center_x, center_y)
                track_stats[track_id]['quality_scores'].append(quality_score)

        frame_num += 1
        pbar.update(1)

    pbar.close()
    cap.release()

    # Compute average quality per track
    avg_qualities = {}
    for tid, stats in track_stats.items():
        if stats['quality_scores']:
            avg_qualities[tid] = np.mean(stats['quality_scores'])

    crops_per_track = {tid: stats['count'] for tid, stats in track_stats.items()}

    return {
        'total_crops': crops_saved,
        'crops_per_track': crops_per_track,
        'frames_processed': frames_processed,
        'avg_quality_per_track': avg_qualities,
        'skipped': False
    }


def main():
    """Main entry point with checkpoint support."""
    print("=" * 80)
    print("PERSON RE-ID DATASET BUILDER FOR STRONGSORT")
    print("=" * 80)
    print(f"\nOptimized for:")
    print(f"  • 546 hours of surgical video (123 cases)")
    print(f"  • General_3 camera (consistent viewpoint)")
    print(f"  • Person Re-Identification task")
    print(f"  • RTX A2000 12GB GPU")
    
    print(f"\nTarget Configuration:")
    print(f"  • Total images: {TARGET_TOTAL_IMAGES:,}")
    print(f"  • Per person: {MIN_IMAGES_PER_CLASS}-{MAX_IMAGES_PER_CLASS}")
    print(f"  • Frame gap: {MIN_FRAME_GAP} ({MIN_FRAME_GAP/30:.1f} sec)")
    print(f"  • Quality: Confidence {CONFIDENCE_THRESHOLD}, Blur {BLUR_THRESHOLD}")

    # Create directories
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    CHECKPOINT_DIR.mkdir(parents=True, exist_ok=True)
    checkpoint_path = CHECKPOINT_DIR / "progress.json"

    print(f"\n[INFO] Output directory: {OUTPUT_DIR}")
    print(f"[INFO] Checkpoint: {checkpoint_path}")

    # Load checkpoint if exists
    checkpoint = load_checkpoint(checkpoint_path)
    if checkpoint:
        print(f"\n[INFO] Resuming from checkpoint:")
        print(f"  • Processed videos: {checkpoint['videos_processed']}")
        print(f"  • Total crops: {checkpoint['total_crops']:,}")
        print(f"  • Last case: {checkpoint['last_case']}")
        
        resume = input("\nResume from checkpoint? (y/n): ").lower()
        if resume != 'y':
            checkpoint = None
            print("[INFO] Starting fresh")
    
    # Get videos
    print(f"\n[INFO] Database: {DB_PATH}")
    print("[INFO] Querying General_3 videos...")
    videos = get_general3_videos()

    if not videos:
        print("[ERROR] No General_3 videos found.")
        sys.exit(1)

    # Count unique cases
    unique_cases = len(set(v['case_no'] for v in videos))
    print(f"[INFO] Found {len(videos)} videos across {unique_cases} cases")

    # Load model
    print(f"\n[INFO] Loading YOLO26 model: {MODEL_PATH}")
    print(f"[INFO] Device: {DEVICE.upper()}")
    model = YOLO(MODEL_PATH)
    model.to(DEVICE)
    print("[INFO] YOLO26 loaded successfully")
    print(f"[INFO] Using NMS-free end-to-end inference on {DEVICE.upper()}")

    # Initialize or resume
    if checkpoint:
        total_crops = checkpoint['total_crops']
        successful_videos = checkpoint['videos_processed']
        global_track_stats = defaultdict(int, checkpoint['global_track_stats'])
        start_idx = checkpoint['last_video_idx'] + 1
    else:
        total_crops = 0
        successful_videos = 0
        global_track_stats = defaultdict(int)
        start_idx = 0

    failed_videos = []

    # Process videos
    print("\n" + "-" * 80)
    print(f"Processing Videos (starting from #{start_idx})")
    print("-" * 80)

    start_time = datetime.now()

    for video_idx in range(start_idx, len(videos)):
        video_info = videos[video_idx]
        video_path = video_info["path"]

        target_remaining = TARGET_TOTAL_IMAGES - total_crops

        if target_remaining <= 0:
            print(f"\n[INFO] Target reached! ({total_crops:,}/{TARGET_TOTAL_IMAGES:,})")
            break

        try:
            stats = process_video(
                video_info,
                model,
                OUTPUT_DIR,
                global_track_stats,
                target_remaining
            )

            if stats['skipped']:
                continue

            total_crops += stats['total_crops']
            successful_videos += 1

            # Update global stats
            for track_id, count in stats['crops_per_track'].items():
                global_track_stats[track_id] += count

            progress_pct = (total_crops / TARGET_TOTAL_IMAGES) * 100
            elapsed = (datetime.now() - start_time).total_seconds() / 3600
            
            print(
                f"[{video_idx+1:3d}/{len(videos)}] Case{video_info['case_no']:3d}: "
                f"+{stats['total_crops']:4d} crops ({len(stats['crops_per_track']):2d} IDs) "
                f"[{total_crops:,}/{TARGET_TOTAL_IMAGES:,} = {progress_pct:5.1f}%] "
                f"({elapsed:.1f}h elapsed)"
            )

            # Save checkpoint every 5 videos
            if video_idx % 5 == 0:
                save_checkpoint(checkpoint_path, {
                    'total_crops': total_crops,
                    'videos_processed': successful_videos,
                    'last_video_idx': video_idx,
                    'last_case': video_info['case_no'],
                    'global_track_stats': dict(global_track_stats),
                    'timestamp': datetime.now().isoformat()
                })

        except Exception as e:
            failed_videos.append((video_path, str(e)))
            print(f"[FAIL] Case{video_info['case_no']}: {e}")
            continue

    # Final summary
    elapsed_total = (datetime.now() - start_time).total_seconds() / 3600
    
    print("\n" + "=" * 80)
    print("FINAL SUMMARY")
    print("=" * 80)
    print(f"  Processing time: {elapsed_total:.1f} hours")
    print(f"  Videos processed: {successful_videos}/{len(videos)}")
    print(f"  Total images: {total_crops:,}")
    print(f"  Target achieved: {(total_crops / TARGET_TOTAL_IMAGES) * 100:.1f}%")
    print(f"  Unique persons: {len(global_track_stats)}")

    # Distribution
    if global_track_stats:
        sorted_tracks = sorted(global_track_stats.items(), key=lambda x: x[1], reverse=True)
        
        print(f"\n  Top 20 Person IDs:")
        for i, (track_id, count) in enumerate(sorted_tracks[:20], 1):
            pct = (count / total_crops) * 100
            print(f"    {i:2d}. ID{track_id:3d}: {count:5d} ({pct:5.2f}%)")

        counts = list(global_track_stats.values())
        imbalance = max(counts) / min(counts) if min(counts) > 0 else float('inf')
        
        print(f"\n  Balance Metrics:")
        print(f"    Min: {min(counts)}, Max: {max(counts)}")
        print(f"    Mean: {np.mean(counts):.1f}, Median: {np.median(counts):.1f}")
        print(f"    Imbalance ratio: {imbalance:.2f}x")

    print(f"\n  Output: {OUTPUT_DIR}")
    
    # Next steps
    print("\n" + "-" * 80)
    print("NEXT STEPS")
    print("-" * 80)
    print("  1. Run analyze_dataset.py to verify quality")
    print("  2. Train SimCLR model:")
    print("     - Batch size: 128-192 (on A2000)")
    print("     - Epochs: 200-300")
    print("     - Use strong augmentations!")
    print("  3. Extract embeddings and integrate with StrongSORT")
    print("  4. Expected Re-ID improvement: 15-30%")

    if failed_videos:
        print(f"\n  Failed ({len(failed_videos)}):")
        for path, err in failed_videos[:5]:
            print(f"    • {Path(path).name}: {err}")

    print("\n[DONE]")


if __name__ == "__main__":
    main()
