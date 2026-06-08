"""ProgressiveSearchEngine — 多轮迭代渐进式搜索。

对复杂查询执行最多 ``max_iterations`` 轮搜索，每轮结束后由 LLM 评估
当前信息是否足以回答问题。达到最大轮次后触发兜底合成机制。

流程：:
    query → [搜索] → [LLM 评估] → 充足 → 返回格式化结果
                               ↘ 不足 → 精炼 query → 继续搜索
                                               ↘ 达最大轮次 → LLM 合成回答
"""

from __future__ import annotations

import json
import logging
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage

from src.core.config import Settings
from src.core.llm import build_llm
from src.tools.search.adapter import (
    SearchAdapter,
    SearchResult,
    _format_results,
)

logger = logging.getLogger(__name__)

_EVALUATION_SYSTEM_PROMPT = (
    "你是一个搜索质量评估专家。你的任务是评估当前的搜索结果是否足以回答用户的问题。\n\n"
    "请返回一个 **纯 JSON 对象**（不要 markdown 包裹，不要代码块），格式如下：\n"
    '{\n'
    '    "sufficient": true,\n'
    '    "reason": "简要说明判断理由",\n'
    '    "refined_query": "当 sufficient 为 false 时，提供更精确的搜索查询来补充缺失的信息"\n'
    '}\n\n'
    "判断标准：\n"
    "- sufficient = true：当前结果已经包含回答用户问题所需的**核心信息**\n"
    "- sufficient = false：当前结果**缺少关键信息**，需要继续搜索\n\n"
    "注意事项：\n"
    "- 如果已有信息可以给出部分回答但不够完整，sufficient 应为 false\n"
    "- refined_query 应该比上一轮更具体、更有针对性\n"
    "- 如果 sufficient 为 true，忽略 refined_query 字段"
)

_SYNTHESIS_SYSTEM_PROMPT = (
    "你是一个信息整合专家。用户提出了一个复杂问题，系统已经进行了多次搜索并收集了以下信息。\n"
    "请基于这些信息生成一个全面、准确、有条理的回答。\n\n"
    "要求：\n"
    "1. 基于已有信息回答，**不要编造**不存在的信息\n"
    "2. 指出信息中的不确定之处或矛盾之处\n"
    "3. 如果信息仍不足以完全回答问题，明确说明哪些方面信息不足\n"
    "4. 用中文回复，保持客观、准确、有条理\n"
    "5. 适当引用信息来源（注明来源名称）"
)


class EvaluationResult:
    """LLM 评估结果。"""

    def __init__(
        self,
        sufficient: bool,
        reason: str = "",
        refined_query: str | None = None,
    ) -> None:
        self.sufficient = sufficient
        self.reason = reason
        self.refined_query = refined_query

    def __repr__(self) -> str:
        return (
            f"EvaluationResult(sufficient={self.sufficient}, "
            f"reason={self.reason!r}, "
            f"refined_query={self.refined_query!r})"
        )


class ProgressiveSearchEngine:
    """渐进式搜索引擎：多轮迭代搜索 + LLM 评估 + 兜底合成。

    Args:
        adapters: 搜索引擎适配器列表（按优先级排序，自动 fallback）。
        llm: 用于评估和合成的 LLM 实例。为 ``None`` 时使用 ``build_llm()`` 创建。
        max_iterations: 最大搜索轮数（默认 3）。
    """

    def __init__(
        self,
        adapters: list[SearchAdapter],
        llm: BaseChatModel | None = None,
        max_iterations: int = 3,
    ) -> None:
        if not adapters:
            raise ValueError("至少需要一个搜索引擎适配器")
        self._adapters = adapters
        self._llm = llm or build_llm()
        self._max_iterations = max_iterations

    async def search(self, query: str, max_results: int = 5) -> str:
        """执行渐进式搜索。

        Args:
            query: 原始搜索查询。
            max_results: 每轮返回结果条数上限。

        Returns:
            格式化字符串（充足时直接展示结果，达上限时走 LLM 合成）。
        """
        all_results: list[SearchResult] = []
        current_query = query
        seen_urls: set[str] = set()

        for iteration in range(1, self._max_iterations + 1):
            logger.info(
                "ProgressiveSearch 第 %d/%d 轮：%s",
                iteration,
                self._max_iterations,
                current_query,
            )

            # 执行一轮搜索
            response = await self._do_search(current_query, max_results)

            # 去重后并入
            new_count = 0
            for r in response.results:
                dup = False
                if r.url and r.url in seen_urls:
                    dup = True
                elif any(existing.title == r.title for existing in all_results):
                    dup = True
                if not dup:
                    if r.url:
                        seen_urls.add(r.url)
                    all_results.append(r)
                    new_count += 1

            logger.info(
                "第 %d 轮获取 %d 条（新增 %d 条），累计 %d 条",
                iteration,
                len(response.results),
                new_count,
                len(all_results),
            )

            if not all_results:
                return f"未找到关于「{query}」的相关结果"

            # LLM 评估是否充足
            evaluation = await self._evaluate(query, all_results, iteration)

            if evaluation.sufficient:
                logger.info(
                    "渐进搜索提前结束（第 %d 轮）：%s",
                    iteration,
                    evaluation.reason,
                )
                return self._format_accumulated(query, all_results)

            # 精炼查询以继续下一轮
            if iteration < self._max_iterations:
                current_query = evaluation.refined_query or self._generate_next_query(
                    query, all_results
                )

        # 兜底：达到最大迭代次数，LLM 合成回答
        logger.info("渐进搜索达最大迭代 %d 次，进入 LLM 兜底合成", self._max_iterations)
        return await self._synthesize(query, all_results)

    async def _do_search(
        self, query: str, max_results: int
    ) -> Any:
        """使用适配器列表执行一次搜索（自动 fallback）。"""
        from src.tools.search.adapter import SearchResponse

        errors: list[str] = []
        for adapter in self._adapters:
            if hasattr(adapter, "available") and not adapter.available:
                continue
            try:
                return await adapter.search(query, max_results=max_results)
            except Exception as exc:
                logger.warning(
                    "ProgressiveSearch adapter %s failed: %s", adapter.name, exc
                )
                errors.append(f"{adapter.name}: {exc}")
                continue

        if errors:
            raise RuntimeError(
                f"所有搜索引擎均不可用：{'；'.join(errors)}"
            )
        raise RuntimeError("没有可用的搜索引擎适配器")

    async def _evaluate(
        self,
        original_query: str,
        results: list[SearchResult],
        iteration: int,
    ) -> EvaluationResult:
        """让 LLM 评估当前结果是否充足。"""
        results_text = _format_for_llm(results)

        prompt = (
            f"用户原始问题：{original_query}\n\n"
            f"当前是第 {iteration} 轮搜索（共最多 {self._max_iterations} 轮）。\n\n"
            f"已收集的搜索结果：\n{results_text}\n\n"
            "请评估当前信息是否足以回答用户的问题。"
        )

        try:
            response = await self._llm.ainvoke(
                [
                    SystemMessage(content=_EVALUATION_SYSTEM_PROMPT),
                    HumanMessage(content=prompt),
                ]
            )
            result = _parse_evaluation(response.content)
            logger.info("LLM 评估结果：%s", result)
            return result
        except Exception as exc:
            logger.warning("LLM 评估调用失败，默认继续搜索：%s", exc)
            return EvaluationResult(
                sufficient=False,
                reason=f"LLM 评估异常（{exc}），继续搜索",
                refined_query=original_query,
            )

    def _generate_next_query(
        self, original_query: str, results: list[SearchResult]
    ) -> str:
        """当 LLM 未提供精炼查询时，基于已有信息构造补充查询。

        简单策略：提取已有结果标题中的实体词 + 原始查询拼接。
        """
        # 提取标题中的关键词（2 字以上中文或 3 字母以上英文）
        import re

        keywords: list[str] = []
        for r in results[-3:]:  # 只看最近 3 条
            title = r.title or ""
            # 中文词组
            keywords.extend(re.findall(r"[\u4e00-\u9fff]{2,}", title))
            # 英文单词
            keywords.extend(
                w for w in re.findall(r"[a-zA-Z]{3,}", title) if w.lower()
                not in {"the", "and", "for", "with", "that", "this", "from"}
            )

        # 取 Top-3 未在原始查询中出现的关键词
        new_terms = [kw for kw in keywords if kw not in original_query][:3]
        if new_terms:
            return f"{original_query} {' '.join(new_terms)}"
        return original_query

    async def _synthesize(
        self, original_query: str, results: list[SearchResult]
    ) -> str:
        """兜底合成：让 LLM 基于已收集信息生成最佳回答。"""
        results_text = _format_for_llm(results)

        prompt = (
            f"用户问题：{original_query}\n\n"
            f"已收集的搜索结果（共 {len(results)} 条）：\n"
            f"{results_text}\n\n"
            "请基于以上信息生成回答。"
        )

        try:
            response = await self._llm.ainvoke(
                [
                    SystemMessage(content=_SYNTHESIS_SYSTEM_PROMPT),
                    HumanMessage(content=prompt),
                ]
            )
            content = response.content.strip()
            header = (
                f"搜索「{original_query}」的综合结果"
                f"（基于 {len(results)} 条信息来源）：\n\n"
            )
            return header + content
        except Exception as exc:
            logger.error("兜底合成 LLM 调用失败：%s", exc)
            # 降级到普通格式化输出
            return _format_results(
                original_query, results, "渐进搜索（综合）"
            )

    @staticmethod
    def _format_accumulated(
        query: str, results: list[SearchResult]
    ) -> str:
        """将充足的搜索结果格式化为最终输出。"""
        return _format_results(query, results, f"渐进搜索（{len(results)} 条）")


def _format_for_llm(results: list[SearchResult]) -> str:
    """将搜索结果格式化为 LLM 可读文本。"""
    if not results:
        return "(无结果)"

    lines: list[str] = []
    for i, r in enumerate(results, 1):
        lines.append(f"[{i}] 标题：{r.title}")
        if r.url:
            lines.append(f"    链接：{r.url}")
        if r.snippet:
            lines.append(f"    摘要：{r.snippet}")
        lines.append(f"    来源：{r.source}")
    return "\n".join(lines)


def _parse_evaluation(content: str) -> EvaluationResult:
    """解析 LLM 返回的 JSON 评估结果。"""
    text = content.strip()

    # 尝试提取 markdown 代码块中的 JSON
    for delimiter in ("```json", "```"):
        if delimiter in text:
            parts = text.split(delimiter)
            if len(parts) >= 2:
                text = parts[1].split("```")[0].strip()
                break

    # 直接解析 JSON
    try:
        data = json.loads(text)
        return EvaluationResult(
            sufficient=bool(data.get("sufficient", False)),
            reason=data.get("reason", ""),
            refined_query=data.get("refined_query"),
        )
    except json.JSONDecodeError:
        pass

    # 兜底：关键词匹配
    low = text.lower()
    # 包含 "sufficient.*true" 或 "充足" 且不包含 "不充足"
    has_true = "sufficient" in low and "true" in low
    has_enough = "充足" in low or "足够" in low or "可以回答" in text
    has_not_enough = "不充足" in low or "不足" in low or "不能回答" in text

    if has_not_enough:
        sufficient = False
    elif has_true or has_enough:
        sufficient = True
    else:
        sufficient = False

    return EvaluationResult(
        sufficient=sufficient,
        reason="基于关键词兜底解析",
        refined_query=None,
    )
