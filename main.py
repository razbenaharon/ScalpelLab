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
from scripts.helpers.sql_to_path import get_paths

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








# Batch video redaction (RTX A2000 optimized: 6 workers)
__import__('subprocess').run(["python", "scripts/batch_blacken.py", r"C:\Users\user\Desktop\blacken\times.xlsx", r"C:\Users\user\Desktop\blacken\output", "8a"])





