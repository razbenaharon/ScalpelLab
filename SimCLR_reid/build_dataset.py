"""
build_dataset.py - Burst-Mode Person Dataset Builder for SimCLR Training
(Final Version v3: Includes Edge Rejection, Unique Filenames, and Burst Abort)

================================================================================
PURPOSE
================================================================================
This script builds a person re-identification dataset from surgical room videos
using a burst-capture strategy designed for SimCLR contrastive learning.

The burst approach captures micro-motion sequences (positive pairs) that teach
the model stability - the same person in slightly different poses/positions.

================================================================================
CAPTURE STRATEGY
================================================================================
1. DETECTION: Run YOLO frame-by-frame to detect class 'person'

2. BURST MODE: When person detected with high confidence (>0.80):
   - Capture 3 images total (the burst)
   - Skip 20 frames (~0.8s @ 25fps) between each capture

   QUALITY CONTROLS (CRITICAL):
   - Edge Rejection: If the person touches the left/right frame edges, ABORT.
     (Prevents saving "half-bodies" or just shoulders entering the frame).
   - Loss Check: If detection is lost during burst, ABORT.
     (Prevents saving empty backgrounds).

3. COOL DOWN: After completing a 3-image burst (or aborting one):
   - Skip 750 frames (~30 seconds @ 25fps) before detecting again
   - Goal: Prevent data redundancy, ensure temporal diversity

================================================================================
OUTPUT STRUCTURE
================================================================================
OUTPUT_ROOT/
├── {CaseID}_v{VideoIdx}_{FrameID}.jpg      # Unique per case, video, and frame
└── ...

================================================================================
"""

import sys
import sqlite3
from pathlib import Path
import json
from datetime import datetime

import cv2
import numpy as np
from tqdm import tqdm
from ultralytics import YOLO
import torch

# Add parent directory to path for config imports
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
try:
    from config import DB_PATH, MP4_ROOT
except ImportError:
    # Fallback/Default paths if config.py is missing
    DB_PATH = "DB/surgery_data.db"
    MP4_ROOT = "F:/"


# ============================================================================
# CONFIGURATION
# ============================================================================

# Model path
MODEL_PATH = r"F:\YOLO_Models\yolo26m-pose.pt"

# Output directory
OUTPUT_ROOT = Path("F:/Room_8_Data/SIMCLR/dataset/simclr_burst_v3_cleaned")

# Detection settings
CONFIDENCE_THRESHOLD = 0.80     # Minimum confidence to trigger burst
PERSON_CLASS_ID = 0             # YOLO class ID for 'person'
EDGE_MARGIN = 15                # Pixels from edge to consider "touching the edge"

# Burst mode settings
BURST_SIZE = 3                  # Number of images per burst
BURST_FRAME_GAP = 20            # Frames to skip between burst captures (~0.8s @ 25fps)

# Cool down settings
COOLDOWN_FRAMES = 300           # Frames to skip after burst (~10sec )

# Crop settings
PADDING_PIXELS = 30             # Padding around detected bounding box

# Device configuration
DEVICE = 'cuda' if torch.cuda.is_available() else 'cpu'


# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

def print_cuda_usage(prefix: str = "") -> None:
    """Print current CUDA memory usage for monitoring."""
    if not torch.cuda.is_available():
        print(f"{prefix}CUDA: Not available (using CPU)")
        return

    allocated = torch.cuda.memory_allocated() / 1024**3
    reserved = torch.cuda.memory_reserved() / 1024**3
    device_name = torch.cuda.get_device_name(0)
    total_memory = torch.cuda.get_device_properties(0).total_memory / 1024**3

    print(f"{prefix}CUDA [{device_name}]: "
          f"Alloc: {allocated:.2f}GB | "
          f"Reserved: {reserved:.2f}GB | "
          f"Total: {total_memory:.1f}GB")


def get_general3_videos() -> list[dict]:
    """
    Query database for General_3 camera videos.
    Returns: List of dicts with 'path', 'recording_date', 'case_no' keys.
    """
    if not Path(DB_PATH).exists():
        print(f"[ERROR] Database not found at {DB_PATH}")
        return []

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
        # Handle relative paths if necessary
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


def crop_with_padding(frame: np.ndarray, bbox: tuple, padding: int = PADDING_PIXELS) -> np.ndarray:
    """Extract a crop from frame with padding around bounding box."""
    h, w = frame.shape[:2]
    x1, y1, x2, y2 = map(int, bbox)

    # Apply padding, clamped to frame boundaries
    x1 = max(0, x1 - padding)
    y1 = max(0, y1 - padding)
    x2 = min(w, x2 + padding)
    y2 = min(h, y2 + padding)

    return frame[y1:y2, x1:x2]


def detect_person(model: YOLO, frame: np.ndarray) -> tuple | None:
    """Run YOLO detection to find a person with high confidence."""
    results = model(frame, classes=[PERSON_CLASS_ID], verbose=False, device=DEVICE)

    if results and len(results) > 0 and results[0].boxes is not None:
        boxes = results[0].boxes

        # Find highest confidence person detection
        best_conf = 0
        best_bbox = None

        for box in boxes:
            conf = float(box.conf[0]) if box.conf is not None else 0.0
            if conf > best_conf and conf >= CONFIDENCE_THRESHOLD:
                best_conf = conf
                best_bbox = box.xyxy[0].cpu().numpy()

        return best_bbox

    return None


def is_touching_edges(bbox: tuple, frame_width: int, margin: int = EDGE_MARGIN) -> bool:
    """Check if the bounding box is touching the left or right edges of the frame."""
    x1, _, x2, _ = bbox
    # Touching left edge OR touching right edge
    if x1 < margin or x2 > (frame_width - margin):
        return True
    return False


def save_checkpoint(checkpoint_path: Path, data: dict) -> None:
    with open(checkpoint_path, 'w') as f:
        json.dump(data, f, indent=2)


def load_checkpoint(checkpoint_path: Path) -> dict | None:
    if checkpoint_path.exists():
        with open(checkpoint_path, 'r') as f:
            return json.load(f)
    return None


# ============================================================================
# MAIN PROCESSING FUNCTION
# ============================================================================

def process_video(video_info: dict, video_idx: int, model: YOLO, output_dir: Path) -> dict:
    """
    Process a single video using burst-mode capture strategy with QC checks.
    """
    video_path = video_info["path"]
    case_no = video_info["case_no"]

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise IOError(f"Cannot open video: {video_path}")

    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    frame_width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))

    # Statistics
    crops_saved = 0
    bursts_completed = 0
    bursts_aborted = 0

    # State machine variables
    frame_num = 0
    state = "SCANNING"      # States: SCANNING, BURST, COOLDOWN
    burst_count = 0         # Images captured in current burst
    cooldown_remaining = 0  # Frames remaining in cooldown
    burst_skip_remaining = 0  # Frames to skip between burst captures

    pbar = tqdm(
        total=total_frames,
        desc=f"  Case {case_no:03d}_v{video_idx:02d}",
        unit="frm",
        leave=False,
        ncols=100
    )

    while True:
        # =====================================================================
        # FRAME ACQUISITION
        # =====================================================================
        grabbed = cap.grab()
        if not grabbed:
            break

        pbar.update(1)

        # =====================================================================
        # STATE: COOLDOWN
        # =====================================================================
        if state == "COOLDOWN":
            cooldown_remaining -= 1
            if cooldown_remaining <= 0:
                state = "SCANNING"
            frame_num += 1
            continue

        # =====================================================================
        # STATE: BURST
        # =====================================================================
        if state == "BURST":
            if burst_skip_remaining > 0:
                burst_skip_remaining -= 1
                frame_num += 1
                continue

            ret, frame = cap.retrieve()
            if not ret: break

            # 1. Re-detect
            bbox = detect_person(model, frame)

            # 2. QC: Check if lost
            if bbox is None:
                state = "COOLDOWN"
                cooldown_remaining = COOLDOWN_FRAMES
                burst_count = 0
                bursts_aborted += 1
                frame_num += 1
                continue

            # 3. QC: Check edges (Edge Rejection)
            if is_touching_edges(bbox, frame_width):
                state = "COOLDOWN"
                cooldown_remaining = COOLDOWN_FRAMES
                burst_count = 0
                bursts_aborted += 1
                frame_num += 1
                continue

            # 4. Save
            crop = crop_with_padding(frame, bbox)
            if crop.size > 0 and crop.shape[0] >= 10 and crop.shape[1] >= 10:
                filename = f"{case_no}_v{video_idx:02d}_{frame_num:06d}.jpg"
                cv2.imwrite(str(output_dir / filename), crop)
                crops_saved += 1

            burst_count += 1

            if burst_count >= BURST_SIZE:
                bursts_completed += 1
                state = "COOLDOWN"
                cooldown_remaining = COOLDOWN_FRAMES
                burst_count = 0
            else:
                burst_skip_remaining = BURST_FRAME_GAP

            frame_num += 1
            continue

        # =====================================================================
        # STATE: SCANNING
        # =====================================================================
        ret, frame = cap.retrieve()
        if not ret: break

        # 1. Detect
        bbox = detect_person(model, frame)

        if bbox is not None:
            # 2. QC: Check edges BEFORE starting burst
            if not is_touching_edges(bbox, frame_width):

                # Start Burst
                crop = crop_with_padding(frame, bbox)
                if crop.size > 0 and crop.shape[0] >= 10 and crop.shape[1] >= 10:
                    filename = f"{case_no}_v{video_idx:02d}_{frame_num:06d}.jpg"
                    cv2.imwrite(str(output_dir / filename), crop)
                    crops_saved += 1

                    state = "BURST"
                    burst_count = 1
                    burst_skip_remaining = BURST_FRAME_GAP

        frame_num += 1

    pbar.close()
    cap.release()

    return {
        'crops_saved': crops_saved,
        'bursts_completed': bursts_completed,
        'bursts_aborted': bursts_aborted,
        'frames_processed': frame_num,
        'fps': fps
    }


# ============================================================================
# MAIN ENTRY POINT
# ============================================================================

def main():
    print("=" * 70)
    print("BURST-MODE DATASET BUILDER (v3: Edge Rejection & Clean)")
    print("=" * 70)
    print(f"\nConfiguration:")
    print(f"  Model:              {MODEL_PATH}")
    print(f"  Output:             {OUTPUT_ROOT}")
    print(f"  Edge Margin:        {EDGE_MARGIN} pixels")
    print(f"  Burst Config:       {BURST_SIZE} images, {BURST_FRAME_GAP} gap")
    print(f"  Device:             {DEVICE}")

    print("\n" + "-" * 40)
    print_cuda_usage("[STARTUP] ")
    print("-" * 40)

    # Output setup
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    checkpoint_path = OUTPUT_ROOT / "progress.json"

    # Checkpoint logic
    checkpoint = load_checkpoint(checkpoint_path)
    if checkpoint:
        print(f"\n[INFO] Found checkpoint: {checkpoint.get('videos_processed', 0)} videos processed.")
        if input("Resume? (y/n): ").lower() != 'y':
            checkpoint = None

    # Get videos
    print("\n[INFO] Querying database...")
    videos = get_general3_videos()
    if not videos:
        print("[ERROR] No videos found.")
        sys.exit(1)
    print(f"[INFO] Found {len(videos)} videos.")

    # Load Model
    print(f"\n[INFO] Loading YOLO...")
    model = YOLO(MODEL_PATH)
    model.to(DEVICE)
    print_cuda_usage("[MODEL LOADED] ")

    # Stats init
    if checkpoint:
        total_crops = checkpoint.get('total_crops', 0)
        total_bursts = checkpoint.get('total_bursts', 0)
        start_idx = checkpoint.get('videos_processed', 0)
    else:
        total_crops = 0
        total_bursts = 0
        start_idx = 0

    failed_videos = []
    start_time = datetime.now()

    print(f"\n{'='*70}\nPROCESSING VIDEOS\n{'='*70}\n")

    for idx in range(start_idx, len(videos)):
        video_info = videos[idx]
        try:
            stats = process_video(video_info, idx, model, OUTPUT_ROOT)

            total_crops += stats['crops_saved']
            total_bursts += stats['bursts_completed']

            elapsed = (datetime.now() - start_time).total_seconds() / 60
            print(f"[{idx+1:3d}/{len(videos)}] Case {video_info['case_no']:03d}: "
                  f"+{stats['crops_saved']:3d} crops "
                  f"({stats['bursts_completed']} bursts, {stats['bursts_aborted']} aborted) | "
                  f"Total: {total_crops:,} | {elapsed:.1f}min")

            if (idx + 1) % 5 == 0:
                save_checkpoint(checkpoint_path, {
                    'videos_processed': idx + 1,
                    'total_crops': total_crops,
                    'total_bursts': total_bursts,
                    'timestamp': datetime.now().isoformat()
                })

        except Exception as e:
            failed_videos.append((video_info['path'], str(e)))
            print(f"[FAIL] Case {video_info['case_no']}: {e}")

    # Summary
    print("\n" + "=" * 70)
    print("COMPLETE")
    print(f"Total Crops: {total_crops:,}")
    print(f"Total Bursts: {total_bursts:,}")
    print(f"Failed Videos: {len(failed_videos)}")

    # Save final stats
    save_checkpoint(OUTPUT_ROOT / "dataset_stats.json", {
        'total_crops': total_crops,
        'failed': failed_videos
    })

if __name__ == "__main__":
    main()