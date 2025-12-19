"""
Database Repository Layer
Provides abstracted access to the ScalpelLab SQLite database.
"""

import sqlite3
import pandas as pd
from contextlib import contextmanager
from typing import List, Dict, Any, Optional
from scalpellab.core.config import settings

class Repository:
    """Handles all database interactions."""

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or str(settings.DB_PATH)

    @contextmanager
    def _connect(self):
        """Internal context manager for database connections."""
        conn = sqlite3.connect(self.db_path)
        try:
            yield conn
        finally:
            conn.commit()
            conn.close()

    def list_tables(self) -> List[str]:
        """Return all non-system table names."""
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT name FROM sqlite_master
                WHERE type='table' AND name NOT LIKE 'sqlite_%'
                ORDER BY name;
            """)
            return [r[0] for r in cur.fetchall()]

    def list_views(self) -> List[str]:
        """Return all view names."""
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT name FROM sqlite_master
                WHERE type='view'
                ORDER BY name;
            """)
            return [r[0] for r in cur.fetchall()]

    def get_table_schema(self, table_name: str) -> pd.DataFrame:
        """Return PRAGMA schema info for a table."""
        with self._connect() as conn:
            return pd.read_sql_query(f"PRAGMA table_info({table_name});", conn)

    def load_table(self, table_name: str) -> pd.DataFrame:
        """Load a whole table into a pandas DataFrame."""
        with self._connect() as conn:
            try:
                return pd.read_sql_query(f"SELECT * FROM {table_name}", conn)
            except Exception:
                return pd.DataFrame()

    def get_next_pk(self, table_name: str, pk_column: str) -> int:
        """Get the next available integer primary key."""
        try:
            with self._connect() as conn:
                cur = conn.cursor()
                cur.execute(f"SELECT MAX({pk_column}) FROM {table_name}")
                result = cur.fetchone()[0]
                return (result + 1) if result is not None else 1
        except Exception:
            return 1

    def insert_row(self, table_name: str, data: Dict[str, Any]):
        """Insert a row into a table."""
        with self._connect() as conn:
            cur = conn.cursor()
            keys = ",".join(data.keys())
            qmarks = ",".join(["?"] * len(data))
            cur.execute(
                f"INSERT INTO {table_name} ({keys}) VALUES ({qmarks})",
                tuple(data.values()),
            )

    def delete_row(self, table_name: str, pk_conditions: Dict[str, Any]):
        """Delete a row based on primary key conditions."""
        with self._connect() as conn:
            cur = conn.cursor()
            where_clause = " AND ".join([f"{k} = ?" for k in pk_conditions.keys()])
            cur.execute(
                f"DELETE FROM {table_name} WHERE {where_clause}",
                tuple(pk_conditions.values())
            )

    def fetch_camera_stats(self, table: str, cameras: List[str]) -> Dict[str, Dict[int, int]]:
        """Fetch camera statistics (Present/Missing)."""
        stats = {cam: {1: 0, 2: 0} for cam in cameras}
        with self._connect() as conn:
            cur = conn.cursor()
            placeholders = ','.join(['?'] * len(cameras))
            cur.execute(
                f"SELECT camera_name, size_mb FROM {table} WHERE camera_name IN ({placeholders})",
                cameras
            )
            for camera_name, size_mb in cur.fetchall():
                status = 1 if size_mb is not None else 2
                if camera_name in stats:
                    stats[camera_name][status] += 1
        return stats

    def create_tables(self):
        """Create required tables if they don't exist."""
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute("""
                CREATE TABLE IF NOT EXISTS "seq_status" (
                    recording_date TEXT NOT NULL,
                    case_no INTEGER NOT NULL,
                    camera_name TEXT NOT NULL,
                    size_mb INTEGER,
                    PRIMARY KEY (recording_date, case_no, camera_name)
                );
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS "mp4_status" (
                    recording_date TEXT NOT NULL,
                    case_no INTEGER NOT NULL,
                    camera_name TEXT NOT NULL,
                    size_mb INTEGER,
                    duration_minutes REAL,
                    pre_black_segment REAL,
                    post_black_segment REAL,
                    PRIMARY KEY (recording_date, case_no, camera_name)
                );
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS "recording_details" (
                    recording_date TEXT NOT NULL,
                    case_no INTEGER NOT NULL,
                    signature_time TEXT,
                    anesthesiology_key INTEGER,
                    months_anesthetic_recording INTEGER,
                    anesthetic_attending TEXT,
                    PRIMARY KEY (recording_date, case_no)
                );
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS "anesthesiology" (
                    anesthesiology_key INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT,
                    code TEXT,
                    anesthesiology_start_date TEXT,
                    grade_a_date TEXT
                );
            """)
