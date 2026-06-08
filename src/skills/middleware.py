"""SkillContextMiddleware — 在 LLM 调用前注入已激活 Skill 的上下文。

通过扫描消息历史中的 ``load_skill`` 工具调用，自动将对应 Skill
的 prompt_injection 注入到后续 LLM 请求中。

不依赖外部存储或会话状态，消息历史本身即为真理来源。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import SystemMessage

if TYPE_CHECKING:
    from src.skills.registry import SkillRegistry

logger = logging.getLogger(__name__)


class SkillContextMiddleware(AgentMiddleware):
    """在 LLM 调用前注入已激活 Skill 的提示信息。

    通过从消息历史中查找 ``load_skill`` 工具调用，
    自动为后续 LLM 调用注入对应 Skill 的 prompt_injection。

    Args:
        registry: SkillRegistry 实例。
    """

    def __init__(self, registry: "SkillRegistry") -> None:
        super().__init__()
        self.registry = registry

    async def awrap_model_call(self, request: Any, handler: Any) -> Any:
        """从消息历史检测已激活的 Skill 并注入上下文。"""
        loaded_skills: set[str] = set()

        for msg in request.messages:
            # 检查 AI 消息中的 tool_calls 是否调用了 load_skill
            tc_list = getattr(msg, "tool_calls", None) or []
            for tc in tc_list:
                if tc.get("name") == "load_skill":
                    skill_name = tc.get("args", {}).get("skill_name", "")
                    if skill_name:
                        loaded_skills.add(skill_name)

        if not loaded_skills:
            return await handler(request)

        # 收集已激活 Skill 的 prompt_injection
        context_parts: list[str] = []
        for name in sorted(loaded_skills):
            skill = self.registry.get(name)
            if skill is None:
                continue
            if skill.prompt_injection:
                context_parts.append(f"[{skill.name}]\n{skill.prompt_injection}")

        if not context_parts:
            return await handler(request)

        skill_text = "已激活的技能：\n\n" + "\n\n".join(context_parts)
        skill_msg = SystemMessage(content=skill_text)

        request = request.override(
            messages=[*request.messages, skill_msg],
        )
        return await handler(request)
