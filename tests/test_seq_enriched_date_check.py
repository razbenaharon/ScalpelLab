from __future__ import annotations

import importlib.util
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


def _load_analyzer(project_root: Path):
    spec = importlib.util.spec_from_file_location(
        "_analyze_seq_fields",
        project_root / "scripts" / "helpers" / "analyze_seq_fields.py",
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _ts(year: int, month: int, day: int, hour: int = 0) -> float:
    return datetime(year, month, day, hour, tzinfo=timezone.utc).timestamp()


def test_seq_enriched_backfills_frame_date_check_and_sync_view(
    project_root: Path,
    temp_db: Path,
):
    analyzer = _load_analyzer(project_root)

    with sqlite3.connect(temp_db) as conn:
        conn.executescript(
            """
            CREATE TABLE seq_enriched (
                recording_date TEXT NOT NULL,
                case_no INTEGER NOT NULL,
                camera_name TEXT NOT NULL,
                has_idx INTEGER,
                header_ok INTEGER,
                idx_frames INTEGER,
                first_frame_time REAL,
                last_frame_time REAL,
                size_mb REAL,
                PRIMARY KEY (recording_date, case_no, camera_name)
            );
            """
        )
        conn.executemany(
            """
            INSERT INTO seq_enriched (
                recording_date, case_no, camera_name, has_idx, header_ok,
                idx_frames, first_frame_time, last_frame_time, size_mb
            )
            VALUES (?, ?, ?, 1, 1, 10, ?, ?, 100)
            """,
            [
                ("2025-01-15", 1, "General_3", _ts(2025, 1, 15), _ts(2025, 1, 15, 1)),
                ("2025-01-15", 1, "Monitor", _ts(2025, 1, 15), _ts(2025, 1, 16)),
            ],
        )

    analyzer.ensure_analysis_table(str(temp_db))

    with sqlite3.connect(temp_db) as conn:
        rows = {
            row[0]: row[1:]
            for row in conn.execute(
                """
                SELECT camera_name,
                       first_frame_date_matches_recording_date,
                       last_frame_date_matches_recording_date,
                       frame_dates_match_recording_date,
                       frame_date_mismatch_reason
                  FROM seq_enriched
                """
            )
        }
        assert rows["General_3"] == (1, 1, 1, None)
        assert rows["Monitor"] == (1, 0, 0, "last_frame_date=2025-01-16")

        syncable = {
            row[0]: row[1]
            for row in conn.execute(
                "SELECT camera_name, is_syncable FROM cur_sync_status"
            )
        }
        assert syncable == {"General_3": 1, "Monitor": 0}
