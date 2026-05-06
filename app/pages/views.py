"""Database Views page — browse predefined SQL views.

NiceGUI port of ``3_Views.py``.
"""

from nicegui import ui

from app import state
from app.layout import page_frame
from app.utils import list_views, load_table


@ui.page("/views")
def views_page() -> None:
    with page_frame("Database Views"):
        db_path = state.get()
        ui.label("Database Views").classes("text-h5")

        try:
            views = list_views(db_path)
        except Exception as exc:
            ui.label(f"Could not list views: {exc}").classes("text-negative")
            return

        if not views:
            ui.label("No views found in the database.").classes("text-warning")
            return

        ui.label(f"Found {len(views)} view(s) in the database.").classes("text-positive")

        ctx = {"view": views[0]}

        @ui.refreshable
        def render_view() -> None:
            ui.label(f"View: {ctx['view']}").classes("text-h6 q-mt-md")
            try:
                df = load_table(db_path, ctx["view"])
                if df.empty:
                    ui.label("View is empty or could not be loaded.").classes("text-warning")
                    return
                ui.aggrid.from_pandas(df).classes("w-full").style("height: 500px")
                ui.label(f"Showing {len(df)} rows").classes("text-caption")
            except Exception as exc:
                ui.label(f"Error loading view: {exc}").classes("text-negative")

        def on_change(e) -> None:
            ctx["view"] = e.value
            render_view.refresh()

        ui.select(options=views, value=ctx["view"], label="Select a view", on_change=on_change).classes("w-full")
        render_view()
