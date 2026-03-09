"""
Database Comparison Script
Compares two SQLite databases and shows the differences
"""

import sqlite3
import sys
import os
from pathlib import Path
from collections import defaultdict

# Add parent directory to path to import config
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import get_db_path


def connect_db(db_path):
    """Connect to SQLite database."""
    if not os.path.exists(db_path):
        raise FileNotFoundError(f"Database not found: {db_path}")
    return sqlite3.connect(db_path)


def get_table_list(conn):
    """Get list of tables in database."""
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
    return [row[0] for row in cursor.fetchall()]


def get_table_columns(conn, table_name):
    """Get list of columns in a table."""
    cursor = conn.cursor()
    cursor.execute(f"PRAGMA table_info({table_name})")
    return [row[1] for row in cursor.fetchall()]


def get_table_data(conn, table_name):
    """Get all data from a table."""
    cursor = conn.cursor()

    # Check which columns exist
    columns = get_table_columns(conn, table_name)

    if not columns:
        return []

    # Get all columns
    query = f"""
        SELECT *
        FROM {table_name}
    """

    cursor.execute(query)
    return cursor.fetchall()


def get_table_primary_keys(table_name):
    """Determine which columns form the primary key for a table."""
    # Define primary key columns for each table
    primary_keys = {
        'seq_status': ['recording_date', 'case_no', 'camera_name'],
        'mp4_status': ['recording_date', 'case_no', 'camera_name'],
        'recording_details': ['recording_date', 'case_no'],
        'anesthesiology': ['recording_date', 'case_no'],
        'analysis_information': ['recording_date', 'case_no'],
        'sqlite_sequence': ['name'],
    }

    return primary_keys.get(table_name, [])


def get_primary_key_indices(columns, primary_keys):
    """Get the indices of primary key columns."""
    indices = []
    for pk in primary_keys:
        if pk in columns:
            indices.append(columns.index(pk))
    return indices


def get_summary_stats(conn):
    """Get summary statistics from database."""
    cursor = conn.cursor()

    stats = {}

    # SEQ stats
    cursor.execute("SELECT COUNT(*) FROM seq_status")
    stats['seq_count'] = cursor.fetchone()[0]

    cursor.execute("SELECT SUM(size_mb) FROM seq_status")
    result = cursor.fetchone()[0]
    stats['seq_total_size_mb'] = result if result else 0

    cursor.execute("SELECT COUNT(DISTINCT recording_date) FROM seq_status")
    stats['seq_unique_dates'] = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(DISTINCT case_no) FROM seq_status")
    stats['seq_unique_cases'] = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(DISTINCT camera_name) FROM seq_status")
    stats['seq_unique_cameras'] = cursor.fetchone()[0]

    # MP4 stats
    cursor.execute("SELECT COUNT(*) FROM mp4_status")
    stats['mp4_count'] = cursor.fetchone()[0]

    cursor.execute("SELECT SUM(size_mb) FROM mp4_status")
    result = cursor.fetchone()[0]
    stats['mp4_total_size_mb'] = result if result else 0

    cursor.execute("SELECT COUNT(DISTINCT recording_date) FROM mp4_status")
    stats['mp4_unique_dates'] = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(DISTINCT case_no) FROM mp4_status")
    stats['mp4_unique_cases'] = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(DISTINCT camera_name) FROM mp4_status")
    stats['mp4_unique_cameras'] = cursor.fetchone()[0]

    return stats


def get_records_by_key(data, key_indices):
    """Convert list of records to dictionary keyed by primary key columns."""
    result = {}
    for row in data:
        if not key_indices:
            # If no primary key defined, use entire row as key (not ideal but works)
            key = tuple(row)
        else:
            # Use primary key columns as key
            key = tuple(row[i] for i in key_indices)
        result[key] = row
    return result


def compare_tables(data1, data2, table_name, columns1, columns2):
    """Compare two sets of table data."""
    # Get primary key configuration
    primary_keys = get_table_primary_keys(table_name)

    # Get indices for primary keys in each dataset
    key_indices1 = get_primary_key_indices(columns1, primary_keys)
    key_indices2 = get_primary_key_indices(columns2, primary_keys)

    dict1 = get_records_by_key(data1, key_indices1)
    dict2 = get_records_by_key(data2, key_indices2)

    keys1 = set(dict1.keys())
    keys2 = set(dict2.keys())

    only_in_db1 = keys1 - keys2
    only_in_db2 = keys2 - keys1
    common = keys1 & keys2

    # Check for differences in common records
    differences = []
    for key in common:
        if dict1[key] != dict2[key]:
            differences.append((key, dict1[key], dict2[key]))

    return {
        'only_in_db1': sorted(only_in_db1),
        'only_in_db2': sorted(only_in_db2),
        'differences': differences,
        'total_db1': len(keys1),
        'total_db2': len(keys2),
        'common': len(common),
        'primary_keys': primary_keys,
        'columns1': columns1,
        'columns2': columns2
    }


def format_key_display(key, primary_keys, columns):
    """Format primary key values for display."""
    if not primary_keys or not key:
        return str(key)

    parts = []
    for i, pk_name in enumerate(primary_keys):
        if i < len(key):
            value = key[i]
            if pk_name == 'case_no':
                parts.append(f"Case{value}")
            else:
                parts.append(str(value))

    return " | ".join(parts)


def format_row_display(row, columns):
    """Format row data for display."""
    parts = []
    for i, col in enumerate(columns):
        if i < len(row):
            value = row[i]
            if value is not None:
                # Try to format as float if possible
                try:
                    if isinstance(value, (int, float)):
                        if col.endswith('_mb') or col == 'size_mb':
                            parts.append(f"{col}={float(value):.1f}MB")
                        elif 'duration' in col:
                            parts.append(f"{col}={float(value):.1f}min")
                        else:
                            parts.append(f"{col}={value}")
                    else:
                        parts.append(f"{col}={value}")
                except (ValueError, TypeError):
                    parts.append(f"{col}={value}")

    return ", ".join(parts)


def print_comparison_results(results, table_name, db1_name, db2_name):
    """Print comparison results in a readable format."""
    print(f"\n{'=' * 80}")
    print(f"COMPARISON: {table_name.upper()}")
    print(f"{'=' * 80}")

    primary_keys = results.get('primary_keys', [])
    columns1 = results.get('columns1', [])
    columns2 = results.get('columns2', [])

    print(f"\nColumns:")
    print(f"  Database 1: {', '.join(columns1)}")
    print(f"  Database 2: {', '.join(columns2)}")

    print(f"\nTotal records:")
    print(f"  Database 1 ({db1_name}): {results['total_db1']}")
    print(f"  Database 2 ({db2_name}): {results['total_db2']}")
    print(f"  Common records: {results['common']}")

    # Records only in DB1
    if results['only_in_db1']:
        print(f"\n📋 Records ONLY in Database 1 ({len(results['only_in_db1'])} records):")
        print("-" * 80)
        for i, key in enumerate(results['only_in_db1'], 1):
            key_display = format_key_display(key, primary_keys, columns1)
            print(f"  {i}. {key_display}")
    else:
        print(f"\n✓ No records unique to Database 1")

    # Records only in DB2
    if results['only_in_db2']:
        print(f"\n📋 Records ONLY in Database 2 ({len(results['only_in_db2'])} records):")
        print("-" * 80)
        for i, key in enumerate(results['only_in_db2'], 1):
            key_display = format_key_display(key, primary_keys, columns2)
            print(f"  {i}. {key_display}")
    else:
        print(f"\n✓ No records unique to Database 2")

    # Differences in common records
    if results['differences']:
        print(f"\n⚠️  Differences in common records ({len(results['differences'])} records):")
        print("-" * 80)
        for i, (key, row1, row2) in enumerate(results['differences'], 1):
            key_display = format_key_display(key, primary_keys, columns1)
            print(f"  {i}. {key_display}")

            # Show full row data
            row1_display = format_row_display(row1, columns1)
            row2_display = format_row_display(row2, columns2)

            print(f"     DB1: {row1_display}")
            print(f"     DB2: {row2_display}")
    else:
        print(f"\n✓ No differences in common records")


def main():
    """Main comparison function."""
    print("=" * 80)
    print("DATABASE COMPARISON TOOL")
    print("=" * 80)

    # Get current database path
    current_db = get_db_path()
    print(f"\nCurrent database: {current_db}")

    # Ask for second database path
    print("\nEnter the path to the database you want to compare with:")
    compare_db = input("Database path: ").strip()

    # Remove quotes if user pasted path with quotes
    compare_db = compare_db.strip('"').strip("'")

    if not compare_db:
        print("❌ No path provided!")
        return

    # Check if both databases exist
    if not os.path.exists(current_db):
        print(f"❌ Current database not found: {current_db}")
        return

    if not os.path.exists(compare_db):
        print(f"❌ Comparison database not found: {compare_db}")
        return

    print(f"\n✓ Comparing databases:")
    print(f"  Database 1: {current_db}")
    print(f"  Database 2: {compare_db}")

    try:
        # Connect to both databases
        conn1 = connect_db(current_db)
        conn2 = connect_db(compare_db)

        # Get database names for display
        db1_name = Path(current_db).name
        db2_name = Path(compare_db).name

        # Check tables
        tables1 = get_table_list(conn1)
        tables2 = get_table_list(conn2)

        print(f"\nTables in Database 1: {', '.join(tables1)}")
        print(f"Tables in Database 2: {', '.join(tables2)}")

        # Get summary statistics
        print(f"\n{'=' * 80}")
        print("SUMMARY STATISTICS")
        print(f"{'=' * 80}")

        stats1 = get_summary_stats(conn1)
        stats2 = get_summary_stats(conn2)

        print(f"\nSEQ FILES:")
        print(f"  Database 1: {stats1['seq_count']} files, {stats1['seq_total_size_mb']:.1f} MB total")
        print(f"              {stats1['seq_unique_dates']} dates, {stats1['seq_unique_cases']} cases, {stats1['seq_unique_cameras']} cameras")
        print(f"  Database 2: {stats2['seq_count']} files, {stats2['seq_total_size_mb']:.1f} MB total")
        print(f"              {stats2['seq_unique_dates']} dates, {stats2['seq_unique_cases']} cases, {stats2['seq_unique_cameras']} cameras")
        print(f"  Difference: {stats2['seq_count'] - stats1['seq_count']:+d} files, {stats2['seq_total_size_mb'] - stats1['seq_total_size_mb']:+.1f} MB")

        print(f"\nMP4 FILES:")
        print(f"  Database 1: {stats1['mp4_count']} files, {stats1['mp4_total_size_mb']:.1f} MB total")
        print(f"              {stats1['mp4_unique_dates']} dates, {stats1['mp4_unique_cases']} cases, {stats1['mp4_unique_cameras']} cameras")
        print(f"  Database 2: {stats2['mp4_count']} files, {stats2['mp4_total_size_mb']:.1f} MB total")
        print(f"              {stats2['mp4_unique_dates']} dates, {stats2['mp4_unique_cases']} cases, {stats2['mp4_unique_cameras']} cameras")
        print(f"  Difference: {stats2['mp4_count'] - stats1['mp4_count']:+d} files, {stats2['mp4_total_size_mb'] - stats1['mp4_total_size_mb']:+.1f} MB")

        # Get all tables that exist in both databases
        common_tables = set(tables1) & set(tables2)
        tables_only_in_db1 = set(tables1) - set(tables2)
        tables_only_in_db2 = set(tables2) - set(tables1)

        if tables_only_in_db1:
            print(f"\n⚠️  Tables only in Database 1: {', '.join(tables_only_in_db1)}")
        if tables_only_in_db2:
            print(f"\n⚠️  Tables only in Database 2: {', '.join(tables_only_in_db2)}")

        # Compare all common tables
        if common_tables:
            print(f"\nComparing {len(common_tables)} common tables...")

            for table in sorted(common_tables):
                # Skip sqlite internal tables
                if table.startswith('sqlite_'):
                    continue

                try:
                    # Get columns for both databases
                    columns1 = get_table_columns(conn1, table)
                    columns2 = get_table_columns(conn2, table)

                    # Get data from both tables
                    data1 = get_table_data(conn1, table)
                    data2 = get_table_data(conn2, table)

                    # Compare tables
                    results = compare_tables(data1, data2, table, columns1, columns2)
                    print_comparison_results(results, table, db1_name, db2_name)

                except Exception as e:
                    print(f"\n❌ Error comparing table '{table}': {e}")
        else:
            print("\n⚠️  No common tables found between databases")

        # Close connections
        conn1.close()
        conn2.close()

        print(f"\n{'=' * 80}")
        print("COMPARISON COMPLETE")
        print(f"{'=' * 80}")

    except Exception as e:
        print(f"\n❌ Error during comparison: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n⚠️  Comparison interrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
