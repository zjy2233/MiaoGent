"""网页抓取工具包。"""

from langchain_core.tools import tool


@tool
def fetch_page(url: str, selector: str = "body") -> str:
    """获取网页内容，支持 CSS 选择器提取特定部分。

    Args:
        url: 目标网页 URL。
        selector: CSS 选择器，默认 "body" 提取正文。

    Returns:
        网页提取的文本内容。
    """
    return f"（抓取 {url} 的选择器 {selector} 的内容）"


@tool
def list_links(url: str) -> str:
    """提取网页中的所有链接。

    Args:
        url: 目标网页 URL。

    Returns:
        页面中的链接列表。
    """
    return f"（提取 {url} 的链接列表）"


__tool_list__ = [fetch_page, list_links]
