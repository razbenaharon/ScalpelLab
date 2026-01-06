"""Database Views Page - Predefined Query Interface.

This Streamlit page provides access to database views, which are predefined
SQL queries stored in the database for common reporting needs.

Features:
    - **View Selector**: Browse and select from available database views
    - **Live Data Display**: Real-time data from selected view
    - **CSV Export**: Download query results for offline analysis
    - **Row Count**: Shows number of records returned

Common Views:
    - cur_mp4_missing: Cases where SEQ exists but MP4 is missing
    - cur_seq_missing: Cases where MP4 exists but SEQ is missing
    - cur_seniority: Current experience levels for all residents

Use Cases:
    - Identify videos that need conversion
    - Find missing source files
    - Generate seniority reports
    - Export data for external analysis

Navigation:
    Access via sidebar: Pages → 3_Views
"""

import streamlit as st
import sys, os
# This line adds the project root to the path to fix the import error
sys.path.append(os.path.dirname(os.path.dirname(__file__)))

from utils import list_views, load_table

st.header("👁️ Database Views")

db_path = st.session_state.get("db_path")

if not db_path:
    st.warning("No database path set.")
else:
    views = list_views(db_path)
    if not views:
        st.info("No views found in the database.")
    else:
        st.success(f"Found {len(views)} view(s) in the database:")

        view_choice = st.selectbox("Select a view to display", options=views)

        if view_choice:
            st.subheader(f"View: {view_choice}")
            try:
                df = load_table(db_path, view_choice)
                if not df.empty:
                    st.dataframe(df, width='stretch', hide_index=True)
                    st.caption(f"Showing {len(df)} rows")
                else:
                    st.info("View is empty or could not be loaded.")
            except Exception as e:
                st.error(f"Error loading view: {e}")