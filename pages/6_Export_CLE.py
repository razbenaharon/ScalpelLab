import streamlit as st
import pandas as pd
import sys, os
from pathlib import Path
import subprocess
import threading
import queue
import time

# Add parent dir to path for imports
sys.path.append(os.path.dirname(os.path.dirname(__file__)))

from utils import connect, load_table

# Add scripts dir
scripts_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "scripts")
sys.path.append(scripts_dir)

from ffmpeg_exporter import (
    compute_out_dir,
    resolve_channel_label,
    find_existing_export,
    get_next_available_filename,
    is_valid_video_file
)

st.header("📤 Export CLE (CLExport)")

db_path = st.session_state.get("db_path")
if not db_path:
    st.warning("No database path set in session. Open the main page and set the DB path in the sidebar.")
    st.stop()

# Configuration
SEQ_ROOT = r"F:\Room_8_Data\Sequence_Backup"
OUT_ROOT = r"F:\Room_8_Data\Recordings"

# CLExport possible locations
CLEXPORT_PATHS = [
    r"C:\Program Files\NorPix\BatchProcessor\CLExport.exe",
    r"C:\Program Files (x86)\NorPix\BatchProcessor\CLExport.exe",
    r"C:\NorPix\BatchProcessor\CLExport.exe",
]

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

    st.markdown("### CLExport Settings")
    output_format = st.selectbox("Output Format", ["mp4", "avi"], help="Video container format")
    codec = st.selectbox("Codec", ["mp4", "mjpeg"], help="Video codec (mp4 for H.264, mjpeg for Motion JPEG)")


def find_clexport() -> str:
    """Find CLExport.exe in common locations."""
    for path in CLEXPORT_PATHS:
        if os.path.exists(path):
            return path
    return None


# Query to get all seq files with their mp4 status
def fetch_export_data(db_path: str, cameras: list, threshold_mb: int = 200):
    """
    Fetch all seq files and their mp4 status.
    Returns a DataFrame with columns: recording_date, case_no, camera_name, seq_status, mp4_status, seq_size_mb, mp4_size_mb
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

        params = [threshold_mb, threshold_mb] + cameras
        df = pd.read_sql_query(query, conn, params=params)
    return df


SEQ_LABELS = {1: ">200MB", 2: "<200MB", 3: "Missing", 4: "FORMAT PROBLEM"}
MP4_LABELS = {1: ">=200MB", 2: "<200MB", 3: "Missing"}


@st.cache_data
def load_export_data(db_path: str, cameras: list, show_all: bool):
    df = fetch_export_data(db_path, cameras)

    # Filter to only exportable files
    if not show_all:
        df = df[df['seq_status'].isin([1, 2])]

    # Add human-readable status labels
    df['seq_status_label'] = df['seq_status'].map(SEQ_LABELS)
    df['mp4_status_label'] = df['mp4_status'].map(MP4_LABELS).fillna('Not checked')

    # Add a column to indicate if MP4 exists
    df['has_mp4'] = df['mp4_status'].isin([1, 2])

    return df


def build_seq_path(recording_date: str, case_no: int, camera_name: str) -> Path:
    """Build the path to the .seq file based on database info."""
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
    return seq_path


# Export with real-time output capture using CLExport
def export_with_clexport(seq_path: Path, out_path: Path, container: str, codec: str,
                         output_queue: queue.Queue, stop_flag: threading.Event):
    """
    Export SEQ file to MP4/AVI using CLExport with real-time output capture.
    Returns (exitcode, message).
    """
    try:
        clexport_path = find_clexport()
        if not clexport_path:
            output_queue.put(("ERROR", "CLExport.exe not found\n"))
            return 1, "CLExport.exe not found"

        # Adjust output path based on container
        if container == "avi":
            out_path = out_path.with_suffix(".avi")
        else:
            out_path = out_path.with_suffix(".mp4")

        # Build CLExport command
        # Note: CLExport parameters: -i input, -o output_dir, -of output_filename, -f format
        out_dir = out_path.parent
        out_filename = out_path.stem  # CLExport adds extension automatically

        cmd = [
            clexport_path,
            "-i", str(seq_path),
            "-o", str(out_dir),
            "-of", out_filename,
            "-f", codec  # mp4 or mjpeg
        ]

        output_queue.put(("INFO", f"Running: {' '.join(cmd)}\n\n"))

        # Start CLExport process
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            universal_newlines=True,
            bufsize=1
        )

        # Read output line by line
        while True:
            if stop_flag.is_set():
                output_queue.put(("STOP", "\n[STOPPING] User requested stop...\n"))
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
                output_queue.put(("STOP", "[STOPPED] Process terminated\n"))
                return 1, "Stopped by user"

            line = process.stdout.readline()
            if not line:
                break

            output_queue.put(("OUTPUT", line))

        # Wait for process to complete
        return_code = process.wait()

        if return_code == 0 and is_valid_video_file(out_path):
            output_queue.put(("SUCCESS", f"\n[SUCCESS] Export completed: {out_path.name}\n"))
            return 0, "Export successful"
        else:
            output_queue.put(("ERROR", f"\n[ERROR] CLExport failed with exit code {return_code}\n"))
            return return_code or 1, f"CLExport failed with exit code {return_code}"

    except Exception as e:
        output_queue.put(("ERROR", f"\n[EXCEPTION] {str(e)}\n"))
        return 1, f"Error: {str(e)}"


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

# Check CLExport availability
clexport_path = find_clexport()
if clexport_path:
    st.success(f"✅ CLExport found: `{clexport_path}`")
else:
    st.error("❌ CLExport.exe not found. Please install NorPix BatchProcessor.")
    st.info(f"Searched in:\n" + "\n".join([f"- {p}" for p in CLEXPORT_PATHS]))
    st.stop()

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

st.divider()

# Multi-select export section
st.markdown("### 📦 Multi-File Export")

# Initialize session state for selected files
if 'cle_selected_files' not in st.session_state:
    st.session_state.cle_selected_files = set()
if 'cle_export_queue' not in st.session_state:
    st.session_state.cle_export_queue = []
if 'cle_is_exporting' not in st.session_state:
    st.session_state.cle_is_exporting = False
if 'cle_stop_export' not in st.session_state:
    st.session_state.cle_stop_export = False
if 'cle_current_output' not in st.session_state:
    st.session_state.cle_current_output = []

# Selection controls
col1, col2, col3 = st.columns([2, 1, 1])
with col1:
    select_mode = st.radio("Selection", ["Select All Missing", "Select All", "Manual"], horizontal=True)
with col2:
    if st.button("Clear Selection", use_container_width=True, key="cle_clear"):
        st.session_state.cle_selected_files = set()
        st.rerun()
with col3:
    selected_count = len(st.session_state.cle_selected_files)
    st.metric("Selected", selected_count)

# Auto-select based on mode
if select_mode == "Select All Missing":
    missing_files = df[~df['has_mp4']].index.tolist()
    if st.button("Apply Selection", use_container_width=True, key="cle_apply_missing"):
        st.session_state.cle_selected_files = set(missing_files)
        st.rerun()
elif select_mode == "Select All":
    all_files = df.index.tolist()
    if st.button("Apply Selection", use_container_width=True, key="cle_apply_all"):
        st.session_state.cle_selected_files = set(all_files)
        st.rerun()

st.divider()

# Display files with checkboxes
st.markdown("### 📁 Files")

for idx, row in df.iterrows():
    with st.container():
        col1, col2, col3, col4, col5 = st.columns([0.5, 2, 1, 2, 2])

        with col1:
            # Checkbox for selection
            is_selected = idx in st.session_state.cle_selected_files
            if st.checkbox("", value=is_selected, key=f"cle_check_{idx}", label_visibility="collapsed"):
                st.session_state.cle_selected_files.add(idx)
            else:
                st.session_state.cle_selected_files.discard(idx)

        with col2:
            st.markdown(f"**{row['recording_date']}** Case {row['case_no']}")
            st.caption(f"{row['camera_name']}")

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
            # Show file path
            seq_path = build_seq_path(row['recording_date'], row['case_no'], row['camera_name'])
            if seq_path.exists() and seq_path.is_file():
                st.caption(f"📁 {seq_path.name}")
            else:
                st.caption("⚠️ File not found")

        st.divider()

# Export controls
st.markdown("### 🎬 Export Control")

col1, col2, col3 = st.columns([2, 1, 1])

with col1:
    if not st.session_state.cle_is_exporting:
        if st.button("🚀 Export Selected Files", type="primary", use_container_width=True,
                     disabled=len(st.session_state.cle_selected_files) == 0, key="cle_export"):
            # Build export queue
            st.session_state.cle_export_queue = []
            for idx in st.session_state.cle_selected_files:
                row = df.loc[idx]
                st.session_state.cle_export_queue.append({
                    'recording_date': row['recording_date'],
                    'case_no': row['case_no'],
                    'camera_name': row['camera_name'],
                    'idx': idx
                })
            st.session_state.cle_is_exporting = True
            st.session_state.cle_stop_export = False
            st.session_state.cle_current_output = []
            st.rerun()
    else:
        st.info(f"⏳ Exporting... ({len(st.session_state.cle_export_queue)} remaining)")

with col2:
    if st.session_state.cle_is_exporting:
        if st.button("⏹️ Stop Export", type="secondary", use_container_width=True, key="cle_stop"):
            st.session_state.cle_stop_export = True
            st.rerun()

with col3:
    if st.button("🔄 Refresh", use_container_width=True, key="cle_refresh"):
        load_export_data.clear()
        st.rerun()

# Export progress section
if st.session_state.cle_is_exporting and st.session_state.cle_export_queue:
    st.markdown("---")
    st.markdown("### 📊 Export Progress")

    current_file = st.session_state.cle_export_queue[0]
    total_files = len(st.session_state.cle_selected_files)
    completed_files = total_files - len(st.session_state.cle_export_queue)

    # Progress bar
    progress = completed_files / total_files
    st.progress(progress, text=f"File {completed_files + 1} of {total_files}")

    # Current file info
    st.markdown(f"**Current:** {current_file['recording_date']} Case {current_file['case_no']} - {current_file['camera_name']}")
    st.caption(f"Format: {output_format.upper()} | Codec: {codec}")

    # CLExport output window
    output_container = st.expander("📺 CLExport Output", expanded=True)
    output_placeholder = output_container.empty()

    # Export current file
    seq_path = build_seq_path(current_file['recording_date'], current_file['case_no'], current_file['camera_name'])

    if not seq_path.exists() or seq_path.is_dir():
        st.error(f"❌ File not found: {seq_path}")
        st.session_state.cle_export_queue.pop(0)
        if not st.session_state.cle_export_queue:
            st.session_state.cle_is_exporting = False
        st.rerun()

    # Compute output path
    seq_root_path = Path(SEQ_ROOT).resolve()
    out_root_path = Path(OUT_ROOT).resolve()
    out_dir = compute_out_dir(seq_path, out_root_path)
    ch_label = resolve_channel_label(seq_path, {})

    # Get output extension based on format
    extension = f".{output_format}"
    exported_name, output_file = get_next_available_filename(out_dir, ch_label, extension)

    # Check if already exists
    if auto_skip_existing and is_valid_video_file(output_file):
        st.info(f"⏭️ Skipped (already exists): {output_file.name}")
        st.session_state.cle_export_queue.pop(0)
        time.sleep(1)
        if not st.session_state.cle_export_queue:
            st.session_state.cle_is_exporting = False
            st.success(f"✅ All exports complete!")
        st.rerun()

    # Create queue and stop flag for this export
    output_queue = queue.Queue()
    stop_flag = threading.Event()

    if st.session_state.cle_stop_export:
        stop_flag.set()

    # Run export in thread
    export_thread = threading.Thread(
        target=export_with_clexport,
        args=(seq_path, output_file, output_format, codec, output_queue, stop_flag)
    )
    export_thread.start()

    # Monitor output
    output_lines = list(st.session_state.cle_current_output)
    max_lines = 50  # Keep last 50 lines

    while export_thread.is_alive() or not output_queue.empty():
        try:
            msg_type, line = output_queue.get(timeout=0.1)
            output_lines.append(line)

            # Keep only last max_lines
            if len(output_lines) > max_lines:
                output_lines = output_lines[-max_lines:]

            # Update display
            output_text = ''.join(output_lines)
            output_placeholder.code(output_text, language=None)

            st.session_state.cle_current_output = output_lines

        except queue.Empty:
            time.sleep(0.1)

    export_thread.join()

    # Check result
    if stop_flag.is_set():
        # Delete partial file
        if output_file.exists():
            try:
                output_file.unlink()
                st.warning(f"🗑️ Deleted partial file: {output_file.name}")
            except:
                st.error(f"⚠️ Could not delete partial file: {output_file.name}")

        st.session_state.cle_export_queue.clear()
        st.session_state.cle_is_exporting = False
        st.session_state.cle_current_output = []
        st.error("❌ Export stopped by user")
        time.sleep(2)
        st.rerun()
    else:
        # Check if successful
        if is_valid_video_file(output_file):
            st.success(f"✅ Exported: {output_file.name}")
            st.session_state.cle_export_queue.pop(0)
            st.session_state.cle_current_output = []

            if not st.session_state.cle_export_queue:
                st.session_state.cle_is_exporting = False
                st.success(f"🎉 All {completed_files + 1} files exported successfully!")
                time.sleep(2)
                load_export_data.clear()

            st.rerun()
        else:
            # Failed - clean up
            if output_file.exists():
                try:
                    output_file.unlink()
                except:
                    pass

            st.error(f"❌ Export failed: {current_file['camera_name']}")
            st.session_state.cle_export_queue.pop(0)
            st.session_state.cle_current_output = []

            if not st.session_state.cle_export_queue:
                st.session_state.cle_is_exporting = False

            time.sleep(2)
            st.rerun()
