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
