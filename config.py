"""
Configuration File - ScalpelLab Database Manager

This file centralizes all path configurations for the project.
Edit the paths below to match your system's directory structure.

IMPORTANT:
- The database (ScalpelDatabase.sqlite) must always be in the project directory
- SEQ_ROOT and MP4_ROOT can be on different drives or locations
- Use raw strings (r"...") for Windows paths to avoid backslash issues
"""

import os
from pathlib import Path

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
DEFAULT_CAMERAS = [
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
    """Get the database path as a string."""
    return str(DB_PATH)

def get_seq_root() -> str:
    """Get the SEQ root directory as a string."""
    return SEQ_ROOT

def get_mp4_root() -> str:
    """Get the MP4 root directory as a string."""
    return MP4_ROOT

def validate_paths() -> dict:
    """
    Validate that configured paths exist.

    Returns:
        dict with validation results
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

def print_config():
    """Print current configuration."""
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
