from __future__ import annotations

from pathlib import Path

import config
from app import config_paths


def test_norpix_sequence_viewer_getter_returns_configured_path():
    assert config.get_norpix_sequence_viewer_path() == config.NORPIX_SEQUENCE_VIEWER_PATH


def test_save_config_paths_preserves_raw_windows_paths(tmp_path, monkeypatch):
    temp_config = tmp_path / "config.py"
    temp_config.write_text(
        "\n".join(
            [
                "from pathlib import Path",
                'PROJECT_ROOT = Path(__file__).parent',
                'DB_PATH = PROJECT_ROOT / "ScalpelDatabase.sqlite"',
                'SEQ_ROOT = r"F:\\OldSeq"',
                'MP4_ROOT = r"F:\\OldMp4"',
                'NORPIX_SEQUENCE_VIEWER_PATH = r"C:\\Old\\SequenceViewer.exe"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(config_paths, "CONFIG_PATH", temp_config)

    config_paths.save_config_paths(
        str(Path("X:/Data/ScalpelDatabase.sqlite")),
        r"F:\Room_8_Data\Sequence_Backup",
        r"F:\Room_8_Data\Recordings",
        r"C:\Program Files\Common Files\NorPix\SequenceViewer.exe",
    )

    text = temp_config.read_text(encoding="utf-8")
    assert 'SEQ_ROOT = r"F:\\Room_8_Data\\Sequence_Backup"' in text
    assert 'MP4_ROOT = r"F:\\Room_8_Data\\Recordings"' in text
    assert (
        'NORPIX_SEQUENCE_VIEWER_PATH = '
        'r"C:\\Program Files\\Common Files\\NorPix\\SequenceViewer.exe"'
    ) in text


def test_path_status_accepts_executable_file(tmp_path):
    exe = tmp_path / "SequenceViewer.exe"
    exe.write_bytes(b"")

    ok, message = config_paths.path_status(str(exe), "file")

    assert ok is True
    assert message == "OK"
