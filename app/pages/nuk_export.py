"""SEQ Curation launcher page."""

from __future__ import annotations

import shutil
from pathlib import Path

from nicegui import ui

from app import state
from app.charts import kpi_card, query_df
from app.config_paths import browse_directory
from app.layout import page_frame
from app.pages.script_common import confirm_dialog, render_job_panel, set_enabled
from app.script_jobs import manager, python_script_args


def _count_query(db_path: str, sql: str) -> int:
    df = query_df(db_path, sql)
    if df.empty:
        return 0
    return int(df.iloc[0, 0] or 0)


def _free_space_label(path: str) -> str:
    try:
        usage = shutil.disk_usage(path)
    except OSError:
        return "not available"
    return f"{usage.free / (1024 ** 3):,.1f} GB free"


@ui.page("/seq-curation")
def nuk_export_page() -> None:
    with page_frame("SEQ Curation"):
        ui.label("SEQ Curation").classes("section-h text-h5 text-weight-medium")
        ui.label(
            "Organize raw NorPix SEQ folders into DATA_YY-MM-DD/CaseN/CameraName "
            "with companion files, hash-checked copies, and JUNK marking for small recordings."
        ).classes("text-caption muted")

        db_path = state.get()
        seq_root = state.get_seq()
        seq_rows = _count_query(db_path, "SELECT COUNT(*) FROM seq_status")
        junk_rows = _count_query(
            db_path,
            "SELECT COUNT(*) FROM seq_status "
            "WHERE camera_name LIKE '%\\_JUNK' ESCAPE '\\' "
            "   OR camera_name LIKE '%\\_Junk' ESCAPE '\\'",
        )

        with ui.row().classes("w-full no-wrap gap-4"):
            kpi_card("SEQ ROWS", f"{seq_rows:,}", "current database")
            kpi_card("JUNK ROWS", f"{junk_rows:,}", "current database")
            kpi_card("DESTINATION", "OK" if Path(seq_root).is_dir() else "Missing", _free_space_label(seq_root))

        state_box = {"preview_ok": False}
        source_input = ui.input("Raw source folder").props("outlined dense").classes("w-full")
        dest_input = ui.input("Destination SEQ root", value=seq_root).props("outlined dense").classes("w-full")
        workers_input = ui.number("Workers", value=8, min=1, max=32, step=1).props("outlined dense")

        with ui.row().classes("items-center gap-2"):
            def pick_source() -> None:
                selected = browse_directory("Select raw SEQ source folder", source_input.value or None)
                if selected:
                    source_input.set_value(selected)
                    state_box["preview_ok"] = False

            def pick_dest() -> None:
                selected = browse_directory("Select destination SEQ root", dest_input.value or state.get_seq())
                if selected:
                    dest_input.set_value(selected)
                    state.set_seq(selected)
                    state_box["preview_ok"] = False

            ui.button("Browse source", icon="folder_open", on_click=pick_source).props("outline")
            ui.button("Browse destination", icon="folder_open", on_click=pick_dest).props("outline")

        status = ui.label("").classes("text-caption muted")

        def _args(dry_run: bool) -> list[str]:
            args = [
                "--source", str(source_input.value or ""),
                "--dest", str(dest_input.value or ""),
                "--workers", str(int(workers_input.value or 8)),
            ]
            if dry_run:
                args.append("--dry-run")
            else:
                args.append("--auto-confirm")
            return python_script_args("scripts/1_seq_curation.py", args)

        def _validate_inputs() -> bool:
            if not source_input.value or not Path(str(source_input.value)).is_dir():
                ui.notify("Choose a valid raw source folder.", type="warning")
                return False
            if not dest_input.value:
                ui.notify("Choose a destination SEQ root.", type="warning")
                return False
            state.set_seq(str(dest_input.value))
            return True

        def preview() -> None:
            if not _validate_inputs():
                return
            if not manager.start("SEQ Curation Preview", _args(True)):
                ui.notify("Another script is already running.", type="warning")
                return
            state_box["preview_ok"] = False
            ui.notify("Preview started.", type="positive")

        def run_real() -> None:
            if not _validate_inputs():
                return
            if not state_box["preview_ok"]:
                ui.notify("Run a successful preview first.", type="warning")
                return
            if not manager.start("SEQ Curation", _args(False)):
                ui.notify("Another script is already running.", type="warning")
                return
            ui.notify("Curation started.", type="positive")

        with ui.row().classes("items-center gap-2 q-mt-sm"):
            preview_button = ui.button("Preview curation", icon="search", on_click=preview).props("color=primary")
            run_button = ui.button(
                "Run curation",
                icon="play_arrow",
                on_click=lambda: confirm_dialog(
                    "Run SEQ curation?",
                    "This will copy files into the destination SEQ root.",
                    run_real,
                ),
            ).props("color=primary")

        def refresh_controls() -> None:
            snap = manager.snapshot()
            if snap.name == "SEQ Curation Preview" and snap.status == "success":
                state_box["preview_ok"] = True
            active = snap.active
            set_enabled(preview_button, not active)
            set_enabled(run_button, (not active) and state_box["preview_ok"])
            status.set_text(
                "Preview complete. Run curation is enabled."
                if state_box["preview_ok"]
                else "Run a preview before starting the real curation."
            )

        refresh_controls()
        ui.timer(0.5, refresh_controls)
        render_job_panel()
