"""MP4 dashboard — KPIs, distribution charts, and coverage panels.

Modern ECharts replacement for the legacy Plotly-based ``mp4_statistics``
page (Streamlit port). Also absorbs the MP4 portion of the former
Coverage page: camera×date heatmap, per-camera presence rate, and the
fully-covered cases trend.

Sources: ``cur_mp4_status_statistics``, ``cur_mp4_missing``, ``mp4_status``.
"""

from __future__ import annotations

import numpy as np
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


# Renders a coloured-dot status cell. Green for Synced, red for Not Syncable,
# amber for Partial. Used by both the case list and the per-camera list.
_STATUS_CELL_RENDERER = """
function(p){
  const v = p.value || '';
  let c = '#94a3b8';
  if (v === 'Synced') c = '#10B981';
  else if (v === 'Not Syncable') c = '#EF4444';
  else if (v === 'Partial') c = '#F59E0B';
  return `<span style="display:inline-flex;align-items:center;gap:6px;">
            <span style="width:10px;height:10px;border-radius:50%;background:${c};display:inline-block;"></span>
            <span style="color:${c};font-weight:600;">${v}</span>
          </span>`;
}
"""


def _sync_df(db_path: str) -> pd.DataFrame:
    df = query_df(
        db_path,
        "SELECT recording_date, case_no, camera_name, is_syncable "
        "FROM cur_sync_status",
    )
    if not df.empty:
        df["is_syncable"] = df["is_syncable"].astype(int)
    return df


def _sync_kpis(sync_df: pd.DataFrame) -> tuple[int, int, int, int]:
    if sync_df.empty:
        return 0, 0, 0, 0
    synced_recs = int(sync_df["is_syncable"].sum())
    not_syncable = int((1 - sync_df["is_syncable"]).sum())
    case_agg = (
        sync_df.groupby(["recording_date", "case_no"])["is_syncable"]
        .agg(["sum", "size"]).reset_index()
    )
    synced_cases = int((case_agg["sum"] == case_agg["size"]).sum())
    partial_cases = int(
        ((case_agg["sum"] > 0) & (case_agg["sum"] < case_agg["size"])).sum()
    )
    return synced_recs, not_syncable, synced_cases, partial_cases


def _sync_status_bar(synced: int, not_syncable: int) -> None:
    if synced == 0 and not_syncable == 0:
        empty_state("No sync status data.")
        return
    axis = echart_axis_color()
    ui.echart({
        "tooltip": base_tooltip("axis"),
        "grid": base_grid(left=60, right=20, top=20, bottom=40),
        "xAxis": {"type": "category", "data": ["Synced", "Not Syncable"],
                  "axisLabel": {"color": axis}},
        "yAxis": {"type": "value", "name": "Recordings",
                  "axisLabel": {"color": axis},
                  "nameTextStyle": {"color": axis}},
        "series": [{
            "type": "bar",
            "data": [
                {"value": synced,       "itemStyle": {"color": "#10B981"}},
                {"value": not_syncable, "itemStyle": {"color": "#EF4444"}},
            ],
            "itemStyle": {"borderRadius": [4, 4, 0, 0]},
            "label": {"show": True, "position": "top", "color": axis,
                      "fontWeight": "bold"},
        }],
    }).style("height: 280px;")


def _avg_cameras_per_month(df: pd.DataFrame) -> None:
    if df.empty:
        empty_state("No data.")
        return
    monthly = (
        df.groupby("month")["cameras_count"].mean().reset_index()
        .sort_values("month")
    )
    monthly["avg"] = monthly["cameras_count"].round(2)
    if monthly.empty:
        empty_state("No data.")
        return
    axis = echart_axis_color()
    palette = chart_palette()
    ui.echart({
        "tooltip": base_tooltip("axis"),
        "grid": base_grid(left=50, right=20, top=20, bottom=70),
        "xAxis": {"type": "category", "data": monthly["month"].tolist(),
                  "axisLabel": {"rotate": 45, "color": axis}},
        "yAxis": {"type": "value", "min": 0, "max": 9,
                  "axisLabel": {"color": axis}},
        "series": [{
            "type": "line", "smooth": True, "showSymbol": True,
            "data": monthly["avg"].tolist(),
            "lineStyle": {"width": 3, "color": palette[3]},
            "itemStyle": {"color": palette[3]},
            "areaStyle": {"opacity": 0.18, "color": palette[3]},
        }],
    }).style("height: 280px;")


def _complete_case_list(sync_df: pd.DataFrame) -> None:
    if sync_df.empty:
        empty_state("No sync status data.")
        return
    case_agg = (
        sync_df.groupby(["recording_date", "case_no"])
        .agg(cameras=("camera_name", "size"),
             synced=("is_syncable", "sum"))
        .reset_index()
    )
    case_agg["Status"] = np.where(
        case_agg["synced"] == case_agg["cameras"], "Synced",
        np.where(case_agg["synced"] == 0, "Not Syncable", "Partial"),
    )
    case_agg = case_agg.sort_values(
        ["recording_date", "case_no"], ascending=[False, True]
    )
    rows = case_agg.assign(
        Date=case_agg["recording_date"],
        Case=case_agg["case_no"].apply(lambda n: f"Case {n}"),
        Cameras=case_agg["cameras"].astype(int),
    )[["Date", "Case", "Cameras", "Status"]]
    ui.aggrid({
        "defaultColDef": {"sortable": True, "filter": True, "resizable": True,
                          "floatingFilter": True},
        "columnDefs": [
            {"field": "Date",    "headerName": "Date",     "width": 130},
            {"field": "Case",    "headerName": "Case No.", "width": 120},
            {"field": "Cameras", "headerName": "Cameras",  "width": 110,
             "filter": "agNumberColumnFilter"},
            {"field": "Status",  "headerName": "Status",
             ":cellRenderer": _STATUS_CELL_RENDERER},
        ],
        "rowData": rows.to_dict("records"),
        "pagination": True, "paginationPageSize": 25,
    }).classes("ag-theme-balham w-full").style("height: 520px;")


def _detailed_recording_list(sync_df: pd.DataFrame) -> None:
    if sync_df.empty:
        empty_state("No sync status data.")
        return
    rows = sync_df.copy()
    rows["Status"] = np.where(rows["is_syncable"] == 1, "Synced", "Not Syncable")
    rows = rows.sort_values(
        ["recording_date", "case_no", "camera_name"],
        ascending=[False, True, True],
    )
    rows = rows.assign(
        Date=rows["recording_date"],
        Case=rows["case_no"].apply(lambda n: f"Case {n}"),
        Camera=rows["camera_name"],
    )[["Date", "Case", "Camera", "Status"]]

    n_recordings = len(rows)
    n_cases = sync_df.groupby(["recording_date", "case_no"]).ngroups
    ui.label(
        f"{n_recordings:,} recordings across {n_cases:,} cases"
    ).classes("text-caption muted q-mb-sm")

    ui.aggrid({
        "defaultColDef": {"sortable": True, "filter": True, "resizable": True,
                          "floatingFilter": True},
        "columnDefs": [
            {"field": "Date",   "headerName": "Date",     "width": 130},
            {"field": "Case",   "headerName": "Case No.", "width": 120},
            {"field": "Camera", "headerName": "Camera",   "width": 200},
            {"field": "Status", "headerName": "Status",
             ":cellRenderer": _STATUS_CELL_RENDERER},
        ],
        "rowData": rows.to_dict("records"),
        "pagination": True, "paginationPageSize": 50,
    }).classes("ag-theme-balham w-full").style("height: 600px;")


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
        sync_df = _sync_df(db_path)
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

        with ui.card().classes("surface-1 w-full q-pa-md"):
            ui.label("Average cameras per month").classes("text-subtitle1 text-weight-medium")
            ui.label("Mean camera count per recording, by month.") \
                .classes("text-caption muted")
            _avg_cameras_per_month(df)

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

        # ── Sync status overview (from cur_sync_status) ────────────────────
        synced_recs, not_syncable_recs, synced_cases, partial_cases = _sync_kpis(sync_df)
        total_sync = synced_recs + not_syncable_recs
        synced_pct = round(synced_recs / total_sync * 100, 1) if total_sync else 0.0

        with ui.card().classes("surface-1 w-full q-pa-md"):
            ui.label("Sync status overview").classes("text-subtitle1 text-weight-medium")
            ui.label(
                f"From cur_sync_status — {total_sync:,} recordings  •  "
                f"{synced_recs:,} synced ({synced_pct}%)  •  "
                f"{not_syncable_recs:,} not syncable"
            ).classes("text-caption muted")
            with ui.row().classes("w-full no-wrap gap-4 q-mt-sm"):
                kpi_card("SYNCED",         f"{synced_recs:,}",
                         f"{synced_pct}% of recordings")
                kpi_card("NOT SYNCABLE",   f"{not_syncable_recs:,}", "unrecoverable")
                kpi_card("SYNCED CASES",   f"{synced_cases:,}",
                         "all cameras syncable")
                kpi_card("PARTIAL CASES",  f"{partial_cases:,}",
                         "some cameras lost")
            _sync_status_bar(synced_recs, not_syncable_recs)

        with ui.card().classes("surface-1 w-full q-pa-md"):
            ui.label("Complete case list").classes("text-subtitle1 text-weight-medium")
            ui.label("Per-case sync status — Synced, Partial, or Not Syncable.") \
                .classes("text-caption muted")
            _complete_case_list(sync_df)

        with ui.card().classes("surface-1 w-full q-pa-md"):
            ui.label("Detailed recording list").classes("text-subtitle1 text-weight-medium")
            ui.label("Per-camera sync status from cur_sync_status.") \
                .classes("text-caption muted")
            _detailed_recording_list(sync_df)
