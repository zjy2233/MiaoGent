"""Skill 查询服务（只读）。"""

from __future__ import annotations


class SkillService:
    """Skill 查询：列出所有可用 Skill 和查看单个 Skill 详情。"""

    def __init__(self) -> None:
        self._skill_registry = None

    def _lazy_skill_registry(self):
        if self._skill_registry is None:
            from src.skills.registry import SkillRegistry
            reg = SkillRegistry()
            reg.discover()
            self._skill_registry = reg
        return self._skill_registry

    def get_skills(self) -> list[dict]:
        """返回所有可用 Skill 的元信息。"""
        reg = self._lazy_skill_registry()
        return [s.to_dict() for s in reg.list_all()]

    def get_skill_detail(self, skill_name: str) -> dict | None:
        """返回单个 Skill 的详细信息。"""
        reg = self._lazy_skill_registry()
        skill = reg.get(skill_name)
        if skill is None:
            return None
        return skill.to_dict()
