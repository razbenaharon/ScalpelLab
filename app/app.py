"""Main Streamlit application for ScalpelLab Database Manager.

This is the entry point for the web-based database management interface. The app
provides:

- **Homepage**: Project overview and navigation guide
- **Database Schema Visualization**: Interactive Entity-Relationship Diagram (ERD)
  with PyCharm-style aesthetics
- **Multi-page Interface**: Access to database management, status summaries, and views

The ERD generator uses Graphviz to create professional diagrams showing tables,
columns, data types, primary keys, foreign keys, and relationships with proper
crow's foot notation.

Pages:
    - 1_Database.py: Browse tables, insert/delete records
    - 2_Status_Summary.py: View statistics and visualizations
    - 3_Views.py: Query database views

Note:
    The database path is configurable via the sidebar and defaults to the path
    specified in config.py. The ERD diagram auto-loads on first visit and can
    be manually refreshed using the "Update Schema" button.
"""

import os
import sys
import streamlit as st
import sqlite3
import graphviz
from PIL import Image
import io
from typing import Optional

# Add Graphviz to PATH for Windows
if sys.platform == 'win32':
    graphviz_bin = r'C:\Program Files\Graphviz\bin'
    if graphviz_bin not in os.environ['PATH']:
        os.environ['PATH'] += os.pathsep + graphviz_bin

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


def generate_erd_diagram(db_path: str) -> Optional[graphviz.Digraph]:
    """Generate an Entity-Relationship Diagram with PyCharm-style aesthetics.

    Creates a professional database schema visualization using Graphviz, showing:
    - Tables with column details (name, type, constraints)
    - Primary keys highlighted in light yellow
    - Foreign keys highlighted in light blue
    - Relationships with crow's foot notation (one-to-many)
    - Curved connection lines to prevent visual clutter

    The diagram uses a left-to-right layout with increased spacing to prevent
    edge overlapping. Color scheme and styling mimic PyCharm's database diagram
    viewer for familiarity.

    Args:
        db_path: Path to the SQLite database file.

    Returns:
        Optional[graphviz.Digraph]: Graphviz diagram object if successful,
            None if an error occurs during generation.

    Raises:
        Exception: Catches and logs all exceptions, displaying errors in Streamlit UI.

    Note:
        Visual improvements applied:
        - splines='curved': Prevents overlapping 'barcode' lines
        - ranksep/nodesep: Increased spacing for clarity
        - Crow's foot notation: 'crow' arrowhead (many), 'otee' arrowtail (one)
    """
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

        # Build a cache of primary key columns for all tables
        pk_cache = {}
        for table in tables:
            cursor.execute(f"PRAGMA table_info({table})")
            columns = cursor.fetchall()
            # Get all primary key columns sorted by pk index
            pk_cols = sorted([col for col in columns if col[5] > 0], key=lambda x: x[5])
            pk_cache[table] = [col[1] for col in pk_cols]

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

                # Skip if local column is not specified
                if not local_col:
                    continue

                # If ref_col is not specified, SQLite uses the primary key of the referenced table
                if not ref_col:
                    # Get the primary key column(s) of the referenced table
                    pk_cols = pk_cache.get(ref_table, [])
                    if not pk_cols:
                        continue  # No primary key found, skip this FK

                    # For composite keys, we need to match by position
                    # Get the sequence number of this FK constraint
                    fk_seq = fk[1]

                    # Use the corresponding PK column if available
                    if fk_seq < len(pk_cols):
                        ref_col = pk_cols[fk_seq]
                    else:
                        ref_col = pk_cols[0]  # Fallback to first PK column

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
st.markdown("### Database Schema Visualization")
st.markdown("""
Explore your database structure with an interactive Entity-Relationship Diagram (ERD).
The diagram shows all tables, columns, data types, and relationships using crow's foot notation.
""")

project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
output_path = os.path.join(project_root, "docs", "scalpel_dbdiagram.txt")

# Initialize schema in session state if not exists
if 'schema_diagram' not in st.session_state:
    st.session_state.schema_diagram = None
if 'schema_stats' not in st.session_state:
    st.session_state.schema_stats = None

# Auto-load schema on first visit
if st.session_state.schema_diagram is None:
    current_db_path = st.session_state.get("db_path", get_db_path())
    if os.path.exists(current_db_path):
        with st.spinner("Loading database schema..."):
            st.session_state.schema_diagram = generate_erd_diagram(current_db_path)
            # Get schema statistics
            try:
                conn = sqlite3.connect(current_db_path)
                cursor = conn.cursor()
                cursor.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
                table_count = cursor.fetchone()[0]

                # Count total foreign keys
                cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
                tables = [row[0] for row in cursor.fetchall()]
                total_fks = 0
                for table in tables:
                    cursor.execute(f"PRAGMA foreign_key_list({table})")
                    total_fks += len(cursor.fetchall())
                conn.close()

                st.session_state.schema_stats = {
                    'tables': table_count,
                    'relationships': total_fks
                }
            except:
                st.session_state.schema_stats = None

# Controls and statistics
col1, col2, col3, col4 = st.columns([2, 1, 1, 2])

with col1:
    if st.button("Refresh Schema", type="primary", use_container_width=True):
        with st.spinner("Regenerating database schema..."):
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

                        # Update statistics
                        conn = sqlite3.connect(current_db_path)
                        cursor = conn.cursor()
                        cursor.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
                        table_count = cursor.fetchone()[0]

                        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
                        tables = [row[0] for row in cursor.fetchall()]
                        total_fks = 0
                        for table in tables:
                            cursor.execute(f"PRAGMA foreign_key_list({table})")
                            total_fks += len(cursor.fetchall())
                        conn.close()

                        st.session_state.schema_stats = {
                            'tables': table_count,
                            'relationships': total_fks
                        }
                        st.success("Schema refreshed successfully")

            except Exception as e:
                st.error(f"Error generating schema: {str(e)}")

# Display statistics if available
if st.session_state.schema_stats:
    with col2:
        st.metric("Tables", st.session_state.schema_stats['tables'])
    with col3:
        st.metric("Relationships", st.session_state.schema_stats['relationships'])

# Display the diagram
st.markdown("") # Spacing
if st.session_state.schema_diagram:
    try:
        # Render to PNG and display as image
        png_bytes = st.session_state.schema_diagram.pipe(format='png')

        # Convert to PIL Image and display
        image = Image.open(io.BytesIO(png_bytes))
        st.image(image, use_container_width=True)

        # Action buttons and download options
        col_a, col_b, col_c = st.columns([2, 2, 6])

        with col_a:
            # Download as PNG
            st.download_button(
                label="Download PNG",
                data=png_bytes,
                file_name="database_schema.png",
                mime="image/png",
                use_container_width=True
            )

        with col_b:
            # Download as DOT source
            st.download_button(
                label="Download DOT",
                data=st.session_state.schema_diagram.source,
                file_name="database_schema.dot",
                mime="text/plain",
                use_container_width=True
            )

        # Show debug info
        with st.expander("View Graphviz Source Code"):
            st.code(st.session_state.schema_diagram.source, language='dot')

    except Exception as e:
        st.error(f"Error displaying diagram: {str(e)}")

        # Show error details in expander
        with st.expander("Error Details"):
            import traceback
            st.code(traceback.format_exc())

        # Fallback to graphviz_chart
        st.warning("Attempting fallback rendering method...")
        try:
            st.graphviz_chart(st.session_state.schema_diagram)
        except Exception as fallback_error:
            st.error(f"Fallback rendering also failed: {str(fallback_error)}")
else:
    st.info("No schema diagram available. Click 'Refresh Schema' to generate the visualization.")