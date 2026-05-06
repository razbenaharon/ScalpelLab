"""Status Summary page — MP4 and SEQ file presence statistics.

NiceGUI port of ``2_Status_Summary.py``. Reuses ``fetch_camera_stats`` /
``stats_to_dataframe`` semantics and renders the per-camera pivot + totals
for both ``mp4_status`` and ``seq_status`` tables.
"""

from collections import Counter

import pandas as pd
from nicegui import ui

from app import state
from app.layout import page_frame
from app.utils import connect


DEFAULT_CAMERAS = [
    "Cart_Center_2", "Cart_LT_4", "Cart_RT_1",
    "General_3", "Monitor", "Patient_Monitor",
    "Ventilator_Monitor", "Injection_Port",
]
LABELS = {1: "Present", 2: "Missing"}
STATUS_ORDER = (1, 2)


def fetch_camera_stats(db_path: str, table: str, cameras: list[str]) -> tuple[int, dict]:
    """Status derived from size_mb: NULL → Missing (2), NOT NULL → Present (1)."""
    with connect(db_path) as conn:
        cur = conn.cursor()
        cur.execute(f"SELECT COUNT(DISTINCT recording_date || '-' || case_no) FROM {table}")
        total_cases = cur.fetchone()[0]
        camera_stats = {cam: Counter() for cam in cameras}
        if not cameras:
            return total_cases, camera_stats
        placeholders = ",".join(["?"] * len(cameras))
        cur.execute(
            f"SELECT camera_name, size_mb FROM {table} WHERE camera_name IN ({placeholders})",
            cameras,
        )
        for camera_name, size_mb in cur.fetchall():
            status = 2 if size_mb is None else 1
            camera_stats[camera_name][status] += 1
        return total_cases, camera_stats


def stats_to_dataframe(camera_stats: dict, labels: dict, status_order) -> pd.DataFrame:
    rows = []
    present_set = set()
    for ctr in camera_stats.values():
        for s, cnt in ctr.items():
            if cnt > 0:
                present_set.add(s)
    statuses = [s for s in status_order if (s in present_set) or (not present_set and s in labels)]
    for cam, ctr in camera_stats.items():
        for s in statuses:
            rows.append({
                "camera": cam,
                "status": s,
                "status_label": labels.get(s, str(s)),
                "count": int(ctr.get(s, 0)),
            })
    return pd.DataFrame(rows)


@ui.page("/status")
def status_summary_page() -> None:
    with page_frame("Status Summary"):
        db_path = state.get()
        ui.label("Status Summary (mp4_status & seq_status)").classes("text-h5")

        opts = {
            "mp4_table": "mp4_status",
            "seq_table": "seq_status",
            "cameras": list(DEFAULT_CAMERAS),
        }

        with ui.expansion("Options", icon="tune").classes("w-full"):
            mp4_input = ui.input("MP4 status table", value=opts["mp4_table"]).classes("w-full")
            seq_input = ui.input("SEQ status table", value=opts["seq_table"]).classes("w-full")
            cam_select = ui.select(
                options=DEFAULT_CAMERAS,
                value=list(DEFAULT_CAMERAS),
                label="Cameras",
                multiple=True,
            ).props("use-chips").classes("w-full")

            def apply_changes() -> None:
                opts["mp4_table"] = mp4_input.value
                opts["seq_table"] = seq_input.value
                opts["cameras"] = cam_select.value or []
                sections.refresh()

            ui.button("Apply", on_click=apply_changes).props("color=primary").classes("q-mt-sm")

        @ui.refreshable
        def sections() -> None:
            render_section("MP4 Status Summary", opts["mp4_table"])
            ui.separator().classes("q-my-md")
            render_section("SEQ Status Summary", opts["seq_table"])

        def render_section(title: str, table_name: str) -> None:
            ui.label(title).classes("text-h6 q-mt-md")
            try:
                total_rows, camera_stats = fetch_camera_stats(db_path, table_name, opts["cameras"])
                ui.label(f"Total cases in {table_name}: {total_rows}").classes("text-caption")
                df = stats_to_dataframe(camera_stats, LABELS, STATUS_ORDER)
                if df.empty:
                    ui.label("No status data found for the selected cameras.").classes("text-warning")
                    return
                pivot = df.pivot_table(
                    index="camera", columns="status_label", values="count",
                    aggfunc="sum", fill_value=0,
                ).reset_index()
                ui.aggrid.from_pandas(pivot).classes("w-full").style("height: 320px")

                totals = df.groupby("status_label")["count"].sum().reset_index()
                ui.label("Totals across all cameras:").classes("text-bold q-mt-sm")
                ui.aggrid.from_pandas(totals).classes("w-full").style("height: 140px")
            except Exception as exc:
                ui.label(f"Error: {exc}").classes("text-negative")

        sections()
