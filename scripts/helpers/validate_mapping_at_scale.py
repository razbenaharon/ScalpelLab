"""Broad-scale validation of the OLD->NEW MP4 frame mapping.

Validates the candidate production formula derived in the v2 discovery
experiment:

    Step 1: OLD MP4 -> SEQ
        K_seq = min(i_old, idx_frames - 2)

    Step 2: SEQ -> NEW MP4
        new_frame = clamp(pre_roll + round((t_seq[K_seq] - t_seq[0]) * 30),
                          0, new_nb_frames - 1)

    Step 3: Verification
        dHash OLD frame vs NEW window predicted_new_frame +/- 3.
        H <= 4   : PASS_HIGH_CONFIDENCE
        4 < H<=16: PASS
        H > 16   : VERIFY_FAILED

Phase 1: stratified sample of up to ``--max-pairs`` (case, camera)
pairs from ``seq_enriched``; ``--anchors-per-pair`` anchors each.
Per anchor: 2 ffmpeg subprocess calls (OLD frame + NEW ±3 window).

Phase 2: triggered per pair when any anchor returns H > 16, or
verify-failed rate >= 30%, or sanity check fails. Re-runs the pair
with ``--anchors-on-failure`` anchors and the full diagnostic block
from validate_mapping_formulas.validate_camera (baselines + empirical
K search).

Phase 3: optional expansion. Enabled with ``--phase3-pairs N``.
Identical to Phase 1 with a larger budget and (usually) fewer anchors.

Outputs (under ``--out-dir``):
    per_anchor_results.csv
    per_pair_summary.csv
    global_summary.csv
    outliers.csv
    final_validation_report.md
"""

from __future__ import annotations

import argparse
import csv
import io
import os
import random
import sqlite3
import statistics
import sys
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

# Stdout UTF-8 on Windows console.
if hasattr(sys.stdout, "buffer") and (sys.stdout.encoding or "").lower() not in ("utf-8", "utf8"):
    sys.stdout = io.TextIOWrapper(
        sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True
    )

_SCRIPT_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _SCRIPT_DIR.parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

try:
    from config import get_db_path, get_seq_root, get_mp4_root  # type: ignore
except ImportError:
    get_db_path = lambda: str(_PROJECT_ROOT / "ScalpelDatabase.sqlite")  # type: ignore
    get_seq_root = lambda: r"F:\Room_8_Data\Sequence_Backup"  # type: ignore
    get_mp4_root = lambda: r"F:\Room_8_Data\Recordings"  # type: ignore

# Reuse the proven primitives from the v2 validator -- no production refactor.
from scripts.helpers.validate_mapping_formulas import (  # type: ignore
    CameraContext,
    G_HYPOTHESES,
    TARGET_FPS,
    _EPOCH_MAX,
    _EPOCH_MIN,
    _build_context_for_camera,
    _camera_group,
    _dhash_batch,
    _extract_window,
    _f_seq_to_new,
    _ffprobe_basic,
    _fit_learned_linear,
    _hamming,
    _make_learned_g,
    _old_mp4,
    _new_mp4,
    _resolve_tool,
    parse_idx_timestamps,
    summarise,
    validate_camera,
)


# ---------------------------------------------------------------------------
# Constants / tuning
# ---------------------------------------------------------------------------
DEFAULT_OLD_ROOT = r"D:\Rec_Backup"
DEFAULT_OUT_DIR = _PROJECT_ROOT / "out" / "at_scale_validation"

# Hamming verdict thresholds (must match the candidate spec above).
PASS_HIGH_CONF_THRESHOLD = 4
PASS_THRESHOLD = 16

# Phase 2 escalation triggers.
ESCALATE_VERIFY_FAILED_FRACTION = 0.30   # >= 30% anchors with H>16
ESCALATE_RESIDUAL_ABS_MAX = 3            # candidate window is +/-3

# Anchor placement: fixed fractions + a few random.
_FIXED_FRACTIONS = (0.02, 0.10, 0.25, 0.50, 0.75, 0.90, 0.98)
_RANDOM_FRACTION_RANGE = (0.05, 0.95)

# Empirical K search window for Phase 2 (matches validate_mapping_formulas).
_PHASE2_SEARCH_RADIUS_SEQ = 30
_PHASE2_LOCAL_RADIUS = 5

# Stratification quotas (target = max_pairs).
@dataclass
class StratumSpec:
    name: str
    quota: int
    predicate: callable  # type: ignore[type-arg]


def _modal_predicate(p):
    return 29.5 <= (p.source_fps or 0) <= 30.5 and 2.0 <= (p.duration_h or 0) <= 6.0


# v2 positive controls (already validated). Pulled in first so the broad
# sample doubles as a cross-version consistency check.
V2_CONTROLS = {
    ("2025-01-01", 1, "Cart_Center_2"),
    ("2023-02-09", 2, "General_3"),
    ("2024-11-17", 1, "Monitor"),
    ("2025-10-12", 1, "General_3"),
}


# ---------------------------------------------------------------------------
# Pair selection
# ---------------------------------------------------------------------------
@dataclass
class PairCandidate:
    recording_date: str
    case_no: int
    camera_name: str
    first_frame_time: float
    idx_frames: int
    source_fps: float
    actual_duration: float
    group_label: str = ""
    pre_roll_frames: int = 0
    old_path: Path | None = None
    new_path: Path | None = None
    duration_h: float = 0.0

    def key(self) -> tuple[str, int, str]:
        return (self.recording_date, self.case_no, self.camera_name)


def query_candidate_pairs(db_path: str) -> list[PairCandidate]:
    """Read seq_enriched and return every (date, case, camera) row that
    has a parseable first_frame_time and a non-trivial IDX."""
    out: list[PairCandidate] = []
    if not Path(db_path).is_file():
        return out
    with sqlite3.connect(db_path) as conn:
        cur = conn.execute(
            "SELECT recording_date, case_no, camera_name, "
            "       first_frame_time, idx_frames, fps, actual_duration "
            "FROM   seq_enriched "
            "WHERE  first_frame_time BETWEEN ? AND ? "
            "  AND  idx_frames > 100 "
            "  AND  camera_name NOT LIKE '%_JUNK' "
            "  AND  camera_name NOT LIKE '%_Junk'",
            (_EPOCH_MIN, _EPOCH_MAX),
        )
        for date, case_no, cam, t0, n_idx, fps, dur in cur.fetchall():
            try:
                t0f = float(t0)
                n_idxi = int(n_idx)
                fpsf = float(fps or 0.0)
                durf = float(dur or 0.0)
            except (TypeError, ValueError):
                continue
            out.append(PairCandidate(
                recording_date=str(date),
                case_no=int(case_no),
                camera_name=str(cam),
                first_frame_time=t0f,
                idx_frames=n_idxi,
                source_fps=fpsf,
                actual_duration=durf,
                duration_h=durf / 3600.0,
            ))
    return out


def annotate_pre_roll(pairs: list[PairCandidate]) -> None:
    """Compute group label and pre_roll_frames per pair, in place.

    pre_roll_frames = round((cam.first_frame_time - group.t_global_start) * 30)
    where t_global_start is the min over the same (date, case, group).
    SOLO cameras have pre_roll = 0 by construction (group of one).
    """
    group_min: dict[tuple[str, int, str], float] = {}
    for p in pairs:
        p.group_label = _camera_group(p.camera_name)
        if p.group_label == "UNKNOWN":
            continue
        gkey = (p.recording_date, p.case_no, p.group_label)
        prev = group_min.get(gkey)
        if prev is None or p.first_frame_time < prev:
            group_min[gkey] = p.first_frame_time
    for p in pairs:
        if p.group_label == "UNKNOWN":
            continue
        gkey = (p.recording_date, p.case_no, p.group_label)
        t_g = group_min.get(gkey, p.first_frame_time)
        p.pre_roll_frames = int(round((p.first_frame_time - t_g) * TARGET_FPS))


def filter_existing_files(
    pairs: list[PairCandidate], old_root: Path, new_root: Path,
) -> list[PairCandidate]:
    """Drop pairs whose OLD and NEW MP4 files do not both exist."""
    kept: list[PairCandidate] = []
    for p in pairs:
        if p.group_label == "UNKNOWN":
            continue
        op = _old_mp4(old_root, p.recording_date, p.case_no, p.camera_name)
        if op is None:
            continue
        np_ = _new_mp4(new_root, p.recording_date, p.case_no, p.camera_name)
        if np_ is None:
            continue
        p.old_path = op
        p.new_path = np_
        kept.append(p)
    return kept


def _bucket_source_fps(fps: float) -> str:
    if fps < 25: return "<25"
    if fps < 28: return "25-28"
    if fps < 29.5: return "28-29.5"
    if fps < 30.5: return "29.5-30.5"
    if fps < 40: return "30.5-40"
    if fps < 50: return "40-50"
    return ">=50"


def _bucket_pre_roll(pr: int) -> str:
    if pr <= 0: return "0"
    if pr <= 2: return "1-2"
    if pr <= 9: return "3-9"
    if pr <= 29: return "10-29"
    if pr <= 99: return "30-99"
    return ">=100"


def _bucket_duration_h(h: float) -> str:
    if h < 0.5: return "<30min"
    if h < 2.0: return "30min-2h"
    if h < 6.0: return "2-6h"
    return ">6h"


def stratified_sample(
    pairs: list[PairCandidate], max_pairs: int, seed: int,
    include_v2_controls: bool = True,
) -> list[tuple[str, PairCandidate]]:
    """Pick up to ``max_pairs`` pairs using ordered stratum quotas.

    Returns list of (stratum_label, pair). v2 controls are taken first
    (when present in ``pairs``) to anchor cross-version consistency.
    """
    rng = random.Random(seed)
    used: set[tuple[str, int, str]] = set()
    out: list[tuple[str, PairCandidate]] = []

    def take(candidates: list[PairCandidate], quota: int, label: str) -> None:
        avail = [p for p in candidates if p.key() not in used]
        n = min(quota, len(avail), max_pairs - len(out))
        if n <= 0:
            return
        chosen = rng.sample(avail, n)
        for p in chosen:
            used.add(p.key())
            out.append((label, p))

    # v2 positive controls.
    if include_v2_controls:
        ctrls = [p for p in pairs if p.key() in V2_CONTROLS]
        for p in ctrls:
            if p.key() not in used and len(out) < max_pairs:
                used.add(p.key())
                out.append(("v2_control", p))

    # Discrimination strata (order = priority).
    take([p for p in pairs if p.camera_name == "Injection_Port"], 8, "injection_port")
    take([p for p in pairs if p.camera_name == "Monitor" and p.source_fps >= 50], 8, "monitor_60fps")
    take([p for p in pairs if 0 < p.source_fps < 25], 6, "source_fps_lt25")
    take([p for p in pairs if p.duration_h > 6.0], 5, "duration_gt6h")
    take([p for p in pairs if 0.0 < p.duration_h < 0.5], 4, "duration_lt30min")
    take([p for p in pairs if p.pre_roll_frames >= 30], 5, "pre_roll_ge30")
    take([p for p in pairs if p.camera_name == "General_3"], 4, "general3_mix")

    # Modal stratum (proportional A vs B, spread across years).
    modal = [p for p in pairs if _modal_predicate(p)]
    take(modal, max_pairs - len(out), "modal")

    # Fallback: if still under-quota, take anything left at random.
    if len(out) < max_pairs:
        leftovers = [p for p in pairs if p.key() not in used]
        take(leftovers, max_pairs - len(out), "fallback")

    return out


# ---------------------------------------------------------------------------
# Per-anchor fast path (candidate-only)
# ---------------------------------------------------------------------------
def pick_anchors(ctx: CameraContext, n_anchors: int, seed_for_pair: int) -> list[int]:
    """Fixed fractions + a few uniform-random anchors in [5%, 95%]."""
    n_old = max(0, ctx.old_nb_frames)
    if n_old <= 10:
        return list(range(n_old))
    lo = max(1, int(n_old * 0.005))
    hi = min(n_old - 1, int(n_old * 0.995))
    if hi <= lo:
        return []

    fixed = [int(round(n_old * f)) for f in _FIXED_FRACTIONS]
    n_random = max(0, n_anchors - len(fixed))
    rng = random.Random(seed_for_pair)
    r_lo = max(lo, int(n_old * _RANDOM_FRACTION_RANGE[0]))
    r_hi = min(hi, int(n_old * _RANDOM_FRACTION_RANGE[1]))
    rand_anchors: list[int] = []
    if r_hi > r_lo:
        rand_anchors = [rng.randint(r_lo, r_hi) for _ in range(n_random)]

    # Truncate fixed to honour n_anchors when caller asked for fewer.
    all_anchors = (fixed[:n_anchors] + rand_anchors[:max(0, n_anchors - len(fixed))])
    # De-dup while preserving order, clamp to valid range, sort.
    seen: set[int] = set()
    cleaned: list[int] = []
    for a in all_anchors:
        a = max(0, min(a, n_old - 1))
        if a in seen:
            continue
        seen.add(a)
        cleaned.append(a)
    cleaned.sort()
    return cleaned


def evaluate_anchor_fast(
    ctx: CameraContext, i_old: int, ffmpeg: str,
) -> dict:
    """Candidate-only evaluation. Two ffmpeg calls per anchor."""
    t_start = time.perf_counter()

    # 1. OLD anchor frame.
    t_old = i_old / ctx.old_fps if ctx.old_fps > 0 else i_old / TARGET_FPS
    old_frames = _extract_window(ctx.old_path, t_old, 1, ffmpeg)
    if old_frames is None or old_frames.shape[0] == 0:
        return {
            "i_old": i_old, "error": "OLD_EXTRACT_FAILED",
            "runtime_seconds": time.perf_counter() - t_start,
        }
    h_old = _dhash_batch(old_frames)[0]

    # 2. K_seq + predicted NEW frame (candidate formula).
    K_seq = max(0, min(i_old, ctx.idx_frames - 2))
    idx_ts_K = float(ctx.t_seq[K_seq])
    predicted_unclamped = ctx.pre_roll + int(round((idx_ts_K - ctx.t_cam) * TARGET_FPS))
    predicted = max(0, min(predicted_unclamped, ctx.new_nb_frames - 1))

    # 3. NEW window [predicted-3, predicted+3].
    lo = max(0, predicted - 3)
    hi = min(ctx.new_nb_frames - 1, predicted + 3)
    n_window = hi - lo + 1
    new_frames = _extract_window(ctx.new_path, lo / TARGET_FPS, n_window, ffmpeg)
    if new_frames is None or new_frames.shape[0] == 0:
        return {
            "i_old": i_old, "K_seq": K_seq, "idx_timestamp_K": idx_ts_K,
            "predicted_new_frame": predicted,
            "error": "NEW_EXTRACT_FAILED",
            "runtime_seconds": time.perf_counter() - t_start,
        }
    new_hashes = _dhash_batch(new_frames)

    # 4. Best match (tie-break toward predicted).
    best: tuple[int, int, int] | None = None
    for k_idx, h in enumerate(new_hashes):
        j = lo + k_idx
        if j > hi:
            break
        ham = _hamming(h_old, h)
        tup = (ham, abs(j - predicted), j)
        if best is None or tup < best:
            best = tup
    if best is None:
        return {
            "i_old": i_old, "K_seq": K_seq, "idx_timestamp_K": idx_ts_K,
            "predicted_new_frame": predicted,
            "error": "NO_HASH_HITS",
            "runtime_seconds": time.perf_counter() - t_start,
        }

    ham, _, best_j = best
    residual = best_j - predicted

    if ham <= PASS_HIGH_CONF_THRESHOLD:
        status = "PASS_HIGH_CONFIDENCE"
    elif ham <= PASS_THRESHOLD:
        status = "PASS"
    else:
        status = "VERIFY_FAILED"

    return {
        "i_old": i_old,
        "K_seq": K_seq,
        "idx_timestamp_K": idx_ts_K,
        "predicted_new_frame": predicted,
        "best_new_frame": best_j,
        "residual_new": residual,
        "hamming": ham,
        "status": status,
        "runtime_seconds": time.perf_counter() - t_start,
        "diagnostic_search_used": False,
    }


def validate_pair_fast(
    ctx: CameraContext, n_anchors: int, ffmpeg: str, seed_for_pair: int,
) -> list[dict]:
    anchors = pick_anchors(ctx, n_anchors, seed_for_pair)
    return [evaluate_anchor_fast(ctx, i, ffmpeg) for i in anchors]


# ---------------------------------------------------------------------------
# Phase 2 escalation -- reuses validate_camera() from v2
# ---------------------------------------------------------------------------
def validate_pair_diagnostic(
    ctx: CameraContext, n_anchors: int, ffmpeg: str,
) -> list[dict]:
    """Full diagnostic block from validate_mapping_formulas.validate_camera.

    Returns anchor results in the v2 schema (with per_formula block).
    Caller normalises these into the per_anchor_results.csv schema.
    """
    # Two-pass fit for g_linear (same approach as v2 main()).
    first = validate_camera(
        ctx, ffmpeg, n_anchors,
        local_radius=_PHASE2_LOCAL_RADIUS,
        search_radius_seq=_PHASE2_SEARCH_RADIUS_SEQ,
    )
    fit = _fit_learned_linear(first)
    if fit is None:
        return first
    a, b = fit
    extras = [("g_linear", _make_learned_g(a, b))]
    return validate_camera(
        ctx, ffmpeg, n_anchors,
        local_radius=_PHASE2_LOCAL_RADIUS,
        search_radius_seq=_PHASE2_SEARCH_RADIUS_SEQ,
        extra_predictors=extras,
    )


def normalise_diagnostic_anchor(
    ctx: CameraContext, v2_anchor: dict,
) -> dict:
    """Map a v2 ``_evaluate_one_anchor`` result into the per_anchor schema.

    Uses the g_identity row from per_formula as the production formula's
    measured outcome (which is what the candidate is); residual_new
    becomes best_offset (from the local search around predicted_j).
    """
    i_old = v2_anchor.get("i_old")
    err = v2_anchor.get("error")
    if err:
        return {
            "i_old": i_old, "error": err,
            "diagnostic_search_used": True,
            "runtime_seconds": 0.0,
        }
    per_formula = v2_anchor.get("per_formula") or {}
    ident = per_formula.get("g_identity") or {}
    K_seq = ident.get("K_pred")
    if K_seq is None:
        K_seq = max(0, min(int(i_old), ctx.idx_frames - 2))
    Kc = max(0, min(int(K_seq), ctx.idx_frames - 1))
    idx_ts_K = float(ctx.t_seq[Kc])
    predicted = ident.get("predicted_j")
    ham_local = ident.get("min_hamming_local")
    ham_at = ident.get("hamming_at_predicted")
    best_offset = ident.get("best_offset")
    ham = ham_local if ham_local is not None else ham_at
    if ham is None:
        ham = v2_anchor.get("hamming_empirical")
    if predicted is None or predicted < 0:
        return {
            "i_old": i_old, "K_seq": Kc, "idx_timestamp_K": idx_ts_K,
            "predicted_new_frame": predicted, "error": "OUT_OF_RANGE",
            "diagnostic_search_used": True, "runtime_seconds": 0.0,
        }
    best_j = predicted + (best_offset or 0)
    residual = best_j - predicted

    if ham is None:
        status = "VERIFY_FAILED"
    elif ham <= PASS_HIGH_CONF_THRESHOLD:
        status = "PASS_HIGH_CONFIDENCE"
    elif ham <= PASS_THRESHOLD:
        status = "PASS"
    else:
        status = "VERIFY_FAILED"

    return {
        "i_old": i_old,
        "K_seq": Kc,
        "idx_timestamp_K": idx_ts_K,
        "predicted_new_frame": predicted,
        "best_new_frame": best_j,
        "residual_new": residual,
        "hamming": ham,
        "status": status,
        "diagnostic_search_used": True,
        "runtime_seconds": 0.0,
        # carry the v2 metric block forward for outliers.csv triage.
        "_diag": {
            "K_empirical": v2_anchor.get("K_empirical"),
            "j_empirical": v2_anchor.get("j_empirical"),
            "hamming_empirical": v2_anchor.get("hamming_empirical"),
            "per_formula": per_formula,
        },
    }


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------
@dataclass
class PairResult:
    pair: PairCandidate
    stratum: str
    ctx: CameraContext | None
    anchors: list[dict] = field(default_factory=list)
    escalated: bool = False
    runtime_seconds: float = 0.0
    ctx_error: str | None = None
    old_nb_eq_idx_minus_1: bool | None = None

    @property
    def key(self) -> tuple[str, int, str]:
        return self.pair.key()


def summarise_pair(pr: PairResult) -> dict:
    """One row per pair for per_pair_summary.csv."""
    good = [a for a in pr.anchors if "error" not in a]
    n = len(good)
    n_phc = sum(1 for a in good if a.get("status") == "PASS_HIGH_CONFIDENCE")
    n_pass = sum(1 for a in good if a.get("status") == "PASS")
    n_fail = sum(1 for a in good if a.get("status") == "VERIFY_FAILED")

    ham = [a["hamming"] for a in good if a.get("hamming") is not None]
    res = [a["residual_new"] for a in good if a.get("residual_new") is not None]
    i_old = [a["i_old"] for a in good if a.get("residual_new") is not None]

    mean_h = sum(ham) / len(ham) if ham else None
    median_h = statistics.median(ham) if ham else None
    max_h = max(ham) if ham else None
    median_r = statistics.median(res) if res else None
    max_abs_r = max(abs(r) for r in res) if res else None

    slope = None
    end_drift = None
    if len(res) >= 3 and len(set(i_old)) >= 3:
        x = np.array(i_old, dtype=np.float64)
        y = np.array(res, dtype=np.float64)
        try:
            slope_, _intercept = np.polyfit(x, y, 1)
            slope = float(slope_)
            if pr.ctx is not None:
                end_drift = float(slope * pr.ctx.old_nb_frames)
        except (np.linalg.LinAlgError, ValueError):
            slope = None

    total = n if n > 0 else 1
    pass_rate = (n_phc + n_pass) / total if n > 0 else 0.0
    phc_rate = n_phc / total if n > 0 else 0.0

    if pr.ctx_error:
        recommendation = "BLOCKED_NO_CONTEXT"
    elif pr.old_nb_eq_idx_minus_1 is False:
        recommendation = "INVESTIGATE_FRAME_COUNT"
    elif n == 0:
        recommendation = "BLOCKED_NO_ANCHORS"
    elif pass_rate >= 0.95 and (max_h or 0) <= PASS_THRESHOLD:
        recommendation = "OK"
    elif pass_rate >= 0.80:
        recommendation = "OK_WITH_WARNINGS"
    elif n_fail >= max(1, int(n * 0.30)):
        recommendation = "VERIFY_FAILED"
    else:
        recommendation = "INVESTIGATE"

    return {
        "recording_date": pr.pair.recording_date,
        "case_no": pr.pair.case_no,
        "camera_name": pr.pair.camera_name,
        "stratum": pr.stratum,
        "n_anchors": n,
        "pass_high_confidence_count": n_phc,
        "pass_count": n_pass,
        "verify_failed_count": n_fail,
        "pass_rate": round(pass_rate, 4),
        "high_confidence_rate": round(phc_rate, 4),
        "mean_hamming": round(mean_h, 3) if mean_h is not None else "",
        "median_hamming": median_h if median_h is not None else "",
        "max_hamming": max_h if max_h is not None else "",
        "median_residual_new": median_r if median_r is not None else "",
        "max_abs_residual_new": max_abs_r if max_abs_r is not None else "",
        "residual_drift_slope": round(slope, 6) if slope is not None else "",
        "estimated_end_drift": round(end_drift, 3) if end_drift is not None else "",
        "recommendation": recommendation,
        "escalation_status": "ESCALATED" if pr.escalated else "PHASE1",
        "runtime_seconds": round(pr.runtime_seconds, 2),
    }


def should_escalate(summary: dict, pr: PairResult, anchors_per_pair: int) -> bool:
    """Decide whether to run Phase 2 on this pair."""
    if pr.ctx_error:
        return True  # context build failed; record but don't escalate further
    if pr.old_nb_eq_idx_minus_1 is False:
        return True
    n = summary["n_anchors"]
    if n == 0:
        return False
    n_fail = summary["verify_failed_count"]
    if n_fail >= max(1, int(round(anchors_per_pair * ESCALATE_VERIFY_FAILED_FRACTION))):
        return True
    max_abs_r = summary.get("max_abs_residual_new")
    if isinstance(max_abs_r, (int, float)) and max_abs_r > ESCALATE_RESIDUAL_ABS_MAX:
        return True
    return False


# ---------------------------------------------------------------------------
# Output writers
# ---------------------------------------------------------------------------
_ANCHOR_COLS = [
    "recording_date", "case_no", "camera_name", "stratum",
    "old_frame", "K_seq", "idx_timestamp_K",
    "predicted_new_frame", "best_new_frame", "residual_new",
    "hamming", "status",
    "source_fps", "old_mp4_fps", "new_pre_roll_frames",
    "old_nb_frames", "idx_frames", "new_nb_frames",
    "runtime_seconds", "diagnostic_search_used",
    "error", "phase",
]

_SUMMARY_COLS = [
    "recording_date", "case_no", "camera_name", "stratum",
    "n_anchors", "pass_high_confidence_count", "pass_count", "verify_failed_count",
    "pass_rate", "high_confidence_rate",
    "mean_hamming", "median_hamming", "max_hamming",
    "median_residual_new", "max_abs_residual_new",
    "residual_drift_slope", "estimated_end_drift",
    "recommendation", "escalation_status", "runtime_seconds",
]

_OUTLIER_COLS = [
    "recording_date", "case_no", "camera_name", "scope",
    "reason", "details",
    "i_old", "K_seq", "predicted_new_frame", "best_new_frame",
    "residual_new", "hamming",
]


def _flatten_anchor_row(pr: PairResult, a: dict, phase: str) -> dict:
    p = pr.pair
    ctx = pr.ctx
    return {
        "recording_date": p.recording_date,
        "case_no": p.case_no,
        "camera_name": p.camera_name,
        "stratum": pr.stratum,
        "old_frame": a.get("i_old", ""),
        "K_seq": a.get("K_seq", ""),
        "idx_timestamp_K": a.get("idx_timestamp_K", ""),
        "predicted_new_frame": a.get("predicted_new_frame", ""),
        "best_new_frame": a.get("best_new_frame", ""),
        "residual_new": a.get("residual_new", ""),
        "hamming": a.get("hamming", ""),
        "status": a.get("status", ""),
        "source_fps": ctx.source_fps if ctx else p.source_fps,
        "old_mp4_fps": ctx.old_fps if ctx else "",
        "new_pre_roll_frames": ctx.pre_roll if ctx else p.pre_roll_frames,
        "old_nb_frames": ctx.old_nb_frames if ctx else "",
        "idx_frames": ctx.idx_frames if ctx else p.idx_frames,
        "new_nb_frames": ctx.new_nb_frames if ctx else "",
        "runtime_seconds": round(a.get("runtime_seconds", 0.0), 3),
        "diagnostic_search_used": bool(a.get("diagnostic_search_used", False)),
        "error": a.get("error", ""),
        "phase": phase,
    }


def write_csv(rows: list[dict], cols: list[str], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in cols})


def write_global_summary(
    path: Path, all_anchor_rows: list[dict], pair_summaries: list[dict],
    runtime_seconds: float,
) -> dict:
    """Write the global summary CSV (totals + roll-ups). Returns the totals
    dict so the markdown report can reuse it."""
    n_pairs = len(pair_summaries)
    n_anchors = sum(1 for r in all_anchor_rows if r.get("status"))
    statuses = Counter(r.get("status") for r in all_anchor_rows if r.get("status"))
    total = max(1, sum(statuses.values()))
    phc_rate = statuses.get("PASS_HIGH_CONFIDENCE", 0) / total
    pass_rate = (statuses.get("PASS_HIGH_CONFIDENCE", 0) + statuses.get("PASS", 0)) / total
    fail_rate = statuses.get("VERIFY_FAILED", 0) / total

    totals = {
        "scope": "TOTAL",
        "key": "ALL",
        "n_pairs": n_pairs,
        "n_anchors": n_anchors,
        "pass_high_confidence_rate": round(phc_rate, 4),
        "pass_rate": round(pass_rate, 4),
        "verify_failed_rate": round(fail_rate, 4),
        "avg_runtime_per_pair": round(runtime_seconds / n_pairs, 2) if n_pairs else "",
        "total_runtime_seconds": round(runtime_seconds, 2),
    }

    # Roll-ups.
    rollups: list[dict] = [totals]
    for scope_name, key_fn in [
        ("camera", lambda r: r.get("camera_name")),
        ("source_fps_bucket", lambda r: _bucket_source_fps(float(r.get("source_fps") or 0))),
        ("pre_roll_bucket", lambda r: _bucket_pre_roll(int(r.get("new_pre_roll_frames") or 0))),
        ("duration_bucket", lambda r: _bucket_duration_h(_dur_h_from_row(r))),
    ]:
        grouped: dict[str, list[dict]] = defaultdict(list)
        for r in all_anchor_rows:
            if not r.get("status"):
                continue
            k = key_fn(r)
            if k is None:
                continue
            grouped[str(k)].append(r)
        for k, rows in sorted(grouped.items()):
            sub_total = max(1, sum(1 for r in rows if r.get("status")))
            phc = sum(1 for r in rows if r.get("status") == "PASS_HIGH_CONFIDENCE")
            p_ = sum(1 for r in rows if r.get("status") == "PASS")
            f_ = sum(1 for r in rows if r.get("status") == "VERIFY_FAILED")
            rollups.append({
                "scope": scope_name,
                "key": k,
                "n_pairs": len({(r["recording_date"], r["case_no"], r["camera_name"]) for r in rows}),
                "n_anchors": sub_total,
                "pass_high_confidence_rate": round(phc / sub_total, 4),
                "pass_rate": round((phc + p_) / sub_total, 4),
                "verify_failed_rate": round(f_ / sub_total, 4),
                "avg_runtime_per_pair": "",
                "total_runtime_seconds": "",
            })

    cols = ["scope", "key", "n_pairs", "n_anchors",
            "pass_high_confidence_rate", "pass_rate", "verify_failed_rate",
            "avg_runtime_per_pair", "total_runtime_seconds"]
    write_csv(rollups, cols, path)
    return totals


def _dur_h_from_row(r: dict) -> float:
    """Estimate duration in hours from old_nb_frames / old_mp4_fps."""
    try:
        nf = float(r.get("old_nb_frames") or 0)
        fps = float(r.get("old_mp4_fps") or 0)
        if fps <= 0:
            return 0.0
        return (nf / fps) / 3600.0
    except (TypeError, ValueError):
        return 0.0


# ---------------------------------------------------------------------------
# Outlier collection
# ---------------------------------------------------------------------------
def collect_outliers(
    pair_results: list[PairResult], all_anchor_rows: list[dict],
) -> list[dict]:
    out: list[dict] = []

    # Pair-level reasons.
    for pr in pair_results:
        p = pr.pair
        base = {
            "recording_date": p.recording_date,
            "case_no": p.case_no,
            "camera_name": p.camera_name,
            "scope": "pair",
        }
        if pr.ctx_error:
            out.append({**base, "reason": pr.ctx_error,
                        "details": "context build failed"})
            continue
        if pr.old_nb_eq_idx_minus_1 is False and pr.ctx is not None:
            out.append({**base,
                        "reason": "OLD_NB_NOT_IDX_MINUS_1",
                        "details": f"old_nb_frames={pr.ctx.old_nb_frames}, "
                                   f"idx_frames={pr.ctx.idx_frames}"})

    # Anchor-level reasons.
    for r in all_anchor_rows:
        scope_base = {
            "recording_date": r["recording_date"],
            "case_no": r["case_no"],
            "camera_name": r["camera_name"],
            "scope": "anchor",
        }
        if r.get("error"):
            out.append({**scope_base, "reason": r["error"], "details": "",
                        "i_old": r.get("old_frame"),
                        "K_seq": r.get("K_seq"),
                        "predicted_new_frame": r.get("predicted_new_frame"),
                        "best_new_frame": r.get("best_new_frame"),
                        "residual_new": r.get("residual_new"),
                        "hamming": r.get("hamming")})
            continue
        ham = r.get("hamming")
        res = r.get("residual_new")
        if isinstance(ham, (int, float)) and ham > PASS_THRESHOLD:
            out.append({**scope_base, "reason": "HAMMING_GT_16",
                        "details": f"H={ham}",
                        "i_old": r.get("old_frame"),
                        "K_seq": r.get("K_seq"),
                        "predicted_new_frame": r.get("predicted_new_frame"),
                        "best_new_frame": r.get("best_new_frame"),
                        "residual_new": res,
                        "hamming": ham})
        elif isinstance(res, (int, float)) and abs(res) > ESCALATE_RESIDUAL_ABS_MAX:
            out.append({**scope_base, "reason": "RESIDUAL_ABS_GT_3",
                        "details": f"residual_new={res}",
                        "i_old": r.get("old_frame"),
                        "K_seq": r.get("K_seq"),
                        "predicted_new_frame": r.get("predicted_new_frame"),
                        "best_new_frame": r.get("best_new_frame"),
                        "residual_new": res,
                        "hamming": ham})

    return out


# ---------------------------------------------------------------------------
# Markdown report
# ---------------------------------------------------------------------------
def write_markdown_report(
    path: Path, pair_summaries: list[dict], totals: dict,
    runtime_seconds: float, args: argparse.Namespace,
    outliers: list[dict], all_anchor_rows: list[dict],
) -> None:
    n_pairs = len(pair_summaries)
    pass_rate = totals["pass_rate"]
    phc_rate = totals["pass_high_confidence_rate"]
    fail_rate = totals["verify_failed_rate"]

    by_camera: dict[str, list[dict]] = defaultdict(list)
    by_fps: dict[str, list[dict]] = defaultdict(list)
    by_duration: dict[str, list[dict]] = defaultdict(list)
    by_preroll: dict[str, list[dict]] = defaultdict(list)
    for r in all_anchor_rows:
        if not r.get("status"):
            continue
        by_camera[str(r.get("camera_name"))].append(r)
        by_fps[_bucket_source_fps(float(r.get("source_fps") or 0))].append(r)
        by_duration[_bucket_duration_h(_dur_h_from_row(r))].append(r)
        by_preroll[_bucket_pre_roll(int(r.get("new_pre_roll_frames") or 0))].append(r)

    def _stratum_rates(rows: list[dict]) -> tuple[float, float, float, int]:
        total = max(1, len(rows))
        phc = sum(1 for r in rows if r.get("status") == "PASS_HIGH_CONFIDENCE")
        p = sum(1 for r in rows if r.get("status") == "PASS")
        f = sum(1 for r in rows if r.get("status") == "VERIFY_FAILED")
        return phc / total, (phc + p) / total, f / total, len(rows)

    def _table(d: dict[str, list[dict]], header: str) -> list[str]:
        lines = [f"### {header}", "",
                 "| key | n_anchors | PASS_HIGH | PASS | VERIFY_FAIL |",
                 "|---|---:|---:|---:|---:|"]
        for k in sorted(d.keys()):
            phc, ps, fl, n = _stratum_rates(d[k])
            lines.append(f"| {k} | {n} | {phc:.1%} | {ps:.1%} | {fl:.1%} |")
        lines.append("")
        return lines

    escalated = [s for s in pair_summaries if s["escalation_status"] == "ESCALATED"]
    failed_recs = [s for s in pair_summaries
                   if s["recommendation"] in ("VERIFY_FAILED", "INVESTIGATE",
                                              "INVESTIGATE_FRAME_COUNT",
                                              "BLOCKED_NO_CONTEXT",
                                              "BLOCKED_NO_ANCHORS")]
    nb_mismatch = [s for s in pair_summaries
                   if s["recommendation"] == "INVESTIGATE_FRAME_COUNT"]

    lines: list[str] = []
    lines.append("# At-scale validation of `K_seq = i_old`")
    lines.append("")
    lines.append(f"- Pairs sampled: **{n_pairs}**")
    lines.append(f"- Anchors evaluated: **{totals['n_anchors']}**")
    lines.append(f"- Global PASS_HIGH_CONFIDENCE rate: **{phc_rate:.2%}**")
    lines.append(f"- Global PASS rate (incl. high-conf): **{pass_rate:.2%}**")
    lines.append(f"- Global VERIFY_FAILED rate: **{fail_rate:.2%}**")
    lines.append(f"- Pairs escalated to Phase 2: **{len(escalated)}**")
    lines.append(f"- Pairs with recommendation != OK: **{len(failed_recs)}**")
    lines.append(f"- Wall-clock: **{runtime_seconds/60:.1f} min**")
    lines.append("")
    lines.append("Candidate formula validated:")
    lines.append("")
    lines.append("```")
    lines.append("Step 1: K_seq = min(i_old, idx_frames - 2)")
    lines.append("Step 2: new_frame = clamp(pre_roll + round((idx_ts[K_seq] - idx_ts[0]) * 30),")
    lines.append("                          0, new_nb_frames - 1)")
    lines.append("Step 3: H<=4 PASS_HIGH_CONFIDENCE, 4<H<=16 PASS, H>16 VERIFY_FAILED")
    lines.append("```")
    lines.append("")

    # A.
    lines.append("## A. Is the formula validated at scale?")
    lines.append("")
    if pass_rate >= 0.98 and fail_rate <= 0.005:
        verdict = "**YES** — the candidate clears the promotion thresholds."
    elif pass_rate >= 0.95:
        verdict = "**MOSTLY** — clears the loose bar but has stragglers worth review."
    else:
        verdict = "**NO** — sample exposes systematic failures; see Section C."
    lines.append(verdict)
    lines.append("")
    lines.append(f"Promotion gate (informational, not actioned by this script):")
    lines.append(f"- global_pass_rate >= 0.98? **{pass_rate >= 0.98}** ({pass_rate:.2%})")
    lines.append(f"- global_verify_failed_rate <= 0.005? "
                 f"**{fail_rate <= 0.005}** ({fail_rate:.2%})")
    lines.append(f"- assert_old_nb == idx_frames - 1 universal? "
                 f"**{len(nb_mismatch) == 0}** ({len(nb_mismatch)} mismatches)")
    lines.append("")

    # B.
    lines.append("## B. Per-stratum performance")
    lines.append("")
    lines.extend(_table(by_camera, "By camera"))
    lines.extend(_table(by_fps, "By source_fps bucket"))
    lines.extend(_table(by_preroll, "By pre_roll bucket"))
    lines.extend(_table(by_duration, "By video-duration bucket"))

    # C.
    lines.append("## C. Systematic failures")
    lines.append("")
    if not failed_recs:
        lines.append("None observed. Every pair scored OK or OK_WITH_WARNINGS.")
    else:
        lines.append("| date | case | camera | recommendation | "
                     "n_fail / n | max_H | max|res| |")
        lines.append("|---|---:|---|---|---|---:|---:|")
        for s in failed_recs:
            lines.append(f"| {s['recording_date']} | {s['case_no']} | "
                         f"{s['camera_name']} | {s['recommendation']} | "
                         f"{s['verify_failed_count']} / {s['n_anchors']} | "
                         f"{s['max_hamming']} | {s['max_abs_residual_new']} |")
    lines.append("")

    # D.
    lines.append("## D. Is OLD nb_frames = idx_frames - 1 universal?")
    lines.append("")
    if not nb_mismatch:
        lines.append(f"**Yes.** All {n_pairs} pairs satisfy old_nb_frames == idx_frames - 1.")
    else:
        lines.append(f"**No** — {len(nb_mismatch)} mismatches:")
        for s in nb_mismatch:
            lines.append(f"- {s['recording_date']} Case{s['case_no']} {s['camera_name']}")
    lines.append("")

    # E.
    lines.append("## E. Likely CLExport / fallback / corrupted backups")
    lines.append("")
    clexport_suspects = [s for s in pair_summaries
                         if isinstance(s["max_hamming"], (int, float)) and s["max_hamming"] >= 20]
    if not clexport_suspects:
        lines.append("None detected (no pair has max_hamming >= 20).")
    else:
        lines.append("Pairs with max Hamming >= 20 (CLExport / fallback suspect):")
        for s in clexport_suspects:
            lines.append(f"- {s['recording_date']} Case{s['case_no']} {s['camera_name']} "
                         f"(max_H={s['max_hamming']}, recommendation={s['recommendation']})")
    lines.append("")

    # F.
    lines.append("## F. Production fallback on verification failure")
    lines.append("")
    lines.append("Recommended fallback ladder when the candidate returns H > 16:")
    lines.append("1. Widen NEW-side search to +/- 30 frames; if found, accept and log "
                 "`UNCONFIDENT_PASS`.")
    lines.append("2. Run empirical K search on the SEQ side over [i_old-30, i_old+30] "
                 "via `validate_mapping_formulas.validate_camera`.")
    lines.append("3. If both fail, mark the anchor `UNKNOWN_MAPPING`. Flag the pair "
                 "`LIKELY_CLEXPORT` if >20% of its anchors reach step 3.")
    lines.append("")

    # G.
    lines.append("## G. Safe to default in `align_old_new_mp4.py`?")
    lines.append("")
    if pass_rate >= 0.98 and fail_rate <= 0.005 and not nb_mismatch:
        lines.append("**Yes.** All three promotion gates passed.")
    else:
        lines.append("**Hold.** At least one promotion gate failed:")
        lines.append(f"- pass_rate >= 0.98: {pass_rate >= 0.98}")
        lines.append(f"- fail_rate <= 0.005: {fail_rate <= 0.005}")
        lines.append(f"- old_nb == idx-1 universal: {len(nb_mismatch) == 0}")
    lines.append("")

    # H.
    lines.append("## H. Runtime and regression-test sizing")
    lines.append("")
    avg = (runtime_seconds / n_pairs) if n_pairs else 0.0
    lines.append(f"- Total: {runtime_seconds/60:.1f} min over {n_pairs} pairs "
                 f"(avg {avg:.1f} s/pair).")
    lines.append(f"- For future regression: 15-20 pairs x 10 anchors should cover "
                 f"all major strata in <5 min wall-clock; keep `v2_control` plus one "
                 f"pair from each tail bucket as a smoke-test set.")
    lines.append("")

    # Outliers appendix (truncated).
    if outliers:
        lines.append("## Appendix — outliers (top 30)")
        lines.append("")
        lines.append("| date | case | camera | scope | reason | details |")
        lines.append("|---|---:|---|---|---|---|")
        for o in outliers[:30]:
            lines.append(f"| {o['recording_date']} | {o['case_no']} | "
                         f"{o['camera_name']} | {o['scope']} | "
                         f"{o['reason']} | {o.get('details', '')} |")
        if len(outliers) > 30:
            lines.append(f"| ... | | | | | {len(outliers) - 30} more rows in outliers.csv |")
        lines.append("")

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def _build_args() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=get_db_path())
    ap.add_argument("--old-root", default=DEFAULT_OLD_ROOT)
    ap.add_argument("--new-root", default=get_mp4_root())
    ap.add_argument("--seq-root", default=get_seq_root())
    ap.add_argument("--max-pairs", type=int, default=50)
    ap.add_argument("--anchors-per-pair", type=int, default=10)
    ap.add_argument("--anchors-on-failure", type=int, default=30)
    ap.add_argument("--phase3-pairs", type=int, default=0,
                    help="If >0, run Phase 3 with this many additional pairs.")
    ap.add_argument("--phase3-anchors", type=int, default=12)
    ap.add_argument("--max-runtime-min", type=float, default=45.0)
    ap.add_argument("--stratification-seed", type=int, default=42)
    ap.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    ap.add_argument("--enable-diagnostic-baselines", action="store_true",
                    help="Run diagnostic baselines on every pair (slow). "
                         "Default: only on escalation.")
    ap.add_argument("--no-v2-controls", action="store_true",
                    help="Exclude v2 positive controls from the sample.")
    ap.add_argument("--ffmpeg", default=None)
    ap.add_argument("--ffprobe", default=None)
    ap.add_argument("--dry-run", action="store_true",
                    help="Select and print pairs, then exit.")
    return ap


def _run_phase(
    pairs_with_stratum: list[tuple[str, PairCandidate]],
    args: argparse.Namespace, ffmpeg: str, ffprobe: str,
    deadline: float, phase_label: str,
) -> list[PairResult]:
    """Run the per-pair fast loop, with deadline-aware truncation."""
    results: list[PairResult] = []
    n_total = len(pairs_with_stratum)
    for idx, (stratum, p) in enumerate(pairs_with_stratum, 1):
        if time.perf_counter() > deadline:
            print(f"[{phase_label}] deadline reached; stopping after {idx - 1} / {n_total} pairs.")
            break
        t_pair = time.perf_counter()
        # Build group t_global_start lookup just for this case (cheap).
        group_t_start = _load_group_t_global_for_case(args.db, p.recording_date, p.case_no)
        ctx = _build_context_for_camera(args, p.recording_date, p.case_no,
                                        p.camera_name, group_t_start, ffprobe)
        pr = PairResult(pair=p, stratum=stratum, ctx=ctx)
        if ctx is None:
            pr.ctx_error = "CONTEXT_BUILD_FAILED"
            pr.runtime_seconds = time.perf_counter() - t_pair
            results.append(pr)
            print(f"  [{idx}/{n_total}] {p.recording_date} Case{p.case_no} "
                  f"{p.camera_name} [{stratum}]: CONTEXT FAILED")
            continue
        pr.old_nb_eq_idx_minus_1 = (ctx.old_nb_frames == ctx.idx_frames - 1)
        seed_for_pair = args.stratification_seed ^ (hash(p.key()) & 0xFFFFFFFF)
        anchors_n = (args.phase3_anchors if phase_label == "phase3"
                     else args.anchors_per_pair)
        pr.anchors = validate_pair_fast(ctx, anchors_n, ffmpeg, seed_for_pair)
        pr.runtime_seconds = time.perf_counter() - t_pair
        results.append(pr)
        good = sum(1 for a in pr.anchors if a.get("status"))
        fails = sum(1 for a in pr.anchors if a.get("status") == "VERIFY_FAILED")
        max_h = max((a.get("hamming") for a in pr.anchors
                     if isinstance(a.get("hamming"), (int, float))), default="-")
        print(f"  [{idx}/{n_total}] {p.recording_date} Case{p.case_no} "
              f"{p.camera_name} [{stratum}]: {good} anchors, "
              f"{fails} fail, max_H={max_h}, {pr.runtime_seconds:.1f}s")
    return results


def _load_group_t_global_for_case(db_path: str, rec_date: str, case_no: int) -> dict[str, float]:
    """Return ``{'A': t_min, 'B': t_min, 'SOLO': t_min}`` for one case."""
    out: dict[str, float] = {}
    if not Path(db_path).is_file():
        return out
    with sqlite3.connect(db_path) as conn:
        cur = conn.execute(
            "SELECT camera_name, first_frame_time "
            "FROM   seq_enriched WHERE recording_date=? AND case_no=?",
            (rec_date, case_no),
        )
        for cam, t0 in cur.fetchall():
            try:
                t0f = float(t0)
            except (TypeError, ValueError):
                continue
            if not (_EPOCH_MIN <= t0f <= _EPOCH_MAX):
                continue
            grp = _camera_group(str(cam))
            if grp in ("A", "B"):
                cur_min = out.get(grp)
                if cur_min is None or t0f < cur_min:
                    out[grp] = t0f
            elif grp == "SOLO":
                out[grp] = t0f
    return out


def _escalate_failed_pairs(
    pair_results: list[PairResult], args: argparse.Namespace,
    ffmpeg: str, ffprobe: str, deadline: float,
) -> None:
    """Phase 2: rerun escalation-flagged pairs with full diagnostic block."""
    for pr in pair_results:
        if time.perf_counter() > deadline:
            print("[phase2] deadline reached; skipping further escalations.")
            return
        if pr.ctx is None:
            continue
        # Quick summary to decide.
        s = summarise_pair(pr)
        if not should_escalate(s, pr, args.anchors_per_pair):
            continue
        print(f"  [escalate] {pr.pair.recording_date} Case{pr.pair.case_no} "
              f"{pr.pair.camera_name}: re-running with {args.anchors_on_failure} anchors "
              f"+ baselines")
        t0 = time.perf_counter()
        v2_anchors = validate_pair_diagnostic(pr.ctx, args.anchors_on_failure, ffmpeg)
        normalised = [normalise_diagnostic_anchor(pr.ctx, a) for a in v2_anchors]
        pr.anchors = normalised
        pr.escalated = True
        pr.runtime_seconds += time.perf_counter() - t0


def main(argv: list[str] | None = None) -> int:
    args = _build_args().parse_args(argv)

    ffmpeg = args.ffmpeg or _resolve_tool("ffmpeg")
    ffprobe = args.ffprobe or _resolve_tool("ffprobe")
    if not ffmpeg or not ffprobe:
        print("ERROR: ffmpeg/ffprobe not found on PATH or known install dirs.")
        return 2

    out_dir = Path(args.out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"Loading seq_enriched from {args.db} ...")
    pairs = query_candidate_pairs(args.db)
    print(f"  found {len(pairs)} candidate rows")

    annotate_pre_roll(pairs)
    old_root = Path(args.old_root).resolve()
    new_root = Path(args.new_root).resolve()
    pairs = filter_existing_files(pairs, old_root, new_root)
    print(f"  {len(pairs)} pairs have both OLD and NEW MP4 files")

    selected = stratified_sample(
        pairs, args.max_pairs, args.stratification_seed,
        include_v2_controls=not args.no_v2_controls,
    )
    print(f"  selected {len(selected)} pairs across strata:")
    strat_counts = Counter(s for s, _ in selected)
    for k, v in strat_counts.items():
        print(f"    {k}: {v}")

    if args.dry_run:
        for stratum, p in selected:
            print(f"  [{stratum}] {p.recording_date} Case{p.case_no} {p.camera_name} "
                  f"src_fps={p.source_fps:.2f} pre_roll={p.pre_roll_frames} "
                  f"dur_h={p.duration_h:.2f}")
        return 0

    overall_start = time.perf_counter()
    deadline = overall_start + args.max_runtime_min * 60.0

    print(f"\n=== Phase 1: {len(selected)} pairs x {args.anchors_per_pair} anchors ===")
    pair_results = _run_phase(selected, args, ffmpeg, ffprobe, deadline, "phase1")

    # Write Phase-1-only artifacts now so we keep them even if Phase 2 is
    # killed or overruns. Phase 2 will overwrite them at the end.
    print(f"\nWriting Phase-1 artifacts (will overwrite after Phase 2) ...")
    _emit_artifacts(out_dir, pair_results, time.perf_counter() - overall_start, args,
                    suffix="_phase1")

    print(f"\n=== Phase 2: escalate failures ===")
    _escalate_failed_pairs(pair_results, args, ffmpeg, ffprobe, deadline)

    if args.phase3_pairs > 0 and time.perf_counter() < deadline:
        print(f"\n=== Phase 3: {args.phase3_pairs} additional pairs x {args.phase3_anchors} anchors ===")
        already = {pr.key for pr in pair_results}
        remaining = [p for p in pairs if p.key() not in already]
        rng = random.Random(args.stratification_seed + 1)
        rng.shuffle(remaining)
        phase3_sample = [("phase3_random", p) for p in remaining[: args.phase3_pairs]]
        phase3_results = _run_phase(phase3_sample, args, ffmpeg, ffprobe,
                                    deadline, "phase3")
        pair_results.extend(phase3_results)

    runtime = time.perf_counter() - overall_start
    totals = _emit_artifacts(out_dir, pair_results, runtime, args, suffix="")
    print(f"\nTotal runtime: {runtime/60:.1f} min")
    print(f"Global PASS rate: {totals['pass_rate']:.2%}  "
          f"PASS_HIGH_CONFIDENCE: {totals['pass_high_confidence_rate']:.2%}  "
          f"VERIFY_FAILED: {totals['verify_failed_rate']:.2%}")
    return 0


def _emit_artifacts(
    out_dir: Path, pair_results: list[PairResult], runtime: float,
    args: argparse.Namespace, suffix: str = "",
) -> dict:
    """Write the 4 CSVs + markdown report from current ``pair_results``.

    Used twice: once after Phase 1 (with ``suffix='_phase1'``) so partial
    results survive a Phase-2 kill, then again at the very end overwriting
    the canonical names.
    """
    all_anchor_rows: list[dict] = []
    pair_summaries: list[dict] = []
    for pr in pair_results:
        phase = "phase2" if pr.escalated else "phase1"
        for a in pr.anchors:
            all_anchor_rows.append(_flatten_anchor_row(pr, a, phase))
        pair_summaries.append(summarise_pair(pr))

    print(f"  writing artifacts to {out_dir} (suffix='{suffix}') ...")
    anchor_path = out_dir / f"per_anchor_results{suffix}.csv"
    summary_path = out_dir / f"per_pair_summary{suffix}.csv"
    global_path = out_dir / f"global_summary{suffix}.csv"
    outlier_path = out_dir / f"outliers{suffix}.csv"
    report_path = out_dir / f"final_validation_report{suffix}.md"

    write_csv(all_anchor_rows, _ANCHOR_COLS, anchor_path)
    write_csv(pair_summaries, _SUMMARY_COLS, summary_path)
    totals = write_global_summary(global_path, all_anchor_rows, pair_summaries, runtime)
    outliers = collect_outliers(pair_results, all_anchor_rows)
    write_csv(outliers, _OUTLIER_COLS, outlier_path)
    write_markdown_report(report_path, pair_summaries, totals, runtime, args,
                          outliers, all_anchor_rows)
    print(f"    per_anchor_results{suffix}.csv : {len(all_anchor_rows)} rows")
    print(f"    per_pair_summary{suffix}.csv   : {len(pair_summaries)} rows")
    print(f"    outliers{suffix}.csv           : {len(outliers)} rows")
    return totals


if __name__ == "__main__":
    raise SystemExit(main())
