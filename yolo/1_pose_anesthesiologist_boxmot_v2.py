"""
Multi-Person Pose Detection using YOLOv8 Pose with BoxMOT StrongSORT + OSNet ReID

Features:
- Tracks ALL persons in the video using BoxMOT library
- Uses OSNet ReID model for better re-identification after occlusions
- More accurate person tracking via skeletal keypoints
- Better handling of occlusions and partial visibility
- Robust tracking with StrongSORT (handles occlusions, re-identification)
- Persistent track IDs across the entire video
- Outputs parquet file with keypoints for all tracks

Requirements:
- pip install ultralytics opencv-python numpy pandas pyarrow boxmot
"""

import sys
import os
from pathlib import Path
import tempfile
import time
import subprocess
import json

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cv2
import numpy as np
import torch
from tqdm import tqdm
import pandas as pd

# Import YOLO from ultralytics
try:
    from ultralytics import YOLO
except ImportError:
    print("=" * 70)
    print("ERROR: Ultralytics YOLO not installed!")
    print("=" * 70)
    print("\nPlease install with:")
    print("  pip install ultralytics")
    print("=" * 70)
    sys.exit(1)

# Import BoxMOT
try:
    from boxmot import StrongSort
except ImportError:
    print("=" * 70)
    print("ERROR: BoxMOT not installed!")
    print("=" * 70)
    print("\nPlease install with:")
    print("  pip install boxmot")
    print("=" * 70)
    sys.exit(1)

try:
    from config import MP4_ROOT
except ImportError:
    MP4_ROOT = None


# =============================================================================
# LOAD CONFIGURATION
# =============================================================================
def load_config():
    """Load configuration from 0_yolo_config.json"""
    config_path = os.path.join(os.path.dirname(__file__), "0_yolo_config.json")

    with open(config_path, 'r') as f:
        config = json.load(f)

    # Ensure keys exist
    if "yolo" not in config:
        raise ValueError(
            "yolo configuration not found in 0_yolo_config.json. "
            "Please add 'yolo' section to the config file."
        )

    if "tracking" not in config:
        raise ValueError(
            "tracking configuration not found in 0_yolo_config.json. "
            "Please add 'tracking' section to the config file."
        )

    # Ensure StrongSort config exists (should be in config file)
    if "strongsort" not in config:
        raise ValueError(
            "StrongSort configuration not found in 0_yolo_config.json. "
            "Please add 'strongsort' section to the config file."
        )

    return config


# Load configuration
CONFIG = load_config()

# Device
DEVICE = "cuda" if CONFIG["device"]["use_cuda"] and torch.cuda.is_available() else "cpu"

# COCO 17 keypoint names (in order)
KEYPOINT_NAMES = [
    "Nose", "Left_Eye", "Right_Eye", "Left_Ear", "Right_Ear",
    "Left_Shoulder", "Right_Shoulder", "Left_Elbow", "Right_Elbow",
    "Left_Wrist", "Right_Wrist", "Left_Hip", "Right_Hip",
    "Left_Knee", "Right_Knee", "Left_Ankle", "Right_Ankle"
]

# =============================================================================
# MULTI-PERSON TRACKING (ALL TRACKS)
# =============================================================================
def setup_device():
    """Setup device and display GPU info."""
    print("\n" + "=" * 70)
    print("GPU SETUP")
    print("=" * 70)

    if not torch.cuda.is_available():
        print("WARNING: CUDA is not available!")
        print("CPU mode will be slower.")
        print("=" * 70)
        return torch.device("cpu")

    device = torch.device("cuda")
    print(f"CUDA Available: YES")
    print(f"GPU Device: {torch.cuda.get_device_name(0)}")
    print("=" * 70)
    return device


def repair_video(video_path):
    """
    Repair potentially corrupted video using ffmpeg before processing.
    Returns path to repaired video (or original if repair not needed).
    """
    print("\n" + "=" * 70)
    print("VIDEO INTEGRITY CHECK")
    print("=" * 70)

    # Create temporary repaired video
    repaired_path = tempfile.mktemp(suffix=".mp4", prefix="repaired_")

    ffmpeg_cmd = [
        "ffmpeg", "-y", "-loglevel", "warning",
        "-i", video_path,
        "-c:v", "h264_nvenc", "-preset", "fast", "-cq", "18",
        "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart",
        "-vsync", "cfr",  # Constant frame rate
        "-r", "30",  # Force 30 fps
        repaired_path
    ]

    print("Repairing video with ffmpeg...")
    result = subprocess.run(ffmpeg_cmd, capture_output=True, text=True,
                          creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)

    if result.returncode != 0:
        print(f"FFmpeg repair failed. Continuing with original video...")
        return video_path, None

    return repaired_path, repaired_path


def pose_anesthesiologist_strongsort(video_path, output_path=None):
    """
    Detect and track ALL persons from video using YOLOv8 pose detection with BoxMOT StrongSORT + OSNet tracking.
    Saves keypoint data for all detected persons to a parquet file.
    """
    # Setup device
    device = setup_device()

    # Repair video first to fix any corruption
    repaired_temp_file = None
    if CONFIG["video"].get("auto_repair", True):
        video_path, repaired_temp_file = repair_video(video_path)

    # Initialize YOLO model
    print("\n" + "=" * 70)
    print("INITIALIZING YOLO MODEL")
    print("=" * 70)

    # Set custom model directory if specified
    model_dir = CONFIG['yolo'].get('model_dir')
    if model_dir:
        os.makedirs(model_dir, exist_ok=True)
        model_path = os.path.join(model_dir, CONFIG['yolo']['model'])
    else:
        model_path = CONFIG['yolo']['model']

    try:
        model = YOLO(model_path)
        model.to(DEVICE)
        print("Model loaded successfully!")
    except Exception as e:
        print(f"ERROR: Failed to load YOLO model: {e}")
        sys.exit(1)

    print("=" * 70)

    # Initialize BoxMOT tracker
    print("\n" + "=" * 70)
    print("INITIALIZING BOXMOT TRACKER")
    print("=" * 70)

    # Get ReID model path
    reid_model_path = os.path.join(
        CONFIG['strongsort']['reid_model_dir'],
        CONFIG['strongsort']['reid_model']
    )

    print(f"ReID Model: {reid_model_path}")

    # Create tracker
    # BoxMOT expects device as integer (0) not "cuda"
    device_for_boxmot = 0 if DEVICE == "cuda" else "cpu"

    # StrongSort parameters from config
    tracker = StrongSort(
        reid_weights=Path(reid_model_path),
        device=device_for_boxmot,
        half=CONFIG['yolo'].get('use_half_precision', True) and DEVICE == "cuda",
        per_class=False,  # Track all classes together
        min_conf=CONFIG['strongsort'].get('min_conf', 0.1),  # Minimum confidence threshold
        max_cos_dist=CONFIG['strongsort'].get('max_cos_dist', 0.2),  # Max cosine distance (appearance)
        max_iou_dist=CONFIG['strongsort'].get('max_iou_dist', 0.7),  # Max IoU distance (motion)
        max_age=CONFIG['strongsort'].get('max_age', 30),  # How long to keep lost tracks
        n_init=CONFIG['strongsort'].get('n_init', 3),  # Number of frames to confirm a track
        nn_budget=CONFIG['strongsort'].get('nn_budget', 100),  # Nearest neighbor budget
        mc_lambda=CONFIG['strongsort'].get('mc_lambda', 0.98),  # Motion compensation lambda
        ema_alpha=CONFIG['strongsort'].get('ema_alpha', 0.9)  # Exponential moving average alpha
    )

    print("BoxMOT StrongSORT tracker initialized!")
    print("=" * 70)

    # Open video
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise ValueError(f"Could not open video: {video_path}")

    # Get video properties
    fps = cap.get(cv2.CAP_PROP_FPS)
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))

    print(f"Resolution: {width}x{height}")
    print(f"FPS: {fps:.2f}")
    print(f"Frame Count: {frame_count}")

    # Determine output path
    if output_path is None:
        video_dir = os.path.dirname(video_path) or "."
        video_name = os.path.splitext(os.path.basename(video_path))[0]
        output_path = os.path.join(video_dir, f"{video_name}_mask.mp4")

    # Create parquet file path
    parquet_path = output_path.replace('.mp4', '_keypoints_strongsort.parquet')

    # Prepare DataFrame columns
    df_columns = ['Frame_ID', 'Timestamp', 'Track_ID']
    for keypoint_name in KEYPOINT_NAMES:
        df_columns.extend([f'{keypoint_name}_x', f'{keypoint_name}_y', f'{keypoint_name}_conf'])

    data_rows = []

    # TARGET FPS: Sample at 30 FPS (or whatever is set)
    TARGET_FPS = 30

    # Calculate frame skip interval for SAVING (not processing)
    frame_interval = max(1, int(fps / TARGET_FPS))

    try:
        print("\n" + "=" * 70)
        print("PROCESSING VIDEO - BOXMOT MULTI-PERSON TRACKING (ALL TRACKS)")
        print("=" * 70)
        print(f"Model: {CONFIG['yolo']['model']}")
        print(f"Tracker: BoxMOT StrongSORT")
        print(f"ReID Model: {CONFIG['strongsort']['reid_model']}")
        print(f"Sampling Rate: Saving data every {frame_interval} frames (approx {TARGET_FPS} FPS)")
        print(f"NOTE: Processing EVERY frame to ensure tracking consistency.")
        print()

        cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
        total_detections = 0
        unique_track_ids = set()
        start_time = time.time()

        # IMPORTANT: We process TOTAL frames now, not just sampled ones
        with tqdm(total=frame_count, desc="Processing", unit="frame") as pbar:
            frame_idx = 0
            saved_count = 0

            while True:
                ret, frame = cap.read()
                if not ret:
                    break

                # -----------------------------------------------------------------
                # YOLO Detection (without tracking)
                # -----------------------------------------------------------------
                results = model.predict(
                    frame,
                    conf=CONFIG['yolo']['confidence_threshold'],
                    iou=CONFIG['yolo']['iou_threshold'],
                    imgsz=CONFIG['yolo'].get('imgsz', 640),
                    half=CONFIG['yolo'].get('use_half_precision', True) and DEVICE == "cuda",
                    verbose=False
                )

                # -----------------------------------------------------------------
                # BoxMOT Tracking
                # -----------------------------------------------------------------
                # Extract detections for tracker (format: [x1, y1, x2, y2, conf, class])
                if results[0].boxes is not None and len(results[0].boxes) > 0:
                    boxes = results[0].boxes.xyxy.cpu().numpy()
                    scores = results[0].boxes.conf.cpu().numpy()
                    classes = results[0].boxes.cls.cpu().numpy()

                    # Only track persons (class 0)
                    person_mask = classes == 0
                    if person_mask.any():
                        dets = np.column_stack([
                            boxes[person_mask],
                            scores[person_mask],
                            classes[person_mask]
                        ])

                        # Update tracker
                        tracks = tracker.update(dets, frame)

                        # -----------------------------------------------------------------
                        # SAVE DATA: Only if we match the interval
                        # -----------------------------------------------------------------
                        if frame_idx % frame_interval == 0 and len(tracks) > 0:
                            # tracks format: [x1, y1, x2, y2, track_id, conf, cls, ...]

                            for track in tracks:
                                track_id = int(track[4])  # Track ID is at index 4
                                unique_track_ids.add(track_id)

                                # Find matching detection to get keypoints
                                # Convert track bbox to center format for matching
                                track_bbox = track[:4]

                                # Find closest matching detection
                                best_match_idx = None
                                best_iou = 0

                                for idx in range(len(results[0].boxes)):
                                    if results[0].boxes.cls[idx].cpu().item() == 0:  # Person class
                                        det_bbox = results[0].boxes.xyxy[idx].cpu().numpy()

                                        # Calculate IoU
                                        x1 = max(track_bbox[0], det_bbox[0])
                                        y1 = max(track_bbox[1], det_bbox[1])
                                        x2 = min(track_bbox[2], det_bbox[2])
                                        y2 = min(track_bbox[3], det_bbox[3])

                                        if x2 > x1 and y2 > y1:
                                            inter_area = (x2 - x1) * (y2 - y1)
                                            track_area = (track_bbox[2] - track_bbox[0]) * (track_bbox[3] - track_bbox[1])
                                            det_area = (det_bbox[2] - det_bbox[0]) * (det_bbox[3] - det_bbox[1])
                                            iou = inter_area / (track_area + det_area - inter_area)

                                            if iou > best_iou:
                                                best_iou = iou
                                                best_match_idx = idx

                                # Extract keypoints for matched detection
                                if best_match_idx is not None and results[0].keypoints is not None:
                                    keypoints_xy = results[0].keypoints.xy[best_match_idx].cpu().numpy()
                                    keypoints_conf = results[0].keypoints.conf[best_match_idx].cpu().numpy()

                                    # Add to data rows
                                    row = [frame_idx, frame_idx / fps, track_id]
                                    for i in range(17):
                                        if i < len(keypoints_xy):
                                            row.extend([
                                                float(keypoints_xy[i][0]),
                                                float(keypoints_xy[i][1]),
                                                float(keypoints_conf[i])
                                            ])
                                        else:
                                            row.extend([np.nan, np.nan, 0.0])
                                    data_rows.append(row)
                                    total_detections += 1

                            saved_count += 1

                frame_idx += 1
                pbar.update(1)

        cap.release()

        # Create DataFrame and save as parquet
        print("\n" + "=" * 70)
        print("SAVING PARQUET FILE")
        print("=" * 70)

        df = pd.DataFrame(data_rows, columns=df_columns)
        df.to_parquet(parquet_path, index=False)

        processing_time = time.time() - start_time
        processing_fps = frame_idx / processing_time

        print(f"\nProcessing complete!")
        print(f"  Time: {processing_time:.1f} seconds")
        print(f"  Speed: {processing_fps:.1f} FPS (Inference speed)")
        print(f"  Frames Processed: {frame_idx}")
        print(f"  Frames Saved: {saved_count}")
        print(f"  Unique persons tracked: {len(unique_track_ids)}")

        if len(unique_track_ids) > 0:
            track_ids_sorted = sorted(list(unique_track_ids))
            print(f"  Track IDs: {track_ids_sorted}")

        # Remove repaired video if it was created
        if repaired_temp_file and os.path.exists(repaired_temp_file):
            os.remove(repaired_temp_file)
            print(f"Removed temporary repaired video")

        return parquet_path

    except Exception as e:
        print(f"\nERROR during processing: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


def main():
    """Main entry point for the script."""
    print("\n" + "=" * 70)
    print("MULTI-PERSON POSE DETECTION - YOLOv8-Pose + BoxMOT StrongSORT")
    print("=" * 70)
    print(f"Model: {CONFIG['yolo']['model']}")
    print(f"Tracker: BoxMOT StrongSORT")
    print(f"ReID Model: {CONFIG['strongsort']['reid_model']}")
    print("=" * 70)

    # Get input video path
    video_path = CONFIG["video"]["input_path"]

    if len(sys.argv) > 1:
        video_path = sys.argv[1]
    elif not video_path:
        default_dir = CONFIG["video"]["default_input_dir"] or MP4_ROOT or "."
        video_path = input("\nVideo path: ").strip()

    video_path = video_path.strip('"').strip("'")

    if not video_path or not os.path.exists(video_path):
        print(f"ERROR: Video file not found: {video_path}")
        sys.exit(1)

    # Get output path
    output_path = CONFIG["video"]["output_path"]
    if len(sys.argv) > 2:
        output_path = sys.argv[2].strip('"').strip("'")
    if not output_path:
        output_path = None

    # Run pose detection
    try:
        result_path = pose_anesthesiologist_strongsort(video_path, output_path)
        print("\nSUCCESS!")
        print(f"\nParquet file saved to: {result_path}")

    except KeyboardInterrupt:
        print("\n\nPose detection interrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n\nERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
