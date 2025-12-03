"""
Compare MP4 Files Between Two Directories

Compares MP4 files in source directory against destination directory.
Reports missing files and generates a detailed report.

Usage:
    python compare_mp4.py                           # Use default paths
    python compare_mp4.py <source> <destination>    # Use custom paths
"""

import os
import sys

# --- Default Configuration ---
# These are used if no command-line arguments are provided
DEFAULT_PATH_X = r'F:\Room_8_Data\Recordings'  # Where the .mp4 files are (source)
DEFAULT_PATH_Y = r'H:'   # Where to check for them (backup/destination)


# ---------------------

def check_files(path_x, path_y):
    """
    Compare MP4 files between two directories.

    Args:
        path_x: Source directory (where files should be)
        path_y: Destination directory (backup/comparison location)
    """
    # 1. Verify paths exist
    if not os.path.exists(path_x):
        print(f"Error: Source path does not exist: {path_x}")
        return

    if not os.path.exists(path_y):
        print(f"Error: Destination path does not exist: {path_y}")
        return

    # 2. Get a snapshot of files in Path Y for comparison (including subdirectories)
    # We use a dict to store (filename, size) -> full path for O(1) lookup speed
    try:
        files_in_y = {}  # Key: (filename, size), Value: full path in Y
        print(f"Scanning {path_y} for .mp4 files...")

        for root, dirs, files in os.walk(path_y):
            for filename in files:
                if filename.lower().endswith(".mp4"):
                    full_path = os.path.join(root, filename)
                    try:
                        file_size = os.path.getsize(full_path)
                        # Store (filename, size) as key
                        key = (filename, file_size)
                        files_in_y[key] = full_path
                    except OSError:
                        # Skip files we can't access
                        pass

        print(f"Found {len(files_in_y)} .mp4 files in destination\n")

    except PermissionError:
        print(f"Error: Permission denied accessing {path_y}")
        return

    print(f"Scanning {path_x} for .mp4 files (including subdirectories)...\n")
    print(f"Comparing by filename and file size...\n")
    print("=" * 80)
    print(f"MISSING FILES REPORT")
    print("=" * 80)

    found_count = 0
    missing_count = 0
    missing_files = []  # Store missing files for detailed report

    # 3. Scan Path X recursively
    for root, dirs, files in os.walk(path_x):
        for filename in files:
            # Check for .mp4 extension (case insensitive)
            if filename.lower().endswith(".mp4"):
                full_path = os.path.join(root, filename)
                rel_path = os.path.relpath(full_path, path_x)

                try:
                    file_size = os.path.getsize(full_path)
                    key = (filename, file_size)

                    if key in files_in_y:
                        found_count += 1
                    else:
                        size_mb = file_size / (1024 * 1024)
                        missing_files.append({
                            'rel_path': rel_path,
                            'full_path': full_path,
                            'size_mb': size_mb,
                            'filename': filename
                        })
                        missing_count += 1
                except OSError:
                    print(f"[ERROR]   {rel_path} (cannot read file)")
                    missing_count += 1

    # 4. Display missing files
    if missing_files:
        print("\nMISSING FILES:")
        print("-" * 80)
        for item in missing_files:
            print(f"File: {item['rel_path']}")
            print(f"  Size: {item['size_mb']:.2f} MB")
            print(f"  Full Path: {item['full_path']}")
            print()
    else:
        print("\nNo missing files found!")
        print()

    # 5. Summary
    print("=" * 80)
    print("SUMMARY")
    print("=" * 80)
    print(f"Source Path:       {path_x}")
    print(f"Destination Path:  {path_y}")
    print()
    print(f"Total Scanned:     {found_count + missing_count}")
    print(f"Found in Backup:   {found_count}")
    print(f"Missing:           {missing_count}")

    if found_count + missing_count > 0:
        backup_rate = (found_count / (found_count + missing_count)) * 100
        print(f"Backup Rate:       {backup_rate:.1f}%")

    print("=" * 80)

    # 6. Export report to file
    if missing_files:
        report_file = "mp4_comparison_report.txt"
        try:
            with open(report_file, 'w', encoding='utf-8') as f:
                f.write("=" * 80 + "\n")
                f.write("MP4 FILES COMPARISON REPORT\n")
                f.write("=" * 80 + "\n")
                f.write(f"Source Path:       {path_x}\n")
                f.write(f"Destination Path:  {path_y}\n")
                f.write(f"Generated:         {__import__('datetime').datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
                f.write("\n")
                f.write(f"Total Scanned:     {found_count + missing_count}\n")
                f.write(f"Found in Backup:   {found_count}\n")
                f.write(f"Missing:           {missing_count}\n")
                f.write("\n")
                f.write("=" * 80 + "\n")
                f.write("MISSING FILES:\n")
                f.write("-" * 80 + "\n")

                for item in missing_files:
                    f.write(f"\nFile: {item['rel_path']}\n")
                    f.write(f"  Size: {item['size_mb']:.2f} MB\n")
                    f.write(f"  Full Path: {item['full_path']}\n")

                f.write("-" * 80 + "\n")

            print(f"\nReport saved to: {os.path.abspath(report_file)}")

        except Exception as e:
            print(f"\nWarning: Could not save report to file: {e}")


def main():
    """Main function to handle command-line arguments."""
    print("=" * 80)
    print("MP4 FILES COMPARISON TOOL")
    print("=" * 80)
    print()

    # Parse command-line arguments
    if len(sys.argv) == 3:
        # Custom paths provided
        path_x = sys.argv[1]
        path_y = sys.argv[2]
        print(f"Using custom paths:")
        print(f"  Source:      {path_x}")
        print(f"  Destination: {path_y}")
    elif len(sys.argv) == 1:
        # Use default paths
        path_x = DEFAULT_PATH_X
        path_y = DEFAULT_PATH_Y
        print(f"Using default paths:")
        print(f"  Source:      {path_x}")
        print(f"  Destination: {path_y}")
    else:
        print("Usage:")
        print("  python compare_mp4.py                           # Use default paths")
        print("  python compare_mp4.py <source> <destination>    # Use custom paths")
        print()
        print("Examples:")
        print("  python compare_mp4.py")
        print(f"  python compare_mp4.py {DEFAULT_PATH_X} {DEFAULT_PATH_Y}")
        sys.exit(1)

    print()

    # Run comparison
    check_files(path_x, path_y)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\nComparison interrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n\nERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
