import streamlit as st
import pandas as pd
from collections import Counter
import plotly.express as px
import sys, os

# If utils.py is in project root (not pages/), uncomment to add parent dir to path:
sys.path.append(os.path.dirname(os.path.dirname(__file__)))

from utils import load_table, list_tables, get_table_schema, connect

DEFAULT_CAMERAS = [
    "Cart_Center_2","Cart_LT_4","Cart_RT_1",
    "General_3","Monitor","Patient_Monitor",
    "Ventilator_Monitor","Injection_Port"
]

LABELS_MP4 = {1: "Present", 2: "Missing"}
LABELS_SEQ = {1: "Present", 2: "Missing"}

st.header("🧮 Status Summary (mp4_status & seq_status)")

db_path = st.session_state.get("db_path")
if not db_path:
    st.warning("No database path set in session. Open the main page and set the DB path in the sidebar.")
    st.stop()

with st.sidebar:
    st.markdown("### Status Summary Options")
    mp4_table = st.text_input("MP4 status table", value="mp4_status")
    seq_table = st.text_input("SEQ status table", value="seq_status")
    cameras = st.multiselect("Cameras", DEFAULT_CAMERAS, default=DEFAULT_CAMERAS)

def fetch_camera_stats(db_path: str, table: str, cameras: list[str], threshold_mb: int = 200) -> tuple[int, dict]:
    """
    Fetch camera statistics by deriving status from size_mb:
    - 1 (Present) if size_mb is NOT NULL (file exists regardless of size)
    - 2 (Missing) if size_mb is NULL (file doesn't exist)
    """
    with connect(db_path) as conn:
        cur = conn.cursor()
        # Count distinct cases in the normalized table
        cur.execute(f"SELECT COUNT(DISTINCT recording_date || '-' || case_no) FROM {table}")
        total_cases = cur.fetchone()[0]
        camera_stats = {cam: Counter() for cam in cameras}
        # Query normalized schema: (recording_date, case_no, camera_name, size_mb)
        placeholders = ','.join(['?'] * len(cameras))
        cur.execute(f"SELECT camera_name, size_mb FROM {table} WHERE camera_name IN ({placeholders})", cameras)
        for camera_name, size_mb in cur.fetchall():
            if size_mb is None:
                status = 2  # Missing
            else:
                status = 1  # Present (any size)
            camera_stats[camera_name][status] += 1
        return total_cases, camera_stats

def stats_to_dataframe(camera_stats: dict, labels: dict, status_order) -> pd.DataFrame:
    rows = []
    present = set()
    for cam, ctr in camera_stats.items():
        for s, cnt in ctr.items():
            if cnt > 0:
                present.add(s)
    statuses = [s for s in status_order if (s in present) or (not present and s in labels)]
    for cam, ctr in camera_stats.items():
        for s in statuses:
            rows.append({
                "camera": cam,
                "status": s,
                "status_label": labels.get(s, str(s)),
                "count": int(ctr.get(s, 0))
            })
    return pd.DataFrame(rows)

def section(title: str, table_name: str, labels: dict, order: tuple[int, ...]):
    st.subheader(title)
    try:
        total_rows, camera_stats = fetch_camera_stats(db_path, table_name, cameras)
        st.caption(f"Total cases in `{table_name}`: **{total_rows}**")
        df = stats_to_dataframe(camera_stats, labels, order)

        if df.empty:
            st.info("No status data found for the selected cameras.")
            return

        pivot = df.pivot_table(index="camera", columns="status_label", values="count",
                               aggfunc="sum", fill_value=0)
        st.dataframe(pivot, use_container_width=True)

        totals = df.groupby("status_label")["count"].sum().reset_index()
        st.markdown("**Totals across all cameras:**")
        st.dataframe(totals, use_container_width=True, hide_index=True)

    except Exception as e:
        st.error(f"Error: {e}")

section("📁 MP4 Status Summary", mp4_table, LABELS_MP4, (1, 2))
st.divider()
section("🎞️ SEQ Status Summary", seq_table, LABELS_SEQ, (1, 2))