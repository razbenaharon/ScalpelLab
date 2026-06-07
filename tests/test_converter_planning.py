from __future__ import annotations

import importlib.util
import sqlite3
from pathlib import Path


def _load_converter(project_root: Path):
    spec = importlib.util.spec_from_file_location(
        "_seq_to_mp4_convert",
        project_root / "scripts" / "3_seq_to_mp4_convert.py",
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_corrupt_idx_can_use_not_syncable_fallback(
    project_root: Path,
    temp_db: Path,
    temp_seq_root: Path,
    temp_mp4_root: Path,
    corrupted_idx: Path,
    monkeypatch,
):
    conv = _load_converter(project_root)
    monkeypatch.setattr(conv, "DB_PATH", str(temp_db))
    monkeypatch.setattr(conv, "SEQ_ROOT", str(temp_seq_root))
    monkeypatch.setattr(conv, "OUT_ROOT", str(temp_mp4_root))
    monkeypatch.setattr(
        conv,
        "probe_h264_stream",
        lambda seq_path, ffprobe_path: {
            "width": 1920,
            "height": 1080,
            "pix_fmt": "yuv420p",
            "ffprobe_fps": 30.0,
        },
    )

    cam_dir = temp_seq_root / "DATA_25-01-15" / "Case1" / "Cart_Center_2"
    cam_dir.mkdir(parents=True)
    seq_path = cam_dir / "corrupt.seq"
    seq_path.write_bytes(b"\x00" * 2048)
    (cam_dir / "corrupt.seq.idx").write_bytes(corrupted_idx.read_bytes())

    with sqlite3.connect(temp_db) as conn:
        conn.executescript(
            """
            CREATE TABLE seq_status (
                recording_date TEXT, case_no INTEGER, camera_name TEXT,
                size_mb REAL, path TEXT,
                PRIMARY KEY (recording_date, case_no, camera_name)
            );
            CREATE TABLE mp4_status (
                recording_date TEXT, case_no INTEGER, camera_name TEXT,
                size_mb REAL, duration_minutes REAL, path TEXT,
                PRIMARY KEY (recording_date, case_no, camera_name)
            );
            """
        )

    files = [{
        "recording_date": "2025-01-15",
        "case_no": 1,
        "camera_name": "Cart_Center_2",
        "seq_size_mb": 100,
    }]
    diag = conv.PlanDiagnostics()

    groups = conv.build_session_groups(files, "ffprobe", diagnostics=diag)
    fallbacks = conv.build_not_syncable_fallbacks(files, "ffprobe", diagnostics=diag)

    assert groups == []
    assert len(fallbacks) == 1
    assert fallbacks[0].camera_name == "Cart_Center_2"
    assert "corrupt IDX timestamps" in fallbacks[0].reason
