"""Tests for Skill system data models (simplified)."""

from __future__ import annotations

from src.skills.schema import SkillDefinition


class TestSkillDefinition:
    def test_default_values(self) -> None:
        s = SkillDefinition(name="test_skill", description="A test skill")
        assert s.name == "test_skill"
        assert s.description == "A test skill"
        assert s.allowed_tools == []
        assert s.prompt_injection == ""

    def test_custom_skill(self) -> None:
        s = SkillDefinition(
            name="data_analysis",
            description="数据分析",
            prompt_injection="你可以分析数据。",
        )
        assert s.name == "data_analysis"
        assert s.prompt_injection == "你可以分析数据。"

    def test_to_dict(self) -> None:
        s = SkillDefinition(
            name="test",
            description="desc",
            prompt_injection="injection text",
        )
        d = s.to_dict()
        assert d["name"] == "test"
        assert d["description"] == "desc"
        assert d["has_prompt_injection"] is True
        assert d["prompt_injection"] == "injection text"
        assert d["allowed_tools"] == []

    def test_to_dict_no_injection(self) -> None:
        s = SkillDefinition(name="empty", description="none")
        d = s.to_dict()
        assert d["has_prompt_injection"] is False
        assert d["prompt_injection"] == ""
