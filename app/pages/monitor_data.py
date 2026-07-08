"""Monitor vitals dashboard for imported Case_Analyses_synced CSV data."""

from __future__ import annotations

import math

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


VITALS = [
    ("hr_bpm", "HR", "bpm"),
    ("spo2_pct", "SpO2", "%"),
    ("pulse_index", "Pulse index", ""),
    ("pr_bpm", "PR", "bpm"),
    ("etco2_mmhg", "EtCO2", "mmHg"),
    ("rr_bpm", "RR", "bpm"),
    ("fico2_mmhg", "FiCO2", "mmHg"),
    ("nibp_sys_mmhg", "NIBP sys", "mmHg"),
    ("nibp_dia_mmhg", "NIBP dia", "mmHg"),
    ("nibp_mean_mmhg", "NIBP mean", "mmHg"),
    ("temp_c", "Temp", "C"),
]

ALERTS = [
    ("hr_alert", "HR"),
    ("spo2_alert", "SpO2"),
    ("pulse_index_alert", "Pulse index"),
    ("pr_alert", "PR"),
    ("etco2_alert", "EtCO2"),
    ("rr_alert", "RR"),
    ("fico2_alert", "FiCO2"),
    ("nibp_sys_alert", "NIBP sys"),
    ("nibp_dia_alert", "NIBP dia"),
    ("nibp_mean_alert", "NIBP mean"),
    ("temp_alert", "Temp"),
]


def _section(title: str, subtitle: str, body_fn) -> None:
    with ui.card().classes("surface-1 w-full q-pa-md"):
        ui.label(title).classes("text-subtitle1 text-weight-medium")
        ui.label(subtitle).classes("text-caption muted")
        body_fn()


def _load_summary(db_path: str) -> pd.DataFrame:
    df = query_df(db_path, "SELECT * FROM monitor_case_summary")
    if df.empty:
        return df
    df["case_key"] = df["recording_date"] + " · Case " + df["case_no"].astype(str)
    return df.sort_values(["recording_date", "case_no"]).reset_index(drop=True)


def _load_case_samples(db_path: str, recording_date: str, case_no: int) -> pd.DataFrame:
    return query_df(
        db_path,
        """
        SELECT *
        FROM monitor_samples
        WHERE recording_date = ?
          AND case_no = ?
        ORDER BY sample_index
        """,
        [recording_date, case_no],
    )


def _downsample(df: pd.DataFrame, max_points: int = 3000) -> pd.DataFrame:
    if len(df) <= max_points:
        return df
    step = max(1, math.ceil(len(df) / max_points))
    return df.iloc[::step].copy()


def _coverage_bar(summary: pd.DataFrame) -> None:
    if summary.empty:
        empty_state("No summary data.")
        return
    total_samples = float(summary["sample_count"].sum() or 0)
    if total_samples == 0:
        empty_state("No monitor samples.")
        return
    labels = []
    values = []
    for col, label, _unit in VITALS:
        count_col = f"{col}_count"
        if count_col in summary.columns:
            labels.append(label)
            values.append(round(float(summary[count_col].sum()) / total_samples * 100, 1))
    axis = echart_axis_color()
    palette = chart_palette()
    ui.echart({
        "tooltip": base_tooltip("axis") | {"valueFormatter": "{value}%"},
        "grid": base_grid(left=140, right=50, top=10, bottom=30),
        "xAxis": {"type": "value", "max": 100,
                  "axisLabel": {"formatter": "{value}%", "color": axis}},
        "yAxis": {"type": "category", "data": labels,
                  "axisLabel": {"color": axis}},
        "series": [{
            "type": "bar",
            "data": values,
            "itemStyle": {"color": palette[1], "borderRadius": [0, 4, 4, 0]},
            "label": {"show": True, "position": "right", "formatter": "{c}%",
                      "color": axis},
        }],
    }).style("height: 360px;")


def _alert_bar(summary: pd.DataFrame) -> None:
    if summary.empty:
        empty_state("No summary data.")
        return
    labels = []
    values = []
    for col, label in ALERTS:
        count_col = f"{col}_count"
        if count_col in summary.columns:
            value = int(summary[count_col].sum())
            if value:
                labels.append(label)
                values.append(value)
    if not labels:
        empty_state("No active alerts were imported.")
        return
    axis = echart_axis_color()
    palette = chart_palette()
    ui.echart({
        "tooltip": base_tooltip("axis"),
        "grid": base_grid(left=140, right=50, top=10, bottom=30),
        "xAxis": {"type": "value", "axisLabel": {"color": axis}},
        "yAxis": {"type": "category", "data": labels,
                  "axisLabel": {"color": axis}},
        "series": [{
            "type": "bar",
            "data": values,
            "itemStyle": {"color": palette[5], "borderRadius": [0, 4, 4, 0]},
            "label": {"show": True, "position": "right", "color": axis},
        }],
    }).style("height: 320px;")


def _line_chart(df: pd.DataFrame, cols: list[tuple[str, str]], title_unit: str) -> None:
    if df.empty:
        empty_state("No samples for this case.")
        return
    chart_df = _downsample(df.dropna(subset=["elapsed_s"]))
    if chart_df.empty:
        empty_state("No timestamped samples for this case.")
        return
    axis = echart_axis_color()
    palette = chart_palette()
    x_data = (chart_df["elapsed_s"].astype(float) / 60.0).round(2).tolist()
    series = []
    for index, (col, label) in enumerate(cols):
        if col not in chart_df.columns or chart_df[col].dropna().empty:
            continue
        values = [
            None if pd.isna(value) else round(float(value), 2)
            for value in chart_df[col].tolist()
        ]
        series.append({
            "name": label,
            "type": "line",
            "showSymbol": False,
            "connectNulls": False,
            "data": values,
            "lineStyle": {"width": 2, "color": palette[index % len(palette)]},
            "itemStyle": {"color": palette[index % len(palette)]},
        })
    if not series:
        empty_state("No values for this vital in the selected case.")
        return
    ui.echart({
        "tooltip": base_tooltip("axis"),
        "legend": {"top": 0, "textStyle": {"color": axis}},
        "grid": base_grid(left=55, right=30, top=40, bottom=45),
        "xAxis": {"type": "category", "name": "min",
                  "nameTextStyle": {"color": axis},
                  "data": x_data,
                  "axisLabel": {"color": axis, "hideOverlap": True}},
        "yAxis": {"type": "value", "name": title_unit,
                  "nameTextStyle": {"color": axis},
                  "axisLabel": {"color": axis}},
        "series": series,
    }).style("height: 320px;")


def _case_table(df: pd.DataFrame) -> None:
    if df.empty:
        empty_state("No samples for this case.")
        return
    cols = [
        "sample_index", "timestamp_text", "elapsed_s",
        "hr_bpm", "spo2_pct", "etco2_mmhg", "rr_bpm",
        "nibp_sys_mmhg", "nibp_dia_mmhg", "nibp_mean_mmhg", "temp_c",
    ]
    use = df[[c for c in cols if c in df.columns]].head(5000).copy()
    if "elapsed_s" in use.columns:
        use["elapsed_s"] = use["elapsed_s"].round(2)
    for col, _label, _unit in VITALS:
        if col in use.columns:
            use[col] = use[col].round(2)
    ui.aggrid({
        "defaultColDef": {
            "sortable": True, "filter": True,
            "resizable": True, "floatingFilter": True,
            "minWidth": 120,
        },
        "columnDefs": [{"field": c} for c in use.columns],
        "rowData": use.to_dict("records"),
        "pagination": True,
        "paginationPageSize": 50,
    }).classes("ag-theme-balham w-full").style("height: 420px;")


def _case_detail(db_path: str, summary: pd.DataFrame, state_box: dict) -> None:
    selected = state_box.get("case")
    if not selected:
        empty_state("No case selected.")
        return
    row = summary[summary["case_key"] == selected].iloc[0]
    samples = _load_case_samples(db_path, str(row.recording_date), int(row.case_no))

    duration_s = 0.0 if pd.isna(row.duration_s) else float(row.duration_s)
    duration_h = round(duration_s / 3600.0, 2)
    with ui.row().classes("w-full no-wrap gap-4"):
        kpi_card("SAMPLES", f"{int(row.sample_count):,}", "selected case")
        kpi_card("DURATION", f"{duration_h:g} h", "derived from frame number")
        kpi_card("SOURCE", str(row.source_file).split("\\")[-1], "monitor CSV")

    with ui.row().classes("w-full no-wrap gap-4 items-stretch"):
        with ui.card().classes("surface-1 q-pa-md flex-grow"):
            ui.label("Heart rate").classes("text-subtitle1 text-weight-medium")
            _line_chart(samples, [("hr_bpm", "HR"), ("pr_bpm", "PR")], "bpm")
        with ui.card().classes("surface-1 q-pa-md flex-grow"):
            ui.label("Oxygenation").classes("text-subtitle1 text-weight-medium")
            _line_chart(samples, [("spo2_pct", "SpO2")], "%")

    with ui.row().classes("w-full no-wrap gap-4 items-stretch"):
        with ui.card().classes("surface-1 q-pa-md flex-grow"):
            ui.label("Ventilation").classes("text-subtitle1 text-weight-medium")
            _line_chart(samples, [("etco2_mmhg", "EtCO2"), ("rr_bpm", "RR")], "mmHg / bpm")
        with ui.card().classes("surface-1 q-pa-md flex-grow"):
            ui.label("Blood pressure").classes("text-subtitle1 text-weight-medium")
            _line_chart(
                samples,
                [
                    ("nibp_sys_mmhg", "Sys"),
                    ("nibp_dia_mmhg", "Dia"),
                    ("nibp_mean_mmhg", "Mean"),
                ],
                "mmHg",
            )

    _section(
        "Temperature",
        "Imported monitor samples for the selected case.",
        lambda: _line_chart(samples, [("temp_c", "Temp")], "C"),
    )
    _section(
        "Raw samples",
        "First 5,000 rows for the selected case.",
        lambda: _case_table(samples),
    )


@ui.page("/monitor-data")
def monitor_data_page() -> None:
    with page_frame("Monitor Data"):
        db_path = state.get()
        ui.label("Monitor Data").classes("section-h text-h5 text-weight-medium")
        ui.label("Imported vitals from Case_Analyses_synced monitor CSV files.") \
            .classes("text-caption muted")

        summary = _load_summary(db_path)
        if summary.empty:
            ui.label(
                "monitor_case_summary is empty or missing. "
                "Run the Analysis Import page first."
            ).classes("text-warning")
            return

        total_cases = len(summary)
        total_samples = int(summary["sample_count"].sum())
        total_hours = round(float(summary["duration_s"].fillna(0).sum()) / 3600.0, 1)
        alert_total = 0
        for col, _label in ALERTS:
            count_col = f"{col}_count"
            if count_col in summary.columns:
                alert_total += int(summary[count_col].sum())

        with ui.row().classes("w-full no-wrap gap-4"):
            kpi_card("CASES", f"{total_cases:,}", "monitor files")
            kpi_card("SAMPLES", f"{total_samples:,}", "time-series rows")
            kpi_card("DURATION", f"{total_hours:,} h", "sum across cases")
            kpi_card("ALERTS", f"{alert_total:,}", "active alert flags")

        with ui.row().classes("w-full no-wrap gap-4 items-stretch"):
            with ui.card().classes("surface-1 q-pa-md flex-grow"):
                ui.label("Vital coverage").classes("text-subtitle1 text-weight-medium")
                ui.label("Non-empty samples per vital across imported cases.") \
                    .classes("text-caption muted")
                _coverage_bar(summary)
            with ui.card().classes("surface-1 q-pa-md flex-grow"):
                ui.label("Alert distribution").classes("text-subtitle1 text-weight-medium")
                ui.label("Counts where alert flags are active.") \
                    .classes("text-caption muted")
                _alert_bar(summary)

        case_choices = summary["case_key"].tolist()
        state_box = {"case": case_choices[0] if case_choices else None}
        detail = ui.column().classes("w-full gap-4")

        def _draw_detail() -> None:
            detail.clear()
            with detail:
                _case_detail(db_path, summary, state_box)

        with ui.card().classes("surface-1 w-full q-pa-md"):
            ui.label("Case").classes("text-subtitle1 text-weight-medium")

            def _on_case(e) -> None:
                state_box["case"] = e.value
                _draw_detail()

            ui.select(
                options=case_choices,
                value=state_box["case"],
                label="Case",
                on_change=_on_case,
            ).props("outlined dense use-input input-debounce=0 hide-selected fill-input") \
             .classes("min-w-[320px]")

        _draw_detail()
