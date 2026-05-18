"""Module-import smoke tests for the launcher pages.

Catches the most common regression: a page module that fails to import (a
syntax error, a missing helper, a typo in an ``@ui.page`` decorator) silently
breaks the dashboard at startup since ``app/app.py`` imports them all in one
block. These tests fail loudly the moment that happens.

A full NiceGUI test-client smoke (server boot + HTTP request) is intentionally
out of scope — too much scaffolding for the regression class it would catch
on top of plain import.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


PAGE_MODULES = [
    "app.pages.nuk_export",
    "app.pages.update_db",
    "app.pages.seq_to_mp4",
]


@pytest.mark.parametrize("module_name", PAGE_MODULES)
def test_page_module_imports(module_name: str):
    module = importlib.import_module(module_name)
    assert module is not None


def test_seq_to_mp4_exports_page_function():
    """The NiceGUI ``@ui.page`` decorator returns the underlying function so we
    can sanity-check it exists and is callable. We don't *call* it (it needs a
    running NiceGUI runtime); we just verify the binding survived import."""
    from app.pages import seq_to_mp4
    assert callable(seq_to_mp4.seq_to_mp4_page)


def test_progress_parser_exports():
    from app.pages.seq_to_mp4 import parse_progress, PROGRESS_PREFIX
    assert PROGRESS_PREFIX.startswith("PROGRESS::JSON")
    assert callable(parse_progress)
