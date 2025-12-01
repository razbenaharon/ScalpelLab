"""
Batch Video Redaction Script
Redacts videos based on Excel file with case time ranges.
Uses GPU-accelerated parallel processing.
"""

import sys
import os
import time
from datetime import datetime, timedelta

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.handle_xlsx import handle_xlsx
from scripts.redact_video import redact_videos_from_df
import pandas as pd


# ============================================================================
# CONFIGURATION
# ============================================================================
CONFIG = {
    # Input Excel file path (leave empty to prompt user)
    'XLSX_PATH': '',  # Example: 'F:/Room_8_Data/Scalpel_Raz/times.xlsx'

    # Output directory for redacted videos (leave empty for same as input)
    'OUTPUT_DIR': '',  # Example: 'F:/Room_8_Data/Output'

    # Number of parallel workers (recommended: 6 for RTX A2000, 2-8 for other GPUs)
    'NUM_WORKERS': 6,
}
# ============================================================================


def main():
    # Get xlsx path from config or command line or prompt
    xlsx_path = None
    output_dir = None
    num_workers = CONFIG['NUM_WORKERS']

    # Check command line arguments first
    if len(sys.argv) >= 2:
        xlsx_path = sys.argv[1]
        output_dir = sys.argv[2] if len(sys.argv) > 2 else CONFIG['OUTPUT_DIR']
        num_workers = int(sys.argv[3]) if len(sys.argv) > 3 else num_workers
    # Then check config
    elif CONFIG['XLSX_PATH']:
        xlsx_path = CONFIG['XLSX_PATH']
        output_dir = CONFIG['OUTPUT_DIR']
    # Finally, prompt user
    else:
        print("="*60)
        print("BATCH VIDEO REDACTION")
        print("="*60)
        print()
        xlsx_path = input("Enter path to Excel file (.xlsx): ").strip()

        if not xlsx_path:
            print("ERROR: No file path provided")
            sys.exit(1)

        output_input = input("Enter output directory (leave empty for same as input): ").strip()
        output_dir = output_input if output_input else CONFIG['OUTPUT_DIR']

        workers_input = input(f"Number of parallel workers (default: {num_workers}): ").strip()
        if workers_input:
            try:
                num_workers = int(workers_input)
            except ValueError:
                print(f"Invalid number, using default: {num_workers}")

    # Ensure output_dir is None if empty string
    if not output_dir:
        output_dir = None

    # Load and process xlsx
    print("="*60)
    print("BATCH VIDEO REDACTION")
    print("="*60)
    print(f"XLSX file: {xlsx_path}")
    print(f"Output dir: {output_dir or 'Same as input'}")
    print(f"Workers: {num_workers}")
    print("="*60)
    print()

    df = handle_xlsx(xlsx_path)
    print(df)
    print("="*60)

    # Display files and let user choose how many to process
    if len(df) == 0:
        print("No files to process!")
        return

    print("\nAVAILABLE FILES:")
    print("-" * 80)
    for i, row in df.iterrows():
        print(f"  {i+1:3d}. {row['path']}")

    print("-" * 80)
    print(f"\nTotal: {len(df)} files")
    print("\nHow many files do you want to process?")
    print("  1. Process ALL files")
    print("  2. Process first N files")
    print("  3. Choose specific files by number")

    selection_mode = input("\nChoice (1, 2, or 3): ").strip()

    selected_df = None

    if selection_mode == '1':
        # Process all
        selected_df = df
        print(f"Selected all {len(df)} files")

    elif selection_mode == '2':
        # Process first N
        n_input = input("How many files (from the top)? ").strip()
        try:
            n = int(n_input)
            selected_df = df.head(n)
            print(f"Selected first {len(selected_df)} files")
        except ValueError:
            print("ERROR: Invalid number")
            return

    elif selection_mode == '3':
        # Choose specific files
        print("\nEnter file numbers (comma-separated or ranges)")
        print("  Examples:")
        print("    1,3,5         - Files 1, 3, and 5")
        print("    1-10          - Files 1 through 10")
        print("    1-5,8,10-15   - Files 1-5, 8, and 10-15")

        numbers_input = input("\nFile numbers: ").strip()

        try:
            # Parse input
            selected_indices = set()
            parts = numbers_input.split(',')

            for part in parts:
                part = part.strip()
                if '-' in part:
                    # Range
                    start, end = part.split('-')
                    start_idx = int(start.strip())
                    end_idx = int(end.strip())
                    selected_indices.update(range(start_idx, end_idx + 1))
                else:
                    # Single number
                    selected_indices.add(int(part))

            # Convert to dataframe rows
            selected_rows = []
            for idx in sorted(selected_indices):
                if 1 <= idx <= len(df):
                    selected_rows.append(df.iloc[idx - 1])

            selected_df = pd.DataFrame(selected_rows)
            print(f"Selected {len(selected_df)} files")

        except Exception as e:
            print(f"ERROR: Invalid input: {e}")
            return
    else:
        print("ERROR: Invalid choice")
        return

    if selected_df is None or len(selected_df) == 0:
        print("No files selected!")
        return

    # Confirm
    response = input(f"\nProcess {len(selected_df)} files with GPU-accelerated redaction ({num_workers} workers)? (y/n): ").strip().lower()
    if response != 'y':
        print("Cancelled.")
        return

    print()
    print("="*60)
    print("STARTING VIDEO REDACTION")
    print("="*60)
    print()

    # Start timing
    start_time = time.time()
    start_datetime = datetime.now()

    # Process videos with case-based redaction
    output_files, success_count, failed_count, file_statuses, processing_report = redact_videos_from_df(
        selected_df,
        output_dir=output_dir,
        num_workers=num_workers
    )

    # End timing
    end_time = time.time()
    end_datetime = datetime.now()
    elapsed_seconds = end_time - start_time
    elapsed_timedelta = timedelta(seconds=int(elapsed_seconds))

    # Build summary text
    summary_lines = []
    summary_lines.append("="*80)
    summary_lines.append("BATCH REDACTION SUMMARY")
    summary_lines.append("="*80)
    summary_lines.append(f"Generated:         {end_datetime.strftime('%Y-%m-%d %H:%M:%S')}")
    summary_lines.append(f"Input XLSX:        {xlsx_path}")
    summary_lines.append("")
    summary_lines.append("TIMING:")
    summary_lines.append(f"  Start Time:      {start_datetime.strftime('%Y-%m-%d %H:%M:%S')}")
    summary_lines.append(f"  End Time:        {end_datetime.strftime('%Y-%m-%d %H:%M:%S')}")
    summary_lines.append(f"  Total Duration:  {elapsed_timedelta} ({elapsed_seconds:.1f} seconds)")
    summary_lines.append("")
    summary_lines.append("RESULTS:")
    summary_lines.append(f"  Total Files:     {len(selected_df)}")
    summary_lines.append(f"  Succeeded:       {success_count}")
    summary_lines.append(f"  Failed:          {failed_count}")
    summary_lines.append(f"  Success Rate:    {(success_count/len(selected_df)*100):.1f}%")
    summary_lines.append("")
    summary_lines.append("PERFORMANCE:")
    summary_lines.append(f"  Workers Used:    {num_workers}")
    summary_lines.append(f"  Avg Time/Video:  {elapsed_seconds/len(selected_df):.1f}s")
    if success_count > 0:
        summary_lines.append(f"  Speedup (est):   ~{num_workers * 0.85:.1f}x")
    summary_lines.append("")
    summary_lines.append(f"OUTPUT:")
    summary_lines.append(f"  Directory:       {output_dir or 'Same as input files'}")
    summary_lines.append("="*80)
    summary_lines.append("")
    summary_lines.append("PROCESSED FILES:")
    summary_lines.append("-"*80)

    # Add successful files first
    if success_count > 0:
        summary_lines.append("")
        summary_lines.append(f"SUCCESSFUL ({success_count}):")
        for idx, row in selected_df.iterrows():
            status = file_statuses.get(idx, "UNKNOWN")
            if status == "SUCCESS":
                summary_lines.append(f"  [SUCCESS] {row['path']}")

    # Add failed files
    if failed_count > 0:
        summary_lines.append("")
        summary_lines.append(f"FAILED ({failed_count}):")
        for idx, row in selected_df.iterrows():
            status = file_statuses.get(idx, "UNKNOWN")
            if status != "SUCCESS":
                summary_lines.append(f"  [{status}]")
                summary_lines.append(f"     File: {row['path']}")

    summary_lines.append("")
    summary_lines.append("-"*80)

    # Add detailed redaction report
    if processing_report:
        summary_lines.append("")
        summary_lines.append("="*80)
        summary_lines.append("REDACTION REPORT")
        summary_lines.append("="*80)

        for i, report in enumerate(processing_report, 1):
            summary_lines.append("")
            summary_lines.append(f"Video {i}: {report['file']}")
            summary_lines.append(f"  Total Duration: {report['duration']:.1f}s ({report['duration']/60:.1f} min)")
            summary_lines.append("")
            summary_lines.append(f"  CASE TIMES (Corner box visible):")

            if report['case_ranges']:
                total_case_time = 0
                for cr in report['case_ranges']:
                    case_duration = cr['end'] - cr['start']
                    total_case_time += case_duration
                    summary_lines.append(f"    Case {cr['case']}: {cr['start']:.1f}s -> {cr['end']:.1f}s (duration: {case_duration:.1f}s)")
                summary_lines.append(f"    Total case time: {total_case_time:.1f}s ({total_case_time/60:.1f} min)")
            else:
                summary_lines.append(f"    None")

            summary_lines.append("")
            summary_lines.append(f"  FULL BLACK TIMES:")
            if report['black_periods']:
                total_black_time = 0
                for j, bp in enumerate(report['black_periods'], 1):
                    black_duration = bp['end'] - bp['start']
                    total_black_time += black_duration
                    summary_lines.append(f"    Period {j}: {bp['start']:.1f}s -> {bp['end']:.1f}s (duration: {black_duration:.1f}s)")
                summary_lines.append(f"    Total black time: {total_black_time:.1f}s ({total_black_time/60:.1f} min)")
            else:
                summary_lines.append(f"    None")

            summary_lines.append("")
            summary_lines.append(f"  SUMMARY:")
            case_pct = (sum(cr['end'] - cr['start'] for cr in report['case_ranges']) / report['duration']) * 100 if report['case_ranges'] else 0
            black_pct = (sum(bp['end'] - bp['start'] for bp in report['black_periods']) / report['duration']) * 100 if report['black_periods'] else 0
            summary_lines.append(f"    Case time: {case_pct:.1f}% of video")
            summary_lines.append(f"    Black time: {black_pct:.1f}% of video")

        summary_lines.append("")
        summary_lines.append("="*80)

    # Print summary to console
    print("\n")
    for line in summary_lines:
        print(line)

    # Export summary to file
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)
        summary_filename = f"redaction_summary_{start_datetime.strftime('%Y%m%d_%H%M%S')}.txt"
        summary_path = os.path.join(output_dir, summary_filename)
    else:
        # Use current directory if no output dir specified
        summary_filename = f"redaction_summary_{start_datetime.strftime('%Y%m%d_%H%M%S')}.txt"
        summary_path = summary_filename

    try:
        with open(summary_path, 'w', encoding='utf-8') as f:
            f.write('\n'.join(summary_lines))
        print(f"\nSummary exported to: {summary_path}")
    except Exception as e:
        print(f"\nWarning: Could not export summary to file: {e}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\nWARNING: Redaction interrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n\nERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
