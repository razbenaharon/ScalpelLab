"""
Simple File Copier

Copies a list of specified files to a destination directory.
Creates the destination directory if it doesn't exist.

Usage:
    python scripts/helpers/copy_files.py file1.mp4 file2.mp4 /destination/dir

Args:
    source_files: One or more source file paths to copy
    destination_dir: The destination directory where files will be copied

Features:
    - Validates source files exist before copying
    - Creates destination directory if needed
    - Preserves file metadata (using shutil.copy2)
    - Reports success/failure for each file
"""

import argparse
import os
import shutil

def copy_files(source_files, destination_dir):
    """
    Copies a list of files to a specified destination directory.
    """
    if not os.path.exists(destination_dir):
        print(f"Destination directory '{destination_dir}' does not exist. Creating it.")
        os.makedirs(destination_dir)
    elif not os.path.isdir(destination_dir):
        print(f"Error: Destination path '{destination_dir}' is not a directory.")
        return

    print(f"Attempting to copy {len(source_files)} file(s) to '{destination_dir}'...")
    
    for src_file in source_files:
        if not os.path.exists(src_file):
            print(f"Warning: Source file '{src_file}' does not exist. Skipping.")
            continue
        if not os.path.isfile(src_file):
            print(f"Warning: Source path '{src_file}' is not a file. Skipping.")
            continue

        try:
            shutil.copy2(src_file, destination_dir)
            print(f"Successfully copied '{src_file}' to '{destination_dir}'")
        except Exception as e:
            print(f"Error copying '{src_file}': {e}")

def main():
    parser = argparse.ArgumentParser(description="Copies a list of specified files to a destination directory.")
    parser.add_argument("source_files", nargs='+', help="One or more source file paths to copy.")
    parser.add_argument("destination_dir", help="The destination directory where files will be copied.")

    args = parser.parse_args()

    copy_files(args.source_files, args.destination_dir)

if __name__ == "__main__":
    main()
