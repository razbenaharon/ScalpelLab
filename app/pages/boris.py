"""BORIS behavioral-tag analysis page.

Surfaces ``boris_events`` for exploration: frequency, duration, per-case
timelines, and data-quality checks. Intervals are reconstructed in pandas,
partitioned by ``boris_events.source_file`` so each TSV (one per case) is
paired independently.
"""

from __future__ import annotations

import re

import pandas as pd
from nicegui import ui

from app import state
from app.charts import (
    base_grid,
    base_tooltip,
    chart_palette,
    echart_axis_color,
    empty_state,
    kpi_card,
    query_df,
)
from app.layout import page_frame


FILENAME_RE = re.compile(
    r"^(?P<yy>\d{2})-(?P<mm>\d{2})-(?P<dd>\d{2})-case(?P<case_no>\d+)\.tsv$",
    re.IGNORECASE,
)

SUBJECT_ORDER = ["Attending", "Resident", "Staff"]
LONG_INTERVAL_SEC = 30 * 60          # flag intervals ≥ 30 min as suspicious
RARE_BEHAVIOR_MAX_CASES = 2          # behaviors appearing in ≤ N cases


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def _parse_source_file(name: str) -> tuple[str | None, int | None]:
    m = FILENAME_RE.fullmatch(name or "")
    if not m:
        return None, None
    return (
        f"20{m.group('yy')}-{m.group('mm')}-{m.group('dd')}",
        int(m.group("case_no")),
    )


def _load_events(db_path: str) -> pd.DataFrame:
    df = query_df(
        db_path,
        "SELECT event_id, subject, behavior, behavior_type, "
        "       modifier_1, modifier_2, modifier_3, time_s, source_file "
        "FROM boris_events",
    )
    if df.empty:
        return df
    parsed = df["source_file"].fillna("").map(_parse_source_file)
    df["recording_date"] = [p[0] for p in parsed]
    df["case_no"] = [p[1] for p in parsed]
    df = df.dropna(subset=["recording_date", "case_no"])
    df["case_no"] = df["case_no"].astype(int)
    df["case_key"] = df["recording_date"] + " · Case " + df["case_no"].astype(str)
    return df


def _build_intervals(events: pd.DataFrame) -> pd.DataFrame:
    """Reconstruct intervals from START/STOP events per source_file."""
    cols = [
        "source_file", "recording_date", "case_no", "case_key",
        "subject", "behavior", "modifier_1", "modifier_2", "modifier_3",
        "start_time_s", "end_time_s", "duration_s", "pairing_status",
    ]
    if events.empty:
        return pd.DataFrame(columns=cols)

    ss = events[events["behavior_type"].isin(["START", "STOP"])].copy()
    if ss.empty:
        return pd.DataFrame(columns=cols)

    for c in ("modifier_1", "modifier_2", "modifier_3", "subject", "behavior"):
        ss[c] = ss[c].fillna("")

    keys = ["source_file", "subject", "behavior",
            "modifier_1", "modifier_2", "modifier_3"]
    ss = ss.sort_values(keys + ["time_s", "event_id"]).reset_index(drop=True)

    g = ss.groupby(keys, sort=False)
    ss["next_type"] = g["behavior_type"].shift(-1)
    ss["next_time"] = g["time_s"].shift(-1)

    starts = ss[ss["behavior_type"] == "START"].copy()
    if starts.empty:
        return pd.DataFrame(columns=cols)

    status = pd.Series("MISSING_STOP", index=starts.index)
    status[starts["next_type"] == "STOP"] = "PAIRED"
    status[starts["next_type"] == "START"] = "ERROR_DOUBLE_START"
    starts["pairing_status"] = status

    end = starts["next_time"].where(starts["pairing_status"] == "PAIRED")
    starts["end_time_s"] = end
    starts["start_time_s"] = starts["time_s"]
    starts["duration_s"] = end - starts["time_s"]

    out = starts[[
        "source_file", "recording_date", "case_no", "case_key",
        "subject", "behavior", "modifier_1", "modifier_2", "modifier_3",
        "start_time_s", "end_time_s", "duration_s", "pairing_status",
    ]].reset_index(drop=True)
    # restore empty-string fillers back to None for display
    for c in ("modifier_1", "modifier_2", "modifier_3"):
        out[c] = out[c].replace("", None)
    return out


# ---------------------------------------------------------------------------
# Filtering
# ---------------------------------------------------------------------------

def _apply_event_filters(df: pd.DataFrame, f: dict) -> pd.DataFrame:
    sub = df
    if f.get("date_from"):
        sub = sub[sub["recording_date"] >= f["date_from"]]
    if f.get("date_to"):
        sub = sub[sub["recording_date"] <= f["date_to"]]
    if f.get("subjects"):
        sub = sub[sub["subject"].isin(f["subjects"])]
    if f.get("behaviors"):
        sub = sub[sub["behavior"].isin(f["behaviors"])]
    if f.get("modifiers"):
        sub = sub[sub["modifier_1"].isin(f["modifiers"])]
    return sub


def _apply_interval_filters(df: pd.DataFrame, f: dict) -> pd.DataFrame:
    sub = df
    if f.get("date_from"):
        sub = sub[sub["recording_date"] >= f["date_from"]]
    if f.get("date_to"):
        sub = sub[sub["recording_date"] <= f["date_to"]]
    if f.get("subjects"):
        sub = sub[sub["subject"].isin(f["subjects"])]
    if f.get("behaviors"):
        sub = sub[sub["behavior"].isin(f["behaviors"])]
    if f.get("modifiers"):
        sub = sub[sub["modifier_1"].fillna("").isin(f["modifiers"])]
    return sub


# ---------------------------------------------------------------------------
# Chart helpers
# ---------------------------------------------------------------------------

def _section(title: str, subtitle: str, body_fn) -> None:
    with ui.card().classes("surface-1 w-full q-pa-md"):
        ui.label(title).classes("text-subtitle1 text-weight-medium")
        ui.label(subtitle).classes("text-caption muted")
        body_fn()


def _format_hms(seconds: float) -> str:
    if seconds is None or pd.isna(seconds):
        return "—"
    s = int(round(float(seconds)))
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    if h:
        return f"{h}h {m:02d}m"
    if m:
        return f"{m}m {sec:02d}s"
    return f"{sec}s"


def _bar_horizontal(labels: list[str], values: list[float], unit: str = "",
                    palette_idx: int = 0, height: int = 320,
                    left: int | None = None) -> None:
    if not labels:
        empty_state("No data.")
        return
    axis = echart_axis_color()
    palette = chart_palette()
    if left is None:
        max_chars = max((len(str(s)) for s in labels), default=8)
        left = max(60, min(240, 8 * max_chars + 24))
    ui.echart({
        "tooltip": base_tooltip("axis") | (
            {"valueFormatter": f"{{value}} {unit}".strip()} if unit else {}
        ),
        "grid": base_grid(left=left, right=80, top=10, bottom=30),
        "xAxis": {"type": "value",
                  "axisLabel": {"color": axis, "hideOverlap": True},
                  "splitNumber": 4},
        "yAxis": {"type": "category", "data": labels,
                  "axisLabel": {"color": axis}},
        "series": [{
            "type": "bar",
            "data": values,
            "itemStyle": {"color": palette[palette_idx % len(palette)],
                          "borderRadius": [0, 4, 4, 0]},
            "label": {"show": True, "position": "right",
                      "formatter": f"{{c}} {unit}".strip(),
                      "color": axis},
        }],
    }).style(f"height: {height}px;")


def _donut(values: dict[str, int], height: int = 280) -> None:
    if not values:
        empty_state("No data.")
        return
    axis = echart_axis_color()
    palette = chart_palette()
    data = [{"name": k, "value": int(v)} for k, v in values.items() if v]
    ui.echart({
        "tooltip": {"trigger": "item",
                    "formatter": "{b}: {c} ({d}%)"},
        "legend": {"bottom": 0, "textStyle": {"color": axis}},
        "color": palette,
        "series": [{
            "type": "pie", "radius": ["40%", "70%"],
            "avoidLabelOverlap": True,
            "label": {"color": axis},
            "data": data,
        }],
    }).style(f"height: {height}px;")


def _events_per_date(events: pd.DataFrame) -> None:
    if events.empty:
        empty_state("No events.")
        return
    counts = (events.groupby("recording_date").size()
              .reset_index(name="n").sort_values("recording_date"))
    axis = echart_axis_color()
    palette = chart_palette()
    ui.echart({
        "tooltip": base_tooltip("axis"),
        "grid": base_grid(left=50, right=30, top=20, bottom=50),
        "xAxis": {"type": "category",
                  "data": counts["recording_date"].tolist(),
                  "axisLabel": {"rotate": 60, "color": axis, "fontSize": 10}},
        "yAxis": {"type": "value", "name": "events",
                  "nameTextStyle": {"color": axis},
                  "axisLabel": {"color": axis}},
        "series": [{
            "type": "line", "smooth": True, "showSymbol": False,
            "data": counts["n"].tolist(),
            "areaStyle": {"opacity": 0.2},
            "lineStyle": {"color": palette[0]},
            "itemStyle": {"color": palette[0]},
        }],
    }).style("height: 300px;")


def _behavior_type_bar(events: pd.DataFrame) -> None:
    if events.empty:
        empty_state("No events.")
        return
    counts = events["behavior_type"].value_counts()
    types = [t for t in ("START", "STOP", "POINT") if t in counts.index]
    _bar_horizontal(types, [int(counts[t]) for t in types],
                    unit="events", palette_idx=2, height=180)


def _top_behaviors(events: pd.DataFrame, top: int = 15) -> None:
    if events.empty:
        empty_state("No events.")
        return
    counts = events["behavior"].value_counts().head(top)
    labels = counts.index.tolist()[::-1]
    vals = [int(v) for v in counts.values][::-1]
    _bar_horizontal(labels, vals, unit="events", palette_idx=0,
                    height=max(280, 30 + 22 * len(labels)))


def _top_modifiers(events: pd.DataFrame, top: int = 15) -> None:
    sub = events.dropna(subset=["modifier_1"])
    sub = sub[sub["modifier_1"].astype(str).str.strip() != ""]
    if sub.empty:
        empty_state("No modifier_1 values in this slice.")
        return
    counts = sub["modifier_1"].value_counts().head(top)
    labels = counts.index.tolist()[::-1]
    vals = [int(v) for v in counts.values][::-1]
    _bar_horizontal(labels, vals, unit="events", palette_idx=3,
                    height=max(280, 30 + 22 * len(labels)))


def _duration_boxplot(intervals: pd.DataFrame, top: int = 10) -> None:
    paired = intervals[intervals["pairing_status"] == "PAIRED"].copy()
    paired = paired.dropna(subset=["duration_s"])
    if paired.empty:
        empty_state("No paired intervals yet.")
        return
    top_behaviors = paired["behavior"].value_counts().head(top).index.tolist()
    paired = paired[paired["behavior"].isin(top_behaviors)]
    boxes = []
    counts = []
    for b in top_behaviors:
        vals = paired.loc[paired["behavior"] == b, "duration_s"].astype(float)
        if vals.empty:
            continue
        q = vals.quantile([0.05, 0.25, 0.5, 0.75, 0.95]).tolist()
        boxes.append([round(x, 1) for x in q])
        counts.append(int(vals.shape[0]))

    axis = echart_axis_color()
    palette = chart_palette()
    ui.echart({
        "tooltip": {"trigger": "item",
                    "formatter": "{b}<br/>p5/p25/p50/p75/p95 (s)"},
        "grid": base_grid(left=200, right=40, top=20, bottom=50),
        "xAxis": {"type": "value", "name": "duration (s)",
                  "nameTextStyle": {"color": axis},
                  "axisLabel": {"color": axis}},
        "yAxis": {"type": "category", "data": top_behaviors,
                  "axisLabel": {"color": axis}},
        "series": [{
            "type": "boxplot", "data": boxes,
            "itemStyle": {"color": palette[1]},
        }],
    }).style(f"height: {max(280, 30 + 28 * len(top_behaviors))}px;")


def _case_gantt(intervals: pd.DataFrame, points: pd.DataFrame) -> None:
    """Swimlane: rows are subjects, x is minutes from case start.

    Each paired interval renders as a colored ``markArea`` rectangle on the
    subject's row. POINT events render as triangles.
    """
    paired = intervals[intervals["pairing_status"] == "PAIRED"].copy()
    if paired.empty and points.empty:
        empty_state("No events for this case.")
        return

    present = (
        set(paired["subject"].dropna().unique())
        | set(points["subject"].dropna().unique())
    )
    subjects = [s for s in SUBJECT_ORDER if s in present] + \
               sorted(s for s in present if s not in SUBJECT_ORDER)

    palette = chart_palette()
    behaviors = sorted(set(paired["behavior"]).union(set(points["behavior"])))
    color_for = {b: palette[i % len(palette)] for i, b in enumerate(behaviors)}

    mark_areas = []
    for r in paired.itertuples():
        mark_areas.append([
            {
                "name": f"{r.behavior} ({_format_hms(r.duration_s)})",
                "xAxis": float(r.start_time_s) / 60.0,
                "yAxis": r.subject,
                "itemStyle": {
                    "color": color_for.get(r.behavior, palette[0]),
                    "opacity": 0.85,
                },
            },
            {"xAxis": float(r.end_time_s) / 60.0, "yAxis": r.subject},
        ])

    point_scatter = [
        {"value": [float(r.time_s) / 60.0, r.subject],
         "name": r.behavior,
         "itemStyle": {"color": color_for.get(r.behavior, palette[5])}}
        for r in points.itertuples() if r.subject in subjects
    ]

    axis = echart_axis_color()
    ui.echart({
        "tooltip": {"trigger": "item"},
        "legend": {
            "data": [{"name": b} for b in behaviors],
            "type": "scroll", "top": 0,
            "textStyle": {"color": axis},
        },
        "grid": base_grid(left=140, right=40, top=50, bottom=40),
        "xAxis": {"type": "value", "name": "minutes from start",
                  "nameTextStyle": {"color": axis},
                  "axisLabel": {"color": axis}},
        "yAxis": {"type": "category", "data": subjects,
                  "axisLabel": {"color": axis}},
        "series": [
            {
                "name": "intervals",
                "type": "scatter",
                "symbolSize": 1,
                "data": [],
                "markArea": {
                    "silent": False,
                    "data": mark_areas,
                    "label": {"show": False},
                },
            },
            {
                "name": "POINT events",
                "type": "scatter",
                "symbol": "triangle",
                "symbolSize": 10,
                "data": point_scatter,
                "encode": {"x": 0, "y": 1, "tooltip": [0, 1, 2]},
            },
        ],
    }).style(f"height: {max(260, 80 + 60 * len(subjects))}px;")


def _case_behavior_share(intervals_case: pd.DataFrame) -> None:
    paired = intervals_case[intervals_case["pairing_status"] == "PAIRED"]
    if paired.empty:
        empty_state("No paired intervals for this case.")
        return
    share = (paired.groupby("behavior")["duration_s"].sum()
             .sort_values(ascending=True))
    labels = share.index.tolist()
    vals = [round(v / 60.0, 1) for v in share.values]
    _bar_horizontal(labels, vals, unit="min", palette_idx=4,
                    height=max(220, 30 + 26 * len(labels)))


def _pairing_status_bar(intervals: pd.DataFrame) -> None:
    if intervals.empty:
        empty_state("No intervals.")
        return
    counts = intervals["pairing_status"].value_counts()
    order = ["PAIRED", "MISSING_STOP", "ERROR_DOUBLE_START"]
    labels = [o for o in order if o in counts.index]
    vals = [int(counts[o]) for o in labels]
    _bar_horizontal(labels, vals, unit="intervals",
                    palette_idx=1, height=200)


def _aggrid(df: pd.DataFrame, columns: list[str] | None = None,
            height: int = 320) -> None:
    if df.empty:
        empty_state("Nothing flagged.")
        return
    use = df[columns] if columns else df
    use = use.copy()
    for c in use.columns:
        if pd.api.types.is_float_dtype(use[c]):
            use[c] = use[c].round(2)
    ui.aggrid({
        "defaultColDef": {
            "sortable": True, "filter": True,
            "resizable": True, "floatingFilter": True,
            "minWidth": 140,
        },
        "columnDefs": [{"field": c} for c in use.columns],
        "rowData": use.to_dict("records"),
        "pagination": True, "paginationPageSize": 25,
        "animateRows": True,
        "suppressColumnVirtualisation": True,
    }).classes("ag-theme-balham w-full").style(f"height: {height}px")


# ---------------------------------------------------------------------------
# Tab panels
# ---------------------------------------------------------------------------

def _overview_panel(events: pd.DataFrame, intervals: pd.DataFrame,
                    total_mp4_cases: int) -> None:
    total_events = len(events)
    total_intervals = int((intervals["pairing_status"] == "PAIRED").sum())
    total_cases = events["case_key"].nunique()
    tagged_hours = round(
        intervals.loc[intervals["pairing_status"] == "PAIRED", "duration_s"]
        .dropna().sum() / 3600, 1
    )
    coverage_pct = round(total_cases / total_mp4_cases * 100, 1) \
        if total_mp4_cases else 0.0

    with ui.row().classes("w-full no-wrap gap-4"):
        kpi_card("EVENTS",   f"{total_events:,}",  "all behavior_types")
        kpi_card("INTERVALS", f"{total_intervals:,}", "PAIRED START/STOP")
        kpi_card("TAGGED CASES", f"{total_cases:,}",
                 f"{coverage_pct}% of {total_mp4_cases} MP4 cases")
        kpi_card("TAGGED TIME", f"{tagged_hours:,} h", "sum of paired durations")

    with ui.row().classes("w-full no-wrap gap-4 items-stretch"):
        with ui.card().classes("surface-1 q-pa-md flex-grow"):
            ui.label("Subject mix").classes("text-subtitle1 text-weight-medium")
            ui.label("Events by subject role.").classes("text-caption muted")
            counts = events["subject"].fillna("(unknown)").value_counts().to_dict()
            ordered = {s: counts.get(s, 0) for s in SUBJECT_ORDER if s in counts}
            for k, v in counts.items():
                if k not in ordered:
                    ordered[k] = v
            _donut(ordered)

        with ui.card().classes("surface-1 q-pa-md flex-grow"):
            ui.label("Behavior-type mix").classes("text-subtitle1 text-weight-medium")
            ui.label("START / STOP / POINT counts.").classes("text-caption muted")
            _behavior_type_bar(events)

    _section(
        "Events per recording date",
        "All events (START + STOP + POINT) per day.",
        lambda: _events_per_date(events),
    )


def _behaviors_panel(events: pd.DataFrame, intervals: pd.DataFrame) -> None:
    _section(
        "Top behaviors",
        "Most frequent behavior labels in the current slice.",
        lambda: _top_behaviors(events, top=15),
    )
    _section(
        "Top modifier_1 values",
        "Drills into the main modifier (target / context of the behavior).",
        lambda: _top_modifiers(events, top=15),
    )
    _section(
        "Duration distribution per behavior",
        "Paired intervals only. p5 / p25 / median / p75 / p95 in seconds. "
        "Top 10 behaviors by interval count.",
        lambda: _duration_boxplot(intervals, top=10),
    )


def _cases_panel(events: pd.DataFrame, intervals: pd.DataFrame,
                 filter_state: dict) -> None:
    if events.empty:
        empty_state("No tagged cases in this slice.")
        return

    case_counts = (events.groupby("case_key").size()
                   .sort_values(ascending=False))
    case_choices = case_counts.index.tolist()
    default_case = filter_state.get("case") or case_choices[0]
    if default_case not in case_choices:
        default_case = case_choices[0]
    filter_state["case"] = default_case

    case_box = ui.card().classes("surface-1 w-full q-pa-md")
    detail_box = ui.column().classes("w-full gap-4")

    def _draw_detail() -> None:
        detail_box.clear()
        selected = filter_state["case"]
        e_case = events[events["case_key"] == selected]
        i_case = intervals[intervals["case_key"] == selected]
        with detail_box:
            with ui.row().classes("w-full no-wrap gap-4"):
                paired = int((i_case["pairing_status"] == "PAIRED").sum())
                pts = int((e_case["behavior_type"] == "POINT").sum())
                length_min = round(e_case["time_s"].max() / 60, 1) \
                    if not e_case.empty else 0.0
                kpi_card("EVENTS", f"{len(e_case):,}", "in this case")
                kpi_card("INTERVALS", f"{paired:,}", "paired")
                kpi_card("POINT MARKS", f"{pts:,}", "instantaneous")
                kpi_card("CASE LENGTH", f"{length_min:,} min",
                         "max time_s seen")

            _section(
                "Behavior timeline",
                "Each colored bar = one paired interval. Triangles = POINT events.",
                lambda: _case_gantt(
                    i_case,
                    e_case[e_case["behavior_type"] == "POINT"],
                ),
            )
            _section(
                "Time share by behavior",
                "Sum of paired interval durations, in minutes.",
                lambda: _case_behavior_share(i_case),
            )

    with case_box:
        ui.label("Pick a case").classes("text-subtitle1 text-weight-medium")
        ui.label("Cases are ordered by event count.") \
            .classes("text-caption muted")

        def _on_case(e) -> None:
            filter_state["case"] = e.value
            _draw_detail()

        ui.select(
            options=case_choices, value=default_case,
            label="Case", on_change=_on_case,
        ).props("outlined dense use-input input-debounce=0 hide-selected fill-input") \
         .classes("min-w-[320px]")

    _draw_detail()

    _section(
        "All tagged cases",
        "Events per case, with link to behavior counts.",
        lambda: _aggrid(
            (events.groupby(["recording_date", "case_no"])
                   .agg(events=("event_id", "count"),
                        subjects=("subject", "nunique"),
                        behaviors=("behavior", "nunique"))
                   .reset_index()
                   .sort_values(["recording_date", "case_no"])),
            height=320,
        ),
    )


def _quality_panel(events: pd.DataFrame, intervals: pd.DataFrame,
                   db_path: str) -> None:
    with ui.row().classes("w-full no-wrap gap-4 items-stretch"):
        with ui.card().classes("surface-1 q-pa-md flex-grow"):
            ui.label("Pairing status").classes("text-subtitle1 text-weight-medium")
            ui.label("How many START events resolve cleanly to a STOP.") \
                .classes("text-caption muted")
            _pairing_status_bar(intervals)

        with ui.card().classes("surface-1 q-pa-md flex-grow"):
            ui.label("Rare behaviors").classes("text-subtitle1 text-weight-medium")
            ui.label(
                f"Behaviors appearing in ≤ {RARE_BEHAVIOR_MAX_CASES} cases — "
                "may be typos or one-off labels."
            ).classes("text-caption muted")
            per_b = (events.groupby("behavior")["case_key"].nunique()
                     .sort_values())
            rare = per_b[per_b <= RARE_BEHAVIOR_MAX_CASES]
            if rare.empty:
                empty_state("No rare behaviors in this slice.")
            else:
                _bar_horizontal(rare.index.tolist(),
                                [int(v) for v in rare.values],
                                unit="cases", palette_idx=2,
                                height=max(200, 30 + 24 * len(rare)))

    _section(
        f"Long intervals (≥ {LONG_INTERVAL_SEC // 60} min)",
        "Probable missing STOPs or operator left a behavior open.",
        lambda: _aggrid(
            (intervals[(intervals["pairing_status"] == "PAIRED")
                       & (intervals["duration_s"] >= LONG_INTERVAL_SEC)]
             .assign(duration_min=lambda d: (d["duration_s"] / 60).round(1))
             [["recording_date", "case_no", "subject", "behavior",
               "modifier_1", "start_time_s", "end_time_s", "duration_min"]]
             .sort_values("duration_min", ascending=False)),
            height=320,
        ),
    )

    _section(
        "Unpaired STARTs",
        "MISSING_STOP and ERROR_DOUBLE_START rows. Worth reviewing in BORIS.",
        lambda: _aggrid(
            (intervals[intervals["pairing_status"] != "PAIRED"]
             [["recording_date", "case_no", "subject", "behavior",
               "modifier_1", "start_time_s", "pairing_status"]]
             .sort_values(["recording_date", "case_no", "start_time_s"])),
            height=320,
        ),
    )

    known = query_df(
        db_path,
        "SELECT DISTINCT recording_date, case_no FROM recording_details",
    )
    if not known.empty:
        known_keys = set(zip(known["recording_date"].astype(str),
                             known["case_no"].astype(int)))
        case_pairs = (events[["recording_date", "case_no"]]
                      .drop_duplicates())
        case_pairs["in_recording_details"] = [
            (str(d), int(c)) in known_keys
            for d, c in zip(case_pairs["recording_date"], case_pairs["case_no"])
        ]
        orphans = case_pairs[~case_pairs["in_recording_details"]] \
            .drop(columns=["in_recording_details"])
        _section(
            "Cases tagged but not in recording_details",
            "Filename in boris_events parses to a (date, case) the master "
            "table has never heard of.",
            lambda: _aggrid(orphans, height=240),
        )


# ---------------------------------------------------------------------------
# Page
# ---------------------------------------------------------------------------

@ui.page("/boris")
def boris_page() -> None:
    with page_frame("BORIS Tags"):
        db_path = state.get()
        ui.label("BORIS Tag Analysis").classes("section-h text-h5 text-weight-medium")
        ui.label(
            "Behavioral tags from BORIS exports. Tags are per-case, not "
            "per-camera, so no camera filter is offered."
        ).classes("text-caption muted")

        events_all = _load_events(db_path)
        if events_all.empty:
            ui.label(
                "boris_events is empty or missing. "
                "Run scripts/import_boris_tags.py first."
            ).classes("text-warning")
            return

        intervals_all = _build_intervals(events_all)
        mp4_cases = query_df(
            db_path,
            "SELECT COUNT(DISTINCT recording_date||'|'||case_no) AS n "
            "FROM cur_mp4_status_statistics",
        )
        total_mp4_cases = int(mp4_cases["n"].iloc[0]) if not mp4_cases.empty else 0

        dates_sorted = sorted(events_all["recording_date"].dropna().unique())
        filter_state: dict = {
            "date_from": dates_sorted[0] if dates_sorted else None,
            "date_to":   dates_sorted[-1] if dates_sorted else None,
            "subjects":  [],
            "behaviors": [],
            "modifiers": [],
            "case":      None,
        }

        body = ui.column().classes("w-full gap-4")

        def _render() -> None:
            body.clear()
            ev = _apply_event_filters(events_all, filter_state)
            iv = _apply_interval_filters(intervals_all, filter_state)
            with body:
                with ui.tabs().classes("w-full") as tabs:
                    ui.tab("Overview")
                    ui.tab("Behaviors")
                    ui.tab("Cases")
                    ui.tab("Quality")
                with ui.tab_panels(tabs, value="Overview").classes("w-full surface-1"):
                    with ui.tab_panel("Overview"):
                        _overview_panel(ev, iv, total_mp4_cases)
                    with ui.tab_panel("Behaviors"):
                        _behaviors_panel(ev, iv)
                    with ui.tab_panel("Cases"):
                        _cases_panel(ev, iv, filter_state)
                    with ui.tab_panel("Quality"):
                        _quality_panel(ev, iv, db_path)

        # ── Filter bar ────────────────────────────────────────────────────
        with ui.card().classes("surface-1 w-full q-pa-md"):
            ui.label("Filters").classes("text-subtitle1 text-weight-medium")
            ui.label(
                "Filters apply to every tab. Date range, subject role, "
                "behavior, and modifier_1 are all multi-aware."
            ).classes("text-caption muted")
            with ui.row().classes("w-full items-end gap-3 q-mt-sm wrap"):
                def _date_input(label: str, key: str):
                    with ui.input(label).props("dense readonly")\
                            .classes("min-w-[150px]") as inp:
                        inp.value = filter_state[key] or ""
                        with inp.add_slot("append"):
                            ui.icon("event").classes("cursor-pointer")
                        with ui.menu().props("no-parent-event") as menu:
                            ui.date(
                                value=filter_state[key],
                                on_change=lambda e, k=key, i=inp: (
                                    filter_state.__setitem__(k, e.value or None),
                                    i.set_value(e.value or ""),
                                    _render(),
                                ),
                            )
                        inp.on("click", lambda _, m=menu: m.open())
                    return inp

                _date_input("From", "date_from")
                _date_input("To", "date_to")

                ui.select(
                    options=sorted(events_all["subject"].dropna().unique().tolist()),
                    value=filter_state["subjects"], multiple=True,
                    label="Subject", clearable=True,
                    on_change=lambda e: (
                        filter_state.__setitem__("subjects", e.value or []),
                        _render(),
                    ),
                ).props("outlined dense use-chips").classes("min-w-[220px]")

                ui.select(
                    options=sorted(events_all["behavior"].dropna().unique().tolist()),
                    value=filter_state["behaviors"], multiple=True,
                    label="Behavior", clearable=True,
                    on_change=lambda e: (
                        filter_state.__setitem__("behaviors", e.value or []),
                        _render(),
                    ),
                ).props("outlined dense use-chips use-input input-debounce=0") \
                 .classes("min-w-[260px]")

                mod_opts = sorted(
                    events_all["modifier_1"].dropna()
                    .astype(str).str.strip()
                    .replace("", pd.NA).dropna().unique().tolist()
                )
                ui.select(
                    options=mod_opts,
                    value=filter_state["modifiers"], multiple=True,
                    label="Modifier", clearable=True,
                    on_change=lambda e: (
                        filter_state.__setitem__("modifiers", e.value or []),
                        _render(),
                    ),
                ).props("outlined dense use-chips use-input input-debounce=0") \
                 .classes("min-w-[260px]")

                def _reset() -> None:
                    filter_state["date_from"] = dates_sorted[0] if dates_sorted else None
                    filter_state["date_to"]   = dates_sorted[-1] if dates_sorted else None
                    filter_state["subjects"]  = []
                    filter_state["behaviors"] = []
                    filter_state["modifiers"] = []
                    filter_state["case"]      = None
                    ui.navigate.reload()

                ui.button("Reset", icon="restart_alt", on_click=_reset) \
                  .props("flat dense")

        _render()
