"""Global visual system for the ScalpelLab NiceGUI app.

`apply_global_theme()` is called once at startup and:
  * registers the Quasar brand colors,
  * loads the Inter web font,
  * installs a small set of utility CSS classes used across pages
    (`section-h`, `kpi-card`, `surface-1/2`, `muted`).

Pages should consume tokens from `PALETTE` / `CHART_SEQ` instead of
hardcoding hex values, so a single edit here re-skins the app.
"""

from nicegui import ui


PALETTE = {
    "primary":   "#4F46E5",
    "secondary": "#0EA5E9",
    "accent":    "#14B8A6",
    "positive":  "#10B981",
    "warning":   "#F59E0B",
    "negative":  "#EF4444",
    "info":      "#6366F1",
}

CHART_SEQ = [
    "#4F46E5", "#14B8A6", "#F59E0B", "#EF4444",
    "#0EA5E9", "#8B5CF6", "#EC4899", "#10B981",
]


_GLOBAL_CSS = """
:root, body, .q-page {
    font-family: 'Inter', system-ui, -apple-system, Segoe UI, Roboto, sans-serif;
}

/* Cap content width on wide monitors so dashboards don't sprawl. */
.q-page-container > .q-page { max-width: 1440px; margin: 0 auto; }

/* Card hierarchy */
.nicegui-card { border-radius: 14px; }
.surface-1 { background: #ffffff; border-radius: 14px;
             box-shadow: 0 1px 2px rgb(15 23 42 / .06); }
.surface-2 { background: #f8fafc; border-radius: 12px; }
.body--dark .surface-1 { background: #1e293b; box-shadow: 0 1px 2px rgb(0 0 0 / .4); }
.body--dark .surface-2 { background: #0f172a; }

.muted { color: #64748b; }
.body--dark .muted { color: #94a3b8; }

/* KPI cards — subtle hover lift */
.kpi-card {
    transition: transform .15s ease, box-shadow .15s ease;
    border-radius: 14px;
}
.kpi-card:hover {
    transform: translateY(-2px);
    box-shadow: 0 6px 20px rgb(15 23 42 / .08);
}
.body--dark .kpi-card:hover { box-shadow: 0 6px 20px rgb(0 0 0 / .5); }

/* Section heading — left accent bar */
.section-h::before {
    content: "";
    display: inline-block;
    width: 4px;
    height: 1.05em;
    margin-right: 10px;
    background: var(--q-primary);
    border-radius: 2px;
    vertical-align: -2px;
}

/* Form polish */
.q-field--outlined .q-field__control { border-radius: 10px !important; }

/* AG-Grid: tighter, friendlier defaults */
.ag-theme-balham, .ag-theme-balham-dark {
    --ag-font-family: 'Inter', sans-serif;
    --ag-font-size: 13px;
    --ag-grid-size: 5px;
    --ag-border-radius: 10px;
}

/* Drawer nav links */
.nav-link {
    display: flex;
    align-items: center;
    gap: 12px;
    padding: 8px 12px;
    margin: 2px 8px;
    border-radius: 10px;
    color: inherit;
    text-decoration: none;
    transition: background .12s ease, color .12s ease;
    font-size: 14px;
}
.nav-link:hover  { background: rgb(79 70 229 / .10); color: var(--q-primary); }
.nav-link.active { background: rgb(79 70 229 / .14); color: var(--q-primary); font-weight: 600; }

/* Dark scrollbars */
.body--dark ::-webkit-scrollbar-thumb { background: #334155; border-radius: 6px; }
"""


def apply_global_theme() -> None:
    """Register colors, font, and global CSS. Call once before `ui.run()`."""
    ui.colors(**PALETTE)
    ui.add_head_html(
        '<link rel="preconnect" href="https://rsms.me/">'
        '<link rel="stylesheet" href="https://rsms.me/inter/inter.css">'
        f"<style>{_GLOBAL_CSS}</style>"
    )
