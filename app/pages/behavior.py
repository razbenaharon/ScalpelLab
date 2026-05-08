"""Behavior dashboard — visualizes BORIS labelled events.

Source: ``cur_boris_intervals`` (paired START/STOP intervals) and
``boris_events`` for the unpaired-event diagnostics.
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


def _top_behaviors_chart(df: pd.DataFrame) -> None:
    paired = df[df["pairing_status"] == "PAIRED"].dropna(subset=["duration_s"])
    if paired.empty:
        empty_state("No paired intervals.")
        return
    agg = (
        paired.groupby("behavior")["duration_s"]
        .sum().div(60).round(1).reset_index()
        .sort_values("duration_s", ascending=True).tail(15)
    )
    axis = echart_axis_color()
    palette = chart_palette()
    ui.echart({
        "tooltip": base_tooltip("axis") | {"valueFormatter": "{value} min"},
        "grid": base_grid(left=180, right=40, top=10, bottom=30),
        "xAxis": {"type": "value", "name": "minutes",
                  "axisLabel": {"color": axis}},
        "yAxis": {"type": "category", "data": agg["behavior"].tolist(),
                  "axisLabel": {"color": axis}},
        "series": [{
            "type": "bar", "data": agg["duration_s"].tolist(),
            "itemStyle": {"color": palette[0], "borderRadius": [0, 4, 4, 0]},
            "label": {"show": True, "position": "right", "color": axis,
                      "formatter": "{c}m"},
        }],
    }).style("height: 420px;")


def _behavior_by_subject(df: pd.DataFrame) -> None:
    paired = df[df["pairing_status"] == "PAIRED"]
    if paired.empty:
        empty_state("No paired intervals.")
        return
    pivot = (
        paired.groupby(["subject", "behavior"]).size().unstack(fill_value=0)
    )
    # Cap to top behaviors so the legend stays readable.
    top_behaviors = paired["behavior"].value_counts().head(10).index.tolist()
    pivot = pivot.reindex(columns=top_behaviors, fill_value=0)

    axis = echart_axis_color()
    palette = chart_palette()
    series = []
    for i, beh in enumerate(top_behaviors):
        series.append({
            "name": beh, "type": "bar", "stack": "total",
            "data": pivot[beh].tolist(),
            "itemStyle": {"color": palette[i % len(palette)]},
            "emphasis": {"focus": "series"},
        })
    ui.echart({
        "tooltip": base_tooltip("axis"),
        "legend": {"top": 0, "type": "scroll", "textStyle": {"color": axis}},
        "grid": base_grid(left=60, right=20, top=40, bottom=70),
        "xAxis": {"type": "category", "data": pivot.index.tolist(),
                  "axisLabel": {"rotate": 30, "color": axis}},
        "yAxis": {"type": "value", "axisLabel": {"color": axis}},
        "series": series,
    }).style("height: 360px;")


def _case_gantt(df: pd.DataFrame, key: str) -> None:
    sub = df[
        (df["pairing_status"] == "PAIRED")
        & (df["case_key"] == key)
    ].dropna(subset=["start_time_s", "end_time_s"])
    if sub.empty:
        ui.label("No paired intervals for this case.").classes("text-warning")
        return

    subjects = sub["subject"].fillna("(none)").unique().tolist()
    subj_idx = {s: i for i, s in enumerate(subjects)}
    palette = chart_palette()
    behaviors = sorted(sub["behavior"].dropna().unique().tolist())
    beh_color = {b: palette[i % len(palette)] for i, b in enumerate(behaviors)}

    data = []
    for r in sub.itertuples():
        s = r.subject if pd.notna(r.subject) else "(none)"
        data.append({
            "name": r.behavior,
            "value": [
                subj_idx[s],
                float(r.start_time_s),
                float(r.end_time_s),
                float(r.duration_s),
                r.behavior,
            ],
            "itemStyle": {"color": beh_color.get(r.behavior, "#64748b")},
        })

    axis = echart_axis_color()
    ui.echart({
        "tooltip": {
            "formatter": (
                "{b}<br/>start: {@[1]}s<br/>end: {@[2]}s<br/>duration: {@[3]}s"
            ),
        },
        "grid": base_grid(left=130, right=30, top=20, bottom=40),
        "xAxis": {"type": "value", "name": "time (s)",
                  "axisLabel": {"color": axis}},
        "yAxis": {"type": "category", "data": subjects,
                  "axisLabel": {"color": axis}},
        "series": [{
            "type": "custom",
            "renderItem": {
                "type": "function",
                "function": (
                    "function (params, api) {"
                    "  var cat = api.value(0);"
                    "  var start = api.coord([api.value(1), cat]);"
                    "  var end   = api.coord([api.value(2), cat]);"
                    "  var height = api.size([0, 1])[1] * 0.55;"
                    "  return {"
                    "    type: 'rect',"
                    "    shape: {"
                    "      x: start[0],"
                    "      y: start[1] - height / 2,"
                    "      width: Math.max(2, end[0] - start[0]),"
                    "      height: height"
                    "    },"
                    "    style: api.style()"
                    "  };"
                    "}"
                ),
            },
            "encode": {"x": [1, 2], "y": 0, "tooltip": [0, 1, 2, 3]},
            "data": data,
        }],
    }).style("height: 380px;")


def _pairing_health_grid(df: pd.DataFrame) -> None:
    bad = df[df["pairing_status"].isin(["MISSING_STOP", "ERROR_DOUBLE_START"])]
    if bad.empty:
        ui.label("No pairing errors. BORIS labels look clean.").classes("text-positive")
        return
    rows = [{
        "recording_date": r.recording_date,
        "case_no": int(r.case_no) if pd.notna(r.case_no) else None,
        "subject": r.subject,
        "behavior": r.behavior,
        "start_time_s": round(float(r.start_time_s), 2)
                        if pd.notna(r.start_time_s) else None,
        "pairing_status": r.pairing_status,
        "source_file": r.source_file,
    } for r in bad.itertuples()]
    ui.aggrid({
        "defaultColDef": {"sortable": True, "filter": True, "resizable": True,
                          "floatingFilter": True},
        "columnDefs": [
            {"field": "recording_date"}, {"field": "case_no"},
            {"field": "subject"}, {"field": "behavior"},
            {"field": "start_time_s"}, {"field": "pairing_status"},
            {"field": "source_file", "flex": 1},
        ],
        "rowData": rows,
        "pagination": True, "paginationPageSize": 10,
    }).classes("ag-theme-balham w-full").style("height: 320px")


@ui.page("/behavior")
def behavior_page() -> None:
    with page_frame("Behavior"):
        db_path = state.get()
        ui.label("Behavior Dashboard").classes("section-h text-h5 text-weight-medium")
        ui.label("BORIS labelled intervals — durations, subjects, pairing health.") \
            .classes("text-caption muted")

        df = query_df(
            db_path,
            "SELECT recording_date, case_no, subject, behavior, modifier_1, modifier_2, "
            "       modifier_3, start_time_s, end_time_s, duration_s, pairing_status, "
            "       source_file "
            "FROM cur_boris_intervals",
        )
        if df.empty:
            ui.label("No rows in cur_boris_intervals. Import BORIS labels first.") \
                .classes("text-warning")
            return

        df["case_key"] = df["recording_date"].astype(str) + " — case " \
                         + df["case_no"].astype("Int64").astype(str)

        paired = df[df["pairing_status"] == "PAIRED"]
        tagged_cases = df.groupby(["recording_date", "case_no"]).ngroups
        n_paired = len(paired)
        n_missing = int((df["pairing_status"] == "MISSING_STOP").sum())
        n_double = int((df["pairing_status"] == "ERROR_DOUBLE_START").sum())

        # ── KPIs ────────────────────────────────────────────────────────────
        with ui.row().classes("w-full no-wrap gap-4"):
            kpi_card("TAGGED CASES", f"{tagged_cases:,}", "with at least one event")
            kpi_card("PAIRED INTERVALS", f"{n_paired:,}", "complete START→STOP")
            kpi_card("MISSING STOP", f"{n_missing:,}", "unterminated starts")
            kpi_card("DOUBLE START", f"{n_double:,}", "back-to-back starts")

        # ── Top behaviors + by subject ──────────────────────────────────────
        with ui.row().classes("w-full no-wrap gap-4 items-stretch"):
            with ui.card().classes("surface-1 q-pa-md flex-grow"):
                ui.label("Top behaviors by total duration") \
                    .classes("text-subtitle1 text-weight-medium")
                ui.label("Sum of paired interval durations.") \
                    .classes("text-caption muted")
                _top_behaviors_chart(df)
            with ui.card().classes("surface-1 q-pa-md flex-grow"):
                ui.label("Behavior counts by subject") \
                    .classes("text-subtitle1 text-weight-medium")
                ui.label("Stacked count of paired intervals per subject.") \
                    .classes("text-caption muted")
                _behavior_by_subject(df)

        # ── Per-case Gantt ──────────────────────────────────────────────────
        with ui.card().classes("surface-1 w-full q-pa-md"):
            ui.label("Case timeline (Gantt)").classes("text-subtitle1 text-weight-medium")
            ui.label("Pick a labelled case to see paired intervals per subject.") \
                .classes("text-caption muted")

            keys = sorted(paired["case_key"].dropna().unique().tolist(), reverse=True)
            if not keys:
                ui.label("No paired cases to plot.").classes("text-warning")
            else:
                ctx = {"key": keys[0]}
                gantt_slot = ui.column().classes("w-full")

                def render() -> None:
                    gantt_slot.clear()
                    with gantt_slot:
                        _case_gantt(df, ctx["key"])

                def on_change(e) -> None:
                    ctx["key"] = e.value
                    render()

                ui.select(options=keys, value=keys[0], label="Case",
                          on_change=on_change).classes("w-full")
                render()

        # ── Pairing health ──────────────────────────────────────────────────
        with ui.card().classes("surface-1 w-full q-pa-md"):
            ui.label("Pairing health").classes("text-subtitle1 text-weight-medium")
            ui.label("Unpaired or doubled events — these need a manual fix.") \
                .classes("text-caption muted")
            _pairing_health_grid(df)
