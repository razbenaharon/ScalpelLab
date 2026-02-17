#!/usr/bin/env python3
"""
IDX + SEQ file structure analyzer v2.

Reads the SEQ file header and frame headers to find timestamps,
since the IDX file in this format doesn't contain per-frame timestamps.

Usage:
    python debug_idx_v2.py "path/to/file.seq"
    python debug_idx_v2.py "path/to/file.seq.idx"
    (either one works - it will find the other automatically)
"""

import struct
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path


WINDOWS_EPOCH = datetime(1601, 1, 1, tzinfo=timezone.utc)


def hex_dump(data: bytes, offset: int = 0, width: int = 16) -> str:
    lines = []
    for i in range(0, len(data), width):
        chunk = data[i:i + width]
        hex_part = ' '.join(f'{b:02X}' for b in chunk)
        ascii_part = ''.join(chr(b) if 32 <= b < 127 else '.' for b in chunk)
        lines.append(f'  {offset + i:06X}  {hex_part:<{width * 3}}  |{ascii_part}|')
    return '\n'.join(lines)


def try_timestamp(val, label=""):
    """Try to interpret a value as various timestamp formats."""
    results = []

    # Unix timestamp (uint32)
    if 946684800 < val < 4102444800:  # 2000-2100
        dt = datetime.fromtimestamp(val, tz=timezone.utc)
        results.append(f"  {label}Unix timestamp: {dt.strftime('%Y-%m-%d %H:%M:%S')} UTC")

    # Windows FILETIME (uint64, 100ns since 1601-01-01)
    if 125000000000000000 < val < 160000000000000000:  # ~1996 to ~2107
        dt = WINDOWS_EPOCH + timedelta(microseconds=val // 10)
        results.append(f"  {label}Windows FILETIME: {dt.strftime('%Y-%m-%d %H:%M:%S.%f')} UTC")

    return results


def analyze_seq_header(seq_path: Path):
    """Parse the 1024-byte NorPix SEQ file header."""
    print("=" * 70)
    print("SEQ FILE HEADER ANALYSIS")
    print("=" * 70)

    with open(seq_path, 'rb') as f:
        header = f.read(1024)

    if len(header) < 1024:
        print(f"[ERROR] SEQ header too short: {len(header)} bytes")
        return None

    # Magic number (bytes 0-3)
    magic = struct.unpack_from('<I', header, 0)[0]
    print(f"Magic: 0x{magic:08X}")

    # Header size (bytes 4-7)
    header_size = struct.unpack_from('<I', header, 4)[0]
    print(f"Header size: {header_size}")

    # Version (bytes 8-11)
    version = struct.unpack_from('<I', header, 8)[0]
    print(f"Version: {version}")

    # Image dimensions and format
    width = struct.unpack_from('<I', header, 460)[0]    # 0x1CC
    height = struct.unpack_from('<I', header, 464)[0]    # 0x1D0
    bit_depth = struct.unpack_from('<I', header, 468)[0]  # 0x1D4
    bit_depth_real = struct.unpack_from('<I', header, 472)[0]  # 0x1D8

    print(f"Image: {width} x {height}, {bit_depth} bit (real: {bit_depth_real} bit)")

    # True image size (0x1E0 = 480)
    true_image_size = struct.unpack_from('<I', header, 480)[0]
    print(f"True image size: {true_image_size:,} bytes")

    # Image format (0x1E4 = 484)
    image_format = struct.unpack_from('<I', header, 484)[0]
    formats = {0: 'Unknown', 100: 'Mono8', 101: 'Mono16',
               200: 'BGR24', 201: 'BGR48',
               300: 'BGRA32', 400: 'JPEG', 500: 'Bayer8', 600: 'Bayer16'}
    fmt_name = formats.get(image_format, f'Code_{image_format}')
    print(f"Image format: {fmt_name} ({image_format})")

    # Number of frames (0x1E8 = 488)
    num_frames = struct.unpack_from('<I', header, 488)[0]
    print(f"Number of frames: {num_frames:,}")

    # FPS (0x1F8 = 504, double 8 bytes)
    fps = struct.unpack_from('<d', header, 504)[0]
    print(f"Suggested FPS: {fps}")

    if num_frames > 0 and fps > 0:
        duration = num_frames / fps
        print(f"Estimated duration: {duration:.1f}s ({duration/60:.1f}min)")

    # Description string (often at offset 18, length varies)
    # Try to find readable ASCII strings in the header
    print(f"\n--- Header hex dump (first 64 bytes) ---")
    print(hex_dump(header[:64]))

    print(f"\n--- Header hex dump (offsets 0x1C0-0x210) ---")
    print(hex_dump(header[0x1C0:0x210], offset=0x1C0))

    # Search for timestamps in the header
    print(f"\n--- Searching for timestamps in header ---")
    for offset in range(0, len(header) - 7):
        # Try as uint32 (Unix)
        val32 = struct.unpack_from('<I', header, offset)[0]
        results = try_timestamp(val32, f"offset 0x{offset:04X} (uint32): ")
        for r in results:
            print(r)

        # Try as uint64 (Windows FILETIME)
        if offset <= len(header) - 8:
            val64 = struct.unpack_from('<Q', header, offset)[0]
            results = try_timestamp(val64, f"offset 0x{offset:04X} (uint64): ")
            for r in results:
                print(r)

    return {
        'header_size': header_size,
        'width': width,
        'height': height,
        'bit_depth': bit_depth,
        'true_image_size': true_image_size,
        'num_frames': num_frames,
        'fps': fps,
        'image_format': image_format,
    }


def analyze_frame_headers(seq_path: Path, seq_info: dict, num_frames_to_check: int = 5):
    """Read per-frame headers from the SEQ file."""
    print("\n" + "=" * 70)
    print("SEQ FRAME HEADER ANALYSIS")
    print("=" * 70)

    header_size = seq_info.get('header_size', 1024)
    true_image_size = seq_info.get('true_image_size', 0)

    if true_image_size == 0:
        print("[WARN] True image size is 0, cannot reliably locate frame headers")
        # Try to get it from IDX
        return

    with open(seq_path, 'rb') as f:
        for frame_idx in range(min(num_frames_to_check, 5)):
            # Frame location: header + frame_idx * (frame_header + image_data)
            # NorPix SEQ typically has a small per-frame header before image data
            # Common per-frame header sizes: 0, 8, 16, 24, 32

            # The IDX told us frame 0 is at offset 1024 (= header_size)
            # And each frame block is true_image_size + some_header bytes

            print(f"\n--- Frame {frame_idx} ---")

            # Try reading from multiple potential positions
            for frame_header_size in [0, 8, 16, 24, 32, 40, 48, 64]:
                frame_block_size = true_image_size + frame_header_size
                frame_offset = header_size + frame_idx * frame_block_size

                f.seek(frame_offset)
                data = f.read(min(64, frame_header_size + 16) if frame_header_size > 0 else 64)

                if len(data) < 8:
                    continue

                if frame_idx == 0 and frame_header_size <= 64:
                    # Only show hex for frame 0
                    if frame_header_size == 0:
                        print(f"\n  frame_header_size={frame_header_size}: reading from SEQ offset 0x{frame_offset:X}")
                        print(hex_dump(data[:64], offset=frame_offset))

                # Search for timestamps in this region
                for off in range(0, min(len(data) - 7, 48)):
                    val32 = struct.unpack_from('<I', data, off)[0]
                    if 946684800 < val32 < 4102444800:
                        dt = datetime.fromtimestamp(val32, tz=timezone.utc)
                        abs_off = frame_offset + off
                        if frame_idx == 0:
                            print(f"  ** UNIX TS at frame_header_size={frame_header_size}, byte {off}: "
                                  f"{val32} = {dt.strftime('%Y-%m-%d %H:%M:%S')} "
                                  f"(SEQ offset 0x{abs_off:X})")

                    if off <= len(data) - 8:
                        val64 = struct.unpack_from('<Q', data, off)[0]
                        if 125000000000000000 < val64 < 160000000000000000:
                            dt = WINDOWS_EPOCH + timedelta(microseconds=val64 // 10)
                            abs_off = frame_offset + off
                            print(f"  ** FILETIME at frame_header_size={frame_header_size}, byte {off}: "
                                  f"{dt.strftime('%Y-%m-%d %H:%M:%S.%f')} "
                                  f"(SEQ offset 0x{abs_off:X})")


def analyze_idx(idx_path: Path):
    """Analyze IDX file structure."""
    print("\n" + "=" * 70)
    print("IDX FILE ANALYSIS")
    print("=" * 70)

    file_size = idx_path.stat().st_size
    print(f"File: {idx_path.name}")
    print(f"Size: {file_size:,} bytes")

    with open(idx_path, 'rb') as f:
        data = f.read(min(file_size, 512))

    print(f"\n--- First 128 bytes ---")
    print(hex_dump(data[:128]))

    # Assuming 32-byte entries with no header
    entry_count = file_size // 32
    print(f"\nEntry count (assuming 32 bytes/entry): {entry_count:,}")

    # Parse first few entries
    print(f"\n--- First 5 entries (32 bytes each) ---")
    for i in range(min(5, len(data) // 32)):
        block = data[i * 32:(i + 1) * 32]
        offset = struct.unpack_from('<Q', block, 0)[0]
        size = struct.unpack_from('<I', block, 8)[0]
        field_12 = struct.unpack_from('<I', block, 12)[0]
        field_16 = struct.unpack_from('<I', block, 16)[0]
        field_20 = struct.unpack_from('<I', block, 20)[0]
        field_24 = struct.unpack_from('<I', block, 24)[0]
        field_28 = struct.unpack_from('<I', block, 28)[0]

        print(f"  Entry {i}: offset={offset:>15,}  size={size:>13,}  "
              f"[12]={field_12:>11,}  [16]={field_16:>11,}  "
              f"[20]={field_20}  [24]={field_24}  [28]={field_28}")


def main():
    if len(sys.argv) < 2:
        print("Usage: python debug_idx_v2.py <path_to_seq_or_idx_file>")
        print("\nProvide either a .seq file or .seq.idx file - it will find the other.")
        sys.exit(1)

    input_path = Path(sys.argv[1])

    # Determine SEQ and IDX paths
    if input_path.name.endswith('.seq.idx'):
        idx_path = input_path
        seq_path = Path(str(input_path)[:-4])  # Remove .idx
    elif input_path.suffix == '.idx':
        idx_path = input_path
        seq_path = input_path.with_suffix('.seq')
    elif input_path.suffix == '.seq':
        seq_path = input_path
        idx_path = Path(str(input_path) + '.idx')
        if not idx_path.is_file():
            idx_path = input_path.with_suffix('.idx')
    else:
        print(f"Unknown file type: {input_path.suffix}")
        sys.exit(1)

    print(f"SEQ file: {seq_path} {'(EXISTS)' if seq_path.is_file() else '(NOT FOUND)'}")
    print(f"IDX file: {idx_path} {'(EXISTS)' if idx_path.is_file() else '(NOT FOUND)'}")

    # Analyze SEQ header
    seq_info = None
    if seq_path.is_file():
        seq_info = analyze_seq_header(seq_path)
        if seq_info:
            analyze_frame_headers(seq_path, seq_info)
    else:
        print(f"\n[WARN] SEQ file not found: {seq_path}")
        print("Cannot analyze frame headers without the SEQ file.")

    # Analyze IDX
    if idx_path.is_file():
        analyze_idx(idx_path)
    else:
        print(f"\n[WARN] IDX file not found: {idx_path}")

    # Summary
    print("\n" + "=" * 70)
    print("FILENAME-BASED TIMESTAMP (fallback)")
    print("=" * 70)
    # Try to extract date from filename
    seq_name = seq_path.stem  # e.g. '2025-07-20_07-59-39'
    if seq_name.endswith('.seq'):
        seq_name = seq_name[:-4]
    try:
        dt = datetime.strptime(seq_name, '%Y-%m-%d_%H-%M-%S')
        print(f"Parsed from filename '{seq_name}': {dt}")
    except ValueError:
        print(f"Could not parse timestamp from filename: {seq_name}")


if __name__ == "__main__":
    main()