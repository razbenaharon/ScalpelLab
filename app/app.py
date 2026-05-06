"""ScalpelLab Database Manager — NiceGUI native desktop app.

Entry point. Defines the home page (project overview + ERD preview) and
imports the four sub-pages so their `@ui.page` decorators register routes.
Run with ``python -m app.app`` (or ``python run_app.py``).
"""

import os
import sys

import fitz  # PyMuPDF
from PIL import Image
from nicegui import ui

# Make the project root importable for `config` and sub-page modules.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.layout import page_frame  # noqa: E402
from app.pages import database, status_summary, views, mp4_statistics  # noqa: F401,E402


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ERD_PDF_PATH = os.path.join(PROJECT_ROOT, "docs", "ERD.pdf")


def _render_erd_image() -> Image.Image | None:
    if not os.path.exists(ERD_PDF_PATH):
        return None
    doc = fitz.open(ERD_PDF_PATH)
    try:
        if len(doc) == 0:
            return None
        page = doc.load_page(0)
        pix = page.get_pixmap(dpi=150)
        return Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
    finally:
        doc.close()


@ui.page("/")
def home() -> None:
    with page_frame("Home"):
        ui.markdown(
            """
            # ScalpelLab – SQLite Database Manager

            ### Available Pages
            - **Database Management**: Browse tables, insert new records, and delete existing rows.
            - **Status Summary**: MP4/SEQ processing statistics, camera distributions, totals.
            - **Views**: Access database views for specialized data perspectives.
            - **MP4 Statistics**: Analytics dashboard with KPIs and Plotly charts.

            Use the left drawer to switch pages and to set the SQLite database path.
            """
        )

        ui.separator()
        ui.label("Database Schema Visualization").classes("text-h5 q-mt-md")
        ui.markdown(
            "The diagram shows all tables, columns, data types, and relationships."
        )

        if not os.path.exists(ERD_PDF_PATH):
            ui.label(
                f"ERD.pdf not found at: {ERD_PDF_PATH}. Please ensure it exists in the 'docs' folder."
            ).classes("text-warning")
            return

        ui.button(
            "Download ERD PDF",
            on_click=lambda: ui.download(ERD_PDF_PATH, filename="ERD.pdf"),
        ).props("icon=download")

        try:
            img = _render_erd_image()
            if img is not None:
                ui.image(img).classes("w-full max-w-5xl")
                ui.label("Database Schema ERD").classes("text-caption")
        except Exception as exc:
            ui.label(f"Error displaying ERD PDF: {exc}").classes("text-negative")


def main() -> None:
    ui.run(
        native=True,
        title="ScalpelLab DB",
        reload=False,
        storage_secret="scalpel-lab",
        window_size=(1400, 900),
    )


if __name__ in {"__main__", "__mp_main__"}:
    main()
