"""
Multi-Person Pose Detection using YOLOv8 Pose with BoT-SORT Tracking

FIX APPLIED:
- Runs tracking on EVERY frame to ensure ID persistence (solves "too many unique IDs").
- Saves data only at the specific TARGET_FPS to keep file size manageable.

Features:
- Tracks ALL persons in the video (no manual selection required)
- More accurate person tracking via skeletal keypoints
- Better handling of occlusions and partial visibility
- Faster inference than segmentation models
- Robust tracking with BoT-SORT (handles occlusions, re-identification)
- Persistent track IDs across the entire video
- Outputs parquet file with keypoints for all tracks

Requirements:
- pip install ultralytics opencv-python numpy pandas pyarrow
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


def pose_anesthesiologist_yolo(video_path, output_path=None):
    """
    Detect and track ALL persons from video using YOLOv8 pose detection with BoT-SORT tracking.
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
        os.environ['YOLO_CONFIG_DIR'] = model_dir
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
    parquet_path = output_path.replace('.mp4', '_keypoints.parquet')

    # Prepare DataFrame columns
    df_columns = ['Frame_ID', 'Timestamp', 'Track_ID']
    for keypoint_name in KEYPOINT_NAMES:
        df_columns.extend([f'{keypoint_name}_x', f'{keypoint_name}_y', f'{keypoint_name}_conf'])

    data_rows = []

    # TARGET FPS: Sample at 5 FPS (or whatever is set)
    TARGET_FPS = 30

    # Calculate frame skip interval for SAVING (not processing)
    frame_interval = max(1, int(fps / TARGET_FPS))

    try:
        print("\n" + "=" * 70)
        print("PROCESSING VIDEO - MULTI-PERSON TRACKING (ALL TRACKS)")
        print("=" * 70)
        print(f"Model: {CONFIG['yolo']['model']}")
        print(f"Tracker: {CONFIG['tracking']['tracker']}")
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
                # FIX: ALWAYS TRACK (Do not skip frames before inference)
                # -----------------------------------------------------------------
                # Running tracking on every frame allows the Kalman Filter to
                # correctly predict motion and keep IDs consistent.
                results = model.track(frame,
                                    conf=CONFIG['yolo']['confidence_threshold'],
                                    iou=CONFIG['yolo']['iou_threshold'],
                                    imgsz=CONFIG['yolo'].get('imgsz', 640),
                                    half=CONFIG['yolo'].get('use_half_precision', True) and DEVICE == "cuda",
                                    tracker=CONFIG['tracking']['tracker'],
                                    persist=True,  # Must be True
                                    verbose=False)

                # -----------------------------------------------------------------
                # SAVE DATA: Only if we match the interval
                # -----------------------------------------------------------------
                if frame_idx % frame_interval == 0:

                    # Iterate through all detected objects
                    if results[0].boxes is not None and hasattr(results[0].boxes, 'id') and results[0].boxes.id is not None:
                        boxes_ids = results[0].boxes.id.cpu().numpy()
                        boxes_cls = results[0].boxes.cls.cpu().numpy()

                        # Process each detected person
                        for idx, (track_id, cls) in enumerate(zip(boxes_ids, boxes_cls)):
                            # Only process persons (class 0)
                            if int(cls) == 0:
                                track_id = int(track_id)
                                unique_track_ids.add(track_id)

                                # Extract keypoints for this person
                                if results[0].keypoints is not None:
                                    keypoints_xy = results[0].keypoints.xy[idx].cpu().numpy()
                                    keypoints_conf = results[0].keypoints.conf[idx].cpu().numpy()

                                    # Add to data rows
                                    row = [frame_idx, frame_idx / fps, track_id]
                                    for i in range(17):
                                        if i < len(keypoints_xy):
                                            row.extend([float(keypoints_xy[i][0]), float(keypoints_xy[i][1]), float(keypoints_conf[i])])
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
    print("MULTI-PERSON POSE DETECTION - YOLOv8-Pose + BoT-SORT")
    print("=" * 70)
    print(f"Model: {CONFIG['yolo']['model']}")
    print(f"Tracker: {CONFIG['tracking']['tracker']}")
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
        result_path = pose_anesthesiologist_yolo(video_path, output_path)
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