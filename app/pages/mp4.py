"""MP4 dashboard — KPIs, distribution charts, and coverage panels.

Modern ECharts replacement for the legacy Plotly-based ``mp4_statistics``
page (Streamlit port). Also absorbs the MP4 portion of the former
Coverage page: camera×date heatmap, per-camera presence rate, and the
fully-covered cases trend.

Sources: ``cur_mp4_status_statistics``, ``cur_mp4_missing``, ``mp4_status``.
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


def _presence_df(db_path: str) -> pd.DataFrame:
    return query_df(
        db_path,
        """
        SELECT recording_date, case_no, camera_name,
               CASE WHEN size_mb IS NOT NULL THEN 1 ELSE 0 END AS present
        FROM mp4_status
        WHERE camera_name NOT LIKE '%\\_JUNK' ESCAPE '\\'
          AND camera_name NOT LIKE '%\\_Junk' ESCAPE '\\'
        """,
    )


def _coverage_heatmap(df: pd.DataFrame) -> None:
    if df.empty:
        empty_state("No MP4 presence data.")
        return
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
            "name": "MP4", "type": "heatmap", "data": data,
            "progressive": 2000,
            "emphasis": {"itemStyle": {"borderColor": "#1e293b", "borderWidth": 1}},
        }],
    }).style("height: 420px;")


def _presence_bars(df: pd.DataFrame) -> None:
    if df.empty:
        empty_state("No data.")
        return
    rates = {}
    for cam in CAMERAS:
        sub = df[df["camera_name"] == cam]
        rates[cam] = round(sub["present"].mean() * 100, 1) if not sub.empty else 0.0
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
            "itemStyle": {"color": palette[0], "borderRadius": [0, 4, 4, 0]},
            "label": {"show": True, "position": "right", "formatter": "{c}%",
                      "color": axis},
        }],
    }).style("height: 360px;")


def _fully_covered_trend(df: pd.DataFrame) -> None:
    if df.empty:
        empty_state("No data.")
        return
    work = df.copy()
    work["date"] = pd.to_datetime(work["recording_date"], errors="coerce")
    work = work.dropna(subset=["date"])
    work["month"] = work["date"].dt.to_period("M").astype(str)
    per_case = (
        work.groupby(["month", "recording_date", "case_no"])["present"]
        .sum().reset_index()
    )
    per_case["full"] = (per_case["present"] >= len(CAMERAS)).astype(int)
    monthly = per_case.groupby("month").agg(
        total=("full", "size"), full=("full", "sum"),
    ).reset_index()
    monthly["pct"] = (monthly["full"] / monthly["total"] * 100).round(1)
    monthly = monthly.sort_values("month").tail(24)
    axis = echart_axis_color()
    palette = chart_palette()
    ui.echart({
        "tooltip": base_tooltip("axis") | {"valueFormatter": "{value}%"},
        "grid": base_grid(left=50, right=20, top=20, bottom=60),
        "xAxis": {"type": "category", "data": monthly["month"].tolist(),
                  "axisLabel": {"rotate": 45, "color": axis}},
        "yAxis": {"type": "value", "max": 100,
                  "axisLabel": {"formatter": "{value}%", "color": axis}},
        "series": [{
            "type": "line", "smooth": True, "showSymbol": True,
            "data": monthly["pct"].tolist(),
            "lineStyle": {"width": 3, "color": palette[2]},
            "itemStyle": {"color": palette[2]},
            "areaStyle": {"opacity": 0.18, "color": palette[2]},
        }],
    }).style("height: 280px;")


def _camera_count_distribution(df: pd.DataFrame) -> None:
    cam_dist = (
        df.groupby("cameras_count").size().reset_index(name="count")
        .sort_values("cameras_count")
    )
    if cam_dist.empty:
        empty_state("No camera-count data.")
        return
    axis = echart_axis_color()
    palette = chart_palette()
    ui.echart({
        "tooltip": base_tooltip("axis"),
        "grid": base_grid(left=40, right=20, top=20, bottom=40),
        "xAxis": {
            "type": "category",
            "data": [f"{int(c)} cam" for c in cam_dist["cameras_count"]],
            "axisLabel": {"color": axis},
        },
        "yAxis": {"type": "value", "axisLabel": {"color": axis}},
        "series": [{
            "type": "bar",
            "data": cam_dist["count"].astype(int).tolist(),
            "itemStyle": {"color": palette[0], "borderRadius": [4, 4, 0, 0]},
            "label": {"show": True, "position": "top", "color": axis},
        }],
    }).style("height: 320px;")


def _yearly_overview(df: pd.DataFrame) -> None:
    yearly_recs = df.groupby("year").size().reset_index(name="recordings")
    yearly_days = (
        df.drop_duplicates(subset=["recording_date"])
        .groupby("year").size().reset_index(name="surgery_days")
    )
    yearly = yearly_recs.merge(yearly_days, on="year").sort_values("year")
    if yearly.empty:
        empty_state("No yearly data.")
        return
    axis = echart_axis_color()
    palette = chart_palette()
    ui.echart({
        "tooltip": base_tooltip("axis"),
        "legend": {"data": ["Recordings", "Surgery Days"], "top": 0,
                   "textStyle": {"color": axis}},
        "grid": base_grid(left=40, right=20, top=40, bottom=40),
        "xAxis": {"type": "category",
                  "data": [str(int(y)) for y in yearly["year"]],
                  "axisLabel": {"color": axis}},
        "yAxis": {"type": "value", "axisLabel": {"color": axis}},
        "series": [
            {"name": "Recordings", "type": "bar",
             "data": yearly["recordings"].astype(int).tolist(),
             "itemStyle": {"color": palette[0], "borderRadius": [4, 4, 0, 0]},
             "label": {"show": True, "position": "top", "color": axis}},
            {"name": "Surgery Days", "type": "bar",
             "data": yearly["surgery_days"].astype(int).tolist(),
             "itemStyle": {"color": palette[1], "borderRadius": [4, 4, 0, 0]},
             "label": {"show": True, "position": "top", "color": axis}},
        ],
    }).style("height: 320px;")


def _cases_per_day(df: pd.DataFrame) -> None:
    cases_per_day = df.groupby("recording_date").size().reset_index(name="cases")
    cpd = cases_per_day.groupby("cases").size().reset_index(name="days")
    if cpd.empty:
        empty_state("No data.")
        return
    axis = echart_axis_color()
    palette = chart_palette()
    ui.echart({
        "tooltip": base_tooltip("axis"),
        "grid": base_grid(left=40, right=20, top=20, bottom=40),
        "xAxis": {
            "type": "category",
            "data": [f"{int(c)} case{'s' if c > 1 else ''}" for c in cpd["cases"]],
            "axisLabel": {"color": axis},
        },
        "yAxis": {"type": "value", "name": "Days",
                  "axisLabel": {"color": axis},
                  "nameTextStyle": {"color": axis}},
        "series": [{
            "type": "bar",
            "data": cpd["days"].astype(int).tolist(),
            "itemStyle": {"color": palette[4], "borderRadius": [4, 4, 0, 0]},
            "label": {"show": True, "position": "top", "color": axis},
        }],
    }).style("height: 320px;")


def _monthly_timeline(df: pd.DataFrame) -> None:
    monthly = df.groupby("month").size().reset_index(name="cases").sort_values("month")
    if monthly.empty:
        empty_state("No data.")
        return
    axis = echart_axis_color()
    palette = chart_palette()
    ui.echart({
        "tooltip": base_tooltip("axis"),
        "grid": base_grid(left=40, right=20, top=20, bottom=70),
        "xAxis": {"type": "category", "data": monthly["month"].tolist(),
                  "axisLabel": {"rotate": 45, "color": axis}},
        "yAxis": {"type": "value", "axisLabel": {"color": axis}},
        "series": [{
            "type": "bar",
            "data": monthly["cases"].astype(int).tolist(),
            "itemStyle": {"color": palette[0], "borderRadius": [4, 4, 0, 0]},
        }],
    }).style("height: 320px;")


def _missing_grid(db_path: str) -> None:
    df = query_df(
        db_path,
        "SELECT recording_date, case_no, camera_name FROM cur_mp4_missing "
        "ORDER BY recording_date DESC, case_no, camera_name",
    )
    if df.empty:
        ui.label("No missing MP4s. All exported recordings present.").classes("text-positive")
        return
    ui.aggrid({
        "defaultColDef": {"sortable": True, "filter": True, "resizable": True,
                          "floatingFilter": True},
        "columnDefs": [
            {"field": "recording_date", "headerName": "Date"},
            {"field": "case_no",        "headerName": "Case"},
            {"field": "camera_name",    "headerName": "Camera"},
        ],
        "rowData": df.to_dict("records"),
        "pagination": True, "paginationPageSize": 15,
    }).classes("ag-theme-balham w-full").style("height: 360px;")


@ui.page("/mp4")
def mp4_page() -> None:
    with page_frame("MP4"):
        db_path = state.get()
        ui.label("MP4 Dashboard").classes("section-h text-h5 text-weight-medium")
        ui.label("Converted MP4 recordings — coverage, distribution, and gaps.") \
            .classes("text-caption muted")

        df = query_df(
            db_path,
            "SELECT recording_date, case_no, cameras_count "
            "FROM cur_mp4_status_statistics",
        )
        if df.empty:
            ui.label("Could not load cur_mp4_status_statistics. "
                     "Verify the SQLite path in the left drawer.").classes("text-warning")
            return

        df["date"] = pd.to_datetime(df["recording_date"], errors="coerce")
        df = df.dropna(subset=["date"])
        df["year"] = df["date"].dt.year
        df["month"] = df["date"].dt.to_period("M").astype(str)

        total_recordings = len(df)
        surgery_days = df["recording_date"].nunique()
        avg_cameras = round(df["cameras_count"].mean(), 1) if total_recordings else 0
        max_cameras = int(df["cameras_count"].max()) if total_recordings else 0
        date_min = df["recording_date"].min()
        date_max = df["recording_date"].max()

        # Coverage data (also used for KPIs below).
        presence = _presence_df(db_path)
        if presence.empty:
            full_cases = partial_cases = 0
        else:
            per_case = (
                presence.groupby(["recording_date", "case_no"])["present"]
                .sum().reset_index()
            )
            full_cases = int((per_case["present"] >= len(CAMERAS)).sum())
            partial_cases = int(
                ((per_case["present"] > 0) & (per_case["present"] < len(CAMERAS))).sum()
            )

        with ui.row().classes("w-full no-wrap gap-4"):
            kpi_card("TOTAL RECORDINGS", f"{total_recordings:,}", "video files")
            kpi_card("SURGERY DAYS",     f"{surgery_days:,}",     "unique dates")
            kpi_card("FULLY COVERED",    f"{full_cases:,}",
                     f"{(full_cases / total_recordings * 100):.0f}% of cases"
                     if total_recordings else "—")
            kpi_card("PARTIAL CASES",    f"{partial_cases:,}",   "missing ≥1 camera")

        ui.label(
            f"Range: {date_min} → {date_max}  •  Avg cameras {avg_cameras}  •  Max {max_cameras}"
        ).classes("text-caption muted")

        with ui.row().classes("w-full no-wrap gap-4 items-stretch"):
            with ui.card().classes("surface-1 q-pa-md flex-grow"):
                ui.label("Camera count distribution").classes("text-subtitle1 text-weight-medium")
                ui.label("How many cameras each recording has.").classes("text-caption muted")
                _camera_count_distribution(df)

            with ui.card().classes("surface-1 q-pa-md flex-grow"):
                ui.label("Yearly overview").classes("text-subtitle1 text-weight-medium")
                ui.label("Recordings vs surgery days per year.").classes("text-caption muted")
                _yearly_overview(df)

        with ui.row().classes("w-full no-wrap gap-4 items-stretch"):
            with ui.card().classes("surface-1 q-pa-md flex-grow"):
                ui.label("Cases per surgery day").classes("text-subtitle1 text-weight-medium")
                ui.label("Distribution of how many cases run on a given day.") \
                    .classes("text-caption muted")
                _cases_per_day(df)

            with ui.card().classes("surface-1 q-pa-md flex-grow"):
                ui.label("Monthly timeline").classes("text-subtitle1 text-weight-medium")
                ui.label("Cases per month.").classes("text-caption muted")
                _monthly_timeline(df)

        # ── Coverage: MP4 camera × date heatmap ────────────────────────────
        with ui.card().classes("surface-1 w-full q-pa-md"):
            ui.label("Camera × date coverage").classes("text-subtitle1 text-weight-medium")
            ui.label("One column per recording day. Green = MP4 present, red = missing.") \
                .classes("text-caption muted")
            _coverage_heatmap(presence)

        # ── Coverage: per-camera presence + fully-covered trend ────────────
        with ui.row().classes("w-full no-wrap gap-4 items-stretch"):
            with ui.card().classes("surface-1 q-pa-md flex-grow"):
                ui.label("Per-camera presence rate").classes("text-subtitle1 text-weight-medium")
                ui.label("Share of cases where each camera has an MP4.") \
                    .classes("text-caption muted")
                _presence_bars(presence)

            with ui.card().classes("surface-1 q-pa-md flex-grow"):
                ui.label("Fully-covered cases — trend").classes("text-subtitle1 text-weight-medium")
                ui.label("Monthly % of cases with all 8 cameras (last 24 months).") \
                    .classes("text-caption muted")
                _fully_covered_trend(presence)

        with ui.card().classes("surface-1 w-full q-pa-md"):
            ui.label("Missing MP4s").classes("text-subtitle1 text-weight-medium")
            ui.label("From cur_mp4_missing — cameras with a SEQ but no MP4 export yet.") \
                .classes("text-caption muted")
            _missing_grid(db_path)
