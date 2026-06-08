"""Tests for ProfileManager."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.store.soul import ProfileManager


class TestProfileManager:
    """Tests for ProfileManager class."""

    @pytest.fixture
    def tmp_path(self, tmp_path: Path) -> Path:
        """Provide a temporary directory for test files."""
        return tmp_path

    def test_load_returns_empty_when_no_file(self, tmp_path: Path) -> None:
        """File doesn't exist -> return default."""
        manager = ProfileManager(path=tmp_path / "nonexistent.json")
        result = manager.load()
        assert result == {"version": 1}

    def test_set_and_get_field(self, tmp_path: Path) -> None:
        """Set name=张三, source=explicit -> verify both set."""
        path = tmp_path / "profile.json"
        manager = ProfileManager(path=path)
        manager.set("name", "张三", "explicit")
        profile = manager.load()
        assert profile["name"] == "张三"
        assert profile["name_source"] == "explicit"

    def test_unset_field(self, tmp_path: Path) -> None:
        """Set then unset -> field and _source both removed."""
        path = tmp_path / "profile.json"
        manager = ProfileManager(path=path)
        manager.set("name", "张三", "explicit")
        manager.unset("name")
        profile = manager.load()
        assert "name" not in profile
        assert "name_source" not in profile

    def test_merge_discovered_fields(self, tmp_path: Path) -> None:
        """Existing + new fields merged, existing not overwritten."""
        path = tmp_path / "profile.json"
        manager = ProfileManager(path=path)
        # Set initial field
        manager.set("name", "张三", "explicit")
        # Merge new fields without overwriting existing
        manager.merge({"age": 25, "city": "北京"})
        profile = manager.load()
        assert profile["name"] == "张三"
        assert profile["name_source"] == "explicit"
        assert profile["age"] == 25
        assert profile["age_source"] == "discovered"