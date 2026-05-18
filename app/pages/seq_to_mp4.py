"""SEQ → MP4 conversion launcher page.

Wraps ``scripts/3_seq_to_mp4_convert.py``: previews the synchronization plan,
then kicks off the real encode. A live counter strip parses
``PROGRESS::JSON {...}`` lines the script emits to stdout, so users can watch
done/total/ok/fail/skip and the most recent camera while a multi-hour run is
in progress.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from nicegui import ui

from app import state
from app.charts import kpi_card, query_df
from app.config_paths import browse_directory, browse_file
from app.layout import page_frame
from app.pages.script_common import confirm_dialog, render_job_panel, set_enabled
from app.script_jobs import manager, python_script_args
from config import DEFAULT_CAMERAS


PROGRESS_PREFIX = "PROGRESS::JSON "
PENDING_SQL = """
SELECT COUNT(*) FROM seq_status s
LEFT JOIN mp4_status m
    ON s.recording_date = m.recording_date
    AND s.case_no = m.case_no
    AND s.camera_name = m.camera_name
WHERE s.size_mb >= 50
    AND (m.size_mb IS NULL OR m.size_mb < 1)
    AND s.camera_name NOT LIKE '%\\_JUNK' ESCAPE '\\'
    AND s.camera_name NOT LIKE '%\\_Junk' ESCAPE '\\'
"""


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


def parse_progress(logs: list[str]) -> dict[str, dict]:
    """Scan recent log lines for ``PROGRESS::JSON`` events.

    Returns a dict keyed by event name (``plan``, ``camera_start``,
    ``camera_done``, ``frame``, ``done``) where each value is the *latest*
    decoded payload for that event. Also tallies per-status counts from all
    ``camera_done`` events seen, exposed under the synthetic ``_counters`` key.
    """
    latest: dict[str, dict] = {}
    counters = {"ok": 0, "fail": 0, "skip": 0}
    for raw in logs:
        if not raw.startswith(PROGRESS_PREFIX):
            continue
        try:
            payload = json.loads(raw[len(PROGRESS_PREFIX):])
        except (ValueError, TypeError):
            continue
        event = payload.get("event")
        if not event:
            continue
        latest[event] = payload
        if event == "camera_done":
            status = payload.get("status")
            if status in counters:
                counters[status] += 1
    latest["_counters"] = counters
    return latest


@ui.page("/seq-to-mp4")
def seq_to_mp4_page() -> None:
    with page_frame("SEQ → MP4"):
        ui.label("SEQ → MP4 Conversion").classes("section-h text-h5 text-weight-medium")
        ui.label(
            "Convert NorPix SEQ recordings to multi-camera-synchronized MP4s "
            "(H.264 → mkvmerge VFR → FFmpeg fps=30 CFR → HEVC NVENC). "
            "Requires ffmpeg, mkvmerge, and an NVIDIA NVENC-capable GPU on the host."
        ).classes("text-caption muted")

        db_path = state.get()
        seq_root = state.get_seq()
        mp4_root = state.get_mp4()
        pending_count = _count_query(db_path, PENDING_SQL)

        with ui.row().classes("w-full no-wrap gap-4"):
            kpi_card("PENDING MP4s", f"{pending_count:,}", "camera-files in DB")
            kpi_card("CAMERAS", f"{len(DEFAULT_CAMERAS)}", "configured")
            kpi_card(
                "MP4 ROOT",
                "OK" if Path(mp4_root).is_dir() else "Missing",
                _free_space_label(mp4_root),
            )

        state_box = {"preview_ok": False}

        db_input = ui.input("SQLite database", value=db_path).props("outlined dense").classes("w-full")
        seq_input = ui.input("SEQ root", value=seq_root).props("outlined dense").classes("w-full")
        mp4_input = ui.input("MP4 root", value=mp4_root).props("outlined dense").classes("w-full")

        with ui.row().classes("items-center gap-2"):
            def pick_db() -> None:
                selected = browse_file(
                    "Select SQLite database",
                    [("SQLite databases", "*.sqlite *.db"), ("All files", "*.*")],
                )
                if selected:
                    db_input.set_value(selected)
                    state.set_(selected)
                    state_box["preview_ok"] = False

            def pick_seq() -> None:
                selected = browse_directory("Select SEQ root", seq_input.value or None)
                if selected:
                    seq_input.set_value(selected)
                    state.set_seq(selected)
                    state_box["preview_ok"] = False

            def pick_mp4() -> None:
                selected = browse_directory("Select MP4 root", mp4_input.value or None)
                if selected:
                    mp4_input.set_value(selected)
                    state.set_mp4(selected)
                    state_box["preview_ok"] = False

            ui.button("Browse DB", icon="storage", on_click=pick_db).props("outline")
            ui.button("Browse SEQ", icon="folder_open", on_click=pick_seq).props("outline")
            ui.button("Browse MP4", icon="folder_open", on_click=pick_mp4).props("outline")

        with ui.card().classes("surface-1 w-full q-pa-md"):
            ui.label("Conversion options").classes("text-subtitle1 text-weight-medium")
            with ui.row().classes("items-center gap-4 q-mt-sm"):
                cameras_select = ui.select(
                    options=list(DEFAULT_CAMERAS),
                    value=list(DEFAULT_CAMERAS),
                    multiple=True,
                    label="Cameras",
                ).props("outlined dense use-chips").classes("min-w-64")
                workers = ui.number("Workers", value=2, min=1, max=4, step=1).props("outlined dense")
                min_pending = ui.number(
                    "Min pending SEQ MB", value=50, min=1, step=1,
                ).props("outlined dense")
                overwrite = ui.switch("Allow overwrite", value=False)
            with ui.row().classes("items-center gap-4 q-mt-sm"):
                date_input = ui.input(
                    "Date filter (CSV, YYYY-MM-DD or YY-MM-DD)",
                ).props("outlined dense").classes("flex-grow")
                case_input = ui.input(
                    "Case filter (CSV of case numbers)",
                ).props("outlined dense").classes("flex-grow")

        # ── Live progress strip ──────────────────────────────────────────
        with ui.card().classes("surface-1 w-full q-pa-md"):
            ui.label("Progress").classes("text-subtitle1 text-weight-medium")
            with ui.row().classes("items-center gap-3 q-mt-sm"):
                current_label = ui.label("Current: —").classes("text-body2")
                progress_bar = ui.linear_progress(value=0, show_value=False).classes("flex-grow")
            with ui.row().classes("items-center gap-6 q-mt-xs"):
                done_label = ui.label("Done: 0 / 0").classes("text-caption")
                ok_label = ui.label("OK: 0").classes("text-caption text-positive")
                fail_label = ui.label("Failed: 0").classes("text-caption text-negative")
                skip_label = ui.label("Skipped: 0").classes("text-caption muted")
                frame_label = ui.label("Frame: —").classes("text-caption muted")

        status = ui.label("").classes("text-caption muted")

        def _validate_inputs() -> bool:
            if not db_input.value or not Path(str(db_input.value)).is_file():
                ui.notify("Choose a valid SQLite database.", type="warning")
                return False
            if not Path(str(seq_input.value)).is_dir():
                ui.notify("Choose a valid SEQ root.", type="warning")
                return False
            if not mp4_input.value:
                ui.notify("Choose an MP4 output root.", type="warning")
                return False
            if not cameras_select.value:
                ui.notify("Pick at least one camera.", type="warning")
                return False
            state.set_(str(db_input.value))
            state.set_seq(str(seq_input.value))
            state.set_mp4(str(mp4_input.value))
            return True

        def _args(dry_run: bool) -> list[str]:
            args: list[str] = [
                "--db", str(db_input.value or ""),
                "--seq-root", str(seq_input.value or ""),
                "--mp4-root", str(mp4_input.value or ""),
                "--cameras", ",".join(cameras_select.value or []),
                "--workers", str(int(workers.value or 2)),
                "--min-pending-mb", str(float(min_pending.value or 50)),
            ]
            for raw in str(date_input.value or "").split(","):
                date = raw.strip()
                if date:
                    args.extend(["--date", date])
            for raw in str(case_input.value or "").split(","):
                case = raw.strip()
                if case.isdigit():
                    args.extend(["--case", case])
            if overwrite.value:
                args.append("--overwrite")
            if dry_run:
                args.append("--dry-run")
            args.append("--auto-confirm")
            return python_script_args("scripts/3_seq_to_mp4_convert.py", args)

        def preview() -> None:
            if not _validate_inputs():
                return
            if not manager.start(
                "SEQ to MP4 Preview",
                _args(dry_run=True),
                env={"SCALPEL_DB": str(db_input.value or "")},
            ):
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
            if not manager.start(
                "SEQ to MP4 Conversion",
                _args(dry_run=False),
                env={"SCALPEL_DB": str(db_input.value or "")},
            ):
                ui.notify("Another script is already running.", type="warning")
                return
            ui.notify("Conversion started.", type="positive")

        with ui.row().classes("items-center gap-2 q-mt-sm"):
            preview_button = ui.button(
                "Preview conversion", icon="search", on_click=preview,
            ).props("color=primary")
            run_button = ui.button(
                "Run conversion",
                icon="play_arrow",
                on_click=lambda: confirm_dialog(
                    "Run SEQ → MP4 conversion?",
                    "This will encode pending camera-files into MP4 under the selected MP4 root. "
                    "Long runs can take hours.",
                    run_real,
                ),
            ).props("color=primary")

        def refresh_controls() -> None:
            snap = manager.snapshot()
            if snap.name == "SEQ to MP4 Preview" and snap.status == "success":
                state_box["preview_ok"] = True
            active = snap.active
            set_enabled(preview_button, not active)
            set_enabled(run_button, (not active) and state_box["preview_ok"])
            status.set_text(
                "Preview complete. Run conversion is enabled."
                if state_box["preview_ok"]
                else "Run a preview before starting the real conversion."
            )

            events = parse_progress(snap.logs)
            counters = events.get("_counters", {"ok": 0, "fail": 0, "skip": 0})
            plan = events.get("plan", {})
            cam_start = events.get("camera_start")
            cam_done = events.get("camera_done")
            done_event = events.get("done")
            frame_event = events.get("frame")

            total = int(
                (done_event or {}).get("total")
                or (cam_done or {}).get("total")
                or (cam_start or {}).get("total")
                or plan.get("total")
                or 0
            )
            done = int((cam_done or {}).get("done") or (cam_start or {}).get("done") or 0)
            if done_event:
                done = counters["ok"] + counters["fail"] + counters["skip"]
            progress_bar.set_value(min(1.0, done / total) if total else 0.0)
            done_label.set_text(f"Done: {done} / {total}")
            ok_label.set_text(f"OK: {counters['ok']}")
            fail_label.set_text(f"Failed: {counters['fail']}")
            skip_label.set_text(f"Skipped: {counters['skip']}")

            recent = cam_done or cam_start
            if recent:
                date = recent.get("date", "?")
                case = recent.get("case", "?")
                cam = recent.get("camera", "?")
                verb = "finished" if recent is cam_done else "started"
                current_label.set_text(f"Current ({verb}): {date} / Case {case} / {cam}")
            else:
                current_label.set_text("Current: —")

            if frame_event:
                frame_label.set_text(
                    f"Frame: {frame_event.get('camera', '?')} "
                    f"{frame_event.get('percent', 0):.1f}% "
                    f"({frame_event.get('frame', 0)}/{frame_event.get('total', 0)})"
                )
            else:
                frame_label.set_text("Frame: —")

        refresh_controls()
        ui.timer(0.5, refresh_controls)
        render_job_panel()
