#!/usr/bin/env python3
"""
SQLite to dbdiagram.io converter
Converts SQLite database to dbdiagram.io format for easy visualization
"""

import sqlite3
import re
import sys
import os
from datetime import date
from typing import List, Dict, Tuple

def parse_foreign_keys_from_sql(create_sql: str) -> List[Tuple[str, str, str]]:
    """Extract foreign key relationships from CREATE TABLE SQL"""
    fk_pattern = r'FOREIGN KEY\s*\(\s*([^)]+)\s*\)\s*REFERENCES\s*["\']?([^"\'(\s]+)["\']?\s*(?:\(\s*([^)]+)\s*\))?'
    matches = re.findall(fk_pattern, create_sql, re.IGNORECASE)

    foreign_keys = []
    for match in matches:
        local_col = match[0].strip().strip('"').strip("'")
        ref_table = match[1].strip().strip('"').strip("'")
        ref_col = match[2].strip().strip('"').strip("'") if match[2] else None
        foreign_keys.append((local_col, ref_table, ref_col))

    return foreign_keys

def sqlite_to_dbdiagram(db_path: str, output_path: str):
    """Convert SQLite database to dbdiagram.io format"""

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # Get all tables including sqlite_sequence
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
    all_tables = [row[0] for row in cursor.fetchall()]

    # Regular tables (excluding sqlite_sequence)
    tables = [t for t in all_tables if t != 'sqlite_sequence']

    # Store table information
    table_schemas = {}
    all_foreign_keys = {}

    # Add sqlite_sequence if it exists
    if 'sqlite_sequence' in all_tables:
        table_schemas['sqlite_sequence'] = [
            (0, 'name', 'varchar', 0, None, 0),
            (1, 'seq', 'varchar', 0, None, 0)
        ]

    for table in tables:
        # Get table schema
        cursor.execute(f"PRAGMA table_info({table})")
        columns = cursor.fetchall()
        table_schemas[table] = columns

        # Get foreign keys using PRAGMA foreign_key_list (more reliable)
        cursor.execute(f"PRAGMA foreign_key_list({table})")
        pragma_fks = cursor.fetchall()

        foreign_keys = []
        for fk in pragma_fks:
            # fk structure: (id, seq, table, from, to, on_update, on_delete, match)
            local_col = fk[3]  # from column
            ref_table = fk[2]  # referenced table
            ref_col = fk[4]    # to column
            foreign_keys.append((local_col, ref_table, ref_col))

        # If PRAGMA didn't find any, try parsing CREATE TABLE SQL as fallback
        if not foreign_keys:
            cursor.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,))
            create_sql = cursor.fetchone()
            if create_sql and create_sql[0]:
                foreign_keys = parse_foreign_keys_from_sql(create_sql[0])

        if foreign_keys:
            all_foreign_keys[table] = foreign_keys

    conn.close()

    # System tables that should be listed separately
    system_tables = {'sqlite_sequence'}

    # Generate dbdiagram.io format
    output_lines = []
    today = date.today().strftime("%Y-%m-%d")
    output_lines.append(f"//// ScalpelLab Database, exported {today}")
    output_lines.append("//// Paste into https://dbdiagram.io")
    output_lines.append("")

    def convert_sqlite_type(col_type: str) -> str:
        """Convert SQLite type to dbdiagram.io type"""
        db_type = (col_type or "TEXT").upper()
        if "INT" in db_type:
            return "int"
        elif "REAL" in db_type or "FLOAT" in db_type or "DOUBLE" in db_type:
            return "decimal"
        elif "BLOB" in db_type:
            return "blob"
        elif "BOOL" in db_type:
            return "boolean"
        else:
            return "varchar"

    def generate_table_definition(table_name: str, columns: list, foreign_keys: list) -> list:
        """Generate dbdiagram.io table definition lines"""
        lines = []
        lines.append(f"Table {table_name} {{")

        # Find max column name length for alignment
        max_name_len = max(len(col[1]) for col in columns) if columns else 10

        # Build FK lookup for comments
        fk_lookup = {fk[0]: (fk[1], fk[2]) for fk in foreign_keys} if foreign_keys else {}

        for col in columns:
            col_name = col[1]
            col_type = col[2] or "TEXT"
            is_pk = col[5]
            not_null = col[3]
            default_val = col[4]

            db_type = convert_sqlite_type(col_type)
            col_def = f"  {col_name:<{max_name_len}} {db_type}"

            # Add constraints
            constraints = []
            if is_pk:
                constraints.append("pk")
            if not_null and not is_pk:
                constraints.append("not null")
            if default_val is not None:
                constraints.append(f"default: {default_val}")

            if constraints:
                col_def += f" [{', '.join(constraints)}]"

            # Add FK comment if this column is a foreign key
            if col_name in fk_lookup:
                ref_table, ref_col = fk_lookup[col_name]
                if ref_col:
                    col_def += f" // FK to {ref_table}.{ref_col}"
                else:
                    col_def += f" // FK to {ref_table}"

            lines.append(col_def)

        lines.append("}")
        return lines

    # Handle sqlite_sequence first if it exists
    if 'sqlite_sequence' in table_schemas:
        output_lines.append("//// System table from SQLite. Usually not modeled, included here for completeness.")
        output_lines.extend(generate_table_definition(
            'sqlite_sequence',
            table_schemas['sqlite_sequence'],
            []
        ))
        output_lines.append("")

    # Generate table definitions for all regular tables (alphabetically sorted)
    regular_tables = sorted([t for t in table_schemas.keys() if t not in system_tables])

    for table in regular_tables:
        table_fks = all_foreign_keys.get(table, [])
        output_lines.extend(generate_table_definition(
            table,
            table_schemas[table],
            table_fks
        ))
        output_lines.append("")

    # Generate relationships dynamically from detected foreign keys
    if all_foreign_keys:
        output_lines.append("// Relationships detected from database:")
        seen_refs = set()  # Track seen relationships to avoid duplicates

        for table, foreign_keys in all_foreign_keys.items():
            for local_col, ref_table, ref_col in foreign_keys:
                # Default to primary key if ref_col is not specified
                if not ref_col:
                    # Find primary key of referenced table
                    if ref_table in table_schemas:
                        for col in table_schemas[ref_table]:
                            if col[5]:  # is primary key
                                ref_col = col[1]
                                break
                    if not ref_col:
                        ref_col = "id"  # fallback

                # Create normalized key for deduplication (sorted endpoints)
                endpoint1 = f"{table}.{local_col}"
                endpoint2 = f"{ref_table}.{ref_col}"
                ref_key = tuple(sorted([endpoint1, endpoint2]))

                if ref_key not in seen_refs:
                    seen_refs.add(ref_key)
                    output_lines.append(f"Ref: {ref_table}.{ref_col} > {table}.{local_col}")
        output_lines.append("")
    else:
        output_lines.append("// No foreign key relationships detected in database schema")
        output_lines.append("")

    # Write to file
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(output_lines))

    print(f"dbdiagram.io format saved to: {output_path}")
    print("\nTo use:")
    print("1. Go to https://dbdiagram.io/")
    print("2. Click 'Go to App'")
    print("3. Copy and paste the content of the output file")
    print("4. Your database diagram will be generated automatically!")

def main():
    # Define paths directly - go up to project root (3 levels: helpers -> scripts -> ScalpelLab)
    project_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
    db_path = os.path.join(project_root, "ScalpelDatabase.sqlite")
    output_path = os.path.join(project_root, "docs", "scalpel_dbdiagram.txt")

    print(f"Converting database: {db_path}")
    print(f"Output file: {output_path}")
    print()

    try:
        sqlite_to_dbdiagram(db_path, output_path)
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()