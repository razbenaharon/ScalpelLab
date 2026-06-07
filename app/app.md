# app/ — NiceGUI Dashboard

Native-window NiceGUI app for browsing and visualizing the ScalpelLab SQLite
database. Entry point is `run_app.py` at the project root, which spawns
`python -m app.app`.

## Layout

```
app/
├── app.py              # entry; defines Home dashboard + Configuration panel + main()
├── layout.py           # page_frame() — header, grouped drawer navigation (NAV_SECTIONS)
├── state.py            # paths + dark-mode persisted in app.storage.general
├── theme.py            # PALETTE, CHART_SEQ, global CSS (surface-1, kpi-card…)
├── utils.py            # connect(), list_tables(), list_views(), load_table()
├── charts.py           # query_df, kpi_card, kpi_with_spark, echart helpers
├── config_paths.py     # browse/validate/persist DB, SEQ, MP4, SequenceViewer paths
├── script_jobs.py      # single-slot background job manager for launcher pages
└── pages/
    ├── anesthesiology.py  — /anesthesiology (cur_seniority + recording_details)
    ├── analysis_import.py — /analysis-import (launcher for finalized BORIS/monitor import)
    ├── database.py        — /database  (table CRUD + ERD viewer)
    ├── mp4.py             — /mp4        (mp4_status + cur_mp4_* — stats + coverage)
    ├── monitor_data.py    — /monitor-data (monitor_samples + monitor_case_summary vitals)
    ├── seq.py             — /seq        (seq_status inventory + seq_enriched time-drift)
    ├── boris.py           — /boris      (boris_events — START/STOP pairing + intervals)
    ├── nuk_export.py      — /seq-curation (launcher for 1_seq_curation.py)
    ├── update_db.py       — /update-db  (launcher for 2_update_db.py + IDX creation)
    ├── seq_to_mp4.py      — /seq-to-mp4 (launcher for 3_seq_to_mp4_convert.py)
    └── script_common.py   — shared job-panel / log widgets for launcher pages (no route)
```

## Page contract

Every page follows the same shape — copy this when adding a new page:

```python
from nicegui import ui
from app import state
from app.charts import kpi_card, query_df, echart_axis_color, base_grid, base_tooltip
from app.layout import page_frame

@ui.page("/my-route")
def my_page() -> None:
    with page_frame("My Page"):
        db_path = state.get()
        ui.label("My Dashboard").classes("section-h text-h5 text-weight-medium")
        ui.label("subtitle").classes("text-caption muted")

        df = query_df(db_path, "SELECT ...")
        if df.empty:
            ui.label("No data.").classes("text-warning")
            return

        with ui.row().classes("w-full no-wrap gap-4"):
            kpi_card("METRIC", f"{value:,}", "hint")
            # …

        with ui.card().classes("surface-1 w-full q-pa-md"):
            ui.label("Section").classes("text-subtitle1 text-weight-medium")
            ui.echart({...}).style("height: 360px;")
```

After creating the page, register it in two places:
1. **`app/app.py`** — add to the `from app.pages import (...)` block so `@ui.page`
   decorators run at startup.
2. **`app/layout.py`** — add it to the appropriate sidebar group. The
   `page_title` must match what the page passes to `page_frame()` so the
   drawer's active-link highlight works.

## Navigation

The sidebar is split into two visually separated groups:

- **Dashboards & Monitoring**: Home, Database, Anesthesiology, MP4, SEQ, BORIS, Monitor Data.
- **Processing Pipeline**: SEQ Curation, Update DB + IDX, SEQ to MP4. The
  underlying pipeline contract includes Analyze SEQ Fields after DB update;
  add a dedicated page here if it is split out from the DB update flow.
  Analysis Import is the launcher for finalized BORIS and monitor CSV imports.

Keep read-only dashboards in the first group and pages that launch real
filesystem or database work in the second group.

## Home configuration

The Home page Configuration panel controls the paths used by the dashboard and
script launcher pages:

- SQLite database (`config.DB_PATH`, overridable by `SCALPEL_DB`).
- SEQ root (`config.SEQ_ROOT`, overridable by `SCALPEL_SEQ_ROOT`).
- MP4 root (`config.MP4_ROOT`, overridable by `SCALPEL_MP4_ROOT`).
- NorPix SequenceViewer executable
  (`config.NORPIX_SEQUENCE_VIEWER_PATH`, overridable by
  `SCALPEL_NORPIX_SEQUENCE_VIEWER`).
- Finalized analyses root (`config.ANALYSES_ROOT`, overridable by
  `SCALPEL_ANALYSES_ROOT`).

The default SequenceViewer path is
`C:\Program Files\Common Files\NorPix\SequenceViewer.exe`. The Home page can
browse to a different executable and persist it back to `config.py`.

## Conventions

- **DB access:** always go through `app/charts.py::query_df` or
  `app/utils.py::connect`. Never open `sqlite3.connect()` directly in a page.
- **Charts:** ECharts via `ui.echart(...)` for new pages. Use `chart_palette()`
  / `CHART_SEQ` for colors — do not hardcode hex values in pages.
- **Theme-aware text:** every chart's axis/legend text must use
  `echart_axis_color()` so dark mode flips correctly.
- **Layout idioms:** wrap chart blocks in `ui.card().classes("surface-1 ...")`,
  use `ui.row().classes("w-full no-wrap gap-4 items-stretch")` for side-by-side
  cards, KPI strips use `kpi_card` or `kpi_with_spark`.
- **No hand-rolled HTML.** Use Quasar/NiceGUI components (`ui.aggrid`,
  `ui.echart`, `ui.toggle`, `ui.select`, etc.).

## Data model quick reference

Tables: `mp4_status`, `mp4_times`, `seq_status`, `seq_enriched` (rich
per-file frame analysis), `boris_events`, `analysis_information`,
`monitor_samples`, `monitor_case_summary`, `anesthesiology`,
`recording_details`.

Views (read-only — don't write to these):
- `cur_mp4_missing` — pivot of cameras present per (date, case)
- `cur_sync_status` — per-(date, case, camera) `is_syncable` flag from `seq_enriched`, encoding the `3_seq_to_mp4_convert.py` planning gates
- `cur_mp4_status_statistics` — MP4 cases with cameras_count
- `cur_seniority` — anesthesiology roster with computed seniority + A/R status

BORIS intervals are reconstructed in pandas inside [pages/boris.py](pages/boris.py) — there is no SQL view because the old `cur_boris_intervals` joined through `analysis_information.event_id` (which only stores one event per case) and truncated to ~one row per case.

Camera names (use the `CAMERAS` constant defined in mp4/seq/quality):
`Cart_Center_2, Cart_LT_4, Cart_RT_1, General_3, Monitor, Patient_Monitor,
Ventilator_Monitor, Injection_Port`. Older data sometimes has `_JUNK` /
`_Junk` suffixes — filter these out for visualizations.

## Pitfalls

- `seq_enriched.time_drift_ms` has corrupt outliers (~2e12 ms). Always clip,
  filter, or bucket large `|drift|` before plotting (see the
  `DRIFT_SMALL_MS` / `DRIFT_MEDIUM_MS` bucketing in `pages/seq.py`).
- BORIS pairing status (computed in [pages/boris.py](pages/boris.py)) takes
  the values `PAIRED`, `MISSING_STOP`, `ERROR_DOUBLE_START`. Most charts
  should filter to `PAIRED`.
- The Windows console is CP1252; if a script prints UTF-8, set the wrapper
  shown in `memory/MEMORY.md`. NiceGUI itself doesn't need this.
- `state.get()` returns the active DB path — call it inside the page handler,
  not at module import time, so the drawer's path input takes effect.
