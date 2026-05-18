"""Unit tests for the PROGRESS::JSON parser the dashboard uses.

These tests are pure-Python — no subprocess, no NiceGUI runtime, no sample
data — so they run anywhere pytest can collect them.
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.pages.seq_to_mp4 import parse_progress


def test_empty_logs_returns_zeros():
    result = parse_progress([])
    assert result["_counters"] == {"ok": 0, "fail": 0, "skip": 0}


def test_ignores_non_progress_lines():
    result = parse_progress([
        "starting conversion",
        "  [Cart_Center_2] ⏳ 45% — frame 100/200",
        "[INFO] Added column",
    ])
    assert result["_counters"] == {"ok": 0, "fail": 0, "skip": 0}
    assert "plan" not in result
    assert "camera_done" not in result


def test_ignores_malformed_json():
    result = parse_progress([
        'PROGRESS::JSON {bad json here',
        'PROGRESS::JSON ',
        'PROGRESS::JSON not even close',
    ])
    assert result["_counters"] == {"ok": 0, "fail": 0, "skip": 0}


def test_counts_camera_done_statuses():
    logs = [
        'PROGRESS::JSON {"event": "plan", "total": 3, "sessions": 1}',
        'PROGRESS::JSON {"event": "camera_start", "camera": "A", "done": 0, "total": 3}',
        'PROGRESS::JSON {"event": "camera_done", "camera": "A", "status": "ok", "done": 1, "total": 3}',
        'PROGRESS::JSON {"event": "camera_done", "camera": "B", "status": "fail", "done": 2, "total": 3}',
        'PROGRESS::JSON {"event": "camera_done", "camera": "C", "status": "skip", "done": 3, "total": 3}',
        'PROGRESS::JSON {"event": "done", "ok": 1, "fail": 1, "skip": 1}',
    ]
    result = parse_progress(logs)
    assert result["_counters"] == {"ok": 1, "fail": 1, "skip": 1}
    assert result["plan"]["total"] == 3
    assert result["plan"]["sessions"] == 1
    assert result["done"]["ok"] == 1
    assert result["camera_done"]["camera"] == "C"  # latest wins


def test_latest_frame_wins():
    logs = [
        'PROGRESS::JSON {"event": "frame", "camera": "A", "percent": 10, "frame": 100, "total": 1000}',
        'PROGRESS::JSON {"event": "frame", "camera": "A", "percent": 50, "frame": 500, "total": 1000}',
    ]
    result = parse_progress(logs)
    assert result["frame"]["percent"] == 50
    assert result["frame"]["frame"] == 500


def test_handles_unknown_status_safely():
    """Future event types or unknown statuses must not crash the parser."""
    logs = [
        'PROGRESS::JSON {"event": "camera_done", "camera": "A", "status": "weird"}',
        'PROGRESS::JSON {"event": "some_future_event", "foo": "bar"}',
    ]
    result = parse_progress(logs)
    assert result["_counters"] == {"ok": 0, "fail": 0, "skip": 0}
    assert result["some_future_event"]["foo"] == "bar"
