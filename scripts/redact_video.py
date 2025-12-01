#!/usr/bin/env python3
"""
FINAL Optimized Video Redaction Script

Uses NVIDIA NVENC GPU acceleration with correct bitrate control.
Single-pass method for reliability.
"""

import subprocess
import json
import os
import sys
import threading
import time
import pandas as pd
import numpy as np
from concurrent.futures import ProcessPoolExecutor, as_completed


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


def redact_video(input_file: str, redact_start: str, redact_end: str, output_file: str = None) -> str:
    """
    Redact video using GPU-accelerated encoding.

    Args:
        input_file: Input video path
        redact_start: Start time (HH:MM:SS, MM:SS, or seconds)
        redact_end: End time (HH:MM:SS, MM:SS, or seconds)
        output_file: Output path (optional)

    Returns:
        Path to output file
    """
    if not os.path.exists(input_file):
        raise FileNotFoundError(f"Input not found: {input_file}")

    if output_file is None:
        base_dir = os.path.dirname(input_file) or '.'
        base_name = os.path.splitext(os.path.basename(input_file))[0]
        output_file = os.path.join(base_dir, f"{base_name}_redacted.mp4")

    print(f"\n{'='*60}")
    print(f"Video Redaction with GPU Acceleration")
    print(f"{'='*60}")
    print(f"Input:  {input_file}")
    print(f"Redact: {redact_start} -> {redact_end}")
    print(f"Output: {output_file}")
    print(f"{'='*60}\n")

    # Probe
    print("Analyzing video...")
    video_info = probe_video(input_file)
    duration = video_info['duration']
    bitrate = video_info['bitrate']

    print(f"  Duration: {duration:.1f}s ({duration/60:.1f} min)")
    print(f"  Bitrate: {bitrate//1000} kbps\n")

    # Parse times
    start_sec = time_to_seconds(redact_start)
    end_sec = time_to_seconds(redact_end)

    if start_sec >= end_sec:
        raise ValueError("Start must be < end")
    if end_sec > duration:
        raise ValueError(f"End ({end_sec}s) > duration ({duration:.1f}s)")

    print(f"Processing (black overlay: {start_sec:.1f}s -> {end_sec:.1f}s)...\n")
    print("Permanent blackout: Bottom-right corner (1/3 width x 1/2 height)\n")

    # FFmpeg command with NVENC
    # Uses drawbox filters:
    # 1. Permanent black box in bottom-right corner (1/3 width x 1/2 height, from middle to bottom)
    # 2. Time-based black overlay for redaction period (full screen)
    filter_str = (
        f"drawbox=x=2*iw/3:y=ih/2:w=iw/3:h=ih/2:color=black:t=fill,"
        f"drawbox=enable='between(t,{start_sec},{end_sec})':color=black:t=fill"
    )

    cmd = [
        'ffmpeg', '-y',
        '-hide_banner', '-loglevel', 'info', '-stats',
        '-hwaccel', 'cuda',  # CUDA-accelerated decoding
        '-i', input_file,
        '-vf', filter_str,
        '-c:v', 'h264_nvenc',
        '-preset', 'p1',  # p1=fastest, p7=best quality
        '-b:v', str(bitrate),
        '-maxrate', str(int(bitrate * 1.1)),
        '-bufsize', str(int(bitrate * 2)),
        '-c:a', 'copy',  # Don't re-encode audio
        '-movflags', '+faststart',  # Enable fast start for web playback
        output_file
    ]

    # Start size monitoring thread
    stop_event = threading.Event()
    size_monitor = threading.Thread(target=monitor_output_size, args=(output_file, stop_event))
    size_monitor.daemon = True
    size_monitor.start()

    try:
        subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError as e:
        stop_event.set()
        print("\n[WARNING] GPU encoding failed, trying CPU fallback...")
        # Fallback to CPU encoding
        cmd_cpu = [
            'ffmpeg', '-y',
            '-hide_banner', '-loglevel', 'info', '-stats',
            '-i', input_file,
            '-vf', filter_str,
            '-c:v', 'libx264',
            '-preset', 'faster',
            '-crf', '23',
            '-c:a', 'copy',
            '-movflags', '+faststart',
            output_file
        ]

        # Restart size monitoring
        stop_event.clear()
        size_monitor = threading.Thread(target=monitor_output_size, args=(output_file, stop_event))
        size_monitor.daemon = True
        size_monitor.start()

        subprocess.run(cmd_cpu, check=True)
    finally:
        stop_event.set()
        size_monitor.join(timeout=1)

    # Results
    if os.path.exists(output_file):
        orig_size = os.path.getsize(input_file) / (1024 * 1024)
        new_size = os.path.getsize(output_file) / (1024 * 1024)

        print(f"\n\n{'='*60}")
        print(f"[SUCCESS]")
        print(f"  Output: {output_file}")
        print(f"  Size: {orig_size:.1f} MB -> {new_size:.1f} MB ({new_size/orig_size:.2f}x)")
        print(f"{'='*60}\n")

        return output_file
    else:
        raise Exception("Output not created")


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
            os.makedirs(output_dir, exist_ok=True)
            base_name = os.path.splitext(os.path.basename(input_file))[0]
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


def redact_videos_from_df(df: pd.DataFrame, output_dir: str = None, num_workers: int = 6) -> list:
    """
    Process videos from dataframe with case-based redaction using parallel GPU processing.

    For each video:
    - During case time ranges: small black box in corner (rest visible)
    - Outside all case ranges: entire screen black

    Args:
        df: DataFrame with columns: 'path', 'start time - case X', 'end time - case X'
        output_dir: Directory for output files (optional)
        num_workers: Number of parallel workers (default: 6 for RTX A2000, 2-8 recommended)

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


def main():
    if len(sys.argv) < 4:
        print("Usage: python redact_video.py <input> <start> <end> [output]")
        print("\nExamples:")
        print("  python redact_video.py video.mp4 0 66")
        print("  python redact_video.py video.mp4 00:10:00 00:11:00 output.mp4")
        sys.exit(1)

    try:
        redact_video(sys.argv[1], sys.argv[2], sys.argv[3],
                    sys.argv[4] if len(sys.argv) > 4 else None)
    except Exception as e:
        print(f"\n[ERROR] {e}")
        sys.exit(1)


if __name__ == '__main__':
    main()
