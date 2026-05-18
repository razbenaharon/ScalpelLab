"""Shared pytest fixtures for the ScalpelLab end-to-end pipeline tests.

The whole-pipeline E2E test consumes a small real-file sample whose location
is provided via the ``SCALPELLAB_TEST_SAMPLE_DIR`` environment variable. The
fixture is opt-in by design: machines without a local sample (e.g. CI, a
clean clone) automatically skip the heavy tests instead of failing.

To set up a sample: see tests/README.md.
"""

from __future__ import annotations

import os
import shutil
import struct
import sqlite3
import sys
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parent.parent
SAMPLE_ENV_VAR = "SCALPELLAB_TEST_SAMPLE_DIR"


def _have_executable(name: str) -> bool:
    return shutil.which(name) is not None


def _have_pipeline_tools() -> bool:
    """Use the conversion script's own ``find_*`` helpers so we also pick up
    Windows installs that aren't on PATH (mkvmerge in particular ships under
    ``C:\\Program Files\\MKVToolNix`` and isn't typically added)."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "_seq_to_mp4_probe",
        PROJECT_ROOT / "scripts" / "3_seq_to_mp4_convert.py",
    )
    if spec is None or spec.loader is None:
        return False
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except Exception:
        return False
    return bool(mod.find_ffmpeg() and mod.find_ffprobe() and mod.find_mkvmerge())


@pytest.fixture(scope="session")
def project_root() -> Path:
    return PROJECT_ROOT


@pytest.fixture(scope="session")
def sample_dir() -> Path:
    """Path to a small real-file sample of NorPix SEQ recordings.

    Expects a layout matching production:
    ``<sample_dir>/DATA_YY-MM-DD/CaseN/CameraName/<*.seq>(+.idx, +.metadata)``.

    Skips the test cleanly when the env var is unset or the dir is empty.
    """
    raw = os.environ.get(SAMPLE_ENV_VAR)
    if not raw:
        pytest.skip(
            f"{SAMPLE_ENV_VAR} not set; see tests/README.md to enable real-sample tests."
        )
    path = Path(raw)
    if not path.is_dir():
        pytest.skip(f"{SAMPLE_ENV_VAR}={raw} is not a directory.")
    if not any(path.rglob("*.seq")):
        pytest.skip(f"{SAMPLE_ENV_VAR}={raw} contains no .seq files.")
    return path


@pytest.fixture
def temp_seq_root(tmp_path_factory) -> Path:
    return tmp_path_factory.mktemp("seq_root")


@pytest.fixture
def temp_mp4_root(tmp_path_factory) -> Path:
    return tmp_path_factory.mktemp("mp4_root")


@pytest.fixture
def temp_db(tmp_path_factory) -> Path:
    """An empty SQLite file. Script 2 will populate the schema on first run
    (it uses ``CREATE TABLE IF NOT EXISTS`` for ``seq_status`` and
    ``mp4_status``; ``analyze_seq_fields`` does the same for ``seq_enriched``).
    """
    db = tmp_path_factory.mktemp("db") / "test.sqlite"
    sqlite3.connect(db).close()
    return db


@pytest.fixture
def staged_flat_sample(sample_dir: Path, tmp_path_factory) -> Path:
    """Stages ``sample_dir`` as a ``<camera>/<file>`` tree for script 1.

    Script 1 (``1_seq_curation.py``) reads channel names from the immediate
    parent folder of each SEQ file (via ``Path(dirpath).name``), and parses
    the recording date from the filename's ``YYYY-MM-DD_HH-MM-SS`` prefix.
    So we strip the DATA_/Case layers but keep the camera folder.
    """
    flat = tmp_path_factory.mktemp("flat_source")
    for seq in sample_dir.rglob("*.seq"):
        camera = seq.parent.name
        cam_dir = flat / camera
        cam_dir.mkdir(exist_ok=True)
        for sibling in seq.parent.iterdir():
            if sibling.is_file():
                shutil.copy2(sibling, cam_dir / sibling.name)
    return flat


@pytest.fixture
def env_overrides(temp_db: Path, temp_seq_root: Path, temp_mp4_root: Path) -> dict[str, str]:
    """Env vars that point all the scripts at the temp tree."""
    return {
        "SCALPEL_DB": str(temp_db),
        "SCALPEL_SEQ_ROOT": str(temp_seq_root),
        "SCALPEL_MP4_ROOT": str(temp_mp4_root),
        "PYTHONIOENCODING": "utf-8",
    }


@pytest.fixture
def python_exe() -> str:
    return sys.executable


@pytest.fixture
def corrupted_idx(tmp_path: Path) -> Path:
    """Synthesize a 32-byte IDX record with a ts_sec of 0 (pre-2015 epoch).

    ``build_session_groups`` should reject this and log a clear warning, not
    crash. Layout matches ``IDX_STRUCT = '<QIIIIIi'`` from script 3.
    """
    idx = tmp_path / "corrupt.seq.idx"
    record = struct.pack(
        '<QIIIIIi',
        1024,   # offset
        1024,   # size
        0,      # ts_sec — far below EPOCH_MIN
        0,      # ts_sub
        0,      # reserved
        0,      # flags
        0,      # frame_number
    )
    idx.write_bytes(record)
    return idx


@pytest.fixture(scope="session")
def has_real_encoder() -> bool:
    """All three external executables present? Conversion tests skip otherwise."""
    return _have_pipeline_tools()
