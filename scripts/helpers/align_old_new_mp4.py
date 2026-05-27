"""Align old-exporter MP4s with new-exporter MP4s and emit a frame-mapping report.

Background
----------
Two different SEQ-to-MP4 exporters produced two parallel sets of recordings:

  * Old exporter (``old.py``): ran ``ffmpeg -r 30 -i <seq>`` and treated the
    SEQ as if it were already 30 fps CFR. No pre-roll padding. The first
    SEQ frame becomes output frame 0.
  * New exporter (``new.py`` / ``scripts/3_seq_to_mp4_convert.py``): builds
    a VFR MKV from IDX timecodes, resamples to 30 fps with
    nearest-neighbor, and uses an FFmpeg ``tpad`` filter to add
    ``round((cam.t_start - group.t_global_start) * 30)`` black pre-roll
    frames so every camera in the same date+case+group shares ``t = 0``.
    Output duration is also hard-cut to a shared global duration.

Annotations were created on the **old** outputs (using the monitor video as
the time reference). We now want to consume the **new** outputs without
re-tagging. This script computes, per (date, case, camera), where the
content of the old MP4 lives inside the new MP4.

Method (two stages, read-only)
------------------------------
1. **Analytical prediction** from ``seq_enriched.first_frame_time``:

       pre_roll_sec   = cam.first_frame_time - group.t_global_start
       pre_roll_frames = round(pre_roll_sec * 30)
       new_frame ~= old_frame + pre_roll_frames

   ``t_global_start`` is the minimum ``first_frame_time`` over all cameras
   in the same date+case+group (groups taken from new.py:75-78).

2. **Spot-check verification** with dHash on a handful of sample frames:
   for each old sample, extract a small window of frames from the new MP4
   around the predicted index and pick the closest hash match. Aggregate
   the per-sample matches into a measured offset plus a drift slope.

The CSV/JSON report names a single ``mapping_formula`` per video:
``new_frame = old_frame + N`` when constant, otherwise the regressed
``new_frame = a * old_frame + b``.

The script never writes to MP4s or the database.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import logging
import re
import shutil
import sqlite3
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Iterable

import numpy as np

# Reconfigure stdout to UTF-8 on Windows console (CLAUDE.md §4).
if hasattr(sys.stdout, "buffer") and (sys.stdout.encoding or "").lower() not in ("utf-8", "utf8"):
    sys.stdout = io.TextIOWrapper(
        sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True
    )

# Project imports — parent-dir shim (same pattern as other helpers).
_SCRIPT_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _SCRIPT_DIR.parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

try:
    from config import get_db_path, get_mp4_root  # type: ignore
except ImportError:
    get_db_path = lambda: str(_PROJECT_ROOT / "ScalpelDatabase.sqlite")  # type: ignore
    get_mp4_root = lambda: r"F:\Room_8_Data\Recordings"  # type: ignore


# =========================================================================
# Constants
# =========================================================================
# Duplicated from new.py:69-78 — stable values; intentionally not imported
# from the Downloads-folder script.
TARGET_FPS = 30
GROUP_A = ("Cart_Center_2", "Cart_LT_4", "Cart_RT_1", "General_3")
GROUP_B = ("Monitor", "Patient_Monitor", "Ventilator_Monitor")
SOLO_CAMERAS = ("Injection_Port",)

DEFAULT_OLD_ROOT = r"D:\Rec_Backup"
DEFAULT_NEW_ROOT = get_mp4_root()

# Path regexes (same convention as scripts/helpers/analyze_seq_fields.py).
_RE_DATA = re.compile(r"^DATA_(\d{2})-(\d{2})-(\d{2})$")
_RE_CASE = re.compile(r"^Case(\d+)$")
_RE_JUNK = re.compile(r"_JUNK$", re.IGNORECASE)

# dHash: 17 cols x 16 rows of grayscale -> 16 rows * 16 bit-diffs = 256 bits.
# 64-bit dHash collides far too often on near-static surgical scenes
# (hamming=0 matches at many different frames in the same window). 256 bits
# gives roughly 2**192 fewer accidental collisions for the same scene noise.
_DHASH_W, _DHASH_H = 17, 16
_DHASH_BITS = (_DHASH_W - 1) * _DHASH_H        # 256
_DHASH_BYTES = _DHASH_W * _DHASH_H             # 272 (raw frame bytes per sample)

# Drift heuristics for the verification stage.
# Threshold scales with the hash size: ~5% of bits is a generous "near match".
_HAMMING_GOOD = max(8, _DHASH_BITS // 20)   # <= 12 of 256 bits considered confident
_DRIFT_FRAME_THRESHOLD = 5  # measured drift across the sample span (frames)
_OFFSET_MISMATCH_FRAMES = 5 # analytical-vs-measured disagreement worth flagging
# Fraction of anchors landing on a window edge that triggers WINDOW_PINNED.
_WINDOW_PINNED_FRACTION = 0.30

# IDX timestamp sanity window (CLAUDE.md §5: corrupt ts values exist).
_EPOCH_MIN = 1_420_070_400   # 2015-01-01 UTC
_EPOCH_MAX = 1_924_905_600   # 2030-12-31 UTC


# =========================================================================
# Logging
# =========================================================================
LOG = logging.getLogger("align_old_new_mp4")


def _setup_logging(debug: bool, log_path: Path | None) -> None:
    """Console + optional file logging."""
    LOG.setLevel(logging.DEBUG if debug else logging.INFO)
    LOG.handlers.clear()
    fmt = logging.Formatter("[%(asctime)s] %(levelname)s %(message)s", "%H:%M:%S")
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    sh.setLevel(LOG.level)
    LOG.addHandler(sh)
    if log_path is not None:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(log_path, encoding="utf-8")
        fh.setFormatter(fmt)
        fh.setLevel(logging.DEBUG)
        LOG.addHandler(fh)


# =========================================================================
# Data structures
# =========================================================================
@dataclass
class Pair:
    """An (old, new) MP4 pair plus everything we learn about it."""
    recording_date: str
    case_no: int
    camera_name: str
    group_name: str
    old_path: Path
    new_path: Path | None

    # Analytical prediction
    predicted_offset_frames: int | None = None
    predicted_offset_seconds: float | None = None

    # ffprobe metadata
    old_fps: float = 0.0
    new_fps: float = 0.0
    old_nb_frames: int = 0
    new_nb_frames: int = 0
    old_duration_s: float = 0.0
    new_duration_s: float = 0.0
    old_codec: str = ""
    new_codec: str = ""

    # Verification results
    measured_offset_frames: int | None = None
    measured_offset_seconds: float | None = None
    drift_slope: float | None = None
    drift_intercept: float | None = None
    is_drifting: bool = False
    confidence: float = 0.0
    n_samples: int = 0
    n_samples_matched: int = 0
    per_sample: list[dict] = field(default_factory=list)

    warnings: list[str] = field(default_factory=list)

    @property
    def mapping_formula(self) -> str:
        """Human-readable mapping expression for the CSV report."""
        if self.is_drifting and self.drift_slope is not None and self.drift_intercept is not None:
            return f"new_frame = {self.drift_slope:.6f} * old_frame + {self.drift_intercept:.3f}"
        # Prefer measured; fall back to analytical.
        n = self.measured_offset_frames
        if n is None:
            n = self.predicted_offset_frames
        if n is None:
            return ""
        return f"new_frame = old_frame + {n}"


# =========================================================================
# Path parsing / pairing
# =========================================================================
def _split_data_path(path: Path, root: Path) -> tuple[str, int, str] | None:
    """Pull (YYYY-MM-DD, case_no, camera) out of ``<root>/DATA_../CaseN/Camera/..``.

    Returns ``None`` if the path does not match the expected layout.
    """
    try:
        rel_parts = path.relative_to(root).parts
    except ValueError:
        return None
    if len(rel_parts) < 4:
        return None
    m_date = _RE_DATA.match(rel_parts[0])
    m_case = _RE_CASE.match(rel_parts[1])
    if not m_date or not m_case:
        return None
    yy, mm, dd = m_date.groups()
    yyyy = f"20{yy}" if int(yy) <= 69 else f"19{yy}"
    camera = _RE_JUNK.sub("", rel_parts[2])
    return f"{yyyy}-{mm}-{dd}", int(m_case.group(1)), camera


def _camera_group(camera: str) -> str:
    """Return ``A``, ``B``, ``SOLO``, or ``UNKNOWN`` for a camera name."""
    if camera in GROUP_A:
        return "A"
    if camera in GROUP_B:
        return "B"
    if camera in SOLO_CAMERAS:
        return "SOLO"
    return "UNKNOWN"


def _walk_mp4s(root: Path) -> list[Path]:
    """List every ``.mp4`` under ``root`` (case-insensitive)."""
    if not root.exists():
        LOG.error("Root does not exist: %s", root)
        return []
    return [p for p in root.rglob("*") if p.is_file() and p.suffix.lower() == ".mp4"]


def _find_new_match(
    new_root: Path, recording_date: str, case_no: int, camera: str,
) -> Path | None:
    """Locate the new-side MP4 for a given (date, case, camera) folder.

    Old and new filenames differ (old uses sequential counters, new uses
    the camera name) so we match the *directory* rather than the basename.
    """
    yy = recording_date[2:4]
    mm = recording_date[5:7]
    dd = recording_date[8:10]
    cam_dir = new_root / f"DATA_{yy}-{mm}-{dd}" / f"Case{case_no}" / camera
    if not cam_dir.is_dir():
        # Tolerate _JUNK suffix mismatch by checking sibling folders.
        parent = new_root / f"DATA_{yy}-{mm}-{dd}" / f"Case{case_no}"
        if not parent.is_dir():
            return None
        for child in parent.iterdir():
            if child.is_dir() and _RE_JUNK.sub("", child.name) == camera:
                cam_dir = child
                break
        else:
            return None

    candidates = [p for p in cam_dir.glob("*.mp4")]
    if not candidates:
        return None
    # Skip the fallback NOT_SYNCABLE outputs (no pre-roll math applies; see
    # scripts.md §3 SEQ-to-MP4 fallback path).
    syncable = [p for p in candidates if "_NOT_SYNCABLE" not in p.stem.upper()]
    if syncable:
        return sorted(syncable)[0]
    # All candidates are NOT_SYNCABLE — return the first so the caller can
    # tag a warning rather than silently dropping the pair.
    return sorted(candidates)[0]


def build_pairs(
    old_root: Path,
    new_root: Path,
    cameras: set[str] | None,
    date_filter: str | None,
    case_filter: int | None,
) -> list[Pair]:
    """Walk the old root and produce a :class:`Pair` for every old MP4 found."""
    pairs: list[Pair] = []
    for old_mp4 in _walk_mp4s(old_root):
        parsed = _split_data_path(old_mp4, old_root)
        if parsed is None:
            LOG.debug("Skip (unparseable path): %s", old_mp4)
            continue
        rec_date, case_no, camera = parsed
        if cameras is not None and camera not in cameras:
            continue
        if date_filter and rec_date != date_filter:
            continue
        if case_filter is not None and case_no != case_filter:
            continue

        new_path = _find_new_match(new_root, rec_date, case_no, camera)
        group = _camera_group(camera)

        p = Pair(
            recording_date=rec_date,
            case_no=case_no,
            camera_name=camera,
            group_name=group,
            old_path=old_mp4,
            new_path=new_path,
        )
        if new_path is None:
            p.warnings.append("NEW_MISSING")
        elif "_NOT_SYNCABLE" in new_path.stem.upper():
            p.warnings.append("NOT_SYNCABLE")
        if group == "UNKNOWN":
            p.warnings.append("UNKNOWN_GROUP")
        pairs.append(p)
    return pairs


# =========================================================================
# Analytical offset (from seq_enriched)
# =========================================================================
def load_enriched_timings(db_path: str) -> dict[tuple[str, int, str], tuple[float, float]]:
    """Read ``seq_enriched.first_frame_time`` / ``last_frame_time`` for every camera.

    Returns a dict ``{(recording_date, case_no, camera_name): (first, last)}``
    Skips rows whose timestamps are obviously corrupt (CLAUDE.md §5).
    """
    out: dict[tuple[str, int, str], tuple[float, float]] = {}
    if not Path(db_path).is_file():
        LOG.warning("DB not found at %s — analytical step will be skipped", db_path)
        return out
    with sqlite3.connect(db_path) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        cur = conn.execute(
            "SELECT recording_date, case_no, camera_name, "
            "       first_frame_time, last_frame_time "
            "FROM seq_enriched "
            "WHERE first_frame_time IS NOT NULL AND last_frame_time IS NOT NULL"
        )
        for rec_date, case_no, cam, t0, t1 in cur.fetchall():
            try:
                t0f, t1f = float(t0), float(t1)
            except (TypeError, ValueError):
                continue
            # Cheap sanity gate: Unix timestamp in a plausible range.
            if not (_EPOCH_MIN <= t0f <= _EPOCH_MAX and _EPOCH_MIN <= t1f <= _EPOCH_MAX):
                continue
            out[(rec_date, int(case_no), cam)] = (t0f, t1f)
    LOG.info("Loaded %d seq_enriched timing rows", len(out))
    return out


def annotate_predicted_offsets(
    pairs: list[Pair],
    enriched: dict[tuple[str, int, str], tuple[float, float]],
) -> None:
    """Set ``predicted_offset_*`` on every pair using IDX timestamps.

    Replicates new.py's pre-roll formula at the per-camera level.
    """
    # Pre-compute each (date, case, group)'s global t_start across whatever
    # cameras the DB has timings for.
    group_t_start: dict[tuple[str, int, str], float] = {}
    for (rec_date, case_no, cam), (t0, _t1) in enriched.items():
        grp = _camera_group(cam)
        if grp == "UNKNOWN":
            continue
        key = (rec_date, case_no, grp)
        prev = group_t_start.get(key, float("inf"))
        if t0 < prev:
            group_t_start[key] = t0

    for p in pairs:
        key_cam = (p.recording_date, p.case_no, p.camera_name)
        t = enriched.get(key_cam)
        if t is None:
            p.warnings.append("MISSING_IDX")
            continue
        t0_cam, _t1_cam = t
        t_global = group_t_start.get((p.recording_date, p.case_no, p.group_name))
        if t_global is None:
            # Solo camera (or only this camera has timing in the group).
            t_global = t0_cam
        pre_roll_sec = max(0.0, t0_cam - t_global)
        p.predicted_offset_seconds = pre_roll_sec
        p.predicted_offset_frames = int(round(pre_roll_sec * TARGET_FPS))


# =========================================================================
# ffprobe / ffmpeg metadata
# =========================================================================
_FFPROBE_CACHE: dict[str, dict] = {}


def _resolve_tool(name: str) -> str | None:
    p = shutil.which(name)
    if p:
        return p
    # Common Windows install locations
    candidates = [
        rf"C:\Program Files\ffmpeg\bin\{name}.exe",
        rf"C:\ffmpeg\bin\{name}.exe",
        rf"C:\Program Files (x86)\ffmpeg\bin\{name}.exe",
    ]
    for c in candidates:
        if Path(c).exists():
            return c
    return None


def _parse_fps_field(value: str) -> float:
    """Parse ``avg_frame_rate``-style strings like ``30/1`` or ``29.97``."""
    if not value:
        return 0.0
    if "/" in value:
        num, den = value.split("/", 1)
        try:
            n, d = float(num), float(den)
        except ValueError:
            return 0.0
        return n / d if d else 0.0
    try:
        return float(value)
    except ValueError:
        return 0.0


def ffprobe_metadata(mp4: Path, ffprobe: str) -> dict:
    """Probe video stream + container for the basic fields we need."""
    key = str(mp4)
    if key in _FFPROBE_CACHE:
        return _FFPROBE_CACHE[key]

    cmd = [
        ffprobe, "-v", "error",
        "-select_streams", "v:0",
        "-show_entries",
        "stream=codec_name,avg_frame_rate,r_frame_rate,nb_frames,duration,time_base,width,height:"
        "format=duration",
        "-of", "json",
        str(mp4),
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    except (subprocess.TimeoutExpired, OSError) as exc:
        LOG.warning("ffprobe failed for %s: %s", mp4, exc)
        _FFPROBE_CACHE[key] = {}
        return {}

    if proc.returncode != 0:
        LOG.warning("ffprobe rc=%d for %s: %s", proc.returncode, mp4, proc.stderr.strip())
        _FFPROBE_CACHE[key] = {}
        return {}

    try:
        data = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError:
        _FFPROBE_CACHE[key] = {}
        return {}

    streams = data.get("streams") or [{}]
    s = streams[0]
    fmt = data.get("format", {}) or {}

    fps = _parse_fps_field(s.get("avg_frame_rate", "")) or _parse_fps_field(s.get("r_frame_rate", ""))
    duration = 0.0
    for src in (s.get("duration"), fmt.get("duration")):
        try:
            if src is not None:
                duration = float(src)
                break
        except ValueError:
            pass

    try:
        nb_frames = int(s.get("nb_frames") or 0)
    except (TypeError, ValueError):
        nb_frames = 0
    if nb_frames == 0 and fps > 0 and duration > 0:
        nb_frames = int(round(duration * fps))

    info = {
        "fps": fps,
        "nb_frames": nb_frames,
        "duration": duration,
        "codec": s.get("codec_name", "") or "",
        "time_base": s.get("time_base", "") or "",
        "width": int(s.get("width") or 0),
        "height": int(s.get("height") or 0),
    }
    _FFPROBE_CACHE[key] = info
    return info


def populate_ffprobe_fields(pair: Pair, ffprobe: str) -> None:
    """Fill the ``old_*`` and ``new_*`` ffprobe fields on a pair."""
    old = ffprobe_metadata(pair.old_path, ffprobe)
    pair.old_fps = old.get("fps", 0.0)
    pair.old_nb_frames = old.get("nb_frames", 0)
    pair.old_duration_s = old.get("duration", 0.0)
    pair.old_codec = old.get("codec", "")
    if pair.new_path is not None:
        new = ffprobe_metadata(pair.new_path, ffprobe)
        pair.new_fps = new.get("fps", 0.0)
        pair.new_nb_frames = new.get("nb_frames", 0)
        pair.new_duration_s = new.get("duration", 0.0)
        pair.new_codec = new.get("codec", "")


# =========================================================================
# Frame extraction + dHash
# =========================================================================
def _extract_grayscale_frames(
    mp4: Path, t_seek: float, n_frames: int, ffmpeg: str,
) -> np.ndarray | None:
    """Extract ``n_frames`` consecutive 9x8 grayscale frames from ``mp4``.

    Uses a two-pass seek: an input-side ``-ss <t_seek - 2>`` (fast, keyframe)
    plus an output-side ``-ss 2`` (precise) so the first decoded output frame
    corresponds to wall-clock time ``t_seek`` within ~1 frame.

    Returns a uint8 array of shape ``(n_frames, 8, 9)`` or ``None`` on
    failure. May return fewer rows if the stream ends mid-window.
    """
    if n_frames <= 0:
        return None
    lead = 2.0
    pre = max(0.0, t_seek - lead)
    cmd = [
        ffmpeg, "-hide_banner", "-loglevel", "error",
        "-ss", f"{pre:.3f}",
        "-i", str(mp4),
        "-ss", f"{t_seek - pre:.3f}",
        "-an",
        "-frames:v", str(n_frames),
        "-vf", f"scale={_DHASH_W}:{_DHASH_H},format=gray",
        "-f", "rawvideo",
        "-pix_fmt", "gray",
        "pipe:1",
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=120)
    except (subprocess.TimeoutExpired, OSError) as exc:
        LOG.warning("ffmpeg extract failed for %s @ %.2fs: %s", mp4, t_seek, exc)
        return None
    if proc.returncode != 0:
        LOG.debug("ffmpeg rc=%d for %s @ %.2fs: %s",
                  proc.returncode, mp4, t_seek, proc.stderr.decode("utf-8", errors="replace").strip())
        return None
    buf = proc.stdout
    n_got = len(buf) // _DHASH_BYTES
    if n_got == 0:
        return None
    arr = np.frombuffer(buf[: n_got * _DHASH_BYTES], dtype=np.uint8)
    return arr.reshape(n_got, _DHASH_H, _DHASH_W)


def _dhash_batch(frames: np.ndarray) -> list[int]:
    """256-bit dHash for each ``(H, W)`` grayscale frame in ``frames``.

    Returns a list of Python ints (arbitrary precision; avoids both the
    int64 top-bit overflow under NumPy 2.x and the per-bit Python loop —
    we pack to bytes inside numpy and bulk-convert with int.from_bytes.
    """
    # diff: shape (n, H, W-1) — True where right pixel > left pixel.
    diff = (frames[:, :, 1:] > frames[:, :, :-1]).reshape(frames.shape[0], -1)
    # _DHASH_BITS must be a multiple of 8 (it is: 256 = 32 bytes).
    packed = np.packbits(diff.astype(np.uint8), axis=1)
    return [int.from_bytes(row.tobytes(), "big") for row in packed]


def _hamming(a: int, b: int) -> int:
    """Hamming distance between two 64-bit ints."""
    return (int(a) ^ int(b)).bit_count()


# =========================================================================
# Verification (per pair)
# =========================================================================
def _sample_old_frame_indices(pair: Pair, n_samples: int, window: int) -> list[int]:
    """Pick frame indices in the old video to use as anchors.

    We avoid the first/last 5% (to dodge encoding-boundary artifacts) and
    ensure each anchor's predicted new index has room for the window.
    """
    if pair.old_nb_frames <= 0 or pair.new_nb_frames <= 0:
        return []
    off = pair.predicted_offset_frames or 0
    # Sample range in OLD frame space:
    #   * not before frame 0
    #   * the corresponding new index must be inside [window, new_nb_frames - 1 - window]
    new_min = window
    new_max = pair.new_nb_frames - 1 - window
    if new_max <= new_min:
        return []
    old_lo = max(0, new_min - off)
    old_hi = min(pair.old_nb_frames - 1, new_max - off)
    # Trim the first/last 5% of the old video.
    old_lo = max(old_lo, int(pair.old_nb_frames * 0.05))
    old_hi = min(old_hi, int(pair.old_nb_frames * 0.95))
    if old_hi <= old_lo:
        return []
    if n_samples <= 1:
        return [(old_lo + old_hi) // 2]
    step = (old_hi - old_lo) / (n_samples - 1)
    return [int(round(old_lo + i * step)) for i in range(n_samples)]


def verify_pair(pair: Pair, ffmpeg: str, n_samples: int, window: int) -> None:
    """Run the dHash spot-check and fill in measured offset / drift on ``pair``."""
    if pair.new_path is None:
        return
    if pair.old_fps <= 0 or pair.new_fps <= 0:
        pair.warnings.append("NO_FFPROBE")
        return
    anchors = _sample_old_frame_indices(pair, n_samples, window)
    if not anchors:
        pair.warnings.append("NO_SAMPLES")
        return
    pair.n_samples = len(anchors)

    predicted_off = pair.predicted_offset_frames or 0
    # Pre-extract anchor frames one at a time (one frame each, frame-accurate).
    old_hashes: dict[int, int] = {}
    for idx in anchors:
        t = idx / pair.old_fps
        frames = _extract_grayscale_frames(pair.old_path, t, 1, ffmpeg)
        if frames is None or frames.shape[0] == 0:
            LOG.debug("Old frame %d (@%.2fs) extract failed", idx, t)
            continue
        old_hashes[idx] = _dhash_batch(frames)[0]

    # For each anchor we have, extract a window from the new video centred
    # on the predicted index, hash the burst, and pick the closest match.
    # Sign convention (matters for OLD -> NEW annotation remapping):
    #
    #   new_frame = old_frame + offset
    #
    # A POSITIVE offset means the new MP4 holds the same content LATER
    # (the new file prepended black pre-roll, so old frame N's content
    # lives at new frame N + pre_roll). A NEGATIVE offset means the
    # opposite — the old file has leading content the new file doesn't.
    # We carry the same sign through analytical (predicted) AND measured
    # values so the CSV's mapping_formula is directly usable: take the
    # tagged old frame, add the offset, get the new frame.
    matches: list[tuple[int, int, int]] = []  # (old_idx, new_idx_best, hamming_dist)
    pinned_count = 0
    for old_idx in anchors:
        h_old = old_hashes.get(old_idx)
        if h_old is None:
            continue
        new_center = old_idx + predicted_off
        new_start = max(0, new_center - window)
        n_window = 2 * window + 1
        t_seek = new_start / TARGET_FPS  # new video is CFR at TARGET_FPS
        frames = _extract_grayscale_frames(pair.new_path, t_seek, n_window, ffmpeg)
        if frames is None or frames.shape[0] == 0:
            LOG.debug("New window @ frame %d extract failed", new_center)
            continue
        hashes = _dhash_batch(frames)
        dists = [_hamming(h_old, h) for h in hashes]
        best = int(np.argmin(dists))
        best_dist = int(dists[best])
        new_idx_best = new_start + best
        window_n = int(frames.shape[0])
        is_pinned = (best == 0) or (best == window_n - 1)
        if is_pinned:
            pinned_count += 1
        matches.append((old_idx, new_idx_best, best_dist))
        pair.per_sample.append({
            "old_frame": old_idx,
            "new_frame": new_idx_best,
            "hamming": best_dist,
            "window_min_dist": int(min(dists)),
            "window_size": window_n,
            "pinned": is_pinned,
        })
        LOG.debug(
            "[%s/%s/%s] anchor old=%d -> new=%d (hamming=%d/%d, predicted=%d, delta=%+d%s)",
            pair.recording_date, pair.case_no, pair.camera_name,
            old_idx, new_idx_best, best_dist, _DHASH_BITS,
            new_center, new_idx_best - new_center,
            " PINNED" if is_pinned else "",
        )

    if not matches:
        pair.warnings.append("VERIFY_FAILED")
        return

    # Window-edge detection: if a large fraction of anchors land on the
    # first or last index of their search window, the true offset is
    # outside the window and the measured numbers are not trustworthy.
    if pinned_count / max(1, len(matches)) >= _WINDOW_PINNED_FRACTION:
        pair.warnings.append("WINDOW_PINNED")

    good = [m for m in matches if m[2] <= _HAMMING_GOOD]
    pair.n_samples_matched = len(good)
    pair.confidence = pair.n_samples_matched / max(1, pair.n_samples)
    if not good:
        pair.warnings.append("LOW_CONFIDENCE")
        return

    olds = np.array([m[0] for m in good], dtype=np.float64)
    news = np.array([m[1] for m in good], dtype=np.float64)
    offsets = news - olds
    measured_off = int(round(float(np.median(offsets))))
    pair.measured_offset_frames = measured_off
    pair.measured_offset_seconds = measured_off / TARGET_FPS

    if len(good) >= 3:
        # Linear regression new = a*old + b
        a, b = np.polyfit(olds, news, 1)
        pair.drift_slope = float(a)
        pair.drift_intercept = float(b)
        # Drift across the sample span, in frames:
        drift_at_lo = a * olds.min() + b - (olds.min() + measured_off)
        drift_at_hi = a * olds.max() + b - (olds.max() + measured_off)
        span_drift = abs(drift_at_hi - drift_at_lo)
        if span_drift > _DRIFT_FRAME_THRESHOLD:
            pair.is_drifting = True
            pair.warnings.append("DRIFT")
    else:
        # Treat as constant offset.
        pair.drift_slope = 1.0
        pair.drift_intercept = float(measured_off)

    if pair.confidence < 0.5:
        pair.warnings.append("LOW_CONFIDENCE")
    if (pair.predicted_offset_frames is not None
            and abs(measured_off - pair.predicted_offset_frames) >= _OFFSET_MISMATCH_FRAMES):
        pair.warnings.append("OFFSET_MISMATCH")


# =========================================================================
# Report I/O
# =========================================================================
_CSV_COLUMNS = [
    "recording_date", "case_no", "camera_name", "group_name",
    "old_path", "new_path",
    "old_fps", "new_fps",
    "old_nb_frames", "new_nb_frames",
    "old_duration_s", "new_duration_s",
    "old_codec", "new_codec",
    "predicted_offset_frames", "predicted_offset_seconds",
    "measured_offset_frames", "measured_offset_seconds",
    "drift_slope", "drift_intercept", "is_drifting",
    "confidence", "n_samples", "n_samples_matched",
    "warnings", "mapping_formula",
]


def _pair_to_row(p: Pair) -> dict:
    return {
        "recording_date": p.recording_date,
        "case_no": p.case_no,
        "camera_name": p.camera_name,
        "group_name": p.group_name,
        "old_path": str(p.old_path),
        "new_path": str(p.new_path) if p.new_path else "",
        "old_fps": f"{p.old_fps:.4f}" if p.old_fps else "",
        "new_fps": f"{p.new_fps:.4f}" if p.new_fps else "",
        "old_nb_frames": p.old_nb_frames or "",
        "new_nb_frames": p.new_nb_frames or "",
        "old_duration_s": f"{p.old_duration_s:.3f}" if p.old_duration_s else "",
        "new_duration_s": f"{p.new_duration_s:.3f}" if p.new_duration_s else "",
        "old_codec": p.old_codec,
        "new_codec": p.new_codec,
        "predicted_offset_frames": p.predicted_offset_frames if p.predicted_offset_frames is not None else "",
        "predicted_offset_seconds": f"{p.predicted_offset_seconds:.4f}" if p.predicted_offset_seconds is not None else "",
        "measured_offset_frames": p.measured_offset_frames if p.measured_offset_frames is not None else "",
        "measured_offset_seconds": f"{p.measured_offset_seconds:.4f}" if p.measured_offset_seconds is not None else "",
        "drift_slope": f"{p.drift_slope:.6f}" if p.drift_slope is not None else "",
        "drift_intercept": f"{p.drift_intercept:.3f}" if p.drift_intercept is not None else "",
        "is_drifting": "1" if p.is_drifting else "0",
        "confidence": f"{p.confidence:.3f}",
        "n_samples": p.n_samples,
        "n_samples_matched": p.n_samples_matched,
        "warnings": ";".join(p.warnings),
        "mapping_formula": p.mapping_formula,
    }


def write_csv(pairs: Iterable[Pair], out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=_CSV_COLUMNS)
        writer.writeheader()
        for p in pairs:
            writer.writerow(_pair_to_row(p))


def write_json(pairs: Iterable[Pair], out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    for p in pairs:
        row = _pair_to_row(p)
        row["per_sample"] = p.per_sample
        rows.append(row)
    with out_path.open("w", encoding="utf-8") as fh:
        json.dump(rows, fh, indent=2)


# =========================================================================
# Main
# =========================================================================
def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        description="Align old-exporter MP4s with new-exporter MP4s.",
    )
    ap.add_argument("--old-root", default=DEFAULT_OLD_ROOT,
                    help=f"Root of the OLD recordings (default: {DEFAULT_OLD_ROOT}).")
    ap.add_argument("--new-root", default=DEFAULT_NEW_ROOT,
                    help=f"Root of the NEW recordings (default: {DEFAULT_NEW_ROOT}).")
    ap.add_argument("--out-csv", default="logs/align/old_new_mapping.csv",
                    help="CSV report path.")
    ap.add_argument("--out-json", default=None,
                    help="Optional JSON report path (includes per-sample matches).")
    ap.add_argument("--cameras", nargs="*", default=None,
                    help="Restrict to these camera folder names.")
    ap.add_argument("--date", default=None,
                    help="Restrict to a single recording date (YYYY-MM-DD).")
    ap.add_argument("--case", type=int, default=None, help="Restrict to a single case number.")
    ap.add_argument("--samples", type=int, default=7,
                    help="Number of dHash spot-checks per pair (default: 7).")
    ap.add_argument("--search-window-frames", type=int, default=1800,
                    help="Search radius around the predicted new index, in frames "
                         "(default: 1800 = +/- 60 s at 30 fps). Wide enough to find "
                         "a match even when the analytical prediction is off by tens "
                         "of seconds; narrow at your own risk.")
    ap.add_argument("--dry-run", action="store_true",
                    help="Pair files and compute analytical offsets only; skip decoding.")
    ap.add_argument("--debug", action="store_true", help="Verbose per-sample logs.")
    return ap.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    old_root = Path(args.old_root).resolve()
    new_root = Path(args.new_root).resolve()
    out_csv = Path(args.out_csv).resolve()

    log_path = _PROJECT_ROOT / "logs" / "align" / f"align_{datetime.now():%Y%m%d_%H%M%S}.log"
    _setup_logging(args.debug, log_path)

    LOG.info("Old root: %s", old_root)
    LOG.info("New root: %s", new_root)
    LOG.info("CSV out:  %s", out_csv)
    if args.out_json:
        LOG.info("JSON out: %s", Path(args.out_json).resolve())
    LOG.info("Mode:     %s", "dry-run (no decoding)" if args.dry_run else "verify (analytical + dHash)")

    cameras_filter: set[str] | None = set(args.cameras) if args.cameras else None

    t0 = time.time()
    pairs = build_pairs(old_root, new_root, cameras_filter, args.date, args.case)
    LOG.info("Found %d old MP4s; %d paired with a new MP4.",
             len(pairs), sum(1 for p in pairs if p.new_path is not None))

    db_path = get_db_path()
    enriched = load_enriched_timings(db_path)
    annotate_predicted_offsets(pairs, enriched)

    if args.dry_run:
        for p in pairs[:50]:
            LOG.info("  %s Case%-2d %-22s predicted_offset=%s frames (%s s) %s",
                     p.recording_date, p.case_no, p.camera_name,
                     p.predicted_offset_frames if p.predicted_offset_frames is not None else "?",
                     f"{p.predicted_offset_seconds:.3f}" if p.predicted_offset_seconds is not None else "?",
                     ";".join(p.warnings))
        if len(pairs) > 50:
            LOG.info("  ... (+%d more)", len(pairs) - 50)
        write_csv(pairs, out_csv)
        if args.out_json:
            write_json(pairs, Path(args.out_json).resolve())
        LOG.info("Dry-run report written. Elapsed: %.1fs", time.time() - t0)
        return 0

    ffmpeg = _resolve_tool("ffmpeg")
    ffprobe = _resolve_tool("ffprobe")
    if not ffmpeg or not ffprobe:
        LOG.error("ffmpeg/ffprobe not found on PATH or in common install locations.")
        return 2

    # ffprobe everything first (fast, ~tens of ms per file) so we know fps
    # and frame counts before we start the slower verification loop.
    for p in pairs:
        populate_ffprobe_fields(p, ffprobe)

    # Sample-and-hash verification.
    for i, p in enumerate(pairs, 1):
        if p.new_path is None:
            LOG.info("[%d/%d] %s Case%-2d %-22s SKIP (no new MP4)",
                     i, len(pairs), p.recording_date, p.case_no, p.camera_name)
            continue
        LOG.info("[%d/%d] %s Case%-2d %-22s verifying ...",
                 i, len(pairs), p.recording_date, p.case_no, p.camera_name)
        verify_pair(p, ffmpeg, args.samples, args.search_window_frames)
        LOG.info(
            "    predicted=%s  measured=%s  conf=%.2f (%d/%d)  drift=%s  warns=%s",
            p.predicted_offset_frames if p.predicted_offset_frames is not None else "?",
            p.measured_offset_frames if p.measured_offset_frames is not None else "?",
            p.confidence, p.n_samples_matched, p.n_samples,
            "yes" if p.is_drifting else "no",
            ";".join(p.warnings) or "-",
        )

    write_csv(pairs, out_csv)
    if args.out_json:
        write_json(pairs, Path(args.out_json).resolve())
    LOG.info("Done. Wrote %d rows in %.1fs", len(pairs), time.time() - t0)
    return 0


if __name__ == "__main__":
    sys.exit(main())
