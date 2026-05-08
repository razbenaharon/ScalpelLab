# migrations/ — SQLite schema migrations

Hand-written, hand-applied SQL migrations. There is no migration framework
(no Alembic, no flyway). Each file is a self-contained transaction.

## Layout

```
migrations/
└── (numbered .sql files, applied in order)
```

## How to apply

```bash
# Always back up the DB first.
copy ScalpelDatabase.sqlite ScalpelDatabase.backup.sqlite

sqlite3 ScalpelDatabase.sqlite < migrations/NNN_short_description.sql
```

## Conventions for new migrations

- File name: `NNN_short_description.sql` (zero-padded sequential number).
- Wrap the entire migration in `BEGIN TRANSACTION; … COMMIT;`. The script
  must leave the DB consistent if it fails partway.
- Toggle `PRAGMA foreign_keys = OFF;` at the top and `ON` at the bottom when
  doing FK-direction changes — see prior migrations in git history for the
  pattern.
- For schema changes that need to preserve child-table FKs by name, use
  `PRAGMA legacy_alter_table = ON;` around the rebuild (procedure 7 in
  https://www.sqlite.org/lang_altertable.html#otheralter).
- Run `PRAGMA foreign_key_check;` before `COMMIT;` so violations show up in
  the script output.
- After applying, re-run `python scripts/helpers/sqlite_to_dbdiagram.py` to
  refresh `docs/scalpel_dbdiagram.txt`.
- Update the schema reference in `docs/project_context/scalpel_database_sqlite_context.md`
  if columns or relationships change.

## Pitfall

The `2_update_db.py` "managed columns" contract (see
`scripts/scripts.md`) means migrations can safely add new columns without
breaking re-runs. But if a migration **renames** a managed column, you must
update the corresponding column list in `2_update_db.py` in the same commit.
