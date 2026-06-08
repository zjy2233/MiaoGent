"""Sub-agent 工厂 — 动态创建隔离的执行单元。

每个 sub-agent 使用独立的 ``MemorySaver`` checkpointer 和受限工具集，
执行完即销毁，不会产生无限递归。

关键安全设计：
- ``REGULAR_TOOLS`` **不包含**任何可创建 agent 的委派能力
- ``create_sub_agent`` 只在 ``step_dispatcher`` 节点内部被调用，
  不是 LLM 可调用的工具
"""

from __future__ import annotations

import uuid
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import MemorySaver

# create_agent 是 LangGraph 推荐的 agent 构建方式
from langchain.agents import create_agent

from src.tools import (
    calculator,
    create_folder,
    current_time,
    grep_search,
    list_files,
    read_file,
    run_python,
    search,
    shell,
    weather,
    web_fetch,
    write_file,
)

# ── Sub-agent 可用工具清单 ──────────────────────────────────────────────
# 安全约束：只包含常规工具，**不含**任何可委派/创建 agent 的能力

REGULAR_TOOLS = [
    calculator,
    current_time,
    weather,
    search,
    web_fetch,
    list_files,
    read_file,
    grep_search,
    create_folder,
    write_file,
    run_python,
    shell,
]

SUB_AGENT_PROMPT = """你是一个子任务执行助手，专注于完成分配给你的具体任务。

行为准则：
1. 请不要创建子任务或委派给其他 agent——你只有当前这一次执行机会。
2. 完成你的任务后，给出简洁的结果。
3. 如果任务涉及最近发生的新闻/事件/消息，**必须先调用 search 工具（topic="news"）获取最新热搜**，避免依据过时的训练数据给出答案。"""


def create_sub_agent(
    llm: BaseChatModel,
    *,
    tools: list[Any] | None = None,
    prompt: str | None = None,
):
    """创建一个隔离的 sub-agent，使用内存级 checkpointer。

    Args:
        llm: LLM 实例（复用主 agent 的 LLM 配置）。
        tools: 工具列表，默认 ``REGULAR_TOOLS``。
        prompt: 系统提示，默认 ``SUB_AGENT_PROMPT``。

    Returns:
        CompiledStateGraph: 可调用的 sub-agent，执行完即弃。
    """
    base_tools = list(tools or REGULAR_TOOLS)
    base_prompt = prompt or SUB_AGENT_PROMPT

    return create_agent(
        model=llm,
        tools=base_tools,
        system_prompt=base_prompt,
        checkpointer=MemorySaver(),
        name="sub-agent",
    )


async def run_sub_agent(
    llm: BaseChatModel,
    task: str,
    *,
    tools: list[Any] | None = None,
    prompt: str | None = None,
) -> dict[str, Any]:
    """创建并运行一个 sub-agent，返回执行结果。

    每个调用生成一个全新的 sub-agent 实例和独立的 thread_id，
    实现**上下文隔离**。

    Args:
        llm: LLM 实例。
        task: 子任务描述。
        tools: 工具列表，默认 ``REGULAR_TOOLS``。
        prompt: 系统提示。

    Returns:
        {"result": str, "agent_id": str}
    """
    agent = create_sub_agent(llm, tools=tools, prompt=prompt)
    result = await agent.ainvoke(
        {"messages": [HumanMessage(content=task)]},
        {"configurable": {"thread_id": uuid.uuid4().hex}, "recursion_limit": 50},
    )
    messages = result.get("messages", [])
    response = messages[-1].content if messages else "(无返回)"
    if isinstance(response, list):
        response = "".join(
            b.get("text", "") for b in response if isinstance(b, dict)
        )
    return {
        "result": str(response) if response else "(空回答)",
        "agent_id": uuid.uuid4().hex[:8],
    }
