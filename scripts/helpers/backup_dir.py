"""
Copy files to a destination path while preserving their directory structure.

This script takes a destination path and a list of source file paths,
then copies each file to the destination while maintaining the original
directory structure.
"""

import os
import shutil
import argparse
from pathlib import Path


def copy_with_structure(source_files, destination):
    """
    Copy files to destination while preserving directory structure.

    Args:
        source_files: List of source file paths to copy
        destination: Destination root directory
    """
    destination = Path(destination).resolve()

    # Create destination directory if it doesn't exist
    destination.mkdir(parents=True, exist_ok=True)

    copied_count = 0
    error_count = 0

    for source_file in source_files:
        try:
            source_path = Path(source_file).resolve()

            # Check if source file exists
            if not source_path.exists():
                print(f"❌ Source file not found: {source_file}")
                error_count += 1
                continue

            if not source_path.is_file():
                print(f"⚠️  Skipping (not a file): {source_file}")
                continue

            # Get the path components
            parts = source_path.parts

            # Find "Recordings" in the path and take everything after it
            recordings_index = -1
            for i, part in enumerate(parts):
                if part.lower() == "recordings":
                    recordings_index = i
                    break

            if recordings_index == -1 or recordings_index == len(parts) - 1:
                # "Recordings" not found or it's the last part, use filename only
                print(f"⚠️  'Recordings' folder not found in path, using filename only: {source_file}")
                relative_parts = [parts[-1]]  # Just the filename
            else:
                # Take everything after "Recordings"
                relative_parts = parts[recordings_index + 1:]

            dest_file_path = destination / Path(*relative_parts)

            # Create parent directories
            dest_file_path.parent.mkdir(parents=True, exist_ok=True)

            # Copy the file
            shutil.copy2(source_path, dest_file_path)
            print(f"✓ Copied: {source_path} -> {dest_file_path}")
            copied_count += 1

        except Exception as e:
            print(f"❌ Error copying {source_file}: {e}")
            error_count += 1

    print(f"\n{'='*60}")
    print(f"Summary: {copied_count} file(s) copied successfully")
    if error_count > 0:
        print(f"         {error_count} error(s) occurred")
    print(f"{'='*60}")


def main():
    parser = argparse.ArgumentParser(
        description="Copy files to destination preserving directory structure",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python backup_dir.py -d D:\\backup file1.txt file2.txt
  python backup_dir.py --destination /mnt/backup /home/user/docs/*.txt
  python backup_dir.py -d "C:\\My Backup" "C:\\folder\\file.mp4"
        """
    )

    parser.add_argument(
        '-d', '--destination',
        type=str,
        help='Destination root directory where files will be copied'
    )

    parser.add_argument(
        'files',
        nargs='*',
        help='Source file paths to copy'
    )

    args = parser.parse_args()

    # Get destination path
    if args.destination:
        destination = args.destination
    else:
        destination = input("Enter destination path: ").strip()

    if not destination:
        print("❌ Error: Destination path is required")
        return

    # Get source files
    source_files = args.files

    if not source_files:
        print("\nEnter source file paths (one per line, empty line to finish):")
        source_files = []
        while True:
            line = input().strip()
            if not line:
                break
            source_files.append(line)

    if not source_files:
        print("❌ Error: No source files provided")
        return

    print(f"\nDestination: {destination}")
    print(f"Files to copy: {len(source_files)}")
    print(f"{'-'*60}\n")

    copy_with_structure(source_files, destination)


if __name__ == "__main__":
    main()
