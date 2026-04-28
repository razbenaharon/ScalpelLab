"""
Audit and repair NorPix .seq.idx files by rebuilding them from SEQ bodies.

Usage:
    python scripts/helpers/repair_seq_idx.py [--root PATH] [--dry-run]
                                             [--report PATH] [--workers N]
                                             [--trust-existing-size]
                                             [--tracking-file PATH]
                                             [--assume-completed-count N]

By default, existing IDX files are rebuilt and compared byte-for-byte so same-
size corruption is detected. Use --trust-existing-size only for a faster,
metadata-only pass that trusts existing IDX files when their size matches the
frame count declared in the SEQ header.

Command-line runs write an incremental checkpoint to
docs/seq_idx_repair_tracking.json by default. If a long run is interrupted,
rerun the same command to reuse completed entries whose SEQ/IDX file stats still
match.

The repair pass excludes:
  - any file under a `seq_not_for_research` directory
  - any file whose path contains a segment ending with `_JUNK`

Supported files are StreamPix 9.x style H.264 SEQ files with a sane 1024-byte
header and per-frame 8-byte timestamps stored after every frame block.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import shutil
import struct
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


_SCRIPT_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _SCRIPT_DIR.parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from config import SEQ_ROOT as DEFAULT_SEQ_ROOT  # noqa: E402


SEQ_HEADER_SIZE = 1024
SEQ_MAGIC = 0x0000FEED
SEQ_NAME = "Norpix seq"
IDX_RECORD_SIZE = 32
IDX_STRUCT = struct.Struct("<QIIIIII")
FRAME_PREFIX_STRUCT = struct.Struct("<IHH")
TIMESTAMP_STRUCT = struct.Struct("<IHH")
UINT32_MAX = 0xFFFFFFFF
UINT64_MAX = 0xFFFFFFFFFFFFFFFF
TRACKING_SCHEMA_VERSION = 1
DEFAULT_TRACKING_FILE = _PROJECT_ROOT / "docs" / "seq_idx_repair_tracking.json"
TRACKED_RESULT_STATUSES = frozenset(
    {
        "ok",
        "created",
        "replaced",
        "skipped_excluded",
        "skipped_invalid_seq",
        "skipped_unsupported",
    }
)
IDX_REQUIRED_TRACKED_STATUSES = frozenset({"ok", "created", "replaced"})
HEADER_NAME_OFFSET = 4
HEADER_NAME_LEN = 22
OFF_VERSION_MAJOR = 28
OFF_HEADER_SIZE = 32
OFF_WIDTH = 548
OFF_HEIGHT = 552
OFF_IMAGE_SIZE = 564
OFF_ALLOCATED_FRAMES = 572
OFF_FPS = 584
OFF_COMPRESSION_FMT = 620
OFF_REC_MS = 628
OFF_REC_US = 630


@dataclass(frozen=True)
class SeqHeader:
    width: int
    height: int
    image_size: int
    allocated_frames: int
    fps: float
    compression_fmt: int


@dataclass(frozen=True)
class RepairResult:
    seq_path: str
    idx_path: str
    status: str
    detail: str = ""
    frames: int = 0


def _format_duration(seconds: float | None) -> str:
    """Format a duration as a short human-readable value."""
    if seconds is None or not math.isfinite(seconds) or seconds < 0:
        return "--:--"

    rounded = int(round(seconds))
    hours, remainder = divmod(rounded, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours:d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


class ProgressBar:
    """Small dependency-free console progress bar with ETA."""

    def __init__(self, total: int, label: str = "SEQ IDX") -> None:
        self.total = total
        self.label = label
        self.start_time = time.monotonic()
        self.last_update = 0.0
        self.last_processed = -1
        self.last_line_len = 0

    def update(self, processed: int, status: str = "", force: bool = False) -> None:
        elapsed = time.monotonic() - self.start_time
        if not force and processed < self.total and elapsed - self.last_update < 0.25:
            return
        self.last_update = elapsed

        fraction = processed / self.total if self.total else 1.0
        percent = fraction * 100
        term_width = shutil.get_terminal_size(fallback=(100, 24)).columns
        bar_width = max(8, min(24, term_width - 70))
        filled = min(bar_width, int(round(bar_width * fraction)))
        bar = "#" * filled + "-" * (bar_width - filled)
        rate = processed / elapsed if elapsed > 0 else 0.0
        eta = ((self.total - processed) / rate) if rate > 0 else None

        detail = f" | {status}" if status else ""
        line = (
            f"\r{self.label}: {processed}/{self.total} {percent:5.1f}% "
            f"[{bar}] | {_format_duration(elapsed)} "
            f"| ETA {_format_duration(eta)}{detail}"
        )
        if len(line) > term_width:
            line = line[: max(1, term_width - 1)]
        padding = " " * max(0, self.last_line_len - len(line))
        print(line + padding, end="", flush=True)
        self.last_processed = processed
        self.last_line_len = len(line)

    def finish(self) -> None:
        print()
        self.last_line_len = 0


def _default_worker_count() -> int:
    """Choose a conservative default for large sequential media scans."""
    return 1


def _utc_now_iso() -> str:
    """Return a compact UTC timestamp for reports and tracking files."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _resolved_path_text(path: Path) -> str:
    """Return an absolute path string without requiring the path to exist."""
    try:
        return str(path.resolve(strict=False))
    except OSError:
        return str(path.absolute())


def _tracking_key(path: Path) -> str:
    """Normalize a path for stable JSON tracking keys."""
    return os.path.normcase(_resolved_path_text(path))


def _file_stat_payload(path: Path) -> dict[str, object]:
    """Capture the small file stat fingerprint needed for safe resume."""
    try:
        stat = path.stat()
    except FileNotFoundError:
        return {"exists": False, "size": None, "mtime_ns": None}
    except OSError as exc:
        return {"exists": False, "size": None, "mtime_ns": None, "error": str(exc)}

    return {"exists": True, "size": stat.st_size, "mtime_ns": stat.st_mtime_ns}


def _same_file_stat(left: object, right: dict[str, object]) -> bool:
    """Return whether two stat fingerprints describe the same file state."""
    if not isinstance(left, dict):
        return False
    return (
        left.get("exists") == right.get("exists")
        and left.get("size") == right.get("size")
        and left.get("mtime_ns") == right.get("mtime_ns")
    )


def _new_tracking_data() -> dict[str, Any]:
    return {
        "schema_version": TRACKING_SCHEMA_VERSION,
        "script": "scripts/helpers/repair_seq_idx.py",
        "assume_completed_count": 0,
        "description": (
            "Incremental checkpoint for SEQ IDX repair. Entries are keyed by "
            "absolute SEQ path and are updated after each file completes."
        ),
        "entries": {},
    }


def load_tracking_data(tracking_path: Path) -> dict[str, Any]:
    """Load an existing JSON tracking file, falling back to an empty ledger."""
    if not tracking_path.exists():
        return _new_tracking_data()

    try:
        with open(tracking_path, "r", encoding="utf-8") as tracking_file:
            data = json.load(tracking_file)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"[WARNING] Could not load tracking file {tracking_path}: {exc}")
        return _new_tracking_data()

    if not isinstance(data, dict):
        return _new_tracking_data()

    entries = data.get("entries")
    if not isinstance(entries, dict):
        data["entries"] = {}
    data["schema_version"] = TRACKING_SCHEMA_VERSION
    data.setdefault("script", "scripts/helpers/repair_seq_idx.py")
    data.setdefault("assume_completed_count", 0)
    data.setdefault("description", _new_tracking_data()["description"])
    return data


def _tracking_assume_completed_count(tracking_path: Path) -> int:
    """Read the default recovery prefix count from a tracking JSON file."""
    data = load_tracking_data(tracking_path)
    try:
        value = int(data.get("assume_completed_count", 0) or 0)
    except (TypeError, ValueError):
        print(
            "[WARNING] Ignoring invalid assume_completed_count in "
            f"{tracking_path}."
        )
        return 0
    if value < 0:
        print(
            "[WARNING] Ignoring negative assume_completed_count in "
            f"{tracking_path}."
        )
        return 0
    return value


def write_tracking_data(tracking_path: Path, data: dict[str, Any]) -> None:
    """Write the JSON tracking file, tolerating Windows replace hiccups."""
    tracking_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = tracking_path.with_name(f"{tracking_path.name}.tmp.{os.getpid()}")
    payload = json.dumps(data, indent=2, sort_keys=True) + "\n"
    try:
        with open(temp_path, "w", encoding="utf-8") as tracking_file:
            tracking_file.write(payload)
        try:
            os.replace(temp_path, tracking_path)
        except PermissionError:
            with open(tracking_path, "w", encoding="utf-8") as tracking_file:
                tracking_file.write(payload)
    finally:
        try:
            if temp_path.exists():
                temp_path.unlink()
        except OSError:
            pass


def is_excluded_seq_path(seq_path: Path) -> tuple[bool, str]:
    """Return whether *seq_path* is intentionally excluded from repair."""
    for part in seq_path.parts:
        lowered = part.lower()
        if lowered == "seq_not_for_research":
            return True, "excluded:seq_not_for_research"
        if lowered.endswith("_junk"):
            return True, "excluded:junk"
    return False, ""


def _decode_utf16_name(raw: bytes) -> str:
    try:
        return raw.decode("utf-16-le", errors="ignore").split("\x00")[0]
    except UnicodeDecodeError:
        return ""


def _parse_seq_header_bytes(header: bytes) -> tuple[SeqHeader | None, str]:
    if len(header) < SEQ_HEADER_SIZE:
        return None, "short_header"

    if struct.unpack_from("<I", header, 0)[0] != SEQ_MAGIC:
        return None, "bad_magic"

    name = _decode_utf16_name(
        header[HEADER_NAME_OFFSET : HEADER_NAME_OFFSET + HEADER_NAME_LEN]
    )
    version_major = struct.unpack_from("<I", header, OFF_VERSION_MAJOR)[0]
    header_size = struct.unpack_from("<I", header, OFF_HEADER_SIZE)[0]
    width = struct.unpack_from("<I", header, OFF_WIDTH)[0]
    height = struct.unpack_from("<I", header, OFF_HEIGHT)[0]
    image_size = struct.unpack_from("<I", header, OFF_IMAGE_SIZE)[0]
    allocated_frames = struct.unpack_from("<I", header, OFF_ALLOCATED_FRAMES)[0]
    fps = struct.unpack_from("<d", header, OFF_FPS)[0]
    compression_fmt = struct.unpack_from("<I", header, OFF_COMPRESSION_FMT)[0]
    rec_ms = struct.unpack_from("<H", header, OFF_REC_MS)[0]
    rec_us = struct.unpack_from("<H", header, OFF_REC_US)[0]

    if name != SEQ_NAME:
        return None, "bad_name"
    if version_major != 5:
        return None, f"unsupported_version:{version_major}"
    if header_size != SEQ_HEADER_SIZE:
        return None, f"bad_header_size:{header_size}"
    if width == 0 or height == 0:
        return None, "bad_dimensions"
    if image_size == 0:
        return None, "bad_image_size"
    if not math.isfinite(fps) or fps <= 0 or fps > 1000:
        return None, f"bad_fps:{fps}"
    if compression_fmt != 8:
        return None, f"unsupported_compression:{compression_fmt}"
    if rec_ms > 999 or rec_us > 999:
        return None, "bad_reference_timestamp"

    return SeqHeader(
        width=width,
        height=height,
        image_size=image_size,
        allocated_frames=allocated_frames,
        fps=fps,
        compression_fmt=compression_fmt,
    ), ""


def parse_seq_header(seq_path: Path) -> tuple[SeqHeader | None, str]:
    """Parse and validate the SEQ header needed for safe IDX regeneration."""
    try:
        with open(seq_path, "rb") as seq_file:
            header = seq_file.read(SEQ_HEADER_SIZE)
    except OSError as exc:
        return None, f"read_error:{exc}"

    return _parse_seq_header_bytes(header)


def unwrap_frame_numbers(low16_values: Iterable[int]) -> list[int]:
    """Lift 16-bit frame-number suffixes into a monotonic 32-bit sequence."""
    frame_numbers: list[int] = []
    prev: int | None = None

    for raw_value in low16_values:
        value = raw_value & 0xFFFF
        if prev is None:
            frame_numbers.append(value)
            prev = value
            continue

        candidate = value
        while candidate < prev:
            candidate += 1 << 16

        frame_numbers.append(candidate)
        prev = candidate

    return frame_numbers


def _require_uint_range(name: str, value: int, max_value: int) -> None:
    if value < 0 or value > max_value:
        raise ValueError(f"{name}_out_of_range:{value}")


def _pack_idx_record(
    offset: int,
    block_size: int,
    ts_seconds: int,
    ts_sub: int,
    flags: int,
    frame_number: int,
) -> bytes:
    """Pack one IDX record after validating the NorPix integer widths."""
    _require_uint_range("offset", offset, UINT64_MAX)
    _require_uint_range("block_size", block_size, UINT32_MAX)
    _require_uint_range("ts_seconds", ts_seconds, UINT32_MAX)
    _require_uint_range("ts_sub", ts_sub, UINT32_MAX)
    _require_uint_range("flags", flags, UINT32_MAX)
    _require_uint_range("frame_number", frame_number, UINT32_MAX)
    return IDX_STRUCT.pack(
        offset,
        block_size,
        ts_seconds,
        ts_sub,
        0,
        flags,
        frame_number,
    )


def regenerate_idx_bytes(seq_path: Path) -> tuple[bytes | None, int, str]:
    """
    Rebuild IDX bytes from a supported H.264 SEQ file.

    Returns (idx_bytes, frame_count, reason). idx_bytes is None on failure.
    """
    idx_bytes = bytearray()
    frame_count = 0
    previous_frame_number: int | None = None

    try:
        seq_file = open(seq_path, "rb")
    except OSError as exc:
        return None, 0, f"read_error:{exc}"

    try:
        with seq_file:
            header, reason = _parse_seq_header_bytes(seq_file.read(SEQ_HEADER_SIZE))
            if header is None:
                return None, 0, reason

            file_size = seq_file.seek(0, os.SEEK_END)
            seq_file.seek(SEQ_HEADER_SIZE)
            offset = SEQ_HEADER_SIZE
            while True:
                if offset == file_size:
                    break

                prefix = seq_file.read(FRAME_PREFIX_STRUCT.size)
                if len(prefix) == 0:
                    break
                if len(prefix) != FRAME_PREFIX_STRUCT.size:
                    return None, frame_count, f"short_frame_prefix@{offset}"

                block_size, flags, frame_no_lo16 = FRAME_PREFIX_STRUCT.unpack(prefix)
                if block_size <= FRAME_PREFIX_STRUCT.size:
                    return None, frame_count, f"bad_block_size:{block_size}@{offset}"

                end_of_block = offset + block_size
                end_of_timestamp = end_of_block + TIMESTAMP_STRUCT.size
                if end_of_timestamp > file_size:
                    return None, frame_count, f"truncated_frame@{offset}"

                seq_file.seek(block_size - FRAME_PREFIX_STRUCT.size, os.SEEK_CUR)
                timestamp = seq_file.read(TIMESTAMP_STRUCT.size)
                if len(timestamp) != TIMESTAMP_STRUCT.size:
                    return None, frame_count, f"short_timestamp@{offset}"

                ts_seconds, ts_ms, ts_us = TIMESTAMP_STRUCT.unpack(timestamp)
                if ts_ms > 999 or ts_us > 999:
                    return None, frame_count, f"bad_timestamp@{offset}"

                ts_sub = ts_ms | (ts_us << 16)
                frame_number = frame_no_lo16 & 0xFFFF
                if previous_frame_number is not None:
                    while frame_number < previous_frame_number:
                        frame_number += 1 << 16
                previous_frame_number = frame_number

                try:
                    idx_bytes.extend(
                        _pack_idx_record(
                            offset,
                            block_size,
                            ts_seconds,
                            ts_sub,
                            flags,
                            frame_number,
                        )
                    )
                except ValueError as exc:
                    return None, frame_count, f"idx_pack_error:{exc}@{offset}"
                frame_count += 1
                offset = end_of_timestamp

    except OSError as exc:
        return None, frame_count, f"io_error:{exc}"

    if header.allocated_frames not in (0, frame_count):
        return None, frame_count, (
            f"allocated_frames_mismatch:{header.allocated_frames}!={frame_count}"
        )

    return bytes(idx_bytes), frame_count, ""


def _classify_existing_idx(idx_path: Path, regenerated: bytes) -> str:
    try:
        existing_size = idx_path.stat().st_size
    except FileNotFoundError:
        return "create"
    except OSError:
        return "replace"

    if existing_size != len(regenerated):
        return "replace"

    try:
        existing = idx_path.read_bytes()
    except OSError:
        return "replace"

    if existing == regenerated:
        return "ok"
    return "replace"


def _expected_idx_size_from_header(header: SeqHeader) -> int | None:
    if header.allocated_frames <= 0:
        return None
    return header.allocated_frames * IDX_RECORD_SIZE


def _quick_existing_idx_result(seq_path: Path, idx_path: Path) -> RepairResult | None:
    """
    Return a fast OK/skip result when an existing IDX looks complete.

    This deliberately checks only cheap metadata: the SEQ header frame count and
    the IDX byte size. The default repair path does a full byte-for-byte
    verification instead.
    """
    header, reason = parse_seq_header(seq_path)
    if header is None:
        status = (
            "skipped_invalid_seq"
            if reason in {"bad_magic", "short_header"}
            else "skipped_unsupported"
        )
        return RepairResult(str(seq_path), str(idx_path), status, reason)

    expected_size = _expected_idx_size_from_header(header)
    if expected_size is None:
        return None

    try:
        existing_size = idx_path.stat().st_size
    except FileNotFoundError:
        return None
    except OSError:
        return None

    if existing_size == expected_size:
        return RepairResult(
            str(seq_path),
            str(idx_path),
            "ok",
            "fast_existing_idx",
            header.allocated_frames,
        )
    return None


def _assume_completed_result(seq_path: Path) -> RepairResult | None:
    """
    Build a tracking result for a file completed before tracking existed.

    This is intentionally conservative: repairable SEQ files are assumed only
    when the current companion IDX exists and its byte size matches the SEQ
    header frame count.
    """
    idx_path = Path(str(seq_path) + ".idx")
    excluded, reason = is_excluded_seq_path(seq_path)
    if excluded:
        return RepairResult(str(seq_path), str(idx_path), "skipped_excluded", reason)

    header, reason = parse_seq_header(seq_path)
    if header is None:
        status = (
            "skipped_invalid_seq"
            if reason in {"bad_magic", "short_header"}
            else "skipped_unsupported"
        )
        return RepairResult(str(seq_path), str(idx_path), status, reason)

    expected_size = _expected_idx_size_from_header(header)
    if expected_size is None:
        return None

    try:
        existing_size = idx_path.stat().st_size
    except (FileNotFoundError, OSError):
        return None

    if existing_size != expected_size:
        return None

    return RepairResult(
        str(seq_path),
        str(idx_path),
        "ok",
        "assumed_previous_run_completed",
        header.allocated_frames,
    )


def write_idx_atomically(idx_path: Path, payload: bytes) -> None:
    """Write IDX bytes via a same-directory temp file, then replace in place."""
    temp_path = idx_path.with_name(f"{idx_path.name}.tmp.{os.getpid()}")
    try:
        with open(temp_path, "wb") as tmp_file:
            tmp_file.write(payload)
        os.replace(temp_path, idx_path)
    finally:
        try:
            if temp_path.exists():
                temp_path.unlink()
        except OSError:
            pass


def repair_seq_file(
    seq_path: Path, dry_run: bool = False, verify_existing: bool = True
) -> RepairResult:
    """Audit one SEQ file and create/replace its IDX companion when needed."""
    excluded, reason = is_excluded_seq_path(seq_path)
    idx_path = Path(str(seq_path) + ".idx")
    if excluded:
        return RepairResult(str(seq_path), str(idx_path), "skipped_excluded", reason)

    if not verify_existing:
        quick_result = _quick_existing_idx_result(seq_path, idx_path)
        if quick_result is not None:
            return quick_result

    regenerated, frame_count, reason = regenerate_idx_bytes(seq_path)
    if regenerated is None:
        status = "skipped_invalid_seq" if reason in {"bad_magic", "short_header"} else "skipped_unsupported"
        return RepairResult(str(seq_path), str(idx_path), status, reason, frame_count)

    action = _classify_existing_idx(idx_path, regenerated)
    if action == "ok":
        return RepairResult(str(seq_path), str(idx_path), "ok", "", frame_count)

    if not dry_run:
        write_idx_atomically(idx_path, regenerated)

    status = "created" if action == "create" else "replaced"
    return RepairResult(str(seq_path), str(idx_path), status, "", frame_count)


class RepairTracker:
    """Incremental JSON checkpoint for long SEQ IDX repair passes."""

    def __init__(
        self,
        tracking_path: Path,
        root: Path,
        dry_run: bool,
        verify_existing: bool,
        ignore_existing: bool = False,
    ) -> None:
        self.path = tracking_path
        self.root = root
        self.dry_run = dry_run
        self.verify_existing = verify_existing
        self.ignore_existing = ignore_existing
        self.data = load_tracking_data(tracking_path)
        self.data["schema_version"] = TRACKING_SCHEMA_VERSION
        self.data["script"] = "scripts/helpers/repair_seq_idx.py"
        self.data["last_started_at"] = _utc_now_iso()
        self.data["last_root"] = _resolved_path_text(root)
        self.data["last_mode"] = {
            "dry_run": dry_run,
            "verify_existing": verify_existing,
        }
        self.data.setdefault("entries", {})
        self.loaded_entries = len(self.data["entries"])

    def completed_result(self, seq_path: Path) -> RepairResult | None:
        """Return a valid previously completed result for *seq_path*, if any."""
        if self.ignore_existing:
            return None

        entry = self.data["entries"].get(_tracking_key(seq_path))
        if not isinstance(entry, dict):
            return None

        status = entry.get("status")
        if status not in TRACKED_RESULT_STATUSES:
            return None
        if entry.get("dry_run") != self.dry_run:
            return None
        if entry.get("verify_existing") != self.verify_existing:
            return None
        if not _same_file_stat(entry.get("seq_stat"), _file_stat_payload(seq_path)):
            return None

        idx_path = Path(str(seq_path) + ".idx")
        idx_required = status == "ok" or (
            status in IDX_REQUIRED_TRACKED_STATUSES and not self.dry_run
        )
        if idx_required and not _same_file_stat(
            entry.get("idx_stat"), _file_stat_payload(idx_path)
        ):
            return None

        try:
            frames = int(entry.get("frames", 0) or 0)
        except (TypeError, ValueError):
            frames = 0

        return RepairResult(
            str(seq_path),
            str(idx_path),
            str(status),
            str(entry.get("detail", "") or ""),
            frames,
        )

    def record(self, result: RepairResult) -> None:
        """Record one completed result and flush it to disk immediately."""
        seq_path = Path(result.seq_path)
        idx_path = Path(result.idx_path)
        entries = self.data["entries"]
        entries[_tracking_key(seq_path)] = {
            "seq_path": result.seq_path,
            "idx_path": result.idx_path,
            "status": result.status,
            "detail": result.detail,
            "frames": result.frames,
            "dry_run": self.dry_run,
            "verify_existing": self.verify_existing,
            "finished_at": _utc_now_iso(),
            "seq_stat": _file_stat_payload(seq_path),
            "idx_stat": _file_stat_payload(idx_path),
        }
        self.data["last_updated_at"] = _utc_now_iso()
        self.data["entry_count"] = len(entries)
        write_tracking_data(self.path, self.data)


def scan_and_repair(
    root: Path,
    dry_run: bool = False,
    workers: int = 1,
    verify_existing: bool = True,
    tracking_file: Path | None = None,
    ignore_tracking: bool = False,
    assume_completed_count: int = 0,
) -> list[RepairResult]:
    """Run the repair pass recursively under *root*."""
    print(f"[INFO] Scanning for SEQ files under {root}...")
    seq_paths = sorted(root.rglob("*.seq"))
    total = len(seq_paths)
    print(f"[INFO] Found {total} SEQ files")
    if total == 0:
        return []

    worker_count = min(max(1, workers), total)
    label = "SEQ IDX"
    if worker_count > 1:
        label = f"{label} ({worker_count} workers)"

    tracker: RepairTracker | None = None
    if tracking_file is not None:
        tracker = RepairTracker(
            tracking_file,
            root,
            dry_run=dry_run,
            verify_existing=verify_existing,
            ignore_existing=ignore_tracking,
        )
        print(
            f"[INFO] Tracking per-file progress in {tracking_file} "
            f"({tracker.loaded_entries} prior entries)."
        )
        if ignore_tracking:
            print("[INFO] Existing tracking entries will be refreshed, not reused.")
        if assume_completed_count > 0:
            print(
                "[INFO] First "
                f"{assume_completed_count} sorted SEQ files may be checkpointed "
                "from existing IDX metadata."
            )

    ordered_results: list[RepairResult | None] = [None] * total
    pending: list[tuple[int, Path]] = []
    progress = ProgressBar(total, label=label)
    progress.update(0, force=True)
    processed_count = 0
    assume_limit = min(max(0, assume_completed_count), total)

    def record_result(result: RepairResult) -> None:
        nonlocal tracker
        if tracker is None:
            return
        try:
            tracker.record(result)
        except OSError as exc:
            print(f"\n[WARNING] Could not update tracking file {tracker.path}: {exc}")
            tracker = None

    try:
        for index, seq_path in enumerate(seq_paths):
            tracked_result = tracker.completed_result(seq_path) if tracker else None
            if tracked_result is None:
                if tracker is not None and index < assume_limit:
                    tracked_result = _assume_completed_result(seq_path)
                    if tracked_result is not None:
                        record_result(tracked_result)
                        ordered_results[index] = tracked_result
                        processed_count += 1
                        progress.update(processed_count, "assumed")
                        continue
                pending.append((index, seq_path))
                continue
            ordered_results[index] = tracked_result
            processed_count += 1
            progress.update(processed_count, "tracked")

        if worker_count == 1:
            for index, seq_path in pending:
                result = repair_seq_file(
                    seq_path,
                    dry_run=dry_run,
                    verify_existing=verify_existing,
                )
                ordered_results[index] = result
                record_result(result)
                processed_count += 1
                progress.update(processed_count, result.status)
        elif pending:
            pending_worker_count = min(worker_count, len(pending))
            with ThreadPoolExecutor(max_workers=pending_worker_count) as executor:
                futures = {
                    executor.submit(
                        repair_seq_file,
                        seq_path,
                        dry_run,
                        verify_existing,
                    ): index
                    for index, seq_path in pending
                }
                for future in as_completed(futures):
                    index = futures[future]
                    result = future.result()
                    ordered_results[index] = result
                    record_result(result)
                    processed_count += 1
                    progress.update(processed_count, result.status)
    finally:
        if progress.last_processed != processed_count:
            progress.update(processed_count, force=True)
        progress.finish()
    return [result for result in ordered_results if result is not None]


def build_summary(results: Iterable[RepairResult]) -> dict[str, object]:
    """Aggregate per-file results into a compact summary object."""
    results_list = list(results)
    counts: dict[str, int] = {}
    for result in results_list:
        counts[result.status] = counts.get(result.status, 0) + 1

    return {
        "counts": counts,
        "results": [asdict(result) for result in results_list],
    }


def write_report(report_path: Path, summary: dict[str, object]) -> None:
    """Write a JSON or CSV report based on the requested file extension."""
    results = summary["results"]
    if report_path.suffix.lower() == ".csv":
        with open(report_path, "w", newline="", encoding="utf-8") as report_file:
            writer = csv.DictWriter(
                report_file,
                fieldnames=["seq_path", "idx_path", "status", "detail", "frames"],
            )
            writer.writeheader()
            writer.writerows(results)
        return

    with open(report_path, "w", encoding="utf-8") as report_file:
        json.dump(summary, report_file, indent=2)


def print_summary(summary: dict[str, object], dry_run: bool = False) -> None:
    """Emit a readable console summary with affected file lists."""
    counts = summary["counts"]
    results = summary["results"]
    mode = "DRY RUN" if dry_run else "REPAIR"
    print(f"SEQ IDX {mode} SUMMARY")
    for status in sorted(counts):
        print(f"  {status}: {counts[status]}")

    fast_ok = sum(
        1
        for result in results
        if result["status"] == "ok" and result["detail"] == "fast_existing_idx"
    )
    if fast_ok:
        print(f"  note: {fast_ok} existing IDX files were accepted by fast size check")

    for result in results:
        if result["status"] == "ok":
            continue
        detail = f" ({result['detail']})" if result["detail"] else ""
        print(f"[{result['status']}] {result['seq_path']}{detail}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Audit and repair NorPix .seq.idx files from SEQ bodies."
    )
    parser.add_argument(
        "--root",
        default=DEFAULT_SEQ_ROOT,
        help="Root directory to scan. Defaults to config.SEQ_ROOT.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Audit only; do not create or replace any .seq.idx files.",
    )
    parser.add_argument(
        "--report",
        help="Optional output path for a JSON or CSV report.",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=_default_worker_count(),
        help=(
            "Number of SEQ files to repair in parallel. "
            "Use 1 for serial mode. Defaults to %(default)s."
        ),
    )
    parser.add_argument(
        "--verify-existing",
        action="store_true",
        help=(
            "Default behavior; rebuild and byte-compare existing .seq.idx files."
        ),
    )
    parser.add_argument(
        "--trust-existing-size",
        action="store_true",
        help=(
            "Fast mode: trust existing .seq.idx files when their size matches "
            "the SEQ header frame count."
        ),
    )
    parser.add_argument(
        "--tracking-file",
        default=str(DEFAULT_TRACKING_FILE),
        help=(
            "Incremental JSON checkpoint path. Defaults to "
            "docs/seq_idx_repair_tracking.json."
        ),
    )
    parser.add_argument(
        "--no-tracking",
        action="store_true",
        help="Do not write or reuse an incremental tracking JSON file.",
    )
    parser.add_argument(
        "--ignore-tracking",
        action="store_true",
        help="Refresh the tracking file without reusing completed entries.",
    )
    parser.add_argument(
        "--assume-completed-count",
        type=int,
        default=None,
        help=(
            "Recovery aid for an interrupted pre-tracking run: checkpoint the "
            "first N sorted SEQ files from existing IDX metadata, then process "
            "the rest normally. When omitted, the value is read from the "
            "tracking JSON's assume_completed_count field."
        ),
    )
    args = parser.parse_args(argv)

    root = Path(args.root)
    if not root.exists():
        print(f"[ERROR] Root not found: {root}")
        return 2
    if args.workers < 1:
        print("[ERROR] --workers must be 1 or greater")
        return 2
    if args.assume_completed_count is not None and args.assume_completed_count < 0:
        print("[ERROR] --assume-completed-count must be 0 or greater")
        return 2

    verify_existing = not args.trust_existing_size or args.verify_existing
    if verify_existing:
        print("[INFO] Existing IDX files will be rebuilt and byte-compared.")
    else:
        print("[INFO] Fast mode: existing IDX files may be accepted by size check.")

    tracking_file = None if args.no_tracking else Path(args.tracking_file)
    assume_completed_count = args.assume_completed_count
    if assume_completed_count is None:
        assume_completed_count = (
            _tracking_assume_completed_count(tracking_file)
            if tracking_file is not None
            else 0
        )
    results = scan_and_repair(
        root,
        dry_run=args.dry_run,
        workers=args.workers,
        verify_existing=verify_existing,
        tracking_file=tracking_file,
        ignore_tracking=args.ignore_tracking,
        assume_completed_count=assume_completed_count,
    )
    summary = build_summary(results)
    print_summary(summary, dry_run=args.dry_run)

    if args.report:
        write_report(Path(args.report), summary)

    counts = summary["counts"]
    failures = counts.get("skipped_invalid_seq", 0) + counts.get("skipped_unsupported", 0)
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
