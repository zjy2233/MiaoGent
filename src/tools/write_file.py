"""文件写入工具：新文件直接创建，覆盖已有文件需用户确认。

使用 LangGraph ``interrupt()`` 在覆盖前暂停 Graph 等待确认。
"""

from __future__ import annotations

from pathlib import Path

from langchain_core.tools import tool
from langgraph.types import interrupt

from src.tools.file_operations import _safe_path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


_TOOL_GUIDE = "优先使用 write_file 而非 shell 重定向（>）写文件。"


@tool
def write_file(path: str, content: str) -> str:
    """写入文件。新文件直接创建，覆盖已有文件需用户确认。

    Args:
        path: 文件路径（相对于项目根或绝对路径）。
        content: 要写入的文本内容。

    Returns:
        操作结果描述。
    """
    # 校验路径安全
    try:
        target = _safe_path(path)
    except ValueError as e:
        return f"错误：{e}"

    # 检查内容
    if not content:
        return "错误：内容为空，拒绝写入"

    # 检查文件是否存在 — 存在则需要确认
    if target.exists():
        if not target.is_file():
            return f"错误：路径已存在且不是文件 — {target}"
        try:
            rel = target.relative_to(_PROJECT_ROOT)
        except ValueError:
            rel = target
        try:
            approved = interrupt({
                "type": "write_confirm",
                "path": str(rel),
                "reason": f"文件已存在，确认覆盖？",
            })
        except (RuntimeError, KeyError):
            # 不在 Graph 上下文中
            return f"需要确认：文件 {rel} 已存在，是否覆盖？（确认后才能写入）"
        if not approved:
            return f"操作已取消：{rel}"

    # 创建父目录
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
    except PermissionError:
        return f"错误：无权限创建目录 — {target.parent}"
    except OSError as e:
        return f"错误：创建目录失败 — {e}"

    # 写入文件
    try:
        target.write_text(content, encoding="utf-8")
    except PermissionError:
        return f"错误：无权限写入文件 — {target}"
    except OSError as e:
        return f"错误：写入文件失败 — {e}"

    try:
        rel = target.relative_to(_PROJECT_ROOT)
    except ValueError:
        rel = target
    return f"已写入文件：{rel}（{len(content)} 字符）"
