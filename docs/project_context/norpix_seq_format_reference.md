# NorPix StreamPix SEQ File Format Reference

## Overview

Binary video container produced by **NorPix StreamPix 9.x (x64)**.
One `.seq` file holds all frames for a single camera channel in a single recording session.
An accompanying `.idx` file provides a per-frame byte-offset index; see `norpix_idx_format_reference.md`.

All multi-byte integers are **little-endian** unless stated otherwise.

---

## File Layout

```
[  1024 bytes  ] File Header
[ IDX[0].size  ] Frame 0 block  (8-byte frame prefix + H.264 Annex B payload)
[    8 bytes   ] Per-frame timestamp: ts_sec (uint32 LE) + ts_ms (uint16 LE) + ts_us (uint16 LE)
[ IDX[1].size  ] Frame 1 block
[    8 bytes   ] Per-frame timestamp
        ...
[ IDX[N].size  ] Frame N block
[    8 bytes   ] Per-frame timestamp  (may be absent at EOF)
```

Each **frame block** consists of an 8-byte per-frame prefix followed by H.264
Annex B compressed data. Frame sizes are variable; the IDX `size` field is the
authoritative source for each frame's block size (prefix + payload).

The 8 bytes after each frame block are a **per-frame hardware timestamp**, not padding.
They are identical to the `ts_seconds` and `ts_sub` fields in the IDX file.

### Offset formula (H.264 / variable-size frames)
```
frame_offset[0]   = 1024
frame_offset[n+1] = frame_offset[n] + IDX[n].size + 8
```

Use the IDX file for all offset/size lookups. The `image_size` header field
(offset 564) holds the **uncompressed** frame size in bytes and is useful for
computing buffer allocations, but does **not** reflect the actual compressed
payload size stored on disk.

---

## Section 1 — File Header (bytes 0–1023)

### 1.1 Identity block (bytes 0–35)

| Offset | Size | Type      | Field           | Value observed                                      |
|--------|------|-----------|-----------------|-----------------------------------------------------|
| 0      | 4    | uint32    | magic           | `0xEDFE0000` (constant; little-endian `00 00 FE ED`) |
| 4      | 22   | UTF-16 LE | file_type_name  | `"Norpix seq\0"` — null-terminated wide string      |
| 26     | 2    | uint16    | version_minor   | e.g. `0x1D15` (varies by recorder version)          |
| 28     | 4    | uint32    | version_major   | e.g. `5`                                            |
| 32     | 4    | uint32    | header_size     | Always `1024`                                       |

> **Magic note**: the first 4 bytes are `ED FE 00 00` — not a standard BOM.
> The magic can be read as `uint32 LE = 0x0000FEED = 65261`.
> The "Norpix seq" string is stored as UTF-16 LE (2 bytes per character),
> so `N` appears as `4E 00`, not `4E`.

### 1.2 Description field (bytes 36–547, 512 bytes total)

| Offset | Size | Type      | Field       | Value observed                       |
|--------|------|-----------|-------------|--------------------------------------|
| 36     | 512  | UTF-16 LE | description | `"StreamPix 9.1.1.0 (x64)\0"` + null padding |

The description is a fixed-size 512-byte field (confirmed by py-norpix-reader and
PIMS source). The actual string occupies bytes 36–85 (25 UTF-16 LE characters
including null terminator); bytes 86–547 are null padding.

Some sparse non-zero bytes appear in the padding (86–547). These are runtime
memory artifacts (pointer-sized values, sentinels) written by StreamPix when it
serialises its internal state into the header. **Do not rely on values in bytes
86–547.**

Notable artifact clusters observed:

| Offset | Observed value | Notes                                      |
|--------|----------------|--------------------------------------------|
| 92     | `0x34A8C3C0`   | Runtime artifact; constant within a session |
| 124    | `8`            | Runtime artifact                           |
| 148    | `0xFFFFFFFFFFFFFFFF` | Sentinel / no-limit marker           |

### 1.4 Image geometry block (bytes 548–583)

All fields are `uint32 LE`.

| Offset | Field              | Description                                              |
|--------|--------------------|----------------------------------------------------------|
| 548    | **width**          | Frame width in pixels (e.g. `2048`)                      |
| 552    | **height**         | Frame height in pixels (e.g. `1536`)                     |
| 556    | **bit_depth**      | Bits per colour component (e.g. `16` for 16-bit mono)    |
| 560    | **bit_depth_real** | Actual significant bits; often equal to `bit_depth`      |
| 564    | **image_size**     | Bytes per raw frame = `width × height × (bit_depth / 8)` |
| 568    | **image_format**   | Pixel format code (see image format table below); e.g. `600` = Mono 16-bit unsigned |
| 572    | **allocated_frames** | Total frames recorded; equals the IDX frame count exactly (confirmed: Cart_Center_2 = 44,566 in both). Reflects the ring buffer pre-allocated by StreamPix. |
| 576    | **origin**         | Image origin flag; `0` = top-left                        |
| 580    | **true_image_size** | In older StreamPix: frame stride bytes (image + gap). In StreamPix 9.x: `0` — stride must be computed as `image_size + 8` |

#### Image format codes (offset 568)

| Code | Pixel format                        |
|------|-------------------------------------|
| 100  | Monochrome 8-bit                    |
| 200  | Monochrome 16-bit (signed)          |
| 600  | Monochrome 16-bit (unsigned)        |
| 102  | Bayer 8-bit                         |
| 103  | Bayer 16-bit                        |
| 300  | BGR 24-bit                          |

> **Note**: `image_format` (pixel layout) is separate from the compression codec.
> In StreamPix 9.x the compression/codec type appears elsewhere in the header.
> py-norpix-reader and PIMS only support `image_format = 100` (Mono 8-bit).
> Our files use `image_format = 600` (Mono 16-bit unsigned), which those libraries
> cannot read without the `as_raw` flag.

### 1.5 Timing and metadata block (bytes 584–639)

Field names from PIMS / py-norpix-reader source confirmed against StreamPix 9.x binaries.

| Offset | Size | Type    | Field                    | Description                                                |
|--------|----- |---------|--------------------------|------------------------------------------------------------|
| 584    | 8    | float64 | **fps** (`suggested_frame_rate`) | Actual capture frame rate (e.g. `30.0`, `17.79`). Use this as the authoritative FPS. |
| 592    | 4    | int32   | `description_format`     | Format of the description string; `0` = unformatted text  |
| 596    | 4    | uint32  | `reference_frame`        | Reference frame index (usually `0`)                        |
| 600    | 4    | uint32  | `fixed_size`             | Non-zero if all frames are fixed size; `0` in StreamPix 9.x |
| 604    | 4    | uint32  | `flags`                  | Capture flags; observed `18` (`0x12`)                      |
| 608    | 4    | int32   | `bayer_pattern`          | Bayer pattern code; `1` for mono sensors (not meaningful)  |
| 612    | 4    | int32   | `time_offset_us`         | Time offset in microseconds; `0`                           |
| 616    | 4    | int32   | `extended_header_size`   | Size of extended header if present; `0`                    |
| 620    | 4    | uint32  | `compression_format`     | In older StreamPix: `0` = uncompressed. In StreamPix 9.x: observed `8` — PIMS rejects files where this != 0, so PIMS is incompatible with StreamPix 9.x files. |
| 624    | 4    | uint32  | **reference_time_s**     | Recording start time — whole seconds, Unix epoch UTC. Per-camera (cameras in same session differ by seconds). Timezone of recording PC: UTC+2/UTC+3, confirmed across 879 files. |
| 628    | 2    | uint16  | **reference_time_ms**    | Recording start time — milliseconds (0–999)                |
| 630    | 2    | uint16  | **reference_time_us**    | Recording start time — microseconds (0–999)                |
| 632    | 4    | uint32  | `fps_integer`            | FPS rounded to nearest integer; redundant copy of `fps`    |
| 636    | 4    | uint32  | **exposure_ns**          | Camera exposure time in nanoseconds. Common: `1000000` (1 ms, 305 files), `1500000` (1.5 ms, 556 files), `5000000` (5 ms), `10000000` (10 ms). |

> **FPS note**: `fps` at offset 584 is the most reliable source for frame
> rate. It reflects the measured capture rate and is not necessarily a
> round number (e.g. `17.787470156647668`).

### 1.6 Remainder (bytes 640–1023)

Mostly zeroed with sparse non-zero values. Contains at minimum:

| Offset | Size | Observed | Notes                  |
|--------|------|----------|------------------------|
| 640    | 4    | `0x80000000` | Nearly constant (99.7% of 879 tested files); unknown flag register (high bit set); rare exceptions observed |
| 656    | 4    | `1`      | Unknown                |
| 660    | 4    | `8192`   | Unknown                |
| 664    | 4    | varies   | Related to offset 572: difference is 5 (62% of files) or 1 (35%); purpose unknown |
| 672    | 8    | `0xFFFFFFFFFFFFFFFF` | Sentinel   |

---

## Section 2 — Frame Data Body (bytes 1024–EOF)

### 2.1 Frame data (H.264, compression_format = 8)

All research files use `compression_format = 8` (StreamPix 9.x H.264).
Frame blocks are **variable-length**; each block = IDX `size` bytes.

Every frame block has an **8-byte per-frame prefix** followed immediately by the
H.264 Annex B bitstream:

| Prefix offset | Size | Type    | Field              | Description |
|---------------|------|---------|--------------------|-------------|
| 0             | 4    | uint32  | **frame_size**     | Total block size in bytes = IDX `size` field exactly (includes this 8-byte prefix). Verified for 1,000 consecutive frames: 0 mismatches. |
| 4             | 2    | uint16  | **fps_integer**    | FPS rounded to integer, only set on IDR/SPS frames (I-frames). `0` on all P/B-slice frames. Equals IDX `flags` field for IDR frames. |
| 6             | 2    | uint16  | **frame_no_lo16**  | Low 16 bits of IDX `frame_number` (the ring-buffer monotonic counter). |

The H.264 Annex B payload starts at byte 8 of the block and is
`frame_size − 8` bytes long:

| Frame type | NAL sequence                                              |
|------------|-----------------------------------------------------------|
| IDR (I)    | SPS (NAL type 7) → PPS (NAL type 8) → IDR slice (NAL 5) |
| P/B slice  | P/B slice (NAL type 1) only                              |

Start code: `00 00 00 01` (4-byte Annex B). IDR frames start with
`00 00 00 01 67` (SPS) and serve as decoder refresh points.

> **image_size vs compressed size**: `image_size` at header offset 564 is the
> *uncompressed* frame size (`width × height × bit_depth/8`). It does **not**
> reflect the compressed payload size. Use IDX `size − 8` for the actual H.264
> data length.

### 2.2 Inter-frame padding (8 bytes) — per-frame timestamp

The 8 bytes immediately following each frame's payload are **not padding** —
they are a **hardware-level per-frame timestamp** in the same encoding used by
the IDX file:

```
[frame_data]  [ts_sec: 4 bytes LE]  [ts_sub: 4 bytes LE]  [next frame_data] ...
```

| Bytes | Type   | Field      | Description                                            |
|-------|--------|------------|--------------------------------------------------------|
| 0–3   | uint32 | **ts_sec** | Whole seconds, Unix epoch UTC (identical to IDX field) |
| 4–5   | uint16 | **ts_ms**  | Milliseconds within the second (0–999)                 |
| 6–7   | uint16 | **ts_us**  | Microseconds within the millisecond (0–999)            |

The 8-byte layout matches the struct format `'<IHH'` (4+2+2 bytes) used by the
PIMS `NorpixSeq` reader for SEQ format version >= 5. The IDX `ts_sub` field
stores the same data packed into a 32-bit integer (ms in bits 15–0, µs in bits
31–16) — both encodings decode to identical timestamps.

**Decoding (two equivalent forms):**
```python
# Form 1: three separate fields (matches PIMS source)
ts_sec, ts_ms, ts_us = struct.unpack_from("<IHH", gap, 0)
t = ts_sec + ts_ms / 1000.0 + ts_us / 1_000_000.0

# Form 2: as ts_sec + packed ts_sub (matches IDX encoding)
ts_sec = struct.unpack_from("<I", gap, 0)[0]
ts_sub = struct.unpack_from("<I", gap, 4)[0]   # same bytes, read as uint32
ms = ts_sub & 0xFFFF
us = (ts_sub >> 16) & 0xFFFF
t  = ts_sec + ms / 1000.0 + us / 1_000_000.0
```

> **Version note**: SEQ format version < 5 (older StreamPix) uses only **6 bytes**
> per timestamp (`'<IH'` = ts_sec + ts_ms only, no microsecond field). Our files
> (StreamPix 9.x, format version 5) always use the full 8-byte form.

Verified by `scripts/helpers/verify_seq_timestamp_in_padding.py`: all 15
checked frames showed an exact byte-for-byte match with the IDX (ts_sec, ts_sub).
Each pattern appeared at exactly one location in the SEQ body. This is why
StreamPix can regenerate a complete, accurate IDX when one is missing.

### 2.3 Walk algorithm

**With IDX file (preferred for H.264):**

```python
import struct

IDX_RECORD_FMT  = "<QIIIIII"   # offset, size, ts_sec, ts_sub, reserved, flags, frame_no
IDX_RECORD_SIZE = 32
TIMESTAMP_FMT   = "<IHH"       # ts_sec (uint32), ts_ms (uint16), ts_us (uint16)

with open(seq_path, "rb") as seq, open(idx_path, "rb") as idx:
    while True:
        rec = idx.read(IDX_RECORD_SIZE)
        if len(rec) < IDX_RECORD_SIZE:
            break
        offset, size, ts_sec, ts_sub, _, flags, frame_no = struct.unpack(IDX_RECORD_FMT, rec)

        # Read per-frame prefix (8 bytes)
        seq.seek(offset)
        prefix = seq.read(8)
        block_size, fps_int, frno_lo16 = struct.unpack("<IHH", prefix)
        # block_size == size (always)

        # Read H.264 Annex B payload
        h264_data = seq.read(size - 8)   # starts with 00 00 00 01

        # Read per-frame timestamp from inter-frame gap
        ts_sec2, ts_ms, ts_us = struct.unpack(TIMESTAMP_FMT, seq.read(8))
        timestamp = ts_sec2 + ts_ms / 1000.0 + ts_us / 1_000_000.0
```

**Without IDX (H.264 self-describing walk using per-frame prefix):**

```python
import struct

SEQ_HEADER_SIZE = 1024
TIMESTAMP_SIZE  = 8

with open(seq_path, "rb") as f:
    f.seek(SEQ_HEADER_SIZE)
    index = 0
    while True:
        prefix = f.read(8)
        if len(prefix) < 8:
            break
        block_size = struct.unpack_from("<I", prefix)[0]
        if block_size == 0:
            break
        h264_data = f.read(block_size - 8)   # H.264 Annex B
        ts_bytes  = f.read(TIMESTAMP_SIZE)    # per-frame timestamp
        if len(ts_bytes) == TIMESTAMP_SIZE:
            ts_sec, ts_ms, ts_us = struct.unpack("<IHH", ts_bytes)
        index += 1
```

> The per-frame `frame_size` prefix field makes IDX-free walking possible for
> H.264 files. The `00 00 00 01` Annex B start code at byte 8 of each block
> can be used as a secondary sanity check.

---

## Section 3 — Relationship to IDX File

The `.idx` file (named `<file>.seq.idx`) provides per-frame random-access metadata
that is not stored in the SEQ file itself:

| IDX field     | Description                               |
|---------------|-------------------------------------------|
| offset        | Byte position of this frame in the SEQ    |
| size          | Frame payload size in bytes               |
| ts_seconds    | Capture timestamp — whole seconds (Unix)  |
| ts_sub        | Sub-second timestamp (ms + µs packed)     |
| frame_number  | Sequential frame counter                  |

**Timestamps exist in both files.** The SEQ file embeds `(ts_sec, ts_sub)` in
the 8-byte gap after every frame (see Section 2.2). The IDX file copies these
exactly, adds `offset`, `size`, and `frame_number`, and provides the
random-access index. If the IDX is missing, StreamPix regenerates it from the
SEQ timestamps — this is why the regenerated IDX is fully accurate.

---

## Section 4 — Quick Reference

```
Bytes 0–3      : magic              = 0x0000FEED (uint32 LE)
Bytes 4–25     : name               "Norpix seq" UTF-16 LE, null-terminated
Bytes 28–31    : version            SEQ format version (= 5 for StreamPix 6–9)
Bytes 32–35    : header_size        = 1024 (int32 LE)
Bytes 36–547   : description        512-byte field; "StreamPix 9.x.x.x (x64)" UTF-16 LE
                                    at bytes 36–85; rest is null-padded
Bytes 548–551  : width              (uint32 LE)
Bytes 552–555  : height             (uint32 LE)
Bytes 556–559  : bit_depth          (uint32 LE)
Bytes 560–563  : bit_depth_real     (uint32 LE)
Bytes 564–567  : image_size         width * height * (bit_depth/8)  (uint32 LE)
Bytes 568–571  : image_format       pixel format code; 600=Mono16u, 100=Mono8 (uint32 LE)
Bytes 572–575  : allocated_frames   total frames recorded = IDX frame count
Bytes 576–579  : origin             image origin; 0=top-left
Bytes 584–591  : fps                actual capture rate (float64 LE)
Bytes 604–607  : flags              capture flags (uint32 LE)
Bytes 608–611  : bayer_pattern      Bayer pattern code (int32 LE)
Bytes 620–623  : compression_format 0=uncompressed in older StreamPix; 8 in StreamPix 9.x
Bytes 624–627  : reference_time_s   recording start whole-seconds UTC (uint32 LE)
Bytes 628–629  : reference_time_ms  recording start milliseconds      (uint16 LE)
Bytes 630–631  : reference_time_us  recording start microseconds      (uint16 LE)
Bytes 632–635  : fps_integer        fps rounded to int                 (uint32 LE)
Bytes 636–639  : exposure_ns        camera exposure time in ns         (uint32 LE)
Bytes 1024+    : frame data         H.264 variable-size blocks (compression_format = 8)
                 Frame block:       [frame_size uint32 LE][fps_int uint16 LE][frno_lo16 uint16 LE][H.264 Annex B ...]
                 After each block:  [ts_sec uint32 LE][ts_ms uint16 LE][ts_us uint16 LE]
                 block_size = IDX size field; H.264 payload = IDX size − 8 bytes
```

---

## Section 5 — Validation Notes

### Header checks
- Magic at bytes 0–3 must equal `0x0000FEED` (uint32 LE)
- `header_size` (bytes 32–35) must equal `1024`
- `image_size` (offset 564) must equal `width × height × (bit_depth / 8)`
- `fps` (offset 584) must be in a reasonable range (e.g. 1–1000)
- `reference_time_s` (offset 624) should parse as a plausible Unix timestamp
- `reference_time_ms` (offset 628, uint16) must be 0–999
- `reference_time_us` (offset 630, uint16) must be 0–999

### H.264 file checks (compression_format = 8)
- First frame block starts at byte **1024** exactly (= `header_size`)
- `IDX[n].offset + IDX[n].size + 8` must equal `IDX[n+1].offset` for all frames
- `IDX[n].offset + IDX[n].size` must not exceed the SEQ file size
- `IDX[n].size` must be > 8 (minimum: 8-byte prefix + at least 1 byte H.264)
- `frame_size` prefix field (first 4 bytes at `IDX[n].offset`) must equal `IDX[n].size`
- Byte 8 of each IDR frame block must be `00 00 00 01 67` (H.264 Annex B SPS)
- Total IDX frame count = `idx_file_size / 32`; must equal `allocated_frames` (offset 572)
- Timestamps must be monotonically non-decreasing across frames
- **Do NOT apply** the raw-stride check `(file_size − 1024) mod (image_size + 8) == 0`
  — this formula is only valid for uncompressed captures and will always fail for H.264

### Exceptions observed in 879 files
- 2 files: completely zeroed / corrupt headers (skip)
- 2 files: `compression_format = 0`, `image_format = 200` (raw 16-bit, `seq_not_for_research` folder)

---

## Section 6 — Confirmed Test Files

| File                             | Width | Height | BD | FPS       | comp_fmt | Frames (IDX) | allocated_frames | reference_time_s (624)       |
|----------------------------------|-------|--------|----|-----------|----------|--------------|------------------|------------------------------|
| `Cart_Center_2/12-04-22_...seq`  | 2048  | 1536   | 16 | 30.000000 | 8 (H.264)| 44,566       | 44,566           | 0x638C4022 = 2022-12-04 06:37:22 UTC |
| `Cart_RT_1/12-04-22_...seq`      | 2048  | 1536   | 16 | 17.787470 | 8 (H.264)| 104,734      | 104,734          | 0x638C4022 = 2022-12-04 06:37:22 UTC |
| `General_3/12-04-22_...seq`      | 2048  | 1536   | 16 | 17.886065 | 8 (H.264)| 105,316      | 105,316          | 0x638C4022 = 2022-12-04 06:37:22 UTC |

All three files: DATA_22-12-04, Case1, StreamPix 9.1.1.0 (x64).

- `reference_time_s` is per-camera (not session-wide); all three happen to share the
  same value here because they were all started within the same second.
- `allocated_frames` equals the IDX frame count exactly (confirmed for Cart_Center_2).
- Frame counts labelled "IDX" are the authoritative source; the earlier incorrect values
  (142, 336, 348) were computed using a fixed-stride raw formula and are wrong for H.264.
- `compression_format = 8` confirmed for all 873 out of 879 research SEQ files.

---

*Reference derived from direct binary inspection of live SEQ files using
`scripts/helpers/inspect_seq_frames.py`. Fields marked (unknown) require
further investigation with additional file samples or NorPix documentation.*
