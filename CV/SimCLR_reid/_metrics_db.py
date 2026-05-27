"""SQLite metric logging for resumable SimCLR experiment sweeps."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any


RUN_META_COLUMNS = {
    "config_json",
    "seed",
    "status",
    "unstable_reason",
    "started_utc",
    "finished_utc",
    "completed_epochs",
    "best_epoch",
    "best_val_loss",
}


def open_db(path: Path) -> sqlite3.Connection:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS epoch_metrics (
            run_name        TEXT    NOT NULL,
            epoch           INTEGER NOT NULL,
            train_loss      REAL,
            val_loss        REAL,
            train_val_gap   REAL,
            learning_rate   REAL,
            seed            INTEGER,
            batch_size      INTEGER,
            run_status      TEXT,
            wall_clock_sec  REAL,
            timestamp_utc   TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
            PRIMARY KEY (run_name, epoch)
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_run_status ON epoch_metrics(run_status)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_run_name ON epoch_metrics(run_name)")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS run_meta (
            run_name         TEXT PRIMARY KEY,
            config_json      TEXT NOT NULL,
            seed             INTEGER,
            status           TEXT,
            unstable_reason  TEXT,
            started_utc      TEXT,
            finished_utc     TEXT,
            completed_epochs INTEGER,
            best_epoch       INTEGER,
            best_val_loss    REAL
        )
        """
    )
    conn.commit()
    return conn


def upsert_epoch(
    conn: sqlite3.Connection,
    run_name: str,
    epoch: int,
    train_loss: float | None,
    val_loss: float | None,
    lr: float | None,
    seed: int | None,
    batch_size: int | None,
    run_status: str,
    wall_clock_sec: float | None,
) -> None:
    train_val_gap = None
    if train_loss is not None and val_loss is not None:
        train_val_gap = float(val_loss) - float(train_loss)

    conn.execute(
        """
        INSERT OR REPLACE INTO epoch_metrics (
            run_name, epoch, train_loss, val_loss, train_val_gap,
            learning_rate, seed, batch_size, run_status, wall_clock_sec
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            run_name,
            int(epoch),
            train_loss,
            val_loss,
            train_val_gap,
            lr,
            seed,
            batch_size,
            run_status,
            wall_clock_sec,
        ),
    )
    conn.commit()


def upsert_run_meta(conn: sqlite3.Connection, run_name: str, **fields: Any) -> None:
    clean_fields: dict[str, Any] = {}
    for key, value in fields.items():
        if key not in RUN_META_COLUMNS:
            raise ValueError(f"Unknown run_meta column: {key}")
        if key == "config_json" and not isinstance(value, str):
            value = json.dumps(value, sort_keys=True, default=str)
        clean_fields[key] = value

    if "config_json" not in clean_fields:
        clean_fields["config_json"] = "{}"

    columns = ["run_name", *clean_fields.keys()]
    placeholders = ", ".join("?" for _ in columns)
    updates = ", ".join(f"{column}=excluded.{column}" for column in clean_fields)
    values = [run_name, *clean_fields.values()]

    conn.execute(
        f"""
        INSERT INTO run_meta ({", ".join(columns)})
        VALUES ({placeholders})
        ON CONFLICT(run_name) DO UPDATE SET {updates}
        """,
        values,
    )
    conn.commit()

