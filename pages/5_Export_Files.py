import streamlit as st
import pandas as pd
import sys, os
from pathlib import Path

# Add parent dir to path for imports
sys.path.append(os.path.dirname(os.path.dirname(__file__)))

from utils import connect, load_table

# Add scripts dir for seq_exporter
scripts_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "scripts")
sys.path.append(scripts_dir)

from seq_exporter import (
    export_seq_once_streaming,
    compute_out_dir,
    resolve_channel_label,
    find_existing_export,
    calculate_timeout,
    get_next_available_filename,
    is_valid_video_file
)

st.header("📤 Export Files")

db_path = st.session_state.get("db_path")
if not db_path:
    st.warning("No database path set in session. Open the main page and set the DB path in the sidebar.")
    st.stop()

# Configuration
SEQ_ROOT = r"F:\Room_8_Data\Sequence_Backup"
OUT_ROOT = r"F:\Room_8_Data\Recordings"

DEFAULT_CAMERAS = [
    "Cart_Center_2", "Cart_LT_4", "Cart_RT_1",
    "General_3", "Monitor", "Patient_Monitor",
    "Ventilator_Monitor", "Injection_Port"
]

# Sidebar filters
with st.sidebar:
    st.markdown("### Export Options")
    selected_cameras = st.multiselect("Cameras", DEFAULT_CAMERAS, default=DEFAULT_CAMERAS)
    show_all = st.checkbox("Show all files", value=False, help="Show all files, not just exportable ones")
    auto_skip_existing = st.checkbox("Skip existing MP4s", value=True, help="Don't convert if MP4 already exists")

# Query to get all seq files with their mp4 status
def fetch_export_data(db_path: str, cameras: list, threshold_mb: int = 200):
    """
    Fetch all seq files and their mp4 status.
    Returns a DataFrame with columns: recording_date, case_no, camera_name, seq_status, mp4_status, seq_size_mb, mp4_size_mb
    Status is derived from size_mb: 1=>=threshold_mb, 2=<threshold_mb, 3=NULL
    """
    with connect(db_path) as conn:
        query = """
        SELECT
            s.recording_date,
            s.case_no,
            s.camera_name,
            CASE
                WHEN s.size_mb IS NULL THEN 3
                WHEN s.size_mb >= ? THEN 1
                ELSE 2
            END as seq_status,
            s.size_mb as seq_size_mb,
            CASE
                WHEN m.size_mb IS NULL THEN 3
                WHEN m.size_mb >= ? THEN 1
                ELSE 2
            END as mp4_status,
            m.size_mb as mp4_size_mb
        FROM seq_status s
        LEFT JOIN mp4_status m
            ON s.recording_date = m.recording_date
            AND s.case_no = m.case_no
            AND s.camera_name = m.camera_name
        WHERE s.camera_name IN ({})
        ORDER BY s.recording_date DESC, s.case_no, s.camera_name
        """.format(','.join(['?'] * len(cameras)))

        # First two params are threshold_mb for both CASE statements, rest are cameras
        params = [threshold_mb, threshold_mb] + cameras
        df = pd.read_sql_query(query, conn, params=params)
    return df

# Status labels
SEQ_LABELS = {1: ">200MB", 2: "<200MB", 3: "Missing", 4: "FORMAT PROBLEM"}
MP4_LABELS = {1: ">=200MB", 2: "<200MB", 3: "Missing"}

@st.cache_data
def load_export_data(db_path: str, cameras: list, show_all: bool):
    df = fetch_export_data(db_path, cameras)

    # Filter to only exportable files (seq_status 1 or 2) unless show_all
    if not show_all:
        df = df[df['seq_status'].isin([1, 2])]

    # Add human-readable status labels
    df['seq_status_label'] = df['seq_status'].map(SEQ_LABELS)
    df['mp4_status_label'] = df['mp4_status'].map(MP4_LABELS).fillna('Not checked')

    # Add a column to indicate if MP4 exists
    df['has_mp4'] = df['mp4_status'].isin([1, 2])

    return df

# Build SEQ path from database info
def build_seq_path(recording_date: str, case_no: int, camera_name: str) -> Path:
    """Build the path to the .seq file based on database info."""
    # recording_date: 'YYYY-MM-DD' -> 'DATA_YY-MM-DD'
    yy = recording_date[2:4]
    mm = recording_date[5:7]
    dd = recording_date[8:10]
    data_folder = f"DATA_{yy}-{mm}-{dd}"
    case_folder = f"Case{case_no}"

    seq_path = Path(SEQ_ROOT) / data_folder / case_folder / camera_name

    # Find the first .seq file in this directory
    seq_files = list(seq_path.glob("*.seq"))
    if seq_files:
        return seq_files[0]
    return seq_path  # Return directory if no .seq found

# Export a single file
def export_single_file(recording_date: str, case_no: int, camera_name: str, skip_existing: bool) -> tuple:
    """
    Export a single SEQ file to MP4.
    Returns (success: bool, message: str, output_path: Path or None)
    """
    try:
        # Build paths
        seq_path = build_seq_path(recording_date, case_no, camera_name)

        if not seq_path.exists():
            return False, f"SEQ file not found: {seq_path}", None

        if seq_path.is_dir():
            return False, f"No .seq file found in directory: {seq_path}", None

        seq_path = seq_path.resolve()
        seq_root_path = Path(SEQ_ROOT).resolve()
        out_root_path = Path(OUT_ROOT).resolve()

        # Compute output directory
        out_dir = compute_out_dir(seq_path, out_root_path)

        # Get base filename
        ch_label = resolve_channel_label(seq_path, {})
        base_stem = ch_label

        # Check if valid export already exists
        if skip_existing:
            existing = find_existing_export(out_dir, base_stem)
            if existing:
                return True, f"MP4 already exists: {existing.name}", existing

        # Calculate timeout
        timeout = calculate_timeout(seq_path)

        # Get next available filename
        exported_name, mp4_path = get_next_available_filename(out_dir, base_stem, ".mp4")

        # Run export
        exitcode, reason = export_seq_once_streaming(
            seq_path=seq_path,
            out_dir=out_dir,
            exported_name=exported_name[:-4],  # Remove .mp4 extension
            container="mp4",
            simulate=False,
            spawn_console=False,
            timeout_secs=timeout,
            kill_after_error_lines=6,
            suppress_console_output=True,
            debug=False
        )

        # Check if export succeeded
        if exitcode == 0 and is_valid_video_file(mp4_path):
            return True, f"Export successful: {mp4_path.name}", mp4_path
        else:
            # Clean up invalid file
            if mp4_path.exists() and not is_valid_video_file(mp4_path):
                try:
                    mp4_path.unlink()
                except:
                    pass
            return False, f"Export failed: {reason}", None

    except Exception as e:
        return False, f"Error during export: {str(e)}", None

# Load data
df = load_export_data(db_path, selected_cameras, show_all)

st.caption(f"Found **{len(df)}** SEQ files")

if df.empty:
    st.info("No SEQ files found for the selected cameras.")
    st.stop()

# Display summary
col1, col2, col3, col4 = st.columns(4)
with col1:
    total_exportable = len(df[df['seq_status'].isin([1, 2])])
    st.metric("Exportable Files", total_exportable)
with col2:
    has_mp4 = len(df[df['has_mp4']])
    st.metric("Has MP4", has_mp4)
with col3:
    missing_mp4 = total_exportable - has_mp4
    st.metric("Missing MP4", missing_mp4)
with col4:
    total_size_gb = df['seq_size_mb'].sum() / 1024
    st.metric("Total Size", f"{total_size_gb:.1f} GB")

st.divider()

# Search/filter
search_term = st.text_input("🔍 Search by date, case, or camera", placeholder="e.g., 2022-12-04, Case1, Monitor")

if search_term:
    mask = (
        df['recording_date'].str.contains(search_term, case=False, na=False) |
        df['case_no'].astype(str).str.contains(search_term, case=False, na=False) |
        df['camera_name'].str.contains(search_term, case=False, na=False)
    )
    df = df[mask]
    st.caption(f"Filtered to **{len(df)}** files")

# Display table with export buttons
st.markdown("### Files")

# Create a more interactive display
for idx, row in df.iterrows():
    with st.container():
        col1, col2, col3, col4, col5, col6 = st.columns([2, 1, 2, 2, 2, 2])

        with col1:
            st.markdown(f"**{row['recording_date']}**")
            st.caption(f"Case {row['case_no']}")

        with col2:
            st.markdown(f"{row['camera_name']}")

        with col3:
            st.markdown(f"SEQ: {row['seq_status_label']}")
            if pd.notna(row['seq_size_mb']):
                st.caption(f"{row['seq_size_mb']:.0f} MB")

        with col4:
            mp4_status = row['mp4_status_label']
            if row['has_mp4']:
                st.markdown(f"✅ {mp4_status}")
                if pd.notna(row['mp4_size_mb']):
                    st.caption(f"{row['mp4_size_mb']:.0f} MB")
            else:
                st.markdown(f"❌ {mp4_status}")

        with col5:
            # Show file path as text
            seq_path = build_seq_path(row['recording_date'], row['case_no'], row['camera_name'])
            if seq_path.exists() and seq_path.is_file():
                st.caption(f"📁 {seq_path.name}")
            else:
                st.caption("⚠️ File not found")

        with col6:
            # Export button
            button_key = f"export_{row['recording_date']}_{row['case_no']}_{row['camera_name']}"

            if row['has_mp4'] and auto_skip_existing:
                st.button("✓ Already exported", key=button_key, disabled=True, use_container_width=True)
            else:
                button_label = "🔄 Re-export" if row['has_mp4'] else "▶️ Export"
                if st.button(button_label, key=button_key, use_container_width=True):
                    # Calculate estimated time
                    seq_path = build_seq_path(row['recording_date'], row['case_no'], row['camera_name'])
                    estimated_time = "unknown"
                    if seq_path.exists() and seq_path.is_file():
                        timeout = calculate_timeout(seq_path)
                        estimated_time = f"~{timeout}s"

                    with st.spinner(f"Exporting {row['camera_name']} (est. time: {estimated_time})..."):
                        success, message, output_path = export_single_file(
                            row['recording_date'],
                            row['case_no'],
                            row['camera_name'],
                            auto_skip_existing
                        )

                        if success:
                            st.success(message)
                            # Clear cache to refresh data
                            load_export_data.clear()
                            st.rerun()
                        else:
                            st.error(message)
                            # Show retry hint for timeout errors
                            if "timed out" in message.lower():
                                st.info("💡 Tip: Large files may need more time. The timeout has been increased - try again!")

        st.divider()

# Bulk export section
st.markdown("### Bulk Export")
col1, col2 = st.columns(2)

with col1:
    if st.button("🚀 Export All Missing MP4s", type="primary", use_container_width=True):
        missing_files = df[~df['has_mp4'] & df['seq_status'].isin([1, 2])]

        if len(missing_files) == 0:
            st.info("No files to export!")
        else:
            progress_bar = st.progress(0)
            status_text = st.empty()

            success_count = 0
            fail_count = 0

            for i, (idx, row) in enumerate(missing_files.iterrows()):
                status_text.text(f"Exporting {i+1}/{len(missing_files)}: {row['camera_name']} (Case {row['case_no']})")

                success, message, _ = export_single_file(
                    row['recording_date'],
                    row['case_no'],
                    row['camera_name'],
                    auto_skip_existing
                )

                if success:
                    success_count += 1
                else:
                    fail_count += 1

                progress_bar.progress((i + 1) / len(missing_files))

            status_text.text("")
            st.success(f"Bulk export complete! ✅ {success_count} succeeded, ❌ {fail_count} failed")
            load_export_data.clear()
            st.rerun()

with col2:
    if st.button("🔄 Refresh Data", use_container_width=True):
        load_export_data.clear()
        st.rerun()
