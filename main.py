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

paths = get_paths(sql_query)
print(f"Found {len(paths)} matching paths:")

for mp4_path in paths[:10]:  # Show first 10
    print(f"  {mp4_path}")

if len(paths) > 10:
    print(f"... and {len(paths) - 10} more")


# Example 2: Get missing MP4 files where SEQ exists
print("\n" + "="*60)
print("Example 2: Missing MP4 files where SEQ exists")
print("="*60)

sql_query2 = """
    SELECT
        m.recording_date,
        m.case_no,
        m.camera_name
    FROM mp4_status m
    INNER JOIN seq_status s
        ON m.recording_date = s.recording_date
        AND m.case_no = s.case_no
        AND m.camera_name = s.camera_name
    WHERE m.size_mb IS NULL
    AND s.size_mb IS NOT NULL
    ORDER BY m.recording_date DESC
    LIMIT 15
"""

paths2 = get_paths(sql_query2)
print(f"Found {len(paths2)} missing MP4 files:")

for mp4_path in paths2[:10]:  # Show first 10
    print(f"  {mp4_path}")

if len(paths2) > 10:
    print(f"... and {len(paths2) - 10} more")


# Example 3: Get files smaller than 200MB
print("\n" + "="*60)
print("Example 3: Files < 200MB")
print("="*60)

sql_query3 = """
    SELECT recording_date, case_no, camera_name
    FROM mp4_status
    WHERE size_mb < 200
    AND size_mb IS NOT NULL
    ORDER BY recording_date DESC
    LIMIT 15
"""

paths3 = get_paths(sql_query3)
print(f"Found {len(paths3)} files:")

for mp4_path in paths3[:10]:
    print(f"  {mp4_path}")

if len(paths3) > 10:
    print(f"... and {len(paths3) - 10} more")


# Example 4: Working with SEQ files
print("\n" + "="*60)
print("Example 4: SEQ files from Sequence_Backup directory")
print("="*60)

sql_query4 = """
    SELECT recording_date, case_no, camera_name
    FROM seq_status
    WHERE camera_name = 'General_3'
    AND size_mb >= 200
    ORDER BY recording_date DESC
    LIMIT 10
"""

# Specify SEQ root path to auto-detect .seq extension
paths4 = get_paths(
    sql_query4,
    root_path=get_seq_root()
)
print(f"Found {len(paths4)} SEQ files:")

for seq_path in paths4:
    print(f"  {seq_path}")


# Example 5: Filter by size range
print("\n" + "="*60)
print("Example 5: Files between 100-200 MB")
print("="*60)

sql_query5 = """
    SELECT recording_date, case_no, camera_name
    FROM mp4_status
    WHERE size_mb >= 100
    AND size_mb < 200
    ORDER BY size_mb DESC
    LIMIT 10
"""

paths5 = get_paths(sql_query5)
print(f"Found {len(paths5)} files in size range:")

for mp4_path in paths5:
    print(f"  {mp4_path}")


# Example 6: Get largest files using SQL ORDER BY
print("\n" + "="*60)
print("Example 6: Largest files by size")
print("="*60)

sql_query6 = """
    SELECT recording_date, case_no, camera_name
    FROM mp4_status
    WHERE camera_name = 'Monitor'
    AND size_mb IS NOT NULL
    ORDER BY size_mb DESC
    LIMIT 10
"""

paths6 = get_paths(sql_query6)
print(f"Found {len(paths6)} largest files:")

for mp4_path in paths6:
    print(f"  {mp4_path}")


print("\n" + "="*60)
print("Examples complete!")
print("="*60)



sql_query7 = """
    SELECT recording_date, case_no, camera_name
    FROM mp4_status
    WHERE camera_name = 'General_3'
    AND recording_date = '2024-11-24'
    AND case_no = 1
"""
paths7 = get_paths(sql_query7)
print(paths7[0])

print("\n" + "="*60)
print("Examples complete!")
print("="*60)

