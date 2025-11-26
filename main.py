"""
SQL to Path Script - Example Usage

This script demonstrates how to use sql_to_path to query the database
and get file paths for MP4 or SEQ files.

Filter files directly in SQL using size_mb:
- Large files: WHERE size_mb >= 200
- Small files: WHERE size_mb < 200
- Missing files: WHERE size_mb IS NULL
- Size range: WHERE size_mb BETWEEN 100 AND 500
"""
from scripts.redact_video import redact_video
from scripts.sql_to_path import get_paths
from config import get_seq_root

# Example 1: Get MP4 files >= 200MB for Monitor cameras
print("="*60)
print("Example 1: MP4 files >= 200MB for Monitor cameras")
print("="*60)

sql_query = """
    SELECT recording_date, case_no, camera_name
    FROM mp4_status
    WHERE camera_name IN ('Monitor', 'Patient_Monitor')
    AND size_mb >= 200
    ORDER BY recording_date DESC
    LIMIT 20
"""

path = get_paths(sql_query)
print(path)
print("="*60)


redact_video(r"C:\Users\user\Desktop\blacken\Ventilator_Monitor.mp4", "0", "5")
