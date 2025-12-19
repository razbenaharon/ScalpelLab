import pytest
import os
import sqlite3
from pathlib import Path
from scalpellab.db.repository import Repository
from scalpellab.services.scanner import ScannerService
from scalpellab.core.config import settings

@pytest.fixture
def mock_filesystem(tmp_path):
    """Create a mock directory structure for scanning."""
    # SEQ Root
    seq_root = tmp_path / "seq"
    case_dir = seq_root / "DATA_25-12-19" / "Case1" / "Monitor"
    case_dir.mkdir(parents=True)
    (case_dir / "test.seq").write_text("dummy content")
    
    # MP4 Root
    mp4_root = tmp_path / "mp4"
    mp4_case_dir = mp4_root / "DATA_25-12-19" / "Case1" / "Monitor"
    mp4_case_dir.mkdir(parents=True)
    (mp4_case_dir / "test.mp4").write_text("dummy content")
    
    return seq_root, mp4_root

def test_scanner_integration(mock_filesystem, tmp_path):
    seq_root, mp4_root = mock_filesystem
    db_path = tmp_path / "test.sqlite"
    
    # Initialize Repo and Service
    repo = Repository(str(db_path))
    repo.create_tables()
    scanner = ScannerService(repo)
    
    # 1. Scan SEQ
    seq_updates = scanner.scan_seq(seq_root=seq_root)
    assert len(seq_updates) == 1
    assert ("2025-12-19", 1, "Monitor") in seq_updates
    
    # 2. Scan MP4 (without duration for simplicity)
    mp4_updates = scanner.scan_mp4(mp4_root=mp4_root, calculate_duration=False)
    assert len(mp4_updates) == 1
    assert ("2025-12-19", 1, "Monitor") in mp4_updates
    
    # 3. Sync to DB
    scanner.sync_to_db(seq_updates, mp4_updates)
    
    # 4. Verify DB
    df_seq = repo.load_table("seq_status")
    assert len(df_seq) == 1
    assert df_seq.iloc[0]["camera_name"] == "Monitor"
    
    df_mp4 = repo.load_table("mp4_status")
    assert len(df_mp4) == 1
    assert df_mp4.iloc[0]["camera_name"] == "Monitor"
