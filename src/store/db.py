"""SQLite connection manager."""

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Generator


@contextmanager
def get_connection(db_path: str | Path) -> Generator[sqlite3.Connection, None, None]:
    """Context manager for SQLite connections."""
    conn = sqlite3.connect(str(db_path))
    try:
        yield conn
    finally:
        conn.close()
