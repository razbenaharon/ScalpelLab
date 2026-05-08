"""Shared chart and KPI helpers for the NiceGUI dashboard pages.

All visualization pages render KPI strips, sparklines, and ECharts panels
through the helpers here so the visual idiom stays consistent across the
Coverage, Quality, Behavior, and Anesthesiology pages (and the Home dashboard).
"""

from __future__ import annotations

from typing import Iterable, Sequence

import pandas as pd
from nicegui import ui

from . import state
from .theme import CHART_SEQ
from .utils import connect


def query_df(db_path: str, sql: str, params: Sequence | None = None) -> pd.DataFrame:
    try:
        with connect(db_path) as conn:
            if params is None:
                return pd.read_sql_query(sql, conn)
            return pd.read_sql_query(sql, conn, params=tuple(params))
    except Exception:
        return pd.DataFrame()


def chart_palette() -> list[str]:
    return list(CHART_SEQ)


def echart_axis_color() -> str:
    return "#e2e8f0" if state.is_dark() else "#334155"


def base_grid(left: int = 60, right: int = 30, top: int = 30, bottom: int = 40) -> dict:
    return {"left": left, "right": right, "top": top, "bottom": bottom, "containLabel": True}


def base_tooltip(trigger: str = "axis") -> dict:
    return {"trigger": trigger, "axisPointer": {"type": "shadow"}}


def kpi_card(label: str, value, hint: str | None = None) -> None:
    with ui.card().classes("kpi-card surface-1 q-pa-md flex-grow").style("min-width: 180px;"):
        ui.label(label).classes("text-caption muted").style("letter-spacing: 1px;")
        ui.label(str(value)).classes("text-h4 text-weight-bold")
        if hint:
            ui.label(hint).classes("text-caption muted")


def kpi_with_spark(label: str, value, hint: str, series: Iterable[float], color: str) -> None:
    series = list(series)
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


def section_card(title: str, subtitle: str | None = None):
    """Context manager: surface-1 card with a heading + optional subtitle."""
    card = ui.card().classes("surface-1 w-full q-pa-md")
    with card:
        ui.label(title).classes("text-subtitle1 text-weight-medium")
        if subtitle:
            ui.label(subtitle).classes("text-caption muted")
    return card


def empty_state(message: str) -> None:
    ui.label(message).classes("text-warning q-mt-sm")
