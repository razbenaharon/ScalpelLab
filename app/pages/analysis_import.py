"""Launcher for finalized BORIS and monitor analysis imports."""

from __future__ import annotations

from pathlib import Path

from nicegui import ui

from app import state
from app.charts import kpi_card, query_df
from app.config_paths import browse_directory, browse_file
from app.layout import page_frame
from app.pages.script_common import confirm_dialog, render_job_panel, set_enabled
from app.script_jobs import manager, python_script_args


def _count_query(db_path: str, sql: str) -> int:
    df = query_df(db_path, sql)
    if df.empty:
        return 0
    return int(df.iloc[0, 0] or 0)


@ui.page("/analysis-import")
def analysis_import_page() -> None:
    with page_frame("Analysis Import"):
        ui.label("Analysis Import").classes("section-h text-h5 text-weight-medium")
        ui.label(
            "Import finalized BORIS tags and monitor vitals from the configured analyses root."
        ).classes("text-caption muted")

        db_path = state.get()
        analyses_root = state.get_analyses()

        with ui.row().classes("w-full no-wrap gap-4"):
            kpi_card("BORIS EVENTS", f"{_count_query(db_path, 'SELECT COUNT(*) FROM boris_events'):,}", "rows")
            kpi_card("MONITOR SAMPLES", f"{_count_query(db_path, 'SELECT COUNT(*) FROM monitor_samples'):,}", "rows")
            kpi_card("MONITOR CASES", f"{_count_query(db_path, 'SELECT COUNT(*) FROM monitor_case_summary'):,}", "cases")

        db_input = ui.input("SQLite database", value=db_path).props("outlined dense").classes("w-full")
        root_input = ui.input("Analyses root", value=analyses_root).props("outlined dense").classes("w-full")

        with ui.row().classes("items-center gap-2"):
            def pick_db() -> None:
                selected = browse_file(
                    "Select SQLite database",
                    [("SQLite databases", "*.sqlite *.db"), ("All files", "*.*")],
                )
                if selected:
                    db_input.set_value(selected)
                    state.set_(selected)

            def pick_root() -> None:
                selected = browse_directory("Select analyses root", root_input.value or None)
                if selected:
                    root_input.set_value(selected)
                    state.set_analyses(selected)

            ui.button("Browse DB", icon="storage", on_click=pick_db).props("outline")
            ui.button("Browse Analyses", icon="analytics", on_click=pick_root).props("outline")

        with ui.card().classes("surface-1 w-full q-pa-md"):
            ui.label("Options").classes("text-subtitle1 text-weight-medium")
            with ui.row().classes("items-center gap-4"):
                include_boris = ui.checkbox("Import BORIS", value=True)
                include_monitor = ui.checkbox("Import monitor data", value=True)

        def _validate_inputs() -> bool:
            if not db_input.value or not Path(str(db_input.value)).is_file():
                ui.notify("Choose a valid SQLite database.", type="warning")
                return False
            if not root_input.value or not Path(str(root_input.value)).is_dir():
                ui.notify("Choose a valid analyses root.", type="warning")
                return False
            if not include_boris.value and not include_monitor.value:
                ui.notify("Choose at least one import type.", type="warning")
                return False
            state.set_(str(db_input.value))
            state.set_analyses(str(root_input.value))
            return True

        def _args(dry_run: bool) -> list[str]:
            args = [
                "--db", str(db_input.value or ""),
                "--input-root", str(root_input.value or ""),
            ]
            if dry_run:
                args.append("--dry-run")
            else:
                args.append("--auto-confirm")
            if not include_boris.value:
                args.append("--skip-boris")
            if not include_monitor.value:
                args.append("--skip-monitor")
            return python_script_args("scripts/import_analysis_finale.py", args)

        def start_import(dry_run: bool) -> None:
            if not _validate_inputs():
                return
            name = "Preview Analysis Import" if dry_run else "Import Analysis Data"
            if not manager.start(
                name,
                _args(dry_run),
                env={
                    "SCALPEL_DB": str(db_input.value or ""),
                    "SCALPEL_ANALYSES_ROOT": str(root_input.value or ""),
                },
            ):
                ui.notify("Another script is already running.", type="warning")
                return
            ui.notify(f"{name} started.", type="positive")

        with ui.row().classes("items-center gap-2"):
            dry_button = ui.button(
                "Preview import",
                icon="search",
                on_click=lambda: start_import(True),
            ).props("color=primary")
            import_button = ui.button(
                "Import analysis data",
                icon="sync",
                on_click=lambda: confirm_dialog(
                    "Import analysis data?",
                    "This will replace BORIS rows and refresh monitor rows for imported cases.",
                    lambda: start_import(False),
                ),
            ).props("color=primary")

        def refresh_controls() -> None:
            active = manager.snapshot().active
            set_enabled(dry_button, not active)
            set_enabled(import_button, not active)

        refresh_controls()
        ui.timer(0.5, refresh_controls)
        render_job_panel()
