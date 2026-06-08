"""Tests for SkillRegistry (simplified, skill.md based).

Uses temporary directories with skill.md fixtures to test discovery and loading.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.skills.registry import SkillRegistry


def _create_skill_md(skill_dir: Path, name: str, description: str = "", body: str = "") -> Path:
    """Helper to create a skill.md file with frontmatter."""
    md_path = skill_dir / "skill.md"
    content = f"---\nname: {name}\ndescription: {description}\n---\n\n{body}"
    md_path.write_text(content, encoding="utf-8")
    return md_path


# ── Fixtures ─────────────────────────────────────────────────────────────


@pytest.fixture
def minimal_skill_dir(tmp_path: Path) -> Path:
    """创建一个最小 skill.md（无工具、无注入）。"""
    skill_dir = tmp_path / "minimal_skill"
    skill_dir.mkdir(parents=True)
    _create_skill_md(skill_dir, "minimal_skill", "A minimal test skill")
    return tmp_path


@pytest.fixture
def skill_with_injection_dir(tmp_path: Path) -> Path:
    """创建一个带提示注入的 skill.md。"""
    skill_dir = tmp_path / "injection_skill"
    skill_dir.mkdir(parents=True)
    _create_skill_md(skill_dir, "injection_skill", "Skill with injection", "你可以画图。")
    return tmp_path


@pytest.fixture
def empty_user_dir(tmp_path: Path) -> Path:
    """每个测试独立的空用户 skill 目录，避免受已安装 skill 影响。"""
    p = tmp_path / ".user-skills"
    p.mkdir(parents=True)
    return p


@pytest.fixture
def registry_with_skills(minimal_skill_dir: Path, empty_user_dir: Path) -> SkillRegistry:
    """注册表 + minimal_skill 已加载。"""
    reg = SkillRegistry(
        skills_dir=str(minimal_skill_dir),
        user_skills_dir=str(empty_user_dir),
    )
    reg.discover()
    return reg


# ── Tests ────────────────────────────────────────────────────────────────


class TestSkillRegistryDiscovery:
    def test_discover_empty_dir(self, tmp_path: Path, empty_user_dir: Path) -> None:
        """空目录 → 无 Skill 被发现。"""
        reg = SkillRegistry(skills_dir=str(tmp_path), user_skills_dir=str(empty_user_dir))
        skills = reg.discover()
        assert skills == {}

    def test_discover_nonexistent_dir(self, tmp_path: Path, empty_user_dir: Path) -> None:
        """不存在的目录 → 空。"""
        reg = SkillRegistry(
            skills_dir="/nonexistent/path",
            user_skills_dir=str(empty_user_dir),
        )
        skills = reg.discover()
        assert skills == {}

    def test_discover_minimal_skill(self, minimal_skill_dir: Path, empty_user_dir: Path) -> None:
        """最小 skill.md → 正确加载元信息。"""
        reg = SkillRegistry(
            skills_dir=str(minimal_skill_dir),
            user_skills_dir=str(empty_user_dir),
        )
        skills = reg.discover()
        assert "minimal_skill" in skills
        s = skills["minimal_skill"]
        assert s.name == "minimal_skill"
        assert s.description == "A minimal test skill"
        assert s.allowed_tools == []
        assert s.prompt_injection == ""

    def test_discover_with_injection(self, skill_with_injection_dir: Path, empty_user_dir: Path) -> None:
        """带提示注入的 skill → body 正确加载。"""
        reg = SkillRegistry(
            skills_dir=str(skill_with_injection_dir),
            user_skills_dir=str(empty_user_dir),
        )
        skills = reg.discover()
        assert "injection_skill" in skills
        s = skills["injection_skill"]
        assert s.name == "injection_skill"
        assert s.prompt_injection == "你可以画图。"
        assert s.allowed_tools == []

    def test_discover_skips_hidden_dirs(self, tmp_path: Path, empty_user_dir: Path) -> None:
        """以下划线开头的目录被跳过。"""
        hidden = tmp_path / "_private_skill"
        hidden.mkdir()
        _create_skill_md(hidden, "private")
        reg = SkillRegistry(
            skills_dir=str(tmp_path),
            user_skills_dir=str(empty_user_dir),
        )
        skills = reg.discover()
        assert "_private_skill" not in skills

    def test_discover_skips_dirs_without_md(self, tmp_path: Path, empty_user_dir: Path) -> None:
        """没有 skill.md 的目录被跳过。"""
        no_md = tmp_path / "no_md_dir"
        no_md.mkdir()
        reg = SkillRegistry(
            skills_dir=str(tmp_path),
            user_skills_dir=str(empty_user_dir),
        )
        skills = reg.discover()
        assert no_md.name not in skills

    def test_discover_idempotent(self, minimal_skill_dir: Path, empty_user_dir: Path) -> None:
        """discover() 幂等 — 多次调用结果相同。"""
        reg = SkillRegistry(
            skills_dir=str(minimal_skill_dir),
            user_skills_dir=str(empty_user_dir),
        )
        skills1 = reg.discover()
        skills2 = reg.discover()
        assert skills1 == skills2


class TestSkillRegistryQueries:
    def test_get_existing(self, registry_with_skills: SkillRegistry) -> None:
        s = registry_with_skills.get("minimal_skill")
        assert s is not None
        assert s.name == "minimal_skill"

    def test_get_nonexistent(self, registry_with_skills: SkillRegistry) -> None:
        assert registry_with_skills.get("nonexistent") is None

    def test_list_all(self, minimal_skill_dir: Path, empty_user_dir: Path) -> None:
        reg = SkillRegistry(
            skills_dir=str(minimal_skill_dir),
            user_skills_dir=str(empty_user_dir),
        )
        reg.discover()
        skills = reg.list_all()
        assert len(skills) == 1
        assert skills[0].name == "minimal_skill"

    def test_names(self, registry_with_skills: SkillRegistry) -> None:
        names = registry_with_skills.names()
        assert names == ["minimal_skill"]
