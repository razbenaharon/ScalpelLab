"""App-wide state for the NiceGUI ScalpelLab UI.

The selected SQLite database path is stored in NiceGUI's general storage so it
persists across navigation between pages and across app restarts.
"""

import os
import sys
from nicegui import app

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import (
    get_analyses_root,
    get_db_path,
    get_mp4_root,
    get_norpix_sequence_viewer_path,
    get_seq_root,
)


DB_PATH_KEY = "db_path"
SEQ_ROOT_KEY = "seq_root"
MP4_ROOT_KEY = "mp4_root"
ANALYSES_ROOT_KEY = "analyses_root"
NORPIX_SEQUENCE_VIEWER_KEY = "norpix_sequence_viewer"
DARK_MODE_KEY = "dark_mode"


def default_db_path() -> str:
    return os.environ.get("SCALPEL_DB", get_db_path())


def default_seq_root() -> str:
    return os.environ.get("SCALPEL_SEQ_ROOT", get_seq_root())


def default_mp4_root() -> str:
    return os.environ.get("SCALPEL_MP4_ROOT", get_mp4_root())


def default_analyses_root() -> str:
    return os.environ.get("SCALPEL_ANALYSES_ROOT", get_analyses_root())


def default_norpix_sequence_viewer() -> str:
    return os.environ.get(
        "SCALPEL_NORPIX_SEQUENCE_VIEWER",
        get_norpix_sequence_viewer_path(),
    )


def get() -> str:
    return app.storage.general.get(DB_PATH_KEY) or default_db_path()


def set_(value: str) -> None:
    app.storage.general[DB_PATH_KEY] = value


def get_seq() -> str:
    return app.storage.general.get(SEQ_ROOT_KEY) or default_seq_root()


def set_seq(value: str) -> None:
    app.storage.general[SEQ_ROOT_KEY] = value


def get_mp4() -> str:
    return app.storage.general.get(MP4_ROOT_KEY) or default_mp4_root()


def set_mp4(value: str) -> None:
    app.storage.general[MP4_ROOT_KEY] = value


def get_analyses() -> str:
    return app.storage.general.get(ANALYSES_ROOT_KEY) or default_analyses_root()


def set_analyses(value: str) -> None:
    app.storage.general[ANALYSES_ROOT_KEY] = value


def get_norpix_sequence_viewer() -> str:
    return app.storage.general.get(NORPIX_SEQUENCE_VIEWER_KEY) or default_norpix_sequence_viewer()


def set_norpix_sequence_viewer(value: str) -> None:
    app.storage.general[NORPIX_SEQUENCE_VIEWER_KEY] = value


def is_dark() -> bool:
    return bool(app.storage.general.get(DARK_MODE_KEY, False))


def set_dark(value: bool) -> None:
    app.storage.general[DARK_MODE_KEY] = bool(value)
