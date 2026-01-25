#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Count Frames Script

Counts the number of frames in a video file using ffmpeg.

Usage:
    python scripts/helpers/count_frames.py <input_video>

Examples:
    python scripts/helpers/count_frames.py video.mp4
"""

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

# FFmpeg possible locations (copied from cut_video.py for consistency)
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


def count_frames(input_path):
    """
    Count frames in a video using ffmpeg.
    
    Args:
        input_path: Path to the video file.
        
    Returns:
        int: Number of frames, or -1 if failed.
    """
    ffmpeg_path = find_ffmpeg()
    if not ffmpeg_path:
        print("Error: ffmpeg not found.")
        return -1

    # Command to read the video stream and discard output, forcing a count
    # -i: Input file
    # -map 0:v:0: Select first video stream
    # -c copy: Copy stream (fast, counts packets/frames without decoding)
    # -f null: Output format null
    # -: Output to stdout (discarded)
    cmd = [
        ffmpeg_path,
        "-i", str(input_path),
        "-map", "0:v:0",
        "-c", "copy",
        "-f", "null",
        "-"
    ]
    
    try:
        # Run ffmpeg, capturing stderr where the progress is printed
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace'
        )
        
        # Parse stderr for "frame= 1234"
        # Example output: frame= 1000 fps=0.0 q=-1.0 Lsize=N/A time=00:00:33.33 bitrate=N/A speed=  66x
        # We look for the last occurrence of 'frame='
        content = result.stderr
        
        # Regex to find frame count
        # Matches "frame=" followed by whitespace and digits
        matches = re.findall(r"frame=\s*(\d+)", content)
        
        if matches:
            # The last match is the final count
            total_frames = int(matches[-1])
            return total_frames
        else:
            print(f"Error: Could not parse frame count from ffmpeg output.")
            # Debug: print last few lines of stderr
            print("FFmpeg stderr tail:")
            print("\n".join(content.splitlines()[-5:]))
            return -1

    except Exception as e:
        print(f"Error running ffmpeg: {e}")
        return -1


def main():
    parser = argparse.ArgumentParser(description="Count frames in a video file using ffmpeg.")
    parser.add_argument("input_path", nargs="?", help="Path to the input video file (optional, will prompt if missing)")
    
    args = parser.parse_args()
    
    input_path_str = args.input_path
    
    # If no path provided, ask the user
    if not input_path_str:
        print("--- Video Frame Counter ---")
        input_path_str = input("Please enter the path to the video file: ").strip().strip('"')
        if not input_path_str:
            print("No path provided. Exiting.")
            sys.exit(1)
            
    input_path = Path(input_path_str)
    if not input_path.exists():
        print(f"Error: File not found: {input_path}")
        sys.exit(1)
        
    print(f"Counting frames for: {input_path.name}...")
    frames = count_frames(input_path)
    
    if frames != -1:
        print(f"Total frames: {frames}")
    else:
        sys.exit(1)


if __name__ == "__main__":
    main()
