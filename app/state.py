"""App-wide state for the NiceGUI ScalpelLab UI.

The selected SQLite database path is stored in NiceGUI's general storage so it
persists across navigation between pages and across app restarts.
"""

import os
import sys
from nicegui import app

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import get_db_path


DB_PATH_KEY = "db_path"
DARK_MODE_KEY = "dark_mode"


def default_db_path() -> str:
    return os.environ.get("SCALPEL_DB", get_db_path())


def get() -> str:
    return app.storage.general.get(DB_PATH_KEY) or default_db_path()


def set_(value: str) -> None:
    app.storage.general[DB_PATH_KEY] = value


def is_dark() -> bool:
    return bool(app.storage.general.get(DARK_MODE_KEY, False))


def set_dark(value: bool) -> None:
    app.storage.general[DARK_MODE_KEY] = bool(value)
