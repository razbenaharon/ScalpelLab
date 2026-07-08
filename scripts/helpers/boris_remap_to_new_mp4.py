"""Remap BORIS event timing from the OLD MP4 timeline to the NEW MP4 timeline.

BORIS annotations are logged against the *OLD* per-camera MP4 (one frame per
SEQ frame, identity mapping ``i_old == K_seq``). The NEW MP4 is a CFR 30fps,
sync-padded re-encode produced by ``scripts/3_seq_to_mp4_convert.py``. This
script applies the end-to-end formula from
``docs/new recordings formula.md`` to translate every event's frame/time into
the NEW MP4 coordinate system::

    new_frame = new_pre_roll_frames + round((idx_ts[i_old] - idx_ts[0]) * 30)
    new_pre_roll_frames = round((first_frame_time - group_t_global_start) * 30)

where ``idx_ts`` are the per-frame hardware timestamps of the *reference*
camera (the one the BORIS row's ``Media file name`` points at), and
``group_t_global_start`` is the minimum first-frame time across all non-JUNK
cameras of the case (the sync-group anchor used by the converter).

The IDX record layout and timestamp decoding mirror
``scripts/3_seq_to_mp4_convert.py`` (32-byte little-endian records, ts_sub =
ms in low word, us in high word) so the result matches the real NEW MP4. Like
the converter, only the *first* SEQ file per camera is used (the converter does
not concatenate splits).

A new CSV is written next to each source BORIS file on the secure data volume;
nothing is written into the repo.

Usage:
    # one CSV
    python scripts/helpers/boris_remap_to_new_mp4.py \
        --csv "F:\\Room_8_Data\\Analyses\\Case_Analyses_synced\\DATA_24-02-08\\Case1\\Boris\\24_02_08-case1_standardized.csv"

    # every BORIS CSV under the analyses root
    python scripts/helpers/boris_remap_to_new_mp4.py --all
"""

import argparse
import bisect
import csv
import io
import os
import re
import sqlite3
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import config  # noqa: E402

# Windows console is CP1252 — force UTF-8 for non-ASCII output.
if hasattr(sys.stdout, "buffer") and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                                  errors="replace", line_buffering=True)

TARGET_FPS = 30  # NEW MP4 frame rate (matches 3_seq_to_mp4_convert.py)
DEFAULT_ANALYSES_ROOT = config.ANALYSES_ROOT

# IDX binary layout (mirrors 3_seq_to_mp4_convert.py).
IDX_RECORD_SIZE = 32
IDX_STRUCT = struct.Struct("<QIIIIIi")

# Columns appended to every remapped row.
NEW_COLS = ["new_mp4_ref_camera", "new_mp4_old_frame",
            "new_mp4_frame", "new_mp4_time_seconds", "new_mp4_timecode"]

# (recording_date, case_no) pairs the formula explicitly does NOT cover
# (docs/new recordings formula.md §4).
FORMULA_EXCEPTIONS = {
    ("2023-09-26", 1),
    ("2023-09-27", 2),
    ("2024-01-01", 1),
    ("2024-01-08", 2),
    ("2024-02-06", 1),
    ("2024-02-20", 1),
}


def decode_timestamp(ts_seconds: int, ts_sub: int) -> float:
    """Decode a NorPix IDX packed timestamp into Unix seconds (float)."""
    ms = ts_sub & 0xFFFF
    us = (ts_sub >> 16) & 0xFFFF
    return ts_seconds + ms / 1000.0 + us / 1000000.0


def read_idx_timestamps(idx_path: str) -> list[float]:
    """Return decoded per-frame timestamps for every non-empty IDX record."""
    with open(idx_path, "rb") as f:
        raw = f.read()
    n = len(raw) // IDX_RECORD_SIZE
    out: list[float] = []
    for i in range(n):
        chunk = raw[i * IDX_RECORD_SIZE:(i + 1) * IDX_RECORD_SIZE]
        _offset, size, ts_sec, ts_sub, _res, _flags, _frame_no = IDX_STRUCT.unpack(chunk)
        if size == 0:
            continue
        out.append(decode_timestamp(ts_sec, ts_sub))
    return out


def _norm(name: str) -> str:
    """Normalise a camera/media name (drop spaces/underscores, uppercase)."""
    return re.sub(r"[\s_]+", "", name).upper()


def timecode(seconds: float) -> str:
    """Format seconds as HH:MM:SS.mmm."""
    ms = int(round(seconds * 1000))
    h, rem = divmod(ms, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"


def parse_data_case(path: str) -> tuple[str, int]:
    """Derive (YYYY-MM-DD, case_no) from a path containing DATA_YY-MM-DD\\CaseN."""
    parts = path.replace("/", "\\").split("\\")
    date = case_no = None
    for p in parts:
        if p.upper().startswith("DATA_") and len(p) == 13:
            date = f"20{p[5:7]}-{p[8:10]}-{p[11:13]}"
        if p.lower().startswith("case") and p[4:].isdigit():
            case_no = int(p[4:])
    if not date or case_no is None:
        raise ValueError(f"Cannot parse DATA_/Case from path: {path}")
    return date, case_no


def nearest_frame_by_time(rel_ts: list[float], rel_time: float) -> int:
    """Index of the frame whose relative timestamp is closest to ``rel_time``."""
    j = bisect.bisect_left(rel_ts, rel_time)
    if j <= 0:
        return 0
    if j >= len(rel_ts):
        return len(rel_ts) - 1
    return j if (rel_ts[j] - rel_time) < (rel_time - rel_ts[j - 1]) else j - 1


class CaseRemapper:
    """Holds the IDX/timeline context for one case and remaps its BORIS CSVs."""

    def __init__(self, date: str, case_no: int, db_path=config.DB_PATH):
        self.date = date
        self.case_no = case_no
        self._data_root = os.path.dirname(config.SEQ_ROOT.rstrip("\\"))
        con = sqlite3.connect(db_path)
        rows = con.execute(
            "SELECT camera_name, first_frame_time FROM seq_enriched "
            "WHERE recording_date=? AND case_no=?", (date, case_no)).fetchall()
        self._ff = {cam: t for cam, t in rows}
        # seq file path(s) per camera, in catalogue order.
        self._seq_paths: dict[str, list[str]] = {}
        for cam, path in con.execute(
                "SELECT camera_name, path FROM seq_status "
                "WHERE recording_date=? AND case_no=? ORDER BY path", (date, case_no)):
            self._seq_paths.setdefault(cam, []).append(path)
        con.close()

        non_junk = [t for cam, t in rows
                    if t is not None and not cam.lower().endswith("_junk")]
        if not non_junk:
            raise ValueError(f"No SEQ data in DB for {date} case {case_no}")
        self.t0_global = min(non_junk)
        self._by_norm = {_norm(cam): cam for cam in self._ff}

        self._rel: dict[str, list[float]] = {}
        self._pre: dict[str, int] = {}

    def resolve_camera(self, media_basename: str) -> str:
        """Map a BORIS 'Media file name' to a DB camera name (space/underscore-insensitive).

        Falls back to a unique prefix match so a suffix-dropped media name like
        ``General.mp4`` resolves to the ``General_3`` camera.
        """
        key = _norm(os.path.splitext(media_basename)[0])
        if key in self._by_norm:
            return self._by_norm[key]
        prefixed = [cam for norm, cam in self._by_norm.items() if norm.startswith(key)]
        if len(prefixed) == 1:
            return prefixed[0]
        raise KeyError(f"media '{media_basename}' has no camera in {sorted(self._ff)}")

    def _ensure(self, cam: str) -> None:
        """Lazily load the reference camera's IDX timestamps and pre-roll."""
        if cam in self._rel:
            return
        paths = self._seq_paths.get(cam)
        if not paths:
            raise FileNotFoundError(f"no SEQ row for camera {cam}")
        if len(paths) > 1:
            print(f"    WARNING: {cam} has {len(paths)} SEQ files; using first "
                  f"(matches converter): {paths[0]}")
        seq_rel = paths[0]
        seq_abs = seq_rel if os.path.isabs(seq_rel) else os.path.join(self._data_root, seq_rel)
        idx_abs = seq_abs + ".idx"
        if not os.path.exists(idx_abs):
            raise FileNotFoundError(f"IDX not found for {cam}: {idx_abs}")
        ts = read_idx_timestamps(idx_abs)
        if not ts:
            raise ValueError(f"empty IDX for {cam}: {idx_abs}")
        self._rel[cam] = [t - ts[0] for t in ts]
        pr_sec = self._ff[cam] - self.t0_global
        self._pre[cam] = int(round(pr_sec * TARGET_FPS))
        print(f"    [{cam}] frames={len(ts)} pre_roll={self._pre[cam]} ({pr_sec:.3f}s)")

    def map_row(self, row: dict) -> bool:
        """Add NEW_COLS to a single BORIS row in place.

        Returns True if the row was mapped. Blank/event-less rows (no media or
        no time) pass through with empty NEW_COLS and return False.
        """
        media = (row.get("Media file name") or "").strip()
        time_str = (row.get("Time") or "").strip()
        if not media or not time_str:
            for c in NEW_COLS:
                row[c] = ""
            return False
        cam = self.resolve_camera(os.path.basename(media))
        self._ensure(cam)
        rel = self._rel[cam]
        idx_str = (row.get("Image index") or "").strip()
        if idx_str:
            i_old = max(0, min(int(round(float(idx_str))), len(rel) - 1))
        else:
            i_old = nearest_frame_by_time(rel, float(row["Time"]))
        new_frame = self._pre[cam] + int(round(rel[i_old] * TARGET_FPS))
        new_time = new_frame / TARGET_FPS
        row["new_mp4_ref_camera"] = cam
        row["new_mp4_old_frame"] = i_old
        row["new_mp4_frame"] = new_frame
        row["new_mp4_time_seconds"] = f"{new_time:.3f}"
        row["new_mp4_timecode"] = timecode(new_time)
        return True

    def remap_csv(self, src: str, out: str | None = None) -> tuple[str, int, int]:
        """Read ``src``, append the NEW MP4 columns, write a new CSV.

        Returns (output_path, n_mapped, n_total). Raises if no row could be
        mapped (so the caller reports the file as skipped).
        """
        with open(src, encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            fields = list(reader.fieldnames or [])
            rows = list(reader)
        if "Media file name" not in fields or "Time" not in fields:
            raise ValueError("CSV lacks 'Media file name'/'Time' columns")
        n_mapped = sum(1 for row in rows if self.map_row(row))
        if rows and n_mapped == 0:
            raise ValueError("no mappable rows (all blank or unresolvable media)")
        out = out or (os.path.splitext(src)[0] + "_new_mp4.csv")
        out_fields = fields + [c for c in NEW_COLS if c not in fields]
        with open(out, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=out_fields)
            w.writeheader()
            w.writerows(rows)
        return out, n_mapped, len(rows)


def find_boris_csvs(root: str) -> list[str]:
    """All BORIS source CSVs under ``root`` (excludes our own *_new_mp4.csv output)."""
    found = []
    for dirpath, _dirs, files in os.walk(root):
        if os.path.basename(dirpath).lower() != "boris":
            continue
        for fn in files:
            if fn.lower().endswith(".csv") and not fn.lower().endswith("_new_mp4.csv"):
                found.append(os.path.join(dirpath, fn))
    return sorted(found)


def process_one(src: str, force: bool, cache: dict | None = None) -> tuple[str, str]:
    """Remap a single CSV. Returns (status, detail) for the run summary.

    ``cache`` (optional) reuses a CaseRemapper across the raw/standardized CSVs
    of the same case so each camera's IDX is parsed only once.
    """
    date, case_no = parse_data_case(src)
    if (date, case_no) in FORMULA_EXCEPTIONS and not force:
        return "skipped-exception", f"{date} case {case_no}"
    try:
        key = (date, case_no)
        rm = cache.get(key) if cache is not None else None
        if rm is None:
            rm = CaseRemapper(date, case_no)
            if cache is not None:
                cache[key] = rm
        out, n_mapped, n_total = rm.remap_csv(src)
        return "ok", f"{os.path.basename(out)} ({n_mapped}/{n_total} rows mapped)"
    except (ValueError, KeyError, FileNotFoundError) as e:
        return "skipped-nodata", f"{date} case {case_no}: {e}"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--csv", help="Remap a single BORIS CSV")
    g.add_argument("--all", action="store_true",
                   help="Remap every BORIS CSV under --root")
    ap.add_argument("--root", default=DEFAULT_ANALYSES_ROOT,
                    help=f"Analyses root for --all (default: {DEFAULT_ANALYSES_ROOT})")
    ap.add_argument("--out", default=None, help="Output path (single-CSV mode only)")
    ap.add_argument("--force", action="store_true",
                    help="Process even cases in the formula exceptions table")
    args = ap.parse_args()

    if args.csv:
        date, case_no = parse_data_case(args.csv)
        if (date, case_no) in FORMULA_EXCEPTIONS and not args.force:
            sys.exit(f"ABORT: ({date}, case {case_no}) is a formula exception "
                     f"(docs/new recordings formula.md §4). Use --force to override.")
        print(f"Case {date} case {case_no}")
        out, n_mapped, n_total = CaseRemapper(date, case_no).remap_csv(args.csv, args.out)
        print(f"Wrote: {out} ({n_mapped}/{n_total} rows mapped)")
        return

    srcs = find_boris_csvs(args.root)
    print(f"Found {len(srcs)} BORIS CSV(s) under {args.root}\n")
    summary = {"ok": [], "skipped-exception": [], "skipped-nodata": []}
    cache: dict = {}
    for src in srcs:
        rel = os.path.relpath(src, args.root)
        print(f"- {rel}")
        status, detail = process_one(src, args.force, cache)
        summary[status].append((rel, detail))
        if status == "ok":
            print(f"    -> {detail}")
        else:
            print(f"    {status}: {detail}")

    print("\n==================== SUMMARY ====================")
    print(f"  remapped         : {len(summary['ok'])}")
    print(f"  skipped (exc)    : {len(summary['skipped-exception'])}")
    print(f"  skipped (no data): {len(summary['skipped-nodata'])}")
    if summary["skipped-exception"]:
        print("\n  Formula exceptions (not remapped):")
        for rel, _ in summary["skipped-exception"]:
            print(f"    - {rel}")
    if summary["skipped-nodata"]:
        print("\n  No SEQ/IDX data — could not remap:")
        for rel, detail in summary["skipped-nodata"]:
            print(f"    - {detail}")


if __name__ == "__main__":
    main()
