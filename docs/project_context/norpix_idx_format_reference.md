# NorPix StreamPix IDX File Format Reference

## Overview
Binary index file (.idx) accompanying NorPix StreamPix sequence files (.seq). Contains one fixed-size record per video frame, enabling direct random access into the SEQ file. No file header — records start at byte 0.

## Record Layout (32 bytes, Little Endian)

| Offset | Size | Type   | Field          | Description |
|--------|------|--------|----------------|-------------|
| 0      | 8    | uint64 | offset         | Byte position of frame data within the .seq file |
| 8      | 4    | uint32 | size           | Frame data size in bytes (JPEG/BMP/RAW payload) |
| 12     | 4    | uint32 | ts_seconds     | Capture time — whole seconds (Unix epoch UTC) |
| 16     | 4    | uint32 | ts_sub         | Capture time — sub-second, packed (see below) |
| 20     | 4    | uint32 | reserved       | Always 0 |
| 24     | 4    | uint32 | flags          | First frame may contain nominal FPS; otherwise 0 |
| 28     | 4    | uint32 | frame_number   | Sequential frame counter (may not start at 0) |

## Sub-Second Timestamp Encoding (ts_sub)

The `ts_sub` field packs two values into 32 bits:

```
bits 15-0  (low word):  milliseconds within the second (0–999)
bits 31-16 (high word): microseconds within the millisecond (0–999)
```

Decoding:
```
ms = ts_sub & 0xFFFF
us = (ts_sub >> 16) & 0xFFFF
full_timestamp = ts_seconds + ms / 1000.0 + us / 1000000.0
```

## SEQ File Relationship

Frames in the .seq file are preceded by a 1024-byte file header and separated by 8-byte inter-frame padding:

```
offset[n+1] = offset[n] + size[n] + 8
```

## Validation Rules

- `offset + size` must not exceed the .seq file size
- Timestamps must be monotonically increasing when decoded correctly
- `size > 0` for all valid frames
- `frame_number` increments by 1 per record (gaps indicate dropped frames at source)
- Total frame count = idx_file_size / 32
