# Changelog

What each milestone delivered. Every version is an annotated git tag on the
commit that closed that stage (`git tag -n20` shows the same notes).

## v1.0.0 — Public release (2026-09)

- Repository modernized for public review: CI on Windows, trimmed
  requirements, per-directory context docs.
- The real catalog is private. The repo ships an anonymized mock
  (`sample_data/ScalpelDatabase_mock.sqlite`, built and leak-checked by
  `scripts/helpers/build_mock_db.py`), and `config.py` falls back to it, so a
  clean clone runs the dashboard.
- History rewritten to remove the private catalog, staff workbooks and the
  computer-vision experiments.

## v0.6.0 — Timeline reconciliation (2026-06 to 2026-07)

- Proved OLD MP4 ↔ SEQ is the identity mapping and specified SEQ → NEW MP4
  (`docs/new recordings formula.md`), with explicit exceptions.
- `boris_remap_to_new_mp4.py` moves BORIS annotations onto the new
  synchronized timeline without overwriting the originals.
- Finalized analyses import: BORIS events and monitor vitals per case.
- SEQ frame-date sanity checks; unusable IDX timing marked NOT_SYNCABLE.

## v0.5.0 — NiceGUI dashboard (2026-05)

- Streamlit replaced by a native NiceGUI desktop app with ECharts, themes and
  dark mode.
- `cur_sync_status` view: one `is_syncable` gate shared by the converter and
  the dashboard.
- MP4, SEQ, BORIS, Anesthesiology and Database pages; launcher pages that run
  the three pipeline steps with live progress.
- End-to-end pipeline tests.

## v0.4.0 — SEQ intelligence and IDX repair (2026-04)

- `seq_enriched`: parsed SEQ headers and IDX timing per recording
  (dropped frames, drift, counter resets).
- `repair_seq_idx.py`: audits and rebuilds NorPix `.seq.idx` files, with
  resumable checkpoints.
- Foreign keys enforced across the catalog; BORIS import.

## v0.3.0 — VFR → CFR synchronized conversion (2026-01 to 2026-03)

- SEQ → MP4 converter rebuilt around real capture timing: raw H.264 from IDX
  offsets + timecodes v2 → mkvmerge VFR → FFmpeg 30 fps CFR, padded to the
  sync group's shared timeline, NVENC encode.
- `mp4_times` and `offset_seconds` in the catalog; DB-driven offset viewer.
- NorPix SEQ/IDX format references and SEQ field analysis; sync status report.

## v0.2.0 — Data-driven redaction (2025-11 to 2025-12)

- Batch redaction of monitor regions over case time ranges, driven by the
  catalog instead of by hand.
- Offset-tuned MultiMPV viewer for reviewing several cameras together.
- Modular core refactor (PR #1).

## v0.1.0 — Catalog and SEQ/MP4 inventory (2025-09 to 2025-11)

- SQLite catalog designed for the project: recordings, SEQ and MP4 status,
  views over missing and partial cases.
- Scanners and exporters for SEQ → MP4 (FFmpeg and CLExport), GPU batch
  export, video cutting.
- First MultiMPV multi-camera playback.
