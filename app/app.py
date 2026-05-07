"""ScalpelLab Database Manager — NiceGUI native desktop app.

Entry point. Defines the home dashboard and imports the four sub-pages
so their `@ui.page` decorators register routes. The ERD lives on the
Database page (Schema expansion). Run with ``python run_app.py``.
"""

import os
import sys
from datetime import date as dt_date

import pandas as pd
from nicegui import ui

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import state  # noqa: E402
from app.layout import page_frame  # noqa: E402
from app.pages import database, status_summary, views, mp4_statistics  # noqa: F401,E402
from app.utils import connect  # noqa: E402


def _query(db_path: str, sql: str) -> pd.DataFrame:
    try:
        with connect(db_path) as conn:
            return pd.read_sql_query(sql, conn)
    except Exception:
        return pd.DataFrame()


def _kpi_with_spark(label: str, value, hint: str, series: list[float], color: str) -> None:
    with ui.card().classes("kpi-card surface-1 q-pa-md flex-grow").style("min-width: 200px;"):
        ui.label(label).classes("text-caption muted").style("letter-spacing: 1px;")
        with ui.row().classes("items-baseline gap-2"):
            ui.label(str(value)).classes("text-h4 text-weight-bold")
            ui.label(hint).classes("text-caption muted")
        if series:
            ui.echart({
                "grid":   {"left": 0, "right": 0, "top": 4, "bottom": 0},
                "xAxis":  {"show": False, "type": "category", "data": list(range(len(series)))},
                "yAxis":  {"show": False, "type": "value"},
                "tooltip": {"show": False},
                "series": [{
                    "type": "line", "data": series, "smooth": True, "showSymbol": False,
                    "areaStyle": {"opacity": 0.20},
                    "lineStyle": {"width": 2, "color": color},
                    "itemStyle": {"color": color},
                }],
            }).style("height: 48px;")


def _calendar_heatmap(mp4: pd.DataFrame) -> None:
    cases_per_day = mp4.groupby("recording_date").size().reset_index(name="cases")
    years_present = sorted(mp4["date"].dt.year.dropna().unique().astype(int))
    if not years_present:
        ui.label("No dated recordings to plot.").classes("muted q-mt-sm")
        return
    cal_range = [str(y) for y in years_present[-2:]]  # cap at 2 most recent years
    max_cases = int(cases_per_day["cases"].max() or 1)

    calendars = []
    series = []
    for i, year in enumerate(cal_range):
        calendars.append({
            "range": year,
            "top": 30 + i * 150,
            "left": 60, "right": 30,
            "cellSize": ["auto", 14],
            "yearLabel":  {"color": "#64748b"},
            "dayLabel":   {"color": "#94a3b8", "fontSize": 10},
            "monthLabel": {"color": "#94a3b8", "fontSize": 11},
            "splitLine":  {"show": False},
            "itemStyle":  {"borderWidth": 1, "borderColor": "rgba(255,255,255,0.4)"},
        })
        series.append({
            "type": "heatmap", "coordinateSystem": "calendar", "calendarIndex": i,
            "data": [
                [r.recording_date, int(r.cases)]
                for r in cases_per_day.itertuples()
                if str(r.recording_date).startswith(year)
            ],
        })

    ui.echart({
        "tooltip":   {"position": "top", "formatter": "{c1} cases on {c0}"},
        "visualMap": {
            "min": 0, "max": max_cases,
            "calculable": True, "orient": "horizontal",
            "left": "center", "bottom": 6,
            "inRange": {"color": ["#e2e8f0", "#a5b4fc", "#4F46E5"]},
        },
        "calendar": calendars,
        "series":   series,
    }).style(f"height: {30 + 150 * len(cal_range) + 60}px;")


def _camera_health(seq: pd.DataFrame) -> None:
    if seq.empty:
        ui.label("No seq_field_analysis data.").classes("text-warning q-mt-sm")
        return
    cam = (
        seq.dropna(subset=["drop_rate"])
        .groupby("camera_name")["drop_rate"]
        .mean().mul(100).round(2).reset_index()
        .sort_values("drop_rate", ascending=True)
    )
    if cam.empty:
        ui.label("No drop-rate data available.").classes("muted q-mt-sm")
        return
    ui.echart({
        "tooltip": {"trigger": "axis", "axisPointer": {"type": "shadow"},
                    "formatter": "{b}: {c}%"},
        "grid":    {"left": 150, "right": 40, "top": 10, "bottom": 30},
        "xAxis":   {"type": "value", "axisLabel": {"formatter": "{value}%"}},
        "yAxis":   {"type": "category", "data": cam["camera_name"].tolist()},
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
            "label": {"show": True, "position": "right", "formatter": "{c}%"},
        }],
    }).style("height: 320px;")


def _recent_activity(mp4: pd.DataFrame) -> None:
    recent = (
        mp4.groupby("recording_date").size().reset_index(name="cases")
        .sort_values("recording_date", ascending=False).head(5)
    )
    if recent.empty:
        ui.label("No recordings yet.").classes("muted q-mt-sm")
        return
    with ui.list().props("dense bordered separator").classes("w-full q-mt-sm"):
        for _, row in recent.iterrows():
            with ui.item():
                with ui.item_section():
                    ui.label(str(row["recording_date"])).classes("text-weight-medium")
                    ui.label(f"{int(row['cases'])} case(s)").classes("text-caption muted")
                with ui.item_section().props("side"):
                    ui.button(
                        icon="open_in_new",
                        on_click=lambda: ui.navigate.to("/database"),
                    ).props("flat dense round").tooltip("Open in Database")


@ui.page("/")
def home() -> None:
    with page_frame("Home"):
        ui.label("ScalpelLab Dashboard").classes("section-h text-h5 text-weight-medium")

        db_path = state.get()
        mp4 = _query(
            db_path,
            "SELECT recording_date, case_no, cameras_count FROM cur_mp4_status_statistics",
        )
        seq = _query(
            db_path,
            "SELECT recording_date, camera_name, drop_rate FROM seq_field_analysis "
            "WHERE drop_rate IS NOT NULL",
        )
        tagged = _query(
            db_path,
            "SELECT DISTINCT recording_date, case_no FROM analysis_information",
        )

        if mp4.empty:
            ui.label(
                "Could not load cur_mp4_status_statistics. "
                "Verify the SQLite path in the left drawer."
            ).classes("text-warning")
            return

        mp4 = mp4.assign(date=pd.to_datetime(mp4["recording_date"], errors="coerce")) \
                 .dropna(subset=["date"])
        mp4["month"] = mp4["date"].dt.to_period("M").astype(str)

        total_recordings = len(mp4)
        surgery_days = mp4["recording_date"].nunique()

        if not seq.empty:
            seq = seq.assign(date=pd.to_datetime(seq["recording_date"], errors="coerce"))
            seq["month"] = seq["date"].dt.to_period("M").astype(str)
            avg_drop = round(seq["drop_rate"].dropna().mean() * 100, 2)
        else:
            avg_drop = None

        if not tagged.empty:
            tagged_days = tagged["recording_date"].nunique()
            coverage_pct = round(tagged_days / surgery_days * 100) if surgery_days else 0
        else:
            tagged_days = 0
            coverage_pct = 0

        last_12 = sorted(mp4["month"].unique())[-12:]
        rec_series = [int((mp4["month"] == m).sum()) for m in last_12]
        days_series = [
            int(mp4[mp4["month"] == m]["recording_date"].nunique()) for m in last_12
        ]
        if not seq.empty:
            drop_series = [
                round(seq.loc[seq["month"] == m, "drop_rate"].mean() * 100, 2)
                if (seq["month"] == m).any() else 0
                for m in last_12
            ]
        else:
            drop_series = []
        if not tagged.empty:
            tagged_d = pd.to_datetime(tagged["recording_date"], errors="coerce")
            tagged_months = tagged_d.dt.to_period("M").astype(str)
            cov_series = [
                round(((tagged_months == m).sum() / max(1, (mp4["month"] == m).sum())) * 100)
                for m in last_12
            ]
        else:
            cov_series = []

        with ui.row().classes("w-full no-wrap gap-4"):
            _kpi_with_spark("TOTAL RECORDINGS", f"{total_recordings:,}", "all time",
                            rec_series, "#4F46E5")
            _kpi_with_spark("SURGERY DAYS", f"{surgery_days:,}", "unique dates",
                            days_series, "#14B8A6")
            _kpi_with_spark(
                "AVG DROP RATE",
                f"{avg_drop:.2f}%" if avg_drop is not None else "—",
                "across all SEQ files",
                drop_series, "#F59E0B",
            )
            _kpi_with_spark(
                "TAGGED COVERAGE", f"{coverage_pct}%",
                f"{tagged_days} of {surgery_days} days",
                cov_series, "#10B981",
            )

        with ui.card().classes("surface-1 w-full q-pa-md"):
            ui.label("Recording activity").classes("text-subtitle1 text-weight-medium")
            ui.label("Cases per day — last two years").classes("text-caption muted")
            _calendar_heatmap(mp4)

        with ui.row().classes("w-full no-wrap gap-4 items-stretch"):
            with ui.card().classes("surface-1 q-pa-md flex-grow"):
                ui.label("Camera health").classes("text-subtitle1 text-weight-medium")
                ui.label("Mean drop rate per camera (lower is better)").classes("text-caption muted")
                _camera_health(seq)

            with ui.card().classes("surface-1 q-pa-md").style("min-width: 360px;"):
                ui.label("Recent activity").classes("text-subtitle1 text-weight-medium")
                ui.label("Last 5 surgery days").classes("text-caption muted")
                _recent_activity(mp4)


def main() -> None:
    ui.run(
        native=True,
        title="ScalpelLab DB",
        reload=False,
        storage_secret="scalpel-lab",
        window_size=(1400, 900),
    )


if __name__ in {"__main__", "__mp_main__"}:
    main()
