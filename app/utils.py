"""
Streamlit Utilities - Wrapper for ScalpelLab Core
This file maintains the legacy API for the Streamlit app while using the new Core library.
"""

import pandas as pd
from scalpellab.db.repository import Repository
from scalpellab.core.config import settings

# Initialize global repository
repo = Repository()

def list_tables(db_path=None):
    if db_path:
        return Repository(db_path).list_tables()
    return repo.list_tables()

def list_views(db_path=None):
    if db_path:
        return Repository(db_path).list_views()
    return repo.list_views()

def get_table_schema(db_path, table):
    return Repository(db_path).get_table_schema(table)

def load_table(db_path, table):
    return Repository(db_path).load_table(table)

from scalpellab.db.repository import Repository as connect_wrapper

# For backward compatibility with the 'with connect(db_path) as conn' pattern
def connect(db_path):
    return Repository(db_path)._connect()