from __future__ import annotations

from pathlib import Path

from scripts.helpers.mapping_project import validate_old_mp4_idx_alignment as validator


def test_count_idx_records_rejects_empty_idx(tmp_path: Path) -> None:
    idx = tmp_path / "empty.seq.idx"
    idx.write_bytes(b"")

    records, file_size, remainder = validator.count_idx_records(idx)

    assert records is None
    assert file_size == 0
    assert remainder == 0


def test_count_idx_records_counts_32_byte_records(tmp_path: Path) -> None:
    idx = tmp_path / "valid.seq.idx"
    idx.write_bytes(b"\0" * (3 * 32))

    records, file_size, remainder = validator.count_idx_records(idx)

    assert records == 3
    assert file_size == 96
    assert remainder == 0


def test_count_idx_records_allows_record_size_override(tmp_path: Path) -> None:
    idx = tmp_path / "valid_24.seq.idx"
    idx.write_bytes(b"\0" * (5 * 24))

    records, file_size, remainder = validator.count_idx_records(idx, record_size=24)

    assert records == 5
    assert file_size == 120
    assert remainder == 0


def test_count_idx_records_reports_remainder(tmp_path: Path) -> None:
    idx = tmp_path / "odd.seq.idx"
    idx.write_bytes(b"\0" * 40)

    records, file_size, remainder = validator.count_idx_records(idx)

    assert records == 1
    assert file_size == 40
    assert remainder == 8


def test_count_idx_records_handles_missing_path(tmp_path: Path) -> None:
    idx = tmp_path / "missing.seq.idx"

    records, file_size, remainder = validator.count_idx_records(idx)

    assert records is None
    assert file_size == 0
    assert remainder == 0


def test_validation_green_when_counts_match(
    tmp_path: Path,
    monkeypatch,
) -> None:
    mp4 = tmp_path / "old.mp4"
    idx = tmp_path / "camera.seq.idx"
    mp4.write_bytes(b"placeholder")
    idx.write_bytes(b"\0" * (4 * 32))
    monkeypatch.setattr(validator, "count_mp4_frames", lambda *args, **kwargs: 4)
    monkeypatch.setattr(
        validator,
        "_count_mp4_frames_with_errors",
        lambda *args, **kwargs: (4, []),
    )

    result = validator.get_alignment_result(mp4, idx)

    assert result.matched is True
    assert result.classification == "GREEN"
    assert validator.validate_alignment(mp4, idx) is True


def test_validation_green_when_mp4_is_one_frame_off(
    tmp_path: Path,
    monkeypatch,
) -> None:
    mp4 = tmp_path / "old.mp4"
    idx = tmp_path / "camera.seq.idx"
    mp4.write_bytes(b"placeholder")
    idx.write_bytes(b"\0" * (4 * 32))
    monkeypatch.setattr(
        validator,
        "_count_mp4_frames_with_errors",
        lambda *args, **kwargs: (5, []),
    )

    result = validator.get_alignment_result(mp4, idx)

    assert result.matched is True
    assert result.classification == "GREEN"
    assert result.delta == 1


def test_validation_red_when_delta_exceeds_tolerance(
    tmp_path: Path,
    monkeypatch,
) -> None:
    mp4 = tmp_path / "old.mp4"
    idx = tmp_path / "camera.seq.idx"
    mp4.write_bytes(b"placeholder")
    idx.write_bytes(b"\0" * (4 * 32))
    monkeypatch.setattr(
        validator,
        "_count_mp4_frames_with_errors",
        lambda *args, **kwargs: (10, []),
    )

    result = validator.get_alignment_result(mp4, idx)

    assert result.matched is False
    assert result.classification == "RED"
    assert result.delta == 6


def test_validation_red_when_idx_has_remainder(
    tmp_path: Path,
    monkeypatch,
) -> None:
    mp4 = tmp_path / "old.mp4"
    idx = tmp_path / "camera.seq.idx"
    mp4.write_bytes(b"placeholder")
    idx.write_bytes(b"\0" * 40)
    monkeypatch.setattr(
        validator,
        "_count_mp4_frames_with_errors",
        lambda *args, **kwargs: (1, []),
    )

    result = validator.get_alignment_result(mp4, idx)

    assert result.matched is False
    assert result.classification == "RED"
    assert result.idx_remainder_bytes == 8


def test_validation_red_when_mp4_frame_count_unavailable(
    tmp_path: Path,
    monkeypatch,
) -> None:
    mp4 = tmp_path / "old.mp4"
    idx = tmp_path / "camera.seq.idx"
    mp4.write_bytes(b"placeholder")
    idx.write_bytes(b"\0" * (4 * 32))
    monkeypatch.setattr(
        validator,
        "_count_mp4_frames_with_errors",
        lambda *args, **kwargs: (None, ["ffprobe failed and OpenCV unavailable"]),
    )

    result = validator.get_alignment_result(mp4, idx)

    assert result.matched is False
    assert result.classification == "RED"
    assert result.delta is None
    assert "ffprobe failed and OpenCV unavailable" in result.errors
