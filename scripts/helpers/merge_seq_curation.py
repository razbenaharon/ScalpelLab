#!/usr/bin/env python3
"""Merge 'Sequence curation' into 'Sequence_Backup', resolving duplicates safely.

Walks the source tree recursively and merges it into the destination tree:

* Identical files (verified by hash) are removed from the source instead of
  re-copied — avoiding redundant work and redundant data.
* Same-name-but-different-content files are NOT overwritten; the incoming file
  is renamed with a unique suffix before being moved.
* Empty source directories are cleaned up once their contents are merged.

Set DRY_RUN = True to log every action without touching the filesystem.
"""

import hashlib
import io
import os
import shutil
import sys

# Windows console is CP1252 by default; force UTF-8 so log output never crashes.
if hasattr(sys.stdout, "buffer") and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout = io.TextIOWrapper(
        sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True
    )

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DRY_RUN = False  # <-- Set to False to actually move/delete files.

TARGET_LOCATION = r"G:\Workstation_F_Backup_250819"
SOURCE_DIR = os.path.join(TARGET_LOCATION, "Sequence curation")   # move FROM
DEST_DIR = os.path.join(TARGET_LOCATION, "Sequence_Backup")       # move TO

# Comparison strategy for duplicate detection.
#   "size_mtime" -> identical if size AND modification time match (fast: metadata
#                   only, no bulk reads). Chosen for the 1.7 TB dataset.
#   "hash"       -> identical if size matches AND full content hash matches
#                   (exact, but reads every same-size collision -> hours).
COMPARE_MODE = "size_mtime"

HASH_ALGORITHM = "sha256"   # used only when COMPARE_MODE == "hash"
HASH_CHUNK_SIZE = 1024 * 1024  # 1 MiB read buffer for hashing large .seq files
MTIME_TOLERANCE_S = 2  # FAT/NTFS mtime granularity slack (seconds)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def log(action: str, message: str) -> None:
    """Print a uniform, prefixed log line.

    Args:
        action: Short verb describing the action (e.g. "MOVED", "SKIP").
        message: Human-readable detail for the log line.
    """
    prefix = "[DRY-RUN] " if DRY_RUN else ""
    print(f"{prefix}{action:<16} {message}")


def file_hash(path: str) -> str:
    """Return the hex digest of a file using the configured algorithm.

    Reads in chunks so multi-gigabyte video files don't load into memory.

    Args:
        path: Absolute path to the file to hash.

    Returns:
        Hex digest string of the file's contents.
    """
    digest = hashlib.new(HASH_ALGORITHM)
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(HASH_CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


def files_are_identical(path_a: str, path_b: str) -> bool:
    """Decide whether two files are duplicates per the configured COMPARE_MODE.

    A size mismatch always means "different" (cheap short-circuit). Beyond that:
      * "size_mtime" -> equal sizes and modification times (within tolerance).
      * "hash"       -> equal sizes and equal full content hashes.

    Args:
        path_a: First file path.
        path_b: Second file path.

    Returns:
        True if both files are considered identical duplicates.
    """
    if os.path.getsize(path_a) != os.path.getsize(path_b):
        return False
    if COMPARE_MODE == "size_mtime":
        return abs(os.path.getmtime(path_a) - os.path.getmtime(path_b)) <= MTIME_TOLERANCE_S
    return file_hash(path_a) == file_hash(path_b)


def unique_conflict_path(dest_path: str) -> str:
    """Build a non-colliding destination path by appending a numeric suffix.

    Example: ``video.seq`` -> ``video_conflicting_1.seq`` (and _2, _3, ...
    if earlier conflict names are also taken).

    Args:
        dest_path: The intended (already-taken) destination path.

    Returns:
        A path under the same directory that does not yet exist.
    """
    directory, filename = os.path.split(dest_path)
    stem, ext = os.path.splitext(filename)
    counter = 1
    while True:
        candidate = os.path.join(directory, f"{stem}_conflicting_{counter}{ext}")
        if not os.path.exists(candidate):
            return candidate
        counter += 1


# ---------------------------------------------------------------------------
# Core operations
# ---------------------------------------------------------------------------

def handle_file(src_file: str, dest_file: str) -> None:
    """Merge a single source file into its destination location.

    Three cases:
      1. No destination file exists -> move the source file across.
      2. Destination exists and is identical -> delete the source copy.
      3. Destination exists but differs -> move source under a unique name.

    Args:
        src_file: Absolute path of the file in the source tree.
        dest_file: Intended absolute path in the destination tree.
    """
    try:
        if not os.path.exists(dest_file):
            # Case 1: clean move — ensure parent dir exists, then move.
            dest_parent = os.path.dirname(dest_file)
            if not os.path.isdir(dest_parent):
                log("MKDIR", dest_parent)
                if not DRY_RUN:
                    os.makedirs(dest_parent, exist_ok=True)
            log("MOVED", f"{src_file} -> {dest_file}")
            if not DRY_RUN:
                shutil.move(src_file, dest_file)
            return

        # A file already exists at the destination — compare contents.
        if files_are_identical(src_file, dest_file):
            # Case 2: exact duplicate — drop the redundant source copy.
            log("DELETED DUP", f"{src_file} (identical to destination)")
            if not DRY_RUN:
                os.remove(src_file)
        else:
            # Case 3: name clash, different content — keep both.
            new_dest = unique_conflict_path(dest_file)
            log("RENAMED", f"{src_file} -> {new_dest} (name clash, differing content)")
            if not DRY_RUN:
                shutil.move(src_file, new_dest)

    except PermissionError as exc:
        log("ERROR PERM", f"{src_file}: {exc}")
    except OSError as exc:
        log("ERROR IO", f"{src_file}: {exc}")


def prune_empty_dir(directory: str) -> None:
    """Remove a directory if it is empty, logging the outcome.

    Args:
        directory: Absolute path of the directory to attempt to remove.
    """
    try:
        # In dry-run, files weren't actually moved, so dirs won't appear empty;
        # we still log the intent so the simulation is complete.
        if DRY_RUN:
            log("RMDIR", f"{directory} (if empty)")
            return
        if os.path.isdir(directory) and not os.listdir(directory):
            os.rmdir(directory)
            log("RMDIR", directory)
    except OSError as exc:
        log("ERROR RMDIR", f"{directory}: {exc}")


def merge_directories(source_root: str, dest_root: str) -> None:
    """Recursively merge ``source_root`` into ``dest_root``.

    Walks bottom-up so that, after files are moved out of each directory,
    the now-empty source subdirectories can be pruned on the way back up.

    Args:
        source_root: Root of the tree to move files out of.
        dest_root: Root of the tree to move files into.
    """
    # topdown=False => children are processed before their parents, which lets
    # us delete emptied subdirectories immediately after handling their files.
    for current_dir, _subdirs, files in os.walk(source_root, topdown=False):
        rel_path = os.path.relpath(current_dir, source_root)
        dest_dir = dest_root if rel_path == "." else os.path.join(dest_root, rel_path)

        for filename in files:
            src_file = os.path.join(current_dir, filename)
            dest_file = os.path.join(dest_dir, filename)
            handle_file(src_file, dest_file)

        # Don't try to remove the source root here; that's handled at the end.
        if os.path.abspath(current_dir) != os.path.abspath(source_root):
            prune_empty_dir(current_dir)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> int:
    """Validate paths, run the merge, then clean up the source root."""
    print("=" * 70)
    print(f"Merge '{SOURCE_DIR}'")
    print(f"  into '{DEST_DIR}'")
    print(f"Mode: {'DRY-RUN (no changes will be made)' if DRY_RUN else 'LIVE'}")
    print(f"Compare: {COMPARE_MODE}" + (f" ({HASH_ALGORITHM})" if COMPARE_MODE == "hash" else ""))
    print("=" * 70)

    if not os.path.isdir(SOURCE_DIR):
        log("ABORT", f"Source directory does not exist: {SOURCE_DIR}")
        return 1

    # Create the destination root if missing (e.g. first-ever merge).
    if not os.path.isdir(DEST_DIR):
        log("MKDIR", DEST_DIR)
        if not DRY_RUN:
            os.makedirs(DEST_DIR, exist_ok=True)

    merge_directories(SOURCE_DIR, DEST_DIR)

    # Finally, remove the (now hopefully empty) source root.
    prune_empty_dir(SOURCE_DIR)

    print("=" * 70)
    print("Done." if not DRY_RUN else "Dry-run complete. Review the log above, then set DRY_RUN = False.")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
