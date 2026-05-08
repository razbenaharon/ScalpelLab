# docs/ — schema references, format specs, and tracking state

Reference material consumed by humans and (occasionally) by the dashboard.
This directory is **not** code-bearing — but its contents encode behavior the
code relies on (e.g. tracking JSONs are checkpoints).

## Contents

```
docs/
├── DOCSTRING_GUIDE.md              # Google-style docstring template used across the repo
├── ERD.pdf                         # rendered ERD viewed by app/pages/database.py
├── mp4_statistics.pdf              # static report
├── scalpel_dbdiagram.txt           # dbdiagram.io DBML export of the live schema
├── sync_status_2026-03-10.md       # snapshot of sync coverage on that date
├── redaction_tracking.json         # checkpoint state for batch_black_squere.py
├── seq_idx_repair_tracking.json    # checkpoint state for repair_seq_idx.py
├── Data_summary_guide.xlsx         # human reference
└── project_context/
    ├── scalpel_database_sqlite_context.md   # authoritative table-by-table schema notes
    ├── seq_enriched_table_reference.md      # per-column meaning of seq_enriched
    ├── norpix_seq_format_reference.md       # NorPix .seq binary layout
    └── norpix_idx_format_reference.md       # NorPix .seq.idx binary layout
```

## When to consult what

- **Adding/changing a table or column** → read
  `project_context/scalpel_database_sqlite_context.md` first; update it after
  the change.
- **Touching the SEQ/IDX parser** (`scripts/helpers/analyze_seq_fields.py` or
  `scripts/helpers/repair_seq_idx.py`) → read both `norpix_seq_format_reference.md`
  and `norpix_idx_format_reference.md`. Field offsets there are
  authoritative.
- **Designing a new dashboard page** that uses `seq_enriched` → read
  `seq_enriched_table_reference.md` for column semantics, especially around
  drift outliers.
- **Re-rendering the ERD** → regenerate `scalpel_dbdiagram.txt` via
  `scripts/helpers/sqlite_to_dbdiagram.py`, paste into dbdiagram.io, export
  the PDF over `ERD.pdf`.

## Tracking JSONs are state, not docs

`redaction_tracking.json` and `seq_idx_repair_tracking.json` are
**checkpoint files** written by their respective helper scripts. Don't edit
them by hand and don't delete them mid-run — you'll lose progress on long
batch jobs.
