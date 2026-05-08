"""Advanced SEQ analysis — header fields and IDX integrity.

Deeper view of the per-file metrics produced by analyze_seq_fields.py
(stored in the ``seq_field_analysis`` table). Surfaces dimensions that are
not on the Quality page: header validity, frame size / fps / compression
mix, IDX integrity flags, drop-rate vs file-size relationship, and a
per-file drill-down grid.

For aggregate timing and drift visuals see ``Quality``.
"""

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


CAMERAS = [
    "Cart_Center_2", "Cart_LT_4", "Cart_RT_1", "General_3",
    "Monitor", "Patient_Monitor", "Ventilator_Monitor", "Injection_Port",
]
DRIFT_LIMIT_MS = 10_000        # ±10 s window for the histogram
SANE_DRIFT_MS  = 5_000         # rows outside ±5 s are flagged as drift outliers


def _is_junk(name: str) -> bool:
    return isinstance(name, str) and (name.endswith("_JUNK") or name.endswith("_Junk"))


def _header_summary(df: pd.DataFrame) -> None:
    if df.empty:
        empty_state("No header data.")
        return
    summary = (
        df.assign(resolution=df["width"].astype("Int64").astype(str)
                                + " × "
                                + df["height"].astype("Int64").astype(str))
        .groupby(["resolution", "fps", "compression_fmt"], dropna=False)
        .size().reset_index(name="files")
        .sort_values("files", ascending=False)
    )
    ui.aggrid({
        "defaultColDef": {"sortable": True, "filter": True, "resizable": True},
        "columnDefs": [
            {"field": "resolution",       "headerName": "Resolution"},
            {"field": "fps",              "headerName": "FPS"},
            {"field": "compression_fmt",  "headerName": "Compression"},
            {"field": "files",            "headerName": "Files",
             "type": "numericColumn"},
        ],
        "rowData": summary.to_dict("records"),
        "pagination": True, "paginationPageSize": 10,
    }).classes("ag-theme-balham w-full").style("height: 280px;")


def _drop_vs_size(df: pd.DataFrame) -> None:
    sub = df.dropna(subset=["size_mb", "drop_rate"]).copy()
    if sub.empty:
        empty_state("No drop_rate / size_mb data.")
        return
    sub["drop_pct"] = (sub["drop_rate"] * 100).round(3)
    palette = chart_palette()
    axis = echart_axis_color()
    cams = sorted(sub["camera_name"].dropna().unique().tolist())
    series = []
    for i, cam in enumerate(cams):
        rows = sub[sub["camera_name"] == cam]
        series.append({
            "name": cam,
            "type": "scatter",
            "symbolSize": 8,
            "data": [[float(r.size_mb), float(r.drop_pct)] for r in rows.itertuples()],
            "itemStyle": {"color": palette[i % len(palette)], "opacity": 0.75},
        })
    ui.echart({
        "tooltip": {"trigger": "item",
                    "formatter": "{a}<br/>{c0} MB &rarr; {c1}% drop"},
        "legend": {"data": cams, "top": 0, "type": "scroll",
                   "textStyle": {"color": axis}},
        "grid": base_grid(left=50, right=20, top=40, bottom=50),
        "xAxis": {"type": "value", "name": "Size (MB)",
                  "nameTextStyle": {"color": axis},
                  "axisLabel": {"color": axis}},
        "yAxis": {"type": "value", "name": "Drop rate (%)",
                  "nameTextStyle": {"color": axis},
                  "axisLabel": {"formatter": "{value}%", "color": axis}},
        "series": series,
    }).style("height: 360px;")


def _drift_histogram(df: pd.DataFrame) -> None:
    sub = df.dropna(subset=["time_drift_ms"]).copy()
    sub = sub[sub["time_drift_ms"].between(-DRIFT_LIMIT_MS, DRIFT_LIMIT_MS)]
    if sub.empty:
        empty_state("No in-range drift data.")
        return
    bins = list(range(-DRIFT_LIMIT_MS, DRIFT_LIMIT_MS + 1, 500))
    counts, edges = pd.cut(
        sub["time_drift_ms"], bins=bins, include_lowest=True, right=False
    ).value_counts().sort_index(), bins
    cats = [f"{int(b/1000):+d}s" if b % 1000 == 0 else f"{b}ms"
            for b in edges[:-1]]
    axis = echart_axis_color()
    palette = chart_palette()
    ui.echart({
        "tooltip": base_tooltip("axis"),
        "grid": base_grid(left=50, right=20, top=20, bottom=50),
        "xAxis": {"type": "category", "data": cats,
                  "axisLabel": {"rotate": 45, "color": axis,
                                "interval": max(1, len(cats) // 12)}},
        "yAxis": {"type": "value", "name": "Files",
                  "nameTextStyle": {"color": axis},
                  "axisLabel": {"color": axis}},
        "series": [{
            "type": "bar",
            "data": [int(c) for c in counts.values],
            "itemStyle": {"color": palette[2], "borderRadius": [4, 4, 0, 0]},
        }],
    }).style("height: 320px;")


def _counter_resets_per_camera(df: pd.DataFrame) -> None:
    sub = df.dropna(subset=["n_counter_resets"])
    if sub.empty:
        empty_state("No counter-reset data.")
        return
    cam = (
        sub.groupby("camera_name")["n_counter_resets"]
        .mean().round(2).reset_index()
        .sort_values("n_counter_resets", ascending=True)
    )
    axis = echart_axis_color()
    palette = chart_palette()
    ui.echart({
        "tooltip": base_tooltip("axis"),
        "grid": base_grid(left=160, right=40, top=10, bottom=30),
        "xAxis": {"type": "value", "axisLabel": {"color": axis}},
        "yAxis": {"type": "category",
                  "data": cam["camera_name"].tolist(),
                  "axisLabel": {"color": axis}},
        "series": [{
            "type": "bar",
            "data": cam["n_counter_resets"].tolist(),
            "itemStyle": {"color": palette[5], "borderRadius": [0, 4, 4, 0]},
            "label": {"show": True, "position": "right", "color": axis},
        }],
    }).style("height: 320px;")


def _integrity_grid(df: pd.DataFrame) -> None:
    flagged = df[
        (df.get("has_idx", 1) == 0)
        | (df.get("header_ok", 1) == 0)
        | (df["time_drift_ms"].abs() > SANE_DRIFT_MS)
    ].copy() if "time_drift_ms" in df.columns else df.iloc[0:0]

    if flagged.empty:
        ui.label("No IDX integrity issues detected.").classes("text-positive")
        return

    cols = [c for c in [
        "recording_date", "case_no", "camera_name",
        "has_idx", "header_ok", "time_drift_ms", "drop_rate",
    ] if c in flagged.columns]
    out = flagged[cols].copy()
    if "time_drift_ms" in out.columns:
        out["time_drift_ms"] = out["time_drift_ms"].round(1)
    if "drop_rate" in out.columns:
        out["drop_rate"] = (out["drop_rate"] * 100).round(2)

    ui.aggrid({
        "defaultColDef": {"sortable": True, "filter": True, "resizable": True,
                          "floatingFilter": True},
        "columnDefs": [
            {"field": "recording_date", "headerName": "Date"},
            {"field": "case_no",        "headerName": "Case"},
            {"field": "camera_name",    "headerName": "Camera"},
            {"field": "has_idx",        "headerName": "Has IDX"},
            {"field": "header_ok",      "headerName": "Header OK"},
            {"field": "time_drift_ms",  "headerName": "Drift (ms)",
             "type": "numericColumn"},
            {"field": "drop_rate",      "headerName": "Drop %",
             "type": "numericColumn"},
        ],
        "rowData": out.sort_values(
            ["recording_date", "case_no", "camera_name"],
            ascending=[False, True, True],
        ).to_dict("records"),
        "pagination": True, "paginationPageSize": 15,
    }).classes("ag-theme-balham w-full").style("height: 360px;")


def _file_grid(df: pd.DataFrame) -> None:
    if df.empty:
        empty_state("No rows.")
        return
    keep = [c for c in [
        "recording_date", "case_no", "camera_name",
        "size_mb", "width", "height", "fps", "compression_fmt",
        "allocated_frames", "idx_frames", "dropped_frames", "drop_rate",
        "n_counter_resets", "n_duplicates",
        "actual_duration", "expected_duration",
        "time_drift_ms", "max_time_gap_ms",
        "has_idx", "header_ok",
    ] if c in df.columns]
    out = df[keep].copy()
    if "drop_rate" in out.columns:
        out["drop_rate"] = (out["drop_rate"] * 100).round(2)
    if "time_drift_ms" in out.columns:
        out["time_drift_ms"] = out["time_drift_ms"].round(1)
    if "max_time_gap_ms" in out.columns:
        out["max_time_gap_ms"] = out["max_time_gap_ms"].round(1)

    ui.aggrid({
        "defaultColDef": {"sortable": True, "filter": True, "resizable": True,
                          "floatingFilter": True},
        "columnDefs": [{"field": c, "headerName": c} for c in keep],
        "rowData": out.sort_values(
            ["recording_date", "case_no", "camera_name"],
            ascending=[False, True, True],
        ).to_dict("records"),
        "pagination": True, "paginationPageSize": 25,
    }).classes("ag-theme-balham w-full").style("height: 460px;")


@ui.page("/seq-advanced")
def seq_advanced_page() -> None:
    with page_frame("Advanced SEQ"):
        db_path = state.get()
        ui.label("Advanced SEQ Analysis").classes("section-h text-h5 text-weight-medium")
        ui.label("Per-file SEQ header + IDX metrics from seq_field_analysis.") \
            .classes("text-caption muted")

        df = query_df(
            db_path,
            "SELECT * FROM seq_field_analysis",
        )
        if df.empty:
            ui.label("Could not load seq_field_analysis. "
                     "Run scripts/helpers/analyze_seq_fields.py to populate it.") \
                .classes("text-warning")
            return

        # Filter JUNK by default; expose toggle.
        include_junk = {"v": False}
        view = {"df": df[~df["camera_name"].map(_is_junk)].copy()}

        files_with_idx = int((view["df"].get("has_idx", pd.Series(dtype=int)) == 1).sum())
        files_header_ok = int((view["df"].get("header_ok", pd.Series(dtype=int)) == 1).sum())
        mean_drop_pct = (
            round(view["df"]["drop_rate"].dropna().mean() * 100, 2)
            if "drop_rate" in view["df"].columns
            and view["df"]["drop_rate"].notna().any()
            else None
        )
        files_with_resets = (
            int((view["df"].get("n_counter_resets", pd.Series(dtype=int)) > 0).sum())
            if "n_counter_resets" in view["df"].columns else 0
        )

        with ui.row().classes("w-full no-wrap gap-4"):
            kpi_card("FILES WITH IDX", f"{files_with_idx:,}",
                     f"of {len(view['df']):,}")
            kpi_card("HEADER OK", f"{files_header_ok:,}",
                     f"of {len(view['df']):,}")
            kpi_card(
                "MEAN DROP RATE",
                f"{mean_drop_pct:.2f}%" if mean_drop_pct is not None else "—",
                "across files",
            )
            kpi_card("COUNTER RESETS", f"{files_with_resets:,}",
                     "files with ≥1 reset")

        # JUNK toggle re-renders the page contents below this point.
        body = ui.column().classes("w-full gap-4")

        def render() -> None:
            body.clear()
            with body:
                with ui.row().classes("w-full no-wrap gap-4 items-stretch"):
                    with ui.card().classes("surface-1 q-pa-md flex-grow"):
                        ui.label("Header field summary") \
                            .classes("text-subtitle1 text-weight-medium")
                        ui.label("Distinct (resolution, fps, compression) combos.") \
                            .classes("text-caption muted")
                        _header_summary(view["df"])

                    with ui.card().classes("surface-1 q-pa-md flex-grow"):
                        ui.label("Counter resets per camera") \
                            .classes("text-subtitle1 text-weight-medium")
                        ui.label("Mean n_counter_resets per camera.") \
                            .classes("text-caption muted")
                        _counter_resets_per_camera(view["df"])

                with ui.card().classes("surface-1 w-full q-pa-md"):
                    ui.label("Drop rate vs file size") \
                        .classes("text-subtitle1 text-weight-medium")
                    ui.label("Each point is one SEQ file, colored by camera.") \
                        .classes("text-caption muted")
                    _drop_vs_size(view["df"])

                with ui.card().classes("surface-1 w-full q-pa-md"):
                    ui.label(f"Time drift histogram (clipped to ±{DRIFT_LIMIT_MS // 1000}s)") \
                        .classes("text-subtitle1 text-weight-medium")
                    ui.label("Outliers from corrupt timestamps are excluded.") \
                        .classes("text-caption muted")
                    _drift_histogram(view["df"])

                with ui.card().classes("surface-1 w-full q-pa-md"):
                    ui.label("IDX integrity issues") \
                        .classes("text-subtitle1 text-weight-medium")
                    ui.label(
                        f"Files with has_idx=0, header_ok=0, or |drift| > {SANE_DRIFT_MS // 1000}s."
                    ).classes("text-caption muted")
                    _integrity_grid(view["df"])

                with ui.card().classes("surface-1 w-full q-pa-md"):
                    ui.label("Per-file drill-down") \
                        .classes("text-subtitle1 text-weight-medium")
                    ui.label("All seq_field_analysis rows. Filter columns to drill in.") \
                        .classes("text-caption muted")
                    _file_grid(view["df"])

        def on_toggle(e) -> None:
            include_junk["v"] = bool(e.value)
            view["df"] = (
                df.copy() if include_junk["v"]
                else df[~df["camera_name"].map(_is_junk)].copy()
            )
            render()

        with ui.row().classes("items-center gap-2"):
            ui.switch("Include JUNK rows", value=False, on_change=on_toggle).props("dense")

        render()
