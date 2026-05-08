"""Recording Quality dashboard — frame drops, time drift, silent stalls.

Sources: ``seq_enriched`` (per-file frame analysis from analyze_seq_fields.py).
"""

from __future__ import annotations

import math

import pandas as pd
from nicegui import ui

from app import state
from app.charts import (
    base_grid,
    base_tooltip,
    chart_palette,
    echart_axis_color,
    empty_state,
    kpi_card,
    query_df,
)
from app.layout import page_frame


CAMERAS = [
    "Cart_Center_2", "Cart_LT_4", "Cart_RT_1", "General_3",
    "Monitor", "Patient_Monitor", "Ventilator_Monitor", "Injection_Port",
]
DRIFT_LIMIT_MS = 10_000        # ±10 s window for the scatter
SANE_DRIFT_MS  = 5_000          # rows outside ±5 s count as "drift outliers"


def _drop_per_camera(seq: pd.DataFrame) -> None:
    cam = (
        seq.dropna(subset=["drop_rate"])
        .groupby("camera_name")["drop_rate"]
        .mean().mul(100).round(2).reset_index()
        .sort_values("drop_rate", ascending=True)
    )
    if cam.empty:
        empty_state("No drop-rate data available.")
        return
    axis = echart_axis_color()
    ui.echart({
        "tooltip": base_tooltip("axis") | {"formatter": "{b}: {c}%"},
        "grid": base_grid(left=160, right=40, top=10, bottom=30),
        "xAxis": {"type": "value", "axisLabel": {"formatter": "{value}%", "color": axis}},
        "yAxis": {"type": "category", "data": cam["camera_name"].tolist(),
                  "axisLabel": {"color": axis}},
        "series": [{
            "type": "bar", "data": cam["drop_rate"].tolist(),
            "itemStyle": {
                "color": {
                    "type": "linear", "x": 0, "y": 0, "x2": 1, "y2": 0,
                    "colorStops": [
                        {"offset": 0, "color": "#10B981"},
                        {"offset": 1, "color": "#F59E0B"},
                    ],
                },
                "borderRadius": [0, 4, 4, 0],
            },
            "label": {"show": True, "position": "right", "formatter": "{c}%",
                      "color": axis},
        }],
    }).style("height: 320px;")


def _drop_distribution(seq: pd.DataFrame) -> None:
    rates = seq["drop_rate"].dropna() * 100
    if rates.empty:
        empty_state("No drop-rate data available.")
        return
    bins = [0, 0.01, 0.1, 0.5, 1, 2, 5, 10, 100]
    labels = ["0", "≤0.01%", "≤0.1%", "≤0.5%", "≤1%", "≤2%", "≤5%", "≤10%", ">10%"]
    counts = [int((rates == 0).sum())]
    for lo, hi in zip(bins[:-1], bins[1:]):
        if lo == 0:  # already counted exact-zero bucket
            counts.append(int(((rates > 0) & (rates <= hi)).sum()))
        else:
            counts.append(int(((rates > lo) & (rates <= hi)).sum()))
    counts.append(int((rates > bins[-1]).sum()))

    axis = echart_axis_color()
    palette = chart_palette()
    ui.echart({
        "tooltip": base_tooltip("axis"),
        "grid": base_grid(left=50, right=20, top=20, bottom=30),
        "xAxis": {"type": "category", "data": labels,
                  "axisLabel": {"color": axis}},
        "yAxis": {"type": "value", "axisLabel": {"color": axis}},
        "series": [{
            "type": "bar", "data": counts,
            "itemStyle": {"color": palette[0], "borderRadius": [4, 4, 0, 0]},
            "label": {"show": True, "position": "top", "color": axis},
        }],
    }).style("height: 280px;")


def _drift_scatter(seq: pd.DataFrame) -> None:
    df = seq.dropna(subset=["time_drift_ms", "recording_date"]).copy()
    df["date"] = pd.to_datetime(df["recording_date"], errors="coerce")
    df = df.dropna(subset=["date"])
    if df.empty:
        empty_state("No time-drift data available.")
        return

    in_range = df[df["time_drift_ms"].abs() <= DRIFT_LIMIT_MS]
    excluded = len(df) - len(in_range)

    palette = chart_palette()
    cam_color = {c: palette[i % len(palette)] for i, c in enumerate(CAMERAS)}

    series = []
    for cam, sub in in_range.groupby("camera_name"):
        series.append({
            "name": cam, "type": "scatter",
            "data": [[r.recording_date, float(r.time_drift_ms)] for r in sub.itertuples()],
            "itemStyle": {"color": cam_color.get(cam, "#64748b"), "opacity": 0.65},
            "symbolSize": 6,
        })

    axis = echart_axis_color()
    ui.echart({
        "tooltip": {"trigger": "item",
                    "formatter": "{a}<br/>{c}"},
        "legend": {"top": 0, "type": "scroll", "textStyle": {"color": axis}},
        "grid": base_grid(left=70, right=30, top=40, bottom=60),
        "xAxis": {"type": "category",
                  "axisLabel": {"rotate": 45, "color": axis}},
        "yAxis": {"type": "value", "name": "drift (ms)",
                  "min": -DRIFT_LIMIT_MS, "max": DRIFT_LIMIT_MS,
                  "axisLabel": {"color": axis}},
        "series": series,
        "graphic": [{
            "type": "text", "right": 20, "top": 8,
            "style": {
                "text": f"Excluded {excluded} rows outside ±{DRIFT_LIMIT_MS // 1000}s "
                        f"(corrupt timestamps).",
                "fill": "#64748b", "fontSize": 11,
            },
        }] if excluded else [],
    }).style("height: 360px;")


def _gap_scoreboard(seq: pd.DataFrame) -> None:
    df = seq.dropna(subset=["max_time_gap_ms"]).copy()
    if df.empty:
        empty_state("No max-gap data available.")
        return
    df = df.sort_values("max_time_gap_ms", ascending=False).head(10)
    rows = [{
        "recording_date": r.recording_date,
        "case_no": int(r.case_no) if pd.notna(r.case_no) else None,
        "camera_name": r.camera_name,
        "max_gap_ms": round(float(r.max_time_gap_ms), 1),
        "drop_rate_%": round(float(r.drop_rate or 0) * 100, 2),
    } for r in df.itertuples()]
    ui.aggrid({
        "defaultColDef": {"sortable": True, "filter": True, "resizable": True},
        "columnDefs": [
            {"field": "recording_date", "headerName": "Date"},
            {"field": "case_no",        "headerName": "Case"},
            {"field": "camera_name",    "headerName": "Camera"},
            {"field": "max_gap_ms",     "headerName": "Max gap (ms)"},
            {"field": "drop_rate_%",    "headerName": "Drop %"},
        ],
        "rowData": rows,
    }).classes("ag-theme-balham w-full").style("height: 380px")


def _resolution_donut(seq: pd.DataFrame) -> None:
    df = seq.dropna(subset=["width", "height", "fps"]).copy()
    if df.empty:
        empty_state("No resolution data available.")
        return
    df["combo"] = df.apply(
        lambda r: f"{int(r.width)}×{int(r.height)} @ {round(float(r.fps), 1)}fps", axis=1
    )
    counts = df["combo"].value_counts().reset_index()
    counts.columns = ["combo", "n"]
    palette = chart_palette()
    axis = echart_axis_color()
    data = [{"value": int(r.n), "name": r.combo,
             "itemStyle": {"color": palette[i % len(palette)]}}
            for i, r in enumerate(counts.itertuples())]
    ui.echart({
        "tooltip": {"trigger": "item", "formatter": "{b}: {c} ({d}%)"},
        "legend": {"orient": "vertical", "left": "left", "type": "scroll",
                   "textStyle": {"color": axis}},
        "series": [{
            "type": "pie", "radius": ["45%", "75%"],
            "data": data,
            "label": {"color": axis},
        }],
    }).style("height: 320px;")


@ui.page("/quality")
def quality_page() -> None:
    with page_frame("Quality"):
        db_path = state.get()
        ui.label("Recording Quality Dashboard").classes("section-h text-h5 text-weight-medium")
        ui.label("Frame drops, timing drift, and silent stalls (from seq_enriched).") \
            .classes("text-caption muted")

        seq = query_df(
            db_path,
            "SELECT recording_date, case_no, camera_name, drop_rate, time_drift_ms, "
            "       max_time_gap_ms, width, height, fps "
            "FROM seq_enriched",
        )

        if seq.empty:
            ui.label("No rows in seq_enriched. Run the SEQ analyzer first.") \
                .classes("text-warning")
            return

        files_n = len(seq)
        valid_drop = seq["drop_rate"].dropna()
        mean_drop = round(valid_drop.mean() * 100, 2) if not valid_drop.empty else None
        high_drop = int((valid_drop > 0.01).sum())  # >1%
        drift_outliers = int(seq["time_drift_ms"].abs().gt(SANE_DRIFT_MS).sum())

        # ── KPIs ────────────────────────────────────────────────────────────
        with ui.row().classes("w-full no-wrap gap-4"):
            kpi_card("FILES ANALYZED", f"{files_n:,}", "rows in seq_enriched")
            kpi_card("MEAN DROP RATE",
                     f"{mean_drop:.2f}%" if mean_drop is not None else "—",
                     "across all files")
            kpi_card("HIGH-DROP FILES", f"{high_drop:,}", ">1% drop rate")
            kpi_card("DRIFT OUTLIERS", f"{drift_outliers:,}",
                     f"|drift| > {SANE_DRIFT_MS // 1000}s")

        # ── Drop rate per camera + distribution ─────────────────────────────
        with ui.row().classes("w-full no-wrap gap-4 items-stretch"):
            with ui.card().classes("surface-1 q-pa-md flex-grow"):
                ui.label("Drop rate per camera").classes("text-subtitle1 text-weight-medium")
                ui.label("Mean drop rate, lower is better.").classes("text-caption muted")
                _drop_per_camera(seq)
            with ui.card().classes("surface-1 q-pa-md flex-grow"):
                ui.label("Drop-rate distribution").classes("text-subtitle1 text-weight-medium")
                ui.label("File counts by drop bucket.").classes("text-caption muted")
                _drop_distribution(seq)

        # ── Time-drift scatter ──────────────────────────────────────────────
        with ui.card().classes("surface-1 w-full q-pa-md"):
            ui.label("Time-drift over time").classes("text-subtitle1 text-weight-medium")
            ui.label(f"Per-file drift (clipped to ±{DRIFT_LIMIT_MS // 1000}s — "
                     "corrupt timestamps excluded).").classes("text-caption muted")
            _drift_scatter(seq)

        # ── Max-gap top 10 + resolution donut ───────────────────────────────
        with ui.row().classes("w-full no-wrap gap-4 items-stretch"):
            with ui.card().classes("surface-1 q-pa-md flex-grow"):
                ui.label("Top silent stalls").classes("text-subtitle1 text-weight-medium")
                ui.label("Largest max_time_gap_ms — possible camera freezes.") \
                    .classes("text-caption muted")
                _gap_scoreboard(seq)
            with ui.card().classes("surface-1 q-pa-md").style("min-width: 380px;"):
                ui.label("Resolution × fps mix").classes("text-subtitle1 text-weight-medium")
                ui.label("Distinct (width × height @ fps) buckets.") \
                    .classes("text-caption muted")
                _resolution_donut(seq)
