"""
Tracking Diagnostics: Analyze Keypoint Detection Quality

This script analyzes parquet files to diagnose tracking issues by examining
keypoint detection rates across different body regions.

USAGE:
    python diagnose_tracking.py [parquet_path]

    If no argument provided, prompts for path.

OUTPUT:
    Prints diagnostic report to console:
    - Total rows and unique Track IDs
    - Detection rates by body region (head, upper body, lower body)
    - Average confidence scores per keypoint
    - Per-track analysis showing which tracks have poor detection
    - Sample frame analysis
    - Summary with recommendations

BODY REGIONS ANALYZED:
    HEAD:       Nose, Left_Eye, Right_Eye, Left_Ear, Right_Ear
    UPPER BODY: Left_Shoulder, Right_Shoulder, Left_Elbow, Right_Elbow, Left_Wrist, Right_Wrist
    LOWER BODY: Left_Hip, Right_Hip, Left_Knee, Right_Knee, Left_Ankle, Right_Ankle

COMMON ISSUES DETECTED:
    - Upper body much lower than lower body: People partially out of frame (heads cut off)
    - Very low overall detection: Poor video quality, lighting, or wrong model
    - Single track with poor detection: Specific person in difficult position

REQUIREMENTS:
    pip install pandas numpy pyarrow

TYPICAL WORKFLOW:
    1. Run pose estimation: python 1_pose_anesthesiologist.py video.mp4
    2. If results look wrong, diagnose: python diagnose_tracking.py video_keypoints.parquet
    3. Based on diagnosis, adjust camera angle, lighting, or tracker settings
"""

import pandas as pd
import numpy as np
import sys

def diagnose_parquet(parquet_path):
    """Analyze parquet file to check keypoint detection quality."""

    print("\n" + "=" * 70)
    print("TRACKING DIAGNOSTICS")
    print("=" * 70)
    print(f"File: {parquet_path}")
    print()

    # Load parquet
    df = pd.read_parquet(parquet_path)

    print(f"Total rows: {len(df)}")
    print(f"Unique Track IDs: {df['Track_ID'].nunique()}")
    print(f"Track IDs: {sorted(df['Track_ID'].unique())}")
    print()

    # Define keypoint groups
    HEAD_KEYPOINTS = ['Nose', 'Left_Eye', 'Right_Eye', 'Left_Ear', 'Right_Ear']
    UPPER_BODY_KEYPOINTS = ['Left_Shoulder', 'Right_Shoulder', 'Left_Elbow', 'Right_Elbow', 'Left_Wrist', 'Right_Wrist']
    LOWER_BODY_KEYPOINTS = ['Left_Hip', 'Right_Hip', 'Left_Knee', 'Right_Knee', 'Left_Ankle', 'Right_Ankle']

    # Check detection rates for each keypoint group
    print("=" * 70)
    print("KEYPOINT DETECTION RATES (confidence > 0.3)")
    print("=" * 70)

    for group_name, keypoints in [
        ("HEAD", HEAD_KEYPOINTS),
        ("UPPER BODY", UPPER_BODY_KEYPOINTS),
        ("LOWER BODY", LOWER_BODY_KEYPOINTS)
    ]:
        print(f"\n{group_name}:")
        total_detected = 0
        total_possible = 0

        for kpt in keypoints:
            conf_col = f"{kpt}_conf"
            if conf_col in df.columns:
                detected = (df[conf_col] > 0.3).sum()
                total = len(df)
                total_detected += detected
                total_possible += total
                detection_rate = 100 * detected / total if total > 0 else 0

                print(f"  {kpt:20s}: {detection_rate:5.1f}% ({detected:5d}/{total:5d})")

        if total_possible > 0:
            overall_rate = 100 * total_detected / total_possible
            print(f"  {'Overall ' + group_name:20s}: {overall_rate:5.1f}%")

    # Check average confidence scores
    print("\n" + "=" * 70)
    print("AVERAGE CONFIDENCE SCORES (when detected)")
    print("=" * 70)

    for group_name, keypoints in [
        ("HEAD", HEAD_KEYPOINTS),
        ("UPPER BODY", UPPER_BODY_KEYPOINTS),
        ("LOWER BODY", LOWER_BODY_KEYPOINTS)
    ]:
        print(f"\n{group_name}:")
        confidences = []

        for kpt in keypoints:
            conf_col = f"{kpt}_conf"
            if conf_col in df.columns:
                # Only consider detected keypoints (conf > 0.3)
                valid_conf = df[df[conf_col] > 0.3][conf_col]
                if len(valid_conf) > 0:
                    avg_conf = valid_conf.mean()
                    confidences.append(avg_conf)
                    print(f"  {kpt:20s}: {avg_conf:.3f}")
                else:
                    print(f"  {kpt:20s}: No detections")

        if confidences:
            print(f"  {'Overall ' + group_name:20s}: {np.mean(confidences):.3f}")

    # Check for tracks with missing upper body
    print("\n" + "=" * 70)
    print("TRACKS WITH POOR UPPER BODY DETECTION")
    print("=" * 70)

    # For each track, calculate upper body detection rate
    track_stats = []

    for track_id in sorted(df['Track_ID'].unique()):
        track_df = df[df['Track_ID'] == track_id]

        # Count upper body detections
        upper_body_detected = 0
        total_frames = len(track_df)

        for kpt in UPPER_BODY_KEYPOINTS:
            conf_col = f"{kpt}_conf"
            if conf_col in track_df.columns:
                upper_body_detected += (track_df[conf_col] > 0.3).sum()

        upper_body_rate = 100 * upper_body_detected / (total_frames * len(UPPER_BODY_KEYPOINTS))

        # Count lower body detections
        lower_body_detected = 0

        for kpt in LOWER_BODY_KEYPOINTS:
            conf_col = f"{kpt}_conf"
            if conf_col in track_df.columns:
                lower_body_detected += (track_df[conf_col] > 0.3).sum()

        lower_body_rate = 100 * lower_body_detected / (total_frames * len(LOWER_BODY_KEYPOINTS))

        track_stats.append({
            'track_id': track_id,
            'frames': total_frames,
            'upper_body_rate': upper_body_rate,
            'lower_body_rate': lower_body_rate
        })

    # Sort by upper body rate
    track_stats.sort(key=lambda x: x['upper_body_rate'])

    print("\nTracks with lowest upper body detection:")
    print(f"{'Track ID':>10s} {'Frames':>8s} {'Upper Body':>12s} {'Lower Body':>12s}")
    print("-" * 50)

    for stat in track_stats[:10]:  # Show worst 10
        print(f"{stat['track_id']:10d} {stat['frames']:8d} {stat['upper_body_rate']:11.1f}% {stat['lower_body_rate']:11.1f}%")

    # Check sample frames
    print("\n" + "=" * 70)
    print("SAMPLE FRAME ANALYSIS (First 5 detections)")
    print("=" * 70)

    for idx in range(min(5, len(df))):
        row = df.iloc[idx]
        print(f"\nFrame {row['Frame_ID']}, Track ID {row['Track_ID']}:")

        # Check which keypoints are detected
        head_detected = sum(1 for kpt in HEAD_KEYPOINTS if row[f"{kpt}_conf"] > 0.3)
        upper_detected = sum(1 for kpt in UPPER_BODY_KEYPOINTS if row[f"{kpt}_conf"] > 0.3)
        lower_detected = sum(1 for kpt in LOWER_BODY_KEYPOINTS if row[f"{kpt}_conf"] > 0.3)

        print(f"  Head keypoints:       {head_detected}/{len(HEAD_KEYPOINTS)}")
        print(f"  Upper body keypoints: {upper_detected}/{len(UPPER_BODY_KEYPOINTS)}")
        print(f"  Lower body keypoints: {lower_detected}/{len(LOWER_BODY_KEYPOINTS)}")

        # Show specific keypoint positions
        print(f"  Nose position: ({row['Nose_x']:.1f}, {row['Nose_y']:.1f}) conf={row['Nose_conf']:.2f}")
        print(f"  Left Shoulder: ({row['Left_Shoulder_x']:.1f}, {row['Left_Shoulder_y']:.1f}) conf={row['Left_Shoulder_conf']:.2f}")
        print(f"  Left Hip:      ({row['Left_Hip_x']:.1f}, {row['Left_Hip_y']:.1f}) conf={row['Left_Hip_conf']:.2f}")
        print(f"  Left Knee:     ({row['Left_Knee_x']:.1f}, {row['Left_Knee_y']:.1f}) conf={row['Left_Knee_conf']:.2f}")

    # Summary recommendation
    print("\n" + "=" * 70)
    print("DIAGNOSIS SUMMARY")
    print("=" * 70)

    # Calculate overall detection rates
    upper_body_cols = [f"{kpt}_conf" for kpt in UPPER_BODY_KEYPOINTS]
    lower_body_cols = [f"{kpt}_conf" for kpt in LOWER_BODY_KEYPOINTS]

    upper_detected = sum((df[col] > 0.3).sum() for col in upper_body_cols)
    upper_total = len(df) * len(upper_body_cols)
    upper_rate = 100 * upper_detected / upper_total

    lower_detected = sum((df[col] > 0.3).sum() for col in lower_body_cols)
    lower_total = len(df) * len(lower_body_cols)
    lower_rate = 100 * lower_detected / lower_total

    print(f"\nOverall upper body detection: {upper_rate:.1f}%")
    print(f"Overall lower body detection: {lower_rate:.1f}%")

    if upper_rate < 50 and lower_rate > 70:
        print("\n⚠️  WARNING: Upper body detection is significantly lower than lower body!")
        print("   This suggests people are partially out of frame (upper body cut off)")
        print("   or the camera angle is pointing downward.")
        print("\n   Recommendations:")
        print("   1. Check your video - are people's upper bodies visible?")
        print("   2. Verify camera angle is not too low")
        print("   3. Check if people are standing close to camera (heads cut off)")
        print("   4. Consider using a different camera position")
    elif upper_rate < 30:
        print("\n⚠️  CRITICAL: Very low upper body detection!")
        print("   Possible causes:")
        print("   1. People are partially out of frame")
        print("   2. YOLO model confidence threshold is too high")
        print("   3. Video quality is poor")
        print("   4. Lighting issues")
    else:
        print("\n✓ Detection rates look reasonable.")
        print("  Both upper and lower body keypoints are being detected.")

    print("\n" + "=" * 70)


if __name__ == "__main__":
    if len(sys.argv) > 1:
        parquet_path = sys.argv[1]
    else:
        parquet_path = input("Enter path to parquet file: ").strip().strip('"').strip("'")

    diagnose_parquet(parquet_path)
