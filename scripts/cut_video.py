#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Cut Video Script - Extract segment from MP4 file

Cuts a video file from start time to end time and saves it in the same directory.

Usage:
    python scripts/cut_video.py <input_mp4> <start_time> <end_time> [--output_name <name>]

Time format: HH:MM:SS or seconds (e.g., "00:01:30" or "90")

Examples:
    python scripts/cut_video.py video.mp4 00:00:10 00:00:30
    python scripts/cut_video.py video.mp4 10 30
    python scripts/cut_video.py video.mp4 00:01:00 00:02:00 --output_name "segment1"
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
    Parse time string to seconds or return as-is if already valid format.

    Args:
        time_str: Time as "HH:MM:SS", "MM:SS", or seconds as string

    Returns:
        String in format suitable for FFmpeg
    """
    # If it contains ':', assume it's already in HH:MM:SS format
    if ':' in time_str:
        return time_str

    # Otherwise, treat as seconds and convert to HH:MM:SS
    try:
        seconds = float(time_str)
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = seconds % 60
        return f"{hours:02d}:{minutes:02d}:{secs:06.3f}"
    except ValueError:
        # If conversion fails, return as-is and let FFmpeg handle it
        return time_str


def cut_video(input_path, start_time, end_time, output_name=None):
    """
    Cut video from start_time to end_time.

    Args:
        input_path: Path to input MP4 file
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

    # Parse times
    start = parse_time(start_time)
    end = parse_time(end_time)

    # Generate output path
    if output_name:
        output_filename = f"{output_name}.mp4"
    else:
        # Use original name with "_cut" suffix
        stem = input_path.stem
        output_filename = f"{stem}_cut.mp4"

    output_path = input_path.parent / output_filename

    # If output file exists, add counter
    if output_path.exists():
        counter = 1
        while counter < 1000:
            if output_name:
                output_filename = f"{output_name}_{counter}.mp4"
            else:
                output_filename = f"{stem}_cut_{counter}.mp4"
            output_path = input_path.parent / output_filename
            if not output_path.exists():
                break
            counter += 1

    # Build FFmpeg command
    # Using -ss before -i for faster seeking, and -to for end time
    cmd = [
        ffmpeg_path,
        "-y",  # Overwrite output file
        "-ss", start,  # Start time
        "-to", end,  # End time
        "-i", str(input_path),  # Input file
        "-c", "copy",  # Copy without re-encoding (fast)
        str(output_path)  # Output file
    ]

    print(f"Cutting video...")
    print(f"  Input:  {input_path.name}")
    print(f"  Start:  {start}")
    print(f"  End:    {end}")
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
    # Check if command-line arguments provided
    if len(sys.argv) > 1:
        # Command-line mode
        parser = argparse.ArgumentParser(
            description="Cut video file(s) from start time to end time",
            formatter_class=argparse.RawDescriptionHelpFormatter,
            epilog="""
Examples:
  Cut single video from 10 seconds to 30 seconds:
    python scripts/cut_video.py video.mp4 10 30

  Cut multiple videos with same time range:
    python scripts/cut_video.py video1.mp4 video2.mp4 video3.mp4 10 30

  Cut video using HH:MM:SS format:
    python scripts/cut_video.py video.mp4 00:01:00 00:02:00
            """
        )

        parser.add_argument(
            "input",
            nargs="+",
            help="Path(s) to input MP4 file(s)"
        )
        parser.add_argument(
            "start",
            help="Start time (HH:MM:SS or seconds)"
        )
        parser.add_argument(
            "end",
            help="End time (HH:MM:SS or seconds)"
        )

        args = parser.parse_args()

        # Separate video paths from start/end times
        # Last two arguments are start and end times
        if len(args.input) < 3:
            print("❌ Error: Need at least 1 video file, start time, and end time")
            return 1

        video_paths = args.input[:-2]
        start_time = args.input[-2]
        end_time = args.input[-1]

        print("=" * 80)
        print(f"Cutting {len(video_paths)} video(s)")
        print(f"Start time: {start_time}")
        print(f"End time: {end_time}")
        print("=" * 80)
        print()

        # Cut all videos
        success_count = 0
        failed_count = 0

        for i, video_path in enumerate(video_paths, 1):
            print(f"[{i}/{len(video_paths)}] Processing: {Path(video_path).name}")

            success, output_path, message = cut_video(
                video_path,
                start_time,
                end_time,
                None
            )

            if success:
                print(f"  ✅ {message}")
                print(f"  📁 {output_path}")
                success_count += 1
            else:
                print(f"  ❌ Failed: {message}")
                failed_count += 1
            print()

        # Summary
        print("=" * 80)
        print("SUMMARY")
        print("=" * 80)
        print(f"Success: {success_count}")
        print(f"Failed:  {failed_count}")
        print(f"Total:   {len(video_paths)}")
        print("=" * 80)

        return 0 if failed_count == 0 else 1
    else:
        # Interactive mode
        print("=" * 80)
        print("VIDEO CUTTER - Interactive Mode")
        print("=" * 80)
        print()
        print("This tool cuts video files from start time to end time.")
        print("Same time range will be applied to all videos.")
        print("Time format: HH:MM:SS or seconds (e.g., '00:01:30' or '90')")
        print()

        # Get input file paths
        input_paths = []
        print("Enter video file paths (one per line, empty line when done):")
        print()

        while True:
            path_input = input(f"Video {len(input_paths) + 1} (or press Enter to finish): ").strip().strip('"')

            # Empty input - check if we have at least one file
            if not path_input:
                if len(input_paths) == 0:
                    print("❌ You must add at least one video file")
                    continue
                else:
                    break

            # Validate path
            file_path = Path(path_input)
            if not file_path.exists():
                print(f"❌ File not found: {file_path}")
                retry = input("Try again? (y/n): ").strip().lower()
                if retry == 'y':
                    continue
                else:
                    if len(input_paths) > 0:
                        break
                    else:
                        print("Cancelled.")
                        return 1

            if not file_path.suffix.lower() in ['.mp4', '.avi', '.mov', '.mkv', '.seq']:
                print(f"⚠️  Warning: File extension is {file_path.suffix}, not a common video format")
                proceed = input("Add anyway? (y/n): ").strip().lower()
                if proceed != 'y':
                    continue

            input_paths.append(file_path)
            print(f"  ✓ Added: {file_path.name}")

        print()
        print(f"✓ Total videos selected: {len(input_paths)}")
        print()

        # Get start time
        while True:
            start_time = input("Enter start time (HH:MM:SS or seconds): ").strip()
            if not start_time:
                print("❌ Start time cannot be empty")
                continue
            break

        # Get end time
        while True:
            end_time = input("Enter end time (HH:MM:SS or seconds): ").strip()
            if not end_time:
                print("❌ End time cannot be empty")
                continue
            break

        # Confirm
        print()
        print("-" * 80)
        print(f"Ready to cut {len(input_paths)} video(s):")
        print(f"  Start time: {start_time}")
        print(f"  End time:   {end_time}")
        print()
        print("  Videos:")
        for i, path in enumerate(input_paths, 1):
            print(f"    {i}. {path.name}")
        print("-" * 80)

        response = input("\nProceed? (y/n): ").strip().lower()
        if response != 'y':
            print("Cancelled.")
            return 1

        print()
        print("=" * 80)
        print("STARTING CUT")
        print("=" * 80)
        print()

        # Cut all videos
        success_count = 0
        failed_count = 0

        for i, input_path in enumerate(input_paths, 1):
            print(f"[{i}/{len(input_paths)}] Processing: {input_path.name}")

            success, output_path, message = cut_video(
                str(input_path),
                start_time,
                end_time,
                None
            )

            if success:
                print(f"  ✅ {message}")
                print(f"  📁 {output_path}")
                success_count += 1
            else:
                print(f"  ❌ Failed: {message}")
                failed_count += 1
            print()

        # Summary
        print("=" * 80)
        print("CUT COMPLETE")
        print("=" * 80)
        print(f"Success: {success_count}")
        print(f"Failed:  {failed_count}")
        print(f"Total:   {len(input_paths)}")
        print("=" * 80)

        return 0 if failed_count == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
