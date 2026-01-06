"""Configuration module for ScalpelLab Database Manager.

This module centralizes all path configurations for the project, including:
- Database location (SQLite)
- Source directories for SEQ and MP4 files
- Default camera configuration
- Path validation utilities

The configuration is designed to be easily customizable for different deployment
environments by editing the SEQ_ROOT and MP4_ROOT paths.

IMPORTANT:
    - The database (ScalpelDatabase.sqlite) must always be in the project directory
    - SEQ_ROOT and MP4_ROOT can be on different drives or locations
    - Use raw strings (r"...") for Windows paths to avoid backslash issues

Example:
    To validate paths and view current configuration::

        python config.py

Note:
    All paths are configurable via module-level constants. Helper functions
    provide both string and Path object access to configured locations.
"""

import os
from pathlib import Path
from typing import List, Dict, Any

# =============================================================================
# Database Configuration
# =============================================================================
# Database is always in the project root directory
PROJECT_ROOT = Path(__file__).parent
DB_PATH = PROJECT_ROOT / "ScalpelDatabase.sqlite"

# =============================================================================
# File System Paths - EDIT THESE TO MATCH YOUR SYSTEM
# =============================================================================

# Root directory for SEQ files (original sequence files)
# Example: r"F:\Room_8_Data\Sequence_Backup"
# Expected structure: SEQ_ROOT/DATA_YY-MM-DD/CaseN/CameraName/*.seq
SEQ_ROOT = r"F:\Room_8_Data\Sequence_Backup"

# Root directory for MP4 files (exported video files)
# Example: r"F:\Room_8_Data\Recordings"
# Expected structure: MP4_ROOT/DATA_YY-MM-DD/CaseN/CameraName/*.mp4
MP4_ROOT = r"F:\Room_8_Data\Recordings"

# =============================================================================
# Camera Configuration
# =============================================================================
# List of default camera names used in the surgical recording system.
# These represent the 8 standard camera sources for multi-angle recording.
DEFAULT_CAMERAS: List[str] = [
    "Cart_Center_2",
    "Cart_LT_4",
    "Cart_RT_1",
    "General_3",
    "Monitor",
    "Patient_Monitor",
    "Ventilator_Monitor",
    "Injection_Port"
]

# =============================================================================
# Helper Functions
# =============================================================================
def get_db_path() -> str:
    """Get the database path as a string.

    Returns:
        str: Absolute path to the ScalpelDatabase.sqlite file.

    Example:
        >>> db_path = get_db_path()
        >>> print(db_path)
        'C:/Users/User/Desktop/Python/ScalpelLab/ScalpelDatabase.sqlite'
    """
    return str(DB_PATH)


def get_seq_root() -> str:
    """Get the SEQ root directory as a string.

    Returns:
        str: Path to the root directory containing SEQ sequence files.

    Note:
        Expected directory structure: SEQ_ROOT/DATA_YY-MM-DD/CaseN/CameraName/*.seq
    """
    return SEQ_ROOT


def get_mp4_root() -> str:
    """Get the MP4 root directory as a string.

    Returns:
        str: Path to the root directory containing exported MP4 video files.

    Note:
        Expected directory structure: MP4_ROOT/DATA_YY-MM-DD/CaseN/CameraName/*.mp4
    """
    return MP4_ROOT


def validate_paths() -> Dict[str, Dict[str, Any]]:
    """Validate that configured paths exist on the filesystem.

    Checks whether the database file and both root directories (SEQ and MP4)
    exist at their configured locations. This is useful for debugging
    configuration issues and verifying setup after deployment.

    Returns:
        Dict[str, Dict[str, Any]]: Nested dictionary with validation results.
            Each key ('db_path', 'seq_root', 'mp4_root') maps to a dictionary
            containing:
                - 'path' (str): The configured path
                - 'exists' (bool): Whether the path exists
                - 'type' (str): Either 'file' or 'directory'

    Example:
        >>> results = validate_paths()
        >>> if not results['db_path']['exists']:
        ...     print("Database not found!")
    """
    results = {
        'db_path': {
            'path': str(DB_PATH),
            'exists': DB_PATH.exists(),
            'type': 'file'
        },
        'seq_root': {
            'path': SEQ_ROOT,
            'exists': Path(SEQ_ROOT).exists(),
            'type': 'directory'
        },
        'mp4_root': {
            'path': MP4_ROOT,
            'exists': Path(MP4_ROOT).exists(),
            'type': 'directory'
        }
    }
    return results


def print_config() -> None:
    """Print current configuration and validate all paths.

    Displays a formatted table showing:
    - Project root directory
    - Database location
    - SEQ files root directory
    - MP4 files root directory
    - Validation status for each path (EXISTS or NOT FOUND)

    The output is printed to stdout and includes warning messages if any
    paths are not found.

    Example:
        >>> print_config()
        ======================================================================
        SCALPELLAB DATABASE MANAGER - CONFIGURATION
        ======================================================================
        ...
    """
    print("=" * 70)
    print("SCALPELLAB DATABASE MANAGER - CONFIGURATION")
    print("=" * 70)
    print(f"Project Root:  {PROJECT_ROOT}")
    print(f"Database:      {DB_PATH}")
    print(f"SEQ Root:      {SEQ_ROOT}")
    print(f"MP4 Root:      {MP4_ROOT}")
    print("=" * 70)

    # Validate paths
    validation = validate_paths()
    print("\nPath Validation:")
    for name, info in validation.items():
        status = "[OK] EXISTS" if info['exists'] else "[X] NOT FOUND"
        print(f"  {name:12s}: {status:15s} - {info['path']}")
    print("=" * 70)

# =============================================================================
# Run this file directly to check configuration
# =============================================================================
if __name__ == "__main__":
    print_config()

    # Additional checks
    validation = validate_paths()
    all_valid = all(info['exists'] for info in validation.values())

    if not all_valid:
        print("\n[WARNING] Some paths do not exist!")
        print("Please edit config.py and set the correct paths for your system.")
    else:
        print("\n[OK] All paths are valid!")
