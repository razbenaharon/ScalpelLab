"""Database update script launcher page."""

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


@ui.page("/update-db")
def update_db_page() -> None:
    with page_frame("Update DB"):
        ui.label("Update DB").classes("section-h text-h5 text-weight-medium")
        ui.label(
            "Scan the configured SEQ and MP4 roots, then refresh only the managed "
            "inventory columns in the SQLite database."
        ).classes("text-caption muted")

        db_path = state.get()
        seq_root = state.get_seq()
        mp4_root = state.get_mp4()

        with ui.row().classes("w-full no-wrap gap-4"):
            kpi_card("SEQ STATUS", f"{_count_query(db_path, 'SELECT COUNT(*) FROM seq_status'):,}", "rows")
            kpi_card("MP4 STATUS", f"{_count_query(db_path, 'SELECT COUNT(*) FROM mp4_status'):,}", "rows")
            kpi_card("SEQ ENRICHED", f"{_count_query(db_path, 'SELECT COUNT(*) FROM seq_enriched'):,}", "rows")
            kpi_card("MISSING MP4", f"{_count_query(db_path, 'SELECT COUNT(*) FROM cur_mp4_missing'):,}", "to export")

        db_input = ui.input("SQLite database", value=db_path).props("outlined dense").classes("w-full")
        seq_input = ui.input("SEQ root", value=seq_root).props("outlined dense").classes("w-full")
        mp4_input = ui.input("MP4 root", value=mp4_root).props("outlined dense").classes("w-full")

        with ui.row().classes("items-center gap-2"):
            def pick_db() -> None:
                selected = browse_file("Select SQLite database", [("SQLite databases", "*.sqlite *.db"), ("All files", "*.*")])
                if selected:
                    db_input.set_value(selected)
                    state.set_(selected)

            def pick_seq() -> None:
                selected = browse_directory("Select SEQ root", seq_input.value or None)
                if selected:
                    seq_input.set_value(selected)
                    state.set_seq(selected)

            def pick_mp4() -> None:
                selected = browse_directory("Select MP4 root", mp4_input.value or None)
                if selected:
                    mp4_input.set_value(selected)
                    state.set_mp4(selected)

            ui.button("Browse DB", icon="storage", on_click=pick_db).props("outline")
            ui.button("Browse SEQ", icon="folder_open", on_click=pick_seq).props("outline")
            ui.button("Browse MP4", icon="folder_open", on_click=pick_mp4).props("outline")

        with ui.card().classes("surface-1 w-full q-pa-md"):
            ui.label("Options").classes("text-subtitle1 text-weight-medium")
            with ui.row().classes("items-center gap-4"):
                threshold = ui.number("JUNK threshold MB", value=200, min=1, step=1).props("outlined dense")
                delete_small = ui.number("Delete small MP4 MB", value=10, min=0, step=1).props("outlined dense")
                skip_duration = ui.checkbox("Skip duration", value=False)
                skip_delete = ui.checkbox("Skip delete", value=False)
                skip_analysis = ui.checkbox("Skip SEQ analysis", value=False)
                skip_seq = ui.checkbox("Skip SEQ", value=False)
                skip_mp4 = ui.checkbox("Skip MP4", value=False)

        def _validate_inputs() -> bool:
            if not db_input.value or not Path(str(db_input.value)).is_file():
                ui.notify("Choose a valid SQLite database.", type="warning")
                return False
            if not Path(str(seq_input.value)).is_dir() and not skip_seq.value:
                ui.notify("Choose a valid SEQ root or skip SEQ.", type="warning")
                return False
            if not Path(str(mp4_input.value)).is_dir() and not skip_mp4.value:
                ui.notify("Choose a valid MP4 root or skip MP4.", type="warning")
                return False
            state.set_(str(db_input.value))
            state.set_seq(str(seq_input.value))
            state.set_mp4(str(mp4_input.value))
            return True

        def _args(dry_run: bool) -> list[str]:
            args = [
                "--db", str(db_input.value or ""),
                "--seq-root", str(seq_input.value or ""),
                "--mp4-root", str(mp4_input.value or ""),
                "--threshold-mb", str(int(threshold.value or 200)),
                "--delete-small-mb", str(int(delete_small.value or 0)),
            ]
            if dry_run:
                args.append("--dry-run")
            else:
                args.append("--auto-confirm")
            for checkbox, flag in [
                (skip_duration, "--skip-duration"),
                (skip_delete, "--skip-delete"),
                (skip_analysis, "--skip-analysis"),
                (skip_seq, "--skip-seq"),
                (skip_mp4, "--skip-mp4"),
            ]:
                if checkbox.value:
                    args.append(flag)
            return python_script_args("scripts/2_update_db.py", args)

        def start_update(dry_run: bool) -> None:
            if not _validate_inputs():
                return
            name = "Update DB Dry Run" if dry_run else "Update DB"
            if not manager.start(
                name,
                _args(dry_run),
                env={"SCALPEL_DB": str(db_input.value or "")},
            ):
                ui.notify("Another script is already running.", type="warning")
                return
            ui.notify(f"{name} started.", type="positive")

        with ui.row().classes("items-center gap-2"):
            dry_button = ui.button("Dry run", icon="search", on_click=lambda: start_update(True)).props("color=primary")
            update_button = ui.button(
                "Update database",
                icon="sync",
                on_click=lambda: confirm_dialog(
                    "Update database?",
                    "This will write managed SEQ/MP4 status columns to the selected database.",
                    lambda: start_update(False),
                ),
            ).props("color=primary")

        def refresh_controls() -> None:
            active = manager.snapshot().active
            set_enabled(dry_button, not active)
            set_enabled(update_button, not active)

        refresh_controls()
        ui.timer(0.5, refresh_controls)
        render_job_panel()
