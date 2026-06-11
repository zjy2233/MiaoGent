"""Sub-agent 委托工具：让主 agent 通过工具调用创建隔离的 sub-agent。

用法：在 ``builder.py`` 中调用 ``build_delegate_task(llm)`` 生成工具实例，
加入主 agent 的工具列表。该工具**不**在 ``REGULAR_TOOLS`` 中，
sub-agent 无法调用此工具，从根本上防止无限递归。

.. versionchanged:: 2.0
   ``build_delegate_task`` accepts ``skill_registry`` to forward all skill
   tools to sub-agents (enable/disable removed, all skills pre-registered).
"""

from __future__ import annotations

import asyncio
import contextvars
from typing import Any

from langchain_core.tools import tool

__category__ = "agent"

from src.agent.sub_agent import REGULAR_TOOLS, run_sub_agent

_recursion_depth: contextvars.ContextVar[int] = contextvars.ContextVar(
    "delegate_recursion_depth", default=0
)
_MAX_DEPTH: int = 3


def build_delegate_task(
    llm: Any,
    *,
    session_id: str | None = None,
    skill_registry: Any | None = None,
) -> Any:
    """构建 ``delegate_task`` 工具，闭包捕获 ``llm`` 实例和 registry 引用。

    Args:
        llm: LLM 实例，传递给 sub-agent 工厂。
        session_id: 当前会话 ID（保留参数签名兼容性）。
        skill_registry: SkillRegistry 实例，用于为 sub-agent 预注册所有 Skill 工具。

    Returns:
        装饰了 ``@tool`` 的 async 函数，可直接加入 agent 工具列表。
    """

    @tool(description="将子任务委托给隔离的 sub-agent 执行。Sub-agent 有独立上下文和常规工具集，但无法再次委托。")
    async def delegate_task(task: str, timeout: int = 60) -> str:
        """将子任务委托给一个隔离的 sub-agent 独立执行。

        Sub-agent 拥有独立的上下文窗口（MemorySaver）和完整的常规工具集，
        但**无法再次调用此委托工具**，防止无限递归。
        Sub-agent 继承主 agent 的所有 Skill 工具。

        Args:
            task: 子任务描述，需清晰具体，sub-agent 会据此独立执行。
            timeout: 超时秒数（默认 60），超时后子任务被强制终止。

        Returns:
            sub-agent 的执行结果文本。
        """
        if _recursion_depth.get() >= _MAX_DEPTH:
            return f"错误：委托层级已达上限（最多 {_MAX_DEPTH} 层）"

        token = _recursion_depth.set(_recursion_depth.get() + 1)
        try:
            # ── 收集工具传给 sub-agent ──
            sub_tools = list(REGULAR_TOOLS)

            result = await asyncio.wait_for(
                run_sub_agent(llm, task, tools=sub_tools),
                timeout=timeout,
            )
            return result["result"]
        except asyncio.TimeoutError:
            return f"错误：子任务超时（{timeout}秒）"
        except Exception as exc:
            return f"错误：子任务执行失败（{exc}）"
        finally:
            _recursion_depth.reset(token)

    return delegate_task
