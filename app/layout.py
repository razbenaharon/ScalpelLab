"""Shared page frame for the NiceGUI ScalpelLab UI.

`page_frame(title)` is a context manager used at the top of every `@ui.page`
handler. It renders the header, a left drawer with the SQLite DB path input
and navigation links, and yields a content slot for the page-specific UI.
"""

from contextlib import contextmanager

from nicegui import ui

from . import state


NAV_LINKS = [
    ("Home", "/"),
    ("Database", "/database"),
    ("Status Summary", "/status"),
    ("Views", "/views"),
    ("MP4 Statistics", "/mp4-stats"),
]


@contextmanager
def page_frame(title: str):
    dark = ui.dark_mode(value=state.is_dark())
    dark.on_value_change(lambda e: state.set_dark(e.value))

    with ui.header().classes("items-center justify-between"):
        with ui.row().classes("items-center q-gutter-md"):
            ui.label("ScalpelLab DB").classes("text-h6")
            ui.label(title).classes("text-subtitle1")
        toggle = ui.button(
            icon="dark_mode" if dark.value else "light_mode",
            on_click=dark.toggle,
        ).props("flat round color=white").tooltip("Toggle dark mode")
        dark.on_value_change(
            lambda e, b=toggle: b.props(f'icon={"dark_mode" if e.value else "light_mode"}')
        )

    with ui.left_drawer(value=True, fixed=False):
        ui.label("Database").classes("text-bold q-mt-sm")
        db_input = ui.input("SQLite DB Path", value=state.get()).classes("w-full")
        db_input.on_value_change(lambda e: state.set_(e.value))

        ui.separator().classes("q-my-md")
        ui.label("Navigation").classes("text-bold")
        for label, href in NAV_LINKS:
            ui.link(label, href).classes("block q-py-xs")

    with ui.column().classes("w-full q-pa-md"):
        yield
