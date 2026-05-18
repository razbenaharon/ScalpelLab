"""Shared UI helpers for pages that launch scripts."""

from __future__ import annotations

from nicegui import ui

from app.script_jobs import manager


def set_enabled(element, enabled: bool) -> None:
    if hasattr(element, "set_enabled"):
        element.set_enabled(enabled)
    elif enabled:
        element.enable()
    else:
        element.disable()


def render_job_panel() -> None:
    with ui.card().classes("surface-1 w-full q-pa-md"):
        ui.label("Script log").classes("text-subtitle1 text-weight-medium")
        ui.label("Only one script can run at a time. Output streams here while the job is active.") \
            .classes("text-caption muted")

        with ui.row().classes("items-center gap-3 q-mt-sm"):
            status = ui.label("Idle").classes("text-caption")
            cancel = ui.button("Cancel", icon="stop", on_click=manager.cancel).props("outline color=negative")

        log = (
            ui.textarea(value="")
            .props("readonly outlined")
            .classes("w-full q-mt-sm")
            .style("height: 360px; font-family: Consolas, monospace; font-size: 12px;")
        )

        def refresh() -> None:
            snap = manager.snapshot()
            name = snap.name or "No job"
            status.set_text(f"{name}: {snap.status}")
            set_enabled(cancel, snap.active)
            log.set_value("\n".join(snap.logs[-500:]))

        refresh()
        ui.timer(0.5, refresh)


def confirm_dialog(title: str, message: str, on_confirm) -> None:
    with ui.dialog() as dialog, ui.card().classes("q-pa-md"):
        ui.label(title).classes("text-subtitle1 text-weight-medium")
        ui.label(message).classes("text-body2")
        with ui.row().classes("justify-end w-full q-mt-md gap-2"):
            ui.button("Cancel", on_click=dialog.close).props("flat")

            def _confirm() -> None:
                dialog.close()
                on_confirm()

            ui.button("Run", icon="play_arrow", on_click=_confirm).props("color=primary")
    dialog.open()
