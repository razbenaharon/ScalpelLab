import os

# --- Configuration ---
# Replace these with your actual paths
PATH_X = r'H:/'  # Where the .seq files are
PATH_Y = r'F:\Room_8_Data\Sequence_Backup'  # Where to check for them


# ---------------------

def check_files():
    # 1. Verify paths exist
    if not os.path.exists(PATH_X) or not os.path.exists(PATH_Y):
        print("Error: One of the paths provided does not exist.")
        return

    # 2. Get a snapshot of files in Path Y for comparison (including subdirectories)
    # We use a dict to store (filename, size) -> full path for O(1) lookup speed
    try:
        files_in_y = {}  # Key: (filename, size), Value: full path in Y
        for root, dirs, files in os.walk(PATH_Y):
            for filename in files:
                if filename.lower().endswith(".seq"):
                    full_path = os.path.join(root, filename)
                    try:
                        file_size = os.path.getsize(full_path)
                        # Store (filename, size) as key
                        key = (filename, file_size)
                        files_in_y[key] = full_path
                    except OSError:
                        # Skip files we can't access
                        pass
    except PermissionError:
        print(f"Error: Permission denied accessing {PATH_Y}")
        return

    print(f"Scanning {PATH_X} for .seq files (including subdirectories)...\n")
    print(f"Comparing by filename and file size...\n")
    print(f"Showing MISSING files only:\n")

    found_count = 0
    missing_count = 0

    # 3. Scan Path X recursively
    for root, dirs, files in os.walk(PATH_X):
        for filename in files:
            # Check for .seq extension (case insensitive)
            if filename.lower().endswith(".seq"):
                full_path = os.path.join(root, filename)
                rel_path = os.path.relpath(full_path, PATH_X)

                try:
                    file_size = os.path.getsize(full_path)
                    key = (filename, file_size)

                    if key in files_in_y:
                        found_count += 1
                    else:
                        size_mb = file_size / (1024 * 1024)
                        print(f"[MISSING] {rel_path} ({size_mb:.2f} MB)")
                        missing_count += 1
                except OSError:
                    print(f"[ERROR]   {rel_path} (cannot read file)")
                    missing_count += 1

    # 4. Summary
    print("-" * 30)
    print(f"Summary: {found_count} found, {missing_count} missing.")


if __name__ == "__main__":
    check_files()