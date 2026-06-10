"""Skill 查询工具：让 Agent 通过工具调用发现可用的 Skill。

用法：在 ``builder.py`` 中调用 ``build_list_skills_tool(...)`` 生成工具实例，
加入 agent 的工具列表。该工具返回所有可用 Skill 的名称和描述。
"""

from __future__ import annotations

from typing import Any

from langchain_core.tools import tool


def build_list_skills_tool(
    *,
    skill_registry: Any | None = None,
) -> Any:
    """构建 ``list_skills`` 工具，闭包捕获 registry 引用。

    Args:
        skill_registry: SkillRegistry 实例。

    Returns:
        装饰了 ``@tool`` 的函数，可直接加入 agent 工具列表。
    """

    @tool
    def list_skills() -> str:
        """列出所有可用的 Skill 及其描述。

        返回每个 Skill 的名称、描述、含有的工具列表。
        适合在用户询问"你有什么技能"或"你能做什么"时调用。
        确定需要某个 Skill 后，使用 load_skill 激活它。
        """
        if not skill_registry:
            return "Skill 系统未启用"

        try:
            all_skills = skill_registry.list_all()
            if not all_skills:
                return "暂无可用 Skill"

            lines: list[str] = []
            for s in all_skills:
                tool_names = s.allowed_tools or []
                tools_str = f" 工具: {', '.join(tool_names)}" if tool_names else ""
                desc = s.description or "无描述"
                lines.append(f"- {s.name}\n  描述: {desc}{tools_str}")

            return (
                f"共 {len(all_skills)} 个 Skill：\n\n" + "\n".join(lines)
                + "\n\n使用 load_skill(\"skill-name\") 激活需要的 Skill。"
            )
        except Exception as exc:
            return f"查询 Skill 失败: {exc}"

    return list_skills
