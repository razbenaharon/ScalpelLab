# NorPix StreamPix SEQ File Format Reference

## Overview

Binary video container produced by **NorPix StreamPix 9.x (x64)**.
One `.seq` file holds all frames for a single camera channel in a single recording session.
An accompanying `.idx` file provides a per-frame byte-offset index; see `norpix_idx_format_reference.md`.

All multi-byte integers are **little-endian** unless stated otherwise.

---

## File Layout

```
[  1024 bytes ] File Header
[  frame_size ] Frame 0 data
[     8 bytes ] Inter-frame padding
[  frame_size ] Frame 1 data
[     8 bytes ] Inter-frame padding
       ...
[  frame_size ] Frame N data
[     8 bytes ] Inter-frame padding   (may be absent at EOF)
```

### Offset formula
```
frame_offset[0]   = 1024
frame_offset[n+1] = frame_offset[n] + frame_size + 8
```

`frame_size` is constant for raw/BMP captures (read from header offset 564).
For compressed formats (JPEG, H.264) it is variable; the IDX file carries the
per-frame size in that case.

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

### 1.2 Description block (bytes 36–91)

| Offset | Size | Type      | Field       | Value observed                       |
|--------|------|-----------|-------------|--------------------------------------|
| 36     | 50   | UTF-16 LE | description | `"StreamPix 9.1.1.0 (x64)\0"`        |

The description string is null-terminated and padded to fill the region.
It encodes the recorder application name and build version.

### 1.3 Padding / unknown region (bytes 92–547)

This region is largely zeroed but contains several non-zero fields whose
semantics are not fully documented. Some values appear to be runtime memory
artifacts (64-bit pointer-sized values) written by the recorder process.
Notable confirmed non-zero clusters:

| Offset | Size | Observed value | Notes                                         |
|--------|------|----------------|-----------------------------------------------|
| 92     | 4    | `0x34A8C3C0`   | Unknown; constant within a session            |
| 124    | 4    | `8`            | Unknown; possibly byte alignment or block size |
| 144    | 4    | `1`            | Unknown                                       |
| 148    | 8    | `0xFFFFFFFFFFFFFFFF` | Sentinel / no-limit marker              |

**Do not rely on fields in this region.**

### 1.4 Image geometry block (bytes 548–583)

All fields are `uint32 LE`.

| Offset | Field              | Description                                              |
|--------|--------------------|----------------------------------------------------------|
| 548    | **width**          | Frame width in pixels (e.g. `2048`)                      |
| 552    | **height**         | Frame height in pixels (e.g. `1536`)                     |
| 556    | **bit_depth**      | Bits per colour component (e.g. `16` for 16-bit mono)    |
| 560    | **bit_depth_real** | Actual significant bits; often equal to `bit_depth`      |
| 564    | **image_size**     | Bytes per raw frame = `width × height × (bit_depth / 8)` |
| 568    | *(unknown)*        | Observed `600`; possibly max-allocated frame count       |
| 572    | *(unknown)*        | Observed `44566`; purpose unclear                        |
| 576    | *(unknown)*        | Observed `0`                                             |
| 580    | **format_code**    | Compression codec (see codec table below)                |

#### Codec codes

| Code | Codec                       |
|------|-----------------------------|
| 0    | Raw / BMP (uncompressed)    |
| 100  | JPEG                        |
| 101  | PNG                         |
| 102  | TIFF                        |
| 200  | H.264 (AVC) — Annex B      |
| 201  | H.265 (HEVC)                |

### 1.5 Timing block (bytes 584–639)

| Offset | Size | Type    | Field           | Description                                                |
|--------|------|---------|-----------------|------------------------------------------------------------|
| 584    | 8    | float64 | **fps**         | Actual capture frame rate (e.g. `30.0`, `17.79`, `17.89`) |
| 592    | 4    | uint32  | origin_x        | Crop/ROI origin X; `0` for full-frame captures             |
| 596    | 4    | uint32  | origin_y        | Crop/ROI origin Y; `0` for full-frame captures             |
| 600    | 4    | uint32  | *(unknown)*     | Observed `0`                                               |
| 604    | 4    | uint32  | *(unknown)*     | Observed `18`                                              |
| 608    | 4    | uint32  | *(unknown)*     | Observed `1`                                               |
| 612    | 8    | ?       | *(unknown)*     | Observed all-zero                                          |
| 620    | 4    | uint32  | *(unknown)*     | Observed `8`                                               |
| 624    | 8    | bytes   | *(unknown)*     | Opaque 8-byte block                                        |
| 632    | 4    | uint32  | fps_integer     | FPS rounded to nearest integer (e.g. `30`); copy of fps    |
| 636    | 4    | uint32  | *(unknown)*     | Observed `5000000`; possibly exposure or timer period      |

> **FPS note**: `fps` at offset 584 is the most reliable source for frame
> rate. It reflects the measured capture rate and is not necessarily a
> round number (e.g. `17.787470156647668`).

### 1.6 Remainder (bytes 640–1023)

Mostly zeroed with sparse non-zero values. Contains at minimum:

| Offset | Size | Observed | Notes                  |
|--------|------|----------|------------------------|
| 656    | 4    | `1`      | Unknown                |
| 660    | 4    | `8192`   | Unknown                |
| 664    | 4    | `44567`  | Unknown (cf. 572 + 1?) |
| 672    | 8    | `0xFFFFFFFFFFFFFFFF` | Sentinel   |

---

## Section 2 — Frame Data Body (bytes 1024–EOF)

### 2.1 Frame data

Frames are stored contiguously after the 1024-byte header with a fixed
8-byte gap between every pair of adjacent frames.

**Raw/BMP format** (format_code = 0):
- Every frame is exactly `image_size` bytes
- Pixel layout: row-major, top-to-bottom
- For 16-bit mono: 2 bytes per pixel, little-endian uint16
- No per-frame header or marker bytes

**JPEG format** (format_code = 100):
- Variable-length frames; starts with `FF D8` (JPEG SOI) and ends with `FF D9` (EOI)
- Frame size varies per frame; use IDX offsets for random access

**H.264 format** (format_code = 200):
- Variable-length frames; Annex B encapsulation (`00 00 00 01` start codes)
- Frame types: IDR (I-frame, NAL type 5), P/B-slice (NAL type 1)
- SPS (NAL type 7) and PPS (NAL type 8) may appear only in the first frame
- Frame size varies; use IDX offsets for random access

### 2.2 Inter-frame padding (8 bytes)

Exactly 8 bytes of opaque data appear between every frame:

```
[frame_data]  [8 padding bytes]  [next frame_data]  [8 padding bytes] ...
```

The padding content is **not timestamps** — it varies randomly between frames
and does not conform to any known timestamp encoding (Unix epoch, Windows
FILETIME, etc.). It may be a checksum, a memory fence written by the recorder,
or uninitialized data. **Do not parse it.**

### 2.3 Walk algorithm (no IDX)

For **fixed-size** formats (raw/BMP):

```python
SEQ_HEADER_SIZE  = 1024
INTER_FRAME_PAD  = 8

frame_size = image_size   # from header offset 564
offset     = SEQ_HEADER_SIZE
index      = 0

while offset + frame_size <= file_size:
    # process frame at [offset : offset + frame_size]
    offset += frame_size + INTER_FRAME_PAD
    index  += 1
```

For **variable-size** formats (JPEG / H.264):
- Without an IDX, scan for codec-specific start markers (see Section 2.1)
- With an IDX, use `offset` and `size` fields directly (preferred)

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

**Timestamps are only in the IDX file.** The SEQ file body has no per-frame
timestamps; the 8-byte inter-frame padding is not a timestamp.

---

## Section 4 — Quick Reference

```
Bytes 0–3      : magic         = 0x0000FEED (uint32 LE)
Bytes 4–25     : "Norpix seq"  UTF-16 LE, null-terminated
Bytes 32–35    : header_size   = 1024 (uint32 LE)
Bytes 36–85    : description   "StreamPix 9.x.x.x (x64)" UTF-16 LE
Bytes 548–551  : width         (uint32 LE)
Bytes 552–555  : height        (uint32 LE)
Bytes 556–559  : bit_depth     (uint32 LE)
Bytes 560–563  : bit_depth_real(uint32 LE)
Bytes 564–567  : image_size    = width * height * (bit_depth/8)  (uint32 LE)
Bytes 580–583  : format_code   0=raw, 100=JPEG, 200=H.264        (uint32 LE)
Bytes 584–591  : fps           actual capture rate                (float64 LE)
Bytes 592–595  : origin_x      (uint32 LE)
Bytes 596–599  : origin_y      (uint32 LE)
Bytes 632–635  : fps_integer   fps rounded to int                 (uint32 LE)
Bytes 1024+    : frame data    stride = image_size + 8 (raw/BMP)
```

---

## Section 5 — Validation Notes

- First frame always starts at byte **1024** exactly
- `image_size` must equal `width × height × (bit_depth / 8)` for raw formats
- `frame_offset[n+1] − frame_offset[n]` must equal `image_size + 8` for all raw frames
- `(file_size − 1024) mod (image_size + 8)` should be **0** for a clean raw file
  (a non-zero remainder indicates a partial final frame or trailing data)
- The IDX frame count equals `(idx_file_size / 32)` and should match
  `(seq_file_size − 1024) / (image_size + 8)` for raw captures

---

## Section 6 — Confirmed Test Files

| File                             | Width | Height | BD | FPS       | Format | Frames (est.) |
|----------------------------------|-------|--------|----|-----------|--------|---------------|
| `Cart_Center_2/12-04-22_...seq`  | 2048  | 1536   | 16 | 30.000000 | 0 (raw)| 142           |
| `Cart_RT_1/12-04-22_...seq`      | 2048  | 1536   | 16 | 17.787470 | 0 (raw)| 336           |
| `General_3/12-04-22_...seq`      | 2048  | 1536   | 16 | 17.886065 | 0 (raw)| 348           |

All three files: DATA_22-12-04, Case1, StreamPix 9.1.1.0 (x64).

---

*Reference derived from direct binary inspection of live SEQ files using
`scripts/helpers/inspect_seq_frames.py`. Fields marked (unknown) require
further investigation with additional file samples or NorPix documentation.*
