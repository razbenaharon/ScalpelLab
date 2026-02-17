# ScalpelLab Database Schema Documentation

## Overview

The ScalpelLab project uses a SQLite database (`ScalpelDatabase.sqlite`) to manage:
- Personnel records (anesthesiology residents and attendings)
- Case metadata (recording dates, case numbers, signatures)
- Video file tracking (MP4 and SEQ formats)
- Case timing information (for privacy redaction)
- Analysis labeling metadata

---

## Entity-Relationship Diagram

```
┌─────────────────────────┐
│  anesthesiology         │
├─────────────────────────┤
│ PK anesthesiology_key   │
│    name                 │◄───────┐
│    code                 │        │
│    anesthesiology_start_│        │
│    grade_a_date         │        │
└─────────────────────────┘        │
                                   │
                                   │ FK
┌─────────────────────────┐        │
│  recording_details      │        │
├─────────────────────────┤        │
│ PK recording_date       │◄───────┼───────┐
│ PK case_no              │◄───────┼───┐   │
│    signature_time       │        │   │   │
│ FK anesthesiology_key   ├────────┘   │   │
│    months_anesthetic_   │            │   │
│    anesthetic_attending │            │   │
└─────────────────────────┘            │   │
                                       │   │
┌─────────────────────────┐            │   │
│  mp4_status             │            │   │
├─────────────────────────┤            │   │
│ PK recording_date       ├────────────┘   │
│ PK case_no              ├──────────────┐ │
│ PK camera_name          │              │ │
│    size_mb              │              │ │
│    duration_minutes     │              │ │
│    pre_black_segment    │              │ │
│    post_black_segment   │              │ │
│    path                 │              │ │
└─────────────────────────┘              │ │
                                         │ │
┌─────────────────────────┐              │ │
│  seq_status             │              │ │
├─────────────────────────┤              │ │
│ PK recording_date       ├──────────────┘ │
│ PK case_no              ├────────────────┤
│ PK camera_name          │                │
│    size_mb              │                │
│    path                 │                │
└─────────────────────────┘                │
                                           │
┌─────────────────────────┐                │
│  mp4_times              │                │
├─────────────────────────┤                │
│ PK recording_date       ├────────────────┤
│ PK case_no              ├────────────────┤
│    start_1, end_1       │                │
│    start_2, end_2       │                │
│    start_3, end_3       │                │
└─────────────────────────┘                │
                                           │
┌─────────────────────────┐                │
│  analysis_information   │                │
├─────────────────────────┤                │
│ PK recording_date       ├────────────────┘
│ PK case_no              ├──────────────────┐
│    label_by             │                  │
└─────────────────────────┘                  │
```

---

## Table Definitions

### 1. `anesthesiology` - Personnel Roster

**Purpose**: Tracks anesthesiology residents and attendings with their training timeline.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `anesthesiology_key` | INTEGER | PRIMARY KEY, AUTOINCREMENT | Unique identifier for each anesthesiologist |
| `name` | TEXT | NOT NULL | Full name of the anesthesiologist |
| `code` | TEXT | UNIQUE, NOT NULL | Auto-generated code: FirstInitial + LastInitial + YYMM (e.g., MK1510) |
| `anesthesiology_start_date` | TEXT | NOT NULL | Training start date (YYYY-MM-DD format) |
| `grade_a_date` | TEXT | NULLABLE | Date promoted to Attending (NULL if still resident) |

**Example Data**:
```sql
INSERT INTO anesthesiology (name, code, anesthesiology_start_date, grade_a_date)
VALUES ('Maria Kobzeva', 'MK1510', '2015-10-01', '2020-10-01');
```

**Code Generation Logic**:
- Format: `{FirstInitial}{LastInitial}{YY}{MM}`
- Example: Maria Kobzeva starting October 2015 → `MK1510`
- Implementation: `app/pages/1_Database.py:generate_code()`

---

### 2. `recording_details` - Authoritative Case Registry

**Purpose**: Master record for each surgical case recording, with auto-calculated experience metrics.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `recording_date` | TEXT | PRIMARY KEY | Recording date (YYYY-MM-DD format) |
| `case_no` | INTEGER | PRIMARY KEY | Case number for the day (1-based) |
| `signature_time` | TEXT | NULLABLE | Timestamp of case validation/signature |
| `anesthesiology_key` | INTEGER | FOREIGN KEY → anesthesiology | Anesthesiologist assigned to this case |
| `months_anesthetic_recording` | INTEGER | AUTO-CALCULATED | Months of experience at recording time |
| `anesthetic_attending` | TEXT | AUTO-CALCULATED | 'A' (Attending) or 'R' (Resident) |

**Composite Primary Key**: `(recording_date, case_no)`

**Database Triggers** (Auto-Calculations):

```sql
-- Trigger 1: Calculate months of experience
CREATE TRIGGER calculate_months
AFTER INSERT ON recording_details
FOR EACH ROW
BEGIN
  UPDATE recording_details
  SET months_anesthetic_recording =
    CAST((julianday(NEW.recording_date) - julianday(a.anesthesiology_start_date)) / 30 AS INTEGER)
  FROM anesthesiology a
  WHERE a.anesthesiology_key = NEW.anesthesiology_key
    AND recording_date = NEW.recording_date
    AND case_no = NEW.case_no;
END;

-- Trigger 2: Determine attending vs resident status
CREATE TRIGGER determine_status
AFTER UPDATE OF months_anesthetic_recording ON recording_details
FOR EACH ROW
BEGIN
  UPDATE recording_details
  SET anesthetic_attending = CASE
    WHEN NEW.months_anesthetic_recording >= 60 THEN 'A'
    ELSE 'R'
  END
  WHERE recording_date = NEW.recording_date
    AND case_no = NEW.case_no;
END;
```

**Business Rules**:
- **Resident (R)**: < 60 months of experience (< 5 years)
- **Attending (A)**: ≥ 60 months of experience (≥ 5 years)

**Example Data**:
```sql
INSERT INTO recording_details (recording_date, case_no, signature_time, anesthesiology_key)
VALUES ('2023-05-15', 1, '2023-05-15 14:32:00', 3);
-- Auto-calculates: months_anesthetic_recording = 45, anesthetic_attending = 'R'
```

---

### 3. `mp4_status` - Exported Video File Tracking

**Purpose**: Tracks converted MP4 video files for each camera and case, including redaction metadata.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `recording_date` | TEXT | PRIMARY KEY | Recording date (YYYY-MM-DD format) |
| `case_no` | INTEGER | PRIMARY KEY | Case number (1-based) |
| `camera_name` | TEXT | PRIMARY KEY | Camera identifier (e.g., "Monitor", "General_3") |
| `size_mb` | INTEGER | NULLABLE | File size in megabytes (largest file if duplicates exist) |
| `duration_minutes` | REAL | NULLABLE | Video duration extracted via ffprobe |
| `pre_black_segment` | REAL | NULLABLE | Minutes of black screen before first case |
| `post_black_segment` | REAL | NULLABLE | Minutes of black screen after last case |
| `path` | VARCHAR | NULLABLE | Full filesystem path to the MP4 file |

**Composite Primary Key**: `(recording_date, case_no, camera_name)`

**Foreign Key**: `(recording_date, case_no)` → `recording_details(recording_date, case_no)`

**Status Inference Logic** (in Streamlit app):
```python
def get_status(size_mb):
    if size_mb is None or pd.isna(size_mb):
        return "Missing"
    elif size_mb >= 200:
        return "Present"
    elif size_mb < 200:
        return "Incomplete"
```

**File Path Pattern**:
```
{MP4_ROOT}/DATA_{YY}-{MM}-{DD}/Case{N}/{CameraName}/*.mp4
Example: F:\Room_8_Data\Recordings\DATA_23-05-15\Case3\Monitor\recording_001.mp4
```

**Redaction Fields**:
- `pre_black_segment`: Calculated by `scripts/5_batch_blacken.py`
- `post_black_segment`: Updated after redaction process
- Used for privacy compliance reporting

**Example Data**:
```sql
INSERT INTO mp4_status (recording_date, case_no, camera_name, size_mb, duration_minutes, path)
VALUES ('2023-05-15', 3, 'Monitor', 342, 58.5,
        'F:\Room_8_Data\Recordings\DATA_23-05-15\Case3\Monitor\recording_001.mp4');
```

---

### 4. `seq_status` - Raw Sequence File Tracking

**Purpose**: Tracks original SEQ video files from recording hardware.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `recording_date` | TEXT | PRIMARY KEY | Recording date (YYYY-MM-DD format) |
| `case_no` | INTEGER | PRIMARY KEY | Case number (1-based) |
| `camera_name` | TEXT | PRIMARY KEY | Camera identifier |
| `size_mb` | INTEGER | NULLABLE | File size in megabytes |
| `path` | TEXT | NULLABLE | Relative path to the SEQ file (from Sequence_Backup) |

**Composite Primary Key**: `(recording_date, case_no, camera_name)`

**Foreign Key**: `(recording_date, case_no)` → `recording_details(recording_date, case_no)`

**File Path Pattern**:
```
{SEQ_ROOT}/DATA_{YY}-{MM}-{DD}/Case{N}/{CameraName}/*.seq
Example: F:\Room_8_Data\Sequence_Backup\DATA_23-05-15\Case3\Monitor\recording_001.seq
```

**Relationship with mp4_status**:
- SEQ files are the **source** for MP4 conversion
- View `cur_mp4_missing` identifies SEQ files without corresponding MP4s
- View `cur_seq_missing` identifies MP4s without SEQ backups (data loss warning)

**Example Data**:
```sql
INSERT INTO seq_status (recording_date, case_no, camera_name, size_mb, path)
VALUES ('2023-05-15', 3, 'Monitor', 487, 'Sequence_Backup\DATA_23-05-15\Case3\Monitor\recording_001.seq');
```

---

### 5. `mp4_times` - Case Timing Information for Redaction

**Purpose**: Stores start/end times for up to 3 cases per recording session, used for privacy redaction.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `recording_date` | TEXT | PRIMARY KEY | Recording date (YYYY-MM-DD format) |
| `case_no` | INTEGER | PRIMARY KEY | Case number (1-based) |
| `start_1` | TEXT/REAL | NULLABLE | Case 1 start time (HH:MM:SS or seconds) |
| `end_1` | TEXT/REAL | NULLABLE | Case 1 end time |
| `start_2` | TEXT/REAL | NULLABLE | Case 2 start time |
| `end_2` | TEXT/REAL | NULLABLE | Case 2 end time |
| `start_3` | TEXT/REAL | NULLABLE | Case 3 start time |
| `end_3` | TEXT/REAL | NULLABLE | Case 3 end time |

**Composite Primary Key**: `(recording_date, case_no)`

**Foreign Key**: `(recording_date, case_no)` → `recording_details(recording_date, case_no)`

**Data Format** (Flexible):
- **Text Format**: `"00:15:30"` (HH:MM:SS)
- **Numeric Format**: `930.0` (seconds since 00:00:00)
- Conversion handled by `scripts/5_batch_blacken.py:parse_time()`

**Redaction Logic** (from `5_batch_blacken.py`):

```python
# During case time ranges
DURING_CASE = [(start_1, end_1), (start_2, end_2), (start_3, end_3)]

# Redaction masks:
# - DURING CASE: Small corner box (1/3 width × 1/2 height, bottom-right)
# - OUTSIDE CASE: Full screen black

# Pre-segment calculation (first case):
pre_black_segment = start_1 - 0  # From video start to first case

# Between cases:
gap_time = start_2 - end_1
# Split gap equally (half blacked each side)

# Post-segment calculation (last case):
video_duration = get_video_duration(video_path)
max_post_duration = min(video_duration, end_last_case + 3600)  # Max 1 hour after
post_black_segment = max_post_duration - end_last_case
```

**Example Data**:
```sql
INSERT INTO mp4_times (recording_date, case_no, start_1, end_1, start_2, end_2)
VALUES ('2023-05-15', 3, '00:10:00', '00:45:30', '00:50:00', '01:25:15');
```

**Usage in Batch Redaction**:
```sql
SELECT ms.path, mt.start_1, mt.end_1, mt.start_2, mt.end_2, mt.start_3, mt.end_3
FROM mp4_status ms
INNER JOIN mp4_times mt
  ON ms.recording_date = mt.recording_date
  AND ms.case_no = mt.case_no
WHERE ms.size_mb >= 200;  -- Only process complete files
```

---

### 6. `analysis_information` - Labeling Metadata

**Purpose**: Tracks who labeled/reviewed each case for quality control.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `recording_date` | TEXT | PRIMARY KEY | Recording date (YYYY-MM-DD format) |
| `case_no` | INTEGER | PRIMARY KEY | Case number (1-based) |
| `label_by` | TEXT | NULLABLE | Name or ID of person who labeled this case |

**Composite Primary Key**: `(recording_date, case_no)`

**Foreign Key**: `(recording_date, case_no)` → `recording_details(recording_date, case_no)`

**Example Data**:
```sql
INSERT INTO analysis_information (recording_date, case_no, label_by)
VALUES ('2023-05-15', 3, 'Dr. Smith');
```

---

## Database Views

### View 1: `cur_mp4_missing`

**Purpose**: Lists cases where SEQ files exist but MP4 files are missing (conversion needed).

```sql
CREATE VIEW cur_mp4_missing AS
SELECT
    s.recording_date,
    s.case_no,
    s.camera_name,
    s.size_mb AS seq_size_mb,
    m.size_mb AS mp4_size_mb
FROM seq_status s
LEFT JOIN mp4_status m
    ON s.recording_date = m.recording_date
    AND s.case_no = m.case_no
    AND s.camera_name = m.camera_name
WHERE m.size_mb IS NULL OR m.size_mb < 100;
```

**Use Case**: Identify videos that need conversion
```bash
# Workflow:
1. Query: SELECT * FROM cur_mp4_missing;
2. Run: python scripts/3_seq_to_mp4_convert.py
3. Re-query to verify conversions
```

---

### View 2: `cur_seq_missing`

**Purpose**: Lists cases where MP4 files exist but SEQ files are missing (data loss warning).

```sql
CREATE VIEW cur_seq_missing AS
SELECT
    m.recording_date,
    m.case_no,
    m.camera_name,
    m.size_mb AS mp4_size_mb,
    s.size_mb AS seq_size_mb
FROM mp4_status m
LEFT JOIN seq_status s
    ON m.recording_date = s.recording_date
    AND m.case_no = s.case_no
    AND m.camera_name = s.camera_name
WHERE s.size_mb IS NULL;
```

**Use Case**: Backup integrity check
```sql
-- Critical: SEQ files are master copies
-- If SEQ is missing but MP4 exists, investigate:
-- 1. Manual deletion?
-- 2. Storage failure?
-- 3. Transfer incomplete?
```

---

### View 3: `cur_seniority`

**Purpose**: Calculates current experience levels for all anesthesiologists.

```sql
CREATE VIEW cur_seniority AS
SELECT
    a.anesthesiology_key,
    a.name,
    a.code,
    a.anesthesiology_start_date,
    a.grade_a_date,
    CAST((julianday('now') - julianday(a.anesthesiology_start_date)) / 30 AS INTEGER)
        AS current_months_experience,
    CASE
        WHEN CAST((julianday('now') - julianday(a.anesthesiology_start_date)) / 30 AS INTEGER) >= 60
        THEN 'A'
        ELSE 'R'
    END AS current_status
FROM anesthesiology a;
```

**Use Case**: Current roster reporting
```sql
-- Show all current attendings:
SELECT name, current_months_experience
FROM cur_seniority
WHERE current_status = 'A'
ORDER BY current_months_experience DESC;
```

---

## Data Integrity Constraints

### Foreign Key Relationships

```sql
-- Enable foreign key enforcement (SQLite requires explicit enable)
PRAGMA foreign_keys = ON;

-- recording_details → anesthesiology
ALTER TABLE recording_details
ADD CONSTRAINT fk_recording_anesthesiology
FOREIGN KEY (anesthesiology_key)
REFERENCES anesthesiology(anesthesiology_key);

-- mp4_status → recording_details
ALTER TABLE mp4_status
ADD CONSTRAINT fk_mp4_recording
FOREIGN KEY (recording_date, case_no)
REFERENCES recording_details(recording_date, case_no);

-- seq_status → recording_details
ALTER TABLE seq_status
ADD CONSTRAINT fk_seq_recording
FOREIGN KEY (recording_date, case_no)
REFERENCES recording_details(recording_date, case_no);

-- mp4_times → recording_details
ALTER TABLE mp4_times
ADD CONSTRAINT fk_times_recording
FOREIGN KEY (recording_date, case_no)
REFERENCES recording_details(recording_date, case_no);

-- analysis_information → recording_details
ALTER TABLE analysis_information
ADD CONSTRAINT fk_analysis_recording
FOREIGN KEY (recording_date, case_no)
REFERENCES recording_details(recording_date, case_no);
```

### Unique Constraints

```sql
-- Prevent duplicate anesthesiology codes
CREATE UNIQUE INDEX idx_anesthesiology_code
ON anesthesiology(code);

-- Prevent duplicate case entries
CREATE UNIQUE INDEX idx_recording_details_pk
ON recording_details(recording_date, case_no);

-- Prevent duplicate camera entries
CREATE UNIQUE INDEX idx_mp4_status_pk
ON mp4_status(recording_date, case_no, camera_name);
```

### Check Constraints

```sql
-- Validate date formats
ALTER TABLE recording_details
ADD CONSTRAINT chk_recording_date_format
CHECK (recording_date LIKE '____-__-__');

-- Validate case numbers (positive integers)
ALTER TABLE recording_details
ADD CONSTRAINT chk_case_no_positive
CHECK (case_no > 0);

-- Validate status values
ALTER TABLE recording_details
ADD CONSTRAINT chk_anesthetic_attending
CHECK (anesthetic_attending IN ('A', 'R'));

-- Validate file sizes (non-negative)
ALTER TABLE mp4_status
ADD CONSTRAINT chk_size_mb_nonnegative
CHECK (size_mb IS NULL OR size_mb >= 0);
```

---

## Common Queries

### 1. Get all complete MP4 files for a specific date

```sql
SELECT
    recording_date,
    case_no,
    camera_name,
    size_mb,
    duration_minutes,
    path
FROM mp4_status
WHERE recording_date = '2023-05-15'
  AND size_mb >= 200
ORDER BY case_no, camera_name;
```

### 2. Find cases missing any camera angles

```sql
SELECT
    rd.recording_date,
    rd.case_no,
    COUNT(DISTINCT ms.camera_name) AS cameras_present,
    GROUP_CONCAT(ms.camera_name) AS available_cameras
FROM recording_details rd
LEFT JOIN mp4_status ms
    ON rd.recording_date = ms.recording_date
    AND rd.case_no = ms.case_no
    AND ms.size_mb >= 200
GROUP BY rd.recording_date, rd.case_no
HAVING COUNT(DISTINCT ms.camera_name) < 8  -- Expected 8 cameras
ORDER BY rd.recording_date DESC, rd.case_no;
```

### 3. Calculate total storage usage by camera

```sql
SELECT
    camera_name,
    COUNT(*) AS file_count,
    SUM(size_mb) AS total_size_mb,
    ROUND(SUM(size_mb) / 1024.0, 2) AS total_size_gb,
    AVG(size_mb) AS avg_size_mb,
    AVG(duration_minutes) AS avg_duration_min
FROM mp4_status
WHERE size_mb IS NOT NULL
GROUP BY camera_name
ORDER BY total_size_mb DESC;
```

### 4. Find residents who became attendings during recorded period

```sql
SELECT
    a.name,
    a.code,
    a.grade_a_date,
    COUNT(DISTINCT rd.recording_date || '-' || rd.case_no) AS total_cases,
    SUM(CASE WHEN rd.anesthetic_attending = 'R' THEN 1 ELSE 0 END) AS cases_as_resident,
    SUM(CASE WHEN rd.anesthetic_attending = 'A' THEN 1 ELSE 0 END) AS cases_as_attending
FROM anesthesiology a
JOIN recording_details rd ON a.anesthesiology_key = rd.anesthesiology_key
WHERE a.grade_a_date IS NOT NULL
  AND a.grade_a_date BETWEEN
      (SELECT MIN(recording_date) FROM recording_details)
      AND
      (SELECT MAX(recording_date) FROM recording_details)
GROUP BY a.anesthesiology_key
ORDER BY a.grade_a_date;
```

### 5. Get redaction statistics summary

```sql
SELECT
    camera_name,
    COUNT(*) AS redacted_files,
    AVG(pre_black_segment) AS avg_pre_black_min,
    AVG(post_black_segment) AS avg_post_black_min,
    AVG(duration_minutes - pre_black_segment - post_black_segment) AS avg_visible_min
FROM mp4_status
WHERE pre_black_segment IS NOT NULL
  AND post_black_segment IS NOT NULL
GROUP BY camera_name
ORDER BY camera_name;
```

### 6. Find incomplete conversions (large SEQ, small MP4)

```sql
SELECT
    s.recording_date,
    s.case_no,
    s.camera_name,
    s.size_mb AS seq_size_mb,
    m.size_mb AS mp4_size_mb,
    ROUND((m.size_mb * 1.0 / s.size_mb) * 100, 2) AS conversion_ratio_percent
FROM seq_status s
JOIN mp4_status m
    ON s.recording_date = m.recording_date
    AND s.case_no = m.case_no
    AND s.camera_name = m.camera_name
WHERE s.size_mb >= 200  -- Valid SEQ file
  AND m.size_mb < (s.size_mb * 0.5)  -- MP4 is < 50% of SEQ size (suspicious)
ORDER BY conversion_ratio_percent ASC;
```

---

## Database Maintenance

### Backup Strategy

```bash
# Daily automated backup
sqlite3 ScalpelDatabase.sqlite ".backup 'backups/ScalpelDatabase_$(date +%Y%m%d).sqlite'"

# Weekly full backup with compression
tar -czf "backups/weekly/ScalpelDB_$(date +%Y%m%d).tar.gz" ScalpelDatabase.sqlite

# Verify backup integrity
sqlite3 "backups/ScalpelDatabase_$(date +%Y%m%d).sqlite" "PRAGMA integrity_check;"
```

### Vacuum and Optimize

```sql
-- Rebuild database to reclaim space (run monthly)
VACUUM;

-- Update statistics for query optimizer
ANALYZE;

-- Check database integrity
PRAGMA integrity_check;

-- Show database size
SELECT page_count * page_size AS size_bytes,
       ROUND((page_count * page_size) / 1024.0 / 1024.0, 2) AS size_mb
FROM pragma_page_count(), pragma_page_size();
```

### Index Maintenance

```sql
-- Create performance indexes for common queries
CREATE INDEX IF NOT EXISTS idx_mp4_status_date
ON mp4_status(recording_date);

CREATE INDEX IF NOT EXISTS idx_mp4_status_size
ON mp4_status(size_mb);

CREATE INDEX IF NOT EXISTS idx_recording_details_key
ON recording_details(anesthesiology_key);

-- List all indexes
SELECT name, tbl_name, sql
FROM sqlite_master
WHERE type = 'index'
  AND tbl_name NOT LIKE 'sqlite_%'
ORDER BY tbl_name, name;
```

---

## Migration Scripts

### Add New Column Example

```sql
-- Add new column with default value
ALTER TABLE mp4_status
ADD COLUMN bitrate_kbps INTEGER DEFAULT NULL;

-- Populate with data
UPDATE mp4_status
SET bitrate_kbps = (size_mb * 1024 * 8) / (duration_minutes * 60)
WHERE duration_minutes > 0;
```

### Schema Version Tracking

```sql
-- Create version tracking table
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY,
    applied_date TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    description TEXT
);

-- Record migrations
INSERT INTO schema_version (version, description)
VALUES (1, 'Initial schema with 6 tables');

INSERT INTO schema_version (version, description)
VALUES (2, 'Added pre_black_segment and post_black_segment to mp4_status');
```

---

## Performance Considerations

### Expected Row Counts

| Table | Estimated Rows | Growth Rate |
|-------|----------------|-------------|
| `anesthesiology` | 50-100 | ~5-10 per year |
| `recording_details` | 5,000-10,000 | ~200-500 per month |
| `mp4_status` | 40,000-80,000 | ~1,600-4,000 per month (8 cameras) |
| `seq_status` | 40,000-80,000 | Same as mp4_status |
| `mp4_times` | 5,000-10,000 | Same as recording_details |
| `analysis_information` | 2,000-5,000 | ~100-200 per month |

### Query Performance Tips

```sql
-- Use EXPLAIN QUERY PLAN to analyze queries
EXPLAIN QUERY PLAN
SELECT * FROM mp4_status WHERE recording_date = '2023-05-15';

-- Create covering indexes for frequent queries
CREATE INDEX idx_mp4_status_date_camera
ON mp4_status(recording_date, case_no, camera_name, size_mb);

-- Use compound indexes for JOIN queries
CREATE INDEX idx_recording_details_composite
ON recording_details(recording_date, case_no, anesthesiology_key);
```

---

## Security & Privacy

### PHI (Protected Health Information) Considerations

The database contains **Protected Health Information** under HIPAA:
- Anesthesiologist names and dates
- Case recording dates and times
- Video file paths

### Recommended Security Measures

```bash
# 1. Encrypt database file at rest
# Use SQLCipher or full-disk encryption

# 2. Restrict file permissions (Linux/Mac)
chmod 600 ScalpelDatabase.sqlite

# 3. Require authentication for Streamlit app
# Add to app/app.py:
import streamlit_authenticator as stauth

# 4. Audit access logs
sqlite3 ScalpelDatabase.sqlite "CREATE TABLE access_log (
    timestamp TEXT DEFAULT CURRENT_TIMESTAMP,
    user TEXT,
    action TEXT,
    table_name TEXT
);"
```

### Data Anonymization for Research

```sql
-- Create anonymized view for research exports
CREATE VIEW research_data_anonymized AS
SELECT
    SUBSTR(recording_date, 1, 7) AS recording_month,  -- YYYY-MM only
    case_no,
    camera_name,
    size_mb,
    duration_minutes,
    rd.anesthetic_attending,
    rd.months_anesthetic_recording
FROM mp4_status ms
JOIN recording_details rd
    ON ms.recording_date = rd.recording_date
    AND ms.case_no = rd.case_no
WHERE ms.size_mb >= 200;

-- Export anonymized data
.mode csv
.output research_export.csv
SELECT * FROM research_data_anonymized;
.output stdout
```

---

## Troubleshooting

### Common Issues

#### Issue 1: Foreign Key Constraint Violations

```sql
-- Check orphaned records
SELECT 'mp4_status orphans:' AS issue, COUNT(*) AS count
FROM mp4_status ms
LEFT JOIN recording_details rd
    ON ms.recording_date = rd.recording_date
    AND ms.case_no = rd.case_no
WHERE rd.recording_date IS NULL;

-- Fix by adding missing parent records
INSERT OR IGNORE INTO recording_details (recording_date, case_no)
SELECT DISTINCT recording_date, case_no
FROM mp4_status
WHERE (recording_date, case_no) NOT IN (
    SELECT recording_date, case_no FROM recording_details
);
```

#### Issue 2: Duplicate Camera Entries

```sql
-- Find duplicates
SELECT recording_date, case_no, camera_name, COUNT(*) AS count
FROM mp4_status
GROUP BY recording_date, case_no, camera_name
HAVING COUNT(*) > 1;

-- Keep only the largest file
DELETE FROM mp4_status
WHERE rowid NOT IN (
    SELECT MAX(rowid)
    FROM mp4_status
    GROUP BY recording_date, case_no, camera_name
);
```

#### Issue 3: Database Locked Errors

```bash
# Check for active connections
lsof ScalpelDatabase.sqlite  # Linux/Mac
handle.exe ScalpelDatabase.sqlite  # Windows

# Kill the process holding the lock, then:
sqlite3 ScalpelDatabase.sqlite "PRAGMA journal_mode=WAL;"
```

---

## Appendix: SQL Schema Creation Script

```sql
-- Full database schema creation script
-- Run this to recreate the database from scratch

PRAGMA foreign_keys = ON;

-- Table 1: anesthesiology
CREATE TABLE IF NOT EXISTS anesthesiology (
    anesthesiology_key INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    code TEXT UNIQUE NOT NULL,
    anesthesiology_start_date TEXT NOT NULL,
    grade_a_date TEXT
);

-- Table 2: recording_details
CREATE TABLE IF NOT EXISTS recording_details (
    recording_date TEXT NOT NULL,
    case_no INTEGER NOT NULL,
    signature_time TEXT,
    anesthesiology_key INTEGER,
    months_anesthetic_recording INTEGER,
    anesthetic_attending TEXT,
    PRIMARY KEY (recording_date, case_no),
    FOREIGN KEY (anesthesiology_key) REFERENCES anesthesiology(anesthesiology_key)
);

-- Table 3: mp4_status
CREATE TABLE IF NOT EXISTS mp4_status (
    recording_date TEXT NOT NULL,
    case_no INTEGER NOT NULL,
    camera_name TEXT NOT NULL,
    size_mb INTEGER,
    duration_minutes REAL,
    pre_black_segment REAL,
    post_black_segment REAL,
    path VARCHAR,
    PRIMARY KEY (recording_date, case_no, camera_name),
    FOREIGN KEY (recording_date, case_no) REFERENCES recording_details(recording_date, case_no)
);

-- Table 4: seq_status
CREATE TABLE IF NOT EXISTS seq_status (
    recording_date TEXT NOT NULL,
    case_no INTEGER NOT NULL,
    camera_name TEXT NOT NULL,
    size_mb INTEGER,
    path TEXT,
    PRIMARY KEY (recording_date, case_no, camera_name),
    FOREIGN KEY (recording_date, case_no) REFERENCES recording_details(recording_date, case_no)
);

-- Table 5: mp4_times
CREATE TABLE IF NOT EXISTS mp4_times (
    recording_date TEXT NOT NULL,
    case_no INTEGER NOT NULL,
    start_1 TEXT,
    end_1 TEXT,
    start_2 TEXT,
    end_2 TEXT,
    start_3 TEXT,
    end_3 TEXT,
    PRIMARY KEY (recording_date, case_no),
    FOREIGN KEY (recording_date, case_no) REFERENCES recording_details(recording_date, case_no)
);

-- Table 6: analysis_information
CREATE TABLE IF NOT EXISTS analysis_information (
    recording_date TEXT NOT NULL,
    case_no INTEGER NOT NULL,
    label_by TEXT,
    PRIMARY KEY (recording_date, case_no),
    FOREIGN KEY (recording_date, case_no) REFERENCES recording_details(recording_date, case_no)
);

-- Create indexes
CREATE INDEX IF NOT EXISTS idx_mp4_status_date ON mp4_status(recording_date);
CREATE INDEX IF NOT EXISTS idx_mp4_status_size ON mp4_status(size_mb);
CREATE INDEX IF NOT EXISTS idx_recording_details_key ON recording_details(anesthesiology_key);

-- Create views
CREATE VIEW IF NOT EXISTS cur_mp4_missing AS
SELECT
    s.recording_date,
    s.case_no,
    s.camera_name,
    s.size_mb AS seq_size_mb,
    m.size_mb AS mp4_size_mb
FROM seq_status s
LEFT JOIN mp4_status m
    ON s.recording_date = m.recording_date
    AND s.case_no = m.case_no
    AND s.camera_name = m.camera_name
WHERE m.size_mb IS NULL OR m.size_mb < 100;

CREATE VIEW IF NOT EXISTS cur_seq_missing AS
SELECT
    m.recording_date,
    m.case_no,
    m.camera_name,
    m.size_mb AS mp4_size_mb,
    s.size_mb AS seq_size_mb
FROM mp4_status m
LEFT JOIN seq_status s
    ON m.recording_date = s.recording_date
    AND m.case_no = s.case_no
    AND m.camera_name = s.camera_name
WHERE s.size_mb IS NULL;

CREATE VIEW IF NOT EXISTS cur_seniority AS
SELECT
    a.anesthesiology_key,
    a.name,
    a.code,
    a.anesthesiology_start_date,
    a.grade_a_date,
    CAST((julianday('now') - julianday(a.anesthesiology_start_date)) / 30 AS INTEGER)
        AS current_months_experience,
    CASE
        WHEN CAST((julianday('now') - julianday(a.anesthesiology_start_date)) / 30 AS INTEGER) >= 60
        THEN 'A'
        ELSE 'R'
    END AS current_status
FROM anesthesiology a;
```

---

## Document Version

- **Version**: 1.0
- **Last Updated**: 2026-01-06
- **Author**: ScalpelLab Documentation Team
- **Database Version**: Compatible with ScalpelDatabase.sqlite schema v2
