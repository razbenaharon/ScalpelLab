import cv2
import pandas as pd
import numpy as np
import sys
import os
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


def draw_skeleton(frame, row, color):
    """Draws skeleton and ID on the frame based on a dataframe row."""
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
        if kp1[2] > 0.3 and kp2[2] > 0.3:
            pt1 = (int(kp1[0]), int(kp1[1]))
            pt2 = (int(kp2[0]), int(kp2[1]))
            cv2.line(frame, pt1, pt2, color, 2)

    # Draw Points
    for kp in kps:
        if kp[2] > 0.3:
            cv2.circle(frame, (int(kp[0]), int(kp[1])), 4, color, -1)

    # Draw ID above head
    nose = kps[0]
    if nose[2] > 0.3:
        label_pos = (int(nose[0]), int(nose[1]) - 20)
    else:
        label_pos = (int(kps[5][0]), int(kps[5][1]) - 20)

    track_id = int(row['Track_ID'])
    cv2.putText(frame, f"ID: {track_id}", label_pos,
                cv2.FONT_HERSHEY_SIMPLEX, 0.8, color, 2)


def main():
    if len(sys.argv) < 3:
        print("Usage: python live_visualize_overlay.py <video_path> <parquet_path>")
        video_path = input("Enter video path: ").strip('"')
        parquet_path = input("Enter parquet path: ").strip('"')
    else:
        video_path = sys.argv[1]
        parquet_path = sys.argv[2]

    if not os.path.exists(video_path) or not os.path.exists(parquet_path):
        print("Error: Files not found.")
        return

    print("Loading data...")
    df = pd.read_parquet(parquet_path)

    cap = cv2.VideoCapture(video_path)
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fps = cap.get(cv2.CAP_PROP_FPS)
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))

    # --- Setup Output (Optional) ---
    # If you purely want to watch live and NOT save the file,
    # you can comment out the VideoWriter lines below.
    output_path = video_path.replace(".mp4", "_debug_overlay.mp4")
    codecs_to_try = [('mp4v', output_path), ('MJPG', video_path.replace(".mp4", "_debug_overlay.avi"))]

    out = None
    # Initialize writer (Keep this if you still want to save the result while watching)
    for codec_name, codec_output_path in codecs_to_try:
        fourcc = cv2.VideoWriter_fourcc(*codec_name)
        temp_out = cv2.VideoWriter(codec_output_path, fourcc, fps, (width, height))
        if temp_out.isOpened():
            temp_out.release()
            out = cv2.VideoWriter(codec_output_path, fourcc, fps, (width, height))
            output_path = codec_output_path
            break

    print(f"Processing... Press 'q' to stop early.")

    frame_data = {}
    for _, row in df.iterrows():
        fid = int(row['Frame_ID'])
        if fid not in frame_data: frame_data[fid] = []
        frame_data[fid].append(row)

    last_known_poses = {}
    pose_age = {}
    max_persistence_frames = 30

    # Create a named window that can be resized
    cv2.namedWindow("Live Pose Overlay", cv2.WINDOW_NORMAL)

    with tqdm(total=total_frames) as pbar:
        frame_idx = 0
        while frame_idx < total_frames:
            ret, frame = cap.read()
            if not ret:
                break

            # Update poses
            if frame_idx in frame_data:
                rows = frame_data[frame_idx]
                for row in rows:
                    tid = int(row['Track_ID'])
                    last_known_poses[tid] = row
                    pose_age[tid] = 0

            # Draw poses
            for tid, row in list(last_known_poses.items()):
                if pose_age.get(tid, 0) <= max_persistence_frames:
                    color = COLORS[tid % len(COLORS)]
                    draw_skeleton(frame, row, color)
                    pose_age[tid] = pose_age.get(tid, 0) + 1
                else:
                    del last_known_poses[tid]
                    if tid in pose_age: del pose_age[tid]

            # --- LIVE VISUALIZATION ---
            cv2.imshow("Live Pose Overlay", frame)

            # Wait 1ms for key press. If 'q' is pressed, exit.
            if cv2.waitKey(1) & 0xFF == ord('q'):
                print("\nStopped by user.")
                break
            # --------------------------

            if out:
                out.write(frame)

            frame_idx += 1
            pbar.update(1)

    cap.release()
    if out:
        out.release()
    cv2.destroyAllWindows()
    print("Done!")


if __name__ == "__main__":
    main()