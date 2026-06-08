"""WebFetch 工具：获取 URL 页面正文内容。

借鉴 Claude Code 的 WebFetch 设计：
1. 获取 HTML 页面
2. 提取正文文本
3. 截断防止上下文溢出
"""

from __future__ import annotations

import re
from typing import Any

import requests
from langchain_core.tools import tool

_DEFAULT_TIMEOUT = 15
_MAX_TEXT_CHARS = 5000
_READ_BYTES = 512 * 1024  # 最多读取 512KB


def _extract_text(html: str) -> str:
    """从 HTML 中提取纯文本内容（去除标签、脚本、样式）。"""
    # 移除 script 和 style 块
    html = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r"<style[^>]*>.*?</style>", "", html, flags=re.DOTALL | re.IGNORECASE)
    # 移除 HTML 标签
    text = re.sub(r"<[^>]+>", " ", html)
    # 解码 HTML 实体
    text = _decode_entities(text)
    # 压缩空白
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _decode_entities(text: str) -> str:
    """解码常见 HTML 实体。"""
    entities = {
        "&amp;": "&",
        "&lt;": "<",
        "&gt;": ">",
        "&quot;": '"',
        "&#39;": "'",
        "&#x27;": "'",
        "&#x2F;": "/",
        "&nbsp;": " ",
        "&ndash;": "–",
        "&mdash;": "—",
        "&hellip;": "…",
    }
    for entity, char in entities.items():
        text = text.replace(entity, char)
    return text


def _try_decode(data: bytes, content_type: str | None = None) -> str | None:
    """尝试解码字节数据，按 Content-Type 或自动检测编码。"""
    charset = None
    if content_type:
        match = re.search(r"charset=([\w-]+)", content_type, re.IGNORECASE)
        if match:
            charset = match.group(1).strip().lower()

    # 按优先级尝试编码
    encodings = []
    if charset:
        encodings.append(charset)
    encodings.extend(["utf-8", "gbk", "gb2312", "big5", "latin-1"])

    for enc in encodings:
        try:
            return data.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return None


def fetch_page(url: str, *, timeout: float = _DEFAULT_TIMEOUT) -> str:
    """获取 URL 页面并提取正文文本。

    Args:
        url: 网页 URL。
        timeout: 请求超时秒数。

    Returns:
        提取的纯文本内容，自动截断到 ``_MAX_TEXT_CHARS`` 字符。
    """
    url = (url or "").strip()
    if not url:
        return "错误：请提供 URL"

    # 简单 URL 校验
    if not url.startswith(("http://", "https://")):
        return "错误：URL 必须以 http:// 或 https:// 开头"

    try:
        resp = requests.get(
            url,
            timeout=timeout,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                ),
            },
            stream=True,
        )
        resp.raise_for_status()
    except requests.RequestException as exc:
        return f"错误：获取页面失败（{exc}）"

    # 读取响应体（限制大小）
    try:
        content = b""
        for chunk in resp.iter_content(chunk_size=8192, decode_unicode=False):
            content += chunk
            if len(content) > _READ_BYTES:
                break
    except requests.RequestException as exc:
        return f"错误：读取页面内容失败（{exc}）"

    content_type = resp.headers.get("Content-Type", "")
    decoded = _try_decode(content, content_type)
    if decoded is None:
        return f"错误：无法解码页面内容"

    text = _extract_text(decoded)

    if len(text) > _MAX_TEXT_CHARS:
        text = text[:_MAX_TEXT_CHARS] + f"\n\n...（原文过长，仅显示前 {_MAX_TEXT_CHARS} 字符）"

    if not text.strip():
        return "（页面无可见文本内容）"

    return text


@tool
async def web_fetch(url: str, timeout: int = _DEFAULT_TIMEOUT) -> str:
    """获取指定 URL 页面的正文内容。

    适合阅读新闻、文档、博客等网页内容。
    自动提取正文纯文本，去除 HTML 标签和广告。
    大页面自动截断到 5000 字符。

    Args:
        url: 网页 URL，必须以 http:// 或 https:// 开头。
        timeout: 超时秒数，默认 15 秒。

    Returns:
        页面的纯文本正文内容。
    """
    import asyncio
    loop = asyncio.get_event_loop()
    try:
        result = await asyncio.wait_for(
            loop.run_in_executor(None, fetch_page, url, float(timeout)),
            timeout=timeout + 10,
        )
    except asyncio.TimeoutError:
        return f"错误：页面加载超时（{timeout}秒）"
    except Exception as exc:
        return f"错误：获取页面失败（{exc}）"

    return result
