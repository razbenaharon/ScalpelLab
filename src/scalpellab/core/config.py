"""
ScalpelLab Configuration Loader
Standardizes access to settings across the core library, CLI, and App.
"""

import os
from pathlib import Path
from typing import List

import sys
import importlib.util

def load_legacy_config():
    """Manually load the root config.py to avoid shadowing."""
    project_root = Path(os.getcwd())
    config_path = project_root / "config.py"

    if not config_path.exists():
        # Try to find it relative to this file if cwd is not root
        config_path = Path(__file__).parent.parent.parent.parent / "config.py"

    if not config_path.exists():
        raise ImportError(f"Could not find legacy config.py at {config_path}")

    spec = importlib.util.spec_from_file_location("legacy_config", config_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

legacy_config = load_legacy_config()
# print(f"DEBUG: Loaded legacy_config from {legacy_config.__file__}")

class Settings:
    """Central settings for ScalpelLab."""

    def __init__(self):
        # Paths
        self.PROJECT_ROOT = Path(legacy_config.PROJECT_ROOT)
        self.DB_PATH = Path(legacy_config.DB_PATH)
        self.SEQ_ROOT = Path(legacy_config.SEQ_ROOT)
        self.MP4_ROOT = Path(legacy_config.MP4_ROOT)

        # Cameras
        self.DEFAULT_CAMERAS: List[str] = legacy_config.DEFAULT_CAMERAS

        # Thresholds
        self.THRESHOLD_MB: int = 200
        self.SMALL_FILE_THRESHOLD_MB: int = 10

    def validate(self) -> dict:
        """Validate paths exist."""
        return legacy_config.validate_paths()

    def __repr__(self):
        return f"<Settings DB={self.DB_PATH}>"

# Singleton instance
settings = Settings()
