"""Main Streamlit application for ScalpelLab Database Manager.

This is the entry point for the web-based database management interface. The app
provides:

- **Homepage**: Project overview and navigation guide
- **Database Schema Visualization**: Entity-Relationship Diagram (ERD)
  displayed from a static PDF file (docs/ERD.pdf)

The app displays the database schema from the official ERD.pdf document,
providing a clear and consistent view of tables, columns, and relationships.

Pages:
    - 1_Database.py: Browse tables, insert/delete records
    - 2_Status_Summary.py: View statistics and visualizations
    - 3_Views.py: Query database views

Note:
    The database path is configurable via the sidebar and defaults to the path
    specified in config.py. The ERD diagram is loaded from the 'docs' folder.
"""

import os
import sys
import streamlit as st
import sqlite3
from PIL import Image
import fitz  # PyMuPDF for PDF rendering

# Import path manager - add parent directory to path for config import
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import get_db_path

st.set_page_config(page_title="ScalpelLab DB", layout="wide")

st.title("ScalpelLab – Streamlit SQLite Database Manager")

# Project overview section
st.markdown("""

### 🛠 **Available Pages**
- **🗄️ Database Management**: Browse tables, insert new records, and delete existing rows with an intuitive interface
- **📊 Status Summary**: View MP4/SEQ processing statistics, camera distributions, and visual charts
- **📑 Views**: Access and query database views for specialized data perspectives

Navigate using the sidebar to access different features and tools.
""")

st.markdown("---")

st.sidebar.header("Database")
DEFAULT_DB = os.environ.get("SCALPEL_DB", get_db_path())
db_path = st.sidebar.text_input("SQLite DB Path", value=DEFAULT_DB)

# make DB path available to all pages
st.session_state["db_path"] = db_path

st.sidebar.markdown("Navigate using the left sidebar menu (pages).")


# Database Schema Visualization Section
st.markdown("---")
st.markdown("### Database Schema Visualization")
st.markdown("""
Explore the database structure using the Entity-Relationship Diagram (ERD).
The diagram shows all tables, columns, data types, and relationships.
""")

project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
erd_pdf_path = os.path.join(project_root, "docs", "ERD.pdf")

if os.path.exists(erd_pdf_path):
    with open(erd_pdf_path, "rb") as f:
        st.download_button(
            label="Download ERD PDF",
            data=f,
            file_name="ERD.pdf",
            mime="application/pdf",
            use_container_width=False
        )
    
    # Display the first page of the PDF as an image for visual reference
    try:
        doc = fitz.open(erd_pdf_path)
        if len(doc) > 0:
            page = doc.load_page(0)  # Load only the first page
            # Use high DPI for better readability of the diagram
            pix = page.get_pixmap(dpi=150)
            img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
            st.image(img, caption="Database Schema ERD", use_container_width=True)
        doc.close()
    except Exception as e:
        st.error(f"Error displaying ERD PDF: {e}")
else:
    st.warning(f"ERD.pdf not found at: {erd_pdf_path}. Please ensure it exists in the 'docs' folder.")
