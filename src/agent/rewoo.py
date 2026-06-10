"""ReWOO 规划-执行模式：一次规划 → 并行执行 → 一次合成。

将标准的 ReAct 迭代循环替换为两阶段 LLM 调用：
1. Plan — 生成 JSON 工具调用计划
2. Execute — asyncio.gather 并行执行所有工具
3. Synthesize — 汇总工具结果生成最终答案

与标准 ReAct 相比，N 个独立工具从 N 次 LLM 调用减少到 2 次。
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import textwrap
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from src.agent.rewoo_intent import should_use_rewoo

logger = logging.getLogger(__name__)

_PLAN_SYSTEM_PROMPT = """You are a task planner. Given a user request and available tools,
generate a plan of tool calls to fulfill the request.

Return ONLY a valid JSON object with this exact structure:
{
    "steps": [
        {"tool": "tool_name", "args": {"param": "value"}, "description": "brief reason"}
    ]
}

Rules:
- Independent steps will be executed in parallel. Only mark dependencies if a step's
  input comes from another step's output.
- Use only tools from the provided list with their exact names.
- Do not include the final answer — the synthesis step will handle that.
- Keep arguments simple — prefer string values over complex nested objects.
- Maximum 6 steps per plan.
"""

_SYNTHESIS_SYSTEM_PROMPT = """You are an information synthesizer. The user asked a question
and multiple tools were executed to gather information. Synthesize the results into
a complete, concise answer.

Rules:
- Combine information from all tool results
- Cite which tool provided which information when it adds clarity
- If a tool returned an error, acknowledge it but work with available results
- Use Chinese if the user's question was in Chinese, English otherwise
"""


def _extract_text(response: Any) -> str:
    """从 LLM 响应/消息中提取文本（共享工具函数）。"""
    if response is None:
        return ""
    if hasattr(response, "content"):
        content = response.content
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return "".join(
                b.get("text", "") if isinstance(b, dict) else str(b)
                for b in content
            )
        return str(content)
    return str(response)


class ReWOOExecutor:
    """ReWOO 执行器：规划 → 并行执行 → 合成。"""

    def __init__(
        self,
        llm: BaseChatModel,
        tools: list[Any],
        *,
        max_parallel: int = 6,
    ):
        self._llm = llm
        self._tools_by_name: dict[str, Any] = {}
        for t in tools:
            name = getattr(t, "name", "") or str(t)
            if name:
                self._tools_by_name[name] = t
        self._max_parallel = max_parallel
        self._semaphore = asyncio.Semaphore(max_parallel)

    async def execute(self, user_query: str, context: str = "") -> str:
        """执行 ReWOO 三阶段流程。

        Args:
            user_query: 用户问题。
            context: 完整上下文（摘要/画像/记忆/时间），由 MergedContextMiddleware 组装。

        Returns:
            合成后的最终答案文本。
        """
        # Phase 1: 生成工具调用计划
        plan = await self._generate_plan(user_query, context)
        if not plan:
            return ""  # 空计划，返回空让调用方回退

        # Phase 2: 并行执行
        results = await self._execute_plan(plan)

        # Phase 3: 合成答案
        answer = await self._synthesize(user_query, results, context)
        return answer

    async def _generate_plan(
        self, user_query: str, context: str
    ) -> list[dict[str, Any]]:
        """生成工具调用计划。"""
        tool_list_lines: list[str] = []
        for name, t in sorted(self._tools_by_name.items()):
            desc = getattr(t, "description", "") or ""
            tool_list_lines.append(f"- {name}: {desc}")

        tool_list = "\n".join(tool_list_lines)
        context_block = f"\n[Context]\n{context}\n" if context else ""

        messages: list = [
            SystemMessage(content=_PLAN_SYSTEM_PROMPT + f"\n\nAvailable tools:\n{tool_list}{context_block}"),
            HumanMessage(content=user_query),
        ]

        try:
            response = await self._llm.ainvoke(messages)
        except Exception as exc:
            logger.error("ReWOO plan LLM call failed: %s", exc)
            return []

        content = _extract_text(response)
        plan = _parse_plan_json(content)
        if not plan:
            logger.warning("ReWOO plan parsing failed, raw response: %s", content[:200])
        return plan

    async def _execute_plan(
        self, steps: list[dict[str, Any]]
    ) -> dict[str, str]:
        """并行执行计划中的所有工具。"""

        async def _run_one(step: dict[str, Any]) -> tuple[str, str]:
            async with self._semaphore:
                tool_name = step.get("tool", "")
                description = step.get("description", tool_name)
                args = step.get("args", {})

                tool = self._tools_by_name.get(tool_name)
                if tool is None:
                    return (description, f"Error: tool '{tool_name}' not found")

                if not isinstance(args, dict):
                    args = {}

                try:
                    if hasattr(tool, "ainvoke"):
                        result = await tool.ainvoke(args)
                    else:
                        result = tool.invoke(args)
                    return (description, str(result))
                except Exception as exc:
                    return (description, f"Error executing {tool_name}: {exc}")

        tasks = [_run_one(s) for s in steps]
        outputs = await asyncio.gather(*tasks)
        return {desc: result for desc, result in outputs}

    async def _synthesize(
        self,
        user_query: str,
        results: dict[str, str],
        context: str,
    ) -> str:
        """合成最终答案。"""
        formatted_parts: list[str] = []
        for desc, result in results.items():
            formatted_parts.append(f"[{desc}]\n{result}")

        tool_results = "\n\n".join(formatted_parts)
        context_block = f"\n[Context]\n{context}\n" if context else ""

        messages: list = [
            SystemMessage(content=_SYNTHESIS_SYSTEM_PROMPT + context_block),
            HumanMessage(
                content=(
                    f"User question: {user_query}\n\n"
                    f"Tool results:\n{tool_results}\n\n"
                    f"Please synthesize a complete answer."
                )
            ),
        ]

        try:
            response = await self._llm.ainvoke(messages)
        except Exception as exc:
            logger.error("ReWOO synthesis LLM call failed: %s", exc)
            # 降级：直接拼接工具结果
            return "\n\n".join(f"[{d}]\n{r}" for d, r in results.items())

        return _extract_text(response)


def _parse_plan_json(text: str) -> list[dict[str, Any]]:
    """从 LLM 响应文本中提取并解析 JSON 计划。

    支持部分成功：如果部分步骤解析失败，提取有效步骤并记录警告。
    """
    # 尝试直接解析
    try:
        data = json.loads(text)
        if isinstance(data, dict) and "steps" in data:
            return _filter_valid_steps(data["steps"])
        if isinstance(data, list):
            return _filter_valid_steps(data)
    except json.JSONDecodeError:
        pass

    # 尝试提取 JSON 块
    match = re.search(r"\{[\s\S]*\}", text)
    if match:
        try:
            data = json.loads(match.group())
            if isinstance(data, dict) and "steps" in data:
                return _filter_valid_steps(data["steps"])
        except json.JSONDecodeError:
            pass

    return []


def _filter_valid_steps(steps: list[Any]) -> list[dict[str, Any]]:
    """过滤出有效的计划步骤。部分无效的步骤会被丢弃并记录警告。"""
    valid: list[dict[str, Any]] = []
    for i, step in enumerate(steps):
        if not isinstance(step, dict):
            logger.warning("ReWOO plan step %d is not a dict, skipping", i)
            continue
        if "tool" not in step:
            logger.warning("ReWOO plan step %d missing 'tool' field, skipping", i)
            continue
        if "args" not in step:
            logger.warning("ReWOO plan step %d missing 'args' field, skipping", i)
            continue
        valid.append(step)
    return valid


class ReWOORoutingMiddleware(AgentMiddleware):
    """ReWOO 路由中间件：检测复杂任务，触发规划-执行模式。

    位于 MergedContextMiddleware 之后，从其注入的 SystemMessage 中读取
    完整上下文（摘要/画像/记忆/时间），传递给 ReWOO 执行器。
    """

    def __init__(
        self,
        llm: BaseChatModel,
        tools: list[Any],
        *,
        enabled: bool = False,
    ):
        super().__init__()
        self._executor = ReWOOExecutor(llm, tools)
        self._enabled = enabled

    @property
    def enabled(self) -> bool:
        return self._enabled

    @enabled.setter
    def enabled(self, value: bool) -> None:
        self._enabled = value

    async def awrap_model_call(self, request: Any, handler: Any) -> Any:
        """拦截 LLM 调用，检测是否应使用 ReWOO。

        MergedContextMiddleware 已先执行并将上下文注入到消息列表头部，
        此处从注入的 SystemMessage 读取完整上下文传入 ReWOO。
        """
        # 未启用或非首轮 → 正常 ReAct
        if not self._enabled:
            return await handler(request)

        # 仅处理首轮（单条人类消息）
        human_msgs = [m for m in request.messages if isinstance(m, HumanMessage)]
        if len(human_msgs) != 1:
            return await handler(request)

        query = _extract_text(human_msgs[0])

        if not should_use_rewoo(query):
            return await handler(request)

        # 从 MergedContextMiddleware 注入的 SystemMessage 读取完整上下文
        full_context = _read_injected_context(request)

        logger.info("ReWOO triggered for query: %s", query[:100])
        try:
            result = await self._executor.execute(query, context=full_context)
            if result:
                return AIMessage(content=result)
            # 空结果 → 回退 ReAct
            logger.warning("ReWOO returned empty result, falling back to ReAct")
        except Exception as exc:
            logger.error("ReWOO execution failed, falling back to ReAct: %s", exc)

        return await handler(request)


def _read_injected_context(request: Any) -> str:
    """从 MergedContextMiddleware 注入的 SystemMessage 读取完整上下文。

    MergedContextMiddleware 在中间件链中先于 ReWOORoutingMiddleware 执行，
    会将合并后的上下文作为 SystemMessage 插入消息列表头部。
    """
    messages = getattr(request, "messages", []) or []
    for msg in messages:
        if isinstance(msg, SystemMessage):
            content = msg.content
            if isinstance(content, str):
                return content
            if isinstance(content, list):
                return "".join(
                    b.get("text", "") if isinstance(b, dict) else str(b)
                    for b in content
                )
    return ""
