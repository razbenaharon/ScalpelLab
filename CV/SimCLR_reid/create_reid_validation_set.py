"""
create_reid_validation_set.py - Manual identity labeling for SimCLR Re-ID crops.

This tool builds a small evaluation-only Re-ID validation set from the flat
SimCLR crop dataset. It never moves or edits source images. Each accepted crop
is copied into an identity folder such as person_01, person_02, and so on, while
labels.csv and summary.json record what was reviewed.

Validation-set guidance:
  - Prefer clear, single-person crops where identity is unambiguous.
  - Skip blurry, partial, occluded, or uncertain crops.
  - Skip crops containing more than one person if the target identity is unclear.
  - Avoid many near-duplicates from the same burst; choose different frame ranges,
    bursts, and video_idx values when possible.
  - Aim for diversity: same person across different poses, times, and videos.
  - This validation set is for evaluation only, not for training.

Keyboard controls:
  1-9  assign to person_01 through person_09
  0    assign to person_10
  n    assign to the next available identity, e.g. person_11
  s    skip and record as skipped
  b    undo the last action from this session and show that image again
  h    print help
  q    quit safely
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import re
import shutil
import sqlite3
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import cv2


FILENAME_RE = re.compile(r"^(?P<case_no>\d+)_v(?P<video_idx>\d+)_(?P<frame_id>\d+)\.jpg$", re.IGNORECASE)
CSV_FIELDS = [
    "original_path",
    "copied_path",
    "assigned_identity",
    "original_filename",
    "case_no",
    "video_idx",
    "frame_id",
    "labeling_timestamp",
]
IMAGE_EXTENSIONS = {".jpg", ".jpeg"}
DEFAULT_OUTPUT_DIR = Path("CV/SimCLR_reid/validation_people")
REID_DISPLAY_SIZE = (128, 256)  # width, height; matches the 128x256 Re-ID crop aspect.


@dataclass(frozen=True)
class CropRecord:
    path: Path
    case_no: str
    video_idx: int
    frame_id: int


def parse_crop_filename(path: Path) -> CropRecord | None:
    match = FILENAME_RE.match(path.name)
    if match is None:
        return None
    return CropRecord(
        path=path,
        case_no=str(int(match.group("case_no"))),
        video_idx=int(match.group("video_idx")),
        frame_id=int(match.group("frame_id")),
    )


def parse_video_idx(value: str | None) -> int | None:
    if value is None:
        return None
    value = value.strip().lower()
    if value.startswith("v"):
        value = value[1:]
    try:
        return int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("--video_idx must look like 3, 03, v3, or v03") from exc


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def resolve_general3_video_path(row_path: str, mp4_root: str) -> Path:
    video_path = Path(row_path)
    if video_path.is_absolute():
        return video_path
    path_str = row_path
    if path_str.startswith("Recordings\\") or path_str.startswith("Recordings/"):
        path_str = path_str[len("Recordings") + 1 :]
    return Path(mp4_root) / path_str


def load_date_filter_pairs(recording_date: str) -> set[tuple[str, int]]:
    """
    Map recording_date to the crop filename identifiers produced by build_dataset.py.

    Crop filenames do not contain dates. build_dataset.py enumerates existing
    General_3 videos ordered by case_no and recording_date, then writes the
    global enumeration index as v{video_idx}. This recreates that mapping.
    """
    root = repo_root()
    sys.path.insert(0, str(root))
    try:
        from config import DB_PATH, MP4_ROOT
    except ImportError as exc:
        raise RuntimeError(
            "Cannot use --date because config.py could not be imported. "
            "Crop filenames contain only case/video/frame identifiers; use --case_no instead."
        ) from exc

    db_path = Path(DB_PATH)
    if not db_path.exists():
        raise RuntimeError(
            f"Cannot use --date because the SQLite database was not found: {db_path}. "
            "Crop filenames contain no date information; use --case_no instead."
        )

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT path, recording_date, case_no
            FROM mp4_status
            WHERE camera_name = 'General_3' OR camera_name = 'General 3'
            ORDER BY case_no, recording_date
            """
        ).fetchall()
    finally:
        conn.close()

    videos: list[sqlite3.Row] = []
    for row in rows:
        if row["path"] is None:
            continue
        video_path = resolve_general3_video_path(row["path"], MP4_ROOT)
        if video_path.exists():
            videos.append(row)

    pairs = {
        (str(int(row["case_no"])), idx)
        for idx, row in enumerate(videos)
        if str(row["recording_date"]) == recording_date
    }
    if not pairs:
        raise RuntimeError(
            f"No existing General_3 videos matched --date {recording_date!r}. "
            "Check the date format, usually YYYY-MM-DD, or use --case_no."
        )
    return pairs


def read_label_rows(labels_path: Path) -> list[dict[str, str]]:
    if not labels_path.exists():
        return []
    with labels_path.open("r", newline="", encoding="utf-8") as file:
        reader = csv.DictReader(file)
        missing = [field for field in CSV_FIELDS if field not in (reader.fieldnames or [])]
        if missing:
            raise RuntimeError(f"{labels_path} is missing required columns: {', '.join(missing)}")
        return [dict(row) for row in reader]


def write_label_rows(labels_path: Path, rows: list[dict[str, str]]) -> None:
    labels_path.parent.mkdir(parents=True, exist_ok=True)
    with labels_path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def label_counts(rows: list[dict[str, str]]) -> Counter:
    return Counter(row["assigned_identity"] for row in rows if row.get("assigned_identity") and row["assigned_identity"] != "skipped")


def reviewed_originals(rows: list[dict[str, str]]) -> set[str]:
    return {row["original_path"] for row in rows if row.get("original_path")}


def next_identity_name(rows: list[dict[str, str]]) -> str:
    max_idx = 0
    for identity in label_counts(rows):
        match = re.match(r"^person_(\d+)$", identity)
        if match:
            max_idx = max(max_idx, int(match.group(1)))
    return f"person_{max_idx + 1:02d}"


def candidate_records(args: argparse.Namespace, already_reviewed: set[str]) -> list[CropRecord]:
    dataset_dir = Path(args.dataset_dir)
    if not dataset_dir.exists() or not dataset_dir.is_dir():
        raise FileNotFoundError(f"--dataset_dir does not exist or is not a directory: {dataset_dir}")

    case_filter = str(int(args.case_no)) if args.case_no is not None else None
    video_filter = parse_video_idx(args.video_idx)
    date_pairs = load_date_filter_pairs(args.date) if args.date else None

    parsed: list[CropRecord] = []
    skipped_bad_names = 0
    for path in sorted(dataset_dir.iterdir()):
        if not path.is_file() or path.suffix.lower() not in IMAGE_EXTENSIONS:
            continue
        record = parse_crop_filename(path)
        if record is None:
            skipped_bad_names += 1
            continue
        if case_filter is not None and record.case_no != case_filter:
            continue
        if video_filter is not None and record.video_idx != video_filter:
            continue
        if date_pairs is not None and (record.case_no, record.video_idx) not in date_pairs:
            continue
        if not args.include_already_labeled and str(path.resolve()) in already_reviewed:
            continue
        parsed.append(record)

    if skipped_bad_names:
        print(f"[Info] Ignored {skipped_bad_names} JPG files that do not match {{case_no}}_v{{video_idx}}_{{frame_id}}.jpg")

    parsed = apply_sampling_filters(
        parsed,
        sample_every_n_frames=args.sample_every_n_frames,
        min_frame_gap=args.min_frame_gap,
    )
    if args.shuffle:
        random.shuffle(parsed)
    if args.max_images is not None:
        parsed = parsed[: args.max_images]
    return parsed


def apply_sampling_filters(
    records: list[CropRecord],
    sample_every_n_frames: int | None,
    min_frame_gap: int | None,
) -> list[CropRecord]:
    selected = sorted(records, key=lambda r: (int(r.case_no), r.video_idx, r.frame_id, r.path.name))

    if sample_every_n_frames and sample_every_n_frames > 0:
        seen_buckets: set[tuple[str, int, int]] = set()
        sampled: list[CropRecord] = []
        for record in selected:
            bucket = (record.case_no, record.video_idx, record.frame_id // sample_every_n_frames)
            if bucket in seen_buckets:
                continue
            seen_buckets.add(bucket)
            sampled.append(record)
        selected = sampled

    if min_frame_gap and min_frame_gap > 0:
        accepted_by_group: dict[tuple[str, int], list[int]] = {}
        spaced: list[CropRecord] = []
        for record in selected:
            key = (record.case_no, record.video_idx)
            accepted_frames = accepted_by_group.setdefault(key, [])
            if any(abs(record.frame_id - frame_id) < min_frame_gap for frame_id in accepted_frames):
                continue
            accepted_frames.append(record.frame_id)
            spaced.append(record)
        selected = spaced

    return selected


def unique_copy_path(output_dir: Path, identity: str, source: Path) -> Path:
    identity_dir = output_dir / identity
    identity_dir.mkdir(parents=True, exist_ok=True)
    candidate = identity_dir / source.name
    if not candidate.exists():
        return candidate

    stem = source.stem
    suffix = source.suffix
    for idx in range(1, 100000):
        candidate = identity_dir / f"{stem}_{idx:03d}{suffix}"
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"Could not find a non-colliding filename for {source.name} in {identity_dir}")


def build_summary(args: argparse.Namespace, rows: list[dict[str, str]]) -> dict:
    counts = label_counts(rows)
    timestamps = [row["labeling_timestamp"] for row in rows if row.get("labeling_timestamp")]
    return {
        "number_of_images_reviewed": len(rows),
        "number_of_skipped_images": sum(1 for row in rows if row.get("assigned_identity") == "skipped"),
        "number_of_images_per_identity": dict(sorted(counts.items())),
        "dataset_dir": str(Path(args.dataset_dir).resolve()),
        "filters": {
            "case_no": args.case_no,
            "video_idx": args.video_idx,
            "date": args.date,
            "max_images": args.max_images,
            "shuffle": bool(args.shuffle),
            "min_frame_gap": args.min_frame_gap,
            "sample_every_n_frames": args.sample_every_n_frames,
            "include_already_labeled": bool(args.include_already_labeled),
        },
        "created_at": min(timestamps) if timestamps else None,
        "updated_at": max(timestamps) if timestamps else datetime.now().isoformat(timespec="seconds"),
    }


def write_summary(summary_path: Path, args: argparse.Namespace, rows: list[dict[str, str]]) -> None:
    with summary_path.open("w", encoding="utf-8") as file:
        json.dump(build_summary(args, rows), file, indent=2)


def print_help() -> None:
    print(__doc__)


def print_progress(index: int, total: int, record: CropRecord, rows: list[dict[str, str]]) -> None:
    counts = label_counts(rows)
    count_text = ", ".join(f"{identity}:{count}" for identity, count in sorted(counts.items())) or "none"
    print(
        f"[{index + 1}/{total}] {record.path.name} "
        f"case={record.case_no} video=v{record.video_idx:02d} frame={record.frame_id} | counts: {count_text}"
    )


def render_display(image, lines: list[str], scale: float):
    """Render a larger Re-ID-rectangle crop with a separate text panel below it."""
    if scale <= 0:
        scale = 1.0
    display_width = max(1, int(REID_DISPLAY_SIZE[0] * scale))
    display_height = max(1, int(REID_DISPLAY_SIZE[1] * scale))
    resized = cv2.resize(image, (display_width, display_height), interpolation=cv2.INTER_LINEAR)

    line_height = 24
    panel_height = max(96, 16 + line_height * len(lines))
    panel = cv2.copyMakeBorder(
        resized[:1, :, :],
        0,
        panel_height - 1,
        0,
        0,
        cv2.BORDER_CONSTANT,
        value=(20, 20, 20),
    )

    y = 24
    for line in lines:
        cv2.putText(panel, line, (8, y), cv2.FONT_HERSHEY_SIMPLEX, 0.56, (255, 255, 255), 1, cv2.LINE_AA)
        y += line_height
    return cv2.vconcat([resized, panel])


def make_label_row(record: CropRecord, identity: str, copied_path: Path | None) -> dict[str, str]:
    return {
        "original_path": str(record.path.resolve()),
        "copied_path": str(copied_path.resolve()) if copied_path else "",
        "assigned_identity": identity,
        "original_filename": record.path.name,
        "case_no": record.case_no,
        "video_idx": f"{record.video_idx:02d}",
        "frame_id": f"{record.frame_id:06d}",
        "labeling_timestamp": datetime.now().isoformat(timespec="seconds"),
    }


def safe_unlink_copied_file(copied_path: str, output_dir: Path) -> None:
    if not copied_path:
        return
    path = Path(copied_path)
    try:
        resolved = path.resolve()
        output_resolved = output_dir.resolve()
        if output_resolved in resolved.parents and resolved.exists() and resolved.is_file():
            resolved.unlink()
            print(f"[Undo] Removed copied file: {resolved}")
    except OSError as exc:
        print(f"[Undo warning] Could not remove copied file {copied_path}: {exc}")


def run_labeling(args: argparse.Namespace) -> None:
    if args.copy_mode != "copy":
        raise ValueError("Only --copy_mode copy is supported. Source dataset images are never moved.")

    output_dir = Path(args.output_dir)
    labels_path = output_dir / "labels.csv"
    summary_path = output_dir / "summary.json"
    output_dir.mkdir(parents=True, exist_ok=True)

    rows = read_label_rows(labels_path)
    already_reviewed = reviewed_originals(rows)
    records = candidate_records(args, already_reviewed)

    total_dataset_matches = len(records)
    print("=" * 72)
    print("Manual Re-ID Validation Labeling")
    print("=" * 72)
    print(f"Dataset:             {Path(args.dataset_dir).resolve()}")
    print(f"Output:              {output_dir.resolve()}")
    print(f"Existing labels:     {len(rows)}")
    print(f"Already reviewed:    {len(already_reviewed)}")
    print(f"Remaining to review: {total_dataset_matches}")
    print("Tip: label diverse, clear crops; skip uncertain or near-duplicate images.")
    print("Press h in the image window for help.")

    if not records:
        write_summary(summary_path, args, rows)
        print("[Done] No candidate images to review.")
        return

    session_rows: list[dict[str, str]] = []
    session_records: list[CropRecord] = []
    window_name = "SimCLR Re-ID validation labeling"
    index = 0

    while index < len(records):
        record = records[index]
        image = cv2.imread(str(record.path))
        if image is None:
            print(f"[Warning] Could not read image, recording skipped: {record.path}")
            row = make_label_row(record, "skipped", None)
            rows.append(row)
            session_rows.append(row)
            session_records.append(record)
            write_label_rows(labels_path, rows)
            write_summary(summary_path, args, rows)
            index += 1
            continue

        print_progress(index, len(records), record, rows)
        counts = label_counts(rows)
        count_text = " ".join(f"{k}:{v}" for k, v in sorted(counts.items())) or "none"
        display = render_display(
            image,
            [
                f"{index + 1}/{len(records)}  {record.path.name}",
                f"case={record.case_no}  video=v{record.video_idx:02d}  frame={record.frame_id}",
                "1-9/0=person  s=skip  n=next identity  b=undo/back  h=help  q=quit",
                f"counts: {count_text}",
            ],
            args.display_scale,
        )
        cv2.imshow(window_name, display)
        key = cv2.waitKey(0) & 0xFF

        if key in (ord("q"), 27):
            print("[Quit] Labels saved safely.")
            break
        if key == ord("h"):
            print_help()
            continue
        if key == ord("b"):
            if not session_rows:
                print("[Back] No current-session action to undo.")
                continue
            last_row = session_rows.pop()
            last_record = session_records.pop()
            if rows and rows[-1] == last_row:
                rows.pop()
            else:
                print("[Back warning] Last row was not at the end of labels.csv; leaving CSV history unchanged.")
            safe_unlink_copied_file(last_row.get("copied_path", ""), output_dir)
            write_label_rows(labels_path, rows)
            write_summary(summary_path, args, rows)
            try:
                index = records.index(last_record)
            except ValueError:
                index = max(0, index - 1)
            continue
        if key == ord("s"):
            copied_path = None
            if args.copy_skipped:
                copied_path = unique_copy_path(output_dir, "skipped", record.path)
                shutil.copy2(record.path, copied_path)
            row = make_label_row(record, "skipped", copied_path)
        elif key == ord("n"):
            identity = next_identity_name(rows)
            copied_path = unique_copy_path(output_dir, identity, record.path)
            shutil.copy2(record.path, copied_path)
            row = make_label_row(record, identity, copied_path)
        elif ord("1") <= key <= ord("9") or key == ord("0"):
            identity_idx = 10 if key == ord("0") else int(chr(key))
            identity = f"person_{identity_idx:02d}"
            copied_path = unique_copy_path(output_dir, identity, record.path)
            shutil.copy2(record.path, copied_path)
            row = make_label_row(record, identity, copied_path)
        else:
            print("[Input] Unknown key. Press h for help.")
            continue

        rows.append(row)
        session_rows.append(row)
        session_records.append(record)
        write_label_rows(labels_path, rows)
        write_summary(summary_path, args, rows)
        index += 1

    cv2.destroyAllWindows()
    write_label_rows(labels_path, rows)
    write_summary(summary_path, args, rows)
    print(f"[Done] labels:  {labels_path.resolve()}")
    print(f"[Done] summary: {summary_path.resolve()}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Manually copy SimCLR crop images into identity folders for Re-ID validation.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--dataset_dir", required=True, help="Flat SimCLR crop dataset directory.")
    parser.add_argument("--output_dir", default=str(DEFAULT_OUTPUT_DIR), help="Validation output directory.")
    parser.add_argument("--case_no", default=None, help="Optional case number filter, e.g. 12.")
    parser.add_argument("--video_idx", default=None, help="Optional video index filter, e.g. 3, 03, v3, or v03.")
    parser.add_argument(
        "--date",
        default=None,
        help="Optional recording_date filter. Requires ScalpelDatabase.sqlite because crop filenames contain no dates.",
    )
    parser.add_argument("--max_images", type=int, default=None, help="Maximum images to review this session.")
    parser.add_argument("--shuffle", action="store_true", help="Shuffle candidates before display.")
    parser.add_argument("--copy_mode", default="copy", choices=["copy"], help="Only copy mode is supported.")
    parser.add_argument(
        "--include_already_labeled",
        action="store_true",
        help="Show images already present in labels.csv.",
    )
    parser.add_argument(
        "--min_frame_gap",
        type=int,
        default=None,
        help="Avoid selecting frames from the same case/video closer than this gap.",
    )
    parser.add_argument(
        "--sample_every_n_frames",
        type=int,
        default=None,
        help="Keep at most one candidate per N-frame bucket within each case/video.",
    )
    parser.add_argument(
        "--copy_skipped",
        action="store_true",
        help="Also copy skipped images into output_dir/skipped. By default skipped images are logged only.",
    )
    parser.add_argument(
        "--display_scale",
        type=float,
        default=3.0,
        help="Scale factor for the displayed crop image. Text is shown in a separate panel below the crop.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    run_labeling(args)


if __name__ == "__main__":
    main()
