"""SessionRegistry 单元测试。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.store.sessions import SessionRegistry


class TestSessionRegistry:
    def test_new_registry_has_no_sessions(self, tmp_path: Path) -> None:
        r = SessionRegistry(tmp_path / "s.json")
        assert r.list() == []
        assert not (tmp_path / "s.json").exists()

    def test_add_and_list(self, tmp_path: Path) -> None:
        r = SessionRegistry(tmp_path / "s.json")
        tid = r.new_thread_id()
        r.add(tid)
        sessions = r.list()
        assert len(sessions) == 1
        assert sessions[0]["thread_id"] == tid
        assert sessions[0]["turn_count"] == 0
        # 写盘后能再次读出
        r2 = SessionRegistry(tmp_path / "s.json")
        assert r2.list()[0]["thread_id"] == tid

    def test_add_duplicate_is_noop(self, tmp_path: Path) -> None:
        r = SessionRegistry(tmp_path / "s.json")
        tid = r.new_thread_id()
        r.add(tid)
        r.add(tid)
        assert len(r.list()) == 1

    def test_update_turn_count_and_last_active(self, tmp_path: Path) -> None:
        r = SessionRegistry(tmp_path / "s.json")
        tid = r.new_thread_id()
        r.add(tid)
        r.update(tid, turn_count=3)
        s = r.get(tid)
        assert s is not None
        assert s["turn_count"] == 3
        # last_active 在 update 后应当 >= created_at
        assert s["last_active"] >= s["created_at"]

    def test_update_missing_thread_is_noop(self, tmp_path: Path) -> None:
        r = SessionRegistry(tmp_path / "s.json")
        # 不应抛异常
        r.update("nonexistent-thread-id", turn_count=5)
        assert r.list() == []

    def test_remove(self, tmp_path: Path) -> None:
        r = SessionRegistry(tmp_path / "s.json")
        tid1 = r.new_thread_id()
        tid2 = r.new_thread_id()
        r.add(tid1)
        r.add(tid2)
        assert r.remove(tid1) is True
        assert r.get(tid1) is None
        assert r.get(tid2) is not None
        # 再删一次返回 False
        assert r.remove(tid1) is False

    def test_list_sorted_by_last_active_desc(self, tmp_path: Path) -> None:
        r = SessionRegistry(tmp_path / "s.json")
        tid_old = r.new_thread_id()
        r.add(tid_old)
        # 手动把 last_active 调早
        r._data["sessions"][0]["last_active"] = "2020-01-01T00:00:00"
        r._save()
        tid_new = r.new_thread_id()
        r.add(tid_new)
        sessions = r.list()
        # 新的（默认 now）在前
        assert sessions[0]["thread_id"] == tid_new
        assert sessions[1]["thread_id"] == tid_old

    def test_corrupt_file_falls_back_to_empty(self, tmp_path: Path) -> None:
        p = tmp_path / "s.json"
        p.write_text("not valid json {{{")
        r = SessionRegistry(p)
        assert r.list() == []
        # 之后能正常写入
        tid = r.new_thread_id()
        r.add(tid)
        data = json.loads(p.read_text())
        assert data["sessions"][0]["thread_id"] == tid

    def test_get_returns_none_for_missing(self, tmp_path: Path) -> None:
        r = SessionRegistry(tmp_path / "s.json")
        assert r.get("missing") is None
