"""MP4 Statistics Dashboard - Comprehensive Recording Analytics.

This Streamlit page recreates the mp4_statistics.pdf report as an interactive
dashboard, providing visual analytics on surgical camera recordings.

Data Sources:
    - **cur_mp4_status_statistics** (view): Recording-level camera counts
      (recording_date, case_no, cameras_count) — 168 logical recordings
    - **analysis_information** (table): Tagged/labeled case records
      (recording_date, case_no, label_by) — 82 labeled recordings

Sections:
    1. Camera Count Statistics — KPIs and camera-count distribution
    2. Yearly Breakdown & Cases Per Day
    3. Timeline Analysis — monthly case totals
    4. Monthly Analysis — recordings vs surgery days, avg cameras
    5. Labeled Data Overview — tagged case statistics and coverage

Navigation:
    Access via sidebar: Pages → 4_MP4 Statistics
"""

import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import sys
import os

sys.path.append(os.path.dirname(os.path.dirname(__file__)))
from utils import connect

st.header("📊 MP4 Statistics Dashboard")

db_path = st.session_state.get("db_path")
if not db_path:
    st.warning(
        "No database path set. Open the main page and set the DB path in the sidebar."
    )
    st.stop()


# ── Helper ───────────────────────────────────────────────────────────────────
def run_query(sql: str) -> pd.DataFrame:
    with connect(db_path) as conn:
        return pd.read_sql_query(sql, conn)


# ── Colour palette (matches PDF) ────────────────────────────────────────────
C_BLUE = "#3A7DD8"
C_TEAL = "#2CA089"
C_ORANGE = "#E8833A"
C_RED = "#D94F4F"
C_GREEN = "#4DAF4A"
C_PURPLE = "#8B5CC2"
C_PINK = "#D94F8F"
BAR_COLORS = [C_RED, C_ORANGE, C_TEAL, C_BLUE, C_PURPLE, "#3A7DD8", C_PINK]

# ── Theme-aware layout (transparent bg, visible text) ────────────────────────
LAYOUT_COMMON = dict(
    paper_bgcolor="rgba(0,0,0,0)",
    plot_bgcolor="rgba(0,0,0,0)",
    font=dict(color="white"),
)
TEXT_STYLE = dict(
    textfont=dict(size=14, color="white"),
    textposition="outside",
)

# ── Load main data ──────────────────────────────────────────────────────────
try:
    df = run_query("SELECT recording_date, case_no, cameras_count FROM cur_mp4_status_statistics")
except Exception as e:
    st.error(f"Could not load view `cur_mp4_status_statistics`: {e}")
    st.stop()

if df.empty:
    st.info("No data found in cur_mp4_status_statistics.")
    st.stop()

df["date"] = pd.to_datetime(df["recording_date"])
df["year"] = df["date"].dt.year
df["month"] = df["date"].dt.to_period("M").astype(str)

# ═════════════════════════════════════════════════════════════════════════════
# SECTION 1 — Camera Count Statistics
# ═════════════════════════════════════════════════════════════════════════════
st.markdown("---")
st.subheader("📹 Camera Count Statistics")

total_recordings = len(df)
surgery_days = df["recording_date"].nunique()
avg_cameras = round(df["cameras_count"].mean(), 1)
max_cameras = int(df["cameras_count"].max())
date_min = df["recording_date"].min()
date_max = df["recording_date"].max()

st.caption(f"Surgical camera recording data  •  {date_min} → {date_max}")

k1, k2, k3, k4 = st.columns(4)
k1.metric("Total Recordings", total_recordings, help="video files")
k2.metric("Surgery Days", surgery_days, help="unique dates")
k3.metric("Avg Cameras", avg_cameras, help="per recording")
k4.metric("Max Cameras", max_cameras, help="in a single recording")

# Camera count distribution
cam_dist = (
    df.groupby("cameras_count")
    .size()
    .reset_index(name="count")
    .rename(columns={"cameras_count": "cameras"})
    .sort_values("cameras")
)
cam_dist["label"] = cam_dist["cameras"].astype(str) + " cam"

fig_cam = px.bar(
    cam_dist,
    x="label",
    y="count",
    text="count",
    color="label",
    color_discrete_sequence=BAR_COLORS,
    title="Camera Count Distribution",
)
fig_cam.update_layout(
    **LAYOUT_COMMON,
    showlegend=False,
    xaxis_title="",
    yaxis_title="Count",
    yaxis_range=[0, cam_dist["count"].max() * 1.15],
    xaxis=dict(tickfont=dict(size=14)),
    height=450,
)
fig_cam.update_traces(**TEXT_STYLE)
st.plotly_chart(fig_cam, use_container_width=True)


# ═════════════════════════════════════════════════════════════════════════════
# SECTION 2 — Yearly Breakdown & Cases Per Day
# ═════════════════════════════════════════════════════════════════════════════
st.markdown("---")
st.subheader("📅 Yearly Breakdown & Cases Per Day")

col_left, col_right = st.columns(2)

with col_left:
    yearly_recs = df.groupby("year").size().reset_index(name="Recordings")
    yearly_days = (
        df.drop_duplicates(subset=["recording_date"])
        .groupby("year")
        .size()
        .reset_index(name="Surgery Days")
    )
    yearly = yearly_recs.merge(yearly_days, on="year")

    fig_yr = go.Figure()
    fig_yr.add_trace(
        go.Bar(
            x=yearly["year"],
            y=yearly["Recordings"],
            name="Recordings",
            marker_color=C_BLUE,
            text=yearly["Recordings"],
            textfont=dict(size=14, color="white"),
            textposition="outside",
        )
    )
    fig_yr.add_trace(
        go.Bar(
            x=yearly["year"],
            y=yearly["Surgery Days"],
            name="Surgery Days",
            marker_color=C_TEAL,
            text=yearly["Surgery Days"],
            textfont=dict(size=14, color="white"),
            textposition="outside",
        )
    )
    fig_yr.update_layout(
        **LAYOUT_COMMON,
        title="Yearly Overview",
        barmode="group",
        xaxis_title="",
        yaxis_title="",
        legend=dict(orientation="h", y=-0.15),
    )
    st.plotly_chart(fig_yr, use_container_width=True)

with col_right:
    cases_per_day = df.groupby("recording_date").size().reset_index(name="cases")
    cpd = cases_per_day.groupby("cases").size().reset_index(name="days")
    cpd["label"] = cpd["cases"].astype(str) + " case" + cpd["cases"].apply(
        lambda x: "s" if x > 1 else ""
    )

    fig_cpd = px.bar(
        cpd,
        x="label",
        y="days",
        text="days",
        color_discrete_sequence=[C_BLUE],
        title="Cases Per Surgery Day",
    )
    fig_cpd.update_layout(
        **LAYOUT_COMMON,
        xaxis_title="",
        yaxis_title="Number of Days",
    )
    fig_cpd.update_traces(**TEXT_STYLE)
    st.plotly_chart(fig_cpd, use_container_width=True)


# ═════════════════════════════════════════════════════════════════════════════
# SECTION 3 — Timeline Analysis
# ═════════════════════════════════════════════════════════════════════════════
st.markdown("---")
st.subheader("📈 Timeline Analysis")

monthly_cases = df.groupby("month").size().reset_index(name="Cases")

fig_tl = px.bar(
    monthly_cases,
    x="month",
    y="Cases",
    text="Cases",
    color_discrete_sequence=[C_BLUE],
    title="Total Cases Per Month",
)
fig_tl.update_layout(
    **LAYOUT_COMMON,
    xaxis_title="",
    yaxis_title="Cases",
    xaxis_tickangle=-45,
)
fig_tl.update_traces(**TEXT_STYLE)
st.plotly_chart(fig_tl, use_container_width=True)


# ═════════════════════════════════════════════════════════════════════════════
# SECTION 4 — Monthly Analysis (dual chart)
# ═════════════════════════════════════════════════════════════════════════════
st.markdown("---")
st.subheader("📊 Monthly Analysis")

monthly_recs = df.groupby("month").size().reset_index(name="Recordings")
monthly_days = (
    df.drop_duplicates(subset=["recording_date"])
    .assign(month=lambda d: pd.to_datetime(d["recording_date"]).dt.to_period("M").astype(str))
    .groupby("month")
    .size()
    .reset_index(name="Surgery Days")
)
monthly_avg = (
    df.groupby("month")["cameras_count"].mean().round(1).reset_index(name="Avg Cameras")
)
monthly = monthly_recs.merge(monthly_days, on="month").merge(monthly_avg, on="month")

fig_m = make_subplots(
    rows=2,
    cols=1,
    shared_xaxes=True,
    vertical_spacing=0.12,
    subplot_titles=("Monthly Recordings & Surgery Days", "Average Cameras Per Month"),
)

fig_m.add_trace(
    go.Bar(x=monthly["month"], y=monthly["Recordings"], name="Recordings", marker_color=C_BLUE),
    row=1, col=1,
)
fig_m.add_trace(
    go.Bar(x=monthly["month"], y=monthly["Surgery Days"], name="Surgery Days", marker_color=C_TEAL),
    row=1, col=1,
)
fig_m.add_trace(
    go.Scatter(
        x=monthly["month"],
        y=monthly["Avg Cameras"],
        name="Avg Cameras",
        mode="lines+markers",
        line=dict(color=C_ORANGE, width=2),
        marker=dict(size=6),
    ),
    row=2, col=1,
)

fig_m.update_layout(
    **LAYOUT_COMMON,
    height=650,
    barmode="group",
    legend=dict(orientation="h", y=-0.08),
)
fig_m.update_xaxes(tickangle=-45, row=2, col=1)
fig_m.update_yaxes(title_text="Count", row=1, col=1)
fig_m.update_yaxes(title_text="Avg Cameras", row=2, col=1)
st.plotly_chart(fig_m, use_container_width=True)


# ═════════════════════════════════════════════════════════════════════════════
# SECTION 5 — Labeled Data Overview
# ═════════════════════════════════════════════════════════════════════════════
st.markdown("---")
st.subheader("🏷️ Labeled Data Overview")

try:
    tagged_raw = run_query(
        "SELECT recording_date, case_no FROM analysis_information GROUP BY recording_date, case_no"
    )
except Exception as e:
    st.error(f"Could not load analysis_information: {e}")
    st.stop()

if tagged_raw.empty:
    st.info("No tagged cases found in analysis_information.")
else:
    # Join tagged cases with the main view to get camera counts
    tagged = tagged_raw.merge(
        df[["recording_date", "case_no", "cameras_count"]],
        on=["recording_date", "case_no"],
        how="inner",
    )

    total_tagged = len(tagged)
    tagged_days = tagged["recording_date"].nunique()
    avg_cam_tagged = round(tagged["cameras_count"].mean(), 1) if not tagged.empty else 0
    coverage_pct = round(tagged_days / surgery_days * 100) if surgery_days else 0
    tag_date_min = tagged["recording_date"].min()
    tag_date_max = tagged["recording_date"].max()

    st.caption(f"Tagged surgery dates  •  {tag_date_min} → {tag_date_max}")

    t1, t2, t3, t4 = st.columns(4)
    t1.metric("Total Tagged Cases", total_tagged, help="labeled recordings")
    t2.metric("Tagged Days", tagged_days, help="unique dates")
    t3.metric("Avg Cameras", avg_cam_tagged, help="per tagged case")
    t4.metric("Coverage", f"{coverage_pct}%", help=f"{tagged_days} of {surgery_days} surgery days")

    tc1, tc2 = st.columns(2)

    with tc1:
        tagged["month"] = pd.to_datetime(tagged["recording_date"]).dt.to_period("M").astype(str)
        monthly_tagged = tagged.groupby("month").size().reset_index(name="Tagged Cases")

        fig_tm = px.bar(
            monthly_tagged,
            x="month",
            y="Tagged Cases",
            text="Tagged Cases",
            color_discrete_sequence=[C_BLUE],
            title="Monthly Tagged Cases",
        )
        fig_tm.update_layout(
            **LAYOUT_COMMON,
            xaxis_title="",
            xaxis_tickangle=-45,
        )
        fig_tm.update_traces(**TEXT_STYLE)
        st.plotly_chart(fig_tm, use_container_width=True)

    with tc2:
        tag_cam_dist = (
            tagged.groupby("cameras_count")
            .size()
            .reset_index(name="count")
            .rename(columns={"cameras_count": "cameras"})
            .sort_values("cameras")
        )
        tag_cam_dist["label"] = tag_cam_dist["cameras"].astype(str) + " cam"

        fig_tc = px.bar(
            tag_cam_dist,
            x="label",
            y="count",
            text="count",
            color="label",
            color_discrete_sequence=BAR_COLORS,
            title="Camera Count Distribution (Tagged Cases)",
        )
        fig_tc.update_layout(
            **LAYOUT_COMMON,
            showlegend=False,
            xaxis_title="",
            yaxis_title="Count",
        )
        fig_tc.update_traces(**TEXT_STYLE)
        st.plotly_chart(fig_tc, use_container_width=True)
