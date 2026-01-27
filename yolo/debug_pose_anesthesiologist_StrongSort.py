"""
DEBUG: YOLO26 StrongSORT Pose Detection with Real-time Visualization

This is a DEBUG version of 1_pose_anesthesiologist_StrongSort.py that includes:
- Real-time bounding box visualization window
- Hardcoded parameter overrides for rapid tuning
- Start frame selector to skip to specific video positions
- Press 'q' to stop early

USAGE:
    python debug_pose_anesthesiologist_StrongSort.py [video_path]

    If no argument provided, uses CONFIG default.

DEBUG FEATURES:
    DEBUG_MODE = True     - Enables live visualization window
    START_FRAME = 0       - Skip to specific frame for testing

OVERRIDE PARAMETERS (in pose_anesthesiologist_strongsort function):
    These override CONFIG values for rapid experimentation:

    OVERRIDE_MIN_CONF       - Min confidence to start track (0.7)
    OVERRIDE_MAX_COS_DIST   - Max cosine distance for ReID (0.45)
    OVERRIDE_MAX_IOU_DIST   - Max IoU distance for motion (0.9)
    OVERRIDE_MAX_AGE        - Frames to keep lost tracks (200 = ~7s at 30fps)
    OVERRIDE_N_INIT         - Detections to confirm track (30)
    OVERRIDE_NN_BUDGET      - ReID feature gallery size (500)
    OVERRIDE_MC_LAMBDA      - Motion compensation weight (0.98)
    OVERRIDE_EMA_ALPHA      - Smoothing factor (0.999)

OUTPUT:
    When DEBUG_MODE = False: Parquet file (*_keypoints_strongsort.parquet)
    When DEBUG_MODE = True: Only displays visualization, returns None

CONTROLS:
    'q' key: Stop processing and exit

CONFIGURATION:
    Edit the CONFIG dictionary below for default paths and model settings.
    For rapid tuning, modify the OVERRIDE_* variables in the function.

REQUIREMENTS:
    pip install ultralytics opencv-python numpy pandas pyarrow boxmot torch

NOTES:
    - Set DEBUG_MODE = False for production runs
    - OVERRIDE_MAX_AGE of 200 (~7s) prevents "zombie" tracks
    - OVERRIDE_NN_BUDGET of 500 stores ~15s of ReID history
    - Higher OVERRIDE_MC_LAMBDA trusts appearance over motion
"""

import sys
import os
from pathlib import Path
import tempfile
import time
import subprocess

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
# CONFIGURATION
# =============================================================================
CONFIG = {
    "yolo": {
        "model": "yolo26n-pose.pt",
        "model_dir": "F:\\YOLO_Models",
        "confidence_threshold": 0.3,
        "iou_threshold": 0.45,
        "brightness_boost": 1.0,
        "use_half_precision": True,
        "imgsz": 640
    },
    "video": {
        "input_path": "F:\\Room_8_Data\\samples\\c.mp4",
        "output_path": "F:\\Room_8_Data\\samples\\3.parquet",
        "default_input_dir": "F:\\Room_8_Data\\Recordings",
        "max_resolution": {"width": 1920, "height": 1080},
        "auto_resize": False,
        "auto_repair": True
    },
    "device": {
        "use_cuda": True
    },
    "tracking": {
        "tracker": "custom_botsort.yaml",
        "persist": True,
        "verbose": False,
        "enable_fallback": True,
        "fallback_max_distance": 400
    },
    "strongsort": {
        "reid_model": "osnet_ain_x1_0_msmt17.pt",
        "reid_model_dir": ".",
        "min_conf": 0.70,
        "max_cos_dist": 0.45,
        "max_iou_dist": 0.9,
        "max_age": 300,
        "n_init": 15,
        "nn_budget": 500,
        "mc_lambda": 0.98,
        "ema_alpha": 0.999
    }
}

# Device
DEVICE = "cuda" if CONFIG["device"]["use_cuda"] and torch.cuda.is_available() else "cpu"

# COCO 17 keypoint names
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
    """Repair potentially corrupted video using ffmpeg before processing."""
    print("\n" + "=" * 70)
    print("VIDEO INTEGRITY CHECK")
    print("=" * 70)

    repaired_path = tempfile.mktemp(suffix=".mp4", prefix="repaired_")

    ffmpeg_cmd = [
        "ffmpeg", "-y", "-loglevel", "warning",
        "-i", video_path,
        "-c:v", "h264_nvenc", "-preset", "fast", "-cq", "18",
        "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart",
        "-vsync", "cfr",
        "-r", "30",
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
    Detect and track ALL persons from video using YOLO26 pose detection.
    """
    # =============================================================================
    # DEBUG PLAYGROUND - RAPID HYPERPARAMETER TUNING
    # =============================================================================
    DEBUG_MODE = True

    # 1. Start Frame Selector
    START_FRAME = 0

    # 2. Hardcoded Overrides for StrongSORT (OPTIMIZED FOR DOCTOR ReID)
    OVERRIDE_MIN_CONF = 0.7
    OVERRIDE_MAX_COS_DIST = 0.45
    OVERRIDE_MAX_IOU_DIST = 0.9

    # CHANGED: 200 is ~7 seconds. Enough for occlusions without creating "zombies".
    OVERRIDE_MAX_AGE = 200

    OVERRIDE_N_INIT = 30

    # CHANGED: 500 is the "Golden Ratio". Stores 15s of history. 3000 is overkill and causes OOM.
    OVERRIDE_NN_BUDGET = 500

    # CHANGED: 0.995 forces the tracker to trust APPEARANCE (ReID) over MOTION.
    OVERRIDE_MC_LAMBDA = 0.98

    OVERRIDE_EMA_ALPHA = 0.999
    # =============================================================================

    # Setup device
    device = setup_device()

    # Repair video first
    repaired_temp_file = None
    if CONFIG["video"].get("auto_repair", True):
        video_path, repaired_temp_file = repair_video(video_path)

    # Initialize YOLO model
    print("\n" + "=" * 70)
    print("INITIALIZING YOLO MODEL")
    print("=" * 70)

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

    reid_model_path = os.path.join(
        CONFIG['strongsort']['reid_model_dir'],
        CONFIG['strongsort']['reid_model']
    )
    print(f"ReID Model: {reid_model_path}")

    # BoxMOT expects device as integer (0) not "cuda"
    device_for_boxmot = 0 if DEVICE == "cuda" else "cpu"

    tracker = StrongSort(
        reid_weights=Path(reid_model_path),
        device=device_for_boxmot,
        half=CONFIG['yolo'].get('use_half_precision', True) and DEVICE == "cuda",
        per_class=False,
        min_conf=OVERRIDE_MIN_CONF if OVERRIDE_MIN_CONF is not None else CONFIG['strongsort'].get('min_conf', 0.1),
        max_cos_dist=OVERRIDE_MAX_COS_DIST if OVERRIDE_MAX_COS_DIST is not None else CONFIG['strongsort'].get('max_cos_dist', 0.2),
        max_iou_dist=OVERRIDE_MAX_IOU_DIST if OVERRIDE_MAX_IOU_DIST is not None else CONFIG['strongsort'].get('max_iou_dist', 0.7),
        max_age=OVERRIDE_MAX_AGE if OVERRIDE_MAX_AGE is not None else CONFIG['strongsort'].get('max_age', 30),
        n_init=OVERRIDE_N_INIT if OVERRIDE_N_INIT is not None else CONFIG['strongsort'].get('n_init', 3),
        nn_budget=OVERRIDE_NN_BUDGET if OVERRIDE_NN_BUDGET is not None else CONFIG['strongsort'].get('nn_budget', 100),
        mc_lambda=OVERRIDE_MC_LAMBDA if OVERRIDE_MC_LAMBDA is not None else CONFIG['strongsort'].get('mc_lambda', 0.98),
        ema_alpha=OVERRIDE_EMA_ALPHA if OVERRIDE_EMA_ALPHA is not None else CONFIG['strongsort'].get('ema_alpha', 0.9)
    )

    print("BoxMOT StrongSORT tracker initialized!")
    print("=" * 70)

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise ValueError(f"Could not open video: {video_path}")

    fps = cap.get(cv2.CAP_PROP_FPS)
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))

    print(f"Resolution: {width}x{height}")
    print(f"FPS: {fps:.2f}")

    if output_path is None:
        video_dir = os.path.dirname(video_path) or "."
        video_name = os.path.splitext(os.path.basename(video_path))[0]
        output_path = os.path.join(video_dir, f"{video_name}_mask.mp4")

    parquet_path = output_path.replace('.mp4', '_keypoints_strongsort.parquet')

    df_columns = ['Frame_ID', 'Timestamp', 'Track_ID']
    for keypoint_name in KEYPOINT_NAMES:
        df_columns.extend([f'{keypoint_name}_x', f'{keypoint_name}_y', f'{keypoint_name}_conf'])

    data_rows = []
    TARGET_FPS = 30
    frame_interval = max(1, int(fps / TARGET_FPS))

    try:
        print("\n" + "=" * 70)
        print("PROCESSING VIDEO - BOXMOT MULTI-PERSON TRACKING")
        print("=" * 70)

        if DEBUG_MODE and START_FRAME > 0:
            print(f"DEBUG: Skipping to frame {START_FRAME}...")
            cap.set(cv2.CAP_PROP_POS_FRAMES, START_FRAME)
            start_frame_idx = START_FRAME
        else:
            cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
            start_frame_idx = 0

        total_detections = 0
        unique_track_ids = set()
        start_time = time.time()

        with tqdm(total=frame_count - start_frame_idx, desc="Processing", unit="frame") as pbar:
            frame_idx = start_frame_idx
            saved_count = 0

            while True:
                ret, frame = cap.read()
                if not ret:
                    break

                # YOLO Detection
                results = model.predict(
                    frame,
                    conf=CONFIG['yolo']['confidence_threshold'],
                    iou=CONFIG['yolo']['iou_threshold'],
                    imgsz=CONFIG['yolo'].get('imgsz', 640),
                    half=CONFIG['yolo'].get('use_half_precision', True) and DEVICE == "cuda",
                    verbose=False
                )

                # BoxMOT Tracking
                if results[0].boxes is not None and len(results[0].boxes) > 0:
                    boxes = results[0].boxes.xyxy.cpu().numpy()
                    scores = results[0].boxes.conf.cpu().numpy()
                    classes = results[0].boxes.cls.cpu().numpy()

                    person_mask = classes == 0
                    if person_mask.any():
                        dets = np.column_stack([
                            boxes[person_mask],
                            scores[person_mask],
                            classes[person_mask]
                        ])

                        tracks = tracker.update(dets, frame)

                        if DEBUG_MODE:
                            for track in tracks:
                                t_x1, t_y1, t_x2, t_y2 = map(int, track[:4])
                                t_id = int(track[4])
                                cv2.rectangle(frame, (t_x1, t_y1), (t_x2, t_y2), (0, 255, 0), 2)
                                cv2.putText(frame, f"ID: {t_id}", (t_x1, t_y1 - 10),
                                            cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 255, 0), 2)

                            debug_view = cv2.resize(frame, (1280, 720))
                            cv2.imshow("Debug Playground (Press 'q' to quit)", debug_view)
                            if cv2.waitKey(1) & 0xFF == ord('q'):
                                print("\nDEBUG: Interrupted by user.")
                                cap.release()
                                cv2.destroyAllWindows()
                                return None

                        if frame_idx % frame_interval == 0 and len(tracks) > 0:
                            for track in tracks:
                                track_id = int(track[4])
                                unique_track_ids.add(track_id)
                                track_bbox = track[:4]

                                # Match track to detection
                                best_match_idx = None
                                best_iou = 0

                                for idx in range(len(results[0].boxes)):
                                    if results[0].boxes.cls[idx].cpu().item() == 0:
                                        det_bbox = results[0].boxes.xyxy[idx].cpu().numpy()
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

                                if best_match_idx is not None and results[0].keypoints is not None:
                                    keypoints_xy = results[0].keypoints.xy[best_match_idx].cpu().numpy()
                                    keypoints_conf = results[0].keypoints.conf[best_match_idx].cpu().numpy()

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
        if DEBUG_MODE:
            cv2.destroyAllWindows()
            return None

        print("\n" + "=" * 70)
        print("SAVING PARQUET FILE")
        print("=" * 70)

        df = pd.DataFrame(data_rows, columns=df_columns)
        df.to_parquet(parquet_path, index=False)
        print(f"Parquet file saved to: {parquet_path}")
        return parquet_path

    except Exception as e:
        print(f"\nERROR: {e}")
        sys.exit(1)

def main():
    # Standard boilerplate logic ...
    video_path = CONFIG["video"]["input_path"]
    if len(sys.argv) > 1: video_path = sys.argv[1]

    if not video_path:
        # Fallback if no config or args
        print("ERROR: No video path provided.")
        sys.exit(1)

    result_path = pose_anesthesiologist_strongsort(video_path, None)
    if result_path:
        print("\nSUCCESS!")

if __name__ == "__main__":
    main()