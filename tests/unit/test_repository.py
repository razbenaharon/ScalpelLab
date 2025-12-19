import pytest
import os
import sqlite3
import pandas as pd
from scalpellab.db.repository import Repository

@pytest.fixture
def test_db(tmp_path):
    """Create a temporary database for testing."""
    db_file = tmp_path / "test.sqlite"
    conn = sqlite3.connect(str(db_file))
    cur = conn.cursor()
    cur.execute("CREATE TABLE test_table (id INTEGER PRIMARY KEY, name TEXT)")
    cur.execute("INSERT INTO test_table (name) VALUES ('test1'), ('test2')")
    cur.execute("CREATE VIEW test_view AS SELECT * FROM test_table")
    conn.commit()
    conn.close()
    return str(db_file)

def test_list_tables(test_db):
    repo = Repository(test_db)
    tables = repo.list_tables()
    assert "test_table" in tables
    assert len(tables) == 1

def test_list_views(test_db):
    repo = Repository(test_db)
    views = repo.list_views()
    assert "test_view" in views
    assert len(views) == 1

def test_load_table(test_db):
    repo = Repository(test_db)
    df = repo.load_table("test_table")
    assert isinstance(df, pd.DataFrame)
    assert len(df) == 2
    assert df.iloc[0]["name"] == "test1"

def test_get_next_pk(test_db):
    repo = Repository(test_db)
    next_id = repo.get_next_pk("test_table", "id")
    assert next_id == 3

def test_insert_delete_row(test_db):
    repo = Repository(test_db)
    repo.insert_row("test_table", {"name": "test3"})
    df = repo.load_table("test_table")
    assert len(df) == 3

    repo.delete_row("test_table", {"name": "test3"})
    df = repo.load_table("test_table")
    assert len(df) == 2
