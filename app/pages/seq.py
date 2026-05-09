"""SEQ inventory dashboard — file presence, size, JUNK summary, and coverage.

Focuses on the raw SEQ side: how much disk each camera occupies, how many
were marked as undersized JUNK, and the camera×date presence heatmap. For
deep frame-level analysis see Advanced SEQ.

Sources: ``seq_status``.
"""

from __future__ import annotations

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


def _is_junk(name: str) -> bool:
    return isinstance(name, str) and (name.endswith("_JUNK") or name.endswith("_Junk"))


def _base_camera(name: str) -> str:
    """Strip a trailing _JUNK / _Junk suffix to recover the underlying camera."""
    if not isinstance(name, str):
        return name
    if name.endswith("_JUNK"):
        return name[:-5]
    if name.endswith("_Junk"):
        return name[:-5]
    return name


def _coverage_heatmap(seq: pd.DataFrame) -> None:
    if seq.empty:
        empty_state("No SEQ presence data.")
        return
    df = seq.copy()
    df["present"] = df["size_mb"].notna().astype(int) if "size_mb" in df.columns else 1
    dates = sorted(df["recording_date"].dropna().unique().tolist())
    agg = (
        df.groupby(["recording_date", "camera_name"])["present"]
        .max().reset_index()
    )
    cam_index = {c: i for i, c in enumerate(CAMERAS)}
    date_index = {d: i for i, d in enumerate(dates)}
    data = [
        [date_index[r.recording_date], cam_index[r.camera_name], int(r.present)]
        for r in agg.itertuples()
        if r.camera_name in cam_index and r.recording_date in date_index
    ]
    axis = echart_axis_color()
    ui.echart({
        "tooltip": {"position": "top",
                    "formatter": "Date {b}<br/>Camera: {c}"},
        "grid": base_grid(left=140, right=20, top=20, bottom=80),
        "xAxis": {"type": "category", "data": dates,
                  "axisLabel": {"rotate": 60, "color": axis, "fontSize": 10},
                  "splitArea": {"show": True}},
        "yAxis": {"type": "category", "data": CAMERAS,
                  "axisLabel": {"color": axis},
                  "splitArea": {"show": True}},
        "visualMap": {"min": 0, "max": 1, "show": False,
                      "inRange": {"color": ["#fee2e2", "#10B981"]}},
        "series": [{
            "name": "SEQ", "type": "heatmap", "data": data,
            "progressive": 2000,
            "emphasis": {"itemStyle": {"borderColor": "#1e293b", "borderWidth": 1}},
        }],
    }).style("height: 420px;")


def _presence_bars(clean: pd.DataFrame) -> None:
    if clean.empty:
        empty_state("No data.")
        return
    cases = clean[["recording_date", "case_no"]].drop_duplicates()
    total_cases = len(cases)
    if total_cases == 0:
        empty_state("No data.")
        return
    rates = {}
    for cam in CAMERAS:
        present_cases = clean[clean["camera_name"] == cam][
            ["recording_date", "case_no"]
        ].drop_duplicates()
        rates[cam] = round(len(present_cases) / total_cases * 100, 1)
    cams_sorted = sorted(CAMERAS, key=lambda c: -rates[c])
    axis = echart_axis_color()
    palette = chart_palette()
    ui.echart({
        "tooltip": base_tooltip("axis") | {"valueFormatter": "{value}%"},
        "grid": base_grid(left=160, right=40, top=10, bottom=30),
        "xAxis": {"type": "value", "max": 100,
                  "axisLabel": {"formatter": "{value}%", "color": axis}},
        "yAxis": {"type": "category", "data": cams_sorted,
                  "axisLabel": {"color": axis}},
        "series": [{
            "type": "bar",
            "data": [rates[c] for c in cams_sorted],
            "itemStyle": {"color": palette[1], "borderRadius": [0, 4, 4, 0]},
            "label": {"show": True, "position": "right", "formatter": "{c}%",
                      "color": axis},
        }],
    }).style("height: 360px;")


def _size_per_camera(clean: pd.DataFrame) -> None:
    if clean.empty or "size_mb" not in clean.columns:
        empty_state("No size data.")
        return
    sizes = (
        clean.dropna(subset=["size_mb"])
        .groupby("camera_name")["size_mb"]
        .sum().div(1024).round(1).reset_index(name="size_gb")
        .sort_values("size_gb", ascending=True)
    )
    if sizes.empty:
        empty_state("No size data.")
        return
    axis = echart_axis_color()
    palette = chart_palette()
    ui.echart({
        "tooltip": base_tooltip("axis") | {"valueFormatter": "{value} GB"},
        "grid": base_grid(left=160, right=50, top=10, bottom=30),
        "xAxis": {"type": "value",
                  "axisLabel": {"formatter": "{value} GB", "color": axis}},
        "yAxis": {"type": "category",
                  "data": sizes["camera_name"].tolist(),
                  "axisLabel": {"color": axis}},
        "series": [{
            "type": "bar",
            "data": sizes["size_gb"].tolist(),
            "itemStyle": {"color": palette[1], "borderRadius": [0, 4, 4, 0]},
            "label": {"show": True, "position": "right",
                      "formatter": "{c} GB", "color": axis},
        }],
    }).style("height: 320px;")


def _junk_breakdown(junk: pd.DataFrame) -> None:
    if junk.empty:
        ui.label("No JUNK rows. All SEQ files passed the size threshold.") \
            .classes("text-positive")
        return
    junk = junk.copy()
    junk["base"] = junk["camera_name"].map(_base_camera)
    counts = (
        junk.groupby("base").size().reset_index(name="junk_files")
        .sort_values("junk_files", ascending=True)
    )
    axis = echart_axis_color()
    palette = chart_palette()
    ui.echart({
        "tooltip": base_tooltip("axis"),
        "grid": base_grid(left=160, right=40, top=10, bottom=30),
        "xAxis": {"type": "value", "axisLabel": {"color": axis}},
        "yAxis": {"type": "category",
                  "data": counts["base"].tolist(),
                  "axisLabel": {"color": axis}},
        "series": [{
            "type": "bar",
            "data": counts["junk_files"].astype(int).tolist(),
            "itemStyle": {"color": palette[3], "borderRadius": [0, 4, 4, 0]},
            "label": {"show": True, "position": "right", "color": axis},
        }],
    }).style("height: 320px;")


def _inventory_grid(seq: pd.DataFrame) -> None:
    if seq.empty:
        empty_state("No inventory rows.")
        return
    cols = [c for c in ["recording_date", "case_no", "camera_name", "size_mb"]
            if c in seq.columns]
    df = seq[cols].copy()
    if "size_mb" in df.columns:
        df["size_mb"] = df["size_mb"].round(1)
    ui.aggrid({
        "defaultColDef": {"sortable": True, "filter": True, "resizable": True,
                          "floatingFilter": True},
        "columnDefs": [
            {"field": "recording_date", "headerName": "Date"},
            {"field": "case_no",        "headerName": "Case"},
            {"field": "camera_name",    "headerName": "Camera"},
            {"field": "size_mb",        "headerName": "Size (MB)",
             "type": "numericColumn"},
        ],
        "rowData": df.sort_values(
            ["recording_date", "case_no", "camera_name"],
            ascending=[False, True, True],
        ).to_dict("records"),
        "pagination": True, "paginationPageSize": 20,
    }).classes("ag-theme-balham w-full").style("height: 420px;")


@ui.page("/seq")
def seq_page() -> None:
    with page_frame("SEQ"):
        db_path = state.get()
        ui.label("SEQ Inventory").classes("section-h text-h5 text-weight-medium")
        ui.label("Raw SEQ files — presence, size, JUNK, and gaps.") \
            .classes("text-caption muted")

        seq = query_df(
            db_path,
            "SELECT recording_date, case_no, camera_name, size_mb FROM seq_status",
        )
        if seq.empty:
            ui.label("Could not load seq_status. Check the SQLite path in the left drawer.") \
                .classes("text-warning")
            return

        junk_mask = seq["camera_name"].map(_is_junk)
        clean = seq[~junk_mask].copy()
        junk = seq[junk_mask].copy()

        total_files = len(clean)
        total_size_gb = round(
            (clean["size_mb"].dropna().sum() / 1024) if "size_mb" in clean.columns else 0,
            1,
        )
        junk_count = len(junk)

        with ui.row().classes("w-full no-wrap gap-4"):
            kpi_card("SEQ FILES", f"{total_files:,}", "JUNK excluded")
            kpi_card("TOTAL SIZE", f"{total_size_gb:,} GB", "on disk")
            kpi_card("JUNK ROWS", f"{junk_count:,}", "undersized / failed")

        with ui.card().classes("surface-1 w-full q-pa-md"):
            ui.label("Total size per camera").classes("text-subtitle1 text-weight-medium")
            ui.label("Sum of size_mb, in GB.").classes("text-caption muted")
            _size_per_camera(clean)

        with ui.card().classes("surface-1 w-full q-pa-md"):
            ui.label("JUNK breakdown").classes("text-subtitle1 text-weight-medium")
            ui.label("Undersized rows grouped by underlying camera.") \
                .classes("text-caption muted")
            _junk_breakdown(junk)

        # ── Coverage: SEQ camera × date heatmap ────────────────────────────
        with ui.card().classes("surface-1 w-full q-pa-md"):
            ui.label("Camera × date coverage").classes("text-subtitle1 text-weight-medium")
            ui.label("One column per recording day. Green = SEQ present, red = missing.") \
                .classes("text-caption muted")
            _coverage_heatmap(clean)

        # ── Coverage: per-camera SEQ presence ──────────────────────────────
        with ui.card().classes("surface-1 w-full q-pa-md"):
            ui.label("Per-camera presence rate").classes("text-subtitle1 text-weight-medium")
            ui.label("Share of cases where each camera has a SEQ.") \
                .classes("text-caption muted")
            _presence_bars(clean)

        with ui.card().classes("surface-1 w-full q-pa-md"):
            ui.label("Inventory").classes("text-subtitle1 text-weight-medium")
            ui.label("All SEQ rows. Sort and filter to drill in.") \
                .classes("text-caption muted")
            _inventory_grid(seq)
