"""Tests for SoulManager."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.store.soul import SoulManager


class TestSoulManager:
    """Tests for SoulManager class."""

    @pytest.fixture
    def tmp_path(self, tmp_path: Path) -> Path:
        """Provide a temporary directory for test files."""
        return tmp_path

    def test_load_returns_empty_when_no_file(self, tmp_path: Path) -> None:
        """File doesn't exist -> return default."""
        manager = SoulManager(path=tmp_path / "nonexistent.json")
        result = manager.load()
        assert result == {"version": 1, "description": ""}

    def test_save_and_load(self, tmp_path: Path) -> None:
        """Save then load -> data preserved."""
        path = tmp_path / "soul.json"
        manager = SoulManager(path=path)
        soul_data = {"version": 1, "description": "Test soul"}
        manager.save(soul_data)
        result = manager.load()
        assert result == soul_data

    def test_load_invalid_json_falls_back_to_default(self, tmp_path: Path) -> None:
        """Malformed JSON -> return default."""
        path = tmp_path / "bad_soul.json"
        path.write_text("{ invalid json", encoding="utf-8")
        manager = SoulManager(path=path)
        result = manager.load()
        assert result == {"version": 1, "description": ""}