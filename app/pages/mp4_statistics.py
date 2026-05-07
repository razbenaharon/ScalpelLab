"""MP4 Statistics dashboard — KPIs + Plotly charts.

NiceGUI port of ``4_MP4_Statistics.py``. Plotly figure construction is
unchanged; only the rendering call is replaced (``ui.plotly`` instead of
``st.plotly_chart``) and the KPI metrics are rendered as small cards.
"""

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from nicegui import ui

from app import state
from app.layout import page_frame
from app.utils import connect


C_BLUE = "#3A7DD8"
C_TEAL = "#2CA089"
C_ORANGE = "#E8833A"
C_RED = "#D94F4F"
C_GREEN = "#4DAF4A"
C_PURPLE = "#8B5CC2"
C_PINK = "#D94F8F"
BAR_COLORS = [C_RED, C_ORANGE, C_TEAL, C_BLUE, C_PURPLE, "#3A7DD8", C_PINK]

def _theme_colors() -> tuple[str, dict, dict]:
    """Return (axis_color, layout_common, text_style) for the active theme."""
    axis_color = "#e5e7eb" if state.is_dark() else "#1f2937"
    layout_common = dict(
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(color=axis_color),
    )
    text_style = dict(
        textfont=dict(size=14, color=axis_color),
        textposition="outside",
    )
    return axis_color, layout_common, text_style


def kpi(label: str, value, hint: str | None = None) -> None:
    with ui.card().classes("flex-grow text-center q-pa-md"):
        ui.label(str(label)).classes("text-caption text-grey-7")
        ui.label(str(value)).classes("text-h4")
        if hint:
            ui.label(hint).classes("text-caption text-grey-6")


def run_query(db_path: str, sql: str) -> pd.DataFrame:
    with connect(db_path) as conn:
        return pd.read_sql_query(sql, conn)


@ui.page("/mp4-stats")
def mp4_statistics_page() -> None:
    with page_frame("MP4 Statistics"):
        AXIS_COLOR, LAYOUT_COMMON, TEXT_STYLE = _theme_colors()
        db_path = state.get()
        ui.label("MP4 Statistics Dashboard").classes("section-h text-h5 text-weight-medium")

        try:
            df = run_query(
                db_path,
                "SELECT recording_date, case_no, cameras_count FROM cur_mp4_status_statistics",
            )
        except Exception as exc:
            ui.label(f"Could not load view cur_mp4_status_statistics: {exc}").classes("text-negative")
            return

        if df.empty:
            ui.label("No data found in cur_mp4_status_statistics.").classes("text-warning")
            return

        df["date"] = pd.to_datetime(df["recording_date"])
        df["year"] = df["date"].dt.year
        df["month"] = df["date"].dt.to_period("M").astype(str)

        # ── Section 1 — Camera Count Statistics ──────────────────────────────
        ui.separator().classes("q-my-md")
        ui.label("Camera Count Statistics").classes("text-h6")

        total_recordings = len(df)
        surgery_days = df["recording_date"].nunique()
        avg_cameras = round(df["cameras_count"].mean(), 1)
        max_cameras = int(df["cameras_count"].max())
        date_min = df["recording_date"].min()
        date_max = df["recording_date"].max()

        ui.label(f"Surgical camera recording data  •  {date_min} → {date_max}").classes("text-caption")

        with ui.row().classes("w-full q-gutter-md"):
            kpi("Total Recordings", total_recordings, "video files")
            kpi("Surgery Days", surgery_days, "unique dates")
            kpi("Avg Cameras", avg_cameras, "per recording")
            kpi("Max Cameras", max_cameras, "in a single recording")

        cam_dist = (
            df.groupby("cameras_count")
            .size()
            .reset_index(name="count")
            .rename(columns={"cameras_count": "cameras"})
            .sort_values("cameras")
        )
        cam_dist["label"] = cam_dist["cameras"].astype(str) + " cam"

        fig_cam = go.Figure(
            go.Bar(
                x=cam_dist["label"],
                y=cam_dist["count"],
                text=cam_dist["count"],
                marker_color=[BAR_COLORS[i % len(BAR_COLORS)] for i in range(len(cam_dist))],
            )
        )
        fig_cam.update_layout(
            **LAYOUT_COMMON, title="Camera Count Distribution",
            showlegend=False, xaxis_title="", yaxis_title="Count",
            yaxis_range=[0, cam_dist["count"].max() * 1.15],
            xaxis=dict(tickfont=dict(size=14)), height=450,
        )
        fig_cam.update_traces(**TEXT_STYLE)
        ui.plotly(fig_cam).classes("w-full")

        # ── Section 2 — Yearly Breakdown & Cases Per Day ─────────────────────
        ui.separator().classes("q-my-md")
        ui.label("Yearly Breakdown & Cases Per Day").classes("text-h6")

        with ui.row().classes("w-full no-wrap q-gutter-md"):
            with ui.column().classes("flex-grow"):
                yearly_recs = df.groupby("year").size().reset_index(name="Recordings")
                yearly_days = (
                    df.drop_duplicates(subset=["recording_date"])
                    .groupby("year").size().reset_index(name="Surgery Days")
                )
                yearly = yearly_recs.merge(yearly_days, on="year")
                fig_yr = go.Figure()
                fig_yr.add_trace(go.Bar(
                    x=yearly["year"], y=yearly["Recordings"], name="Recordings",
                    marker_color=C_BLUE, text=yearly["Recordings"],
                    textfont=dict(size=14, color=AXIS_COLOR), textposition="outside",
                ))
                fig_yr.add_trace(go.Bar(
                    x=yearly["year"], y=yearly["Surgery Days"], name="Surgery Days",
                    marker_color=C_TEAL, text=yearly["Surgery Days"],
                    textfont=dict(size=14, color=AXIS_COLOR), textposition="outside",
                ))
                fig_yr.update_layout(
                    **LAYOUT_COMMON, title="Yearly Overview", barmode="group",
                    xaxis_title="", yaxis_title="",
                    legend=dict(orientation="h", y=-0.15),
                )
                ui.plotly(fig_yr).classes("w-full")

            with ui.column().classes("flex-grow"):
                cases_per_day = df.groupby("recording_date").size().reset_index(name="cases")
                cpd = cases_per_day.groupby("cases").size().reset_index(name="days")
                cpd["label"] = cpd["cases"].astype(str) + " case" + cpd["cases"].apply(
                    lambda x: "s" if x > 1 else ""
                )
                fig_cpd = go.Figure(
                    go.Bar(
                        x=cpd["label"], y=cpd["days"], text=cpd["days"],
                        marker_color=C_BLUE,
                    )
                )
                fig_cpd.update_layout(
                    **LAYOUT_COMMON, title="Cases Per Surgery Day",
                    xaxis_title="", yaxis_title="Number of Days",
                )
                fig_cpd.update_traces(**TEXT_STYLE)
                ui.plotly(fig_cpd).classes("w-full")

        # ── Section 3 — Timeline Analysis ────────────────────────────────────
        ui.separator().classes("q-my-md")
        ui.label("Timeline Analysis").classes("text-h6")

        monthly_cases = df.groupby("month").size().reset_index(name="Cases")
        fig_tl = go.Figure(
            go.Bar(
                x=monthly_cases["month"], y=monthly_cases["Cases"],
                text=monthly_cases["Cases"], marker_color=C_BLUE,
            )
        )
        fig_tl.update_layout(
            **LAYOUT_COMMON, title="Total Cases Per Month",
            xaxis_title="", yaxis_title="Cases", xaxis_tickangle=-45,
        )
        fig_tl.update_traces(**TEXT_STYLE)
        ui.plotly(fig_tl).classes("w-full")

        # ── Section 4 — Monthly Analysis (dual chart) ────────────────────────
        ui.separator().classes("q-my-md")
        ui.label("Monthly Analysis").classes("text-h6")

        monthly_recs = df.groupby("month").size().reset_index(name="Recordings")
        monthly_days = (
            df.drop_duplicates(subset=["recording_date"])
            .assign(month=lambda d: pd.to_datetime(d["recording_date"]).dt.to_period("M").astype(str))
            .groupby("month").size().reset_index(name="Surgery Days")
        )
        monthly_avg = (
            df.groupby("month")["cameras_count"].mean().round(1).reset_index(name="Avg Cameras")
        )
        monthly = monthly_recs.merge(monthly_days, on="month").merge(monthly_avg, on="month")

        fig_m = make_subplots(
            rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.12,
            subplot_titles=("Monthly Recordings & Surgery Days", "Average Cameras Per Month"),
        )
        fig_m.add_trace(go.Bar(
            x=monthly["month"], y=monthly["Recordings"], name="Recordings", marker_color=C_BLUE,
        ), row=1, col=1)
        fig_m.add_trace(go.Bar(
            x=monthly["month"], y=monthly["Surgery Days"], name="Surgery Days", marker_color=C_TEAL,
        ), row=1, col=1)
        fig_m.add_trace(go.Scatter(
            x=monthly["month"], y=monthly["Avg Cameras"], name="Avg Cameras",
            mode="lines+markers", line=dict(color=C_ORANGE, width=2), marker=dict(size=6),
        ), row=2, col=1)
        fig_m.update_layout(
            **LAYOUT_COMMON, height=650, barmode="group",
            legend=dict(orientation="h", y=-0.08),
        )
        fig_m.update_xaxes(tickangle=-45, row=2, col=1)
        fig_m.update_yaxes(title_text="Count", row=1, col=1)
        fig_m.update_yaxes(title_text="Avg Cameras", row=2, col=1)
        ui.plotly(fig_m).classes("w-full")

        # ── Section 5 — Labeled Data Overview ────────────────────────────────
        ui.separator().classes("q-my-md")
        ui.label("Labeled Data Overview").classes("text-h6")

        try:
            tagged_raw = run_query(
                db_path,
                "SELECT recording_date, case_no FROM analysis_information GROUP BY recording_date, case_no",
            )
        except Exception as exc:
            ui.label(f"Could not load analysis_information: {exc}").classes("text-negative")
            return

        if tagged_raw.empty:
            ui.label("No tagged cases found in analysis_information.").classes("text-warning")
            return

        tagged = tagged_raw.merge(
            df[["recording_date", "case_no", "cameras_count"]],
            on=["recording_date", "case_no"], how="inner",
        )

        total_tagged = len(tagged)
        tagged_days = tagged["recording_date"].nunique()
        avg_cam_tagged = round(tagged["cameras_count"].mean(), 1) if not tagged.empty else 0
        coverage_pct = round(tagged_days / surgery_days * 100) if surgery_days else 0
        tag_date_min = tagged["recording_date"].min()
        tag_date_max = tagged["recording_date"].max()

        ui.label(f"Tagged surgery dates  •  {tag_date_min} → {tag_date_max}").classes("text-caption")

        with ui.row().classes("w-full q-gutter-md"):
            kpi("Total Tagged Cases", total_tagged, "labeled recordings")
            kpi("Tagged Days", tagged_days, "unique dates")
            kpi("Avg Cameras", avg_cam_tagged, "per tagged case")
            kpi("Coverage", f"{coverage_pct}%", f"{tagged_days} of {surgery_days} surgery days")

        with ui.row().classes("w-full no-wrap q-gutter-md"):
            with ui.column().classes("flex-grow"):
                tagged["month"] = pd.to_datetime(tagged["recording_date"]).dt.to_period("M").astype(str)
                monthly_tagged = tagged.groupby("month").size().reset_index(name="Tagged Cases")
                fig_tm = go.Figure(
                    go.Bar(
                        x=monthly_tagged["month"], y=monthly_tagged["Tagged Cases"],
                        text=monthly_tagged["Tagged Cases"], marker_color=C_BLUE,
                    )
                )
                fig_tm.update_layout(
                    **LAYOUT_COMMON, title="Monthly Tagged Cases",
                    xaxis_title="", xaxis_tickangle=-45,
                )
                fig_tm.update_traces(**TEXT_STYLE)
                ui.plotly(fig_tm).classes("w-full")

            with ui.column().classes("flex-grow"):
                tag_cam_dist = (
                    tagged.groupby("cameras_count")
                    .size().reset_index(name="count")
                    .rename(columns={"cameras_count": "cameras"})
                    .sort_values("cameras")
                )
                tag_cam_dist["label"] = tag_cam_dist["cameras"].astype(str) + " cam"
                fig_tc = go.Figure(
                    go.Bar(
                        x=tag_cam_dist["label"], y=tag_cam_dist["count"],
                        text=tag_cam_dist["count"],
                        marker_color=[BAR_COLORS[i % len(BAR_COLORS)] for i in range(len(tag_cam_dist))],
                    )
                )
                fig_tc.update_layout(
                    **LAYOUT_COMMON, title="Camera Count Distribution (Tagged Cases)",
                    showlegend=False, xaxis_title="", yaxis_title="Count",
                )
                fig_tc.update_traces(**TEXT_STYLE)
                ui.plotly(fig_tc).classes("w-full")
