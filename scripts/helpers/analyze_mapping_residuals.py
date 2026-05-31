"""Analyze per-anchor residual time-series from validate_mapping_formulas.py.

Takes one or more JSON dumps produced by
``validate_mapping_formulas.py --out-json ...`` and answers focused
discovery questions about the OLD MP4 -> SEQ -> NEW MP4 mapping:

* Hamming distribution per hypothesis, binned to
  PASS_HIGH_CONFIDENCE (H<=4) / PASS (4<H<=16) / VERIFY_FAILED (H>16).
* residual_K linear fit (slope, intercept, R^2): tells fps-mismatch
  from constant-offset.
* residual_K jump count: tells segment-level discontinuities.
* Edge-anchor failure rates (first/middle/last quintile): tells
  padding/trimming.
* A 6-bit multi-criteria score per hypothesis, per the user's
  decision rule.

No matplotlib dependency. Pure stdlib + numpy. Read-only on disk.
"""

from __future__ import annotations

import argparse
import io
import json
import sys
from pathlib import Path

import numpy as np


if hasattr(sys.stdout, "buffer") and (sys.stdout.encoding or "").lower() not in ("utf-8", "utf8"):
    sys.stdout = io.TextIOWrapper(
        sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True
    )


_HIGH = 4
_PASS = 16


def _bin_hamming(h: float) -> str:
    if h <= _HIGH:
        return "PASS_HIGH"
    if h <= _PASS:
        return "PASS"
    return "VERIFY_FAILED"


def _per_hypothesis_arrays(anchors: list[dict], name: str):
    """Extract (i_old, residual_K, min_hamming) arrays for one hypothesis."""
    iolds, resids, hams = [], [], []
    for anc in anchors:
        pf = (anc.get("per_formula") or {}).get(name)
        if not pf or pf.get("out_of_range"):
            continue
        h = pf.get("min_hamming_local")
        r = pf.get("residual_K")
        if h is None or r is None:
            continue
        iolds.append(anc["i_old"])
        resids.append(int(r))
        hams.append(int(h))
    return (np.array(iolds, dtype=np.int64),
            np.array(resids, dtype=np.int64),
            np.array(hams, dtype=np.int64))


def _linear_fit(x: np.ndarray, y: np.ndarray) -> tuple[float, float, float]:
    if x.size < 2:
        return float("nan"), float("nan"), float("nan")
    a, b = np.polyfit(x, y, 1)
    yhat = a * x + b
    ss_res = np.sum((y - yhat) ** 2)
    ss_tot = np.sum((y - np.mean(y)) ** 2)
    r2 = 1.0 - (ss_res / ss_tot) if ss_tot > 0 else 1.0
    return float(a), float(b), float(r2)


def _jump_count(iolds: np.ndarray, resids: np.ndarray, thr: int = 5) -> int:
    if iolds.size < 2:
        return 0
    order = np.argsort(iolds)
    rs = resids[order]
    diffs = np.abs(np.diff(rs))
    return int(np.sum(diffs > thr))


def _edge_fail_rates(iolds: np.ndarray, hams: np.ndarray) -> dict:
    """Hamming fail-rate (H > _PASS) split first 20% / middle / last 20%."""
    if iolds.size == 0:
        return {"first": 0.0, "middle": 0.0, "last": 0.0, "n_first": 0, "n_middle": 0, "n_last": 0}
    order = np.argsort(iolds)
    hs = hams[order]
    n = hs.size
    n1 = max(1, n // 5)
    n3 = max(1, n // 5)
    first = hs[:n1]
    last = hs[-n3:]
    middle = hs[n1:n - n3] if n - n3 > n1 else hs[n1:n]
    def fr(arr):
        return float(np.mean(arr > _PASS)) if arr.size else 0.0
    return {
        "first": fr(first), "middle": fr(middle), "last": fr(last),
        "n_first": int(first.size), "n_middle": int(middle.size), "n_last": int(last.size),
    }


def _criteria_score(stats: dict, idx_frames: int) -> tuple[int, list[bool]]:
    """6-bit score per the user's checklist."""
    end_drift = abs(stats["slope"]) * idx_frames if not np.isnan(stats["slope"]) else 1e18
    bits = [
        stats["mean_H"] <= 12,                                # 1
        stats["median_H"] <= 8,                               # 2
        abs(stats["median_residual"]) <= 2,                   # 3
        end_drift <= 5,                                       # 4
        stats["jumps"] == 0,                                  # 5
        stats["edge_fail"]["first"] <= stats["edge_fail"]["middle"] * 1.5 + 1e-9
            and stats["edge_fail"]["last"] <= stats["edge_fail"]["middle"] * 1.5 + 1e-9,  # 6
    ]
    return sum(bits), bits


def _camera_block(cam: dict) -> dict:
    """Compute all per-hypothesis stats for one camera entry."""
    anchors = cam.get("anchors") or []
    if not anchors:
        return {"meta": cam, "hypotheses": {}}
    names: list[str] = []
    seen: set = set()
    for anc in anchors:
        for k in (anc.get("per_formula") or {}).keys():
            if k not in seen:
                seen.add(k)
                names.append(k)

    out = {}
    idx_frames = int(cam.get("idx_frames") or 1)
    for name in names:
        iolds, resids, hams = _per_hypothesis_arrays(anchors, name)
        if iolds.size == 0:
            out[name] = {"n": 0}
            continue
        slope, intercept, r2 = _linear_fit(iolds, resids.astype(np.float64))
        bins = [_bin_hamming(int(h)) for h in hams]
        n_pass_high = bins.count("PASS_HIGH")
        n_pass = bins.count("PASS")
        n_fail = bins.count("VERIFY_FAILED")
        stats = {
            "n": int(hams.size),
            "n_pass_high": n_pass_high,
            "n_pass": n_pass,
            "n_fail": n_fail,
            "mean_H": float(np.mean(hams)),
            "median_H": float(np.median(hams)),
            "max_H": float(np.max(hams)),
            "median_residual": int(np.median(resids)),
            "max_abs_residual": int(np.max(np.abs(resids))),
            "slope": slope,
            "intercept": intercept,
            "r2": r2,
            "end_drift": slope * idx_frames if not np.isnan(slope) else float("nan"),
            "jumps": _jump_count(iolds, resids),
            "edge_fail": _edge_fail_rates(iolds, hams),
        }
        score, bits = _criteria_score(stats, idx_frames)
        stats["score"] = score
        stats["criteria_bits"] = bits
        out[name] = stats
    return {"meta": cam, "hypotheses": out}


def _print_camera_table(camera_block: dict) -> None:
    cam = camera_block["meta"]
    print("=" * 110)
    print(f"Camera: {cam.get('camera','?')}  "
          f"({cam.get('recording_date','?')} Case{cam.get('case_no','?')}, group {cam.get('group','?')})")
    print(f"  pre_roll={cam.get('pre_roll')}  source_fps={cam.get('source_fps'):.4f}  "
          f"idx_frames={cam.get('idx_frames')}  "
          f"OLD={cam.get('old_nb_frames')}@{cam.get('old_fps'):.4f}  "
          f"NEW={cam.get('new_nb_frames')}@{cam.get('new_fps'):.4f}")
    eh = cam.get("empirical_health") or {}
    if eh.get("n"):
        print(f"  empirical: n={eh['n']}  meanH={eh.get('mean',0):.2f}  "
              f"medH={eh.get('median',0):.2f}  maxH={eh.get('max',0):.2f}")
    lab = cam.get("learned_ab")
    if lab:
        print(f"  g_linear fit: K = {lab[0]:.6f}*i_old + {lab[1]:+.3f}")
    print(f"  clexport_flag: {cam.get('clexport_flag')}")
    print()
    print(f"  {'hypothesis':>22s}  {'n':>3} {'HI':>3} {'PS':>3} {'FL':>3}  "
          f"{'meanH':>6} {'medH':>5} {'maxH':>5}  "
          f"{'medRes':>6} {'maxAbsRes':>9}  "
          f"{'slope':>10} {'endDr':>8} {'R^2':>6}  {'jmps':>4}  "
          f"{'edgeF/M/L':>13}  {'score':>5}")
    hyps = camera_block["hypotheses"]
    for name in sorted(hyps, key=lambda k: (-hyps[k].get("score", -1) if hyps[k].get("n") else -99, k)):
        s = hyps[name]
        if not s.get("n"):
            print(f"  {name:>22s}    -")
            continue
        edge = s["edge_fail"]
        ef = f"{edge['first']:.2f}/{edge['middle']:.2f}/{edge['last']:.2f}"
        print(f"  {name:>22s}  {s['n']:>3d} {s['n_pass_high']:>3d} "
              f"{s['n_pass']:>3d} {s['n_fail']:>3d}  "
              f"{s['mean_H']:>6.2f} {s['median_H']:>5.1f} {s['max_H']:>5.0f}  "
              f"{s['median_residual']:>+6d} {s['max_abs_residual']:>9d}  "
              f"{s['slope']:>10.2e} {s['end_drift']:>+8.1f} {s['r2']:>6.3f}  "
              f"{s['jumps']:>4d}  {ef:>13s}  {s['score']:>2d}/6")
    print()


def _print_cross_summary(blocks: list[dict]) -> None:
    print()
    print("=" * 110)
    print("CROSS-PAIR SUMMARY")
    print("=" * 110)
    # Collect per-hypothesis stats across all (case, camera) blocks.
    rows: dict[str, list[dict]] = {}
    for blk in blocks:
        for name, s in blk["hypotheses"].items():
            if s.get("n"):
                rows.setdefault(name, []).append({"cam": blk["meta"].get("camera"), **s})
    # Sort hypotheses by total cross-pair score.
    ranked = sorted(
        rows.items(),
        key=lambda kv: -sum(s["score"] for s in kv[1]),
    )
    print(f"  {'hypothesis':>22s}  {'pairs':>5}  {'sum_score':>9}  "
          f"{'mean_meanH':>10}  {'med_endDr':>9}  {'sum_jumps':>9}  {'n_FAIL_pairs':>12}")
    for name, lst in ranked:
        n_pairs = len(lst)
        sum_score = sum(s["score"] for s in lst)
        means = np.array([s["mean_H"] for s in lst])
        eds = np.array([abs(s["end_drift"]) for s in lst if not np.isnan(s["end_drift"])])
        jumps = sum(s["jumps"] for s in lst)
        n_fail_pairs = sum(1 for s in lst if s["n_fail"] > s["n"] * 0.2)
        print(f"  {name:>22s}  {n_pairs:>5d}  {sum_score:>9d}  "
              f"{float(np.mean(means)):>10.2f}  "
              f"{(float(np.median(eds)) if eds.size else float('nan')):>9.2f}  "
              f"{jumps:>9d}  {n_fail_pairs:>12d}")
    print()
    print("Criteria bits (1=pass, 0=fail) per hypothesis per (case, camera):")
    print("  bit1: mean H <= 12     bit4: |slope*idx_frames| <= 5 frames")
    print("  bit2: median H <= 8    bit5: jumps == 0")
    print("  bit3: |medRes| <= 2    bit6: edge fail <= middle * 1.5")
    print()
    print(f"  {'hypothesis':>22s} | " + " | ".join(f"{blk['meta'].get('camera','?')[:14]:>14}" for blk in blocks))
    for name, _ in ranked:
        line = f"  {name:>22s} | "
        cells = []
        for blk in blocks:
            s = blk["hypotheses"].get(name) or {}
            if not s.get("n"):
                cells.append(f"{'-':>14}")
                continue
            bits = "".join("1" if b else "0" for b in s["criteria_bits"])
            cells.append(f"{bits:>10}  {s['score']:>1d}/6")
        print(line + " | ".join(cells))
    print()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=("Analyze per-anchor residual time series from "
                     "validate_mapping_formulas.py JSON dumps."),
    )
    ap.add_argument("json_paths", nargs="+", help="One or more JSON dump paths.")
    args = ap.parse_args(argv)

    blocks: list[dict] = []
    for jp in args.json_paths:
        path = Path(jp).resolve()
        if not path.is_file():
            print(f"[skip] {path}: not a file", file=sys.stderr)
            continue
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        for cam in data.get("per_camera", []):
            blk = _camera_block(cam)
            blocks.append(blk)
            _print_camera_table(blk)

    if len(blocks) >= 1:
        _print_cross_summary(blocks)
    return 0


if __name__ == "__main__":
    sys.exit(main())
