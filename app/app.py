"""ScalpelLab Database Manager — NiceGUI native desktop app.

Entry point. Defines the home dashboard and imports the four sub-pages
so their `@ui.page` decorators register routes. The ERD lives on the
Database page (Schema expansion). Run with ``python run_app.py``.
"""

import os
import sys
from datetime import date as dt_date
from pathlib import Path

import pandas as pd
from nicegui import app as ng_app, native, ui

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _docs_dir() -> Path:
    """Location of the bundled docs/ folder (dev tree or PyInstaller _MEIPASS)."""
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS) / "docs"
    return Path(__file__).resolve().parent.parent / "docs"


ng_app.add_static_files("/static", str(_docs_dir()))

from app import state  # noqa: E402
from app.charts import kpi_with_spark as _kpi_with_spark, query_df as _query  # noqa: E402
from app.layout import page_frame  # noqa: E402
from app.pages import (  # noqa: F401,E402
    anesthesiology,
    boris,
    database,
    mp4,
    seq,
)


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


@ui.page("/")
def home() -> None:
    with page_frame("Home"):
        ui.label("ScalpelLab Dashboard").classes("section-h text-h5 text-weight-medium")

        db_path = state.get()
        mp4 = _query(
            db_path,
            "SELECT recording_date, case_no, cameras_count FROM cur_mp4_status_statistics",
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
                "TAGGED COVERAGE", f"{coverage_pct}%",
                f"{tagged_days} of {surgery_days} days",
                cov_series, "#10B981",
            )

        with ui.card().classes("surface-1 w-full q-pa-md"):
            ui.label("Recording activity").classes("text-subtitle1 text-weight-medium")
            ui.label("Cases per day — last two years").classes("text-caption muted")
            _calendar_heatmap(mp4)


def main() -> None:
    ui.run(
        native=True,
        title="ScalpelLab DB",
        reload=False,
        port=native.find_open_port(),
        storage_secret="scalpel-lab",
        window_size=(1400, 900),
    )


if __name__ in {"__main__", "__mp_main__"}:
    main()
