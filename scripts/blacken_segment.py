#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Blacken Video Segment Script - Make a specific time segment black

Makes a specific segment of a video completely black using ffmpeg's color filter.

Usage:
    python scripts/blacken_segment.py <input_video> <start_time> <end_time> [--output_name <name>]

Time format: HH:MM:SS or seconds (e.g., "00:01:30" or "90")

Examples:
    python scripts/blacken_segment.py video.mp4 00:00:10 00:00:30
    python scripts/blacken_segment.py video.mp4 10 30
    python scripts/blacken_segment.py video.mp4 00:01:00 00:02:00 --output_name "censored"
"""

import argparse
import os
import subprocess
import sys
from pathlib import Path

# Add parent directory to path to import config
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# FFmpeg possible locations
FFMPEG_PATHS = [
    r"C:\Program Files\ffmpeg\bin\ffmpeg.exe",
    r"C:\ffmpeg\bin\ffmpeg.exe",
    r"C:\Program Files (x86)\ffmpeg\bin\ffmpeg.exe",
    "ffmpeg",  # Try from PATH
]


def find_ffmpeg():
    """Find ffmpeg executable in common locations or PATH"""
    for path in FFMPEG_PATHS:
        if path == "ffmpeg":
            # Try from PATH
            try:
                result = subprocess.run(
                    ["where" if os.name == 'nt' else "which", "ffmpeg"],
                    capture_output=True,
                    text=True,
                    timeout=5
                )
                if result.returncode == 0:
                    ffmpeg_path = result.stdout.strip().split('\n')[0]
                    if os.path.exists(ffmpeg_path):
                        return ffmpeg_path
            except Exception:
                pass
        else:
            if os.path.exists(path):
                return path
    return None


def parse_time(time_str):
    """
    Parse time string to seconds.

    Args:
        time_str: Time as "HH:MM:SS", "MM:SS", or seconds as string

    Returns:
        Float seconds value
    """
    # If it contains ':', parse HH:MM:SS format
    if ':' in time_str:
        parts = time_str.split(':')
        if len(parts) == 3:
            hours, minutes, seconds = parts
            return float(hours) * 3600 + float(minutes) * 60 + float(seconds)
        elif len(parts) == 2:
            minutes, seconds = parts
            return float(minutes) * 60 + float(seconds)

    # Otherwise, treat as seconds
    return float(time_str)


def blacken_segment(input_path, start_time, end_time, output_name=None):
    """
    Make a specific segment of video completely black.

    Args:
        input_path: Path to input video file
        start_time: Start time (HH:MM:SS or seconds)
        end_time: End time (HH:MM:SS or seconds)
        output_name: Optional custom output name (without extension)

    Returns:
        (success, output_path, message)
    """
    # Check if input file exists
    input_path = Path(input_path)
    if not input_path.exists():
        return False, None, f"Input file not found: {input_path}"

    # Find FFmpeg
    ffmpeg_path = find_ffmpeg()
    if not ffmpeg_path:
        return False, None, "ffmpeg.exe not found"

    # Parse times to seconds
    try:
        start_sec = parse_time(start_time)
        end_sec = parse_time(end_time)
    except ValueError as e:
        return False, None, f"Invalid time format: {str(e)}"

    if start_sec >= end_sec:
        return False, None, "Start time must be before end time"

    # Generate output path
    if output_name:
        output_filename = f"{output_name}{input_path.suffix}"
    else:
        # Use original name with "_blackened" suffix
        stem = input_path.stem
        output_filename = f"{stem}_blackened{input_path.suffix}"

    output_path = input_path.parent / output_filename

    # If output file exists, add counter
    if output_path.exists():
        counter = 1
        while counter < 1000:
            if output_name:
                output_filename = f"{output_name}_{counter}{input_path.suffix}"
            else:
                output_filename = f"{stem}_blackened_{counter}{input_path.suffix}"
            output_path = input_path.parent / output_filename
            if not output_path.exists():
                break
            counter += 1

    # Build FFmpeg command
    # Use drawbox filter to draw a black rectangle covering the entire frame
    # The enable parameter specifies when to apply the filter
    filter_str = f"drawbox=x=0:y=0:w=iw:h=ih:color=black:t=fill:enable='between(t,{start_sec},{end_sec})'"

    cmd = [
        ffmpeg_path,
        "-y",  # Overwrite output file
        "-i", str(input_path),  # Input file
        "-vf", filter_str,  # Video filter
        "-c:a", "copy",  # Copy audio without re-encoding
        str(output_path)  # Output file
    ]

    print(f"Blackening video segment...")
    print(f"  Input:  {input_path.name}")
    print(f"  Start:  {start_time} ({start_sec:.2f}s)")
    print(f"  End:    {end_time} ({end_sec:.2f}s)")
    print(f"  Output: {output_path.name}")
    print()

    try:
        # Run FFmpeg
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True
        )

        # Check if successful
        if result.returncode == 0 and output_path.exists():
            size_mb = output_path.stat().st_size / (1024 * 1024)
            return True, output_path, f"Success! Output size: {size_mb:.1f} MB"
        else:
            error_msg = result.stderr if result.stderr else f"Failed with code {result.returncode}"
            return False, None, error_msg

    except Exception as e:
        return False, None, f"Error: {str(e)}"


def main():
    """Main function."""
    parser = argparse.ArgumentParser(
        description="Make a specific segment of video completely black",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  Blacken segment from 10 to 30 seconds:
    python scripts/blacken_segment.py video.mp4 10 30

  Blacken segment using HH:MM:SS format:
    python scripts/blacken_segment.py video.mp4 00:01:00 00:02:00

  Blacken segment with custom output name:
    python scripts/blacken_segment.py video.mp4 10 30 --output_name censored
        """
    )

    parser.add_argument(
        "input",
        help="Path to input video file"
    )
    parser.add_argument(
        "start",
        help="Start time (HH:MM:SS or seconds)"
    )
    parser.add_argument(
        "end",
        help="End time (HH:MM:SS or seconds)"
    )
    parser.add_argument(
        "--output_name",
        help="Custom output filename (without extension)"
    )

    args = parser.parse_args()

    print("=" * 80)
    print("BLACKEN VIDEO SEGMENT")
    print("=" * 80)
    print()

    success, output_path, message = blacken_segment(
        args.input,
        args.start,
        args.end,
        args.output_name
    )

    if success:
        print(f"Success: {message}")
        print(f"Output: {output_path}")
        return 0
    else:
        print(f"Failed: {message}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
