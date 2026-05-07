"""Database Management page — table browser, inserter, and row deleter.

Faithful NiceGUI port of the original Streamlit ``1_Database.py``. Builds the
input form dynamically from the selected table's schema, with special handling
for the ``anesthesiology`` table (auto-incremented key, auto-generated
``staff_code`` from name + start date).
"""

from datetime import date as dt_date

import pandas as pd
from nicegui import ui

from app import state
from app.layout import page_frame
from app.utils import connect, get_table_schema, list_tables, load_table


SKIP_COLUMNS = {"date_case", "months_anesthetic_recording", "anesthetic_attending"}


def get_next_anesthesiology_key(db_path: str) -> int:
    try:
        with connect(db_path) as conn:
            cur = conn.cursor()
            cur.execute("SELECT MAX(anesthesiology_key) FROM anesthesiology")
            result = cur.fetchone()[0]
            return (result + 1) if result else 1
    except Exception:
        return 1


def generate_anesthesiology_code(name: str | None, start_date: str | None) -> str:
    """FirstInitial + LastInitial + YYMM, e.g. ``Maria Kobzeva, 2015-10-01 -> MK1510``."""
    if not name or not start_date:
        return ""
    parts = name.strip().split()
    if len(parts) < 2:
        return ""
    first_initial = parts[0][0].upper()
    last_initial = parts[-1][0].upper()
    date_str = str(start_date)
    if len(date_str) >= 10:
        return f"{first_initial}{last_initial}{date_str[2:4]}{date_str[5:7]}"
    return ""


def _column_classifier(col_name: str, ctype: str) -> str:
    """Return one of: date / time / signature_time / int / float / textarea / text."""
    if "DATE" in ctype or col_name.endswith("_date") or col_name == "date":
        return "date"
    if "TIME" in ctype or col_name.endswith("_time") or "time" in col_name.lower():
        if "signature" in col_name.lower():
            return "signature_time"
        return "time"
    if "INT" in ctype:
        return "int"
    if "REAL" in ctype or "FLOA" in ctype or "DOUB" in ctype:
        return "float"
    if col_name.lower() in ("comments", "comment", "notes"):
        return "textarea"
    return "text"


@ui.page("/database")
def database_page() -> None:
    with page_frame("Database Management"):
        db_path = state.get()
        ui.label("Database Management").classes("section-h text-h5 text-weight-medium")

        try:
            tables = list_tables(db_path)
        except Exception as exc:
            ui.label(f"Could not list tables: {exc}").classes("text-negative")
            return

        if not tables:
            ui.label("No tables found.").classes("text-warning")
            return

        # Mutable state shared between refreshable sections.
        ctx: dict = {
            "table": tables[0],
            "inputs": {},  # col_name -> nicegui input element OR static value
            "anes": {
                "name": "",
                "start_date": None,
                "code_manual": False,
                "code_value": "",
            },
        }

        @ui.refreshable
        def form_section() -> None:
            table = ctx["table"]
            ctx["inputs"] = {}

            schema_df = get_table_schema(db_path, table)
            cols_meta = schema_df.to_dict(orient="records")

            ui.label(f"Insert into {table}").classes("text-subtitle1 text-weight-medium")

            anes = ctx["anes"]
            is_anes = table == "anesthesiology"
            next_key = get_next_anesthesiology_key(db_path) if is_anes else None
            if is_anes:
                ui.label(f"Next available anesthesiology key: {next_key}").classes(
                    "text-info"
                )
                ctx["inputs"]["anesthesiology_key"] = next_key

            # Anesthesiology code-generation glue.
            name_input: ui.input | None = None
            date_input: ui.input | None = None
            code_input: ui.input | None = None

            def recompute_code() -> None:
                if not (name_input and date_input and code_input):
                    return
                auto = generate_anesthesiology_code(name_input.value, date_input.value)
                if not anes["code_manual"]:
                    code_input.value = auto

            for col in cols_meta:
                col_name = col["name"]
                ctype = (col["type"] or "").upper()

                if is_anes and col_name == "anesthesiology_key":
                    continue
                if col_name.lower() in SKIP_COLUMNS:
                    continue

                kind = _column_classifier(col_name, ctype)

                if is_anes and col_name == "name":
                    name_input = ui.input(col_name, value=anes["name"]).classes("w-full")
                    name_input.on_value_change(lambda e: (anes.__setitem__("name", e.value), recompute_code()))
                    ctx["inputs"][col_name] = name_input
                    continue

                if is_anes and col_name == "anesthesiology_start_date":
                    default = anes["start_date"] or dt_date.today().strftime("%Y-%m-%d")
                    with ui.input(col_name, value=default).classes("w-full") as date_input:
                        with ui.menu().props("no-parent-event") as menu:
                            with ui.date().bind_value(date_input):
                                with ui.row().classes("justify-end"):
                                    ui.button("Close", on_click=menu.close).props("flat")
                        with date_input.add_slot("append"):
                            ui.icon("event").on("click", menu.open).classes("cursor-pointer")
                    date_input.on_value_change(
                        lambda e: (anes.__setitem__("start_date", e.value), recompute_code())
                    )
                    ctx["inputs"][col_name] = date_input
                    continue

                if is_anes and col_name == "staff_code":
                    auto = generate_anesthesiology_code(anes["name"], anes["start_date"])
                    initial = anes["code_value"] if anes["code_manual"] else auto
                    code_input = ui.input(
                        col_name,
                        value=initial or "",
                    ).classes("w-full")
                    code_input.props('hint="Auto-generated from name + start date; edit to override"')

                    def on_code_change(e, ci=code_input):
                        auto_now = generate_anesthesiology_code(anes["name"], anes["start_date"])
                        if e.value != auto_now:
                            anes["code_manual"] = True
                            anes["code_value"] = e.value
                        else:
                            anes["code_manual"] = False
                            anes["code_value"] = ""

                    code_input.on_value_change(on_code_change)
                    ctx["inputs"][col_name] = code_input
                    continue

                # Generic fields.
                if kind == "date":
                    el = ui.input(col_name, value=dt_date.today().strftime("%Y-%m-%d")).classes("w-full")
                    with el:
                        with ui.menu().props("no-parent-event") as menu:
                            with ui.date().bind_value(el):
                                with ui.row().classes("justify-end"):
                                    ui.button("Close", on_click=menu.close).props("flat")
                        with el.add_slot("append"):
                            ui.icon("event").on("click", menu.open).classes("cursor-pointer")
                    ctx["inputs"][col_name] = el
                elif kind == "signature_time":
                    with ui.row().classes("w-full no-wrap"):
                        d_el = ui.input(f"{col_name} (date)", value=dt_date.today().strftime("%Y-%m-%d")).classes("flex-grow")
                        with d_el:
                            with ui.menu().props("no-parent-event") as d_menu:
                                with ui.date().bind_value(d_el):
                                    with ui.row().classes("justify-end"):
                                        ui.button("Close", on_click=d_menu.close).props("flat")
                            with d_el.add_slot("append"):
                                ui.icon("event").on("click", d_menu.open).classes("cursor-pointer")
                        t_el = ui.input(f"{col_name} (time)", value="00:00:00").classes("flex-grow")
                        with t_el:
                            with ui.menu().props("no-parent-event") as t_menu:
                                with ui.time().bind_value(t_el):
                                    with ui.row().classes("justify-end"):
                                        ui.button("Close", on_click=t_menu.close).props("flat")
                            with t_el.add_slot("append"):
                                ui.icon("schedule").on("click", t_menu.open).classes("cursor-pointer")
                    ctx["inputs"][col_name] = ("signature_time", d_el, t_el)
                elif kind == "time":
                    el = ui.input(col_name, value="00:00:00").classes("w-full")
                    with el:
                        with ui.menu().props("no-parent-event") as menu:
                            with ui.time().bind_value(el):
                                with ui.row().classes("justify-end"):
                                    ui.button("Close", on_click=menu.close).props("flat")
                        with el.add_slot("append"):
                            ui.icon("schedule").on("click", menu.open).classes("cursor-pointer")
                    ctx["inputs"][col_name] = el
                elif kind == "int":
                    ctx["inputs"][col_name] = ui.number(col_name, value=0, format="%d", step=1).classes("w-full")
                elif kind == "float":
                    ctx["inputs"][col_name] = ui.number(col_name, value=0).classes("w-full")
                elif kind == "textarea":
                    ctx["inputs"][col_name] = ui.textarea(col_name).classes("w-full")
                else:
                    ctx["inputs"][col_name] = ui.input(col_name).classes("w-full")

            ui.button("Insert Row", on_click=do_insert).props("color=primary").classes("q-mt-md")

        def collect_values() -> dict:
            values: dict = {}
            for col_name, el in ctx["inputs"].items():
                if isinstance(el, tuple) and el[0] == "signature_time":
                    _, d_el, t_el = el
                    if d_el.value and t_el.value:
                        values[col_name] = f"{d_el.value} {t_el.value}"
                    else:
                        values[col_name] = None
                elif hasattr(el, "value"):
                    v = el.value
                    values[col_name] = v if v not in ("",) else None
                else:
                    values[col_name] = el  # static (e.g. anesthesiology_key)
            return values

        def do_insert() -> None:
            cleaned = collect_values()
            try:
                with connect(db_path) as conn:
                    cur = conn.cursor()
                    keys = ",".join(cleaned.keys())
                    qmarks = ",".join(["?"] * len(cleaned))
                    cur.execute(
                        f"INSERT INTO {ctx['table']} ({keys}) VALUES ({qmarks})",
                        tuple(cleaned.values()),
                    )
                ui.notify(f"Inserted into {ctx['table']}.", type="positive")
                if ctx["table"] == "anesthesiology":
                    ctx["anes"] = {
                        "name": "",
                        "start_date": None,
                        "code_manual": False,
                        "code_value": "",
                    }
                form_section.refresh()
                data_grid.refresh()
                delete_section.refresh()
            except Exception as exc:
                ui.notify(f"Insert failed: {exc}", type="negative")

        @ui.refreshable
        def data_grid() -> None:
            ui.label(f"{ctx['table']} — rows").classes("text-subtitle1 text-weight-medium")
            df = load_table(db_path, ctx["table"])
            if df.empty:
                ui.label("Table is empty.").classes("text-warning")
                return
            ui.aggrid({
                "defaultColDef": {
                    "sortable": True, "filter": True,
                    "resizable": True, "floatingFilter": True,
                },
                "columnDefs": [{"field": c} for c in df.columns],
                "rowData": df.to_dict("records"),
                "pagination": True,
                "paginationPageSize": 25,
                "animateRows": True,
            }).classes("ag-theme-balham w-full").style("height: 540px")

        @ui.refreshable
        def delete_section() -> None:
            ui.label("Delete row").classes("text-subtitle1 text-weight-medium")

            table = ctx["table"]
            df = load_table(db_path, table)
            if df.empty:
                ui.label("Table is empty. No rows to delete.").classes("text-warning")
                return

            with connect(db_path) as conn:
                cur = conn.cursor()
                cur.execute(f"PRAGMA table_info({table})")
                columns_info = cur.fetchall()
                pk_columns = [c[1] for c in columns_info if c[5] > 0]

            if not pk_columns:
                ui.label("This table has no primary key defined. Cannot delete rows safely.").classes(
                    "text-warning"
                )
                return

            ui.label(f"Select row to delete by primary key: {', '.join(pk_columns)}")

            pk_inputs: dict = {}
            with ui.row().classes("w-full no-wrap"):
                for pk_col in pk_columns:
                    unique_vals = [v for v in df[pk_col].unique().tolist() if v is not None]
                    col_dtype = df[pk_col].dtype
                    if str(col_dtype).startswith("int"):
                        options = sorted([int(v) for v in unique_vals])
                    else:
                        options = sorted([str(v) for v in unique_vals])
                    if not options:
                        continue
                    pk_inputs[pk_col] = ui.select(
                        options=options, value=options[0], label=pk_col
                    ).classes("flex-grow")

            preview_container = ui.column().classes("w-full q-mt-sm")

            def show_preview() -> None:
                preview_container.clear()
                if not pk_inputs:
                    return
                condition = " AND ".join(f"{k} = ?" for k in pk_inputs)
                params = tuple(el.value for el in pk_inputs.values())
                with connect(db_path) as conn:
                    matching = pd.read_sql_query(
                        f"SELECT * FROM {table} WHERE {condition}", conn, params=params
                    )
                with preview_container:
                    if matching.empty:
                        ui.label("No matching row found.").classes("text-warning")
                    else:
                        ui.label("Row to be deleted:").classes("text-bold")
                        ui.aggrid.from_pandas(matching).classes("w-full").style(
                            "height: 150px"
                        )
                        ui.button(
                            "Delete Row",
                            on_click=lambda: do_delete(pk_inputs),
                        ).props("color=negative icon=delete")

            for el in pk_inputs.values():
                el.on_value_change(lambda _: show_preview())

            show_preview()

        def do_delete(pk_inputs: dict) -> None:
            try:
                where = " AND ".join(f"{k} = ?" for k in pk_inputs)
                params = tuple(el.value for el in pk_inputs.values())
                with connect(db_path) as conn:
                    cur = conn.cursor()
                    cur.execute(f"DELETE FROM {ctx['table']} WHERE {where}", params)
                ui.notify(f"Row deleted from {ctx['table']}.", type="positive")
                data_grid.refresh()
                delete_section.refresh()
            except Exception as exc:
                ui.notify(f"Delete failed: {exc}", type="negative")

        def on_table_change(e) -> None:
            ctx["table"] = e.value
            ctx["anes"] = {
                "name": "",
                "start_date": None,
                "code_manual": False,
                "code_value": "",
            }
            form_section.refresh()
            data_grid.refresh()
            delete_section.refresh()

        with ui.card().classes("surface-1 w-full q-pa-md"):
            ui.label("Target table").classes("text-caption muted")
            ui.select(
                options=tables,
                value=ctx["table"],
                on_change=on_table_change,
            ).props("outlined dense options-dense").classes("w-full")

        with ui.tabs().classes("w-full") as table_tabs:
            t_browse = ui.tab("Browse",  icon="table_view")
            t_insert = ui.tab("Insert",  icon="add_circle")
            t_delete = ui.tab("Delete",  icon="delete_outline")

        with ui.tab_panels(table_tabs, value=t_browse).classes("w-full surface-1"):
            with ui.tab_panel(t_browse):
                data_grid()
            with ui.tab_panel(t_insert):
                form_section()
            with ui.tab_panel(t_delete):
                delete_section()
