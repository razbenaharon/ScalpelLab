"""Advanced SEQ analysis — header fields and IDX integrity.

Deeper view of the per-file metrics produced by analyze_seq_fields.py
(stored in the ``seq_enriched`` table). Surfaces dimensions that are
not on the Quality page: header validity, frame size / fps / compression
mix, IDX integrity flags, drop-rate vs file-size relationship, and a
per-file drill-down grid.

Organized into tabs: Overview, Drops, Timing, Sync, Capture, Worst-N.
A shared JUNK toggle and date-range filter apply to every chart.
"""

from __future__ import annotations

from datetime import datetime

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
DRIFT_LIMIT_MS = 10_000        # ±10 s window for the histogram / trend
SANE_DRIFT_MS  = 5_000         # rows outside ±5 s are flagged as drift outliers
JITTER_CLIP_MS = 2_000         # cap on jitter heatmap color scale
GAP_CLIP_MS    = 5_000         # cap on drops-vs-jitter scatter Y axis


def _is_junk(name: str) -> bool:
    return isinstance(name, str) and (name.endswith("_JUNK") or name.endswith("_Junk"))


# ---------------------------------------------------------------------------
# Existing charts (Overview tab)
# ---------------------------------------------------------------------------

def _header_summary(df: pd.DataFrame) -> None:
    if df.empty:
        empty_state("No header data.")
        return
    summary = (
        df.assign(resolution=df["width"].astype("Int64").astype(str)
                                + " × "
                                + df["height"].astype("Int64").astype(str))
        .groupby(["resolution", "fps", "compression_fmt"], dropna=False)
        .size().reset_index(name="files")
        .sort_values("files", ascending=False)
    )
    ui.aggrid({
        "defaultColDef": {"sortable": True, "filter": True, "resizable": True},
        "columnDefs": [
            {"field": "resolution",       "headerName": "Resolution"},
            {"field": "fps",              "headerName": "FPS"},
            {"field": "compression_fmt",  "headerName": "Compression"},
            {"field": "files",            "headerName": "Files",
             "type": "numericColumn"},
        ],
        "rowData": summary.to_dict("records"),
        "pagination": True, "paginationPageSize": 10,
    }).classes("ag-theme-balham w-full").style("height: 280px;")


def _drop_vs_size(df: pd.DataFrame) -> None:
    sub = df.dropna(subset=["size_mb", "drop_rate"]).copy()
    if sub.empty:
        empty_state("No drop_rate / size_mb data.")
        return
    sub["drop_pct"] = (sub["drop_rate"] * 100).round(3)
    palette = chart_palette()
    axis = echart_axis_color()
    cams = sorted(sub["camera_name"].dropna().unique().tolist())
    series = []
    for i, cam in enumerate(cams):
        rows = sub[sub["camera_name"] == cam]
        series.append({
            "name": cam,
            "type": "scatter",
            "symbolSize": 8,
            "data": [[float(r.size_mb), float(r.drop_pct)] for r in rows.itertuples()],
            "itemStyle": {"color": palette[i % len(palette)], "opacity": 0.75},
        })
    ui.echart({
        "tooltip": {"trigger": "item",
                    "formatter": "{a}<br/>{c0} MB &rarr; {c1}% drop"},
        "legend": {"data": cams, "top": 0, "type": "scroll",
                   "textStyle": {"color": axis}},
        "grid": base_grid(left=50, right=20, top=40, bottom=50),
        "xAxis": {"type": "value", "name": "Size (MB)",
                  "nameTextStyle": {"color": axis},
                  "axisLabel": {"color": axis}},
        "yAxis": {"type": "value", "name": "Drop rate (%)",
                  "nameTextStyle": {"color": axis},
                  "axisLabel": {"formatter": "{value}%", "color": axis}},
        "series": series,
    }).style("height: 360px;")


def _drift_histogram(df: pd.DataFrame) -> None:
    sub = df.dropna(subset=["time_drift_ms"]).copy()
    sub = sub[sub["time_drift_ms"].between(-DRIFT_LIMIT_MS, DRIFT_LIMIT_MS)]
    if sub.empty:
        empty_state("No in-range drift data.")
        return
    bins = list(range(-DRIFT_LIMIT_MS, DRIFT_LIMIT_MS + 1, 500))
    counts, edges = pd.cut(
        sub["time_drift_ms"], bins=bins, include_lowest=True, right=False
    ).value_counts().sort_index(), bins
    cats = [f"{int(b/1000):+d}s" if b % 1000 == 0 else f"{b}ms"
            for b in edges[:-1]]
    axis = echart_axis_color()
    palette = chart_palette()
    ui.echart({
        "tooltip": base_tooltip("axis"),
        "grid": base_grid(left=50, right=20, top=20, bottom=50),
        "xAxis": {"type": "category", "data": cats,
                  "axisLabel": {"rotate": 45, "color": axis,
                                "interval": max(1, len(cats) // 12)}},
        "yAxis": {"type": "value", "name": "Files",
                  "nameTextStyle": {"color": axis},
                  "axisLabel": {"color": axis}},
        "series": [{
            "type": "bar",
            "data": [int(c) for c in counts.values],
            "itemStyle": {"color": palette[2], "borderRadius": [4, 4, 0, 0]},
        }],
    }).style("height: 320px;")


def _counter_resets_per_camera(df: pd.DataFrame) -> None:
    sub = df.dropna(subset=["n_counter_resets"])
    if sub.empty:
        empty_state("No counter-reset data.")
        return
    cam = (
        sub.groupby("camera_name")["n_counter_resets"]
        .mean().round(2).reset_index()
        .sort_values("n_counter_resets", ascending=True)
    )
    axis = echart_axis_color()
    palette = chart_palette()
    ui.echart({
        "tooltip": base_tooltip("axis"),
        "grid": base_grid(left=160, right=40, top=10, bottom=30),
        "xAxis": {"type": "value", "axisLabel": {"color": axis}},
        "yAxis": {"type": "category",
                  "data": cam["camera_name"].tolist(),
                  "axisLabel": {"color": axis}},
        "series": [{
            "type": "bar",
            "data": cam["n_counter_resets"].tolist(),
            "itemStyle": {"color": palette[5], "borderRadius": [0, 4, 4, 0]},
            "label": {"show": True, "position": "right", "color": axis},
        }],
    }).style("height: 320px;")


def _integrity_grid(df: pd.DataFrame) -> None:
    flagged = df[
        (df.get("has_idx", 1) == 0)
        | (df.get("header_ok", 1) == 0)
        | (df["time_drift_ms"].abs() > SANE_DRIFT_MS)
    ].copy() if "time_drift_ms" in df.columns else df.iloc[0:0]

    if flagged.empty:
        ui.label("No IDX integrity issues detected.").classes("text-positive")
        return

    cols = [c for c in [
        "recording_date", "case_no", "camera_name",
        "has_idx", "header_ok", "time_drift_ms", "drop_rate",
    ] if c in flagged.columns]
    out = flagged[cols].copy()
    if "time_drift_ms" in out.columns:
        out["time_drift_ms"] = out["time_drift_ms"].round(1)
    if "drop_rate" in out.columns:
        out["drop_rate"] = (out["drop_rate"] * 100).round(2)

    ui.aggrid({
        "defaultColDef": {"sortable": True, "filter": True, "resizable": True,
                          "floatingFilter": True},
        "columnDefs": [
            {"field": "recording_date", "headerName": "Date"},
            {"field": "case_no",        "headerName": "Case"},
            {"field": "camera_name",    "headerName": "Camera"},
            {"field": "has_idx",        "headerName": "Has IDX"},
            {"field": "header_ok",      "headerName": "Header OK"},
            {"field": "time_drift_ms",  "headerName": "Drift (ms)",
             "type": "numericColumn"},
            {"field": "drop_rate",      "headerName": "Drop %",
             "type": "numericColumn"},
        ],
        "rowData": out.sort_values(
            ["recording_date", "case_no", "camera_name"],
            ascending=[False, True, True],
        ).to_dict("records"),
        "pagination": True, "paginationPageSize": 15,
    }).classes("ag-theme-balham w-full").style("height: 360px;")


def _file_grid(df: pd.DataFrame) -> None:
    if df.empty:
        empty_state("No rows.")
        return
    keep = [c for c in [
        "recording_date", "case_no", "camera_name",
        "size_mb", "width", "height", "fps", "compression_fmt",
        "allocated_frames", "idx_frames", "dropped_frames", "drop_rate",
        "n_counter_resets", "n_duplicates",
        "actual_duration", "expected_duration",
        "time_drift_ms", "max_time_gap_ms",
        "has_idx", "header_ok",
    ] if c in df.columns]
    out = df[keep].copy()
    if "drop_rate" in out.columns:
        out["drop_rate"] = (out["drop_rate"] * 100).round(2)
    if "time_drift_ms" in out.columns:
        out["time_drift_ms"] = out["time_drift_ms"].round(1)
    if "max_time_gap_ms" in out.columns:
        out["max_time_gap_ms"] = out["max_time_gap_ms"].round(1)

    ui.aggrid({
        "defaultColDef": {"sortable": True, "filter": True, "resizable": True,
                          "floatingFilter": True},
        "columnDefs": [{"field": c, "headerName": c} for c in keep],
        "rowData": out.sort_values(
            ["recording_date", "case_no", "camera_name"],
            ascending=[False, True, True],
        ).to_dict("records"),
        "pagination": True, "paginationPageSize": 25,
    }).classes("ag-theme-balham w-full").style("height: 460px;")


# ---------------------------------------------------------------------------
# Drops tab
# ---------------------------------------------------------------------------

def _heatmap_pivot(df: pd.DataFrame, value_col: str, agg: str = "mean") -> pd.DataFrame:
    return (
        df.dropna(subset=[value_col])
        .pivot_table(index="recording_date", columns="camera_name",
                     values=value_col, aggfunc=agg)
        .sort_index()
    )


def _date_axis_interval(n: int) -> int:
    return max(0, n // 20)


def _drop_heatmap(df: pd.DataFrame) -> None:
    sub = df.dropna(subset=["drop_rate"])
    if sub.empty:
        empty_state("No drop_rate data.")
        return
    pivot = _heatmap_pivot(sub, "drop_rate") * 100
    dates = list(pivot.index)
    cams = list(pivot.columns)
    cells = []
    for y, _ in enumerate(dates):
        for x, _ in enumerate(cams):
            v = pivot.iat[y, x]
            if pd.notna(v):
                cells.append([x, y, round(float(v), 2)])
    if not cells:
        empty_state("No drop_rate data after pivot.")
        return
    vmax = max(c[2] for c in cells) or 1
    axis = echart_axis_color()
    ui.echart({
        "tooltip": {"position": "top",
                    "formatter": "{c}"},
        "grid": base_grid(left=110, right=20, top=30, bottom=70),
        "xAxis": {"type": "category", "data": cams,
                  "axisLabel": {"color": axis, "rotate": 30}},
        "yAxis": {"type": "category", "data": dates,
                  "axisLabel": {"color": axis,
                                "interval": _date_axis_interval(len(dates))}},
        "visualMap": {"min": 0, "max": vmax,
                      "calculable": True, "orient": "horizontal",
                      "left": "center", "bottom": 5,
                      "textStyle": {"color": axis},
                      "inRange": {"color": ["#10b981", "#f59e0b", "#ef4444"]}},
        "series": [{
            "type": "heatmap",
            "data": cells,
            "label": {"show": False},
        }],
    }).style(f"height: {min(800, max(360, 18 * len(dates)))}px;")


def _drop_trend(df: pd.DataFrame) -> None:
    sub = df.dropna(subset=["drop_rate"])
    if sub.empty:
        empty_state("No drop_rate data.")
        return
    grp = (sub.groupby(["recording_date", "camera_name"])["drop_rate"]
              .median().reset_index())
    grp["drop_pct"] = (grp["drop_rate"] * 100).round(3)
    dates = sorted(grp["recording_date"].unique().tolist())
    cams = sorted(grp["camera_name"].unique().tolist())
    palette = chart_palette()
    axis = echart_axis_color()
    series = []
    for i, cam in enumerate(cams):
        per = grp[grp["camera_name"] == cam].set_index("recording_date")["drop_pct"]
        series.append({
            "name": cam,
            "type": "line",
            "smooth": True,
            "showSymbol": False,
            "connectNulls": False,
            "data": [float(per.get(d)) if d in per.index else None for d in dates],
            "itemStyle": {"color": palette[i % len(palette)]},
            "lineStyle": {"color": palette[i % len(palette)], "width": 2},
        })
    ui.echart({
        "tooltip": base_tooltip("axis"),
        "legend": {"data": cams, "top": 0, "type": "scroll",
                   "textStyle": {"color": axis}},
        "grid": base_grid(left=50, right=20, top=40, bottom=60),
        "xAxis": {"type": "category", "data": dates,
                  "axisLabel": {"color": axis, "rotate": 30,
                                "interval": _date_axis_interval(len(dates))}},
        "yAxis": {"type": "value", "name": "Drop rate (%)",
                  "nameTextStyle": {"color": axis},
                  "axisLabel": {"formatter": "{value}%", "color": axis}},
        "series": series,
    }).style("height: 380px;")


def _cumulative_drops_bar(df: pd.DataFrame) -> None:
    sub = df.dropna(subset=["dropped_frames"])
    if sub.empty:
        empty_state("No dropped_frames data.")
        return
    cam = (
        sub.groupby("camera_name")["dropped_frames"]
        .sum().reset_index()
        .sort_values("dropped_frames", ascending=True)
    )
    axis = echart_axis_color()
    palette = chart_palette()
    ui.echart({
        "tooltip": base_tooltip("axis"),
        "grid": base_grid(left=160, right=60, top=10, bottom=30),
        "xAxis": {"type": "value", "name": "Total dropped frames",
                  "nameTextStyle": {"color": axis},
                  "axisLabel": {"color": axis}},
        "yAxis": {"type": "category", "data": cam["camera_name"].tolist(),
                  "axisLabel": {"color": axis}},
        "series": [{
            "type": "bar",
            "data": [int(v) for v in cam["dropped_frames"].tolist()],
            "itemStyle": {"color": palette[3], "borderRadius": [0, 4, 4, 0]},
            "label": {"show": True, "position": "right", "color": axis,
                      "formatter": "{c}"},
        }],
    }).style("height: 340px;")


def _drop_vs_duration(df: pd.DataFrame) -> None:
    sub = df.dropna(subset=["actual_duration", "drop_rate"]).copy()
    if sub.empty:
        empty_state("No actual_duration / drop_rate data.")
        return
    sub = sub[sub["actual_duration"] > 0]
    sub["dur_min"] = (sub["actual_duration"] / 60).round(2)
    sub["drop_pct"] = (sub["drop_rate"] * 100).round(3)
    palette = chart_palette()
    axis = echart_axis_color()
    cams = sorted(sub["camera_name"].dropna().unique().tolist())
    series = []
    for i, cam in enumerate(cams):
        rows = sub[sub["camera_name"] == cam]
        series.append({
            "name": cam,
            "type": "scatter",
            "symbolSize": 7,
            "data": [[float(r.dur_min), float(r.drop_pct)] for r in rows.itertuples()],
            "itemStyle": {"color": palette[i % len(palette)], "opacity": 0.7},
        })
    ui.echart({
        "tooltip": {"trigger": "item",
                    "formatter": "{a}<br/>{c0} min &rarr; {c1}% drop"},
        "legend": {"data": cams, "top": 0, "type": "scroll",
                   "textStyle": {"color": axis}},
        "grid": base_grid(left=50, right=20, top=40, bottom=50),
        "xAxis": {"type": "value", "name": "Duration (min)",
                  "nameTextStyle": {"color": axis},
                  "axisLabel": {"color": axis}},
        "yAxis": {"type": "value", "name": "Drop rate (%)",
                  "nameTextStyle": {"color": axis},
                  "axisLabel": {"formatter": "{value}%", "color": axis}},
        "series": series,
    }).style("height: 360px;")


def _drops_vs_jitter(df: pd.DataFrame) -> None:
    sub = df.dropna(subset=["drop_rate", "max_time_gap_ms"]).copy()
    if sub.empty:
        empty_state("No drop_rate / max_time_gap_ms data.")
        return
    sub = sub[sub["max_time_gap_ms"].between(0, GAP_CLIP_MS)]
    sub["drop_pct"] = (sub["drop_rate"] * 100).round(3)
    palette = chart_palette()
    axis = echart_axis_color()
    cams = sorted(sub["camera_name"].dropna().unique().tolist())
    series = []
    for i, cam in enumerate(cams):
        rows = sub[sub["camera_name"] == cam]
        series.append({
            "name": cam,
            "type": "scatter",
            "symbolSize": 7,
            "data": [[float(r.drop_pct), float(r.max_time_gap_ms)] for r in rows.itertuples()],
            "itemStyle": {"color": palette[i % len(palette)], "opacity": 0.7},
        })
    ui.echart({
        "tooltip": {"trigger": "item",
                    "formatter": "{a}<br/>{c0}% drop &rarr; {c1} ms gap"},
        "legend": {"data": cams, "top": 0, "type": "scroll",
                   "textStyle": {"color": axis}},
        "grid": base_grid(left=60, right=20, top=40, bottom=50),
        "xAxis": {"type": "value", "name": "Drop rate (%)",
                  "nameTextStyle": {"color": axis},
                  "axisLabel": {"formatter": "{value}%", "color": axis}},
        "yAxis": {"type": "value", "name": "Max gap (ms)",
                  "nameTextStyle": {"color": axis},
                  "axisLabel": {"color": axis}},
        "series": series,
    }).style("height: 360px;")


# ---------------------------------------------------------------------------
# Timing tab
# ---------------------------------------------------------------------------

def _effective_vs_nominal_fps(df: pd.DataFrame) -> None:
    sub = df.dropna(subset=["fps", "idx_frames", "actual_duration"]).copy()
    sub = sub[(sub["fps"] > 0) & (sub["actual_duration"] > 0)]
    if sub.empty:
        empty_state("No fps / duration data.")
        return
    sub["effective_fps"] = (sub["idx_frames"] / sub["actual_duration"]).round(3)
    palette = chart_palette()
    axis = echart_axis_color()
    cams = sorted(sub["camera_name"].dropna().unique().tolist())
    series = []
    for i, cam in enumerate(cams):
        rows = sub[sub["camera_name"] == cam]
        series.append({
            "name": cam,
            "type": "scatter",
            "symbolSize": 7,
            "data": [[float(r.fps), float(r.effective_fps)] for r in rows.itertuples()],
            "itemStyle": {"color": palette[i % len(palette)], "opacity": 0.7},
        })
    fps_min = float(sub["fps"].min())
    fps_max = float(sub["fps"].max())
    series.append({
        "name": "y = x",
        "type": "line",
        "showSymbol": False,
        "data": [[fps_min, fps_min], [fps_max, fps_max]],
        "lineStyle": {"color": axis, "type": "dashed", "width": 1},
        "tooltip": {"show": False},
    })
    ui.echart({
        "tooltip": {"trigger": "item",
                    "formatter": "{a}<br/>nominal {c0} &rarr; effective {c1}"},
        "legend": {"data": cams + ["y = x"], "top": 0, "type": "scroll",
                   "textStyle": {"color": axis}},
        "grid": base_grid(left=50, right=20, top=40, bottom=50),
        "xAxis": {"type": "value", "name": "Header FPS",
                  "nameTextStyle": {"color": axis},
                  "axisLabel": {"color": axis}},
        "yAxis": {"type": "value", "name": "Effective FPS",
                  "nameTextStyle": {"color": axis},
                  "axisLabel": {"color": axis}},
        "series": series,
    }).style("height: 360px;")


def _jitter_heatmap(df: pd.DataFrame) -> None:
    sub = df.dropna(subset=["max_time_gap_ms"]).copy()
    sub = sub[sub["max_time_gap_ms"] >= 0]
    if sub.empty:
        empty_state("No max_time_gap_ms data.")
        return
    sub["max_time_gap_ms"] = sub["max_time_gap_ms"].clip(upper=JITTER_CLIP_MS)
    pivot = _heatmap_pivot(sub, "max_time_gap_ms", agg="max")
    dates = list(pivot.index)
    cams = list(pivot.columns)
    cells = []
    for y, _ in enumerate(dates):
        for x, _ in enumerate(cams):
            v = pivot.iat[y, x]
            if pd.notna(v):
                cells.append([x, y, round(float(v), 1)])
    if not cells:
        empty_state("No jitter data after pivot.")
        return
    axis = echart_axis_color()
    ui.echart({
        "tooltip": {"position": "top",
                    "formatter": "{c} ms"},
        "grid": base_grid(left=110, right=20, top=30, bottom=70),
        "xAxis": {"type": "category", "data": cams,
                  "axisLabel": {"color": axis, "rotate": 30}},
        "yAxis": {"type": "category", "data": dates,
                  "axisLabel": {"color": axis,
                                "interval": _date_axis_interval(len(dates))}},
        "visualMap": {"min": 0, "max": JITTER_CLIP_MS,
                      "calculable": True, "orient": "horizontal",
                      "left": "center", "bottom": 5,
                      "textStyle": {"color": axis},
                      "inRange": {"color": ["#10b981", "#f59e0b", "#ef4444"]}},
        "series": [{"type": "heatmap", "data": cells, "label": {"show": False}}],
    }).style(f"height: {min(800, max(360, 18 * len(dates)))}px;")


def _drift_trend(df: pd.DataFrame) -> None:
    sub = df.dropna(subset=["time_drift_ms"]).copy()
    sub = sub[sub["time_drift_ms"].between(-DRIFT_LIMIT_MS, DRIFT_LIMIT_MS)]
    if sub.empty:
        empty_state("No in-range drift data.")
        return
    grp = (sub.groupby(["recording_date", "camera_name"])["time_drift_ms"]
              .median().round(1).reset_index())
    dates = sorted(grp["recording_date"].unique().tolist())
    cams = sorted(grp["camera_name"].unique().tolist())
    palette = chart_palette()
    axis = echart_axis_color()
    series = []
    for i, cam in enumerate(cams):
        per = grp[grp["camera_name"] == cam].set_index("recording_date")["time_drift_ms"]
        series.append({
            "name": cam,
            "type": "line",
            "smooth": True,
            "showSymbol": False,
            "connectNulls": False,
            "data": [float(per.get(d)) if d in per.index else None for d in dates],
            "itemStyle": {"color": palette[i % len(palette)]},
            "lineStyle": {"color": palette[i % len(palette)], "width": 2},
        })
    ui.echart({
        "tooltip": base_tooltip("axis"),
        "legend": {"data": cams, "top": 0, "type": "scroll",
                   "textStyle": {"color": axis}},
        "grid": base_grid(left=60, right=20, top=40, bottom=60),
        "xAxis": {"type": "category", "data": dates,
                  "axisLabel": {"color": axis, "rotate": 30,
                                "interval": _date_axis_interval(len(dates))}},
        "yAxis": {"type": "value", "name": "Median drift (ms)",
                  "nameTextStyle": {"color": axis},
                  "axisLabel": {"color": axis}},
        "series": series,
    }).style("height: 380px;")


# ---------------------------------------------------------------------------
# Sync tab
# ---------------------------------------------------------------------------

def _start_skew_strip(df: pd.DataFrame) -> None:
    sub = df.dropna(subset=["first_frame_time"]).copy()
    if sub.empty:
        empty_state("No first_frame_time data.")
        return
    sub["case_key"] = sub["recording_date"].astype(str) + " · Case" + sub["case_no"].astype(str)
    baseline = sub.groupby("case_key")["first_frame_time"].transform("min")
    sub["offset_s"] = (sub["first_frame_time"] - baseline).round(2)
    cams = sorted(sub["camera_name"].dropna().unique().tolist())
    palette = chart_palette()
    axis = echart_axis_color()
    series = []
    for i, cam in enumerate(cams):
        rows = sub[sub["camera_name"] == cam]
        series.append({
            "name": cam,
            "type": "scatter",
            "symbolSize": 7,
            "data": [[cam, float(r.offset_s), r.case_key] for r in rows.itertuples()],
            "itemStyle": {"color": palette[i % len(palette)], "opacity": 0.7},
        })
    ui.echart({
        "tooltip": {"trigger": "item",
                    "formatter": "{a}<br/>{c2}<br/>+{c1} s"},
        "legend": {"data": cams, "top": 0, "type": "scroll",
                   "textStyle": {"color": axis}},
        "grid": base_grid(left=60, right=20, top=40, bottom=70),
        "xAxis": {"type": "category", "data": cams,
                  "axisLabel": {"color": axis, "rotate": 30}},
        "yAxis": {"type": "value", "name": "Offset from earliest start (s)",
                  "nameTextStyle": {"color": axis},
                  "axisLabel": {"color": axis}},
        "series": series,
    }).style("height: 420px;")


def _case_gantt_panel(df: pd.DataFrame, container) -> None:
    """Render dropdowns + reactive Gantt-style chart inside *container*."""
    sub = df.dropna(subset=["first_frame_time", "last_frame_time"]).copy()
    container.clear()
    if sub.empty:
        with container:
            empty_state("No first/last_frame_time data.")
        return

    dates = sorted(sub["recording_date"].dropna().unique().tolist(), reverse=True)
    pick = {"date": dates[0] if dates else None, "case": None}

    def _cases_for(d: str | None) -> list[int]:
        if not d:
            return []
        return sorted(sub[sub["recording_date"] == d]["case_no"].dropna().unique().astype(int).tolist())

    def _draw() -> None:
        chart_box.clear()
        if pick["date"] is None or pick["case"] is None:
            with chart_box:
                empty_state("Pick a date and case.")
            return
        rows = sub[
            (sub["recording_date"] == pick["date"])
            & (sub["case_no"].astype("Int64") == int(pick["case"]))
        ]
        if rows.empty:
            with chart_box:
                empty_state("No rows for this case.")
            return
        baseline = rows["first_frame_time"].min()
        rows = rows.assign(
            start_s=(rows["first_frame_time"] - baseline).round(2),
            dur_s=(rows["last_frame_time"] - rows["first_frame_time"]).round(2),
        ).sort_values("camera_name")
        cams = rows["camera_name"].tolist()
        offsets = rows["start_s"].tolist()
        durations = rows["dur_s"].tolist()
        axis = echart_axis_color()
        palette = chart_palette()
        with chart_box:
            ui.echart({
                "tooltip": {"trigger": "axis", "axisPointer": {"type": "shadow"},
                            "formatter": "{b}<br/>start +{c0}s · dur {c1}s"},
                "grid": base_grid(left=140, right=30, top=20, bottom=50),
                "xAxis": {"type": "value", "name": "Seconds from earliest start",
                          "nameTextStyle": {"color": axis},
                          "axisLabel": {"color": axis}},
                "yAxis": {"type": "category", "data": cams,
                          "axisLabel": {"color": axis}},
                "series": [
                    {"name": "offset", "type": "bar", "stack": "g",
                     "itemStyle": {"color": "transparent"},
                     "data": [float(v) for v in offsets]},
                    {"name": "duration", "type": "bar", "stack": "g",
                     "itemStyle": {"color": palette[0], "borderRadius": [0, 4, 4, 0]},
                     "label": {"show": True, "position": "insideRight",
                               "color": "#fff", "formatter": "{c}s"},
                     "data": [float(v) for v in durations]},
                ],
            }).style(f"height: {max(220, 36 * len(cams) + 80)}px;")

    def _on_date(e) -> None:
        pick["date"] = e.value
        cases = _cases_for(pick["date"])
        case_select.options = cases
        case_select.value = cases[0] if cases else None
        case_select.update()
        pick["case"] = case_select.value
        _draw()

    def _on_case(e) -> None:
        pick["case"] = e.value
        _draw()

    with container:
        with ui.row().classes("items-center gap-3 q-mb-sm"):
            ui.select(
                options=dates, value=pick["date"], label="Date",
                on_change=_on_date,
            ).props("dense").classes("min-w-[180px]")
            initial_cases = _cases_for(pick["date"])
            pick["case"] = initial_cases[0] if initial_cases else None
            case_select = ui.select(
                options=initial_cases, value=pick["case"], label="Case",
                on_change=_on_case,
            ).props("dense").classes("min-w-[120px]")
        chart_box = ui.column().classes("w-full")
        _draw()


# ---------------------------------------------------------------------------
# Capture tab
# ---------------------------------------------------------------------------

def _time_of_day_histogram(df: pd.DataFrame) -> None:
    sub = df.dropna(subset=["rec_timestamp"]).copy()
    if sub.empty:
        empty_state("No rec_timestamp data.")
        return
    sub["hour"] = (
        pd.to_datetime(sub["rec_timestamp"], unit="s", utc=True)
          .dt.tz_convert(datetime.now().astimezone().tzinfo)
          .dt.hour
    )
    hours = list(range(24))
    counts = [int((sub["hour"] == h).sum()) for h in hours]
    if "drop_rate" in sub.columns:
        means = []
        for h in hours:
            v = sub.loc[sub["hour"] == h, "drop_rate"].dropna()
            means.append(round(float(v.mean()) * 100, 3) if len(v) else None)
    else:
        means = [None] * 24
    palette = chart_palette()
    axis = echart_axis_color()
    ui.echart({
        "tooltip": base_tooltip("axis"),
        "legend": {"data": ["Recordings", "Mean drop %"], "top": 0,
                   "textStyle": {"color": axis}},
        "grid": base_grid(left=60, right=60, top=40, bottom=40),
        "xAxis": {"type": "category", "data": [f"{h:02d}" for h in hours],
                  "axisLabel": {"color": axis}},
        "yAxis": [
            {"type": "value", "name": "Recordings",
             "nameTextStyle": {"color": axis},
             "axisLabel": {"color": axis}},
            {"type": "value", "name": "Drop %",
             "nameTextStyle": {"color": axis},
             "axisLabel": {"formatter": "{value}%", "color": axis}},
        ],
        "series": [
            {"name": "Recordings", "type": "bar",
             "itemStyle": {"color": palette[0], "borderRadius": [4, 4, 0, 0]},
             "data": counts},
            {"name": "Mean drop %", "type": "line", "yAxisIndex": 1,
             "smooth": True, "connectNulls": True,
             "lineStyle": {"color": palette[3], "width": 2},
             "itemStyle": {"color": palette[3]},
             "data": means},
        ],
    }).style("height: 340px;")


def _cumulative_hours(df: pd.DataFrame) -> None:
    sub = df.dropna(subset=["actual_duration"]).copy()
    sub = sub[sub["actual_duration"] > 0]
    if sub.empty:
        empty_state("No actual_duration data.")
        return
    sub["hours"] = sub["actual_duration"] / 3600.0
    grp = sub.groupby(["recording_date", "camera_name"])["hours"].sum().reset_index()
    dates = sorted(grp["recording_date"].unique().tolist())
    cams = sorted(grp["camera_name"].unique().tolist())
    palette = chart_palette()
    axis = echart_axis_color()
    series = []
    for i, cam in enumerate(cams):
        per = grp[grp["camera_name"] == cam].set_index("recording_date")["hours"]
        running = 0.0
        vals = []
        for d in dates:
            running += float(per.get(d, 0.0))
            vals.append(round(running, 2))
        series.append({
            "name": cam,
            "type": "line",
            "smooth": True,
            "showSymbol": False,
            "data": vals,
            "itemStyle": {"color": palette[i % len(palette)]},
            "lineStyle": {"color": palette[i % len(palette)], "width": 2},
        })
    ui.echart({
        "tooltip": base_tooltip("axis"),
        "legend": {"data": cams, "top": 0, "type": "scroll",
                   "textStyle": {"color": axis}},
        "grid": base_grid(left=60, right=20, top=40, bottom=60),
        "xAxis": {"type": "category", "data": dates,
                  "axisLabel": {"color": axis, "rotate": 30,
                                "interval": _date_axis_interval(len(dates))}},
        "yAxis": {"type": "value", "name": "Cumulative hours",
                  "nameTextStyle": {"color": axis},
                  "axisLabel": {"color": axis}},
        "series": series,
    }).style("height: 380px;")


def _storage_efficiency(df: pd.DataFrame) -> None:
    sub = df.dropna(subset=["size_mb", "actual_duration"]).copy()
    sub = sub[sub["actual_duration"] > 0]
    if sub.empty:
        empty_state("No size_mb / duration data.")
        return
    sub["mb_per_min"] = sub["size_mb"] / (sub["actual_duration"] / 60.0)
    cams = sorted(sub["camera_name"].dropna().unique().tolist())
    box_data = []
    for cam in cams:
        vals = sub.loc[sub["camera_name"] == cam, "mb_per_min"].dropna()
        if vals.empty:
            box_data.append([0, 0, 0, 0, 0])
            continue
        q = vals.quantile([0, 0.25, 0.5, 0.75, 1.0]).round(2).tolist()
        box_data.append(q)
    palette = chart_palette()
    axis = echart_axis_color()
    ui.echart({
        "tooltip": {"trigger": "item"},
        "grid": base_grid(left=60, right=20, top=20, bottom=60),
        "xAxis": {"type": "category", "data": cams,
                  "axisLabel": {"color": axis, "rotate": 30}},
        "yAxis": {"type": "value", "name": "MB / min",
                  "nameTextStyle": {"color": axis},
                  "axisLabel": {"color": axis}},
        "series": [{
            "type": "boxplot",
            "data": box_data,
            "itemStyle": {"color": palette[1], "borderColor": axis},
        }],
    }).style("height: 340px;")


# ---------------------------------------------------------------------------
# Worst-N tab
# ---------------------------------------------------------------------------

def _worst_recordings_grid(df: pd.DataFrame) -> None:
    if df.empty:
        empty_state("No rows.")
        return
    sub = df.copy()
    if "header_ok" in sub.columns:
        sub = sub[sub["header_ok"].fillna(0) == 1]
    cols = [c for c in [
        "recording_date", "case_no", "camera_name",
        "drop_rate", "max_time_gap_ms", "time_drift_ms",
        "dropped_frames", "size_mb",
    ] if c in sub.columns]
    out = sub[cols].copy()
    if "drop_rate" in out.columns:
        out["drop_rate"] = (out["drop_rate"] * 100).round(2)
    if "max_time_gap_ms" in out.columns:
        out["max_time_gap_ms"] = out["max_time_gap_ms"].round(1)
    if "time_drift_ms" in out.columns:
        out["time_drift_ms"] = out["time_drift_ms"].clip(
            lower=-DRIFT_LIMIT_MS, upper=DRIFT_LIMIT_MS).round(1)
    if "size_mb" in out.columns:
        out["size_mb"] = out["size_mb"].round(1)
    out = out.sort_values("drop_rate", ascending=False, na_position="last")
    headers = {
        "recording_date": "Date",
        "case_no": "Case",
        "camera_name": "Camera",
        "drop_rate": "Drop %",
        "max_time_gap_ms": "Max gap (ms)",
        "time_drift_ms": "Drift (ms)",
        "dropped_frames": "Dropped frames",
        "size_mb": "Size (MB)",
    }
    ui.aggrid({
        "defaultColDef": {"sortable": True, "filter": True, "resizable": True,
                          "floatingFilter": True},
        "columnDefs": [{"field": c, "headerName": headers.get(c, c),
                        "type": ("numericColumn" if c not in
                                 ("recording_date", "camera_name") else None)}
                       for c in cols],
        "rowData": out.to_dict("records"),
        "pagination": True, "paginationPageSize": 25,
    }).classes("ag-theme-balham w-full").style("height: 460px;")


# ---------------------------------------------------------------------------
# Page
# ---------------------------------------------------------------------------

def _format_jitter(ms: float | None) -> str:
    if ms is None or pd.isna(ms):
        return "—"
    if ms >= 5000:
        return "5s+"
    if ms >= 1000:
        return f"{ms/1000:.1f} s"
    return f"{ms:.0f} ms"


@ui.page("/seq-advanced")
def seq_advanced_page() -> None:
    with page_frame("Advanced SEQ"):
        db_path = state.get()
        ui.label("Advanced SEQ Analysis").classes("section-h text-h5 text-weight-medium")
        ui.label("Per-file SEQ header + IDX metrics from seq_enriched.") \
            .classes("text-caption muted")

        df = query_df(db_path, "SELECT * FROM seq_enriched")
        if df.empty:
            ui.label("Could not load seq_enriched. "
                     "Run scripts/helpers/analyze_seq_fields.py to populate it.") \
                .classes("text-warning")
            return

        # Filter state
        all_dates = sorted(df["recording_date"].dropna().unique().tolist())
        state_box = {
            "junk": False,
            "from": all_dates[0] if all_dates else None,
            "to": all_dates[-1] if all_dates else None,
        }
        view = {"df": df.copy()}

        def _apply() -> None:
            sub = df if state_box["junk"] else df[~df["camera_name"].map(_is_junk)]
            if state_box["from"]:
                sub = sub[sub["recording_date"] >= state_box["from"]]
            if state_box["to"]:
                sub = sub[sub["recording_date"] <= state_box["to"]]
            view["df"] = sub.copy()

        _apply()

        # ---------- KPI strip (recomputed on filter change) ----------
        kpi_box = ui.row().classes("w-full no-wrap gap-4")

        def _draw_kpis() -> None:
            kpi_box.clear()
            v = view["df"]
            files_with_idx = int((v.get("has_idx", pd.Series(dtype=int)) == 1).sum())
            files_header_ok = int((v.get("header_ok", pd.Series(dtype=int)) == 1).sum())
            mean_drop_pct = (
                round(v["drop_rate"].dropna().mean() * 100, 2)
                if "drop_rate" in v.columns and v["drop_rate"].notna().any()
                else None
            )
            files_with_resets = (
                int((v.get("n_counter_resets", pd.Series(dtype=int)) > 0).sum())
                if "n_counter_resets" in v.columns else 0
            )
            total_hours = (
                round(float(v["actual_duration"].dropna().sum()) / 3600.0, 1)
                if "actual_duration" in v.columns else 0.0
            )
            max_jitter = (
                float(v["max_time_gap_ms"].dropna().max())
                if "max_time_gap_ms" in v.columns and v["max_time_gap_ms"].notna().any()
                else None
            )
            with kpi_box:
                kpi_card("FILES WITH IDX", f"{files_with_idx:,}", f"of {len(v):,}")
                kpi_card("HEADER OK", f"{files_header_ok:,}", f"of {len(v):,}")
                kpi_card(
                    "MEAN DROP RATE",
                    f"{mean_drop_pct:.2f}%" if mean_drop_pct is not None else "—",
                    "across files",
                )
                kpi_card("COUNTER RESETS", f"{files_with_resets:,}",
                         "files with ≥1 reset")
                kpi_card("TOTAL HOURS", f"{total_hours:,.1f}",
                         "actual_duration sum")
                kpi_card("MAX JITTER", _format_jitter(max_jitter),
                         "worst max_time_gap_ms")

        _draw_kpis()

        # ---------- Filter bar ----------
        body = ui.column().classes("w-full gap-4")

        def _rerender() -> None:
            _apply()
            _draw_kpis()
            _draw_tabs()

        with ui.row().classes("items-center gap-4 q-mt-sm"):
            ui.switch(
                "Include JUNK rows", value=False,
                on_change=lambda e: (state_box.__setitem__("junk", bool(e.value)), _rerender()),
            ).props("dense")

            with ui.input("From").props("dense readonly").classes("min-w-[140px]") as from_in:
                from_in.value = state_box["from"] or ""
                with from_in.add_slot("append"):
                    ui.icon("event").classes("cursor-pointer")
                with ui.menu().props("no-parent-event") as from_menu:
                    ui.date(
                        value=state_box["from"],
                        on_change=lambda e: (
                            state_box.__setitem__("from", e.value or None),
                            from_in.set_value(e.value or ""),
                            _rerender(),
                        ),
                    )
                from_in.on("click", lambda _: from_menu.open())

            with ui.input("To").props("dense readonly").classes("min-w-[140px]") as to_in:
                to_in.value = state_box["to"] or ""
                with to_in.add_slot("append"):
                    ui.icon("event").classes("cursor-pointer")
                with ui.menu().props("no-parent-event") as to_menu:
                    ui.date(
                        value=state_box["to"],
                        on_change=lambda e: (
                            state_box.__setitem__("to", e.value or None),
                            to_in.set_value(e.value or ""),
                            _rerender(),
                        ),
                    )
                to_in.on("click", lambda _: to_menu.open())

            def _reset_dates() -> None:
                state_box["from"] = all_dates[0] if all_dates else None
                state_box["to"] = all_dates[-1] if all_dates else None
                from_in.set_value(state_box["from"] or "")
                to_in.set_value(state_box["to"] or "")
                _rerender()

            ui.button("Reset", on_click=_reset_dates).props("dense flat")

        # ---------- Tabs ----------
        TAB_NAMES = ["Overview", "Drops", "Timing", "Sync", "Capture", "Worst-N"]

        def _draw_tabs() -> None:
            body.clear()
            with body:
                with ui.tabs().classes("w-full") as tabs:
                    for name in TAB_NAMES:
                        ui.tab(name)
                with ui.tab_panels(tabs, value="Overview").classes("w-full surface-1"):
                    with ui.tab_panel("Overview"):
                        _tab_overview(view["df"])
                    with ui.tab_panel("Drops"):
                        _tab_drops(view["df"])
                    with ui.tab_panel("Timing"):
                        _tab_timing(view["df"])
                    with ui.tab_panel("Sync"):
                        _tab_sync(view["df"])
                    with ui.tab_panel("Capture"):
                        _tab_capture(view["df"])
                    with ui.tab_panel("Worst-N"):
                        _tab_worst(view["df"])

        _draw_tabs()


# ---------------------------------------------------------------------------
# Tab layouts
# ---------------------------------------------------------------------------

def _section(title: str, subtitle: str, body_fn) -> None:
    with ui.card().classes("surface-1 w-full q-pa-md"):
        ui.label(title).classes("text-subtitle1 text-weight-medium")
        ui.label(subtitle).classes("text-caption muted")
        body_fn()


def _tab_overview(df: pd.DataFrame) -> None:
    with ui.row().classes("w-full no-wrap gap-4 items-stretch"):
        with ui.card().classes("surface-1 q-pa-md flex-grow"):
            ui.label("Header field summary").classes("text-subtitle1 text-weight-medium")
            ui.label("Distinct (resolution, fps, compression) combos.") \
                .classes("text-caption muted")
            _header_summary(df)
        with ui.card().classes("surface-1 q-pa-md flex-grow"):
            ui.label("Counter resets per camera").classes("text-subtitle1 text-weight-medium")
            ui.label("Mean n_counter_resets per camera.").classes("text-caption muted")
            _counter_resets_per_camera(df)

    _section("Drop rate vs file size",
             "Each point is one SEQ file, colored by camera.",
             lambda: _drop_vs_size(df))
    _section(f"Time drift histogram (clipped to ±{DRIFT_LIMIT_MS // 1000}s)",
             "Outliers from corrupt timestamps are excluded.",
             lambda: _drift_histogram(df))
    _section("IDX integrity issues",
             f"Files with has_idx=0, header_ok=0, or |drift| > {SANE_DRIFT_MS // 1000}s.",
             lambda: _integrity_grid(df))
    _section("Per-file drill-down",
             "All seq_enriched rows. Filter columns to drill in.",
             lambda: _file_grid(df))


def _tab_drops(df: pd.DataFrame) -> None:
    _section("Drop-rate heatmap",
             "Mean drop_rate (%) per (date, camera). Quick fleet-health view.",
             lambda: _drop_heatmap(df))
    with ui.row().classes("w-full no-wrap gap-4 items-stretch"):
        with ui.card().classes("surface-1 q-pa-md flex-grow"):
            ui.label("Drop trend per camera").classes("text-subtitle1 text-weight-medium")
            ui.label("Median drop_rate (%) over time, per camera.").classes("text-caption muted")
            _drop_trend(df)
        with ui.card().classes("surface-1 q-pa-md flex-grow"):
            ui.label("Cumulative dropped frames").classes("text-subtitle1 text-weight-medium")
            ui.label("SUM(dropped_frames) per camera over the filtered range.") \
                .classes("text-caption muted")
            _cumulative_drops_bar(df)
    with ui.row().classes("w-full no-wrap gap-4 items-stretch"):
        with ui.card().classes("surface-1 q-pa-md flex-grow"):
            ui.label("Drop rate vs duration").classes("text-subtitle1 text-weight-medium")
            ui.label("Test: do longer recordings drop more?").classes("text-caption muted")
            _drop_vs_duration(df)
        with ui.card().classes("surface-1 q-pa-md flex-grow"):
            ui.label("Drops vs jitter").classes("text-subtitle1 text-weight-medium")
            ui.label("Separates lossy-but-smooth from smooth-but-stalls.") \
                .classes("text-caption muted")
            _drops_vs_jitter(df)


def _tab_timing(df: pd.DataFrame) -> None:
    _section("Effective vs nominal FPS",
             "Header fps vs idx_frames / actual_duration. Off-diagonal = off-spec.",
             lambda: _effective_vs_nominal_fps(df))
    _section("Jitter heatmap",
             f"Max max_time_gap_ms per (date, camera), clipped to {JITTER_CLIP_MS} ms.",
             lambda: _jitter_heatmap(df))
    _section(f"Drift trend (clipped to ±{DRIFT_LIMIT_MS // 1000}s)",
             "Median time_drift_ms per (date, camera).",
             lambda: _drift_trend(df))


def _tab_sync(df: pd.DataFrame) -> None:
    _section("Per-case start-skew",
             "Each point = one case. Y = camera's offset from earliest start in that case.",
             lambda: _start_skew_strip(df))
    with ui.card().classes("surface-1 w-full q-pa-md"):
        ui.label("Case timeline (Gantt)").classes("text-subtitle1 text-weight-medium")
        ui.label("Pick a date and case to compare per-camera coverage.") \
            .classes("text-caption muted")
        gantt_box = ui.column().classes("w-full")
        _case_gantt_panel(df, gantt_box)


def _tab_capture(df: pd.DataFrame) -> None:
    _section("Recording starts by hour-of-day",
             "Bars = recording starts (rec_timestamp); line = mean drop %.",
             lambda: _time_of_day_histogram(df))
    _section("Cumulative recording hours",
             "Running SUM(actual_duration) per camera. Flat = silent outage.",
             lambda: _cumulative_hours(df))
    _section("Storage efficiency",
             "MB per minute distribution per camera (boxplot).",
             lambda: _storage_efficiency(df))


def _tab_worst(df: pd.DataFrame) -> None:
    with ui.card().classes("surface-1 w-full q-pa-md"):
        ui.label("Worst recordings").classes("text-subtitle1 text-weight-medium")
        ui.label("header_ok=1 only, JUNK already filtered. Sort any column.") \
            .classes("text-caption muted")
        _worst_recordings_grid(df)
