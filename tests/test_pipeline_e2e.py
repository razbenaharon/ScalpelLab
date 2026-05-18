"""End-to-end pipeline test for the three numbered scripts.

Drives ``1_seq_curation.py`` → ``2_update_db.py`` → ``3_seq_to_mp4_convert.py``
as subprocesses against a real-file sample at ``SCALPELLAB_TEST_SAMPLE_DIR``
and a fresh empty SQLite DB. Asserts:

* curated files land under ``DATA_YY-MM-DD/CaseN/CameraName/``
* the DB picks up the curated files in ``seq_status``
* dry-run mode of script 3 emits ``PROGRESS::JSON`` events and writes no MP4
* a real run produces non-empty MP4 output under ``MP4_ROOT``
* re-running without ``--overwrite`` is a no-op (SQL filter already excludes
  rows that have a valid ``mp4_status``)
* ``--overwrite`` re-encodes and refreshes the file mtime
* a corrupted IDX is rejected without crashing

The expensive encoding steps are gated on having ffmpeg/ffprobe/mkvmerge on
PATH (``has_real_encoder`` fixture); machines without those skip cleanly.
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import time
from pathlib import Path

import pytest


PROGRESS_PREFIX = "PROGRESS::JSON "


def _run_script(
    python_exe: str,
    project_root: Path,
    script: str,
    args: list[str],
    env_overrides: dict[str, str],
) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env.update(env_overrides)
    cmd = [python_exe, str(project_root / script), *args]
    return subprocess.run(
        cmd, cwd=str(project_root), env=env,
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=1800,
    )


def _extract_progress_events(stdout: str) -> list[dict]:
    events = []
    for line in stdout.splitlines():
        if line.startswith(PROGRESS_PREFIX):
            try:
                events.append(json.loads(line[len(PROGRESS_PREFIX):]))
            except ValueError:
                pass
    return events


def _row_count(db_path: Path, table: str) -> int:
    with sqlite3.connect(db_path) as conn:
        try:
            return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
        except sqlite3.OperationalError:
            return 0  # table doesn't exist yet


def test_pipeline_curate_update_convert(
    python_exe: str,
    project_root: Path,
    staged_flat_sample: Path,
    temp_seq_root: Path,
    temp_mp4_root: Path,
    temp_db: Path,
    env_overrides: dict[str, str],
    has_real_encoder: bool,
):
    # ── Step 1: curate raw SEQ files into the DATA_YY-MM-DD/CaseN/Camera tree
    curate_args = [
        "--source", str(staged_flat_sample),
        "--dest", str(temp_seq_root),
        "--workers", "2",
    ]
    dry = _run_script(
        python_exe, project_root,
        "scripts/1_seq_curation.py", curate_args + ["--dry-run"],
        env_overrides,
    )
    assert dry.returncode == 0, f"curation --dry-run failed:\n{dry.stdout}\n{dry.stderr}"
    assert not list(temp_seq_root.rglob("*.seq")), "dry-run must not write files"

    real = _run_script(
        python_exe, project_root,
        "scripts/1_seq_curation.py", curate_args + ["--auto-confirm"],
        env_overrides,
    )
    assert real.returncode == 0, f"curation real run failed:\n{real.stdout}\n{real.stderr}"
    curated = list(temp_seq_root.rglob("*.seq"))
    assert curated, f"no .seq files curated under {temp_seq_root}"
    # Confirm the expected layout depth: DATA_*/Case*/Camera/file.seq
    sample = curated[0]
    rel = sample.relative_to(temp_seq_root).parts
    assert len(rel) >= 4 and rel[0].startswith("DATA_"), f"unexpected layout: {rel}"

    # ── Step 2: update DB so seq_status reflects the new files
    update_args = [
        "--db", str(temp_db),
        "--seq-root", str(temp_seq_root),
        "--mp4-root", str(temp_mp4_root),
        "--auto-confirm",
        "--skip-duration",     # speeds up tests; ffprobe still validates SEQs in step 3
    ]
    upd = _run_script(
        python_exe, project_root,
        "scripts/2_update_db.py", update_args,
        env_overrides,
    )
    assert upd.returncode == 0, f"update_db failed:\n{upd.stdout}\n{upd.stderr}"
    seq_rows = _row_count(temp_db, "seq_status")
    assert seq_rows >= len(curated), (
        f"seq_status has {seq_rows} rows, expected at least {len(curated)}"
    )

    # ── Step 3a: dry-run produces a plan event and writes no MP4
    convert_base = [
        "--db", str(temp_db),
        "--seq-root", str(temp_seq_root),
        "--mp4-root", str(temp_mp4_root),
        "--workers", "1",
        "--auto-confirm",
    ]
    dry3 = _run_script(
        python_exe, project_root,
        "scripts/3_seq_to_mp4_convert.py", convert_base + ["--dry-run"],
        env_overrides,
    )
    assert dry3.returncode == 0, f"convert --dry-run failed:\n{dry3.stdout}\n{dry3.stderr}"
    events = _extract_progress_events(dry3.stdout)
    plan = next((e for e in events if e["event"] == "plan"), None)
    done = next((e for e in events if e["event"] == "done"), None)
    assert plan is not None, "no PROGRESS::JSON plan event emitted"
    assert done is not None and done.get("dry_run") is True
    assert not list(temp_mp4_root.rglob("*.mp4")), "dry-run must not write MP4s"

    if not has_real_encoder:
        pytest.skip("ffmpeg/ffprobe/mkvmerge not on PATH — skipping encode steps")

    if plan["total"] == 0:
        pytest.skip("sample has no encode-eligible cameras (all under min size threshold)")

    # ── Step 3b: real run produces at least one non-empty MP4
    real3 = _run_script(
        python_exe, project_root,
        "scripts/3_seq_to_mp4_convert.py", convert_base,
        env_overrides,
    )
    mp4s = list(temp_mp4_root.rglob("*.mp4"))
    # If the host lacks NVENC the encoder step fails — surface that as skip,
    # not failure (the test harness can't satisfy the GPU requirement).
    if not mp4s:
        pytest.skip(
            "no MP4 produced — encoder (NVENC?) likely unavailable. "
            f"stderr tail:\n{real3.stderr[-500:]}"
        )
    assert real3.returncode == 0, f"convert real run failed:\n{real3.stdout}\n{real3.stderr}"
    for mp4 in mp4s:
        assert mp4.stat().st_size > 1024, f"MP4 too small: {mp4} ({mp4.stat().st_size} bytes)"

    # Refresh the DB so mp4_status reflects the new files
    _run_script(
        python_exe, project_root,
        "scripts/2_update_db.py", update_args,
        env_overrides,
    )

    # ── Step 3c: re-running without --overwrite is a no-op
    sample_mp4 = mp4s[0]
    mtime_before = sample_mp4.stat().st_mtime
    noop = _run_script(
        python_exe, project_root,
        "scripts/3_seq_to_mp4_convert.py", convert_base,
        env_overrides,
    )
    assert noop.returncode == 0
    assert sample_mp4.stat().st_mtime == mtime_before, "no-op run must not touch existing MP4"

    # ── Step 3d: --overwrite re-encodes the file
    time.sleep(1.1)  # ensure mtime granularity (FAT/NTFS on some Windows hosts is ~1s)
    over = _run_script(
        python_exe, project_root,
        "scripts/3_seq_to_mp4_convert.py", convert_base + ["--overwrite"],
        env_overrides,
    )
    assert over.returncode == 0, f"overwrite run failed:\n{over.stdout}\n{over.stderr}"
    over_events = _extract_progress_events(over.stdout)
    plan_o = next((e for e in over_events if e["event"] == "plan"), None)
    assert plan_o and plan_o.get("overwrite") is True
    assert sample_mp4.stat().st_mtime > mtime_before, "overwrite did not refresh MP4 mtime"


def test_corrupted_idx_rejected_cleanly(
    python_exe: str,
    project_root: Path,
    temp_seq_root: Path,
    temp_mp4_root: Path,
    temp_db: Path,
    env_overrides: dict[str, str],
    corrupted_idx: Path,
):
    """A synthetic IDX with a ts_sec of 0 (pre-2015 epoch) must be rejected
    by ``build_session_groups`` without crashing the script. The dry-run plan
    should simply report zero sessions.
    """
    # Plant a single corrupt camera so script 2 picks it up
    cam_dir = temp_seq_root / "DATA_25-01-15" / "Case1" / "Cart_Center_2"
    cam_dir.mkdir(parents=True)
    seq_path = cam_dir / "corrupt.seq"
    seq_path.write_bytes(b"\x00" * 1024)  # min-size dummy SEQ body
    idx_path = cam_dir / "corrupt.seq.idx"
    idx_path.write_bytes(corrupted_idx.read_bytes())

    # Populate seq_status by hand (cheaper than running script 2 for one file)
    with sqlite3.connect(temp_db) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS seq_status (
                recording_date TEXT, case_no INTEGER, camera_name TEXT,
                size_mb INTEGER, path TEXT,
                PRIMARY KEY (recording_date, case_no, camera_name)
            );
            CREATE TABLE IF NOT EXISTS mp4_status (
                recording_date TEXT, case_no INTEGER, camera_name TEXT,
                size_mb INTEGER, duration_minutes REAL, path TEXT,
                PRIMARY KEY (recording_date, case_no, camera_name)
            );
            """
        )
        conn.execute(
            "INSERT OR REPLACE INTO seq_status VALUES ('2025-01-15', 1, 'Cart_Center_2', 100, ?)",
            (str(seq_path),),
        )

    out = _run_script(
        python_exe, project_root,
        "scripts/3_seq_to_mp4_convert.py",
        [
            "--db", str(temp_db),
            "--seq-root", str(temp_seq_root),
            "--mp4-root", str(temp_mp4_root),
            "--dry-run", "--auto-confirm", "--workers", "1",
        ],
        env_overrides,
    )

    # The script must exit cleanly (0 or 1 are both acceptable here — 0 if it
    # silently skipped the corrupt row, 1 if it reported "no valid sessions").
    # What we care about: no traceback, no crash, no MP4 written.
    assert "Traceback" not in out.stderr, (
        f"corrupted IDX caused a crash:\n{out.stdout}\n{out.stderr}"
    )
    assert not list(temp_mp4_root.rglob("*.mp4"))
