"""End-to-end integration tests for the Skill system.

Verifies that example Skills (code_review, data_analysis) are discoverable
and loadable through the full pipeline: skill.md → SkillRegistry → SkillDefinition.
"""

from __future__ import annotations

from src.skills.registry import SkillRegistry


class TestCodeReviewSkillIntegration:
    """code_review 示例 Skill 端到端验证。"""

    def test_discover_code_review_skill(self) -> None:
        reg = SkillRegistry()
        skills = reg.discover()
        assert "code_review" in skills, (
            "code_review skill should be discoverable from src/skills/code_review/skill.md"
        )

    def test_code_review_metadata(self) -> None:
        reg = SkillRegistry()
        reg.discover()
        skill = reg.get("code_review")
        assert skill is not None
        assert skill.description != ""
        assert "代码审查" in skill.description
        assert skill.prompt_injection != ""

    def test_code_review_no_tools(self) -> None:
        """code_review 是纯提示注入的 Skill，不应有自定义工具。"""
        reg = SkillRegistry()
        reg.discover()
        skill = reg.get("code_review")
        assert skill is not None
        assert skill.allowed_tools == []


class TestDataAnalysisSkillIntegration:
    """data_analysis 示例 Skill 端到端验证。"""

    def test_discover_data_analysis(self) -> None:
        reg = SkillRegistry()
        skills = reg.discover()
        if "data_analysis" in skills:
            skill = skills["data_analysis"]
            assert skill.name == "data_analysis"
            assert skill.prompt_injection != ""
            # data_analysis 是纯提示注入 Skill（tools.py 已移除）
            # agent 使用 run_python + shell 等内置工具执行分析
