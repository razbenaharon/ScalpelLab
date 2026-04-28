from __future__ import annotations

import json
import struct
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
HELPERS_DIR = PROJECT_ROOT / "scripts" / "helpers"
sys.path.insert(0, str(HELPERS_DIR))

import repair_seq_idx as repair  # noqa: E402


def build_seq_header(
    *,
    version_major: int = 5,
    header_size: int = repair.SEQ_HEADER_SIZE,
    width: int = 2048,
    height: int = 1536,
    image_size: int = 2048 * 1536 * 2,
    allocated_frames: int = 0,
    fps: float = 30.0,
    compression_fmt: int = 8,
    valid_magic: bool = True,
) -> bytes:
    header = bytearray(repair.SEQ_HEADER_SIZE)
    struct.pack_into("<I", header, 0, repair.SEQ_MAGIC if valid_magic else 0)
    header[repair.HEADER_NAME_OFFSET : repair.HEADER_NAME_OFFSET + repair.HEADER_NAME_LEN] = (
        "Norpix seq\x00".encode("utf-16-le")
    )
    struct.pack_into("<I", header, repair.OFF_VERSION_MAJOR, version_major)
    struct.pack_into("<I", header, repair.OFF_HEADER_SIZE, header_size)
    struct.pack_into("<I", header, repair.OFF_WIDTH, width)
    struct.pack_into("<I", header, repair.OFF_HEIGHT, height)
    struct.pack_into("<I", header, repair.OFF_IMAGE_SIZE, image_size)
    struct.pack_into("<I", header, repair.OFF_ALLOCATED_FRAMES, allocated_frames)
    struct.pack_into("<d", header, repair.OFF_FPS, fps)
    struct.pack_into("<I", header, repair.OFF_COMPRESSION_FMT, compression_fmt)
    struct.pack_into("<H", header, repair.OFF_REC_MS, 123)
    struct.pack_into("<H", header, repair.OFF_REC_US, 456)
    return bytes(header)


def pack_ts_sub(milliseconds: int, microseconds: int) -> int:
    return milliseconds | (microseconds << 16)


def build_seq_file(
    seq_path: Path,
    frames: list[dict[str, int | bytes]],
    *,
    allocated_frames: int | None = None,
    header_kwargs: dict | None = None,
) -> bytes:
    header_kwargs = dict(header_kwargs or {})
    allocated = len(frames) if allocated_frames is None else allocated_frames
    header = build_seq_header(allocated_frames=allocated, **header_kwargs)
    expected_idx = bytearray()
    body = bytearray()
    offset = repair.SEQ_HEADER_SIZE

    for frame in frames:
        payload = frame["payload"]
        size = len(payload) + repair.FRAME_PREFIX_STRUCT.size
        body += repair.FRAME_PREFIX_STRUCT.pack(size, frame["flags"], frame["frame_no_lo16"])
        body += payload
        body += repair.TIMESTAMP_STRUCT.pack(
            frame["ts_seconds"], frame["ts_ms"], frame["ts_us"]
        )
        expected_idx += repair.IDX_STRUCT.pack(
            offset,
            size,
            frame["ts_seconds"],
            pack_ts_sub(frame["ts_ms"], frame["ts_us"]),
            0,
            frame["flags"],
            0,  # filled below when we know the unwrapped sequence
        )
        offset += size + repair.TIMESTAMP_STRUCT.size

    frame_numbers = repair.unwrap_frame_numbers(
        frame["frame_no_lo16"] for frame in frames
    )
    for i, frame_number in enumerate(frame_numbers):
        struct.pack_into("<I", expected_idx, i * repair.IDX_RECORD_SIZE + 28, frame_number)

    seq_path.parent.mkdir(parents=True, exist_ok=True)
    seq_path.write_bytes(header + body)
    return bytes(expected_idx)


class RepairSeqIdxTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_unwrap_frame_numbers_handles_nonzero_start_and_wrap(self) -> None:
        values = [29169, 29170, 65535, 0, 2]
        self.assertEqual(
            repair.unwrap_frame_numbers(values),
            [29169, 29170, 65535, 65536, 65538],
        )

    def test_regenerate_idx_bytes_matches_expected_records(self) -> None:
        seq_path = self.root / "DATA_25-07-22" / "Case1" / "Patient_Monitor" / "sample.seq"
        expected = build_seq_file(
            seq_path,
            [
                {
                    "payload": b"\x00\x00\x00\x01\x67\x64\x00\x32",
                    "flags": 30,
                    "frame_no_lo16": 29169,
                    "ts_seconds": 1700000000,
                    "ts_ms": 123,
                    "ts_us": 456,
                },
                {
                    "payload": b"\x00\x00\x00\x01\x41\x9a\x22",
                    "flags": 0,
                    "frame_no_lo16": 29170,
                    "ts_seconds": 1700000000,
                    "ts_ms": 156,
                    "ts_us": 789,
                },
            ],
        )

        actual, frame_count, reason = repair.regenerate_idx_bytes(seq_path)

        self.assertEqual(reason, "")
        self.assertEqual(frame_count, 2)
        self.assertEqual(actual, expected)

    def test_regenerate_idx_bytes_reports_unrepresentable_idx_record(self) -> None:
        seq_path = self.root / "DATA_25-07-22" / "Case1" / "Patient_Monitor" / "overflow.seq"
        build_seq_file(
            seq_path,
            [
                {
                    "payload": b"\x00\x00\x00\x01\x67",
                    "flags": 0,
                    "frame_no_lo16": 65535,
                    "ts_seconds": 1,
                    "ts_ms": 1,
                    "ts_us": 0,
                },
                {
                    "payload": b"\x00\x00\x00\x01\x41",
                    "flags": 0,
                    "frame_no_lo16": 0,
                    "ts_seconds": 1,
                    "ts_ms": 2,
                    "ts_us": 0,
                },
                {
                    "payload": b"\x00\x00\x00\x01\x41",
                    "flags": 0,
                    "frame_no_lo16": 65535,
                    "ts_seconds": 1,
                    "ts_ms": 3,
                    "ts_us": 0,
                },
            ],
        )

        with mock.patch.object(repair, "UINT32_MAX", 65536):
            actual, frame_count, reason = repair.regenerate_idx_bytes(seq_path)

        self.assertIsNone(actual)
        self.assertEqual(frame_count, 2)
        self.assertIn("idx_pack_error:frame_number_out_of_range", reason)

    def test_scan_and_repair_creates_missing_idx(self) -> None:
        seq_path = self.root / "DATA_25-08-20" / "Case1" / "Ventilator_Monitor" / "missing.seq"
        expected = build_seq_file(
            seq_path,
            [
                {
                    "payload": b"\x00\x00\x00\x01\x67\x64\x00\x32\x01",
                    "flags": 30,
                    "frame_no_lo16": 0,
                    "ts_seconds": 1701000000,
                    "ts_ms": 1,
                    "ts_us": 2,
                }
            ],
        )

        results = repair.scan_and_repair(self.root)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].status, "created")
        self.assertEqual(Path(results[0].idx_path).read_bytes(), expected)

    def test_scan_and_repair_replaces_empty_and_corrupted_idx(self) -> None:
        empty_seq = self.root / "DATA_25-07-22" / "Case1" / "Patient_Monitor" / "empty.seq"
        empty_expected = build_seq_file(
            empty_seq,
            [
                {
                    "payload": b"\x00\x00\x00\x01\x67\x64\x00\x32",
                    "flags": 30,
                    "frame_no_lo16": 1,
                    "ts_seconds": 1702000000,
                    "ts_ms": 10,
                    "ts_us": 20,
                }
            ],
        )
        Path(str(empty_seq) + ".idx").write_bytes(b"")

        bad_seq = self.root / "DATA_25-07-23" / "Case1" / "Monitor" / "bad.seq"
        bad_expected = build_seq_file(
            bad_seq,
            [
                {
                    "payload": b"\x00\x00\x00\x01\x67\x64\x00\x32\x02",
                    "flags": 30,
                    "frame_no_lo16": 65535,
                    "ts_seconds": 1702000001,
                    "ts_ms": 30,
                    "ts_us": 40,
                },
                {
                    "payload": b"\x00\x00\x00\x01\x41\xaa",
                    "flags": 0,
                    "frame_no_lo16": 0,
                    "ts_seconds": 1702000001,
                    "ts_ms": 60,
                    "ts_us": 70,
                },
            ],
        )
        Path(str(bad_seq) + ".idx").write_bytes(b"not-an-idx")

        results = repair.scan_and_repair(self.root)
        statuses = {Path(result.seq_path).name: result.status for result in results}

        self.assertEqual(statuses["empty.seq"], "replaced")
        self.assertEqual(statuses["bad.seq"], "replaced")
        self.assertEqual(Path(str(empty_seq) + ".idx").read_bytes(), empty_expected)
        self.assertEqual(Path(str(bad_seq) + ".idx").read_bytes(), bad_expected)

    def test_excluded_paths_are_skipped(self) -> None:
        junk_seq = self.root / "DATA_25-02-27" / "Case1" / "Monitor_JUNK" / "junk.seq"
        build_seq_file(
            junk_seq,
            [
                {
                    "payload": b"\x00\x00\x00\x01\x67",
                    "flags": 30,
                    "frame_no_lo16": 0,
                    "ts_seconds": 1703000000,
                    "ts_ms": 1,
                    "ts_us": 2,
                }
            ],
        )
        research_seq = (
            self.root
            / "seq_not_for_research"
            / "DATA_25-02-27"
            / "Case1"
            / "Monitor"
            / "research.seq"
        )
        build_seq_file(
            research_seq,
            [
                {
                    "payload": b"\x00\x00\x00\x01\x67",
                    "flags": 30,
                    "frame_no_lo16": 0,
                    "ts_seconds": 1703000001,
                    "ts_ms": 1,
                    "ts_us": 2,
                }
            ],
        )

        results = repair.scan_and_repair(self.root)
        statuses = {Path(result.seq_path).name: result.status for result in results}

        self.assertEqual(statuses["junk.seq"], "skipped_excluded")
        self.assertEqual(statuses["research.seq"], "skipped_excluded")
        self.assertFalse(Path(str(junk_seq) + ".idx").exists())
        self.assertFalse(Path(str(research_seq) + ".idx").exists())

    def test_invalid_and_unsupported_seq_are_reported_without_writes(self) -> None:
        invalid_seq = self.root / "DATA_25-06-26" / "Case1" / "Cart_RT_1" / "invalid.seq"
        invalid_seq.parent.mkdir(parents=True, exist_ok=True)
        invalid_seq.write_bytes(b"\x00" * repair.SEQ_HEADER_SIZE)

        unsupported_seq = self.root / "DATA_25-02-06" / "Case1" / "Monitor" / "unsupported.seq"
        build_seq_file(
            unsupported_seq,
            [
                {
                    "payload": b"\x00\x00\x00\x01\x67",
                    "flags": 30,
                    "frame_no_lo16": 0,
                    "ts_seconds": 1704000000,
                    "ts_ms": 1,
                    "ts_us": 2,
                }
            ],
            header_kwargs={"compression_fmt": 0},
        )

        results = repair.scan_and_repair(self.root)
        statuses = {Path(result.seq_path).name: result.status for result in results}

        self.assertEqual(statuses["invalid.seq"], "skipped_invalid_seq")
        self.assertEqual(statuses["unsupported.seq"], "skipped_unsupported")
        self.assertFalse(Path(str(invalid_seq) + ".idx").exists())
        self.assertFalse(Path(str(unsupported_seq) + ".idx").exists())

    def test_dry_run_and_report_do_not_write_idx(self) -> None:
        seq_path = self.root / "DATA_25-01-01" / "Case1" / "Monitor" / "dryrun.seq"
        build_seq_file(
            seq_path,
            [
                {
                    "payload": b"\x00\x00\x00\x01\x67\x64",
                    "flags": 30,
                    "frame_no_lo16": 5,
                    "ts_seconds": 1705000000,
                    "ts_ms": 10,
                    "ts_us": 11,
                }
            ],
        )

        results = repair.scan_and_repair(self.root, dry_run=True)
        summary = repair.build_summary(results)
        report_path = self.root / "report.json"
        repair.write_report(report_path, summary)

        self.assertEqual(results[0].status, "created")
        self.assertFalse(Path(str(seq_path) + ".idx").exists())
        data = json.loads(report_path.read_text(encoding="utf-8"))
        self.assertEqual(data["counts"]["created"], 1)

    def test_scan_and_repair_tracks_and_reuses_completed_files(self) -> None:
        seq_path = self.root / "DATA_25-03-03" / "Case1" / "Monitor" / "tracked.seq"
        build_seq_file(
            seq_path,
            [
                {
                    "payload": b"\x00\x00\x00\x01\x67\x64",
                    "flags": 30,
                    "frame_no_lo16": 7,
                    "ts_seconds": 1706000000,
                    "ts_ms": 10,
                    "ts_us": 11,
                }
            ],
        )
        tracking_path = self.root / "docs" / "seq_idx_repair_tracking.json"

        first_results = repair.scan_and_repair(self.root, tracking_file=tracking_path)

        self.assertEqual(first_results[0].status, "created")
        tracking_data = json.loads(tracking_path.read_text(encoding="utf-8"))
        self.assertEqual(tracking_data["entry_count"], 1)

        with mock.patch.object(
            repair,
            "repair_seq_file",
            side_effect=AssertionError("tracked file should not be reprocessed"),
        ):
            second_results = repair.scan_and_repair(
                self.root,
                tracking_file=tracking_path,
            )

        self.assertEqual(len(second_results), 1)
        self.assertEqual(second_results[0].status, "created")

    def test_tracking_write_falls_back_when_replace_is_denied(self) -> None:
        tracking_path = self.root / "docs" / "seq_idx_repair_tracking.json"
        tracking_path.parent.mkdir(parents=True, exist_ok=True)
        tracking_path.write_text("{}", encoding="utf-8")

        with mock.patch.object(
            repair.os,
            "replace",
            side_effect=PermissionError("simulated Windows lock"),
        ):
            repair.write_tracking_data(
                tracking_path,
                {
                    "schema_version": 1,
                    "script": "scripts/helpers/repair_seq_idx.py",
                    "assume_completed_count": 727,
                    "entries": {"sample": {"status": "ok"}},
                },
            )

        tracking_data = json.loads(tracking_path.read_text(encoding="utf-8"))
        self.assertEqual(tracking_data["assume_completed_count"], 727)
        self.assertEqual(tracking_data["entries"]["sample"]["status"], "ok")

    def test_scan_and_repair_can_assume_prior_completed_prefix(self) -> None:
        first_seq = self.root / "DATA_25-03-03" / "Case1" / "Monitor" / "a.seq"
        first_expected = build_seq_file(
            first_seq,
            [
                {
                    "payload": b"\x00\x00\x00\x01\x67",
                    "flags": 30,
                    "frame_no_lo16": 1,
                    "ts_seconds": 1707000000,
                    "ts_ms": 1,
                    "ts_us": 2,
                }
            ],
        )
        Path(str(first_seq) + ".idx").write_bytes(first_expected)

        second_seq = self.root / "DATA_25-03-03" / "Case1" / "Monitor" / "b.seq"
        build_seq_file(
            second_seq,
            [
                {
                    "payload": b"\x00\x00\x00\x01\x41",
                    "flags": 0,
                    "frame_no_lo16": 2,
                    "ts_seconds": 1707000001,
                    "ts_ms": 3,
                    "ts_us": 4,
                }
            ],
        )
        tracking_path = self.root / "docs" / "seq_idx_repair_tracking.json"

        results = repair.scan_and_repair(
            self.root,
            tracking_file=tracking_path,
            assume_completed_count=1,
        )

        self.assertEqual([result.status for result in results], ["ok", "created"])
        self.assertEqual(results[0].detail, "assumed_previous_run_completed")
        tracking_data = json.loads(tracking_path.read_text(encoding="utf-8"))
        self.assertEqual(tracking_data["entry_count"], 2)

    def test_main_reads_assume_completed_count_from_tracking_json(self) -> None:
        first_seq = self.root / "DATA_25-03-04" / "Case1" / "Monitor" / "a.seq"
        first_expected = build_seq_file(
            first_seq,
            [
                {
                    "payload": b"\x00\x00\x00\x01\x67",
                    "flags": 30,
                    "frame_no_lo16": 1,
                    "ts_seconds": 1708000000,
                    "ts_ms": 1,
                    "ts_us": 2,
                }
            ],
        )
        Path(str(first_seq) + ".idx").write_bytes(first_expected)

        second_seq = self.root / "DATA_25-03-04" / "Case1" / "Monitor" / "b.seq"
        build_seq_file(
            second_seq,
            [
                {
                    "payload": b"\x00\x00\x00\x01\x41",
                    "flags": 0,
                    "frame_no_lo16": 2,
                    "ts_seconds": 1708000001,
                    "ts_ms": 3,
                    "ts_us": 4,
                }
            ],
        )
        tracking_path = self.root / "docs" / "seq_idx_repair_tracking.json"
        tracking_path.parent.mkdir(parents=True, exist_ok=True)
        tracking_path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "script": "scripts/helpers/repair_seq_idx.py",
                    "assume_completed_count": 1,
                    "entries": {},
                }
            ),
            encoding="utf-8",
        )

        exit_code = repair.main(
            ["--root", str(self.root), "--tracking-file", str(tracking_path)]
        )

        self.assertEqual(exit_code, 0)
        tracking_data = json.loads(tracking_path.read_text(encoding="utf-8"))
        entries = tracking_data["entries"].values()
        statuses = {Path(entry["seq_path"]).name: entry["status"] for entry in entries}
        self.assertEqual(statuses, {"a.seq": "ok", "b.seq": "created"})
        details = {Path(entry["seq_path"]).name: entry["detail"] for entry in entries}
        self.assertEqual(details["a.seq"], "assumed_previous_run_completed")


if __name__ == "__main__":
    unittest.main()
