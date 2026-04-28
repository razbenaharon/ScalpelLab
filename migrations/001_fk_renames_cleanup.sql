-- ============================================================================
-- Migration 001: FK direction flip, code-column renames, orphan cleanup
-- Target: SQLite
-- Run with:  sqlite3 ScalpelDatabase.sqlite < migrations/001_fk_renames_cleanup.sql
--
-- Back up the database file before running this script.
-- The script is idempotent only in the success case; partial runs leave the
-- DB in a consistent state because everything is wrapped in a transaction.
-- ============================================================================

PRAGMA foreign_keys = OFF;
BEGIN TRANSACTION;

-- ---------------------------------------------------------------------------
-- Task 3 (part A): clean orphans in child tables BEFORE adding the new FKs.
-- The 97 "orphan" rows in recording_details are NOT deleted: flipping the FK
-- direction (Task 1) reclassifies them as legitimate parent rows.
-- ---------------------------------------------------------------------------

-- 32 rows: seq_status keys with no matching recording_details row
DELETE FROM seq_status
 WHERE (recording_date, case_no) NOT IN (
       SELECT recording_date, case_no FROM recording_details);

-- 36 rows: mp4_status keys with no matching recording_details row
DELETE FROM mp4_status
 WHERE (recording_date, case_no) NOT IN (
       SELECT recording_date, case_no FROM recording_details);

-- 3 rows: mp4_times keys with no matching recording_details row
DELETE FROM mp4_times
 WHERE (recording_date, case_no) NOT IN (
       SELECT recording_date, case_no FROM recording_details);

-- ---------------------------------------------------------------------------
-- Task 1 + Task 2 (part A): rebuild recording_details
--   - drop the inverted FK (recording_details -> analysis_information)
--   - rename column code -> case_code
--   - keep the FK on anesthesiology_key
--
-- legacy_alter_table=ON so child tables (seq_status, mp4_status, mp4_times)
-- keep their FKs pointing at "recording_details" by name during the swap.
-- (Per https://www.sqlite.org/lang_altertable.html#otheralter, procedure 7.)
-- ---------------------------------------------------------------------------
PRAGMA legacy_alter_table = ON;

ALTER TABLE recording_details RENAME TO recording_details__old;

CREATE TABLE recording_details
(
    recording_date              TEXT    NOT NULL,
    case_no                     INTEGER NOT NULL,
    signature_time              TEXT,
    case_code                   TEXT,
    anesthesiology_key          INTEGER REFERENCES anesthesiology(anesthesiology_key),
    months_anesthetic_recording INTEGER,
    anesthetic_attending        TEXT,
    PRIMARY KEY (recording_date, case_no)
);

INSERT INTO recording_details
    (recording_date, case_no, signature_time, case_code,
     anesthesiology_key, months_anesthetic_recording, anesthetic_attending)
SELECT
     recording_date, case_no, signature_time, code,
     anesthesiology_key, months_anesthetic_recording, anesthetic_attending
  FROM recording_details__old;

DROP TABLE recording_details__old;

PRAGMA legacy_alter_table = OFF;

-- ---------------------------------------------------------------------------
-- Task 3 (part B): clean orphans in analysis_information.
-- After flipping the FK direction, analysis_information becomes the child;
-- 3 rows (2025-09-15/1, 2025-10-05/1, 2025-10-12/1) have no parent in
-- recording_details and must be removed before adding the FK.
-- ---------------------------------------------------------------------------
DELETE FROM analysis_information
 WHERE (recording_date, case_no) NOT IN (
       SELECT recording_date, case_no FROM recording_details);

-- ---------------------------------------------------------------------------
-- Task 1 (part B): rebuild analysis_information with the correct FK direction
-- (analysis_information -> recording_details).
-- ---------------------------------------------------------------------------
PRAGMA legacy_alter_table = ON;

ALTER TABLE analysis_information RENAME TO analysis_information__old;

CREATE TABLE analysis_information
(
    recording_date TEXT,
    case_no        INTEGER,
    label_by       TEXT,
    PRIMARY KEY (recording_date, case_no),
    FOREIGN KEY (recording_date, case_no)
        REFERENCES recording_details (recording_date, case_no)
);

INSERT INTO analysis_information (recording_date, case_no, label_by)
SELECT recording_date, case_no, label_by
  FROM analysis_information__old;

DROP TABLE analysis_information__old;

PRAGMA legacy_alter_table = OFF;

-- ---------------------------------------------------------------------------
-- Task 2 (part B): rename anesthesiology.code -> staff_code.
-- RENAME COLUMN (SQLite >= 3.25) auto-updates the cur_seniority view that
-- selects a.code, since legacy_alter_table is OFF.
-- ---------------------------------------------------------------------------
ALTER TABLE anesthesiology RENAME COLUMN code TO staff_code;

-- ---------------------------------------------------------------------------
-- Verify integrity before commit. If this returns any rows, the script will
-- still commit; review and roll forward as needed.
-- ---------------------------------------------------------------------------
PRAGMA foreign_key_check;

COMMIT;
PRAGMA foreign_keys = ON;
