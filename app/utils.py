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


# ============================================================================
# ERD VISUALIZATION FUNCTIONS
# ============================================================================

def infer_foreign_key_relationships(
    db_path: str,
    manual_overrides: Optional[Dict[Tuple[str, str], Tuple[str, str]]] = None
) -> List[Tuple[str, str, str, str]]:
    """Infer foreign key relationships using naming conventions.

    Since SQLite doesn't always enforce FK constraints, this function uses
    smart inference based on column naming patterns:
    - If a column is named `tablename_id` or `tablename_key`, it references that table
    - Detects composite FK patterns (e.g., recording_date + case_no)
    - Handles both single and composite primary keys

    Args:
        db_path: Path to the SQLite database file.
        manual_overrides: Dictionary of manual FK overrides.
            Format: {(source_table, source_col): (target_table, target_col)}
            Example: {('recording_details', 'code'): ('anesthesiology', 'code')}

    Returns:
        List of tuples: (from_table, from_col, to_table, to_col)
    """
    relationships = []
    manual_overrides = manual_overrides or {}

    with connect(db_path) as conn:
        cursor = conn.cursor()

        # Get all tables and their columns
        cursor.execute("""
            SELECT name FROM sqlite_master
            WHERE type='table' AND name NOT LIKE 'sqlite_%'
        """)
        tables = [row[0] for row in cursor.fetchall()]

        # Build a map of table names and their PKs
        table_set = set(tables)
        table_pks = {}  # table -> list of PK columns

        for tbl in tables:
            cursor.execute(f"PRAGMA table_info({tbl})")
            cols = cursor.fetchall()
            pk_cols = [col[1] for col in cols if col[5] > 0]  # col[5] is pk flag
            table_pks[tbl] = pk_cols

        # Process each table
        for table in tables:
            # Get explicit foreign keys first
            cursor.execute(f"PRAGMA foreign_key_list({table})")
            explicit_fks = cursor.fetchall()

            # Group explicit FKs by target table to detect composite FKs
            fk_groups = {}  # target_table -> [(from_col, to_col), ...]
            for fk in explicit_fks:
                # fk: (id, seq, ref_table, from_col, to_col, on_update, on_delete, match)
                fk_id = fk[0]
                to_table = fk[2]
                from_col = fk[3]
                to_col = fk[4] if fk[4] else None

                if fk_id not in fk_groups:
                    fk_groups[fk_id] = {'table': to_table, 'cols': []}
                fk_groups[fk_id]['cols'].append((from_col, to_col))

            # Add explicit FKs
            for fk_group in fk_groups.values():
                to_table = fk_group['table']
                for from_col, to_col in fk_group['cols']:
                    # If to_col is None, infer from PK
                    if to_col is None:
                        if len(table_pks.get(to_table, [])) == 1:
                            to_col = table_pks[to_table][0]
                        else:
                            to_col = from_col  # Assume same name
                    relationships.append((table, from_col, to_table, to_col))

            # Get all columns for inference
            cursor.execute(f"PRAGMA table_info({table})")
            columns = cursor.fetchall()
            col_names = [col[1] for col in columns]

            # Track which columns already have explicit FKs
            explicit_fk_cols = set()
            for fk_group in fk_groups.values():
                for from_col, _ in fk_group['cols']:
                    explicit_fk_cols.add(from_col)

            # Check for composite key pattern matching
            # Common pattern: (recording_date, case_no) as composite FK
            current_pk_cols = table_pks.get(table, [])

            for target_table in tables:
                if target_table == table:
                    continue

                target_pk_cols = table_pks.get(target_table, [])
                if len(target_pk_cols) < 2:
                    continue  # Only check for composite PKs

                # Check if this table has all columns of the target's composite PK
                matching_cols = [col for col in target_pk_cols if col in col_names and col not in explicit_fk_cols]

                # If we have all PK columns from target table, it's likely a composite FK
                # BUT: Avoid circular references where both tables have the same PK columns
                if len(matching_cols) == len(target_pk_cols):
                    # Don't infer if both tables have identical PKs (avoid circular refs)
                    if set(current_pk_cols) == set(target_pk_cols):
                        # Only add if there's an explicit FK (which we already handled)
                        continue

                    for col in matching_cols:
                        relationships.append((table, col, target_table, col))
                        explicit_fk_cols.add(col)

            # Infer single-column FKs based on naming conventions
            for col in columns:
                col_name = col[1]

                # Skip if already has explicit FK or was part of composite FK
                if col_name in explicit_fk_cols:
                    continue

                # Check for manual override first
                if (table, col_name) in manual_overrides:
                    target_table, target_col = manual_overrides[(table, col_name)]
                    relationships.append((table, col_name, target_table, target_col))
                    continue

                # Pattern: column_name ends with '_id' or '_key'
                match = re.match(r'^(.+?)_(id|key)$', col_name, re.IGNORECASE)
                if match:
                    potential_table = match.group(1)

                    # Try exact match first
                    if potential_table in table_set:
                        # Check if target table has this column
                        cursor.execute(f"PRAGMA table_info({potential_table})")
                        target_cols = {c[1] for c in cursor.fetchall()}

                        # Try to find matching column (prefer exact match, then 'id', then the key column)
                        if col_name in target_cols:
                            # Exact match (e.g., anesthesiology_key -> anesthesiology.anesthesiology_key)
                            relationships.append((table, col_name, potential_table, col_name))
                        elif 'id' in target_cols:
                            # Standard id reference
                            relationships.append((table, col_name, potential_table, 'id'))
                        elif f"{potential_table}_key" in target_cols:
                            # Key reference (e.g., anesthesiology_key)
                            relationships.append((table, col_name, potential_table, f"{potential_table}_key"))

    return relationships


def classify_table_for_erd(table_name: str) -> str:
    """Classify tables into logical groups for ERD visualization."""
    if table_name in ['recording_details', 'analysis_information']:
        return 'core_data'
    elif table_name in ['anesthesiology']:
        return 'personnel'
    elif table_name in ['mp4_status', 'seq_status', 'mp4_times']:
        return 'media_files'
    else:
        return 'core_data'


@st.cache_data(show_spinner=False)
def get_database_schema_graphviz(
    db_path: str,
    manual_overrides: Optional[Dict[Tuple[str, str], Tuple[str, str]]] = None
) -> str:
    """Generate Graphviz DOT source for database ERD with smart FK inference.

    This function creates a clean, professional ERD visualization with:
    - Smart relationship inference based on naming conventions
    - Logical table grouping (Core Data, Personnel, Media Files)
    - HTML-like labels with primary/foreign key indicators
    - Crow's foot notation for relationships

    Args:
        db_path: Path to the SQLite database file.
        manual_overrides: Optional dictionary of manual FK relationships.
            Format: {(source_table, source_col): (target_table, target_col)}

    Returns:
        str: Graphviz DOT language source code.

    Example:
        >>> dot_source = get_database_schema_graphviz("ScalpelDatabase.sqlite")
        >>> st.graphviz_chart(dot_source)
    """
    try:
        import graphviz
    except ImportError:
        return "digraph { error[label='graphviz package not installed\\nRun: pip install graphviz'] }"

    # Color scheme
    COLORS = {
        'core_data': '#E8F4F8',
        'personnel': '#FFF4E6',
        'media_files': '#F0F8E8',
        'pk': '#FFD700',
        'fk': '#87CEEB',
        'border': '#2C3E50',
    }

    with connect(db_path) as conn:
        cursor = conn.cursor()

        # Get all tables
        cursor.execute("""
            SELECT name FROM sqlite_master
            WHERE type='table' AND name NOT LIKE 'sqlite_%'
            ORDER BY name
        """)
        tables = [row[0] for row in cursor.fetchall()]

        # Infer relationships
        relationships = infer_foreign_key_relationships(db_path, manual_overrides)

        # Build FK lookup for coloring
        fk_by_table = {}
        for from_table, from_col, to_table, to_col in relationships:
            if from_table not in fk_by_table:
                fk_by_table[from_table] = set()
            fk_by_table[from_table].add(from_col)

        # Group tables by category
        groups = {'core_data': [], 'personnel': [], 'media_files': []}
        for table in tables:
            group = classify_table_for_erd(table)
            groups[group].append(table)

        # Start building DOT source
        # Use LR (left-right) layout for better readability with many relationships
        dot_lines = [
            'digraph ScalpelLabERD {',
            '  rankdir=LR;',  # Changed from TB to LR for better horizontal layout
            '  splines=polyline;',  # Changed from ortho to polyline for smoother lines
            '  nodesep=1.0;',  # Increased spacing
            '  ranksep=2.0;',  # Increased rank separation
            '  bgcolor=white;',
            '  concentrate=true;',  # Merge edges with same source and target
            '  dpi=300;',  # High resolution (300 DPI)
            '  resolution=300;',  # Ensure high quality rendering
            '  pad=0.5;',  # Padding around diagram
            '  node [fontname="Arial" fontsize=11 shape=none];',
            '  edge [fontname="Arial" fontsize=9 color="#2C3E50" penwidth=1.5];',
            ''
        ]

        # Create subgraphs for each group
        group_labels = {
            'core_data': 'Core Data',
            'personnel': 'Personnel',
            'media_files': 'Media Files'
        }
        group_colors = {
            'core_data': '#E0F0FF',
            'personnel': '#FFE8D0',
            'media_files': '#E0F0D8'
        }

        cluster_id = 0
        for group_key, group_tables in groups.items():
            if not group_tables:
                continue

            dot_lines.append(f'  subgraph cluster_{cluster_id} {{')
            dot_lines.append(f'    label="{group_labels[group_key]}";')
            dot_lines.append(f'    style="rounded,filled";')
            dot_lines.append(f'    fillcolor="{group_colors[group_key]}";')
            dot_lines.append(f'    fontsize=16;')
            dot_lines.append(f'    fontname="Arial Bold";')
            dot_lines.append('')

            # Add table nodes
            for table in group_tables:
                cursor.execute(f"PRAGMA table_info({table})")
                columns = cursor.fetchall()

                # Identify PKs and FKs
                pk_cols = {col[1] for col in columns if col[5]}
                fk_cols = fk_by_table.get(table, set())

                # Build HTML table
                html_parts = [
                    '<<TABLE BORDER="0" CELLBORDER="1" CELLSPACING="0" CELLPADDING="4">',
                    f'<TR><TD COLSPAN="2" BGCOLOR="{COLORS[classify_table_for_erd(table)]}" '
                    f'BORDER="2"><B>{table}</B></TD></TR>'
                ]

                for col in columns:
                    col_name = col[1]
                    col_type = col[2] or 'TEXT'
                    is_pk = col[5]

                    # Determine styling
                    if is_pk:
                        bg_color = COLORS['pk']
                        icon = ' 🔑'
                    elif col_name in fk_cols:
                        bg_color = COLORS['fk']
                        icon = ' 🔗'
                    else:
                        bg_color = 'white'
                        icon = ''

                    html_parts.append(
                        f'<TR>'
                        f'<TD ALIGN="LEFT" BGCOLOR="{bg_color}"><B>{col_name}</B>{icon}</TD>'
                        f'<TD ALIGN="LEFT">{col_type}</TD>'
                        f'</TR>'
                    )

                html_parts.append('</TABLE>>')
                html_label = ''.join(html_parts)

                dot_lines.append(f'    {table} [label={html_label}];')

            dot_lines.append('  }')
            dot_lines.append('')
            cluster_id += 1

        # Add relationships - group composite FKs together
        dot_lines.append('  // Relationships')

        # Group relationships by (from_table, to_table) to combine composite FKs
        rel_groups = {}  # (from_table, to_table) -> [(from_col, to_col), ...]
        for from_table, from_col, to_table, to_col in relationships:
            key = (from_table, to_table)
            if key not in rel_groups:
                rel_groups[key] = []
            rel_groups[key].append((from_col, to_col))

        # Draw relationships
        for (from_table, to_table), col_pairs in rel_groups.items():
            if len(col_pairs) == 1:
                # Single column FK
                from_col, to_col = col_pairs[0]
                label = f"{from_col} → {to_col}"
            else:
                # Composite FK - show all columns
                col_strs = [f"{fc}→{tc}" for fc, tc in col_pairs]
                label = "(" + ", ".join(col_strs) + ")"

            dot_lines.append(
                f'  {to_table} -> {from_table} '
                f'[label="{label}" dir=both arrowhead=crow arrowtail=none];'
            )

        dot_lines.append('}')

        return '\n'.join(dot_lines)


def clear_erd_cache():
    """Clear the cached ERD visualization to force regeneration."""
    get_database_schema_graphviz.clear()
