"""
Multi-Person Pose Detection using YOLOv8 Pose with HEAD-FOCUSED BoxMOT StrongSORT + OSNet ReID
VERSION 3: STRICT FILTERING (Ignore Legs/Noise)

Changes in V3:
- ADDED `is_valid_upper_body`: Ignores detections that only contain legs/hips.
  (Prevents creating IDs for "walking legs" or surgical drapes).
- Logic: To be tracked, a person MUST have at least one facial keypoint OR one shoulder detected.
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
    sys.exit(1)

# Import BoxMOT
try:
    from boxmot import StrongSort
except ImportError:
    print("=" * 70)
    print("ERROR: BoxMOT not installed!")
    print("=" * 70)
    sys.exit(1)

try:
    from config import MP4_ROOT
except ImportError:
    MP4_ROOT = None

# ==============================================================================
# FIX FOR PYTORCH 2.6+ COMPATIBILITY
# ==============================================================================
_original_torch_load = torch.load
def patched_torch_load(*args, **kwargs):
    if 'weights_only' not in kwargs:
        kwargs['weights_only'] = False
    return _original_torch_load(*args, **kwargs)
torch.load = patched_torch_load


# =============================================================================
# CONFIGURATION
# =============================================================================
def load_config():
    config_path = os.path.join(os.path.dirname(__file__), "0_yolo_config.json")
    with open(config_path, 'r') as f:
        config = json.load(f)

    # Fallback if strongsort_head is missing
    if "strongsort_head" not in config and "strongsort" in config:
        config["strongsort_head"] = config["strongsort"].copy()

    return config

CONFIG = load_config()
DEVICE = "cuda" if CONFIG["device"]["use_cuda"] and torch.cuda.is_available() else "cpu"

# Keypoint Indices Map (YOLO format)
# 0: Nose, 1: L-Eye, 2: R-Eye, 3: L-Ear, 4: R-Ear
# 5: L-Shoulder, 6: R-Shoulder
FACIAL_INDICES = [0, 1, 2, 3, 4]
SHOULDER_INDICES = [5, 6]

KEYPOINT_NAMES = [
    "Nose", "Left_Eye", "Right_Eye", "Left_Ear", "Right_Ear",
    "Left_Shoulder", "Right_Shoulder", "Left_Elbow", "Right_Elbow",
    "Left_Wrist", "Right_Wrist", "Left_Hip", "Right_Hip",
    "Left_Knee", "Right_Knee", "Left_Ankle", "Right_Ankle"
]

# =============================================================================
# LOGIC: FILTERS & BOX CALCULATION
# =============================================================================

def is_valid_upper_body(keypoints_conf, threshold=0.3):
    """
    Returns True if the detection has at least one Face OR Shoulder keypoint.
    Filters out 'Legs only' detections.
    """
    if keypoints_conf is None:
        return False

    # Check for any face keypoint
    has_face = np.any(keypoints_conf[FACIAL_INDICES] > threshold)

    # Check for any shoulder keypoint
    has_shoulder = np.any(keypoints_conf[SHOULDER_INDICES] > threshold)

    return has_face or has_shoulder

def calculate_head_bbox(keypoints_xy, keypoints_conf, conf_threshold=0.3, padding=0.2):
    """Calculate precise head bbox from facial keypoints."""
    facial_points = []
    for idx in FACIAL_INDICES:
        if idx < len(keypoints_conf) and keypoints_conf[idx] > conf_threshold:
            x, y = keypoints_xy[idx]
            if x > 1 and y > 1: # Valid coordinates
                facial_points.append([x, y])

    if len(facial_points) < 2:
        return None

    facial_points = np.array(facial_points)
    x_min, y_min = np.min(facial_points, axis=0)
    x_max, y_max = np.max(facial_points, axis=0)

    width = x_max - x_min
    height = y_max - y_min

    # Minimal size sanity check
    if width < 5 or height < 5:
        return None

    # Add padding for hat/helmet
    # More padding on top for surgical caps
    pad_w = width * padding
    pad_h = height * padding

    x_min = max(0, x_min - pad_w)
    y_min = max(0, y_min - (pad_h * 2.0)) # Extra space on top
    x_max = x_max + pad_w
    y_max = y_max + pad_h

    return (x_min, y_min, x_max, y_max)

def calculate_virtual_head_bbox(body_bbox):
    """Fallback: Top 1/7th of the body box, centered horizontally."""
    x1, y1, x2, y2 = body_bbox
    body_h = y2 - y1
    body_w = x2 - x1

    head_h = body_h / 7.0
    head_w = min(head_h * 1.2, body_w) # Slightly wider than high

    center_x = (x1 + x2) / 2

    vx1 = center_x - (head_w / 2)
    vy1 = y1
    vx2 = center_x + (head_w / 2)
    vy2 = y1 + head_h

    return (vx1, vy1, vx2, vy2)

def validate_bbox(bbox, frame_w, frame_h):
    x1, y1, x2, y2 = bbox
    x1 = max(0, min(x1, frame_w - 1))
    y1 = max(0, min(y1, frame_h - 1))
    x2 = max(0, min(x2, frame_w))
    y2 = max(0, min(y2, frame_h))

    if (x2 - x1) < 5 or (y2 - y1) < 5:
        return None
    return (x1, y1, x2, y2)

# =============================================================================
# MAIN LOOP
# =============================================================================
def pose_anesthesiologist_head_tracking(video_path, output_path=None):
    device = torch.device("cuda" if DEVICE == "cuda" else "cpu")
    print(f"Running on: {device}")

    # Load YOLO
    model_dir = CONFIG['yolo'].get('model_dir')
    if model_dir:
        os.makedirs(model_dir, exist_ok=True)
        model_path = os.path.join(model_dir, CONFIG['yolo']['model'])
    else:
        model_path = CONFIG['yolo']['model']

    print(f"Loading YOLO: {model_path}")
    model = YOLO(model_path)
    model.to(DEVICE)

    # Init Tracker
    reid_cfg = CONFIG['strongsort_head']
    reid_model_path = os.path.join(reid_cfg['reid_model_dir'], reid_cfg['reid_model'])
    print(f"Loading Tracker ReID: {reid_model_path}")

    tracker = StrongSort(
        reid_weights=Path(reid_model_path),
        device=0 if DEVICE == "cuda" else "cpu",
        half=CONFIG['yolo'].get('use_half_precision', True) and DEVICE == "cuda",
        per_class=False,
        min_conf=reid_cfg.get('min_conf', 0.4),
        max_cos_dist=reid_cfg.get('max_cos_dist', 0.15),
        max_iou_dist=reid_cfg.get('max_iou_dist', 0.7),
        max_age=reid_cfg.get('max_age', 600),
        n_init=reid_cfg.get('n_init', 3),
        nn_budget=reid_cfg.get('nn_budget', 750),
        mc_lambda=reid_cfg.get('mc_lambda', 0.99),
        ema_alpha=reid_cfg.get('ema_alpha', 0.9)
    )

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        print(f"Error opening video: {video_path}")
        return

    fps = cap.get(cv2.CAP_PROP_FPS)
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))

    if output_path is None:
        video_dir = os.path.dirname(video_path) or "."
        video_name = os.path.splitext(os.path.basename(video_path))[0]
        output_path = os.path.join(video_dir, f"{video_name}_head_v3.parquet")

    # Output Data Structure
    df_columns = ['Frame_ID', 'Timestamp', 'Track_ID']
    for kp in KEYPOINT_NAMES:
        df_columns.extend([f'{kp}_x', f'{kp}_y', f'{kp}_conf'])

    data_rows = []
    stats = {'real_head': 0, 'virtual_head': 0, 'ignored_legs': 0}

    with tqdm(total=frame_count, desc="Tracking Heads") as pbar:
        frame_idx = 0
        while True:
            ret, frame = cap.read()
            if not ret: break

            # Predict
            results = model.predict(
                frame,
                conf=CONFIG['yolo']['confidence_threshold'],
                iou=CONFIG['yolo']['iou_threshold'],
                imgsz=CONFIG['yolo'].get('imgsz', 1920),
                half=CONFIG['yolo'].get('use_half_precision', True) and DEVICE == "cuda",
                verbose=False
            )[0]

            detections_to_track = []

            if results.boxes and len(results.boxes) > 0:
                boxes = results.boxes.xyxy.cpu().numpy()
                scores = results.boxes.conf.cpu().numpy()
                classes = results.boxes.cls.cpu().numpy()

                # Check Keypoints
                if results.keypoints is not None:
                    kpts_xy = results.keypoints.xy.cpu().numpy()
                    kpts_conf = results.keypoints.conf.cpu().numpy()
                else:
                    kpts_xy = [None] * len(boxes)
                    kpts_conf = [None] * len(boxes)

                for i in range(len(boxes)):
                    # Only look at Person class (0)
                    if int(classes[i]) != 0:
                        continue

                    # --- FILTER STEP: IGNORE LEGS ---
                    # If detection has no face AND no shoulders, skip it!
                    if not is_valid_upper_body(kpts_conf[i], threshold=0.3):
                        stats['ignored_legs'] += 1
                        continue

                    # 1. Try Real Head Box (from facial keypoints)
                    track_box = None
                    if kpts_xy[i] is not None:
                        track_box = calculate_head_bbox(kpts_xy[i], kpts_conf[i], conf_threshold=0.3)

                    if track_box is not None:
                        track_box = validate_bbox(track_box, width, height)
                        if track_box: stats['real_head'] += 1

                    # 2. Fallback: Virtual Head Box (top of body)
                    # Only used if Real Head failed, BUT we know it's a valid upper body
                    if track_box is None:
                        track_box = calculate_virtual_head_bbox(boxes[i])
                        track_box = validate_bbox(track_box, width, height)
                        if track_box: stats['virtual_head'] += 1

                    if track_box is not None:
                        # [x1, y1, x2, y2, conf, cls, ORIG_INDEX]
                        detections_to_track.append([
                            track_box[0], track_box[1], track_box[2], track_box[3],
                            scores[i], classes[i], i
                        ])

            # Update Tracker
            if len(detections_to_track) > 0:
                dets_array = np.array([d[:6] for d in detections_to_track])
                tracks = tracker.update(dets_array, frame)

                for track in tracks:
                    track_id = int(track[4])
                    track_bbox = track[:4]

                    # Match track back to original detection for full keypoints
                    best_iou = 0
                    best_det_idx = -1

                    for det in detections_to_track:
                        det_idx = int(det[6])
                        det_bbox = det[:4]

                        # IoU
                        xx1 = max(track_bbox[0], det_bbox[0])
                        yy1 = max(track_bbox[1], det_bbox[1])
                        xx2 = min(track_bbox[2], det_bbox[2])
                        yy2 = min(track_bbox[3], det_bbox[3])
                        w = max(0, xx2 - xx1)
                        h = max(0, yy2 - yy1)
                        inter = w * h
                        area_t = (track_bbox[2]-track_bbox[0]) * (track_bbox[3]-track_bbox[1])
                        area_d = (det_bbox[2]-det_bbox[0]) * (det_bbox[3]-det_bbox[1])
                        union = area_t + area_d - inter

                        iou = inter / union if union > 0 else 0
                        if iou > best_iou:
                            best_iou = iou
                            best_det_idx = det_idx

                    if best_iou > 0.1 and best_det_idx != -1:
                        # Extract Original Body Keypoints
                        orig_kpts = kpts_xy[best_det_idx]
                        orig_conf = kpts_conf[best_det_idx]

                        row = [frame_idx, frame_idx/fps, track_id]
                        for k in range(17):
                            row.extend([orig_kpts[k][0], orig_kpts[k][1], orig_conf[k]])
                        data_rows.append(row)

            frame_idx += 1
            pbar.update(1)

    cap.release()
    print("\nProcessing Complete.")
    print(f"Stats: Real Heads: {stats['real_head']}, Virtual Heads: {stats['virtual_head']}")
    print(f"       Ignored (Legs/Noise): {stats['ignored_legs']}")

    if data_rows:
        df = pd.DataFrame(data_rows, columns=df_columns)
        df.to_parquet(output_path, index=False)
        print(f"Saved: {output_path}")
    else:
        print("WARNING: No tracks generated.")

if __name__ == "__main__":
    pose_anesthesiologist_head_tracking(CONFIG["video"]["input_path"])