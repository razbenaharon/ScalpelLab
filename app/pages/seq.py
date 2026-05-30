"""SEQ dashboard — inventory + time-drift analysis.

Combines the raw SEQ inventory (presence, size, JUNK summary, coverage)
with per-file time-drift dashboards from ``seq_enriched``. Files are bucketed
by ``|time_drift_ms|`` (see ``DRIFT_SMALL_MS`` / ``DRIFT_MEDIUM_MS``), so the
corrupted-IDX outliers (~2e12 ms) simply fall into the "Large" bucket rather
than skewing a continuous axis.

Sources: ``seq_status``, ``seq_enriched``.
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

DRIFT_SMALL_MS = 1_000         # |drift| <= 1 s
DRIFT_MEDIUM_MS = 5_000        # 1 s < |drift| <= 5 s ; > 5 s is "large"


def _is_junk(name: str) -> bool:
    return isinstance(name, str) and (name.endswith("_JUNK") or name.endswith("_Junk"))


# ---------------------------------------------------------------------------
# Inventory charts (seq_status)
# ---------------------------------------------------------------------------

def _coverage_heatmap(seq: pd.DataFrame) -> None:
    if seq.empty:
        empty_state("No SEQ presence data.")
        return
    df = seq.copy()
    df["present"] = df["size_mb"].notna().astype(int) if "size_mb" in df.columns else 1
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
            "name": "SEQ", "type": "heatmap", "data": data,
            "progressive": 2000,
            "emphasis": {"itemStyle": {"borderColor": "#1e293b", "borderWidth": 1}},
        }],
    }).style("height: 420px;")


def _size_per_camera(clean: pd.DataFrame) -> None:
    if clean.empty or "size_mb" not in clean.columns:
        empty_state("No size data.")
        return
    sizes = (
        clean.dropna(subset=["size_mb"])
        .groupby("camera_name")["size_mb"]
        .sum().div(1024).round(1).reset_index(name="size_gb")
        .sort_values("size_gb", ascending=True)
    )
    if sizes.empty:
        empty_state("No size data.")
        return
    axis = echart_axis_color()
    palette = chart_palette()
    ui.echart({
        "tooltip": base_tooltip("axis") | {"valueFormatter": "{value} GB"},
        "grid": base_grid(left=160, right=50, top=10, bottom=30),
        "xAxis": {"type": "value",
                  "axisLabel": {"formatter": "{value} GB", "color": axis}},
        "yAxis": {"type": "category",
                  "data": sizes["camera_name"].tolist(),
                  "axisLabel": {"color": axis}},
        "series": [{
            "type": "bar",
            "data": sizes["size_gb"].tolist(),
            "itemStyle": {"color": palette[1], "borderRadius": [0, 4, 4, 0]},
            "label": {"show": True, "position": "right",
                      "formatter": "{c} GB", "color": axis},
        }],
    }).style("height: 320px;")


# ---------------------------------------------------------------------------
# Drift charts (seq_enriched)
# ---------------------------------------------------------------------------

def _drift_buckets_per_camera(df: pd.DataFrame) -> None:
    sub = df.dropna(subset=["time_drift_ms"]).copy()
    if sub.empty:
        empty_state("No drift data.")
        return
    abs_drift = sub["time_drift_ms"].abs()
    sub["bucket"] = pd.cut(
        abs_drift,
        bins=[-1, DRIFT_SMALL_MS, DRIFT_MEDIUM_MS, float("inf")],
        labels=["Small", "Medium", "Large"],
    )
    counts = (
        sub.groupby(["camera_name", "bucket"], observed=False)
        .size().unstack(fill_value=0)
    )
    for b in ("Small", "Medium", "Large"):
        if b not in counts.columns:
            counts[b] = 0
    counts = counts[["Small", "Medium", "Large"]]
    counts["__total"] = counts.sum(axis=1)
    counts = counts.sort_values("__total", ascending=True)
    cams = counts.index.tolist()
    axis = echart_axis_color()
    bucket_labels = {
        "Small":  f"Small (|drift| ≤ {DRIFT_SMALL_MS // 1000}s)",
        "Medium": f"Medium ({DRIFT_SMALL_MS // 1000}s < |drift| ≤ {DRIFT_MEDIUM_MS // 1000}s)",
        "Large":  f"Large (|drift| > {DRIFT_MEDIUM_MS // 1000}s)",
    }
    colors = {"Small": "#10b981", "Medium": "#f59e0b", "Large": "#ef4444"}
    series = [
        {
            "name": bucket_labels[b],
            "type": "bar",
            "stack": "drift",
            "data": [int(counts.loc[c, b]) for c in cams],
            "itemStyle": {"color": colors[b]},
            "label": {"show": True, "color": "#fff", "formatter": "{c}"},
        }
        for b in ("Small", "Medium", "Large")
    ]
    ui.echart({
        "tooltip": base_tooltip("axis"),
        "legend": {"data": [bucket_labels[b] for b in ("Small", "Medium", "Large")],
                   "top": 0, "textStyle": {"color": axis}},
        "grid": base_grid(left=160, right=40, top=40, bottom=30),
        "xAxis": {"type": "value", "name": "Files",
                  "nameTextStyle": {"color": axis},
                  "axisLabel": {"color": axis}},
        "yAxis": {"type": "category", "data": cams,
                  "axisLabel": {"color": axis}},
        "series": series,
    }).style("height: 380px;")


# ---------------------------------------------------------------------------
# Page
# ---------------------------------------------------------------------------

def _section(title: str, subtitle: str, body_fn) -> None:
    with ui.card().classes("surface-1 w-full q-pa-md"):
        ui.label(title).classes("text-subtitle1 text-weight-medium")
        ui.label(subtitle).classes("text-caption muted")
        body_fn()


@ui.page("/seq")
def seq_page() -> None:
    with page_frame("SEQ"):
        db_path = state.get()
        ui.label("SEQ Inventory & Time Drift").classes("section-h text-h5 text-weight-medium")
        ui.label("Raw SEQ files plus per-file time-drift analysis.") \
            .classes("text-caption muted")

        seq = query_df(
            db_path,
            "SELECT recording_date, case_no, camera_name, size_mb FROM seq_status",
        )
        if seq.empty:
            ui.label("Could not load seq_status. Check the SQLite path in the left drawer.") \
                .classes("text-warning")
            return

        junk_mask = seq["camera_name"].map(_is_junk)
        clean = seq[~junk_mask].copy()
        junk = seq[junk_mask].copy()

        total_files = len(clean)
        total_size_gb = round(
            (clean["size_mb"].dropna().sum() / 1024) if "size_mb" in clean.columns else 0,
            1,
        )
        junk_count = len(junk)

        # ── Inventory KPIs ────────────────────────────────────────────────
        with ui.row().classes("w-full no-wrap gap-4"):
            kpi_card("SEQ FILES", f"{total_files:,}", "JUNK excluded")
            kpi_card("TOTAL SIZE", f"{total_size_gb:,} GB", "on disk")
            kpi_card("JUNK ROWS", f"{junk_count:,}", "undersized / failed")

        with ui.card().classes("surface-1 w-full q-pa-md"):
            ui.label("Total size per camera").classes("text-subtitle1 text-weight-medium")
            ui.label("Sum of size_mb, in GB.").classes("text-caption muted")
            _size_per_camera(clean)

        with ui.card().classes("surface-1 w-full q-pa-md"):
            ui.label("Camera × date coverage").classes("text-subtitle1 text-weight-medium")
            ui.label("One column per recording day. Green = SEQ present, red = missing.") \
                .classes("text-caption muted")
            _coverage_heatmap(clean)

        # ── Time-drift section (seq_enriched) ─────────────────────────────
        enriched = query_df(db_path, "SELECT * FROM seq_enriched")
        if enriched.empty:
            ui.label(
                "Could not load seq_enriched. "
                "Run scripts/helpers/analyze_seq_fields.py to populate it."
            ).classes("text-warning")
            return

        ui.label("Time-Drift Analysis").classes("section-h text-h5 text-weight-medium q-mt-md")
        ui.label("All dashboards below focus on the time_drift_ms column.") \
            .classes("text-caption muted")

        all_dates = sorted(enriched["recording_date"].dropna().unique().tolist())
        state_box = {
            "from": all_dates[0] if all_dates else None,
            "to": all_dates[-1] if all_dates else None,
        }
        view = {"df": enriched.copy()}

        def _apply() -> None:
            sub = enriched
            if state_box["from"]:
                sub = sub[sub["recording_date"] >= state_box["from"]]
            if state_box["to"]:
                sub = sub[sub["recording_date"] <= state_box["to"]]
            view["df"] = sub.copy()

        _apply()

        body = ui.column().classes("w-full gap-4")

        def _draw_body() -> None:
            body.clear()
            with body:
                _section(
                    "Drift buckets per camera",
                    f"File counts by |drift|: small ≤ {DRIFT_SMALL_MS // 1000}s, "
                    f"medium ≤ {DRIFT_MEDIUM_MS // 1000}s, large > {DRIFT_MEDIUM_MS // 1000}s.",
                    lambda: _drift_buckets_per_camera(view["df"]),
                )

        def _rerender() -> None:
            _apply()
            _draw_body()

        with ui.row().classes("items-center gap-4 q-mt-sm"):
            with ui.input("From").props("dense readonly").classes("min-w-[140px]") as from_in:
                from_in.value = state_box["from"] or ""
                with from_in.add_slot("append"):
                    ui.icon("event").classes("cursor-pointer")
                with ui.menu().props("no-parent-event") as from_menu:
                    ui.date(
                        value=state_box["from"],
                        on_change=lambda e: (
                            state_box.__setitem__("from", e.value or None),
                            from_in.set_value(e.value or ""),
                            _rerender(),
                        ),
                    )
                from_in.on("click", lambda _: from_menu.open())

            with ui.input("To").props("dense readonly").classes("min-w-[140px]") as to_in:
                to_in.value = state_box["to"] or ""
                with to_in.add_slot("append"):
                    ui.icon("event").classes("cursor-pointer")
                with ui.menu().props("no-parent-event") as to_menu:
                    ui.date(
                        value=state_box["to"],
                        on_change=lambda e: (
                            state_box.__setitem__("to", e.value or None),
                            to_in.set_value(e.value or ""),
                            _rerender(),
                        ),
                    )
                to_in.on("click", lambda _: to_menu.open())

            def _reset_dates() -> None:
                state_box["from"] = all_dates[0] if all_dates else None
                state_box["to"] = all_dates[-1] if all_dates else None
                from_in.set_value(state_box["from"] or "")
                to_in.set_value(state_box["to"] or "")
                _rerender()

            ui.button("Reset", on_click=_reset_dates).props("dense flat")

        _draw_body()
