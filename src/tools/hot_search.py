"""百度热搜榜抓取。

为什么是百度：
- 国内访问稳定（Yahoo News 在国内被墙 / 访问极慢）
- 服务端渲染的 HTML，不需要 JS
- HTML 结构 ``c-single-text-ellipsis`` 多年稳定
- 不需要 API key

返回 top_n 条当前热搜词条，**不接受 query 参数**（榜单是固定的）。
"""

from __future__ import annotations

import re
from typing import Final

import requests

BAIDU_HOT_URL: Final = "https://top.baidu.com/board?tab=realtime"
DEFAULT_TOP_N: Final = 20
DEFAULT_TIMEOUT: Final = 8

# 词条 selector：百度热搜页面的标题在 <div class="c-single-text-ellipsis"> 里
_TITLE_PATTERN: Final = re.compile(
    r'<div class="c-single-text-ellipsis">([^<]+)</div>'
)

USER_AGENT: Final = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


def fetch_hot_search(
    *,
    top_n: int = DEFAULT_TOP_N,
    timeout: float = DEFAULT_TIMEOUT,
    url: str = BAIDU_HOT_URL,
) -> str:
    """从百度热搜榜抓取实时热门词条。

    Args:
        top_n: 返回前 N 条，默认 20。
        timeout: 请求超时秒数，默认 8。
        url: 热搜页面 URL（测试时可注入 mock HTML）。

    Returns:
        格式化字符串，每行一个词条。
        抓取失败或解析不到词条时返回明确的错误信息（便于 agent/LLM 区分）。

    Notes:
        维护提示：百度热搜页面 HTML 结构变化时，``_TITLE_PATTERN`` 失配 → 返回空列表
        → 本函数返回 "页面结构变化" 错误。建议在监控告警里同时关注
        "返回 0 条热搜" 的频率。
    """
    try:
        resp = requests.get(
            url,
            headers={
                "User-Agent": USER_AGENT,
                "Accept-Language": "zh-CN,zh;q=0.9",
            },
            timeout=timeout,
        )
        resp.raise_for_status()
    except Exception as exc:
        return f"错误：抓取百度热搜失败（{exc}）"

    titles = [t.strip() for t in _TITLE_PATTERN.findall(resp.text)]
    titles = [t for t in titles if t][:top_n]

    if not titles:
        return "错误：百度热搜页面结构变化或返回空，未解析到词条"

    lines = [f"百度热搜 Top {len(titles)}：", ""]
    for i, t in enumerate(titles, 1):
        lines.append(f"{i}. {t}")
    return "\n".join(lines)
