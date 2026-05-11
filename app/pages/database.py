"""Database Management page — table browser, inserter, and row deleter.

Faithful NiceGUI port of the original Streamlit ``1_Database.py``. Builds the
input form dynamically from the selected table's schema, with special handling
for the ``anesthesiology`` table (auto-incremented key, auto-generated
``staff_code`` from name + start date).
"""

import os
from datetime import date as dt_date

import pandas as pd
from nicegui import ui

from app import state
from app.layout import page_frame
from app.utils import connect, get_table_schema, list_tables, load_table


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ERD_PDF_PATH = os.path.join(PROJECT_ROOT, "docs", "ERD.pdf")


def _render_erd_image(dpi: int = 150):
    """Lazy-import fitz/PIL so the rest of the page loads even if PyMuPDF is missing."""
    if not os.path.exists(ERD_PDF_PATH):
        return None
    try:
        import fitz  # PyMuPDF
        from PIL import Image
    except ImportError:
        return None
    doc = fitz.open(ERD_PDF_PATH)
    try:
        if len(doc) == 0:
            return None
        page = doc.load_page(0)
        pix = page.get_pixmap(dpi=dpi)
        return Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
    finally:
        doc.close()


def _open_erd_dialog() -> None:
    img = _render_erd_image(dpi=300)
    if img is None:
        ui.notify("ERD could not be rendered.", type="negative")
        return

    img_el: ui.image | None = None
    scale = {"v": 1.0}

    def apply_scale() -> None:
        if img_el is None:
            return
        img_el.style(f"transform: scale({scale['v']}); transform-origin: top left;")

    def zoom_in() -> None:
        scale["v"] = min(4.0, scale["v"] + 0.25)
        apply_scale()

    def zoom_out() -> None:
        scale["v"] = max(0.4, scale["v"] - 0.25)
        apply_scale()

    def reset() -> None:
        scale["v"] = 1.0
        apply_scale()

    with ui.dialog().props("maximized") as dialog, \
         ui.card().classes("w-full h-full q-pa-none column no-wrap"):
        with ui.row().classes(
            "items-center justify-between q-px-md q-py-sm "
            "bg-grey-2 dark:bg-slate-800 w-full"
        ):
            ui.label("Database schema (ERD)").classes("text-subtitle1 text-weight-medium")
            with ui.row().classes("gap-1 items-center"):
                ui.button(icon="zoom_out", on_click=zoom_out) \
                    .props("flat dense round").tooltip("Zoom out")
                ui.button(icon="fit_screen", on_click=reset) \
                    .props("flat dense round").tooltip("Reset zoom (100%)")
                ui.button(icon="zoom_in", on_click=zoom_in) \
                    .props("flat dense round").tooltip("Zoom in")
                ui.separator().props("vertical").classes("q-mx-sm")
                ui.button(
                    icon="download",
                    on_click=lambda: ui.download(ERD_PDF_PATH, filename="ERD.pdf"),
                ).props("flat dense round").tooltip("Download PDF")
                ui.button(icon="close", on_click=dialog.close) \
                    .props("flat dense round").tooltip("Close")
        with ui.scroll_area().classes("w-full flex-grow"):
            img_el = ui.image(img).style(
                "transform-origin: top left; transition: transform .15s ease;"
            )
    dialog.open()


def _render_schema_panel() -> None:
    if not os.path.exists(ERD_PDF_PATH):
        ui.label(
            f"ERD.pdf not found at: {ERD_PDF_PATH}. Place it in the docs/ folder to enable the preview."
        ).classes("text-warning")
        return
    with ui.row().classes("gap-2 q-mb-md"):
        ui.button("Zoom", on_click=_open_erd_dialog) \
            .props("unelevated icon=zoom_in color=primary") \
            .tooltip("Open ERD fullscreen with zoom controls")
        ui.button(
            "Download PDF",
            on_click=lambda: ui.download(ERD_PDF_PATH, filename="ERD.pdf"),
        ).props("outline icon=download color=primary")
    try:
        img = _render_erd_image()
        if img is not None:
            ui.image(img).classes("w-full cursor-pointer") \
                .on("click", _open_erd_dialog) \
                .tooltip("Click to zoom")
            ui.label("Database schema (ERD) — click image to zoom").classes("text-caption muted")
        else:
            ui.label("PyMuPDF (fitz) not installed — install it to render the ERD inline.") \
                .classes("muted")
    except Exception as exc:
        ui.label(f"Error displaying ERD PDF: {exc}").classes("text-negative")


SKIP_COLUMNS = {"date_case", "months_anesthetic_recording", "anesthetic_attending"}

GROUP_ORDER = ["Anesthesiologist", "Identity", "Dates & Times", "Numeric", "Notes", "Other"]


def _bucket_for(col_name: str, kind: str) -> str:
    if kind in ("date", "time", "signature_time"):
        return "Dates & Times"
    if kind in ("int", "float"):
        return "Numeric"
    if kind == "textarea":
        return "Notes"
    lc = col_name.lower()
    if lc == "name" or lc.endswith(("_key", "_code", "_id")):
        return "Identity"
    return "Other"


def _humanize(name: str) -> str:
    return name.replace("_", " ").strip().title()


def _date_picker(label: str, default: str, classes: str = "w-full") -> "ui.input":
    el = ui.input(label, value=default).props("outlined dense readonly").classes(classes)
    with el:
        with ui.menu().props("no-parent-event") as menu:
            with ui.date().bind_value(el):
                with ui.row().classes("justify-end"):
                    ui.button("Close", on_click=menu.close).props("flat")
        with el.add_slot("append"):
            ui.icon("event").on("click", menu.open).classes("cursor-pointer")
    return el


def _time_picker(label: str, default: str, classes: str = "w-full") -> "ui.input":
    el = ui.input(label, value=default).props("outlined dense readonly").classes(classes)
    with el:
        with ui.menu().props("no-parent-event") as menu:
            with ui.time().bind_value(el):
                with ui.row().classes("justify-end"):
                    ui.button("Close", on_click=menu.close).props("flat")
        with el.add_slot("append"):
            ui.icon("schedule").on("click", menu.open).classes("cursor-pointer")
    return el


def _render_generic_field(col: dict, kind: str, ctx: dict) -> None:
    """Render a non-anesthesiology field into the current container."""
    name = col["name"]
    not_null = bool(col.get("notnull", 0))
    label = _humanize(name) + (" *" if not_null else "")
    required_text = {"Required": lambda v: v not in (None, "")}

    if kind == "date":
        el = _date_picker(label, dt_date.today().strftime("%Y-%m-%d"))
        ctx["inputs"][name] = el
    elif kind == "time":
        el = _time_picker(label, "00:00:00")
        ctx["inputs"][name] = el
    elif kind == "signature_time":
        with ui.element("div").classes("md:col-span-2 w-full"):
            with ui.row().classes("w-full no-wrap gap-2"):
                d_el = _date_picker(f"{label} (date)", dt_date.today().strftime("%Y-%m-%d"), "flex-grow")
                t_el = _time_picker(f"{label} (time)", "00:00:00", "flex-grow")
        ctx["inputs"][name] = ("signature_time", d_el, t_el)
    elif kind == "int":
        el = ui.number(label, value=None, format="%d", step=1).props(
            "outlined dense clearable"
        ).classes("w-full")
        if not_null:
            el.validation = required_text
        ctx["inputs"][name] = el
    elif kind == "float":
        el = ui.number(label, value=None).props("outlined dense clearable").classes("w-full")
        if not_null:
            el.validation = required_text
        ctx["inputs"][name] = el
    elif kind == "textarea":
        el = ui.textarea(label).props(
            "outlined autogrow counter maxlength=1000"
        ).classes("w-full md:col-span-2")
        if not_null:
            el.validation = required_text
        ctx["inputs"][name] = el
    else:
        el = ui.input(label).props("outlined dense clearable").classes("w-full")
        if not_null:
            el.validation = required_text
        ctx["inputs"][name] = el


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

            anes = ctx["anes"]
            is_anes = table == "anesthesiology"
            next_key = get_next_anesthesiology_key(db_path) if is_anes else None
            if is_anes:
                ctx["inputs"]["anesthesiology_key"] = next_key

            ui.label(f"Insert into {table}").classes("text-subtitle1 text-weight-medium")
            if is_anes:
                ui.label(
                    f"Next anesthesiology_key: {next_key} (auto-assigned)"
                ).classes("text-caption muted")

            # Anesthesiology code-generation glue (re-bound below as fields render).
            name_input: ui.input | None = None
            date_input: ui.input | None = None
            code_input: ui.input | None = None

            def recompute_code() -> None:
                if not (name_input and date_input and code_input):
                    return
                auto = generate_anesthesiology_code(name_input.value, date_input.value)
                if not anes["code_manual"]:
                    code_input.value = auto

            anes_special = {"name", "anesthesiology_start_date", "staff_code"}
            buckets: dict[str, list[tuple[dict, str, str]]] = {g: [] for g in GROUP_ORDER}

            for col in cols_meta:
                col_name = col["name"]
                ctype = (col["type"] or "").upper()
                if is_anes and col_name == "anesthesiology_key":
                    continue
                if col_name.lower() in SKIP_COLUMNS:
                    continue

                kind = _column_classifier(col_name, ctype)
                if is_anes and col_name in anes_special:
                    buckets["Anesthesiologist"].append((col, kind, "anes"))
                else:
                    buckets[_bucket_for(col_name, kind)].append((col, kind, "generic"))

            non_empty = [g for g in GROUP_ORDER if buckets[g]]

            with ui.stepper().props("vertical header-nav animated").classes("w-full surface-1") as stepper:
                for group_name in non_empty:
                    with ui.step(group_name):
                        with ui.element("div").classes(
                            "grid grid-cols-1 md:grid-cols-2 gap-4 w-full"
                        ):
                            for col, kind, mode in buckets[group_name]:
                                if mode == "anes":
                                    cn = col["name"]
                                    not_null = bool(col.get("notnull", 0))
                                    if cn == "name":
                                        el = ui.input(
                                            "Name" + (" *" if not_null else ""),
                                            value=anes["name"],
                                        ).props("outlined dense clearable").classes("w-full")
                                        if not_null:
                                            el.validation = {"Required": lambda v: bool(v)}
                                        el.on_value_change(
                                            lambda e: (anes.__setitem__("name", e.value), recompute_code())
                                        )
                                        name_input = el
                                        ctx["inputs"][cn] = el
                                    elif cn == "anesthesiology_start_date":
                                        default = anes["start_date"] or dt_date.today().strftime("%Y-%m-%d")
                                        el = _date_picker("Start date *", default)
                                        el.on_value_change(
                                            lambda e: (anes.__setitem__("start_date", e.value), recompute_code())
                                        )
                                        date_input = el
                                        ctx["inputs"][cn] = el
                                    elif cn == "staff_code":
                                        auto = generate_anesthesiology_code(anes["name"], anes["start_date"])
                                        initial = anes["code_value"] if anes["code_manual"] else auto
                                        el = ui.input(
                                            "Staff code", value=initial or ""
                                        ).props(
                                            'outlined dense clearable hint="Auto-generated from name + start date; edit to override"'
                                        ).classes("w-full")

                                        def on_code_change(e, ci=el):
                                            auto_now = generate_anesthesiology_code(anes["name"], anes["start_date"])
                                            if e.value != auto_now:
                                                anes["code_manual"] = True
                                                anes["code_value"] = e.value
                                            else:
                                                anes["code_manual"] = False
                                                anes["code_value"] = ""

                                        el.on_value_change(on_code_change)
                                        code_input = el
                                        ctx["inputs"][cn] = el
                                else:
                                    _render_generic_field(col, kind, ctx)

                        with ui.stepper_navigation():
                            ui.button("Next", on_click=stepper.next).props("color=primary unelevated")
                            ui.button("Back", on_click=stepper.previous).props("flat")

                with ui.step("Review & Insert"):
                    ui.label(
                        "Review your inputs in the previous steps, then commit the row."
                    ).classes("muted")
                    with ui.row().classes("gap-2 q-mt-sm"):
                        ui.button("Insert row", on_click=do_insert).props(
                            "color=primary unelevated icon=save"
                        )
                        ui.button("Back", on_click=stepper.previous).props("flat")

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
                    "minWidth": 140,
                },
                "columnDefs": [{"field": c} for c in df.columns],
                "rowData": df.to_dict("records"),
                "pagination": True,
                "paginationPageSize": 25,
                "animateRows": True,
                "suppressColumnVirtualisation": True,
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

        with ui.expansion("Schema (ERD)", icon="schema").classes("w-full surface-1 q-mt-md"):
            _render_schema_panel()
