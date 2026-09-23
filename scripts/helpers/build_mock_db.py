"""Build the public mock database from the private ScalpelDatabase.sqlite.

The real catalog stays on the research machine and is gitignored. The public
repository ships ``sample_data/ScalpelDatabase_mock.sqlite`` instead: the same
schema, views and recording metadata (dates, cases, cameras, file sizes, SEQ
analysis), with every person-identifying value replaced by a stable
pseudonym:

- ``anesthesiology.name``           -> ``Anesthesiologist NN``
- ``anesthesiology.code`` and
  ``recording_details.code``        -> initials swapped for synthetic letters,
                                       the numeric suffix kept so seniority
                                       charts still work
- ``analysis_information.label_by`` -> ``Annotator X``

The output is written with ``VACUUM INTO`` so no freed page still holds an
original value, and the finished file is byte-scanned for every original
value before the script reports success.

Example:
    Rebuild the mock after the real catalog changes::

        $ python scripts/helpers/build_mock_db.py
        $ python scripts/helpers/build_mock_db.py --source D:/other.sqlite --force
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from config import MOCK_DB_PATH, REAL_DB_PATH  # noqa: E402


# Placeholder roster entries use these words; they are not personal and also
# appear in camera names (General_3), so they must not count as leaks.
GENERIC_NAME_WORDS = {"general", "senior"}


def _synthetic_initials(index: int) -> str:
    """Return two letters (AA, AB, ... ZZ) for a zero-based index."""
    return chr(ord("A") + (index // 26) % 26) + chr(ord("A") + index % 26)


def _collect_sensitive(conn: sqlite3.Connection) -> set[str]:
    values: set[str] = set()
    queries = (
        "SELECT name FROM anesthesiology",
        "SELECT code FROM anesthesiology",
        "SELECT code FROM recording_details",
        "SELECT label_by FROM analysis_information",
    )
    for sql in queries:
        for (value,) in conn.execute(sql):
            if value and str(value).strip():
                values.add(str(value).strip())
    # Surnames and given names on their own can still identify someone.
    for (name,) in conn.execute("SELECT name FROM anesthesiology"):
        for part in str(name or "").split():
            if len(part) >= 4 and part.lower() not in GENERIC_NAME_WORDS:
                values.add(part)
    return values


def anonymize(conn: sqlite3.Connection) -> dict[str, int]:
    """Replace person-identifying values in place.

    Args:
        conn: Connection to a scratch copy of the real database.

    Returns:
        dict[str, int]: Number of distinct values replaced per column group.
    """
    keys = [k for (k,) in conn.execute(
        "SELECT anesthesiology_key FROM anesthesiology ORDER BY anesthesiology_key")]
    for i, key in enumerate(keys, start=1):
        conn.execute("UPDATE anesthesiology SET name = ? WHERE anesthesiology_key = ?",
                     (f"Anesthesiologist {i:02d}", key))

    codes = sorted({c for (c,) in conn.execute(
        "SELECT code FROM anesthesiology UNION SELECT code FROM recording_details")
        if c})
    originals = set(codes)
    code_map = {}
    candidate = 0
    for code in codes:
        digits = "".join(ch for ch in code if ch.isdigit())
        # A synthetic code must never equal some other person's real code.
        while (new := _synthetic_initials(candidate) + digits) in originals:
            candidate += 1
        code_map[code] = new
        candidate += 1
    # One pass through a mapping table, so a new value is never remapped again.
    conn.execute("CREATE TEMP TABLE code_map (old TEXT PRIMARY KEY, new TEXT)")
    conn.executemany("INSERT INTO code_map VALUES (?, ?)", code_map.items())
    for table in ("anesthesiology", "recording_details"):
        conn.execute(f"UPDATE {table} SET code = (SELECT new FROM code_map WHERE old = code) "
                     "WHERE code IN (SELECT old FROM code_map)")
    conn.execute("DROP TABLE code_map")

    labelers = sorted({l for (l,) in conn.execute(
        "SELECT DISTINCT label_by FROM analysis_information") if l})
    for i, labeler in enumerate(labelers):
        conn.execute("UPDATE analysis_information SET label_by = ? WHERE label_by = ?",
                     (f"Annotator {chr(ord('A') + i)}", labeler))
    conn.commit()
    return {"names": len(keys), "codes": len(code_map), "labelers": len(labelers)}


def find_leaks(db_file: Path, sensitive: set[str]) -> list[str]:
    """Return the original values still present anywhere in ``db_file``'s bytes."""
    blob = db_file.read_bytes().lower()
    leaks = []
    for value in sensitive:
        for encoding in ("utf-8", "utf-16-le"):
            if value.lower().encode(encoding) in blob:
                leaks.append(value)
                break
    return leaks


def build(source: Path, output: Path) -> None:
    """Copy ``source``, anonymize it and write a compacted file to ``output``."""
    with tempfile.TemporaryDirectory() as tmp:
        scratch = Path(tmp) / "scratch.sqlite"
        with sqlite3.connect(source) as src, sqlite3.connect(scratch) as dst:
            src.backup(dst)
            sensitive = _collect_sensitive(dst)
            counts = anonymize(dst)
            output.parent.mkdir(parents=True, exist_ok=True)
            dst.execute("VACUUM INTO ?", (str(output),))
        # Close before TemporaryDirectory cleanup; Windows keeps the file locked otherwise.
        dst.close()

    leaks = find_leaks(output, sensitive)
    if leaks:
        output.unlink()
        raise SystemExit(f"Refusing to keep mock DB: {len(leaks)} original values still present.")
    print(f"Mock DB written: {output}")
    print(f"  replaced {counts['names']} names, {counts['codes']} codes, "
          f"{counts['labelers']} annotators; byte scan clean ({len(sensitive)} values checked)")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--source", type=Path, default=REAL_DB_PATH, help="private catalog to read")
    ap.add_argument("--output", type=Path, default=MOCK_DB_PATH, help="mock catalog to write")
    ap.add_argument("--force", action="store_true", help="overwrite an existing output file")
    args = ap.parse_args()

    if not args.source.is_file():
        raise SystemExit(f"Source DB not found: {args.source}")
    if args.source.resolve() == args.output.resolve():
        raise SystemExit("Refusing to overwrite the source database.")
    if args.output.exists():
        if not args.force:
            raise SystemExit(f"{args.output} exists; pass --force to rebuild it.")
        os.remove(args.output)
    build(args.source, args.output)


if __name__ == "__main__":
    main()
