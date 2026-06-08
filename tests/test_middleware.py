"""Tests for ProfileMiddleware and SummaryMiddleware."""

import pytest
from unittest.mock import AsyncMock, MagicMock

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import SystemMessage, HumanMessage

from src.agent.builder import ProfileMiddleware, SummaryMiddleware


class FakeRequest:
    """Fake request object for testing middleware."""

    def __init__(self, messages, state=None):
        self.messages = messages
        self.state = state or {}

    def override(self, messages=None):
        """Return a new FakeRequest with updated messages."""
        return FakeRequest(messages or self.messages, self.state)


class TestProfileMiddleware:
    """Tests for ProfileMiddleware."""

    async def test_injects_profile_into_messages(self):
        """Verify profile text is prepended to messages."""
        profile = {
            "version": 1,
            "name": "张三",
            "occupation": "工程师",
            "interest_source": "sports",
        }
        middleware = ProfileMiddleware(profile=profile)

        request = FakeRequest(
            messages=[HumanMessage(content="你好")],
            state={},
        )
        handler = AsyncMock(return_value="response")

        await middleware.awrap_model_call(request, handler)

        handler.assert_awaited_once()
        call_args = handler.call_args[0][0]
        injected_messages = call_args.messages

        # First message should be the profile system message
        assert isinstance(injected_messages[0], SystemMessage)
        assert "[用户画像]" in injected_messages[0].content
        assert "name: 张三" in injected_messages[0].content
        assert "occupation: 工程师" in injected_messages[0].content
        # _source fields should be excluded
        assert "interest_source" not in injected_messages[0].content

    async def test_does_not_inject_empty_profile(self):
        """If profile only has version, don't inject."""
        profile = {"version": 1}
        middleware = ProfileMiddleware(profile=profile)

        original_messages = [HumanMessage(content="你好")]
        request = FakeRequest(messages=original_messages, state={})
        handler = AsyncMock(return_value="response")

        await middleware.awrap_model_call(request, handler)

        handler.assert_awaited_once()
        call_args = handler.call_args[0][0]
        # Should pass through unchanged
        assert call_args.messages == original_messages

    async def test_injects_profile_with_only_non_version_fields(self):
        """Profile with only non-version data should be injected."""
        profile = {"name": "李四", "age": 30}
        middleware = ProfileMiddleware(profile=profile)

        request = FakeRequest(messages=[HumanMessage(content="你好")], state={})
        handler = AsyncMock(return_value="response")

        await middleware.awrap_model_call(request, handler)

        handler.assert_awaited_once()
        call_args = handler.call_args[0][0]
        assert isinstance(call_args.messages[0], SystemMessage)
        assert "name: 李四" in call_args.messages[0].content


class TestSummaryMiddleware:
    """Tests for SummaryMiddleware."""

    async def test_injects_summary_into_messages(self):
        """Verify summary is injected as SystemMessage."""
        middleware = SummaryMiddleware()

        request = FakeRequest(
            messages=[HumanMessage(content="你好")],
            state={"summary": "用户想要查询天气"},
        )
        handler = AsyncMock(return_value="response")

        await middleware.awrap_model_call(request, handler)

        handler.assert_awaited_once()
        call_args = handler.call_args[0][0]
        injected_messages = call_args.messages

        assert isinstance(injected_messages[0], SystemMessage)
        assert "[对话历史摘要]" in injected_messages[0].content
        assert "用户想要查询天气" in injected_messages[0].content

    async def test_does_not_inject_empty_summary(self):
        """If summary is empty, don't inject."""
        middleware = SummaryMiddleware()

        original_messages = [HumanMessage(content="你好")]
        request = FakeRequest(messages=original_messages, state={"summary": ""})
        handler = AsyncMock(return_value="response")

        await middleware.awrap_model_call(request, handler)

        handler.assert_awaited_once()
        call_args = handler.call_args[0][0]
        assert call_args.messages == original_messages