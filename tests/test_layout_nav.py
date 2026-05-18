from __future__ import annotations

from app.layout import NAV_LINKS, NAV_SECTIONS


def test_sidebar_has_dashboard_and_pipeline_sections():
    section_names = [name for name, _ in NAV_SECTIONS]

    assert section_names == ["Dashboards & Monitoring", "Processing Pipeline"]


def test_nav_links_flatten_section_links():
    flattened = [link for _, links in NAV_SECTIONS for link in links]

    assert NAV_LINKS == flattened
    assert any(label == "Update DB + IDX" for label, *_ in NAV_LINKS)
