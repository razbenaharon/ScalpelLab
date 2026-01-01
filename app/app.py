import os
import sys
import streamlit as st
import fitz  # PyMuPDF
from PIL import Image
import io


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

# Display ERD PDF on main page
st.markdown("---")
st.subheader("Database Schema Overview")

# Check if ERD.pdf exists (in project root, one level up from app/)
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
erd_pdf_path = os.path.join(project_root, "docs", "ERD.pdf")
if os.path.exists(erd_pdf_path):
    try:
        # Open PDF and get first page
        pdf_document = fitz.open(erd_pdf_path)
        first_page = pdf_document[0]

        # Convert page to image
        pix = first_page.get_pixmap(matrix=fitz.Matrix(2, 2))  # 2x zoom for better quality
        img_data = pix.tobytes("png")

        # Convert to PIL Image and display
        image = Image.open(io.BytesIO(img_data))
        st.image(image, caption="ScalpelLab Database Entity Relationship Diagram", width='stretch')

        pdf_document.close()

    except Exception as e:
        st.error(f"Error loading ERD.pdf: {e}")
        st.info("Please make sure ERD.pdf is in the project directory.")
else:
    st.info("ERD.pdf not found. Please add your database ERD to display it here.")
