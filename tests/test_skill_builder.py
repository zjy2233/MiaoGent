"""Tests for Skill integration in builder.py (simplified — pre-registration).

Verifies that:
- build_agent() initializes SkillRegistry and adds skill tools unconditionally
- AgentBundle returns skill fields
- All skill tools are pre-registered via tools list
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest


class TestBuilderSkillIntegration:
    def test_build_agent_signature(self) -> None:
        """build_agent 签名接受 session_id 和 skill_registry（向后兼容）。"""
        import inspect
        from src.agent.builder import build_agent

        sig = inspect.signature(build_agent)
        assert "session_id" in sig.parameters
        assert "skill_registry" in sig.parameters

    def test_build_supervisor_agent_signature(self) -> None:
        """build_supervisor_agent 签名接受 session_id 和 skill_registry。"""
        import inspect
        from src.agent.builder import build_supervisor_agent

        sig = inspect.signature(build_supervisor_agent)
        assert "session_id" in sig.parameters
        assert "skill_registry" in sig.parameters

    @patch("src.agent.builder.SkillRegistry")
    @patch("src.agent.builder.SkillContextMiddleware")
    @patch("src.agent.builder.build_delegate_task")
    @patch("src.agent.builder.create_agent")
    @patch("src.agent.builder.MemoryStore")
    @patch("src.agent.builder._get_profile_manager")
    @patch("src.agent.builder._get_soul_manager")
    def test_build_agent_calls_discover(
        self,
        mock_soul: MagicMock,
        mock_profile: MagicMock,
        mock_memory_store: MagicMock,
        mock_create_agent: MagicMock,
        mock_delegate: MagicMock,
        mock_middleware_cls: MagicMock,
        mock_registry_cls: MagicMock,
    ) -> None:
        """build_agent → 调用 registry.discover() 并预注册工具。"""
        mock_soul.return_value.load.return_value = {"version": 1, "description": ""}
        mock_profile.return_value.load.return_value = {"version": 1}
        mock_create_agent.return_value = MagicMock()

        mock_registry = MagicMock()
        from src.skills.schema import SkillDefinition
        mock_registry.list_all.return_value = [
            SkillDefinition(name="test_skill", description="desc"),
        ]
        mock_registry_cls.return_value = mock_registry

        from src.agent.builder import build_agent
        from langchain_core.language_models import BaseChatModel

        llm = MagicMock(spec=BaseChatModel)
        bundle = build_agent(llm)

        # 验证 discover 被调用
        mock_registry.discover.assert_called_once()

        # 验证 bundle 包含 skill_middleware
        assert bundle.skill_middleware is not None
        assert bundle.skill_registry is not None

        # 验证 create_agent 收到 tools 和 middleware
        call_args = mock_create_agent.call_args[1]
        assert "tools" in call_args
        assert "middleware" in call_args

        tool_list = call_args["tools"]
        # 不再有 skill tools（pure prompt），应有 list_skills + load_skill
        assert any(t.name == "load_skill" for t in tool_list if hasattr(t, "name"))

        # 验证 middleware 包含 SkillContextMiddleware
        middlewares = call_args["middleware"]
        assert mock_middleware_cls.return_value in middlewares

    @patch("src.agent.builder.SkillContextMiddleware")
    @patch("src.agent.builder.SkillRegistry")
    @patch("src.agent.builder.build_delegate_task")
    @patch("src.agent.builder.create_agent")
    @patch("src.agent.builder.MemoryStore")
    @patch("src.agent.builder._get_profile_manager")
    @patch("src.agent.builder._get_soul_manager")
    def test_build_agent_with_session_id(
        self,
        mock_soul: MagicMock,
        mock_profile: MagicMock,
        mock_memory_store: MagicMock,
        mock_create_agent: MagicMock,
        mock_delegate: MagicMock,
        mock_registry_cls: MagicMock,
        mock_middleware_cls: MagicMock,
    ) -> None:
        """session_id 不影响技能集成（技能对所有会话统一可用）。"""
        mock_soul.return_value.load.return_value = {"version": 1, "description": ""}
        mock_profile.return_value.load.return_value = {"version": 1}
        mock_create_agent.return_value = MagicMock()
        mock_registry_cls.return_value = MagicMock()

        from src.agent.builder import build_agent
        from langchain_core.language_models import BaseChatModel

        llm = MagicMock(spec=BaseChatModel)
        bundle = build_agent(llm, session_id="test_session")

        assert bundle.skill_middleware is not None
        assert bundle.skill_registry is not None
        mock_registry_cls.return_value.discover.assert_called_once()
