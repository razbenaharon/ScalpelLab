"""
Fast Video Redaction Script - Cut-Generate-Stitch Method
Maximizes speed by:
1. Stream copying original segments (no re-encoding)
2. Generating black segment from source with drawbox filter (preserves exact timing)
3. Using concat demuxer for stitching

This approach ensures the output has the exact same duration as the input.
"""

import subprocess
import json
import os
import sys
import shutil
from pathlib import Path


def probe_video(file_path):
    """
    Use ffprobe to detect video properties: resolution, frame rate, time base, duration.
    """
    cmd = [
        'ffprobe',
        '-v', 'quiet',
        '-print_format', 'json',
        '-show_streams',
        '-select_streams', 'v:0',
        '-show_format',
        str(file_path)
    ]

    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    data = json.loads(result.stdout)

    if not data.get('streams'):
        raise ValueError("No video stream found in input file")

    stream = data['streams'][0]
    format_data = data.get('format', {})

    # Parse frame rate
    fps_str = stream.get('r_frame_rate', '30/1')
    fps_num, fps_den = map(int, fps_str.split('/'))
    fps = fps_num / fps_den

    # Get duration
    duration = float(format_data.get('duration', stream.get('duration', 0)))

    props = {
        'width': stream['width'],
        'height': stream['height'],
        'fps': fps,
        'fps_str': fps_str,
        'time_base': stream.get('time_base', '1/1000'),
        'duration': duration,
        'codec': stream.get('codec_name', 'h264'),
        'pix_fmt': stream.get('pix_fmt', 'yuv420p'),
        'profile': stream.get('profile', 'High')
    }

    return props


def time_to_seconds(time_str):
    """Convert HH:MM:SS or HH:MM:SS.mmm or plain seconds to float seconds."""
    # If already a number, return it
    if isinstance(time_str, (int, float)):
        return float(time_str)

    time_str = str(time_str).strip()

    # If it's a plain number string (no colons), treat as seconds
    if ':' not in time_str:
        return float(time_str)

    # Parse HH:MM:SS format
    parts = time_str.split(':')
    if len(parts) == 3:
        h = float(parts[0])
        m = float(parts[1])
        s = float(parts[2])
        return h * 3600 + m * 60 + s
    elif len(parts) == 2:
        m = float(parts[0])
        s = float(parts[1])
        return m * 60 + s
    else:
        return float(time_str)


def extract_segment_copy(input_file, output_file, start_time=None, duration=None):
    """
    Extract video segment using stream copy (no re-encoding).
    """
    cmd = ['ffmpeg', '-y', '-v', 'warning', '-hide_banner']

    if start_time is not None:
        cmd.extend(['-ss', str(start_time)])

    cmd.extend(['-i', str(input_file)])

    if duration is not None:
        cmd.extend(['-t', str(duration)])

    cmd.extend(['-c', 'copy', '-avoid_negative_ts', '1', str(output_file)])

    print(f"  $ {' '.join(cmd)}")
    subprocess.run(cmd, check=True)


def generate_black_clip_from_source(input_file, output_file, start_time, duration, pix_fmt='yuv420p'):
    """
    Generate black segment by filtering the source video.
    This preserves exact timing/frame rate/time base, preventing slow-motion issues.
    """
    cmd = [
        'ffmpeg', '-y', '-v', 'warning', '-hide_banner',
        '-ss', str(start_time),
        '-t', str(duration),
        '-i', str(input_file),
        '-vf', 'drawbox=x=0:y=0:w=iw:h=ih:color=black:t=fill',
        '-c:v', 'libx264',
        '-preset', 'ultrafast',  # Fast encoding
        '-crf', '18',            # High quality (black compresses well)
        '-pix_fmt', pix_fmt,
        '-an',                   # No audio
        str(output_file)
    ]

    print(f"  $ {' '.join(cmd)}")
    subprocess.run(cmd, check=True)


def stitch_segments(segment_files, output_file, temp_dir):
    """
    Stitch segments using FFmpeg concat demuxer.
    """
    concat_list = temp_dir / 'concat_list.txt'

    with open(concat_list, 'w') as f:
        for seg in segment_files:
            # Convert to absolute path and use forward slashes for cross-platform
            abs_path = str(Path(seg).absolute()).replace('\\', '/')
            f.write(f"file '{abs_path}'\n")

    cmd = [
        'ffmpeg', '-y', '-v', 'warning', '-hide_banner',
        '-f', 'concat',
        '-safe', '0',
        '-i', str(concat_list),
        '-c', 'copy',
        str(output_file)
    ]

    print(f"  $ {' '.join(cmd)}")
    subprocess.run(cmd, check=True)


def redact_video_single_pass(input_file, start_time, end_time, output_file=None):
    """
    Single-pass redaction using FFmpeg select/drawbox filters.
    Most reliable method - ensures perfect timing and duration.
    """
    input_path = Path(input_file).absolute()

    if not input_path.exists():
        raise FileNotFoundError(f"Input file not found: {input_path}")

    # Generate output filename if not provided
    if output_file is None:
        output_file = input_path.parent / f"{input_path.stem}_redacted{input_path.suffix}"
    else:
        output_file = Path(output_file).absolute()

    print(f"\n{'='*70}")
    print(f"FAST VIDEO REDACTION - Single Pass Method")
    print(f"{'='*70}")
    print(f"Input:  {input_path.name}")
    print(f"Output: {output_file.name}")
    print(f"Redact: {start_time} to {end_time}")
    print(f"{'='*70}\n")

    # Step 1: Probe video
    print("[1/2] Probing video properties...")
    props = probe_video(input_path)
    print(f"  Resolution:   {props['width']}x{props['height']}")
    print(f"  Frame Rate:   {props['fps']:.3f} fps ({props['fps_str']})")
    print(f"  Duration:     {props['duration']:.2f} seconds")
    print(f"  Codec:        {props['codec']}")
    print(f"  Pixel Format: {props['pix_fmt']}")
    print()

    # Calculate times
    start_sec = time_to_seconds(start_time)
    end_sec = time_to_seconds(end_time)

    if end_sec <= start_sec:
        raise ValueError("Invalid redaction range: end time must be after start time")

    # Step 2: Single-pass redaction with conditional filter
    print(f"[2/2] Processing video with selective blackout filter...")

    # Build filter that blacks out frames between start_sec and end_sec
    # Using geq (generic equation) filter for conditional processing
    vf = f"geq=lum='if(between(T,{start_sec},{end_sec}),0,lum(X,Y))':cb='if(between(T,{start_sec},{end_sec}),128,cb(X,Y))':cr='if(between(T,{start_sec},{end_sec}),128,cr(X,Y))'"

    cmd = [
        'ffmpeg', '-y', '-v', 'warning', '-hide_banner',
        '-i', str(input_path),
        '-vf', vf,
        '-c:v', 'libx264',
        '-preset', 'ultrafast',
        '-crf', '18',
        '-pix_fmt', props['pix_fmt'],
        '-an',  # Remove audio
        str(output_file)
    ]

    print(f"  $ {' '.join(cmd)}")
    subprocess.run(cmd, check=True)
    print(f"  ✓ Video processed\n")

    print(f"{'='*70}")
    print(f"✓ SUCCESS! Redacted video saved to:")
    print(f"  {output_file}")
    print(f"{'='*70}\n")


def redact_video(input_file, start_time, end_time, output_file=None):
    """
    Main redaction function - delegates to single-pass method.
    """
    return redact_video_single_pass(input_file, start_time, end_time, output_file)


def main():
    if len(sys.argv) < 4:
        print("Usage: python fast_redact.py <input_file> <start_time> <end_time> [output_file]")
        print("\nExamples:")
        print("  python fast_redact.py Cart_Center_2.mp4 00:10:00 00:11:00")
        print("  python fast_redact.py video.mp4 00:05:30 00:06:45 output.mp4")
        print("  python fast_redact.py video.mp4 0 66")
        print("  python fast_redact.py video.mp4 30.5 90.75 output.mp4")
        print("\nTime format: HH:MM:SS, HH:MM:SS.mmm, or plain seconds (int/float)")
        sys.exit(1)

    input_file = sys.argv[1]
    start_time = sys.argv[2]
    end_time = sys.argv[3]
    output_file = sys.argv[4] if len(sys.argv) > 4 else None

    try:
        redact_video(input_file, start_time, end_time, output_file)
    except subprocess.CalledProcessError as e:
        print(f"\n✗ ERROR: FFmpeg command failed")
        print(f"  Command returned exit code {e.returncode}")
        sys.exit(1)
    except Exception as e:
        print(f"\n✗ ERROR: {e}")
        sys.exit(1)


if __name__ == '__main__':
    main()
