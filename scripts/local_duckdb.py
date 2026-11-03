"""Shared bounded DuckDB connections for Task3 local fixture/loading/check paths."""

from pathlib import Path

import duckdb

LOCAL_DUCKDB_SETTINGS = {
    "memory_limit": "4GB",
    "threads": 1,
    "max_temp_directory_size": "2GB",
    "preserve_insertion_order": False,
}


def connect_local(path: str | Path):
    return duckdb.connect(str(path), config=dict(LOCAL_DUCKDB_SETTINGS))
