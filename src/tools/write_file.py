"""文件写入工具：新文件直接创建，覆盖已有文件需用户确认。

使用 LangGraph ``interrupt()`` 在覆盖前暂停 Graph 等待确认。
临时脚本（temp=True）自动写入 ``~/.miaogent/temp/``，不占用项目目录。
"""

from __future__ import annotations

import time
from pathlib import Path

from langchain_core.tools import tool
from langgraph.types import interrupt

from src.core.miaogent_home import get_temp_dir
from src.tools.file_operations import _safe_path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


_TOOL_GUIDE = "优先使用 write_file 而非 shell 重定向（>）写文件。生成临时脚本时务必设置 temp=True。"


@tool
def write_file(path: str, content: str, temp: bool = False) -> str:
    """写入文件。新文件直接创建，覆盖已有文件需用户确认。

    生成临时脚本（数据分析、调试、一次性任务）时务必设置 temp=True，
    文件将自动写入 ``~/.miaogent/temp/`` 目录，避免污染项目工作目录。

    Args:
        path: 文件路径（相对于项目根或绝对路径）。temp=True 时仅为文件名。
        content: 要写入的文本内容。
        temp: 是否为临时脚本。True 时写入 ~/.miaogent/temp/，不会覆盖项目文件。

    Returns:
        操作结果描述。
    """
    # 检查内容
    if not content:
        return "错误：内容为空，拒绝写入"

    # ── 临时文件模式：写入 ~/.miaogent/temp/ ──
    if temp:
        temp_dir = get_temp_dir()
        # 时间戳前缀防冲突，同时保留原始文件名便于识别
        stem = Path(path).stem
        suffix = Path(path).suffix or ".py"
        timestamp = int(time.time() * 1000)
        target = temp_dir / f"{stem}_{timestamp}{suffix}"
        # 直接写入，无需 interrupt（临时文件不存在覆盖问题）
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        except PermissionError:
            return f"错误：无权限写入临时目录 — {target}"
        except OSError as e:
            return f"错误：写入临时文件失败 — {e}"
        return f"已写入临时文件：{target.name}（{len(content)} 字符，目录：{temp_dir}）"

    # ── 项目文件模式：原逻辑 ──
    # 校验路径安全
    try:
        target = _safe_path(path)
    except ValueError as e:
        return f"错误：{e}"

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
