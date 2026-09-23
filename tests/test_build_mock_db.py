"""Tests for the public mock-catalog builder (scripts/helpers/build_mock_db.py)."""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "helpers"))
import build_mock_db  # noqa: E402

REAL_NAMES = ["Jane Doe", "Johnny Appleseed", "General Senior"]


def _make_source(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE anesthesiology (anesthesiology_key INTEGER PRIMARY KEY, name TEXT NOT NULL,
                                     code TEXT, anesthesiology_start_date TEXT, grade_a_date TEXT);
        CREATE TABLE analysis_information (recording_date TEXT, case_no INTEGER, label_by TEXT,
                                           PRIMARY KEY (recording_date, case_no));
        CREATE TABLE recording_details (recording_date TEXT, case_no INTEGER, code TEXT,
                                        anesthesiology_key INTEGER, PRIMARY KEY (recording_date, case_no));
        CREATE TABLE mp4_status (recording_date TEXT, case_no INTEGER, camera_name TEXT);
        """
    )
    # AB1510 is deliberately a real code that a naive synthetic mapping would produce.
    conn.executemany("INSERT INTO anesthesiology VALUES (?, ?, ?, '2015-10-01', NULL)",
                     [(1, REAL_NAMES[0], "JD1510"), (2, REAL_NAMES[1], "AB1510"), (3, REAL_NAMES[2], "GS1603")])
    conn.executemany("INSERT INTO analysis_information VALUES (?, ?, ?)",
                     [("2023-01-01", 1, "Moshe"), ("2023-01-02", 1, "Dana")])
    conn.executemany("INSERT INTO recording_details VALUES (?, ?, ?, ?)",
                     [("2023-01-01", 1, "JD1510", 1), ("2023-01-02", 1, "AB1510", 2)])
    conn.execute("INSERT INTO mp4_status VALUES ('2023-01-01', 1, 'General_3')")
    conn.commit()
    conn.close()


def test_mock_has_no_original_values_and_keeps_structure(tmp_path):
    src, out = tmp_path / "real.sqlite", tmp_path / "mock.sqlite"
    _make_source(src)

    build_mock_db.build(src, out)

    blob = out.read_bytes().lower()
    for value in ["jane doe", "johnny", "appleseed", "jd1510", "ab1510", "gs1603", "moshe", "dana"]:
        assert value.encode() not in blob, value
    conn = sqlite3.connect(out)
    assert [r[0] for r in conn.execute("SELECT name FROM anesthesiology ORDER BY anesthesiology_key")] == [
        "Anesthesiologist 01", "Anesthesiologist 02", "Anesthesiologist 03"]
    # Codes stay joinable across tables and keep their YYMM suffix.
    joined = conn.execute("""SELECT a.code, r.code FROM recording_details r
                             JOIN anesthesiology a USING (anesthesiology_key)""").fetchall()
    assert all(a == r and a.endswith("1510") for a, r in joined)
    assert conn.execute("SELECT camera_name FROM mp4_status").fetchone() == ("General_3",)
    conn.close()


def test_builder_refuses_output_that_still_leaks(tmp_path, monkeypatch):
    src, out = tmp_path / "real.sqlite", tmp_path / "mock.sqlite"
    _make_source(src)
    monkeypatch.setattr(build_mock_db, "anonymize", lambda conn: {"names": 0, "codes": 0, "labelers": 0})

    with pytest.raises(SystemExit):
        build_mock_db.build(src, out)
    assert not out.exists()


def test_committed_mock_exists_and_config_can_fall_back_to_it():
    import config

    assert config.MOCK_DB_PATH.is_file()
    assert config.DB_PATH in (config.REAL_DB_PATH, config.MOCK_DB_PATH)
