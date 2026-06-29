# AGENTS.md

Codex should treat [`CLAUDE.md`](CLAUDE.md) as the canonical repository router.
This file is intentionally thin to avoid context drift between agents.

Read `CLAUDE.md` first, then load only the per-directory context file relevant
to the task:

| Task involves... | Read |
|---|---|
| NiceGUI dashboard, pages, charts, theme | [`app/app.md`](app/app.md) |
| Numbered SEQ/DB/MP4 pipeline scripts or BORIS/analysis import | [`scripts/scripts.md`](scripts/scripts.md) |
| Helpers: SEQ fields, IDX repair, redaction, compare, video cutting | [`scripts/helpers/helpers.md`](scripts/helpers/helpers.md) |
| Tkinter + MPV multi-camera viewer | [`MPV_Multiviewer/mpv_multiviewer.md`](MPV_Multiviewer/mpv_multiviewer.md) |
| Schema references, NorPix format docs, ERD | [`docs/docs.md`](docs/docs.md) |

Key cross-cutting rules are in `CLAUDE.md`: preserve the managed-columns
contract in `scripts/2_update_db.py`, use dashboard DB helpers instead of raw
`sqlite3.connect()` inside NiceGUI pages, treat video/DB data as sensitive, and
verify potentially destructive script work with `--dry-run` first.
