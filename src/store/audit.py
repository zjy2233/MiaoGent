"""命令执行审计日志 — SQLite 持久化 + 自动轮转。"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any

from src.core.miaogent_home import get_data_path
from src.store.db import get_connection

MAX_RECORDS = 10_000
SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp REAL NOT NULL,
    session_id TEXT,
    command TEXT NOT NULL,
    returncode INTEGER NOT NULL,
    duration REAL NOT NULL,
    stdout_size INTEGER DEFAULT 0,
    approved INTEGER DEFAULT 1
);
"""
INDEX_SQL = "CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit_log(timestamp);"
DELETE_OLD_SQL = (
    "DELETE FROM audit_log WHERE id IN ("
    "  SELECT id FROM audit_log ORDER BY timestamp ASC LIMIT ?"
    ")"
)
INSERT_SQL = (
    "INSERT INTO audit_log (timestamp, session_id, command, returncode, duration, stdout_size, approved) "
    "VALUES (?, ?, ?, ?, ?, ?, ?)"
)


@dataclass
class AuditRecord:
    timestamp: float
    command: str
    returncode: int
    duration: float
    stdout_size: int = 0
    session_id: str | None = None
    approved: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)


class AuditLogger:
    """SQLite 审计日志。"""

    def __init__(self, db_path: str | None = None) -> None:
        resolved = Path(db_path).resolve() if db_path else get_data_path("audit.db")
        self._db_path = str(resolved)
        self._lock = threading.Lock()
        self._init_db()

    def _init_db(self) -> None:
        with self._lock:
            with get_connection(self._db_path) as conn:
                conn.execute(SCHEMA_SQL)
                conn.execute(INDEX_SQL)
                conn.commit()

    def log(self, record: AuditRecord) -> None:
        with self._lock:
            with get_connection(self._db_path) as conn:
                conn.execute(
                    INSERT_SQL,
                    (
                        record.timestamp,
                        record.session_id,
                        record.command,
                        record.returncode,
                        record.duration,
                        record.stdout_size,
                        1 if record.approved else 0,
                    ),
                )
                conn.commit()
                self._maybe_cleanup(conn)

    def log_simple(
        self,
        command: str,
        returncode: int,
        duration: float,
        *,
        session_id: str | None = None,
        stdout_size: int = 0,
        approved: bool = True,
    ) -> None:
        self.log(AuditRecord(
            timestamp=time.time(),
            command=command,
            returncode=returncode,
            duration=duration,
            stdout_size=stdout_size,
            session_id=session_id,
            approved=approved,
        ))

    def query(self, limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
        with self._lock:
            with get_connection(self._db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.execute(
                    "SELECT * FROM audit_log ORDER BY id DESC LIMIT ? OFFSET ?",
                    (limit, offset),
                )
                return [dict(row) for row in cursor.fetchall()]

    def count(self) -> int:
        with self._lock:
            with get_connection(self._db_path) as conn:
                row = conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()
                return row[0] if row else 0

    def _maybe_cleanup(self, conn: sqlite3.Connection) -> None:
        row = conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()
        count = row[0] if row else 0
        if count > MAX_RECORDS:
            to_delete = count - MAX_RECORDS + 100
            conn.execute(DELETE_OLD_SQL, (to_delete,))
            conn.commit()

    def clear(self) -> None:
        with self._lock:
            with get_connection(self._db_path) as conn:
                conn.execute("DELETE FROM audit_log")
                conn.commit()
