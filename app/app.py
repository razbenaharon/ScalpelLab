import os
import sys
import streamlit as st
import sqlite3
import graphviz
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


def generate_erd_diagram(db_path):
    """Generate an ERD diagram mimicking PyCharm Database Diagram aesthetic"""
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        # Get all tables
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [row[0] for row in cursor.fetchall() if row[0] != 'sqlite_sequence']

        # Create graphviz graph with PyCharm-like styling
        dot = graphviz.Digraph(comment='Database Schema', engine='dot')

        # VISUAL FIXES:
        # 1. splines='curved': Prevents the 'barcode' overlapping lines seen with 'ortho'
        # 2. ranksep/nodesep: Increased spacing allows curved lines to flow clearly
        dot.attr(rankdir='LR',
                 bgcolor='#FFFFFF',
                 pad='0.5',
                 ranksep='2.5',
                 nodesep='1.2',
                 splines='curved')

        dot.attr('node',
                 shape='plaintext',
                 fontname='Segoe UI, Arial',
                 fontsize='10')

        dot.attr('edge',
                 color='#6C6C6C',
                 arrowsize='0.8',
                 penwidth='1.2',
                 fontname='Segoe UI, Arial',
                 fontsize='8',
                 fontcolor='#6C6C6C')

        # PyCharm color scheme
        HEADER_BG = '#EAF2F8'  # Light blue-gray header
        HEADER_BORDER = '#D5E1E8'  # Header border
        PK_BG = '#FFFACD'  # Light yellow for primary keys
        FK_BG = '#E8F4F8'  # Light blue for foreign keys
        REGULAR_BG = '#FFFFFF'  # White for regular columns
        BORDER_COLOR = '#D0D0D0'  # Light gray borders
        TEXT_COLOR = '#2B2B2B'  # Dark gray text

        # Collect foreign key information for all tables
        all_fk_columns = {}
        for table in tables:
            cursor.execute(f"PRAGMA foreign_key_list({table})")
            fks = cursor.fetchall()
            all_fk_columns[table] = [fk[3] for fk in fks]  # Store FK column names

        # Add tables as nodes with PyCharm-style HTML labels
        for table in tables:
            cursor.execute(f"PRAGMA table_info({table})")
            columns = cursor.fetchall()

            # Build HTML table mimicking PyCharm's style
            html_label = f'<<TABLE BORDER="2" CELLBORDER="1" CELLSPACING="0" CELLPADDING="5" COLOR="{BORDER_COLOR}">'

            # Table header (PyCharm style - subtle background)
            html_label += f'<TR>'
            html_label += f'<TD COLSPAN="3" BGCOLOR="{HEADER_BG}" BORDER="2" SIDES="B" COLOR="{HEADER_BORDER}">'
            html_label += f'<FONT COLOR="{TEXT_COLOR}" POINT-SIZE="11"><B>{table}</B></FONT>'
            html_label += f'</TD></TR>'

            # Add columns with ports for connection points
            for col in columns:
                col_name = col[1]
                col_type = col[2] or "TEXT"
                is_pk = col[5]
                is_fk = col_name in all_fk_columns.get(table, [])

                # Create a port for this column (sanitize for graphviz)
                port_name = f'{table}_{col_name}'.replace(' ', '_').replace('-', '_').replace('.', '_')

                # Determine background color (PyCharm style)
                if is_pk:
                    bg_color = PK_BG
                    key_icon = '🔑'
                elif is_fk:
                    bg_color = FK_BG
                    key_icon = '🔗'
                else:
                    bg_color = REGULAR_BG
                    key_icon = ''

                # Format column name with icon
                if key_icon:
                    col_display = f'{key_icon} {col_name}'
                else:
                    col_display = col_name

                # Build row with 3 columns: icon/name, type, constraints
                html_label += f'<TR>'
                html_label += f'<TD PORT="{port_name}" BGCOLOR="{bg_color}" ALIGN="LEFT">'
                html_label += f'<FONT COLOR="{TEXT_COLOR}" POINT-SIZE="10">{col_display}</FONT>'
                html_label += f'</TD>'

                html_label += f'<TD BGCOLOR="{bg_color}" ALIGN="LEFT">'
                html_label += f'<FONT COLOR="#666666" POINT-SIZE="9">{col_type}</FONT>'
                html_label += f'</TD>'

                # Constraints column
                constraints = []
                if is_pk:
                    constraints.append('PK')
                if is_fk:
                    constraints.append('FK')
                constraint_text = ', '.join(constraints) if constraints else ' '

                html_label += f'<TD BGCOLOR="{bg_color}" ALIGN="CENTER">'
                if constraint_text.strip():
                    html_label += f'<FONT COLOR="#999999" POINT-SIZE="8">{constraint_text}</FONT>'
                else:
                    html_label += ' '
                html_label += f'</TD>'
                html_label += f'</TR>'

            html_label += '</TABLE>>'
            dot.node(table, html_label)

        # Add foreign key relationships with PyCharm-style connections
        for table in tables:
            cursor.execute(f"PRAGMA foreign_key_list({table})")
            foreign_keys = cursor.fetchall()

            for fk in foreign_keys:
                ref_table = fk[2]
                local_col = fk[3]
                ref_col = fk[4]

                # Skip if referenced table doesn't exist in our table list
                if ref_table not in tables:
                    continue

                # Create port names (sanitize for graphviz)
                from_port = f'{ref_table}_{ref_col}'.replace(' ', '_').replace('-', '_').replace('.', '_')
                to_port = f'{table}_{local_col}'.replace(' ', '_').replace('-', '_').replace('.', '_')

                # PyCharm-style relationship line
                # NOTE: Removed :e and :w constraints to allow auto-routing (fixes looping)
                # NOTE: Added 'crow' and 'otee' for correct database notation
                dot.edge(f'{ref_table}:{from_port}',
                         f'{table}:{to_port}',
                         color='#6C6C6C',
                         arrowhead='crow',  # Crow's foot on Child side (Many)
                         arrowtail='otee',  # Tee/Bar on Parent side (One)
                         dir='both',  # Render both symbols
                         penwidth='1.5',
                         constraint='true')

        conn.close()
        return dot

    except Exception as e:
        st.error(f"Error generating diagram: {e}")
        import traceback
        st.code(traceback.format_exc())
        return None


# Database Schema Generator Section
st.markdown("---")
st.subheader("Database Schema Overview")

project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
output_path = os.path.join(project_root, "docs", "scalpel_dbdiagram.txt")

# Initialize schema in session state if not exists
if 'schema_diagram' not in st.session_state:
    st.session_state.schema_diagram = None

col1, col2 = st.columns([1, 4])

with col1:
    if st.button("Update Schema", type="primary"):
        with st.spinner("Generating database schema..."):
            try:
                # Import and run the schema generator for dbdiagram.io
                sys.path.insert(0, os.path.join(project_root, "scripts", "helpers"))
                from sqlite_to_dbdiagram import sqlite_to_dbdiagram

                current_db_path = st.session_state.get("db_path", get_db_path())

                # Check if DB exists
                if not os.path.exists(current_db_path):
                    st.error(f"Database not found at: {current_db_path}")
                else:
                    sqlite_to_dbdiagram(current_db_path, output_path)

                    # Generate visual diagram
                    diagram = generate_erd_diagram(current_db_path)

                    if diagram is None:
                        st.error("Failed to generate diagram")
                    else:
                        st.session_state.schema_diagram = diagram
                        st.success("✓ Schema updated")

            except Exception as e:
                st.error(f"Error generating schema: {e}")
                import traceback

                st.code(traceback.format_exc())

    # Auto-load schema on first visit
    if st.session_state.schema_diagram is None:
        current_db_path = st.session_state.get("db_path", get_db_path())
        if os.path.exists(current_db_path):
            st.session_state.schema_diagram = generate_erd_diagram(current_db_path)

with col2:
    st.markdown("**Database Schema Visualization**")

# Display the diagram
if st.session_state.schema_diagram:
    try:
        # Render to PNG and display as image
        png_bytes = st.session_state.schema_diagram.pipe(format='png')

        # Convert to PIL Image and display
        image = Image.open(io.BytesIO(png_bytes))
        st.image(image, width="stretch")  # FIXED: use_container_width deprecated -> width="stretch"

        # Show debug info
        with st.expander("View Graphviz Source"):
            st.code(st.session_state.schema_diagram.source, language='dot')

    except Exception as e:
        st.error(f"Error displaying diagram: {e}")
        import traceback

        st.code(traceback.format_exc())

        # Fallback to graphviz_chart
        try:
            st.graphviz_chart(st.session_state.schema_diagram)
        except:
            st.code(st.session_state.schema_diagram.source, language='dot')
else:
    st.info("Click 'Update Schema' to generate and display the database diagram.")