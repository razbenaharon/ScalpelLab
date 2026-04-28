"""
Track Post-Processing: Filter, Merge, Interpolate, and Smooth

This script cleans up tracking results by:
1. Filtering out short/noisy tracks
2. Merging fragmented tracks (same person split across multiple IDs)
3. Interpolating missing frames between detections
4. Smoothing keypoint coordinates with rolling average

USAGE:
    python 3_process_tracks.py <parquet_path>

    If no argument provided, prompts for path.

INPUT:
    Parquet file from pose estimation (*_keypoints.parquet)

OUTPUT:
    Cleaned parquet file (*_keypoints_cleaned.parquet) with:
    - Fewer unique Track_IDs (merged fragments)
    - More rows (interpolated missing frames)
    - Smoother coordinate values

CONFIGURATION:
    Edit the CONFIG dictionary below:

    min_track_duration_sec - Minimum track duration to keep (filters noise/false positives)
                             Tracks shorter than this are removed. Default: 1.5s

    max_merge_gap_sec      - Maximum time gap to merge two track fragments
                             If track B starts within this time after track A ends,
                             and they're spatially close, merge them. Default: 3.0s

    max_merge_dist_px      - Maximum pixel distance to merge tracks
                             Uses centroid of shoulders+hips. Default: 300px

    smooth_window_size     - Rolling average window for smoothing coordinates
                             Higher = smoother but more lag. Default: 5 frames

    fill_missing_frames    - Whether to linearly interpolate gaps
                             Default: True

ALGORITHM:
    1. Filter: Remove tracks < min_track_duration_sec
    2. Merge: For each track, look for subsequent tracks that:
       - Start within max_merge_gap_sec of current track's end
       - Start position is within max_merge_dist_px of current track's end position
       - If found, merge by reassigning Track_ID
    3. Interpolate: For each track, fill missing Frame_IDs with linear interpolation
    4. Smooth: Apply rolling average to x,y coordinates

REQUIREMENTS:
    pip install pandas numpy scipy pyarrow

TYPICAL WORKFLOW:
    1. Run pose estimation: python 1_pose_anesthesiologist.py video.mp4
    2. Inspect results: python 2_inspect_parquet.py video_keypoints.parquet
    3. Process tracks: python 3_process_tracks.py video_keypoints.parquet
    4. Visualize: python live_visualize_overlay.py video.mp4 video_keypoints_cleaned.parquet
"""

import pandas as pd
import numpy as np
import sys
import os
from scipy.spatial import distance

# ==============================================================================
# CONFIGURATION
# ==============================================================================
CONFIG = {
    "min_track_duration_sec": 1.5,  # Minimum duration to keep a track (filters noise)
    "max_merge_gap_sec": 3.0,  # Max time gap allowed to merge two tracks
    "max_merge_dist_px": 300,  # Max pixel distance allowed to merge two tracks
    "smooth_window_size": 5,  # Window size for rolling average smoothing
    "fill_missing_frames": True  # Whether to interpolate gaps
}


def calculate_track_centroids(df):
    """Calculates the center of mass (x, y) for each row to help with distance matching."""
    # We use shoulders and hips as stable body markers
    x_cols = ['Left_Shoulder_x', 'Right_Shoulder_x', 'Left_Hip_x', 'Right_Hip_x']
    y_cols = ['Left_Shoulder_y', 'Right_Shoulder_y', 'Left_Hip_y', 'Right_Hip_y']

    # Calculate mean ignoring NaNs
    df['centroid_x'] = df[x_cols].mean(axis=1)
    df['centroid_y'] = df[y_cols].mean(axis=1)
    return df


def filter_short_tracks(df):
    """Removes tracks that are too short (noise)."""
    print("\n--- Step 1: Filtering Noise ---")
    track_durations = df.groupby('Track_ID')['Timestamp'].agg(lambda x: x.max() - x.min())

    valid_ids = track_durations[track_durations >= CONFIG['min_track_duration_sec']].index
    n_removed = len(track_durations) - len(valid_ids)

    print(f"Removed {n_removed} short tracks (duration < {CONFIG['min_track_duration_sec']}s)")
    print(f"Remaining valid tracks: {list(valid_ids)}")

    return df[df['Track_ID'].isin(valid_ids)].copy()


def merge_tracks(df):
    """
    The core logic: Iteratively attempts to merge broken tracks.
    It looks at the end of one track and the start of another.
    """
    print("\n--- Step 2: Merging Fragmented Tracks ---")

    # Calculate centroids for distance measurement
    df = calculate_track_centroids(df)

    # Get summary of each track: start_time, end_time, start_pos, end_pos
    tracks_summary = []
    for tid, group in df.groupby('Track_ID'):
        group = group.sort_values('Frame_ID')
        tracks_summary.append({
            'id': tid,
            'start_frame': group['Frame_ID'].iloc[0],
            'end_frame': group['Frame_ID'].iloc[-1],
            'start_time': group['Timestamp'].iloc[0],
            'end_time': group['Timestamp'].iloc[-1],
            'start_pos': (group['centroid_x'].iloc[0], group['centroid_y'].iloc[0]),
            'end_pos': (group['centroid_x'].iloc[-1], group['centroid_y'].iloc[-1]),
            'count': len(group)
        })

    # Sort by start time
    tracks_summary.sort(key=lambda x: x['start_time'])

    # Mapping of old_id -> new_id
    id_map = {t['id']: t['id'] for t in tracks_summary}

    # Iterate and try to merge
    for i in range(len(tracks_summary)):
        current = tracks_summary[i]
        curr_id = id_map[current['id']]  # Get current mapped ID

        # Look ahead at future tracks
        for j in range(i + 1, len(tracks_summary)):
            next_track = tracks_summary[j]
            next_id = id_map[next_track['id']]

            # If already merged or same ID, skip
            if curr_id == next_id:
                continue

            # Check Time Gap
            time_gap = next_track['start_time'] - current['end_time']

            # If overlap (gap < 0), we assume they are different people (unless gap is tiny jitter)
            if time_gap < -0.5:
                continue  # Overlapping tracks are likely different people

            if time_gap > CONFIG['max_merge_gap_sec']:
                break  # Tracks are too far apart in time, stop looking for this track

            # Check Spatial Distance (Euclidean distance between End of A and Start of B)
            # Handle NaNs in positions
            if np.isnan(current['end_pos'][0]) or np.isnan(next_track['start_pos'][0]):
                dist = 0  # Fallback if centroids missing
            else:
                dist = distance.euclidean(current['end_pos'], next_track['start_pos'])

            if dist <= CONFIG['max_merge_dist_px']:
                # MATCH FOUND! Merge Next into Current
                print(f"Merging Track {next_track['id']} -> {curr_id} (Gap: {time_gap:.2f}s, Dist: {dist:.1f}px)")

                # Update map: All future references to next_track['id'] become curr_id
                # We also need to update any chains pointing to next_track['id']
                for k, v in id_map.items():
                    if v == next_id:
                        id_map[k] = curr_id

                # Update 'current' end stats to extend the chain
                current['end_time'] = next_track['end_time']
                current['end_pos'] = next_track['end_pos']

                # We successfully merged, continue checking from this extended current track
                # but we don't break, because we might merge yet another track later

    # Apply changes to DataFrame
    df['Track_ID'] = df['Track_ID'].map(id_map)
    return df


def interpolate_and_smooth(df):
    """
    Fills gaps (NaNs) and smoothes the movement.
    """
    print("\n--- Step 3: Interpolation & Smoothing ---")

    processed_dfs = []

    # Process each unique person separately
    for tid, group in df.groupby('Track_ID'):
        # Handle duplicate Frame_IDs from merged overlapping tracks
        # Keep the first occurrence (usually more reliable)
        group = group.drop_duplicates(subset='Frame_ID', keep='first')

        # 1. Reindex to full frame range (fill missing frames with NaNs)
        min_frame = group['Frame_ID'].min()
        max_frame = group['Frame_ID'].max()
        full_range = range(int(min_frame), int(max_frame) + 1)

        # Set Frame_ID as index for reindexing
        group = group.set_index('Frame_ID')

        # Reindex handles the missing frames between merged segments
        group = group.reindex(full_range)

        # Reset index to make Frame_ID a column again
        group = group.reset_index()
        group = group.rename(columns={'index': 'Frame_ID'})
        group['Track_ID'] = tid  # Fill Track_ID for the new rows

        # 2. Interpolate (Linear) - Fills the gaps
        # Limit direction='both' to fill small start/end gaps if needed
        cols_to_fix = [c for c in group.columns if c not in ['Frame_ID', 'Track_ID', 'Timestamp']]

        # Interpolate numeric columns
        group[cols_to_fix] = group[cols_to_fix].interpolate(method='linear', limit_direction='both')

        # Fill Timestamp
        group['Timestamp'] = group['Timestamp'].interpolate(method='linear')

        # 3. Smoothing (Rolling Average)
        # Apply only to coordinate columns (x, y)
        coord_cols = [c for c in cols_to_fix if '_x' in c or '_y' in c]
        group[coord_cols] = group[coord_cols].rolling(window=CONFIG['smooth_window_size'], min_periods=1,
                                                      center=True).mean()

        processed_dfs.append(group)

    if not processed_dfs:
        return df

    final_df = pd.concat(processed_dfs).sort_values(['Frame_ID', 'Track_ID'])
    return final_df


def main():
    if len(sys.argv) < 2:
        print("Usage: python 3_process_tracks.py <parquet_path>")
        parquet_path = input("Enter parquet path: ").strip('"')
    else:
        parquet_path = sys.argv[1]

    if not os.path.exists(parquet_path):
        print("Error: File not found")
        return

    print(f"Processing: {parquet_path}")
    df = pd.read_parquet(parquet_path)

    original_count = len(df)
    original_ids = df['Track_ID'].nunique()

    # 1. Filter
    df = filter_short_tracks(df)

    # 2. Merge
    df = merge_tracks(df)

    # 3. Interpolate & Smooth
    df = interpolate_and_smooth(df)

    # Save output
    output_path = parquet_path.replace('.parquet', '_cleaned.parquet')

    # Select original columns only (remove helper columns like centroid)
    final_cols = [c for c in df.columns if 'centroid' not in c]
    df[final_cols].to_parquet(output_path, index=False)

    print("\n" + "=" * 50)
    print("RESULTS")
    print("=" * 50)
    print(f"Original IDs: {original_ids} -> Final IDs: {df['Track_ID'].nunique()}")
    print(f"Original Rows: {original_count} -> Final Rows: {len(df)} (Added interpolated frames)")
    print(f"Saved to: {output_path}")
    print("=" * 50)
    print("Next Step: Run 'live_visualize_overlay.py' on the CLEANED file to verify.")


if __name__ == "__main__":
    main()