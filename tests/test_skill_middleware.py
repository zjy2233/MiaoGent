"""Tests for SkillContextMiddleware (simplified — message-based detection)."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.skills.middleware import SkillContextMiddleware
from src.skills.schema import SkillDefinition


class _MockRequest:
    """模拟 AgentMiddleware 请求对象。"""

    def __init__(self, state: dict | None = None, messages: list | None = None) -> None:
        self.state = state or {}
        self.messages = messages or []

    def override(self, **kwargs: Any) -> "_MockRequest":
        for k, v in kwargs.items():
            setattr(self, k, v)
        return self


def _make_ai_msg(tool_calls: list[dict[str, Any]]) -> Any:
    """创建一个模拟的 AIMessage with tool_calls。"""
    # 使用简单的 MagicMock 模拟 AIMessage 结构
    msg = MagicMock()
    msg.type = "ai"
    msg.content = ""
    msg.tool_calls = tool_calls
    return msg


def _make_human_msg(content: str) -> Any:
    msg = MagicMock()
    msg.type = "human"
    msg.content = content
    msg.tool_calls = None
    return msg


class TestSkillContextMiddleware:
    @pytest.fixture
    def registry(self) -> MagicMock:
        reg = MagicMock()
        reg.get.side_effect = lambda name: {
            "skill_a": SkillDefinition(
                name="skill_a",
                description="Skill A",
                prompt_injection="你可以做 A 任务。",
            ),
            "skill_b": SkillDefinition(
                name="skill_b",
                description="Skill B",
                prompt_injection="你可以做 B 任务。",
            ),
        }.get(name)
        return reg

    @pytest.fixture
    def middleware(self, registry: MagicMock) -> SkillContextMiddleware:
        return SkillContextMiddleware(registry=registry)

    async def test_no_skills_does_nothing(self, middleware: SkillContextMiddleware) -> None:
        """没有 load_skill 调用 → 不注入消息，直接调用 handler。"""
        handler = AsyncMock()
        req = _MockRequest(messages=[_make_human_msg("hello")])
        await middleware.awrap_model_call(req, handler)

        handler.assert_called_once_with(req)

    async def test_injects_skill_context(self, middleware: SkillContextMiddleware) -> None:
        """消息中有 load_skill 调用 → 注入对应 SystemMessage。"""
        handler = AsyncMock()
        handler.return_value = "done"
        req = _MockRequest(messages=[
            _make_human_msg("你会什么技能？"),
            _make_ai_msg([{"name": "load_skill", "args": {"skill_name": "skill_a"}, "id": "1"}]),
        ])
        await middleware.awrap_model_call(req, handler)

        assert len(req.messages) >= 1
        last_msg = req.messages[-1]
        assert last_msg.type == "system"
        assert "skill_a" in last_msg.content
        assert "你可以做 A 任务" in last_msg.content
        handler.assert_called_once()

    async def test_multiple_skills(self, middleware: SkillContextMiddleware) -> None:
        """多个 load_skill 调用 → 按名称排序注入。"""
        handler = AsyncMock()
        handler.return_value = "done"
        req = _MockRequest(messages=[
            _make_human_msg("你需要什么技能？"),
            _make_ai_msg([
                {"name": "load_skill", "args": {"skill_name": "skill_b"}, "id": "1"},
                {"name": "load_skill", "args": {"skill_name": "skill_a"}, "id": "2"},
            ]),
        ])
        await middleware.awrap_model_call(req, handler)

        last_msg = req.messages[-1]
        # 按字母序：skill_a 在前，skill_b 在后
        assert last_msg.content.index("skill_a") < last_msg.content.index("skill_b")

    async def test_skill_without_injection(self, registry: MagicMock) -> None:
        """Skill 没有 prompt_injection → 不注入。"""
        reg = MagicMock()
        reg.get.return_value = SkillDefinition(
            name="no_inject", description="No injection",
        )
        mw = SkillContextMiddleware(registry=reg)
        req = _MockRequest(messages=[
            _make_ai_msg([{"name": "load_skill", "args": {"skill_name": "no_inject"}, "id": "1"}]),
        ])
        handler = AsyncMock()
        await mw.awrap_model_call(req, handler)
        handler.assert_called_once_with(req)

    async def test_ignores_other_tool_calls(self, middleware: SkillContextMiddleware) -> None:
        """非 load_skill 的 tool_calls → 不注入。"""
        handler = AsyncMock()
        req = _MockRequest(messages=[
            _make_ai_msg([{"name": "calculator", "args": {"expression": "1+1"}, "id": "1"}]),
        ])
        await middleware.awrap_model_call(req, handler)
        handler.assert_called_once_with(req)
