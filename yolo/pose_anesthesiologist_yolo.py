"""
Anesthesiologist Pose Detection and Masking using YOLOv8 Pose with BoT-SORT Tracking

This version uses YOLOv8-pose for real-time person pose detection and tracking,
combined with BoT-SORT for robust multi-object tracking. Generates body masks from pose keypoints.

Advantages over Segmentation:
- More accurate person tracking via skeletal keypoints
- Better handling of occlusions and partial visibility
- Faster inference than segmentation models
- Robust tracking with BoT-SORT (handles occlusions, re-identification)
- Persistent track IDs across the entire video

Requirements:
- pip install ultralytics opencv-python numpy
"""

import sys
import os

# Set environment variables for optimal performance
os.environ["PYTORCH_CUDA_ALLOC_CONF"] = "expandable_segments:True"

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
import csv

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
    """Load configuration from yolo_config.json"""
    config_path = os.path.join(os.path.dirname(__file__), "yolo_config.json")

    if not os.path.exists(config_path):
        print(f"ERROR: Configuration file not found: {config_path}")
        print("Creating default configuration file...")
        default_config = {
            "yolo": {
                "model": "yolov8m-pose.pt",  # Options: yolov8n-pose, yolov8s-pose, yolov8m-pose, yolov8l-pose, yolov8x-pose
                "confidence_threshold": 0.15,  # Lower for dark/difficult videos
                "iou_threshold": 0.7,
                "brightness_boost": 1.0,  # Disabled for performance (1.0 = no boost)
                "use_half_precision": True,  # Use FP16 for 2x faster inference
                "imgsz": 640  # Inference image size (640, 1280, etc.)
            },
            "video": {
                "input_path": "",
                "output_path": "",
                "default_input_dir": "F:\\Room_8_Data\\Recordings",
                "max_resolution": {"width": 1920, "height": 1080},  # YOLO can handle higher res
                "auto_resize": False,  # YOLO is faster, less need for resizing
                "auto_repair": True  # Automatically repair corrupted videos with ffmpeg
            },
            "encoding": {
                "h264_preset": "slow",
                "h264_crf": 18,
                "resize_preset": "fast"
            },
            "device": {
                "use_cuda": True
            },
            "tracking": {
                "tracker": "botsort.yaml",  # Options: "botsort.yaml", "bytetrack.yaml"
                "persist": True,  # Keep tracks across frames
                "verbose": False,
                "enable_fallback": True,  # Use spatial fallback when track is lost
                "fallback_max_distance": 400  # Max pixel distance for fallback tracking
            }
        }
        with open(config_path, 'w') as f:
            json.dump(default_config, f, indent=2)
        print(f"Created default config at: {config_path}")
        return default_config

    with open(config_path, 'r') as f:
        config = json.load(f)

    # Add YOLO config if not present
    if "yolo" not in config:
        config["yolo"] = {
            "model": "yolov8m-pose.pt",
            "confidence_threshold": 0.15,
            "iou_threshold": 0.7,
            "brightness_boost": 1.0,
            "use_half_precision": True,
            "imgsz": 640
        }

    # Add performance settings if missing
    if "brightness_boost" not in config.get("yolo", {}):
        config["yolo"]["brightness_boost"] = 1.0
    if "use_half_precision" not in config.get("yolo", {}):
        config["yolo"]["use_half_precision"] = True
    if "imgsz" not in config.get("yolo", {}):
        config["yolo"]["imgsz"] = 640

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

# Skeleton connections between keypoints (pairs of indices)
# Note: Nose-to-shoulder connections (0,5) and (0,6) are handled separately via mid-shoulder
SKELETON_CONNECTIONS = [
    (0, 1), (0, 2), (1, 3), (2, 4),  # Head: nose to eyes, eyes to ears
    (5, 6),  # Shoulders connected
    (5, 7), (7, 9),  # Left arm: shoulder -> elbow -> wrist
    (6, 8), (8, 10),  # Right arm: shoulder -> elbow -> wrist
    (5, 11), (6, 12),  # Shoulders to hips
    (11, 12),  # Hips connected
    (11, 13), (13, 15),  # Left leg: hip -> knee -> ankle
    (12, 14), (14, 16)  # Right leg: hip -> knee -> ankle
]


# =============================================================================
# GLOBAL VARIABLES FOR MOUSE CALLBACK
# =============================================================================
click_point = None
window_name = "Select Anesthesiologist - Click once"


def mouse_callback(event, x, y, flags, param):
    """Mouse callback function to capture user click."""
    global click_point

    if event == cv2.EVENT_LBUTTONDOWN:
        click_point = (x, y)
        print(f"Point selected at: ({x}, {y})")

        # Draw a circle at the clicked point for visual feedback
        frame = param
        cv2.circle(frame, (x, y), 5, (0, 255, 0), -1)
        cv2.imshow(window_name, frame)


def get_user_click(frame, results=None):
    """Display first frame and get user click for anesthesiologist location."""
    global click_point, window_name

    click_point = None

    # Create a copy for display
    display_frame = frame.copy()

    # Draw detected persons
    if results is not None and results[0].boxes is not None and len(results[0].boxes) > 0:
        person_count = 0

        for idx, cls in enumerate(results[0].boxes.cls):
            cls_id = int(cls)
            if cls_id == 0:  # Person class only
                person_count += 1
                box = results[0].boxes.xyxy[idx].cpu().numpy().astype(int)
                conf = results[0].boxes.conf[idx].cpu().numpy()

                # Draw bounding box in green
                cv2.rectangle(display_frame, (box[0], box[1]), (box[2], box[3]), (0, 255, 0), 3)

                # Draw confidence label
                label = f"Person {person_count}: {conf:.2f}"
                cv2.putText(display_frame, label, (box[0], max(box[1] - 10, 20)),
                           cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 255, 0), 2)

    # Create window and set mouse callback
    cv2.namedWindow(window_name, cv2.WINDOW_NORMAL)
    cv2.resizeWindow(window_name, 1280, 720)
    cv2.setMouseCallback(window_name, mouse_callback, display_frame)

    print("\n" + "=" * 70)
    print("USER INTERACTION REQUIRED")
    print("=" * 70)
    print("Please click ONCE on the anesthesiologist.")
    print("(Green boxes show detected persons)")
    print("Press any key after clicking to continue...")
    print("=" * 70)

    cv2.imshow(window_name, display_frame)
    cv2.waitKey(0)
    cv2.destroyAllWindows()

    if click_point is None:
        raise ValueError("No point was selected. Please run the script again and click on the anesthesiologist.")

    return click_point


def setup_device():
    """Setup device and display GPU info."""
    print("\n" + "=" * 70)
    print("GPU SETUP")
    print("=" * 70)

    if not torch.cuda.is_available():
        print("WARNING: CUDA is not available!")
        print("CPU mode will be slower for video segmentation.")
        print("=" * 70)

        response = input("\nContinue with CPU? (y/n): ").strip().lower()
        if response != 'y':
            print("Exiting...")
            sys.exit(1)
        return torch.device("cpu")

    device = torch.device("cuda")

    print(f"CUDA Available: YES")
    print(f"GPU Device: {torch.cuda.get_device_name(0)}")
    print(f"CUDA Version: {torch.version.cuda}")
    print(f"GPU Memory: {torch.cuda.get_device_properties(0).total_memory / 1024**3:.1f} GB")
    print("=" * 70)

    return device


def point_in_box(point, box):
    """Check if point (x, y) is inside box [x1, y1, x2, y2]."""
    x, y = point
    x1, y1, x2, y2 = box
    return x1 <= x <= x2 and y1 <= y <= y2


def find_target_person(results, click_point):
    """
    Find the person detection that contains the clicked point.
    Returns the track ID of the target person, or None if not found.
    """
    if results[0].boxes is None or len(results[0].boxes) == 0:
        return None

    # Filter for person class (class 0 in COCO)
    person_data = []
    for idx, cls in enumerate(results[0].boxes.cls):
        if int(cls) == 0:  # Person class
            box = results[0].boxes.xyxy[idx].cpu().numpy()
            # Get track ID if available
            track_id = None
            if hasattr(results[0].boxes, 'id') and results[0].boxes.id is not None:
                track_id = int(results[0].boxes.id[idx].cpu().numpy())
            person_data.append((idx, box, track_id))

    if not person_data:
        return None

    # Find person whose box contains the click point
    for idx, box, track_id in person_data:
        if point_in_box(click_point, box):
            return track_id if track_id is not None else idx

    # If no box contains the point, find closest person
    min_dist = float('inf')
    closest_track_id = None

    for idx, box, track_id in person_data:
        centroid = ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2)
        dist = np.sqrt((centroid[0] - click_point[0])**2 + (centroid[1] - click_point[1])**2)
        if dist < min_dist:
            min_dist = dist
            closest_track_id = track_id if track_id is not None else idx

    return closest_track_id


def repair_video(video_path):
    """
    Repair potentially corrupted video using ffmpeg before processing.
    Returns path to repaired video (or original if repair not needed).
    """
    print("\n" + "=" * 70)
    print("VIDEO INTEGRITY CHECK")
    print("=" * 70)
    print("Checking video for corruption...")

    # Create temporary repaired video
    repaired_path = tempfile.mktemp(suffix=".mp4", prefix="repaired_")

    # Re-encode with ffmpeg to fix any corruption
    ffmpeg_cmd = [
        "ffmpeg", "-y", "-loglevel", "warning",
        "-i", video_path,
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "18",
        "-c:a", "aac",
        "-b:a", "192k",
        "-movflags", "+faststart",
        "-vsync", "cfr",  # Constant frame rate
        "-r", "30",  # Force 30 fps
        repaired_path
    ]

    print("Repairing video with ffmpeg (fixing corruption, frame drops, variable FPS)...")
    result = subprocess.run(ffmpeg_cmd, capture_output=True, text=True,
                          creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)

    if result.returncode != 0:
        print(f"FFmpeg repair failed: {result.stderr}")
        print("Continuing with original video...")
        return video_path, None

    # Verify the repaired video
    cap_original = cv2.VideoCapture(video_path)
    cap_repaired = cv2.VideoCapture(repaired_path)

    original_frames = int(cap_original.get(cv2.CAP_PROP_FRAME_COUNT))
    repaired_frames = int(cap_repaired.get(cv2.CAP_PROP_FRAME_COUNT))

    cap_original.release()
    cap_repaired.release()

    print(f"✓ Video repaired successfully")
    print(f"  Original frames: {original_frames}")
    print(f"  Repaired frames: {repaired_frames}")
    print(f"  Temp file: {repaired_path}")
    print("=" * 70)

    return repaired_path, repaired_path


def pose_anesthesiologist_yolo(video_path, output_path=None):
    """
    Detect and mask anesthesiologist from video using YOLOv8 pose detection.
    Creates masks from detected pose keypoints using convex hull.
    """
    # Setup device
    device = setup_device()

    # Repair video first to fix any corruption
    repaired_temp_file = None
    if CONFIG["video"].get("auto_repair", True):
        video_path, repaired_temp_file = repair_video(video_path)
    else:
        print("\nVideo auto-repair disabled (set video.auto_repair=true to enable)")

    # Initialize YOLO model
    print("\n" + "=" * 70)
    print("INITIALIZING YOLO MODEL")
    print("=" * 70)
    print(f"Model: {CONFIG['yolo']['model']}")
    print(f"Device: {DEVICE}")

    # Set custom model directory if specified
    model_dir = CONFIG['yolo'].get('model_dir')
    if model_dir:
        os.makedirs(model_dir, exist_ok=True)
        os.environ['YOLO_CONFIG_DIR'] = model_dir
        print(f"Model Directory: {model_dir}")
        model_path = os.path.join(model_dir, CONFIG['yolo']['model'])
    else:
        model_path = CONFIG['yolo']['model']

    print("Loading model...")

    try:
        model = YOLO(model_path)
        model.to(DEVICE)

        print("Model loaded successfully!")

        # FP16 will be enabled via half=True parameter in track() calls
        if CONFIG['yolo'].get('use_half_precision', True) and DEVICE == "cuda":
            print("FP16 half precision will be used for inference (enabled via track parameter)")

        print(f"Half Precision: {CONFIG['yolo'].get('use_half_precision', True) and DEVICE == 'cuda'}")
        print(f"Inference Size: {CONFIG['yolo'].get('imgsz', 640)}")
    except Exception as e:
        print(f"ERROR: Failed to load YOLO model: {e}")
        sys.exit(1)

    print("=" * 70)

    # Open video
    print("\n" + "=" * 70)
    print("OPENING VIDEO")
    print("=" * 70)

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
    print(f"Duration: {frame_count/fps:.1f} seconds")
    print("=" * 70)

    # Read first frame
    ret, first_frame = cap.read()
    if not ret:
        raise ValueError("Could not read first frame")

    # Check if frame is valid
    if first_frame is None or first_frame.size == 0:
        raise ValueError("First frame is empty or invalid")

    # Run tracking on first frame to initialize tracker
    print("\n" + "=" * 70)
    print("DETECTING PERSONS IN FIRST FRAME")
    print("=" * 70)
    print(f"Running YOLO with {CONFIG['tracking']['tracker']} tracker...")

    results = model.track(first_frame,
                         conf=CONFIG['yolo']['confidence_threshold'],
                         iou=CONFIG['yolo']['iou_threshold'],
                         imgsz=CONFIG['yolo'].get('imgsz', 640),
                         half=CONFIG['yolo'].get('use_half_precision', True) and DEVICE == "cuda",
                         tracker=CONFIG['tracking']['tracker'],
                         persist=CONFIG['tracking']['persist'],
                         verbose=CONFIG['tracking']['verbose'])

    # Check detection results
    if results[0].boxes is not None and len(results[0].boxes) > 0:
        person_count = sum(1 for cls in results[0].boxes.cls if int(cls) == 0)
        print(f"Detected {person_count} person(s)")
    else:
        print("WARNING: No persons detected!")
        print("Try adjusting brightness_boost or confidence_threshold in yolo_config.json")

    # Get user click on first frame with detections shown
    click_x, click_y = get_user_click(first_frame, results)

    # Find target person
    print("\n" + "=" * 70)
    print("IDENTIFYING TARGET PERSON")
    print("=" * 70)
    print(f"Click point: ({click_x}, {click_y})")

    target_track_id = find_target_person(results, (click_x, click_y))

    if target_track_id is None:
        print("ERROR: No person found at clicked location!")
        print("Try clicking directly on a person in the frame.")
        cap.release()
        sys.exit(1)

    print(f"Target person locked!")
    print(f"  Track ID: {target_track_id}")
    print("=" * 70)

    # Determine output path
    if output_path is None:
        video_dir = os.path.dirname(video_path) or "."
        video_name = os.path.splitext(os.path.basename(video_path))[0]
        output_path = os.path.join(video_dir, f"{video_name}_mask.mp4")

    print(f"Output: {output_path}")

    # Create output directory if needed
    os.makedirs(os.path.dirname(output_path) if os.path.dirname(output_path) else ".", exist_ok=True)

    # Create CSV file for keypoints export
    csv_path = output_path.replace('.mp4', '_keypoints.csv')
    csv_file = None

    try:
        csv_file = open(csv_path, 'w', newline='')
        csv_writer = csv.writer(csv_file)

        # Write CSV header
        csv_header = ['Frame_ID', 'Timestamp']
        for keypoint_name in KEYPOINT_NAMES:
            csv_header.extend([f'{keypoint_name}_x', f'{keypoint_name}_y', f'{keypoint_name}_conf'])
        csv_writer.writerow(csv_header)

        print(f"CSV Output: {csv_path}")

        # Process video
        print("\n" + "=" * 70)
        print("PROCESSING VIDEO WITH POSE DETECTION + BOT-SORT TRACKING")
        print("=" * 70)
        print(f"Model: {CONFIG['yolo']['model']}")
        print(f"Tracker: {CONFIG['tracking']['tracker']}")
        print()

        # Create temporary directory for mask frames
        mask_temp_dir = tempfile.mkdtemp(prefix="yolo_pose_masks_")

        # Reset video to beginning
        cap.set(cv2.CAP_PROP_POS_FRAMES, 0)

        frames_tracked = 0
        frames_lost = 0
        frames_fallback = 0
        last_known_box = None
        consecutive_lost = 0

        # CSV batch writing buffer for better performance
        csv_batch_buffer = []
        csv_batch_size = 30  # Write every 30 frames (1 second at 30fps)

        start_time = time.time()

        with tqdm(total=frame_count, desc="Processing", unit="frame") as pbar:
            frame_idx = 0
            while True:
                ret, frame = cap.read()
                if not ret:
                    break

                # Run YOLO tracking with optimizations (FP16, imgsz)
                results = model.track(frame,
                                    conf=CONFIG['yolo']['confidence_threshold'],
                                    iou=CONFIG['yolo']['iou_threshold'],
                                    imgsz=CONFIG['yolo'].get('imgsz', 640),
                                    half=CONFIG['yolo'].get('use_half_precision', True) and DEVICE == "cuda",
                                    tracker=CONFIG['tracking']['tracker'],
                                    persist=CONFIG['tracking']['persist'],
                                    verbose=CONFIG['tracking']['verbose'])

                # Find the target track ID in current frame
                current_mask_idx = None
                fallback_idx = None

                if results[0].boxes is not None and hasattr(results[0].boxes, 'id') and results[0].boxes.id is not None:
                    # Try to find exact track ID match
                    for idx, track_id in enumerate(results[0].boxes.id):
                        if int(track_id.cpu().numpy()) == target_track_id:
                            # Check if it's a person (class 0)
                            if int(results[0].boxes.cls[idx]) == 0:
                                current_mask_idx = idx
                                break

                    # If track ID not found, use fallback: find closest person to last known position
                    if current_mask_idx is None and last_known_box is not None:
                        if CONFIG['tracking'].get('enable_fallback', True):
                            max_dist = CONFIG['tracking'].get('fallback_max_distance', 400)
                            last_center = ((last_known_box[0] + last_known_box[2]) / 2,
                                          (last_known_box[1] + last_known_box[3]) / 2)
                            min_dist = float('inf')

                            for idx, cls in enumerate(results[0].boxes.cls):
                                if int(cls) == 0:  # Person class
                                    box = results[0].boxes.xyxy[idx].cpu().numpy()
                                    center = ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2)
                                    dist = np.sqrt((center[0] - last_center[0])**2 + (center[1] - last_center[1])**2)

                                    # Only use fallback if person is within configured distance
                                    if dist < min_dist and dist < max_dist:
                                        min_dist = dist
                                        fallback_idx = idx

                            if fallback_idx is not None:
                                current_mask_idx = fallback_idx
                                frames_fallback += 1

                # Create skeleton visualization from pose keypoints and write to CSV
                if current_mask_idx is not None and results[0].keypoints is not None:
                    # Get pose keypoints (xy coordinates and confidence)
                    keypoints_xy = results[0].keypoints.xy[current_mask_idx].cpu().numpy()
                    keypoints_conf = results[0].keypoints.conf[current_mask_idx].cpu().numpy()

                    # Create black background for skeleton
                    binary_mask = np.zeros((height, width), dtype=np.uint8)

                    # Draw skeleton connections (bones)
                    for start_idx, end_idx in SKELETON_CONNECTIONS:
                        if start_idx < len(keypoints_xy) and end_idx < len(keypoints_xy):
                            pt1 = keypoints_xy[start_idx]
                            pt2 = keypoints_xy[end_idx]

                            # Check if both keypoints are valid
                            if pt1[0] > 0 and pt1[1] > 0 and pt2[0] > 0 and pt2[1] > 0:
                                x1, y1 = int(pt1[0]), int(pt1[1])
                                x2, y2 = int(pt2[0]), int(pt2[1])
                                # Draw bone with thickness
                                cv2.line(binary_mask, (x1, y1), (x2, y2), 255, thickness=3)

                    # Draw neck connection (nose to mid-shoulder)
                    nose_idx, left_sh_idx, right_sh_idx = 0, 5, 6
                    if (len(keypoints_conf) > max(nose_idx, left_sh_idx, right_sh_idx) and
                        keypoints_conf[nose_idx] > 0.3 and
                        keypoints_conf[left_sh_idx] > 0.3 and
                        keypoints_conf[right_sh_idx] > 0.3):

                        # Nose coordinates
                        nose_pos = (int(keypoints_xy[nose_idx][0]), int(keypoints_xy[nose_idx][1]))

                        # Calculate midpoint between shoulders (Mid-Shoulder)
                        mid_shoulder_x = int((keypoints_xy[left_sh_idx][0] + keypoints_xy[right_sh_idx][0]) / 2)
                        mid_shoulder_y = int((keypoints_xy[left_sh_idx][1] + keypoints_xy[right_sh_idx][1]) / 2)
                        mid_shoulder_pos = (mid_shoulder_x, mid_shoulder_y)

                        # Draw neck
                        cv2.line(binary_mask, nose_pos, mid_shoulder_pos, 255, thickness=3)

                    # Draw keypoints as circles
                    for kp in keypoints_xy:
                        x, y = int(kp[0]), int(kp[1])
                        if x > 0 and y > 0:  # Valid keypoint
                            cv2.circle(binary_mask, (x, y), radius=5, color=255, thickness=-1)

                    # Add to CSV batch buffer - frame with detected person
                    csv_row = [frame_idx, frame_idx / fps]  # Frame ID and timestamp
                    for i in range(17):
                        if i < len(keypoints_xy):
                            csv_row.extend([keypoints_xy[i][0], keypoints_xy[i][1], keypoints_conf[i]])
                        else:
                            csv_row.extend([np.nan, np.nan, 0.0])
                    csv_batch_buffer.append(csv_row)

                    frames_tracked += 1
                    consecutive_lost = 0

                    # Update last known position
                    last_known_box = results[0].boxes.xyxy[current_mask_idx].cpu().numpy()
                else:
                    # No detection/tracking - create empty mask and add to CSV buffer
                    binary_mask = np.zeros((height, width), dtype=np.uint8)

                    # Add to CSV batch buffer - frame with no detection
                    csv_row = [frame_idx, frame_idx / fps]  # Frame ID and timestamp
                    for i in range(17):
                        csv_row.extend([np.nan, np.nan, 0.0])
                    csv_batch_buffer.append(csv_row)

                    frames_lost += 1
                    consecutive_lost += 1

                # Flush CSV buffer periodically for better performance
                if len(csv_batch_buffer) >= csv_batch_size:
                    csv_writer.writerows(csv_batch_buffer)
                    csv_batch_buffer.clear()

                # Write mask frame as JPG for faster I/O
                mask_path = os.path.join(mask_temp_dir, f"{frame_idx:05d}.jpg")
                cv2.imwrite(mask_path, binary_mask, [cv2.IMWRITE_JPEG_QUALITY, 95])

                frame_idx += 1
                pbar.update(1)

        cap.release()

        # Flush any remaining CSV rows in buffer
        if csv_batch_buffer:
            csv_writer.writerows(csv_batch_buffer)
            csv_batch_buffer.clear()

        processing_time = time.time() - start_time
        processing_fps = frame_count / processing_time

        print(f"\nProcessing complete!")
        print(f"  Time: {processing_time:.1f} seconds")
        print(f"  Speed: {processing_fps:.1f} FPS")
        print(f"  Frames tracked: {frames_tracked}/{frame_count} ({frames_tracked/frame_count*100:.1f}%)")
        print(f"    - Direct tracking: {frames_tracked - frames_fallback}/{frame_count} ({(frames_tracked-frames_fallback)/frame_count*100:.1f}%)")
        print(f"    - Fallback tracking: {frames_fallback}/{frame_count} ({frames_fallback/frame_count*100:.1f}%)")
        print(f"  Frames lost: {frames_lost}/{frame_count} ({frames_lost/frame_count*100:.1f}%)")

        if frames_lost > frame_count * 0.1:
            print(f"\n  WARNING: More than 10% of frames were lost!")
            print(f"  Consider:")
            print(f"    - Lowering confidence_threshold")
            print(f"    - Increasing fallback_max_distance")
            print(f"    - Using a different video with better visibility")

        # Encode with FFmpeg
        print("\n" + "=" * 70)
        print("ENCODING VIDEO")
        print("=" * 70)
        print("Encoding with H.264...")

        ffmpeg_cmd = [
            "ffmpeg", "-y", "-loglevel", "error",
            "-framerate", str(fps),
            "-i", os.path.join(mask_temp_dir, "%05d.jpg"),
            "-c:v", "libx264",
            "-preset", CONFIG["encoding"]["h264_preset"],
            "-crf", str(CONFIG["encoding"]["h264_crf"]),
            "-pix_fmt", "yuv420p",
            output_path
        ]

        result = subprocess.run(ffmpeg_cmd, capture_output=True, text=True,
                              creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)

        if result.returncode != 0:
            print(f"FFmpeg encoding failed: {result.stderr}")
            raise RuntimeError("Failed to encode video with FFmpeg")

        # Verify output
        if os.path.exists(output_path):
            output_size = os.path.getsize(output_path) / (1024 * 1024)

            print("\n" + "=" * 70)
            print("POSE DETECTION & MASKING COMPLETE")
            print("=" * 70)
            print(f"Output File: {output_path}")
            print(f"File Size: {output_size:.1f} MB")
            print(f"CSV Keypoints: {csv_path}")
            print(f"Frames Processed: {frame_count}")
            print(f"Processing Speed: {processing_fps:.1f} FPS")
            print(f"Tracking Success Rate: {frames_tracked/frame_count*100:.1f}%")

            if frames_fallback > 0:
                print(f"Fallback Tracking Used: {frames_fallback} frames ({frames_fallback/frame_count*100:.1f}%)")

            print("=" * 70)
        else:
            print("\nERROR: Output file was not created!")
            success = False

        # Cleanup temporary files
        import shutil
        print("\nCleaning up temporary files...")
        shutil.rmtree(mask_temp_dir, ignore_errors=True)

        # Remove repaired video if it was created
        if repaired_temp_file and os.path.exists(repaired_temp_file):
            os.remove(repaired_temp_file)
            print(f"  Removed temporary repaired video")

        if not os.path.exists(output_path):
            sys.exit(1)

        return output_path

    finally:
        # Always close CSV file
        if csv_file is not None:
            csv_file.close()
            print(f"CSV file saved: {csv_path}")


def main():
    """Main entry point for the script."""
    print("\n" + "=" * 70)
    print("ANESTHESIOLOGIST POSE DETECTION - YOLOv8-Pose + BoT-SORT")
    print("=" * 70)
    print(f"Model: {CONFIG['yolo']['model']}")
    print(f"Tracker: {CONFIG['tracking']['tracker']}")
    print(f"Confidence Threshold: {CONFIG['yolo']['confidence_threshold']}")
    print(f"Config: {os.path.join(os.path.dirname(__file__), 'yolo_config.json')}")
    print("=" * 70)

    # Get input video path from config first, then command line, then prompt
    video_path = CONFIG["video"]["input_path"]

    if len(sys.argv) > 1:
        video_path = sys.argv[1]
    elif not video_path:
        default_dir = CONFIG["video"]["default_input_dir"] or MP4_ROOT or "."
        print(f"\nEnter the path to the input video:")
        print(f"(Default search location: {default_dir})")
        video_path = input("\nVideo path: ").strip()

    # Remove quotes if present
    video_path = video_path.strip('"').strip("'")

    # Validate input
    if not video_path:
        print("ERROR: No video path provided")
        sys.exit(1)

    if not os.path.exists(video_path):
        print(f"ERROR: Video file not found: {video_path}")
        sys.exit(1)

    # Get output path from config first, then command line, then auto-generate
    output_path = CONFIG["video"]["output_path"]
    if len(sys.argv) > 2:
        output_path = sys.argv[2].strip('"').strip("'")

    # Convert empty string to None for auto-generation
    if not output_path:
        output_path = None

    # Run pose detection
    try:
        result_path = pose_anesthesiologist_yolo(video_path, output_path)
        print("\nSUCCESS!")
        print(f"\nMask video saved to: {result_path}")

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
