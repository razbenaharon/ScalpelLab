import cv2
import pandas as pd
import numpy as np
import sys
import os
import json
from tqdm import tqdm

# Suppress FFmpeg warnings
os.environ['OPENCV_FFMPEG_LOGLEVEL'] = '-8'

# Define connections between keypoints for drawing skeletons
SKELETON_CONNECTIONS = [
    (0, 1), (0, 2), (1, 3), (2, 4),  # Head
    (5, 6),  # Shoulders
    (5, 7), (7, 9),  # Left Arm
    (6, 8), (8, 10),  # Right Arm
    (5, 11), (6, 12),  # Torso
    (11, 12),  # Hips
    (11, 13), (13, 15),  # Left Leg
    (12, 14), (14, 16)  # Right Leg
]

# Colors for different Track IDs (B, G, R)
COLORS = [
    (0, 255, 0),  # Green
    (0, 0, 255),  # Red
    (255, 0, 0),  # Blue
    (0, 255, 255),  # Yellow
    (255, 0, 255),  # Magenta
    (255, 255, 0),  # Cyan
    (255, 255, 255)  # White
]


def draw_skeleton(frame, row, color, min_conf=0.001):
    """Draws skeleton and ID on the frame based on a dataframe row."""
    # Keypoint indices from the parquet columns
    # Columns are: Frame, Time, Track, Nose_x, Nose_y, Nose_c, ...

    # Extract coordinates
    kps = []
    base_idx = 3  # Start after Frame, Timestamp, Track_ID

    for i in range(17):
        x = row.iloc[base_idx + i * 3]
        y = row.iloc[base_idx + i * 3 + 1]
        conf = row.iloc[base_idx + i * 3 + 2]
        kps.append((x, y, conf))

    # Draw Connections
    for p1, p2 in SKELETON_CONNECTIONS:
        kp1 = kps[p1]
        kp2 = kps[p2]

        # Only draw if confidence is > min_conf (lowered from 0.3)
        if kp1[2] > min_conf and kp2[2] > min_conf:
            pt1 = (int(kp1[0]), int(kp1[1]))
            pt2 = (int(kp2[0]), int(kp2[1]))
            cv2.line(frame, pt1, pt2, color, 2)

    # Draw Points
    for kp in kps:
        if kp[2] > min_conf:
            cv2.circle(frame, (int(kp[0]), int(kp[1])), 4, color, -1)

    # Draw ID above head (Nose or Shoulders)
    nose = kps[0]
    if nose[2] > min_conf:
        label_pos = (int(nose[0]), int(nose[1]) - 20)
    else:
        # Fallback to shoulders
        left_shoulder = kps[5]
        right_shoulder = kps[6]
        if left_shoulder[2] > min_conf or right_shoulder[2] > min_conf:
            avg_x = (left_shoulder[0] + right_shoulder[0]) / 2
            avg_y = (left_shoulder[1] + right_shoulder[1]) / 2
            label_pos = (int(avg_x), int(avg_y) - 20)
        else:
            # No valid keypoints, skip label
            return

    track_id = int(row['Track_ID'])
    cv2.putText(frame, f"ID: {track_id}", label_pos,
                cv2.FONT_HERSHEY_SIMPLEX, 0.8, color, 2)


def main():
    # Load config
    config_path = os.path.join(os.path.dirname(__file__), "0_yolo_config.json")
    config = {}
    if os.path.exists(config_path):
        try:
            with open(config_path, 'r') as f:
                config = json.load(f)
        except Exception as e:
            print(f"Warning: Could not load config file {config_path}: {e}")

    default_video = config.get('video', {}).get('input_path', '')
    default_parquet = config.get('video', {}).get('output_path', '')

    if len(sys.argv) < 3:
        if default_video and default_parquet and os.path.exists(default_video) and os.path.exists(default_parquet):
            print(f"Using paths from config: {config_path}")
            print(f"  Video: {default_video}")
            print(f"  Parquet: {default_parquet}")
            video_path = default_video
            parquet_path = default_parquet
        else:
            print("Usage: python visualize_overlay.py <video_path> <parquet_path>")
            # Fallback input for easy testing
            video_path = input(f"Enter video path [{default_video}]: ").strip('"') or default_video
            parquet_path = input(f"Enter parquet path [{default_parquet}]: ").strip('"') or default_parquet
    else:
        video_path = sys.argv[1]
        parquet_path = sys.argv[2]

    if not os.path.exists(video_path) or not os.path.exists(parquet_path):
        print("Error: Files not found.")
        print(f"Video: {video_path}")
        print(f"Parquet: {parquet_path}")
        return

    print("Loading data...")
    df = pd.read_parquet(parquet_path)

    # Check Frame_ID range vs video frame count
    cap_temp = cv2.VideoCapture(video_path)
    total_video_frames = int(cap_temp.get(cv2.CAP_PROP_FRAME_COUNT))
    video_fps = cap_temp.get(cv2.CAP_PROP_FPS)
    cap_temp.release()

    parquet_min_frame = df['Frame_ID'].min()
    parquet_max_frame = df['Frame_ID'].max()

    print(f"\nFrame Range Analysis:")
    print(f"  Video frames: 0 to {total_video_frames-1} (total: {total_video_frames})")
    print(f"  Parquet Frame_IDs: {parquet_min_frame} to {parquet_max_frame}")

    # Check if Frame_IDs need remapping
    if parquet_min_frame >= total_video_frames or parquet_max_frame < 0:
        print(f"\nWARNING: Frame_ID mismatch detected!")
        print(f"Parquet Frame_IDs don't overlap with video frame indices.")
        print(f"\nAttempting to remap Frame_IDs...")

        # Try timestamp-based mapping (assumes video starts at parquet's first frame)
        # Calculate expected frame offset
        offset = int(parquet_min_frame)
        print(f"Applying offset: Frame_ID_new = Frame_ID_old - {offset}")
        df['Frame_ID'] = df['Frame_ID'] - offset

        new_min = df['Frame_ID'].min()
        new_max = df['Frame_ID'].max()
        print(f"Remapped Frame_IDs: {new_min} to {new_max}")

        if new_max >= total_video_frames:
            print(f"\nWARNING: Remapped range still exceeds video length.")
            print(f"This parquet likely came from a different/longer video.")
            print(f"Visualization will only show overlays for matching frames.")

    cap = cv2.VideoCapture(video_path)
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fps = cap.get(cv2.CAP_PROP_FPS)
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))

    # Filter to only include frames within video range
    df = df[(df['Frame_ID'] >= 0) & (df['Frame_ID'] < total_frames)].copy()

    output_path = video_path.replace(".mp4", "_debug_overlay.mp4")

    # Try codecs in order of preference
    codecs_to_try = [
        ('mp4v', output_path),  # MPEG-4 Part 2 - most compatible with OpenCV
        ('MJPG', video_path.replace(".mp4", "_debug_overlay.avi")),  # Motion JPEG - very reliable
        ('XVID', video_path.replace(".mp4", "_debug_overlay.avi"))   # Xvid - fallback
    ]

    out = None
    for codec_name, codec_output_path in codecs_to_try:
        print(f"Trying codec: {codec_name}")
        fourcc = cv2.VideoWriter_fourcc(*codec_name)
        temp_out = cv2.VideoWriter(codec_output_path, fourcc, fps, (width, height))

        # Check if VideoWriter opened successfully
        if temp_out.isOpened():
            # Release the test writer and create a fresh one for actual use
            temp_out.release()
            out = cv2.VideoWriter(codec_output_path, fourcc, fps, (width, height))
            output_path = codec_output_path
            print(f"Successfully initialized VideoWriter with {codec_name} codec")
            break
        else:
            print(f"Codec {codec_name} failed to initialize")
            temp_out.release()
            continue

    if out is None or not out.isOpened():
        print("Error: Could not initialize VideoWriter with any codec.")
        cap.release()
        return

    print(f"Processing... Output will be saved to: {output_path}")

    # We need to map Frame_ID from parquet to video frames
    # The parquet might skip frames (if we sampled at 5FPS), but we want to draw on the original video
    # Strategy: For every frame in video, check if we have data in DF.
    # Note: If we sampled, we will only see overlays on sampled frames.

    # Create a lookup dictionary for faster access: {frame_id: [rows...]}
    # Because there might be multiple people per frame
    frame_data = {}
    for _, row in df.iterrows():
        fid = int(row['Frame_ID'])
        if fid not in frame_data:
            frame_data[fid] = []
        frame_data[fid].append(row)

    unique_tracks = df['Track_ID'].nunique()
    print(f"\nPose data ready:")
    print(f"  Frames with data: {len(frame_data)} out of {total_frames} video frames")
    print(f"  Unique Track IDs: {unique_tracks}")
    print(f"  Total detections: {len(df)}")

    # Keep track of last known pose for each track ID to avoid blinking
    last_known_poses = {}  # {track_id: row_data}
    max_persistence_frames = 30  # Keep pose for max 30 frames (~1 second at 30fps)
    pose_age = {}  # {track_id: frames_since_last_update}

    with tqdm(total=total_frames) as pbar:
        frame_idx = 0
        consecutive_failures = 0
        max_consecutive_failures = 10
        last_valid_frame = None

        while frame_idx < total_frames:
            ret, frame = cap.read()

            if not ret:
                consecutive_failures += 1

                if consecutive_failures >= max_consecutive_failures:
                    print(f"\nVideo capture failed after {consecutive_failures} consecutive errors at frame {frame_idx}")
                    print(f"Processed {frame_idx}/{total_frames} frames")
                    break

                # Try to skip the bad frame by setting position
                if last_valid_frame is not None:
                    # Use last valid frame for corrupted frames
                    frame = last_valid_frame.copy()
                    print(f"\nWarning: Failed to read frame {frame_idx}, using previous frame")
                else:
                    # Skip this frame and try next
                    frame_idx += 1
                    cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
                    pbar.update(1)
                    continue
            else:
                consecutive_failures = 0
                last_valid_frame = frame.copy()

            # Update poses if we have new data for this frame
            if frame_idx in frame_data:
                rows = frame_data[frame_idx]
                for row in rows:
                    tid = int(row['Track_ID'])
                    last_known_poses[tid] = row
                    pose_age[tid] = 0  # Reset age counter

            # Draw all tracked poses (both new and persisted)
            for tid, row in list(last_known_poses.items()):
                # Check if pose is still fresh enough to display
                if pose_age.get(tid, 0) <= max_persistence_frames:
                    color = COLORS[tid % len(COLORS)]
                    draw_skeleton(frame, row, color)
                    pose_age[tid] = pose_age.get(tid, 0) + 1
                else:
                    # Remove stale pose
                    del last_known_poses[tid]
                    if tid in pose_age:
                        del pose_age[tid]

            # Write frame
            out.write(frame)
            frame_idx += 1
            pbar.update(1)

    cap.release()
    out.release()
    cv2.destroyAllWindows()
    print("Done!")
    print(f"Video saved successfully to: {output_path}")

    # Verify the output file exists and has size > 0
    if os.path.exists(output_path):
        file_size = os.path.getsize(output_path)
        print(f"Output file size: {file_size / (1024*1024):.2f} MB")
    else:
        print("Warning: Output file was not created!")


if __name__ == "__main__":
    main()