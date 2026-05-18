"""Shared page frame for the NiceGUI ScalpelLab UI.

`page_frame(title)` is a context manager used at the top of every `@ui.page`
handler. It renders the header, a left drawer with the SQLite DB path input
and navigation links, and yields a content slot for the page-specific UI.
"""

from contextlib import contextmanager

from nicegui import ui

from . import state
from .theme import apply_global_theme


# (label, href, icon, page_title)
NAV_LINKS = [
    ("Home",            "/",                "dashboard",      "Home"),
    ("Database",        "/database",        "table_view",     "Database Management"),
    ("Anesthesiology",  "/anesthesiology",  "medical_services", "Anesthesiology"),
    ("MP4",             "/mp4",             "movie",          "MP4"),
    ("SEQ",             "/seq",             "folder_open",    "SEQ"),
    ("SEQ Curation",    "/seq-curation",    "drive_folder_upload", "SEQ Curation"),
    ("Update DB",       "/update-db",       "sync",           "Update DB"),
    ("SEQ → MP4",       "/seq-to-mp4",      "movie_filter",   "SEQ → MP4"),
    ("BORIS",           "/boris",           "label",          "BORIS Tags"),
]


@contextmanager
def page_frame(title: str):
    apply_global_theme()
    dark = ui.dark_mode(value=state.is_dark())
    dark.on_value_change(lambda e: state.set_dark(e.value))

    # ── Header ───────────────────────────────────────────────────────────
    with ui.header().classes("items-center justify-between q-px-md").style(
        "background: linear-gradient(90deg, #4F46E5 0%, #6366F1 100%);"
    ):
        with ui.row().classes("items-center gap-3"):
            ui.html(
                '<img src="/static/scalpel_logo_2.png" '
                'style="height: 32px; width: auto; display: block;" '
                'alt="ScalpelLab">'
            )
            ui.label("/").classes("text-white opacity-50")
            ui.label(title).classes("text-white opacity-90").style("font-size: 14px;")

        with ui.row().classes("items-center gap-2"):
            toggle = ui.button(
                icon="dark_mode" if dark.value else "light_mode",
                on_click=dark.toggle,
            ).props("flat round color=white").tooltip("Toggle dark mode")
            dark.on_value_change(
                lambda e, b=toggle: b.props(f'icon={"dark_mode" if e.value else "light_mode"}')
            )

    # ── Left drawer ──────────────────────────────────────────────────────
    with ui.left_drawer(value=True, fixed=False).classes("q-pa-none").style("min-width: 240px;"):
        with ui.column().classes("w-full q-pt-md gap-1"):
            ui.label("DATABASE").classes("text-caption muted q-px-md").style("letter-spacing: 1px;")
            with ui.row().classes("items-center w-full no-wrap q-px-md q-mt-xs"):
                db_input = (
                    ui.input(value=state.get(), placeholder="path/to/file.sqlite")
                    .props('outlined dense clearable')
                    .classes("flex-grow")
                )
                with db_input.add_slot("prepend"):
                    ui.icon("storage").classes("muted")
                db_input.on_value_change(lambda e: state.set_(e.value))

            ui.separator().classes("q-my-md")

            ui.label("NAVIGATION").classes("text-caption muted q-px-md").style("letter-spacing: 1px;")
            with ui.column().classes("w-full gap-0 q-mt-xs"):
                for label, href, icon, page_title in NAV_LINKS:
                    is_active = (page_title == title)
                    cls = "nav-link active" if is_active else "nav-link"
                    with ui.link(target=href).classes(cls):
                        ui.icon(icon)
                        ui.label(label)

    # ── Page body ────────────────────────────────────────────────────────
    with ui.column().classes("w-full q-pa-lg gap-4"):
        yield
