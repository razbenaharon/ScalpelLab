"""
SEQ Frame Inspector — Parse Frame Metadata Directly from a SEQ File
====================================================================
Reads a NorPix StreamPix .seq file (no .idx required) and reconstructs
per-frame metadata by:

  1. Parsing the 1024-byte SEQ file header (codec, dimensions, FPS, frame count)
  2. Walking the file body starting at offset 1024, detecting frame boundaries
     using codec-appropriate markers (H.264 Annex B start codes, JPEG SOI/EOI)
  3. Reading the 8-byte inter-frame padding between frames
  4. Reporting: frame index, byte offset, frame size, frame type, padding bytes

SEQ body layout (from norpix_idx_format_reference.md):
    [1024-byte header][frame_data][8-byte padding][frame_data][8-byte padding]...
    offset[n+1] = offset[n] + size[n] + 8

Usage:
    python scripts/helpers/inspect_seq_frames.py path/to/file.seq [--max-frames N] [--hex-header]

Author: Raz (Technion)
"""

import struct
import sys
import argparse
from pathlib import Path
from datetime import datetime


# =============================================================================
# SEQ Header Layout (1024 bytes, Little Endian)
# Based on published Norpix StreamPix documentation and open-source references.
# =============================================================================
SEQ_MAGIC        = b"Norpix seq  "   # 24 bytes at offset 0 (null-padded)
SEQ_HEADER_SIZE  = 1024
INTER_FRAME_PAD  = 8                  # bytes between frames

# Known header field offsets
HDR_MAGIC_OFFSET         = 0      # 24 bytes  — "Norpix seq  " signature
HDR_VERSION_OFFSET       = 24     # 8 bytes   — version string (e.g. "1.09")
HDR_HEADER_SIZE_OFFSET   = 548    # uint32    — should always be 1024
HDR_WIDTH_OFFSET         = 548    # uint32
HDR_HEIGHT_OFFSET        = 552    # uint32
HDR_BIT_DEPTH_OFFSET     = 556    # uint32    — bits per component (e.g. 8)
HDR_BIT_DEPTH_R_OFFSET   = 560    # uint32    — real bit depth
HDR_IMAGE_SIZE_OFFSET    = 564    # uint32    — bytes per frame (uncompressed)
HDR_TRUE_IMAGE_OFFSET    = 568    # uint32
HDR_SUGGEST_IMG_OFFSET   = 572    # uint32
HDR_FORMAT_OFFSET        = 580    # uint32    — compression codec (see CODEC_MAP)
HDR_ALLOC_FRAMES_OFFSET  = 584    # uint32    — allocated frame slots
HDR_ORIGIN_X_OFFSET      = 592    # uint32
HDR_ORIGIN_Y_OFFSET      = 596    # uint32
HDR_TRUE_FRAMES_OFFSET   = 600    # uint32    — actual recorded frame count
HDR_FPS_OFFSET           = 612    # float64   — capture frames-per-second

# Compression codec codes
CODEC_MAP = {
    0:   "BMP (uncompressed)",
    100: "JPEG",
    101: "PNG",
    102: "TIFF",
    200: "H.264 (AVC)",
    201: "H.265 (HEVC)",
}

# Frame-type markers
JPEG_SOI       = b'\xff\xd8'              # JPEG Start of Image
JPEG_EOI       = b'\xff\xd9'              # JPEG End of Image
H264_START     = b'\x00\x00\x00\x01'     # H.264 Annex B start code

H264_NAL_NAMES = {
    1:  "P/B-slice",
    5:  "IDR (I-frame)",
    6:  "SEI",
    7:  "SPS",
    8:  "PPS",
    9:  "Access Unit Delimiter",
}


# =============================================================================
# Header Parsing
# =============================================================================
def _safe_u32(data: bytes, offset: int) -> int:
    """Read uint32 LE at offset, return 0 on out-of-bounds."""
    if offset + 4 > len(data):
        return 0
    return struct.unpack_from('<I', data, offset)[0]


def _safe_f64(data: bytes, offset: int) -> float:
    """Read float64 LE at offset, return 0.0 on out-of-bounds."""
    if offset + 8 > len(data):
        return 0.0
    return struct.unpack_from('<d', data, offset)[0]


def parse_seq_header(header: bytes) -> dict:
    """
    Parse the 1024-byte NorPix SEQ file header.

    Returns a dict with all extracted fields plus a 'valid' bool
    indicating whether the magic bytes matched.
    """
    magic = header[:24]
    valid = magic[:12] == SEQ_MAGIC[:12]  # first 12 chars of "Norpix seq  "

    try:
        version_raw = header[HDR_VERSION_OFFSET:HDR_VERSION_OFFSET + 8]
        version = version_raw.rstrip(b'\x00').decode('latin-1', errors='replace')
    except Exception:
        version = "?"

    width       = _safe_u32(header, HDR_WIDTH_OFFSET)
    height      = _safe_u32(header, HDR_HEIGHT_OFFSET)
    bit_depth   = _safe_u32(header, HDR_BIT_DEPTH_OFFSET)
    image_size  = _safe_u32(header, HDR_IMAGE_SIZE_OFFSET)
    format_code = _safe_u32(header, HDR_FORMAT_OFFSET)
    alloc_frames= _safe_u32(header, HDR_ALLOC_FRAMES_OFFSET)
    true_frames = _safe_u32(header, HDR_TRUE_FRAMES_OFFSET)
    origin_x    = _safe_u32(header, HDR_ORIGIN_X_OFFSET)
    origin_y    = _safe_u32(header, HDR_ORIGIN_Y_OFFSET)
    fps         = _safe_f64(header, HDR_FPS_OFFSET)

    codec_name  = CODEC_MAP.get(format_code, f"Unknown ({format_code})")

    return {
        'valid':        valid,
        'magic':        magic[:12],
        'version':      version,
        'width':        width,
        'height':       height,
        'bit_depth':    bit_depth,
        'image_size':   image_size,
        'format_code':  format_code,
        'codec':        codec_name,
        'alloc_frames': alloc_frames,
        'true_frames':  true_frames,
        'origin_x':     origin_x,
        'origin_y':     origin_y,
        'fps':          fps,
    }


# =============================================================================
# Frame Walking — H.264
# =============================================================================
def _h264_frame_type(data: bytes) -> str:
    """
    Return a human-readable frame type label by inspecting the first
    NAL unit found with a 4-byte Annex B start code.
    """
    pos = data.find(H264_START)
    while pos >= 0 and pos + 5 <= len(data):
        nal_type = data[pos + 4] & 0x1F
        if nal_type in (1, 5, 9):
            return H264_NAL_NAMES.get(nal_type, f"NAL {nal_type}")
        pos = data.find(H264_START, pos + 4)
    return "H.264 (unknown NAL)"


def walk_h264_frames(f, file_size: int, max_frames: int):
    """
    Walk H.264 frames in a SEQ file body starting at offset 1024.

    Strategy: scan forward for the next H.264 Annex B start code (00 00 00 01)
    to find the boundary of each frame, then account for the 8-byte inter-frame
    padding that follows each frame's data.

    Yields dicts with: index, offset, size, frame_type, padding_bytes.
    """
    # Gather all start-code positions first (memory efficient: read in chunks)
    CHUNK = 4 * 1024 * 1024  # 4 MB read chunks
    offsets = []
    pos = SEQ_HEADER_SIZE
    prev_tail = b''

    f.seek(pos)
    while pos < file_size:
        chunk = f.read(min(CHUNK, file_size - pos))
        if not chunk:
            break

        search = prev_tail + chunk
        search_base = pos - len(prev_tail)
        start = 0

        while True:
            idx = search.find(H264_START, start)
            if idx < 0:
                break
            abs_offset = search_base + idx
            if abs_offset >= SEQ_HEADER_SIZE:
                offsets.append(abs_offset)
            start = idx + 4

        prev_tail = search[-3:]  # keep overlap to catch split start codes
        pos += len(chunk)

    if not offsets:
        return

    # Pair consecutive start-code positions to compute frame sizes.
    # Each "frame" in the SEQ ends 8 bytes before the next start code
    # (the 8-byte inter-frame padding lives between frame_data and the next code).
    for i, frame_off in enumerate(offsets):
        if i >= max_frames:
            break

        if i + 1 < len(offsets):
            raw_end = offsets[i + 1]  # start of next frame (including its start code)
            # Last 8 bytes before the next start code are inter-frame padding
            frame_end = raw_end - INTER_FRAME_PAD
            frame_size = frame_end - frame_off

            # Read padding bytes
            f.seek(frame_end)
            padding = f.read(INTER_FRAME_PAD)
        else:
            # Last frame: extends to EOF
            frame_size = file_size - frame_off
            padding = b''

        if frame_size <= 0:
            continue

        # Read a small sample to identify frame type
        f.seek(frame_off)
        sample = f.read(min(256, frame_size))
        frame_type = _h264_frame_type(sample)

        yield {
            'index':        i,
            'offset':       frame_off,
            'size':         frame_size,
            'frame_type':   frame_type,
            'padding_bytes': padding.hex() if padding else '',
        }


# =============================================================================
# Frame Walking — JPEG
# =============================================================================
def walk_jpeg_frames(f, file_size: int, max_frames: int):
    """
    Walk JPEG frames in a SEQ file body starting at offset 1024.

    Strategy: scan for JPEG SOI (FF D8) markers to find frame starts,
    then find the matching EOI (FF D9) to determine each frame's end.
    The 8-byte inter-frame padding follows each EOI.

    Yields dicts with: index, offset, size, frame_type, padding_bytes.
    """
    CHUNK = 4 * 1024 * 1024
    frame_index = 0
    pos = SEQ_HEADER_SIZE
    f.seek(pos)

    while pos < file_size and frame_index < max_frames:
        chunk = f.read(min(CHUNK, file_size - pos))
        if not chunk:
            break

        soi_idx = chunk.find(JPEG_SOI)
        if soi_idx < 0:
            pos += len(chunk)
            continue

        frame_off = pos + soi_idx

        # Find matching EOI — read from frame start
        f.seek(frame_off)
        frame_data = f.read(min(20 * 1024 * 1024, file_size - frame_off))  # up to 20MB
        eoi_idx = frame_data.rfind(JPEG_EOI)  # last EOI in this chunk

        if eoi_idx < 0:
            pos += len(chunk)
            continue

        frame_size = eoi_idx + 2  # include the EOI bytes

        # Read 8-byte inter-frame padding
        pad_off = frame_off + frame_size
        f.seek(pad_off)
        padding = f.read(INTER_FRAME_PAD)

        yield {
            'index':        frame_index,
            'offset':       frame_off,
            'size':         frame_size,
            'frame_type':   "JPEG",
            'padding_bytes': padding.hex() if padding else '',
        }

        frame_index += 1
        pos = pad_off + INTER_FRAME_PAD  # skip past padding to next frame
        f.seek(pos)


# =============================================================================
# Frame Walking — Fixed-Size (BMP / RAW)
# =============================================================================
def walk_fixed_frames(f, file_size: int, max_frames: int, frame_size: int):
    """
    Walk fixed-size frames (BMP, RAW) using the known per-frame byte count
    from the SEQ header's image_size field.

    offset[n+1] = offset[n] + frame_size + 8 (inter-frame padding)

    Yields dicts with: index, offset, size, frame_type, padding_bytes.
    """
    if frame_size <= 0:
        return

    stride = frame_size + INTER_FRAME_PAD
    offset = SEQ_HEADER_SIZE
    frame_index = 0

    while offset + frame_size <= file_size and frame_index < max_frames:
        # Read 8-byte padding after frame
        pad_off = offset + frame_size
        f.seek(pad_off)
        padding = f.read(INTER_FRAME_PAD) if pad_off + INTER_FRAME_PAD <= file_size else b''

        yield {
            'index':        frame_index,
            'offset':       offset,
            'size':         frame_size,
            'frame_type':   "fixed",
            'padding_bytes': padding.hex() if padding else '',
        }

        offset += stride
        frame_index += 1


# =============================================================================
# Main Inspection Logic
# =============================================================================
def inspect_seq(seq_path: Path, max_frames: int = 100, hex_header: bool = False):
    """Parse and display frame metadata from a SEQ file without an IDX."""
    file_size = seq_path.stat().st_size
    print("=" * 80)
    print(f"SEQ FRAME INSPECTOR")
    print("=" * 80)
    print(f"File : {seq_path}")
    print(f"Size : {file_size:,} bytes ({file_size / (1024**3):.3f} GB)")
    print()

    with open(seq_path, 'rb') as f:
        # ── 1. Parse header ──────────────────────────────────────────────────
        header = f.read(SEQ_HEADER_SIZE)

    if len(header) < SEQ_HEADER_SIZE:
        print(f"ERROR: File too small for a SEQ header ({len(header)} bytes, need {SEQ_HEADER_SIZE})")
        return

    hdr = parse_seq_header(header)

    print("-- SEQ HEADER -----------------------------------------------------")
    print(f"  Magic valid : {'YES' if hdr['valid'] else 'NO - unexpected magic bytes'}")
    print(f"  Magic bytes : {hdr['magic']!r}")
    print(f"  Version     : {hdr['version']}")
    print(f"  Resolution  : {hdr['width']} x {hdr['height']}")
    print(f"  Bit depth   : {hdr['bit_depth']}")
    print(f"  Image size  : {hdr['image_size']} bytes  (uncompressed frame size)")
    print(f"  Codec       : {hdr['codec']}  (code={hdr['format_code']})")
    print(f"  FPS         : {hdr['fps']:.6f}")
    print(f"  Alloc frames: {hdr['alloc_frames']}")
    print(f"  True frames : {hdr['true_frames']}")
    print(f"  Origin      : ({hdr['origin_x']}, {hdr['origin_y']})")
    print()

    if hex_header:
        print("-- HEADER HEX DUMP (first 128 bytes) -----------------------------")
        for row in range(0, 128, 16):
            hex_part = ' '.join(f'{b:02x}' for b in header[row:row+16])
            asc_part = ''.join(chr(b) if 32 <= b < 127 else '.' for b in header[row:row+16])
            print(f"  {row:04x}: {hex_part:<48}  {asc_part}")
        print()

    # ── 2. Walk frames ───────────────────────────────────────────────────────
    fmt = hdr['format_code']
    print(f"-- FRAME SCAN (first {max_frames} frames) ---------------------------------")
    print(f"  Codec   : {hdr['codec']}")
    print(f"  Method  : ", end="")

    if fmt in (200, 201):
        print("H.264 Annex B start-code scan")
        walker = walk_h264_frames
        walk_args = ()
    elif fmt == 100:
        print("JPEG SOI/EOI marker scan")
        walker = walk_jpeg_frames
        walk_args = ()
    elif fmt == 0:
        print(f"Fixed-size walk (frame_size={hdr['image_size']} bytes from header)")
        walker = walk_fixed_frames
        walk_args = (hdr['image_size'],)
    else:
        print(f"Unknown codec {fmt} - attempting H.264 start-code scan as fallback")
        walker = walk_h264_frames
        walk_args = ()

    print()
    print(f"  {'IDX':>6}  {'OFFSET':>14}  {'SIZE':>10}  {'TYPE':<22}  PADDING (8 bytes)")
    print("  " + "-" * 70)

    frames = []
    total_bytes = 0

    with open(seq_path, 'rb') as f:
        for frame in walker(f, file_size, max_frames, *walk_args):
            frames.append(frame)
            total_bytes += frame['size']
            print(f"  {frame['index']:>6}  {frame['offset']:>14,}  {frame['size']:>10,}  "
                  f"{frame['frame_type']:<22}  {frame['padding_bytes'] or '(none/EOF)'}")

    print("  " + "-" * 70)
    print()

    # -- 3. Summary -----------------------------------------------------------
    print("-- SUMMARY --------------------------------------------------------")
    n = len(frames)
    print(f"  Frames found   : {n}")

    if n > 0:
        f0 = frames[0]
        fN = frames[-1]
        data_span = (fN['offset'] + fN['size'] + INTER_FRAME_PAD) - f0['offset']

        print(f"  First offset   : {f0['offset']:,}  (expected {SEQ_HEADER_SIZE} for frame 0)")
        print(f"  Last  offset   : {fN['offset']:,}")
        print(f"  Total frame bytes: {total_bytes:,}")
        print(f"  Avg frame size : {total_bytes / n:,.0f} bytes")
        print(f"  Data span      : {data_span:,} bytes")

        # Validate inter-frame formula: offset[n+1] == offset[n] + size[n] + 8
        violations = 0
        for i in range(1, len(frames)):
            expected = frames[i-1]['offset'] + frames[i-1]['size'] + INTER_FRAME_PAD
            actual   = frames[i]['offset']
            if actual != expected:
                violations += 1
                if violations <= 5:
                    print(f"  WARNING frame {i}: expected offset {expected:,}, got {actual:,} "
                          f"(delta={actual - expected:+,})")

        if violations == 0:
            print(f"  Inter-frame gap: OK - all gaps are exactly {INTER_FRAME_PAD} bytes")
        else:
            print(f"  Inter-frame gap: {violations} violation(s) of offset[n+1]=offset[n]+size[n]+8")

        # Check padding bytes for patterns
        unique_pads = set(fr['padding_bytes'] for fr in frames if fr['padding_bytes'])
        if len(unique_pads) == 1:
            print(f"  Padding bytes  : constant = {next(iter(unique_pads))}")
        elif len(unique_pads) <= 5:
            print(f"  Padding bytes  : {len(unique_pads)} unique values: {unique_pads}")
        else:
            print(f"  Padding bytes  : {len(unique_pads)} unique values (variable content)")

        # Estimate total frame count from file size
        if n >= 2:
            avg_stride = (frames[-1]['offset'] - frames[0]['offset']) / (n - 1)
            body_size = file_size - SEQ_HEADER_SIZE
            estimated_total = int(body_size / avg_stride) if avg_stride > 0 else 0
            print(f"  Estimated total: ~{estimated_total:,} frames in full file "
                  f"(avg stride {avg_stride:,.0f} bytes)")
            if hdr['true_frames'] > 0:
                print(f"  Header says    : {hdr['true_frames']:,} frames")

    print("=" * 80)


# =============================================================================
# Entry Point
# =============================================================================
def main():
    parser = argparse.ArgumentParser(
        description="Inspect NorPix SEQ frame metadata without an IDX file.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python inspect_seq_frames.py recording.seq
  python inspect_seq_frames.py recording.seq --max-frames 500
  python inspect_seq_frames.py recording.seq --hex-header
        """,
    )
    parser.add_argument("seq_file", help="Path to the .seq file")
    parser.add_argument(
        "--max-frames", type=int, default=100,
        help="Maximum number of frames to scan and display (default: 100)",
    )
    parser.add_argument(
        "--hex-header", action="store_true",
        help="Print a hex dump of the first 128 bytes of the SEQ header",
    )
    args = parser.parse_args()

    seq_path = Path(args.seq_file)
    if not seq_path.exists():
        print(f"ERROR: File not found: {seq_path}")
        sys.exit(1)
    if not seq_path.is_file():
        print(f"ERROR: Not a file: {seq_path}")
        sys.exit(1)

    inspect_seq(seq_path, max_frames=args.max_frames, hex_header=args.hex_header)


if __name__ == "__main__":
    main()
