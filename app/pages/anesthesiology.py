"""Anesthesiology dashboard — staff seniority and case load.

Sources: ``cur_seniority`` (computed seniority/attending status) and
``recording_details`` (per-case anesthesiologist assignment).
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


ATTENDING = "A"
RESIDENT = "R"


def _seniority_bar(df: pd.DataFrame) -> None:
    sub = df.dropna(subset=["seniority_month_cur"]).copy()
    if sub.empty:
        empty_state("No seniority data.")
        return
    sub = sub.sort_values("seniority_month_cur", ascending=True)

    palette = chart_palette()
    color_map = {ATTENDING: palette[0], RESIDENT: palette[2]}
    colors = [color_map.get(s, "#94a3b8") for s in sub["anesthetic_attending_cur"]]
    axis = echart_axis_color()

    ui.echart({
        "tooltip": base_tooltip("axis") | {"valueFormatter": "{value} months"},
        "grid": base_grid(left=180, right=60, top=10, bottom=30),
        "xAxis": {"type": "value", "name": "months",
                  "axisLabel": {"color": axis}},
        "yAxis": {"type": "category", "data": sub["name"].tolist(),
                  "axisLabel": {"color": axis}},
        "series": [{
            "type": "bar",
            "data": [
                {"value": int(v), "itemStyle": {"color": c, "borderRadius": [0, 4, 4, 0]}}
                for v, c in zip(sub["seniority_month_cur"], colors)
            ],
            "label": {"show": True, "position": "right", "formatter": "{c}m",
                      "color": axis},
        }],
    }).style(f"height: {max(280, 28 * len(sub))}px;")


def _attending_donut(df: pd.DataFrame) -> None:
    counts = df["anesthetic_attending_cur"].value_counts(dropna=True).to_dict()
    a = int(counts.get(ATTENDING, 0))
    r = int(counts.get(RESIDENT, 0))
    if a + r == 0:
        empty_state("No attending/resident data.")
        return
    palette = chart_palette()
    axis = echart_axis_color()
    ui.echart({
        "tooltip": {"trigger": "item", "formatter": "{b}: {c} ({d}%)"},
        "legend": {"top": 0, "textStyle": {"color": axis}},
        "series": [{
            "type": "pie", "radius": ["55%", "80%"],
            "data": [
                {"value": a, "name": "Attending",
                 "itemStyle": {"color": palette[0]}},
                {"value": r, "name": "Resident",
                 "itemStyle": {"color": palette[2]}},
            ],
            "label": {"color": axis, "formatter": "{b}\n{c}"},
        }],
    }).style("height: 280px;")


def _cases_per_anesthesiologist(db_path: str) -> None:
    df = query_df(
        db_path,
        """
        SELECT a.name, a.anesthetic_attending_cur AS status,
               COUNT(*) AS cases
        FROM recording_details r
        JOIN cur_seniority a ON a.anesthesiology_key = r.anesthesiology_key
        GROUP BY a.anesthesiology_key
        ORDER BY cases ASC
        """,
    )
    if df.empty:
        empty_state("No cases linked to anesthesiology yet.")
        return

    palette = chart_palette()
    color_map = {ATTENDING: palette[0], RESIDENT: palette[2]}
    colors = [color_map.get(s, "#94a3b8") for s in df["status"]]
    axis = echart_axis_color()

    ui.echart({
        "tooltip": base_tooltip("axis") | {"valueFormatter": "{value} cases"},
        "grid": base_grid(left=180, right=60, top=10, bottom=30),
        "xAxis": {"type": "value", "axisLabel": {"color": axis}},
        "yAxis": {"type": "category", "data": df["name"].tolist(),
                  "axisLabel": {"color": axis}},
        "series": [{
            "type": "bar",
            "data": [
                {"value": int(v), "itemStyle": {"color": c, "borderRadius": [0, 4, 4, 0]}}
                for v, c in zip(df["cases"], colors)
            ],
            "label": {"show": True, "position": "right", "color": axis},
        }],
    }).style(f"height: {max(280, 28 * len(df))}px;")


@ui.page("/anesthesiology")
def anesthesiology_page() -> None:
    with page_frame("Anesthesiology"):
        db_path = state.get()
        ui.label("Anesthesiology Dashboard").classes("section-h text-h5 text-weight-medium")
        ui.label("Staff seniority and case load (cur_seniority + recording_details).") \
            .classes("text-caption muted")

        df = query_df(
            db_path,
            "SELECT anesthesiology_key, name, code, "
            "       anesthesiology_start_date, seniority_month_cur, "
            "       anesthetic_attending_cur "
            "FROM cur_seniority",
        )

        if df.empty:
            ui.label("No rows in cur_seniority.").classes("text-warning")
            return

        total = len(df)
        attendings = int((df["anesthetic_attending_cur"] == ATTENDING).sum())
        residents = int((df["anesthetic_attending_cur"] == RESIDENT).sum())

        # ── KPIs ────────────────────────────────────────────────────────────
        with ui.row().classes("w-full no-wrap gap-4"):
            kpi_card("STAFF", f"{total:,}", "in roster")
            kpi_card("ATTENDINGS", f"{attendings:,}", "≥ 60 months")
            kpi_card("RESIDENTS", f"{residents:,}", "< 60 months")

        # ── Seniority + donut ───────────────────────────────────────────────
        with ui.row().classes("w-full no-wrap gap-4 items-stretch"):
            with ui.card().classes("surface-1 q-pa-md flex-grow"):
                ui.label("Seniority (months)").classes("text-subtitle1 text-weight-medium")
                ui.label("Months since anesthesiology_start_date — colored by status.") \
                    .classes("text-caption muted")
                _seniority_bar(df)
            with ui.card().classes("surface-1 q-pa-md").style("min-width: 320px;"):
                ui.label("Attending vs Resident").classes("text-subtitle1 text-weight-medium")
                ui.label("Current roster split.").classes("text-caption muted")
                _attending_donut(df)

        # ── Cases per anesthesiologist ──────────────────────────────────────
        with ui.card().classes("surface-1 w-full q-pa-md"):
            ui.label("Cases per anesthesiologist") \
                .classes("text-subtitle1 text-weight-medium")
            ui.label("Joined from recording_details — total cases each staff member has signed.") \
                .classes("text-caption muted")
            _cases_per_anesthesiologist(db_path)
