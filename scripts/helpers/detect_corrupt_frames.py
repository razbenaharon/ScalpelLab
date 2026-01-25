"""
Detect Corrupt Frames in Video
Uses FFmpeg to scan video files for decoding errors which indicate damaged frames.
Can process a single file or a batch of files.
"""

import subprocess
import sys
import os
import argparse
import time
from pathlib import Path
from datetime import datetime

# FFmpeg possible locations (copied from scripts/3_seq_to_mp4_convert.py)
FFMPEG_PATHS = [
    r"C:\\Program Files\\ffmpeg\\bin\\ffmpeg.exe",
    r"C:\\ffmpeg\\bin\\ffmpeg.exe",
    r"C:\\Program Files (x86)\\ffmpeg\\bin\\ffmpeg.exe",
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

def scan_video(video_path, output_log=None):
    """
    Scans a video file for errors using FFmpeg.
    
    Args:
        video_path (Path): Path to the video file.
        output_log (Path, optional): Path to save the detailed error log. 
                                     If None, a default name is generated.
                                     
    Returns:
        dict: Summary of results {'total_errors': int, 'log_file': Path, 'duration': float}
    """
    ffmpeg_path = find_ffmpeg()
    if not ffmpeg_path:
        print("Error: FFmpeg not found.")
        sys.exit(1)

    video_path = Path(video_path)
    if not video_path.exists():
        print(f"Error: File {video_path} not found.")
        return None

    if output_log is None:
        # Create a default log file name based on video name
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_log = video_path.parent / f"{video_path.stem}_integrity_log_{timestamp}.txt"
    else:
        output_log = Path(output_log)

    print(f"Scanning: {video_path.name}")
    print(f"Log file: {output_log.name}")
    print("This process may take some time depending on video length...")

    start_time = time.time()
    
    # Run FFmpeg without "-v error" so we get progress updates (and timestamps)
    cmd = [
        ffmpeg_path,
        "-i", str(video_path),
        "-f", "null",
        "-"
    ]

    import re
    # Regex to find "time=XX:XX:XX.XX" in the progress line
    time_pattern = re.compile(r"time=(\d{2}:\d{2}:\d{2}\.\d{2})")
    
    # Common prefixes for FFmpeg header info to ignore
    ignore_prefixes = (
        "ffmpeg version", "  built with", "  configuration:", "  libav", 
        "  libsw", "  libpost", "Input #", "  Metadata:", "  Duration:", 
        "Stream mapping:", "Press [q] to stop", "Output #", "  Stream #"
    )

    error_count = 0
    last_time = "00:00:00.00"

    try:
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            universal_newlines=True,
            encoding='utf-8',
            errors='replace'
        )

        with open(output_log, "w") as log_file:
            log_file.write(f"Integrity Scan for: {video_path.name}\n")
            log_file.write(f"Scan Date: {datetime.now()}\n")
            log_file.write("-" * 50 + "\n")

            while True:
                line = process.stderr.readline()
                if not line and process.poll() is not None:
                    break
                
                if not line:
                    continue

                line_stripped = line.strip()
                
                # Check for time update (Progress Line)
                time_match = time_pattern.search(line)
                if time_match:
                    last_time = time_match.group(1)
                    # Show progress on console (overwrite line)
                    sys.stdout.write(f"\rScanning... Time: {last_time}")
                    sys.stdout.flush()
                    continue

                # Filter out empty lines and standard headers
                if not line_stripped or line_stripped.startswith(ignore_prefixes):
                    continue
                
                # If we are here, it's likely an error or warning
                # (Progress lines usually match the time_pattern and are skipped above)
                if not line.startswith("frame="): 
                    error_count += 1
                    log_entry = f"[{last_time}] {line_stripped}\n"
                    log_file.write(log_entry)

        print() # Newline after progress
        duration = time.time() - start_time
        
        return {
            'file': video_path.name,
            'total_errors': error_count,
            'log_file': output_log,
            'duration': duration,
            'success': True # Always successful if we finished scanning
        }

    except Exception as e:
        print(f"An error occurred: {e}")
        return None

def main():
    parser = argparse.ArgumentParser(description="Check video files for damaged frames using FFmpeg.")
    parser.add_argument("path", nargs="?", help="Path to a video file or directory of videos")
    parser.add_argument("--ext", default=".mp4", help="Extension to look for if scanning directory (default: .mp4)")
    parser.add_argument("--keep-empty", action="store_true", help="Keep empty log files (clean videos)")
    
    args = parser.parse_args()
    
    path_str = args.path
    if not path_str:
        print("Please enter the path to the video file or directory:")
        path_str = input("> ").strip().strip('"').strip("'")
    
    if not path_str:
        print("Error: No path provided.")
        sys.exit(1)

    target_path = Path(path_str)
    
    if not target_path.exists():
        print(f"Error: Path {target_path} does not exist.")
        sys.exit(1)

    files_to_scan = []
    if target_path.is_file():
        files_to_scan.append(target_path)
    else:
        files_to_scan = list(target_path.glob(f"*{args.ext}"))
        print(f"Found {len(files_to_scan)} files in directory.")

    if not files_to_scan:
        print("No files found to scan.")
        sys.exit(0)

    results = []
    print("=" * 60)
    print(f"Starting integrity scan on {len(files_to_scan)} files")
    print("=" * 60)

    for i, video_file in enumerate(files_to_scan, 1):
        print(f"\n[{i}/{len(files_to_scan)}] Processing {video_file.name}")
        result = scan_video(video_file)
        if result:
            results.append(result)
            if result['total_errors'] == 0:
                print(f"✅ CLEAN. No errors found ({result['duration']:.1f}s)")
                if not args.keep_empty and result['log_file'].exists():
                    try:
                        os.remove(result['log_file'])
                    except:
                        pass
            else:
                print(f"❌ ISSUES FOUND. {result['total_errors']} errors detected.")
                print(f"   Details saved to: {result['log_file']}")

    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    
    issues_found = 0
    for res in results:
        status = "CLEAN" if res['total_errors'] == 0 else f"ERRORS ({res['total_errors']})"
        print(f"{res['file']:<40} : {status}")
        if res['total_errors'] > 0:
            issues_found += 1

    print("-" * 60)
    if issues_found == 0:
        print("All files appear to be healthy!")
    else:
        print(f"Found issues in {issues_found} files.")

if __name__ == "__main__":
    main()
