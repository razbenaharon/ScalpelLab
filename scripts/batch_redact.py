"""
Batch Video Redaction Script
Redacts videos based on Excel file with case time ranges.
Uses GPU-accelerated parallel processing.

Features:
- Tracks successfully processed files to avoid re-processing
- GPU-accelerated parallel processing
- Comprehensive reporting
"""

import sys
import os
import time
import json
import re
import sqlite3
import subprocess
import threading
from datetime import datetime, timedelta
from concurrent.futures import ProcessPoolExecutor, as_completed

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.helpers.handle_xlsx import handle_xlsx
from config import get_db_path
import pandas as pd
import numpy as np


# ============================================================================
# CONFIGURATION
# ============================================================================
CONFIG = {
    # Input Excel file path (leave empty to prompt user)
    'XLSX_PATH': 'F:/Room_8_Data/Scalpel_Raz/times.xlsx',  # Example: 'F:/Room_8_Data/Scalpel_Raz/times.xlsx'

    # Output directory for redacted videos (leave empty for same as input)
    'OUTPUT_DIR': 'D:\Recordings',  # Example: 'F:/Room_8_Data/Output'

    # Number of parallel workers (recommended: 6 for RTX A2000, 2-8 for other GPUs)
    'NUM_WORKERS': 8,

    # Tracking file to store processed files (prevents re-processing)
    'TRACKING_FILE': 'F:/Room_8_Data/Scalpel_Raz/docs/redaction_tracking.json',
}
# ============================================================================


# ============================================================================
# VIDEO REDACTION FUNCTIONS
# ============================================================================

def probe_video(input_file: str) -> dict:
    """Get video properties."""
    cmd = [
        'ffprobe', '-v', 'error',
        '-show_entries', 'format=duration,bit_rate,size',
        '-of', 'json', input_file
    ]

    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    data = json.loads(result.stdout)
    format_info = data['format']

    # Calculate bitrate
    if 'bit_rate' in format_info:
        bitrate = int(format_info['bit_rate'])
    else:
        size_bytes = int(format_info.get('size', 0))
        duration = float(format_info['duration'])
        bitrate = int((size_bytes * 8) / duration) if duration > 0 else 500000

    return {
        'duration': float(format_info['duration']),
        'bitrate': bitrate
    }


def time_to_seconds(time_str: str) -> float:
    """Convert HH:MM:SS or MM:SS or seconds to float."""
    parts = time_str.split(':')
    if len(parts) == 3:
        h, m, s = map(float, parts)
        return h * 3600 + m * 60 + s
    elif len(parts) == 2:
        m, s = map(float, parts)
        return m * 60 + s
    return float(parts[0])


def monitor_output_size(output_file: str, stop_event: threading.Event):
    """Monitor and display output file size during encoding."""
    while not stop_event.is_set():
        if os.path.exists(output_file):
            size_mb = os.path.getsize(output_file) / (1024 * 1024)
            print(f"\rOutput size: {size_mb:.1f} MB", end='', flush=True)
        time.sleep(0.5)


def process_single_video_from_row(args):
    """
    Worker function to process a single video row.
    Designed to be called by ProcessPoolExecutor.

    Args:
        args: Tuple of (idx, row_dict, output_dir, total_videos)

    Returns:
        Tuple of (success, report_data or None, error_message or None)
    """
    idx, row_dict, output_dir, total_videos = args

    try:
        input_file = row_dict['path']

        if not os.path.exists(input_file):
            return (False, None, f"File not found: {input_file}")

        print(f"\n{'='*60}")
        print(f"[Worker] Processing video {idx + 1}/{total_videos}")
        print(f"{'='*60}")
        print(f"Input: {input_file}")

        # Collect all case time ranges
        case_ranges = []
        case_num = 1

        print("\nExtracting case time ranges from dataframe:")

        while True:
            start_col = f'start time - case {case_num}'
            end_col = f'end time - case {case_num}'

            if start_col not in row_dict or end_col not in row_dict:
                break

            start_val = row_dict[start_col]
            end_val = row_dict[end_col]

            # Skip if both are NaN
            if pd.isna(start_val) and pd.isna(end_val):
                case_num += 1
                continue

            # Skip if either is NaN
            if pd.isna(start_val) or pd.isna(end_val):
                case_num += 1
                continue

            case_ranges.append({
                'case': case_num,
                'start': str(start_val),
                'end': str(end_val)
            })
            case_num += 1

        if not case_ranges:
            return (False, None, "No valid case ranges found")

        print(f"\nFound {len(case_ranges)} valid case range(s):")
        for cr in case_ranges:
            print(f"  Case {cr['case']}: '{cr['start']}' -> '{cr['end']}'")

        # Probe video
        print("\nAnalyzing video...")
        video_info = probe_video(input_file)
        duration = video_info['duration']
        bitrate = video_info['bitrate']

        print(f"  Duration: {duration:.1f}s ({duration/60:.1f} min)")
        print(f"  Bitrate: {bitrate//1000} kbps")

        # Convert time ranges to seconds
        time_ranges_sec = []
        for cr in case_ranges:
            start_str = cr['start']
            end_str = cr['end']

            start_sec = time_to_seconds(start_str)

            if end_str.lower() == 'end':
                end_sec = duration
            else:
                end_sec = time_to_seconds(end_str)

            # Validate time range
            if start_sec >= end_sec:
                print(f"\n[ERROR] Case {cr['case']}: Invalid time range - skipping this case")
                print(f"  Start: {start_str} = {start_sec:.1f}s")
                print(f"  End: {end_str} = {end_sec:.1f}s")
                continue

            # Validate against video duration
            if start_sec > duration:
                print(f"\n[WARNING] Case {cr['case']}: Start time exceeds video duration - skipping")
                continue

            if end_sec > duration:
                print(f"\n[WARNING] Case {cr['case']}: End time exceeds video duration - clamping")
                end_sec = duration

            time_ranges_sec.append({
                'case': cr['case'],
                'start': start_sec,
                'end': end_sec
            })

        if not time_ranges_sec:
            return (False, None, "No valid time ranges after validation")

        # Check if gap after last case is > 1 hour
        # If so, trim the video to end 1 hour after the last case
        sorted_ranges = sorted(time_ranges_sec, key=lambda x: x['start'])
        last_case_end = sorted_ranges[-1]['end']
        gap_to_end = duration - last_case_end

        if gap_to_end > 3600:
            # Trim video to 1 hour after last case
            new_duration = last_case_end + 3600
            print(f"\n[INFO] Gap after last case is {gap_to_end/3600:.1f} hours")
            print(f"  Original duration: {duration:.1f}s")
            print(f"  Last case ends at: {last_case_end:.1f}s")
            print(f"  Trimming video to: {new_duration:.1f}s (1 hour after last case)")
            duration = new_duration

        # Build enable expressions for ffmpeg
        case_condition_parts = [f"between(t,{tr['start']},{tr['end']})" for tr in time_ranges_sec]
        case_condition = '+'.join(case_condition_parts)

        # Filter string
        filter_str = (
            f"drawbox=x=0:y=0:w=iw:h=ih:color=black:t=fill:enable='not({case_condition})',"
            f"drawbox=x=2*iw/3:y=ih/2:w=iw/3:h=ih/2:color=black:t=fill:enable='{case_condition}'"
        )

        # Determine output file
        if output_dir:
            # Preserve directory structure: dateX/caseY/camera_name/file.mp4
            # E.g., input: "F:\Room_8_Data\Recordings\DATA_23-02-06\Case1\Patient_Monitor\Patient_Monitor.mp4"
            # becomes: "D:\Recordings\DATA_23-02-06\Case1\Patient_Monitor\Patient_Monitor_redacted.mp4"

            input_path = os.path.normpath(input_file)
            path_parts = input_path.split(os.sep)

            # Find the relative path from "Recordings" folder onwards
            # Look for common parent folders like "Recordings", "DATA_", or just use last 3 parts
            relative_parts = []
            for i, part in enumerate(path_parts):
                if 'Recordings' in part or part.startswith('DATA_'):
                    # Take everything from this point onwards except the filename
                    relative_parts = path_parts[i:]
                    break

            # If we didn't find a recognizable structure, use last 3 parts (date/case/camera)
            if not relative_parts and len(path_parts) >= 3:
                relative_parts = path_parts[-3:]
            elif not relative_parts:
                relative_parts = path_parts[-1:]

            # Remove "Recordings" from the path if it's there
            if relative_parts and 'Recordings' in relative_parts[0]:
                relative_parts = relative_parts[1:]

            # Construct output path
            if len(relative_parts) > 1:
                # Has directory structure
                output_subdir = os.path.join(output_dir, *relative_parts[:-1])
                os.makedirs(output_subdir, exist_ok=True)
                base_name = os.path.splitext(relative_parts[-1])[0]
                output_file = os.path.join(output_subdir, f"{base_name}_redacted.mp4")
            else:
                # Just a filename
                os.makedirs(output_dir, exist_ok=True)
                base_name = os.path.splitext(relative_parts[-1])[0]
                output_file = os.path.join(output_dir, f"{base_name}_redacted.mp4")
        else:
            base_dir = os.path.dirname(input_file) or '.'
            base_name = os.path.splitext(os.path.basename(input_file))[0]
            output_file = os.path.join(base_dir, f"{base_name}_redacted.mp4")

        print(f"\nOutput: {output_file}")
        print(f"\nProcessing with GPU acceleration...")

        # FFmpeg command with NVENC GPU acceleration
        cmd = [
            'ffmpeg', '-y',
            '-hide_banner', '-loglevel', 'error',  # Reduced logging for parallel processing
            '-hwaccel', 'cuda',
            '-i', input_file,
            '-t', str(duration),  # Trim video to specified duration
            '-vf', filter_str,
            '-c:v', 'h264_nvenc',
            '-preset', 'p1',
            '-b:v', str(bitrate),
            '-maxrate', str(int(bitrate * 1.1)),
            '-bufsize', str(int(bitrate * 2)),
            '-c:a', 'copy',
            '-movflags', '+faststart',
            output_file
        ]

        # Run FFmpeg
        try:
            subprocess.run(cmd, check=True, capture_output=True)
        except subprocess.CalledProcessError as e:
            print("\n[WARNING] GPU encoding failed, trying CPU fallback...")

            cmd_cpu = [
                'ffmpeg', '-y',
                '-hide_banner', '-loglevel', 'error',
                '-i', input_file,
                '-t', str(duration),  # Trim video to specified duration
                '-vf', filter_str,
                '-c:v', 'libx264',
                '-preset', 'faster',
                '-crf', '23',
                '-c:a', 'copy',
                '-movflags', '+faststart',
                output_file
            ]

            subprocess.run(cmd_cpu, check=True, capture_output=True)

        # Verify output
        if os.path.exists(output_file):
            orig_size = os.path.getsize(input_file) / (1024 * 1024)
            new_size = os.path.getsize(output_file) / (1024 * 1024)

            print(f"\n[SUCCESS] Video {idx + 1}/{total_videos}")
            print(f"  Size: {orig_size:.1f} MB -> {new_size:.1f} MB")

            # Calculate black periods for report
            black_periods = []

            if sorted_ranges and sorted_ranges[0]['start'] > 0:
                black_periods.append({'start': 0, 'end': sorted_ranges[0]['start']})

            for i in range(len(sorted_ranges) - 1):
                gap_start = sorted_ranges[i]['end']
                gap_end = sorted_ranges[i + 1]['start']
                if gap_end > gap_start:
                    black_periods.append({'start': gap_start, 'end': gap_end})

            if sorted_ranges and sorted_ranges[-1]['end'] < duration:
                black_periods.append({'start': sorted_ranges[-1]['end'], 'end': duration})

            report_data = {
                'file': os.path.basename(input_file),
                'duration': duration,
                'case_ranges': sorted_ranges,
                'black_periods': black_periods,
                'output_file': output_file
            }

            return (True, report_data, None)
        else:
            return (False, None, "Output file not created")

    except Exception as e:
        return (False, None, f"Error: {str(e)}")


def redact_videos_from_df(df: pd.DataFrame, output_dir: str = None, num_workers: int = 6,
                         on_video_complete: callable = None) -> list:
    """
    Process videos from dataframe with case-based redaction using parallel GPU processing.

    For each video:
    - During case time ranges: small black box in corner (rest visible)
    - Outside all case ranges: entire screen black

    Args:
        df: DataFrame with columns: 'path', 'start time - case X', 'end time - case X'
        output_dir: Directory for output files (optional)
        num_workers: Number of parallel workers (default: 6 for RTX A2000, 2-8 recommended)
        on_video_complete: Optional callback function called after each video completes successfully.
                          Receives (video_path, case_ranges, duration, report_data)

    Returns:
        Tuple of (output_files, success_count, failed_count, file_statuses, processing_report)
    """
    output_files = []
    processing_report = []  # Store report data for each video
    file_statuses = {}  # Track status of each file by index
    total_videos = len(df)

    print(f"\nUsing {num_workers} parallel workers for GPU-accelerated processing")
    print(f"Total videos to process: {total_videos}\n")

    # Prepare arguments for each video
    tasks = []
    for idx, row in df.iterrows():
        row_dict = row.to_dict()
        tasks.append((idx, row_dict, output_dir, total_videos))

    # Process videos in parallel
    success_count = 0
    failed_count = 0

    with ProcessPoolExecutor(max_workers=num_workers) as executor:
        # Submit all tasks
        future_to_idx = {executor.submit(process_single_video_from_row, task): task[0] for task in tasks}

        # Process results as they complete
        for future in as_completed(future_to_idx):
            idx = future_to_idx[future]
            try:
                success, report_data, error_msg = future.result()

                if success:
                    output_files.append(report_data['output_file'])
                    processing_report.append(report_data)
                    success_count += 1
                    file_statuses[idx] = "SUCCESS"

                    # Call the completion callback if provided
                    if on_video_complete:
                        try:
                            video_path = df.iloc[idx]['path']
                            on_video_complete(
                                video_path,
                                report_data['case_ranges'],
                                report_data['duration'],
                                report_data
                            )
                        except Exception as e:
                            print(f"\n[WARNING] Completion callback failed for video {idx + 1}: {e}")
                else:
                    print(f"\n[FAILED] Video {idx + 1}: {error_msg}")
                    failed_count += 1
                    file_statuses[idx] = f"FAILED: {error_msg}"

            except Exception as e:
                print(f"\n[ERROR] Video {idx + 1}: {str(e)}")
                failed_count += 1
                file_statuses[idx] = f"ERROR: {str(e)}"

    print(f"\n{'='*60}")
    print(f"BATCH COMPLETE: {success_count}/{total_videos} videos processed successfully")
    print(f"Failed: {failed_count}")
    print(f"{'='*60}\n")

    # Print comprehensive report
    if processing_report:
        print(f"\n{'='*80}")
        print(f"REDACTION REPORT")
        print(f"{'='*80}\n")

        for i, report in enumerate(processing_report, 1):
            print(f"Video {i}: {report['file']}")
            print(f"  Total Duration: {report['duration']:.1f}s ({report['duration']/60:.1f} min)")
            print(f"\n  CASE TIMES (Corner box visible):")

            if report['case_ranges']:
                total_case_time = 0
                for cr in report['case_ranges']:
                    case_duration = cr['end'] - cr['start']
                    total_case_time += case_duration
                    print(f"    Case {cr['case']}: {cr['start']:.1f}s -> {cr['end']:.1f}s (duration: {case_duration:.1f}s)")
                print(f"    Total case time: {total_case_time:.1f}s ({total_case_time/60:.1f} min)")
            else:
                print(f"    None")

            print(f"\n  FULL BLACK TIMES:")
            if report['black_periods']:
                total_black_time = 0
                for j, bp in enumerate(report['black_periods'], 1):
                    black_duration = bp['end'] - bp['start']
                    total_black_time += black_duration
                    print(f"    Period {j}: {bp['start']:.1f}s -> {bp['end']:.1f}s (duration: {black_duration:.1f}s)")
                print(f"    Total black time: {total_black_time:.1f}s ({total_black_time/60:.1f} min)")
            else:
                print(f"    None")

            print(f"\n  SUMMARY:")
            case_pct = (sum(cr['end'] - cr['start'] for cr in report['case_ranges']) / report['duration']) * 100 if report['case_ranges'] else 0
            black_pct = (sum(bp['end'] - bp['start'] for bp in report['black_periods']) / report['duration']) * 100 if report['black_periods'] else 0
            print(f"    Case time: {case_pct:.1f}% of video")
            print(f"    Black time: {black_pct:.1f}% of video")
            print(f"\n{'-'*80}\n")

        print(f"{'='*80}\n")

    return output_files, success_count, failed_count, file_statuses, processing_report


# ============================================================================
# DATABASE UPDATE FUNCTIONS
# ============================================================================

def parse_video_path(video_path):
    """
    Extract recording_date, case_no, and camera_name from video path.

    Expected path format: .../DATA_YY-MM-DD/CaseN/CameraName/video.mp4

    Args:
        video_path: Full path to the video file

    Returns:
        Tuple of (recording_date, case_no, camera_name) or (None, None, None) if parsing fails
    """
    try:
        # Normalize path
        normalized_path = os.path.normpath(video_path)
        parts = normalized_path.split(os.sep)

        # Find DATA_YY-MM-DD pattern
        recording_date = None
        case_no = None
        camera_name = None

        for i, part in enumerate(parts):
            # Match DATA_YY-MM-DD
            date_match = re.match(r'DATA_(\d{2})-(\d{2})-(\d{2})', part)
            if date_match:
                yy, mm, dd = date_match.groups()
                # Convert 2-digit year to 4-digit (assuming 20YY for YY <= 69, 19YY otherwise)
                yyyy = f"20{yy}" if int(yy) <= 69 else f"19{yy}"
                recording_date = f"{yyyy}-{mm}-{dd}"

                # Next part should be CaseN
                if i + 1 < len(parts):
                    case_match = re.match(r'Case(\d+)', parts[i + 1])
                    if case_match:
                        case_no = int(case_match.group(1))

                        # Next part should be camera name
                        if i + 2 < len(parts):
                            camera_name = parts[i + 2]
                            break

        if recording_date and case_no is not None and camera_name:
            return recording_date, case_no, camera_name
        else:
            return None, None, None

    except Exception as e:
        print(f"Warning: Could not parse video path '{video_path}': {e}")
        return None, None, None


def calculate_black_segments(case_ranges, video_duration):
    """
    Calculate pre and post black segments for each case.

    Logic:
    - Case 1 pre: from 0 to start of case 1
    - Between cases: split gap equally (post for case N, pre for case N+1)
    - Last case post: from end of case to min(video end, case_end + 3600)

    Args:
        case_ranges: List of dicts with {'case': int, 'start': float, 'end': float}
        video_duration: Total video duration in seconds

    Returns:
        Dict mapping case_no -> {'pre': float, 'post': float} (in minutes)
    """
    if not case_ranges:
        return {}

    # Sort by start time
    sorted_ranges = sorted(case_ranges, key=lambda x: x['start'])

    result = {}

    for i, case_range in enumerate(sorted_ranges):
        case_no = case_range['case']
        case_start = case_range['start']
        case_end = case_range['end']

        # Calculate pre_black_segment
        if i == 0:
            # First case: pre is from 0 to case start
            pre_black = case_start
        else:
            # Middle/last case: pre is half of gap from previous case
            prev_case_end = sorted_ranges[i - 1]['end']
            gap = case_start - prev_case_end
            pre_black = gap / 2.0

        # Calculate post_black_segment
        if i == len(sorted_ranges) - 1:
            # Last case: post is from case end to min(video end, case_end + 1 hour)
            max_end = min(video_duration, case_end + 3600)
            post_black = max_end - case_end
        else:
            # Not last case: post is half of gap to next case
            next_case_start = sorted_ranges[i + 1]['start']
            gap = next_case_start - case_end
            post_black = gap / 2.0

        # Convert seconds to minutes
        result[case_no] = {
            'pre': pre_black / 60.0,
            'post': post_black / 60.0
        }

    return result


def update_mp4_status_black_segments(video_path, case_ranges, video_duration, db_path=None):
    """
    Update mp4_status table with pre and post black segment data.

    Args:
        video_path: Full path to the video file
        case_ranges: List of dicts with {'case': int, 'start': float, 'end': float}
        video_duration: Total video duration in seconds
        db_path: Path to database (uses default if None)

    Returns:
        Number of database rows updated
    """
    if db_path is None:
        db_path = get_db_path()

    # Parse video path to get metadata
    recording_date, _, camera_name = parse_video_path(video_path)

    if not recording_date or not camera_name:
        print(f"[WARNING] Could not parse video path for database update: {video_path}")
        return 0

    # Calculate black segments for each case
    black_segments = calculate_black_segments(case_ranges, video_duration)

    if not black_segments:
        print(f"[WARNING] No black segments to update for {video_path}")
        return 0

    # Update database
    updated_count = 0

    try:
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()

        for case_no, segments in black_segments.items():
            pre_black = segments['pre']
            post_black = segments['post']

            # Update the database
            cur.execute('''
                UPDATE mp4_status
                SET pre_black_segment = ?,
                    post_black_segment = ?
                WHERE recording_date = ?
                  AND case_no = ?
                  AND camera_name = ?
            ''', (pre_black, post_black, recording_date, case_no, camera_name))

            if cur.rowcount > 0:
                updated_count += cur.rowcount
                print(f"  [DB UPDATE] {recording_date} Case{case_no} {camera_name}: "
                      f"pre={pre_black:.1f}min, post={post_black:.1f}min")
            else:
                # Row doesn't exist, try to insert it
                print(f"  [DB WARNING] Row not found for {recording_date} Case{case_no} {camera_name}, "
                      f"attempting insert...")
                try:
                    cur.execute('''
                        INSERT INTO mp4_status
                        (recording_date, case_no, camera_name, pre_black_segment, post_black_segment)
                        VALUES (?, ?, ?, ?, ?)
                    ''', (recording_date, case_no, camera_name, pre_black, post_black))
                    updated_count += 1
                    print(f"  [DB INSERT] Created new row for {recording_date} Case{case_no} {camera_name}")
                except sqlite3.IntegrityError:
                    print(f"  [DB ERROR] Could not insert row for {recording_date} Case{case_no} {camera_name}")

        conn.commit()

    except Exception as e:
        print(f"[ERROR] Database update failed: {e}")
    finally:
        if 'conn' in locals():
            conn.close()

    return updated_count


# ============================================================================


def load_tracking_data(tracking_file):
    """Load the tracking data from JSON file."""
    if not tracking_file or not os.path.exists(tracking_file):
        return {'processed_files': {}}

    try:
        with open(tracking_file, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        print(f"Warning: Could not load tracking file: {e}")
        return {'processed_files': {}}


def save_tracking_data(tracking_file, data):
    """Save the tracking data to JSON file."""
    if not tracking_file:
        return

    try:
        # Create directory if it doesn't exist
        os.makedirs(os.path.dirname(tracking_file), exist_ok=True)

        with open(tracking_file, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        print(f"Warning: Could not save tracking file: {e}")


def update_tracking(tracking_file, input_path, output_path, status="SUCCESS"):
    """Update tracking file with newly processed video."""
    tracking_data = load_tracking_data(tracking_file)

    # Normalize path for consistent tracking
    normalized_path = os.path.abspath(input_path)

    tracking_data['processed_files'][normalized_path] = {
        'output_path': output_path,
        'status': status,
        'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    }

    save_tracking_data(tracking_file, tracking_data)


def main():
    # Get xlsx path from config or command line or prompt
    xlsx_path = None
    output_dir = None
    num_workers = CONFIG['NUM_WORKERS']

    # Check command line arguments first
    if len(sys.argv) >= 2:
        xlsx_path = sys.argv[1]
        output_dir = sys.argv[2] if len(sys.argv) > 2 else CONFIG['OUTPUT_DIR']
        num_workers = int(sys.argv[3]) if len(sys.argv) > 3 else num_workers
    # Then check config
    elif CONFIG['XLSX_PATH']:
        xlsx_path = CONFIG['XLSX_PATH']
        output_dir = CONFIG['OUTPUT_DIR']
    # Finally, prompt user
    else:
        print("="*60)
        print("BATCH VIDEO REDACTION")
        print("="*60)
        print()
        xlsx_path = input("Enter path to Excel file (.xlsx): ").strip()

        if not xlsx_path:
            print("ERROR: No file path provided")
            sys.exit(1)

        output_input = input("Enter output directory (leave empty for same as input): ").strip()
        output_dir = output_input if output_input else CONFIG['OUTPUT_DIR']

        workers_input = input(f"Number of parallel workers (default: {num_workers}): ").strip()
        if workers_input:
            try:
                num_workers = int(workers_input)
            except ValueError:
                print(f"Invalid number, using default: {num_workers}")

    # Ensure output_dir is None if empty string
    if not output_dir:
        output_dir = None

    # Load and process xlsx
    print("="*60)
    print("BATCH VIDEO REDACTION")
    print("="*60)
    print(f"XLSX file: {xlsx_path}")
    print(f"Output dir: {output_dir or 'Same as input'}")
    print(f"Workers: {num_workers}")
    print("="*60)
    print()

    df = handle_xlsx(xlsx_path)
    print(df)
    print("="*60)

    # Load tracking data
    tracking_file = CONFIG['TRACKING_FILE']
    tracking_data = load_tracking_data(tracking_file)
    processed_files = tracking_data.get('processed_files', {})

    # Check which files are already processed
    already_processed = []
    unprocessed_files = []

    for idx, row in df.iterrows():
        normalized_path = os.path.abspath(row['path'])
        if normalized_path in processed_files:
            already_processed.append((idx, row))
        else:
            unprocessed_files.append((idx, row))

    # Display tracking status
    print()
    print("="*60)
    print("TRACKING STATUS")
    print("="*60)
    print(f"Tracking file: {tracking_file}")
    print(f"Total files in Excel: {len(df)}")
    print(f"Already processed: {len(already_processed)}")
    print(f"Unprocessed: {len(unprocessed_files)}")
    print("="*60)

    # Show already processed files
    if already_processed:
        print()
        print("ALREADY PROCESSED FILES:")
        print("-" * 80)
        for idx, row in already_processed:
            proc_info = processed_files[os.path.abspath(row['path'])]
            timestamp = proc_info.get('timestamp', 'Unknown')
            print(f"  {idx+1:3d}. {row['path']}")
            print(f"       Processed: {timestamp}")
        print("-" * 80)

    # Check if we should force reprocess
    force_reprocess = False
    if already_processed and unprocessed_files:
        print()
        choice = input("Some files are already processed. Process (U)nprocessed only or (A)ll files? [U/A]: ").strip().upper()
        if choice == 'A':
            force_reprocess = True
            print("Will re-process all files")
        else:
            print("Will process unprocessed files only")
    elif already_processed and not unprocessed_files:
        print()
        print("All files have been processed!")
        choice = input("Do you want to re-process all files? [y/N]: ").strip().lower()
        if choice == 'y':
            force_reprocess = True
            print("Will re-process all files")
        else:
            print("Nothing to do. Exiting.")
            return

    # Filter dataframe based on tracking
    if force_reprocess:
        df_to_process = df
    else:
        if unprocessed_files:
            unprocessed_indices = [idx for idx, _ in unprocessed_files]
            df_to_process = df.loc[unprocessed_indices]
        else:
            print("No files to process!")
            return

    # Display files and let user choose how many to process
    if len(df_to_process) == 0:
        print("No files to process!")
        return

    print("\nFILES TO PROCESS:")
    print("-" * 80)
    file_list = []
    for i, (idx, row) in enumerate(df_to_process.iterrows(), 1):
        print(f"  {i:3d}. {row['path']}")
        file_list.append((i, idx, row))

    print("-" * 80)
    print(f"\nTotal: {len(df_to_process)} files")
    print("\nHow many files do you want to process?")
    print("  1. Process ALL files")
    print("  2. Process first N files")
    print("  3. Choose specific files by number")

    selection_mode = input("\nChoice (1, 2, or 3): ").strip()

    selected_df = None

    if selection_mode == '1':
        # Process all
        selected_df = df_to_process
        print(f"Selected all {len(df_to_process)} files")

    elif selection_mode == '2':
        # Process first N
        n_input = input("How many files (from the top)? ").strip()
        try:
            n = int(n_input)
            selected_df = df_to_process.head(n)
            print(f"Selected first {len(selected_df)} files")
        except ValueError:
            print("ERROR: Invalid number")
            return

    elif selection_mode == '3':
        # Choose specific files
        print("\nEnter file numbers (comma-separated or ranges)")
        print("  Examples:")
        print("    1,3,5         - Files 1, 3, and 5")
        print("    1-10          - Files 1 through 10")
        print("    1-5,8,10-15   - Files 1-5, 8, and 10-15")

        numbers_input = input("\nFile numbers: ").strip()

        try:
            # Parse input
            selected_indices = set()
            parts = numbers_input.split(',')

            for part in parts:
                part = part.strip()
                if '-' in part:
                    # Range
                    start, end = part.split('-')
                    start_idx = int(start.strip())
                    end_idx = int(end.strip())
                    selected_indices.update(range(start_idx, end_idx + 1))
                else:
                    # Single number
                    selected_indices.add(int(part))

            # Convert to dataframe rows using the file_list mapping
            selected_rows = []
            for idx in sorted(selected_indices):
                # Find the corresponding row from file_list
                for display_num, df_idx, row in file_list:
                    if display_num == idx:
                        selected_rows.append(row)
                        break

            selected_df = pd.DataFrame(selected_rows)
            print(f"Selected {len(selected_df)} files")

        except Exception as e:
            print(f"ERROR: Invalid input: {e}")
            return
    else:
        print("ERROR: Invalid choice")
        return

    if selected_df is None or len(selected_df) == 0:
        print("No files selected!")
        return

    # Confirm
    response = input(f"\nProcess {len(selected_df)} files with GPU-accelerated redaction ({num_workers} workers)? (y/n): ").strip().lower()
    if response != 'y':
        print("Cancelled.")
        return

    print()
    print("="*60)
    print("STARTING VIDEO REDACTION")
    print("="*60)
    print()

    # Start timing
    start_time = time.time()
    start_datetime = datetime.now()

    # Create callback function for database updates after each video
    db_updated_count = 0

    def on_video_complete_callback(video_path, case_ranges, duration, report_data):
        """Callback to update database immediately after each video completes."""
        nonlocal db_updated_count
        try:
            updated = update_mp4_status_black_segments(
                video_path,
                case_ranges,
                duration
            )
            db_updated_count += updated
        except Exception as e:
            print(f"  [DB ERROR] Failed to update {video_path}: {e}")

    # Process videos with case-based redaction
    # Database will be updated via callback as each video completes
    output_files, success_count, failed_count, file_statuses, processing_report = redact_videos_from_df(
        selected_df,
        output_dir=output_dir,
        num_workers=num_workers,
        on_video_complete=on_video_complete_callback
    )

    # End timing
    end_time = time.time()
    end_datetime = datetime.now()
    elapsed_seconds = end_time - start_time
    elapsed_timedelta = timedelta(seconds=int(elapsed_seconds))

    # Print database update summary
    print()
    if db_updated_count > 0:
        print(f"[DB] Updated {db_updated_count} case entries in database")
    else:
        print("[DB] No database updates performed")

    # Update tracking for successfully processed files
    print("\nUpdating tracking file...")
    for idx, row in selected_df.iterrows():
        status = file_statuses.get(idx, "UNKNOWN")
        if status == "SUCCESS":
            # Find the output file from processing_report
            output_file = None
            for report in processing_report:
                if report['file'] == os.path.basename(row['path']):
                    output_file = report.get('output_file', 'Unknown')
                    break

            update_tracking(tracking_file, row['path'], output_file or 'Unknown', status="SUCCESS")

    print(f"Tracking updated: {success_count} files marked as processed")

    # Build summary text
    summary_lines = []
    summary_lines.append("="*80)
    summary_lines.append("BATCH REDACTION SUMMARY")
    summary_lines.append("="*80)
    summary_lines.append(f"Generated:         {end_datetime.strftime('%Y-%m-%d %H:%M:%S')}")
    summary_lines.append(f"Input XLSX:        {xlsx_path}")
    summary_lines.append("")
    summary_lines.append("TIMING:")
    summary_lines.append(f"  Start Time:      {start_datetime.strftime('%Y-%m-%d %H:%M:%S')}")
    summary_lines.append(f"  End Time:        {end_datetime.strftime('%Y-%m-%d %H:%M:%S')}")
    summary_lines.append(f"  Total Duration:  {elapsed_timedelta} ({elapsed_seconds:.1f} seconds)")
    summary_lines.append("")
    summary_lines.append("RESULTS:")
    summary_lines.append(f"  Total Files:     {len(selected_df)}")
    summary_lines.append(f"  Succeeded:       {success_count}")
    summary_lines.append(f"  Failed:          {failed_count}")
    summary_lines.append(f"  Success Rate:    {(success_count/len(selected_df)*100):.1f}%")
    summary_lines.append("")
    summary_lines.append("PERFORMANCE:")
    summary_lines.append(f"  Workers Used:    {num_workers}")
    summary_lines.append(f"  Avg Time/Video:  {elapsed_seconds/len(selected_df):.1f}s")
    if success_count > 0:
        summary_lines.append(f"  Speedup (est):   ~{num_workers * 0.85:.1f}x")
    summary_lines.append("")
    summary_lines.append(f"OUTPUT:")
    summary_lines.append(f"  Directory:       {output_dir or 'Same as input files'}")
    summary_lines.append("")
    summary_lines.append("DATABASE:")
    summary_lines.append(f"  Database file:   {get_db_path()}")
    summary_lines.append(f"  Cases updated:   {db_updated_count}")
    summary_lines.append(f"  Table:           mp4_status (pre_black_segment, post_black_segment)")
    summary_lines.append("")
    summary_lines.append("TRACKING:")
    summary_lines.append(f"  Tracking file:   {tracking_file}")
    summary_lines.append(f"  Previously done: {len(already_processed)}")
    summary_lines.append(f"  Newly processed: {success_count}")
    summary_lines.append(f"  Total tracked:   {len(already_processed) + success_count}")
    summary_lines.append("="*80)
    summary_lines.append("")
    summary_lines.append("PROCESSED FILES:")
    summary_lines.append("-"*80)

    # Add successful files first
    if success_count > 0:
        summary_lines.append("")
        summary_lines.append(f"SUCCESSFUL ({success_count}):")
        for idx, row in selected_df.iterrows():
            status = file_statuses.get(idx, "UNKNOWN")
            if status == "SUCCESS":
                summary_lines.append(f"  [SUCCESS] {row['path']}")

    # Add failed files
    if failed_count > 0:
        summary_lines.append("")
        summary_lines.append(f"FAILED ({failed_count}):")
        for idx, row in selected_df.iterrows():
            status = file_statuses.get(idx, "UNKNOWN")
            if status != "SUCCESS":
                summary_lines.append(f"  [{status}]")
                summary_lines.append(f"     File: {row['path']}")

    summary_lines.append("")
    summary_lines.append("-"*80)

    # Add detailed redaction report
    if processing_report:
        summary_lines.append("")
        summary_lines.append("="*80)
        summary_lines.append("REDACTION REPORT")
        summary_lines.append("="*80)

        for i, report in enumerate(processing_report, 1):
            summary_lines.append("")
            summary_lines.append(f"Video {i}: {report['file']}")
            summary_lines.append(f"  Total Duration: {report['duration']:.1f}s ({report['duration']/60:.1f} min)")
            summary_lines.append("")
            summary_lines.append(f"  CASE TIMES (Corner box visible):")

            if report['case_ranges']:
                total_case_time = 0
                for cr in report['case_ranges']:
                    case_duration = cr['end'] - cr['start']
                    total_case_time += case_duration
                    summary_lines.append(f"    Case {cr['case']}: {cr['start']:.1f}s -> {cr['end']:.1f}s (duration: {case_duration:.1f}s)")
                summary_lines.append(f"    Total case time: {total_case_time:.1f}s ({total_case_time/60:.1f} min)")
            else:
                summary_lines.append(f"    None")

            summary_lines.append("")
            summary_lines.append(f"  FULL BLACK TIMES:")
            if report['black_periods']:
                total_black_time = 0
                for j, bp in enumerate(report['black_periods'], 1):
                    black_duration = bp['end'] - bp['start']
                    total_black_time += black_duration
                    summary_lines.append(f"    Period {j}: {bp['start']:.1f}s -> {bp['end']:.1f}s (duration: {black_duration:.1f}s)")
                summary_lines.append(f"    Total black time: {total_black_time:.1f}s ({total_black_time/60:.1f} min)")
            else:
                summary_lines.append(f"    None")

            summary_lines.append("")
            summary_lines.append(f"  SUMMARY:")
            case_pct = (sum(cr['end'] - cr['start'] for cr in report['case_ranges']) / report['duration']) * 100 if report['case_ranges'] else 0
            black_pct = (sum(bp['end'] - bp['start'] for bp in report['black_periods']) / report['duration']) * 100 if report['black_periods'] else 0
            summary_lines.append(f"    Case time: {case_pct:.1f}% of video")
            summary_lines.append(f"    Black time: {black_pct:.1f}% of video")

        summary_lines.append("")
        summary_lines.append("="*80)

    # Print summary to console
    print("\n")
    for line in summary_lines:
        print(line)

    # Export summary to file
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)
        summary_filename = f"redaction_summary_{start_datetime.strftime('%Y%m%d_%H%M%S')}.txt"
        summary_path = os.path.join(output_dir, summary_filename)
    else:
        # Use current directory if no output dir specified
        summary_filename = f"redaction_summary_{start_datetime.strftime('%Y%m%d_%H%M%S')}.txt"
        summary_path = summary_filename

    try:
        with open(summary_path, 'w', encoding='utf-8') as f:
            f.write('\n'.join(summary_lines))
        print(f"\nSummary exported to: {summary_path}")
    except Exception as e:
        print(f"\nWarning: Could not export summary to file: {e}")

    # Final tracking information
    print()
    print("="*60)
    print("TRACKING INFO")
    print("="*60)
    print(f"Tracking file: {tracking_file}")
    print(f"Total tracked files: {len(already_processed) + success_count}")
    print()
    print("Next run will automatically skip already processed files.")
    print("To re-process files, choose 'All files' when prompted.")
    print("="*60)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\nWARNING: Redaction interrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n\nERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
