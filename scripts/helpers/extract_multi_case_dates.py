"""
Extract Multi-Case Dates - Check which cases are handled in times.xlsx

This script:
1. Loads all dates/cases from cur_mp4_status view (database baseline)
2. Checks which of these are present in times.xlsx
3. Outputs CSV showing which cases are handled

Output CSV format:
- recording_date: Date in YYYY-MM-DD format
- case_no: Case number (1, 2, 3)
- Camera columns: Cart_Center_2, Cart_LT_4, etc. ('V' if camera has data)
- in_case_2: 'V' if case 2 exists in times.xlsx, empty otherwise
- in_case_3: 'V' if case 3 exists in times.xlsx, empty otherwise

The CSV includes ALL rows from cur_mp4_status view.
Output saved to: docs/multi_case_dates.csv (overwrites each time)
"""

import sys
import os
import re
import sqlite3
import pandas as pd
from pathlib import Path

# Get project root directory (two levels up from this file)
PROJECT_ROOT = Path(__file__).parent.parent.parent
DOCS_DIR = PROJECT_ROOT / "docs"

# Add project root to path
sys.path.insert(0, str(PROJECT_ROOT))

from config import get_db_path


def parse_date_from_path(path):
    """
    Extract recording_date from path.

    Expected format: .../DATA_YY-MM-DD/...

    Returns:
        recording_date string (YYYY-MM-DD) or None
    """
    try:
        # Match DATA_YY-MM-DD pattern
        match = re.search(r'DATA_(\d{2})-(\d{2})-(\d{2})', str(path))
        if match:
            yy, mm, dd = match.groups()
            # Convert 2-digit year to 4-digit
            yyyy = f"20{yy}" if int(yy) <= 69 else f"19{yy}"
            return f"{yyyy}-{mm}-{dd}"
        return None
    except Exception as e:
        return None


def extract_multi_case_dates(xlsx_path, output_csv=None, db_path=None):
    """
    Create CSV from cur_mp4_status view with in_case_2, in_case_3 columns showing which cases are in times.xlsx.

    Args:
        xlsx_path: Path to times.xlsx
        output_csv: Path to output CSV file (optional)
        db_path: Path to database (uses default if None)

    Returns:
        DataFrame with results
    """
    print("="*70)
    print("EXTRACT MULTI-CASE DATES")
    print("="*70)
    print(f"Database view: cur_mp4_status")
    print(f"XLSX file: {xlsx_path}")
    print()

    # Get database path
    if db_path is None:
        db_path = get_db_path()

    # Step 1: Load full cur_mp4_status view from database
    print("1. Loading cur_mp4_status view from database...")
    try:
        conn = sqlite3.connect(db_path)
        # Get all rows from cur_mp4_status view
        db_df = pd.read_sql('''
            SELECT *
            FROM cur_mp4_status
            ORDER BY recording_date, case_no
        ''', conn)
        conn.close()
        print(f"   -> Loaded {len(db_df)} rows from cur_mp4_status")
        print(f"   -> Columns: {', '.join(db_df.columns)}")
        print()
    except Exception as e:
        print(f"   [ERROR] Could not load from database: {e}")
        return None

    # Step 2: Load times.xlsx and parse which cases are present
    print("2. Loading times.xlsx...")
    try:
        xlsx_df = pd.read_excel(xlsx_path)

        # Remove completely blank rows
        xlsx_df = xlsx_df.dropna(how='all')

        # Remove rows where path is NaN
        xlsx_df = xlsx_df[xlsx_df['path'].notna()]

        print(f"   -> Loaded {len(xlsx_df)} rows from xlsx")
        print()
    except Exception as e:
        print(f"   [ERROR] Could not load xlsx: {e}")
        return None

    # Step 3: Parse dates from xlsx
    print("3. Parsing dates and cases from xlsx...")
    xlsx_df['recording_date'] = xlsx_df['path'].apply(parse_date_from_path)

    # Remove rows where date couldn't be parsed
    xlsx_df = xlsx_df[xlsx_df['recording_date'].notna()]

    # Check which case columns exist
    has_case_1 = 'start time - case 1' in xlsx_df.columns
    has_case_2 = 'start time - case 2' in xlsx_df.columns
    has_case_3 = 'start time - case 3' in xlsx_df.columns

    # Mark which cases have data
    if has_case_1:
        xlsx_df['has_case_1'] = xlsx_df['start time - case 1'].notna()
    else:
        xlsx_df['has_case_1'] = False

    if has_case_2:
        xlsx_df['has_case_2'] = xlsx_df['start time - case 2'].notna()
    else:
        xlsx_df['has_case_2'] = False

    if has_case_3:
        xlsx_df['has_case_3'] = xlsx_df['start time - case 3'].notna()
    else:
        xlsx_df['has_case_3'] = False

    # Group by date to see which cases are in xlsx
    xlsx_cases = xlsx_df.groupby('recording_date').agg({
        'has_case_1': 'any',
        'has_case_2': 'any',
        'has_case_3': 'any'
    }).reset_index()

    print(f"   -> Found {len(xlsx_cases)} unique dates in xlsx")
    print()

    # Step 4: Add C1, C2, C3 columns to database dataframe
    print("4. Matching database rows with xlsx data...")

    def get_case_marker(row):
        """Get C2, C3 markers for a database row."""
        date = row['recording_date']
        case_no = row['case_no']

        # Find matching date in xlsx
        xlsx_match = xlsx_cases[xlsx_cases['recording_date'] == date]

        if len(xlsx_match) == 0:
            # Date not in xlsx at all
            return '', ''

        xlsx_row = xlsx_match.iloc[0]

        # Check if this specific case is in xlsx (only case 2 and 3)
        c2 = 'V' if case_no == 2 and xlsx_row['has_case_2'] else ''
        c3 = 'V' if case_no == 3 and xlsx_row['has_case_3'] else ''

        return c2, c3

    # Apply the function to each row
    db_df[['in_case_2', 'in_case_3']] = db_df.apply(get_case_marker, axis=1, result_type='expand')

    print(f"   -> Added in_case_2, in_case_3 columns to {len(db_df)} rows")
    print()

    # Step 4b: Copy camera values from case 1 to case 2/3 when marked
    print("4b. Copying camera values from case 1 to marked cases 2 and 3...")

    # Get list of camera columns
    camera_cols = ['Cart_Center_2', 'Cart_LT_4', 'Cart_RT_1', 'General_3',
                   'Injection_Port', 'Monitor', 'Patient_Monitor', 'Ventilator_Monitor']

    updated_count = 0

    # For each date
    for date in db_df['recording_date'].unique():
        # Get case 1 row for this date
        case_1_rows = db_df[(db_df['recording_date'] == date) & (db_df['case_no'] == 1)]

        if len(case_1_rows) == 0:
            continue

        case_1_row = case_1_rows.iloc[0]

        # Get case 2 row if it has in_case_2='V'
        case_2_rows = db_df[(db_df['recording_date'] == date) &
                           (db_df['case_no'] == 2) &
                           (db_df['in_case_2'] == 'V')]

        if len(case_2_rows) > 0:
            # Copy camera values from case 1 to case 2
            case_2_idx = case_2_rows.index[0]
            for cam in camera_cols:
                db_df.at[case_2_idx, cam] = case_1_row[cam]
            updated_count += 1

        # Get case 3 row if it has in_case_3='V'
        case_3_rows = db_df[(db_df['recording_date'] == date) &
                           (db_df['case_no'] == 3) &
                           (db_df['in_case_3'] == 'V')]

        if len(case_3_rows) > 0:
            # Copy camera values from case 1 to case 3
            case_3_idx = case_3_rows.index[0]
            for cam in camera_cols:
                db_df.at[case_3_idx, cam] = case_1_row[cam]
            updated_count += 1

    print(f"   -> Copied camera values to {updated_count} case 2/3 rows")
    print()

    # Step 5: Statistics
    print("5. Statistics:")
    total_rows = len(db_df)
    rows_with_case_2_or_3 = ((db_df['in_case_2'] == 'V') | (db_df['in_case_3'] == 'V')).sum()
    rows_case_2 = (db_df['in_case_2'] == 'V').sum()
    rows_case_3 = (db_df['in_case_3'] == 'V').sum()

    print(f"   Total rows in cur_mp4_status: {total_rows}")
    print(f"   Rows with case 2 or 3 marked: {rows_with_case_2_or_3}")
    print(f"   ")
    print(f"   Rows by case:")
    print(f"     Case 2 (in_case_2='V'): {rows_case_2}")
    print(f"     Case 3 (in_case_3='V'): {rows_case_3}")
    print()

    # Step 6: Show sample of rows with case 2 or case 3
    print("6. Sample rows with Case 2 or Case 3:")
    print("-"*70)
    multi_case = db_df[(db_df['in_case_2'] == 'V') | (db_df['in_case_3'] == 'V')]
    if len(multi_case) > 0:
        print(f"   Found {len(multi_case)} rows with case 2 or case 3")
        print()
        # Show first 10 rows
        print(multi_case.head(10).to_string(index=False))
    else:
        print("   No rows with case 2 or case 3 found")
    print("-"*70)
    print()

    # Step 7: Save to CSV
    if output_csv:
        # Ensure docs directory exists
        os.makedirs(os.path.dirname(output_csv), exist_ok=True)

        print(f"7. Saving results to: {output_csv}")
        db_df.to_csv(output_csv, index=False)
        print(f"   -> Saved {len(db_df)} rows")
        print(f"   -> Columns: {', '.join(db_df.columns)}")
        print()

    print("="*70)
    print("DONE!")
    print("="*70)

    return db_df


def main():
    """Main entry point."""
    # Get xlsx path from command line or use default
    default_xlsx = PROJECT_ROOT / "times.xlsx"
    xlsx_path = sys.argv[1] if len(sys.argv) > 1 else str(default_xlsx)

    if not os.path.exists(xlsx_path):
        print(f"ERROR: XLSX file not found: {xlsx_path}")
        sys.exit(1)

    # Output to docs directory with constant filename
    output_csv = DOCS_DIR / "multi_case_dates.csv"

    # Extract multi-case dates
    result = extract_multi_case_dates(xlsx_path, str(output_csv))

    # Additional summary
    print()
    print("SUMMARY:")
    print(f"  Database: {get_db_path()}")
    print(f"  XLSX Input: {xlsx_path}")
    print(f"  CSV Output: {output_csv}")
    print(f"  Total rows exported: {len(result)}")
    print()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\nInterrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n\nERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
