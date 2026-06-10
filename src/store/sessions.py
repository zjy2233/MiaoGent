"""会话注册表：用 JSON 文件记录所有历史 thread_id。

文件位置：~/.miaogent/.sessions.json（默认），已加入 ``.gitignore``。
兼容旧 data/.sessions.json 路径。
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from pathlib import Path

from src.core.miaogent_home import get_data_path


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


class SessionRegistry:
    """一个轻量级的 thread_id 注册表。"""

    def __init__(self, path: Path | str | None = None) -> None:
        self.path = Path(path) if path else get_data_path(".sessions.json")
        self._load()

    def _load(self) -> None:
        if self.path.exists():
            try:
                self._data = json.loads(self.path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                self._data = {"sessions": []}
        else:
            self._data = {"sessions": []}

    def _save(self) -> None:
        self.path.write_text(
            json.dumps(self._data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def list(self) -> list[dict]:
        return sorted(
            self._data["sessions"],
            key=lambda s: s.get("last_active", ""),
            reverse=True,
        )

    def add(self, thread_id: str) -> None:
        if any(s["thread_id"] == thread_id for s in self._data["sessions"]):
            return
        now = _now_iso()
        self._data["sessions"].append(
            {
                "thread_id": thread_id,
                "created_at": now,
                "last_active": now,
                "turn_count": 0,
                "last_message": "",
            }
        )
        self._save()

    def update(self, thread_id: str, *, turn_count: int | None = None, last_message: str | None = None) -> None:
        for s in self._data["sessions"]:
            if s["thread_id"] == thread_id:
                s["last_active"] = _now_iso()
                if turn_count is not None:
                    s["turn_count"] = turn_count
                if last_message is not None:
                    s["last_message"] = last_message
                break
        self._save()

    def remove(self, thread_id: str) -> bool:
        before = len(self._data["sessions"])
        self._data["sessions"] = [
            s for s in self._data["sessions"] if s["thread_id"] != thread_id
        ]
        if len(self._data["sessions"]) < before:
            self._save()
            return True
        return False

    def remove_many(self, thread_ids: list[str]) -> int:
        """批量删除会话。返回实际删除的数量。"""
        ids = set(thread_ids)
        before = len(self._data["sessions"])
        self._data["sessions"] = [
            s for s in self._data["sessions"] if s["thread_id"] not in ids
        ]
        removed = before - len(self._data["sessions"])
        if removed:
            self._save()
        return removed

    def get(self, thread_id: str) -> dict | None:
        for s in self._data["sessions"]:
            if s["thread_id"] == thread_id:
                return s
        return None

    def new_thread_id(self) -> str:
        return str(uuid.uuid4())
