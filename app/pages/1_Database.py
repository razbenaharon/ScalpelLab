"""Database Management Page - Table Browser and Editor.

This Streamlit page provides a comprehensive interface for managing database tables:

Features:
    - **Table Browser**: View all tables with sortable, filterable data grids
    - **Record Insertion**: Add new records with auto-generated keys and validation
    - **Record Deletion**: Delete records with confirmation prompts
    - **Schema Inspection**: View table structure and constraints
    - **Smart Defaults**: Auto-populates anesthesiology_key and generates codes

Special Functions:
    - Auto-increment for anesthesiology_key
    - Code generation for anesthesiology table (FirstInitial + LastInitial + YYMM)
    - Validation for required fields

Navigation:
    Access via sidebar: Pages → 1_Database
"""

import streamlit as st
import sys, os
# This line adds the project root to the path to fix the import error
sys.path.append(os.path.dirname(os.path.dirname(__file__)))

from utils import (
    list_tables, get_table_schema, load_table, connect,
    get_database_schema_graphviz, clear_erd_cache
)

def get_next_anesthesiology_key(db_path):
    """Get the next available anesthesiology_key"""
    try:
        with connect(db_path) as conn:
            cur = conn.cursor()
            cur.execute("SELECT MAX(anesthesiology_key) FROM anesthesiology")
            result = cur.fetchone()[0]
            return (result + 1) if result else 1
    except Exception:
        return 1

def generate_anesthesiology_code(name, start_date):
    """
    Generate anesthesiology code from name and start date.
    Format: FirstInitial + LastInitial + YYMM
    Example: Maria Kobzeva, 2015-10-01 -> MK1510
    """
    if not name or not start_date:
        return ""

    # Parse name - take first and last word
    parts = name.strip().split()
    if len(parts) < 2:
        return ""

    first_initial = parts[0][0].upper()
    last_initial = parts[-1][0].upper()

    # Parse date - extract YY and MM
    # Date format: YYYY-MM-DD
    date_str = str(start_date)
    if len(date_str) >= 10:
        year = date_str[2:4]  # YY
        month = date_str[5:7]  # MM
        return f"{first_initial}{last_initial}{year}{month}"

    return ""

st.header("🗄️ Database Management")

db_path = st.session_state.get("db_path")

if not db_path:
    st.warning("No database path set.")
else:
    tables = list_tables(db_path)
    if not tables:
        st.info("No tables found.")
    else:
        table_choice = st.selectbox("Target table", options=tables)

        schema_df = get_table_schema(db_path, table_choice)
        cols_meta = schema_df.to_dict(orient="records")

        # Special handling for anesthesiology table
        if table_choice == "anesthesiology":
            # Auto-generate anesthesiology_key
            next_key = get_next_anesthesiology_key(db_path)
            st.info(f"Next available anesthesiology key: {next_key}")

        input_values = {}

        # For anesthesiology table, process fields in specific order to enable auto-generation
        if table_choice == "anesthesiology":
            # Initialize session state for anesthesiology fields if not present
            if 'anes_name' not in st.session_state:
                st.session_state.anes_name = ""
            if 'anes_start_date' not in st.session_state:
                st.session_state.anes_start_date = None
            if 'anes_code_manual' not in st.session_state:
                st.session_state.anes_code_manual = False

            for col in cols_meta:
                col_name = col["name"]

                # Skip auto-generated key
                if col_name == "anesthesiology_key":
                    input_values[col_name] = next_key
                    continue

                # Skip common generated columns
                if col_name.lower() in {"date_case", "months_anesthetic_recording", "anesthetic_attending"}:
                    continue

                ctype = (col["type"] or "").upper()

                # Handle name field
                if col_name == "name":
                    name_value = st.text_input(col_name, value=st.session_state.anes_name, key="anes_name_input")
                    st.session_state.anes_name = name_value
                    input_values[col_name] = name_value if name_value else None

                # Handle anesthesiology_start_date field
                elif col_name == "anesthesiology_start_date":
                    from datetime import date as dt_date
                    default_date = st.session_state.anes_start_date if st.session_state.anes_start_date else dt_date.today()
                    d = st.date_input(col_name, value=default_date, key="anes_start_date_input")
                    st.session_state.anes_start_date = d
                    date_str = d.strftime("%Y-%m-%d") if d else None
                    input_values[col_name] = date_str

                # Handle code field - auto-generate but allow editing
                elif col_name == "code":
                    # Generate code from current name and date
                    date_str = st.session_state.anes_start_date.strftime("%Y-%m-%d") if st.session_state.anes_start_date else None
                    auto_code = generate_anesthesiology_code(st.session_state.anes_name, date_str)

                    # Show auto-generated code
                    if auto_code:
                        st.info(f"Auto-generated code: **{auto_code}**")

                    # Use auto-generated code unless user manually edited it
                    if not st.session_state.anes_code_manual:
                        code_default = auto_code
                    else:
                        code_default = st.session_state.get('anes_code_value', auto_code)

                    code_value = st.text_input(
                        col_name,
                        value=code_default,
                        key="anes_code_input",
                        help="Auto-generated from name and start date. You can edit if needed."
                    )

                    # Track if user manually edited the code
                    if code_value != auto_code:
                        st.session_state.anes_code_manual = True
                        st.session_state.anes_code_value = code_value
                    else:
                        st.session_state.anes_code_manual = False

                    input_values[col_name] = code_value if code_value else None

                # Handle other date fields
                elif "DATE" in ctype or col_name.endswith("_date") or col_name == "date":
                    d = st.date_input(col_name)
                    input_values[col_name] = d.strftime("%Y-%m-%d") if d else None

                # Handle other fields normally
                elif "INT" in ctype:
                    input_values[col_name] = st.number_input(col_name, step=1)
                elif "REAL" in ctype or "FLOA" in ctype or "DOUB" in ctype:
                    input_values[col_name] = st.number_input(col_name)
                elif col_name.lower() in ("comments", "comment", "notes"):
                    input_values[col_name] = st.text_area(col_name)
                else:
                    input_values[col_name] = st.text_input(col_name)

        else:
            # Standard processing for other tables
            for col in cols_meta:
                name = col["name"]
                # skip common generated column patterns (adjust if needed)
                if name.lower() in {"date_case", "months_anesthetic_recording", "anesthetic_attending"}:
                    continue

                ctype = (col["type"] or "").upper()
                if "DATE" in ctype or name.endswith("_date") or name == "date":
                    d = st.date_input(name)
                    input_values[name] = d.strftime("%Y-%m-%d") if d else None
                elif "TIME" in ctype or name.endswith("_time") or "time" in name.lower():
                    if "signature" in name.lower():
                        # For signature_time, we need both date and time
                        col1, col2 = st.columns(2)
                        with col1:
                            d = st.date_input(f"{name} (date)")
                        with col2:
                            t = st.time_input(f"{name} (time)")
                        if d and t:
                            input_values[name] = f"{d.strftime('%Y-%m-%d')} {t.strftime('%H:%M:%S')}"
                        else:
                            input_values[name] = None
                    else:
                        t = st.time_input(name)
                        input_values[name] = t.strftime("%H:%M:%S") if t else None
                elif "INT" in ctype:
                    input_values[name] = st.number_input(name, step=1)
                elif "REAL" in ctype or "FLOA" in ctype or "DOUB" in ctype:
                    input_values[name] = st.number_input(name)
                elif name.lower() in ("comments", "comment", "notes"):
                    input_values[name] = st.text_area(name)
                else:
                    input_values[name] = st.text_input(name)

        if st.button("Insert Row"):
            cleaned = {k: (v if v != "" else None) for k, v in input_values.items()}
            try:
                with connect(db_path) as conn:
                    cur = conn.cursor()
                    keys = ",".join(cleaned.keys())
                    qmarks = ",".join(["?"] * len(cleaned))
                    cur.execute(
                        f"INSERT INTO {table_choice} ({keys}) VALUES ({qmarks})",
                        tuple(cleaned.values()),
                    )
                st.success(f"Inserted into {table_choice}.")

                # Clear anesthesiology form session state after successful insert
                if table_choice == "anesthesiology":
                    st.session_state.anes_name = ""
                    st.session_state.anes_start_date = None
                    st.session_state.anes_code_manual = False
                    if 'anes_code_value' in st.session_state:
                        del st.session_state.anes_code_value

                load_table.clear()
                st.rerun()
            except Exception as e:
                st.error(f"Insert failed: {e}")

        st.divider()

        # Display table data
        st.subheader(f"📊 {table_choice} Data")
        table_data = load_table(db_path, table_choice)
        st.dataframe(table_data, width='stretch', hide_index=True)

        # Delete row section
        st.divider()
        st.subheader("🗑️ Delete Row")

        if not table_data.empty:
            # Get primary key columns
            with connect(db_path) as conn:
                cur = conn.cursor()
                cur.execute(f"PRAGMA table_info({table_choice})")
                columns_info = cur.fetchall()
                pk_columns = [col[1] for col in columns_info if col[5] > 0]  # col[5] is pk flag

            if pk_columns:
                st.write(f"Select row to delete by primary key: **{', '.join(pk_columns)}**")

                # Create input fields for primary key values
                delete_values = {}
                cols = st.columns(len(pk_columns))
                for i, pk_col in enumerate(pk_columns):
                    with cols[i]:
                        # Get unique values for this primary key column
                        unique_vals = table_data[pk_col].unique().tolist()

                        # Determine input type based on column type
                        col_type = table_data[pk_col].dtype
                        if col_type in ['int64', 'int32']:
                            # For integer primary keys, use selectbox with sorted values
                            unique_vals_sorted = sorted([int(v) for v in unique_vals if v is not None])
                            if unique_vals_sorted:
                                delete_values[pk_col] = st.selectbox(
                                    pk_col,
                                    options=unique_vals_sorted,
                                    key=f"delete_{pk_col}"
                                )
                        else:
                            # For text/date primary keys, use selectbox
                            unique_vals_sorted = sorted([str(v) for v in unique_vals if v is not None])
                            if unique_vals_sorted:
                                delete_values[pk_col] = st.selectbox(
                                    pk_col,
                                    options=unique_vals_sorted,
                                    key=f"delete_{pk_col}"
                                )

                # Show matching row
                if delete_values:
                    condition = " AND ".join([f"{k} = ?" for k in delete_values.keys()])
                    with connect(db_path) as conn:
                        import pandas as pd
                        query = f"SELECT * FROM {table_choice} WHERE {condition}"
                        matching_row = pd.read_sql_query(query, conn, params=tuple(delete_values.values()))

                    if not matching_row.empty:
                        st.write("**Row to be deleted:**")
                        st.dataframe(matching_row, hide_index=True)

                        col1, col2 = st.columns([1, 4])
                        with col1:
                            if st.button("🗑️ Delete Row", type="primary"):
                                try:
                                    with connect(db_path) as conn:
                                        cur = conn.cursor()
                                        where_clause = " AND ".join([f"{k} = ?" for k in delete_values.keys()])
                                        cur.execute(
                                            f"DELETE FROM {table_choice} WHERE {where_clause}",
                                            tuple(delete_values.values())
                                        )
                                    st.success(f"Row deleted from {table_choice}!")
                                    load_table.clear()
                                    st.rerun()
                                except Exception as e:
                                    st.error(f"Delete failed: {e}")
                    else:
                        st.warning("No matching row found.")
            else:
                st.info("This table has no primary key defined. Cannot delete rows safely.")
        else:
            st.info("Table is empty. No rows to delete.")

        # ERD Visualization Section
        st.divider()
        st.subheader("📊 Database Schema (ERD)")

        col1, col2 = st.columns([1, 4])
        with col1:
            if st.button("🔄 Update Schema", help="Regenerate the ERD visualization"):
                clear_erd_cache()
                st.rerun()

        st.markdown("""
        **Entity-Relationship Diagram** (HD Vector Graphics) showing database structure with:
        - 🔑 **Primary Keys** (Gold)
        - 🔗 **Foreign Keys** (Blue) - inferred from naming conventions
        - Logical grouping by table type
        - Crow's foot notation for relationships
        - ⚡ **High Resolution**: SVG format scales perfectly at any zoom level
        """)

        # Manual overrides for special FK relationships (if needed)
        # Format: {(source_table, source_col): (target_table, target_col)}
        manual_overrides = {
            # Example: ('recording_details', 'code'): ('anesthesiology', 'code'),
            # Add any special relationships here that can't be inferred automatically
        }

        try:
            dot_source = get_database_schema_graphviz(db_path, manual_overrides)

            # Render as high-resolution SVG for perfect scaling
            import graphviz
            import tempfile
            import base64
            from pathlib import Path

            # Create Graphviz object from DOT source
            graph = graphviz.Source(dot_source)

            # Render to SVG (vector graphics - infinite resolution)
            svg_data = graph.pipe(format='svg').decode('utf-8')

            # Display SVG with custom styling for better quality
            st.markdown(
                f'<div style="background-color: white; padding: 20px; border-radius: 10px; '
                f'box-shadow: 0 2px 4px rgba(0,0,0,0.1);">'
                f'{svg_data}'
                f'</div>',
                unsafe_allow_html=True
            )

            # Alternative: Provide download buttons for different formats
            col1, col2, col3 = st.columns([1, 1, 3])
            with col1:
                # Generate high-DPI PNG for download
                try:
                    # Try cairo renderer first (best quality)
                    png_bytes = graph.pipe(format='png', renderer='cairo', formatter='cairo')
                except:
                    # Fallback to default renderer (still high quality with dpi=300)
                    png_bytes = graph.pipe(format='png')

                st.download_button(
                    label="📥 PNG (HD)",
                    data=png_bytes,
                    file_name="database_erd_hd.png",
                    mime="image/png",
                    help="Download high-resolution PNG (300 DPI)"
                )

            with col2:
                # Also offer SVG download (vector - infinite resolution)
                st.download_button(
                    label="📥 SVG (Vector)",
                    data=svg_data.encode('utf-8'),
                    file_name="database_erd.svg",
                    mime="image/svg+xml",
                    help="Download vector SVG (scales to any size without quality loss)"
                )

            with st.expander("ℹ️ About Smart Relationship Inference"):
                st.markdown("""
                Since SQLite doesn't always enforce foreign key constraints, this ERD uses **smart inference**:

                **Inference Rules:**
                1. Columns named `tablename_id` or `tablename_key` are assumed to reference the `tablename` table
                2. The target column is matched in this order:
                   - Exact column name match (e.g., `anesthesiology_key` → `anesthesiology.anesthesiology_key`)
                   - Standard `id` column
                   - Table's primary key column

                **Example:**
                - `recording_details.anesthesiology_key` → `anesthesiology.anesthesiology_key`
                - `mp4_status.recording_date`, `mp4_status.case_no` → `recording_details.(recording_date, case_no)`

                **Manual Overrides:**
                You can define custom relationships in the code by editing the `manual_overrides` dictionary.
                """)

        except Exception as e:
            st.error(f"Failed to generate ERD: {e}")
            st.info("Make sure the `graphviz` Python package is installed: `pip install graphviz`")