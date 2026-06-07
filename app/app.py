"""ScalpelLab Database Manager — NiceGUI native desktop app.

Entry point. Defines the Home dashboard and imports the dashboard and
pipeline sub-pages so their `@ui.page` decorators register routes. The ERD
lives on the Database page (Schema expansion). Run with ``python run_app.py``.
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
from app.config_paths import browse_directory, browse_file, path_status, save_config_paths  # noqa: E402
from app.layout import page_frame  # noqa: E402
from app.pages import (  # noqa: F401,E402
    analysis_import,
    anesthesiology,
    boris,
    database,
    monitor_data,
    mp4,
    nuk_export,
    seq,
    seq_to_mp4,
    update_db,
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


def _configuration_panel() -> None:
    with ui.card().classes("surface-1 w-full q-pa-md"):
        ui.label("Configuration").classes("text-subtitle1 text-weight-medium")
        ui.label(
            "These paths drive dashboard queries and the script pages. Changes are saved "
            "for this app session; use Save to config.py to make them the defaults."
        ).classes("text-caption muted")

        db_input = ui.input("SQLite database", value=state.get()).props("outlined dense").classes("w-full")
        seq_input = ui.input("SEQ root", value=state.get_seq()).props("outlined dense").classes("w-full")
        mp4_input = ui.input("MP4 root", value=state.get_mp4()).props("outlined dense").classes("w-full")
        analyses_input = (
            ui.input("Analyses root", value=state.get_analyses())
            .props("outlined dense")
            .classes("w-full")
        )
        viewer_input = (
            ui.input("NorPix SequenceViewer", value=state.get_norpix_sequence_viewer())
            .props("outlined dense")
            .classes("w-full")
        )
        status_label = ui.label("").classes("text-caption muted")

        def _sync_state() -> None:
            state.set_(str(db_input.value or ""))
            state.set_seq(str(seq_input.value or ""))
            state.set_mp4(str(mp4_input.value or ""))
            state.set_analyses(str(analyses_input.value or ""))
            state.set_norpix_sequence_viewer(str(viewer_input.value or ""))

        db_input.on_value_change(lambda e: state.set_(e.value))
        seq_input.on_value_change(lambda e: state.set_seq(e.value))
        mp4_input.on_value_change(lambda e: state.set_mp4(e.value))
        analyses_input.on_value_change(lambda e: state.set_analyses(e.value))
        viewer_input.on_value_change(lambda e: state.set_norpix_sequence_viewer(e.value))

        with ui.row().classes("items-center gap-2"):
            def pick_db() -> None:
                selected = browse_file(
                    "Select SQLite database",
                    [("SQLite databases", "*.sqlite *.db"), ("All files", "*.*")],
                )
                if selected:
                    db_input.set_value(selected)
                    state.set_(selected)

            def pick_seq() -> None:
                selected = browse_directory("Select SEQ root", seq_input.value or None)
                if selected:
                    seq_input.set_value(selected)
                    state.set_seq(selected)

            def pick_mp4() -> None:
                selected = browse_directory("Select MP4 root", mp4_input.value or None)
                if selected:
                    mp4_input.set_value(selected)
                    state.set_mp4(selected)

            def pick_analyses() -> None:
                selected = browse_directory("Select analyses root", analyses_input.value or None)
                if selected:
                    analyses_input.set_value(selected)
                    state.set_analyses(selected)

            def pick_viewer() -> None:
                selected = browse_file(
                    "Select NorPix SequenceViewer executable",
                    [("Executable files", "*.exe"), ("All files", "*.*")],
                )
                if selected:
                    viewer_input.set_value(selected)
                    state.set_norpix_sequence_viewer(selected)

            def save_config() -> None:
                _sync_state()
                try:
                    save_config_paths(
                        state.get(),
                        state.get_seq(),
                        state.get_mp4(),
                        state.get_analyses(),
                        state.get_norpix_sequence_viewer(),
                    )
                except Exception as exc:
                    ui.notify(f"Save failed: {exc}", type="negative")
                else:
                    ui.notify("config.py updated.", type="positive")

            ui.button("Browse DB", icon="storage", on_click=pick_db).props("outline")
            ui.button("Browse SEQ", icon="folder_open", on_click=pick_seq).props("outline")
            ui.button("Browse MP4", icon="folder_open", on_click=pick_mp4).props("outline")
            ui.button("Browse Analyses", icon="analytics", on_click=pick_analyses).props("outline")
            ui.button("Browse Viewer", icon="movie_creation", on_click=pick_viewer).props("outline")
            ui.button("Save to config.py", icon="save", on_click=save_config).props("color=primary")

        def refresh_status() -> None:
            db_ok, db_msg = path_status(str(db_input.value or ""), "file")
            seq_ok, seq_msg = path_status(str(seq_input.value or ""), "directory")
            mp4_ok, mp4_msg = path_status(str(mp4_input.value or ""), "directory")
            analyses_ok, analyses_msg = path_status(str(analyses_input.value or ""), "directory")
            viewer_ok, viewer_msg = path_status(str(viewer_input.value or ""), "file")
            status_label.set_text(
                f"DB: {db_msg}  |  SEQ: {seq_msg}  |  MP4: {mp4_msg}  |  "
                f"Analyses: {analyses_msg}  |  Viewer: {viewer_msg}"
            )
            status_label.classes(
                remove="text-negative text-positive",
                add=(
                    "text-positive"
                    if db_ok and seq_ok and mp4_ok and analyses_ok and viewer_ok
                    else "text-negative"
                ),
            )

        refresh_status()
        ui.timer(1.0, refresh_status)


@ui.page("/")
def home() -> None:
    with page_frame("Home"):
        ui.label("ScalpelLab Dashboard").classes("section-h text-h5 text-weight-medium")
        _configuration_panel()

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
                "Verify the SQLite path in Configuration."
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
