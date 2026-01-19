"""Database utility functions for the Streamlit application.

This module provides helper functions for interacting with the SQLite database,
including connection management, table/view listing, and data loading.

All database operations use context managers to ensure proper connection handling
and automatic commits/cleanup.
"""

import sqlite3
import pandas as pd
import streamlit as st
from contextlib import contextmanager
from typing import Generator, List, Dict, Set, Tuple, Optional
import re


@contextmanager
def connect(db_path: str) -> Generator[sqlite3.Connection, None, None]:
    """Context manager for safe SQLite database connections.

    Automatically handles connection lifecycle, committing changes and closing
    the connection when the context exits. This ensures database integrity
    even if errors occur during operations.

    Args:
        db_path: Absolute or relative path to the SQLite database file.

    Yields:
        sqlite3.Connection: Active database connection object.

    Example:
        >>> with connect("ScalpelDatabase.sqlite") as conn:
        ...     cursor = conn.cursor()
        ...     cursor.execute("SELECT * FROM recording_details LIMIT 5")
        ...     results = cursor.fetchall()
    """
    conn = sqlite3.connect(db_path)
    try:
        yield conn
    finally:
        conn.commit()
        conn.close()


def list_tables(db_path: str) -> List[str]:
    """Retrieve all user-defined table names from the database.

    Queries the sqlite_master table to find all tables, excluding system
    tables (those prefixed with 'sqlite_'). Results are sorted alphabetically.

    Args:
        db_path: Path to the SQLite database file.

    Returns:
        List[str]: Sorted list of table names (excluding system tables).

    Example:
        >>> tables = list_tables("ScalpelDatabase.sqlite")
        >>> print(tables)
        ['analysis_information', 'anesthesiology', 'mp4_status', ...]
    """
    with connect(db_path) as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT name FROM sqlite_master
            WHERE type='table' AND name NOT LIKE 'sqlite_%'
            ORDER BY name;
        """)
        return [r[0] for r in cur.fetchall()]


def list_views(db_path: str) -> List[str]:
    """Retrieve all view names from the database.

    Queries the sqlite_master table to find all database views (virtual tables
    created by SELECT statements). Results are sorted alphabetically.

    Args:
        db_path: Path to the SQLite database file.

    Returns:
        List[str]: Sorted list of view names.

    Example:
        >>> views = list_views("ScalpelDatabase.sqlite")
        >>> print(views)
        ['cur_mp4_missing', 'cur_seq_missing', 'cur_seniority']
    """
    with connect(db_path) as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT name FROM sqlite_master
            WHERE type='view'
            ORDER BY name;
        """)
        return [r[0] for r in cur.fetchall()]


def get_table_schema(db_path: str, table: str) -> pd.DataFrame:
    """Retrieve schema information for a specific table.

    Uses SQLite's PRAGMA table_info command to get detailed column metadata,
    including column names, data types, nullability, default values, and
    primary key status.

    Args:
        db_path: Path to the SQLite database file.
        table: Name of the table to inspect.

    Returns:
        pd.DataFrame: Schema information with columns:
            - cid: Column ID (integer position)
            - name: Column name
            - type: Data type (TEXT, INTEGER, REAL, etc.)
            - notnull: 1 if NOT NULL constraint exists, 0 otherwise
            - dflt_value: Default value (or None)
            - pk: 1 if column is part of primary key, 0 otherwise

    Example:
        >>> schema = get_table_schema("ScalpelDatabase.sqlite", "mp4_status")
        >>> print(schema[['name', 'type', 'pk']])
           name      type  pk
        0  recording_date  TEXT  1
        1  case_no    INTEGER  1
        2  camera_name    TEXT  1
        ...
    """
    with connect(db_path) as conn:
        return pd.read_sql_query(f"PRAGMA table_info({table});", conn)


def load_table(db_path: str, table: str) -> pd.DataFrame:
    """Load all rows from a table into a pandas DataFrame.

    Executes a SELECT * query to retrieve all data from the specified table.
    If the table doesn't exist or an error occurs, returns an empty DataFrame.

    Args:
        db_path: Path to the SQLite database file.
        table: Name of the table to load.

    Returns:
        pd.DataFrame: All rows from the table, or empty DataFrame on error.

    Note:
        This function loads the entire table into memory. For large tables,
        consider using chunked reading or SQL filtering.

    Example:
        >>> df = load_table("ScalpelDatabase.sqlite", "recording_details")
        >>> print(f"Loaded {len(df)} recordings")
        Loaded 150 recordings
    """
    with connect(db_path) as conn:
        try:
            return pd.read_sql_query(f"SELECT * FROM {table}", conn)
        except Exception:
            return pd.DataFrame()

