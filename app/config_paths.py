"""Path picking and config.py persistence helpers for the dashboard."""

from __future__ import annotations

import re
from pathlib import Path
from tkinter import Tk, filedialog


PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = PROJECT_ROOT / "config.py"


def browse_file(title: str, filetypes: list[tuple[str, str]] | None = None) -> str | None:
    root = Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    try:
        selected = filedialog.askopenfilename(title=title, filetypes=filetypes or [])
        return selected or None
    finally:
        root.destroy()


def browse_directory(title: str, initialdir: str | None = None) -> str | None:
    root = Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    try:
        selected = filedialog.askdirectory(title=title, initialdir=initialdir or None)
        return selected or None
    finally:
        root.destroy()


def _py_raw_string(value: str) -> str:
    text = str(value)
    if text.endswith("\\") or '"' in text:
        return repr(text)
    return f'r"{text}"'


def _db_assignment(db_path: str) -> str:
    selected = Path(db_path)
    try:
        if selected.resolve() == (PROJECT_ROOT / "ScalpelDatabase.sqlite").resolve():
            return 'DB_PATH = PROJECT_ROOT / "ScalpelDatabase.sqlite"'
    except OSError:
        pass
    return f"DB_PATH = Path({_py_raw_string(db_path)})"


def save_config_paths(
    db_path: str,
    seq_root: str,
    mp4_root: str,
    analyses_root: str,
    norpix_sequence_viewer: str,
) -> None:
    """Rewrite only the path constants in config.py."""
    text = CONFIG_PATH.read_text(encoding="utf-8")
    replacements = {
        r"^DB_PATH\s*=.*$": _db_assignment(db_path),
        r"^SEQ_ROOT\s*=.*$": f"SEQ_ROOT = {_py_raw_string(seq_root)}",
        r"^MP4_ROOT\s*=.*$": f"MP4_ROOT = {_py_raw_string(mp4_root)}",
        r"^ANALYSES_ROOT\s*=.*$": f"ANALYSES_ROOT = {_py_raw_string(analyses_root)}",
        r"^NORPIX_SEQUENCE_VIEWER_PATH\s*=.*$": (
            f"NORPIX_SEQUENCE_VIEWER_PATH = {_py_raw_string(norpix_sequence_viewer)}"
        ),
    }
    for pattern, replacement in replacements.items():
        text, count = re.subn(
            pattern,
            lambda _match, value=replacement: value,
            text,
            count=1,
            flags=re.MULTILINE,
        )
        if count != 1:
            raise RuntimeError(f"Could not find config assignment matching {pattern}")
    CONFIG_PATH.write_text(text, encoding="utf-8")


def path_status(path: str, kind: str) -> tuple[bool, str]:
    p = Path(path)
    if kind == "file":
        exists = p.is_file()
    else:
        exists = p.is_dir()
    return exists, "OK" if exists else "Not found"
