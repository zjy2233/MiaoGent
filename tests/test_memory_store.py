"""Tests for MemoryStore and MemoryExtractor."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.store.memory_store import MemoryStore, CORE_CATEGORIES


class TestMemoryStore:
    """MemoryStore 单元测试。"""

    @pytest.fixture
    def store(self, tmp_path: Path) -> MemoryStore:
        return MemoryStore(
            memory_path=str(tmp_path / "memory.json"),
            db_path=str(tmp_path / "memory.db"),
        )

    def test_default_core_memory(self, store: MemoryStore) -> None:
        core = store.load_core_memory()
        assert isinstance(core, dict)
        for cat in CORE_CATEGORIES:
            assert cat in core
            assert core[cat] == {}

    @pytest.mark.asyncio
    async def test_update_core_category(self, store: MemoryStore) -> None:
        await store.update_core_category("identity", {
            "name": {"value": "测试用户", "source": "explicit"},
        })
        core = store.load_core_memory()
        assert core["identity"]["name"]["value"] == "测试用户"
        assert core["identity"]["name"]["source"] == "explicit"

    @pytest.mark.asyncio
    async def test_update_core_explicit_over_discovered(self, store: MemoryStore) -> None:
        await store.update_core_category("identity", {
            "name": {"value": "旧值", "source": "discovered"},
        })
        await store.update_core_category("identity", {
            "name": {"value": "新值", "source": "explicit"},
        })
        core = store.load_core_memory()
        assert core["identity"]["name"]["value"] == "新值"

    @pytest.mark.asyncio
    async def test_core_discovered_does_not_overwrite_explicit(self, store: MemoryStore) -> None:
        await store.update_core_category("identity", {
            "name": {"value": "手动输入", "source": "explicit"},
        })
        await store.update_core_category("identity", {
            "name": {"value": "自动发现", "source": "discovered"},
        })
        core = store.load_core_memory()
        # explicit 优先级更高，不会被 discovered 覆盖
        assert core["identity"]["name"]["value"] == "手动输入"

    @pytest.mark.asyncio
    async def test_get_formatted_core_memory(self, store: MemoryStore) -> None:
        await store.update_core_category("identity", {
            "name": {"value": "tester", "source": "explicit"},
        })
        formatted = store.get_formatted_core_memory()
        assert "tester" in formatted
        assert "explicit" in formatted

    def test_add_working_memory(self, store: MemoryStore) -> None:
        ok = store.add_working_memory("preferences", "language", "zh", "explicit")
        assert ok is True
        memories = store.get_working_memories()
        assert len(memories) == 1
        assert memories[0]["key"] == "language"
        assert memories[0]["value"] == "zh"

    def test_add_working_memory_duplicate_lower_confidence(self, store: MemoryStore) -> None:
        store.add_working_memory("preferences", "style", "concise", "explicit")
        ok = store.add_working_memory("preferences", "style", "verbose", "discovered")
        assert ok is False  # discovered < explicit, 被拒绝
        memories = store.get_working_memories()
        assert memories[0]["value"] == "concise"

    def test_add_working_memory_duplicate_higher_confidence(self, store: MemoryStore) -> None:
        store.add_working_memory("preferences", "style", "verbose", "discovered")
        ok = store.add_working_memory("preferences", "style", "concise", "explicit")
        assert ok is True  # explicit > discovered, 覆盖
        memories = store.get_working_memories()
        assert memories[0]["value"] == "concise"

    def test_delete_working_memory(self, store: MemoryStore) -> None:
        store.add_working_memory("facts", "pet", "dog", "discovered")
        memories = store.get_working_memories()
        mid = memories[0]["id"]
        ok = store.delete_working_memory(mid)
        assert ok is True
        assert len(store.get_working_memories()) == 0

    def test_merge_working_memory(self, store: MemoryStore) -> None:
        updates = [
            {"category": "facts", "key": "pet", "value": "cat", "confidence": "discovered"},
            {"category": "facts", "key": "city", "value": "shanghai", "confidence": "discovered"},
        ]
        count = store.merge_working_memory(updates)
        assert count == 2
        assert len(store.get_working_memories()) == 2

    @pytest.mark.asyncio
    async def test_get_all_formatted(self, store: MemoryStore) -> None:
        await store.update_core_category("identity", {
            "name": {"value": "tester", "source": "explicit"},
        })
        store.add_working_memory("facts", "pet", "cat", "discovered")
        formatted = store.get_all_formatted()
        assert "tester" in formatted
        assert "cat" in formatted

    @pytest.mark.asyncio
    async def test_stats(self, store: MemoryStore) -> None:
        await store.update_core_category("identity", {"name": {"value": "t", "source": "explicit"}})
        store.add_working_memory("preferences", "a", "1", "discovered")
        store.add_working_memory("preferences", "b", "2", "discovered")
        stats = store.stats()
        assert stats["core_memory_entries"] == 1
        assert stats["working_memories_total"] == 2


class TestMemoryExtractor:
    """MemoryExtractor 单元测试（不依赖 LLM）。"""

    def test_classify_gate_empty(self) -> None:
        from src.agent.memory_extractor import _classify_gate
        assert _classify_gate([]) is False

    def test_extract_json_simple(self) -> None:
        from src.agent.memory_extractor import _extract_json
        result = _extract_json('{"identity": {"name": "test"}}')
        assert result == '{"identity": {"name": "test"}}'

    def test_extract_json_with_markdown(self) -> None:
        from src.agent.memory_extractor import _extract_json
        text = "```json\n{\"identity\": {\"name\": \"test\"}}\n```"
        result = _extract_json(text)
        assert result == '{"identity": {"name": "test"}}'

    def test_extract_json_no_brace(self) -> None:
        from src.agent.memory_extractor import _extract_json
        assert _extract_json("nothing here") is None
