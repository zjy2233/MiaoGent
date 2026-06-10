"""纯 Python 文件操作原子工具（不依赖 shell）。

工具：
    list_files   — 列出目录内容（替代 ls / dir）
    read_file    — 读取文件内容（替代 cat / type）
    grep_search  — 搜索文件内容（替代 grep / findstr）

安全：所有工具共享 _safe_path() 校验，拦截 .. 路径穿越。
"""

from __future__ import annotations

import fnmatch
import os
import re
from pathlib import Path

from langchain_core.tools import tool

__category__ = "file_system"

# ── 路径安全 ────────────────────────────────────────────────────

# 项目根目录（src/tools/file_operations.py → 上三层到项目根）
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

# 系统关键路径前缀（始终禁止访问）
_SYSTEM_BLOCKED_PREFIXES: tuple[str, ...] = ()
if os.name == "nt":
    _SYSTEM_BLOCKED_PREFIXES = (
        os.environ.get("SystemRoot", "C:\\Windows").lower(),
        "C:\\Windows\\System32".lower(),
        "C:\\Program Files".lower(),
        "C:\\Program Files (x86)".lower(),
    )
else:
    _SYSTEM_BLOCKED_PREFIXES = (
        "/etc", "/usr", "/bin", "/sbin", "/lib", "/boot", "/dev", "/proc", "/sys",
    )


def _safe_path(path: str | Path) -> Path:
    """校验并解析为安全绝对路径。

    规则：
    - 阻止 ``..`` 路径穿越
    - 阻止访问系统关键目录（Windows、/etc 等）
    - 相对路径基于项目根解析，绝对路径直接使用
    - 允许访问项目外用户目录（Desktop、Documents 等）

    Raises:
        ValueError: 路径穿越或系统目录访问被拒绝。
    """
    p = Path(path)

    # 相对路径基于项目根解析
    if not p.is_absolute():
        p = _PROJECT_ROOT / p

    # 标准化
    try:
        p = p.resolve(strict=False)
    except (OSError, RuntimeError):
        p = p.absolute()

    # 检查路径穿越：标准化后不能包含 ..（resolve 会展开 ..）
    # 但 resolve 前检查原始路径中是否有 .. 隔离
    if ".." in path.split(os.sep) or ".." in path.split("/"):
        raise ValueError(f"路径访问被拒绝：{path} — 不允许路径穿越（..）")

    # 检查系统关键路径
    p_lower = str(p).lower()
    for blocked in _SYSTEM_BLOCKED_PREFIXES:
        if p_lower.startswith(blocked):
            raise ValueError(f"路径访问被拒绝：{path} — 系统目录禁止访问")

    return p


# ── 工具 ────────────────────────────────────────────────────────


_TOOL_GUIDE = "优先使用专用工具而非 shell：list_files 代替 dir/ls，read_file 代替 cat/type，grep_search 代替 grep/findstr，create_folder 代替 mkdir。"


@tool(description="列出目录内容。支持 glob 过滤。替代 dir/ls。")
def list_files(path: str = ".", pattern: str | None = None) -> str:
    """列出指定目录的文件和子目录。

    Args:
        path: 目录路径，默认为项目根目录。
        pattern: 可选 glob 过滤模式，如 ``"*.py"``、``"*.{txt,md}"``。

    Returns:
        目录内容的文本描述，包含目录/文件列表及文件大小。
    """
    try:
        target = _safe_path(path)
    except ValueError as e:
        return f"错误：{e}"

    if not target.exists():
        return f"错误：目录不存在 — {target}"
    if not target.is_dir():
        return f"错误：路径不是目录 — {target}"

    try:
        entries = list(target.iterdir())
    except PermissionError:
        return f"错误：无权限访问目录 — {target}"
    except OSError as e:
        return f"错误：读取目录失败 — {e}"

    if pattern:
        entries = [e for e in entries if e.match(pattern)]

    if not entries:
        return f"（目录 {target} 为空{'，过滤模式：' + pattern if pattern else ''}）"

    dirs = [e for e in entries if e.is_dir()]
    files = [e for e in entries if e.is_file()]
    others = [e for e in entries if not e.is_dir() and not e.is_file()]

    lines = [f"目录：{target}"]
    if pattern:
        lines.append(f"过滤：{pattern}")
    lines.append("")

    if dirs:
        lines.append("📁 子目录：")
        for d in sorted(dirs):
            lines.append(f"  {d.name}/")
        lines.append("")

    if files:
        lines.append("📄 文件：")
        for f in sorted(files):
            try:
                size = f.stat().st_size
                size_str = _format_size(size)
                lines.append(f"  {f.name:<30} {size_str:>8}")
            except OSError:
                lines.append(f"  {f.name:<30} {'?':>8}")
        lines.append("")

    if others:
        lines.append("其他：")
        for o in sorted(others):
            lines.append(f"  {o.name}")
        lines.append("")

    lines.append(f"共 {len(dirs)} 个目录，{len(files)} 个文件")
    if others:
        lines.append(f"，{len(others)} 个其他")

    return "\n".join(lines)


@tool(description="读取文本文件内容。支持行范围、自动编码检测（UTF-8/GBK）。")
def read_file(path: str, offset: int = 0, limit: int = 200) -> str:
    """读取文本文件内容，支持行范围。

    自动检测编码（UTF-8 → GBK）。
    二进制文件（含 null 字节）会检测并跳过。

    Args:
        path: 文件路径（相对于项目根或绝对路径）。
        offset: 起始行号（0-based），默认 0。
        limit: 最多读取行数，默认 200。

    Returns:
        文件内容。文件过大时自动截断并提示。
    """
    try:
        target = _safe_path(path)
    except ValueError as e:
        return f"错误：{e}"

    if not target.exists():
        return f"错误：文件不存在 — {target}"
    if not target.is_file():
        return f"错误：路径不是文件 — {target}"

    # 尝试读取
    raw_bytes = None
    try:
        raw_bytes = target.read_bytes()
    except PermissionError:
        return f"错误：无权限读取文件 — {target}"
    except OSError as e:
        return f"错误：读取文件失败 — {e}"

    # 检测二进制（含 null 字节）
    if b"\x00" in raw_bytes[:8192]:
        size = _format_size(len(raw_bytes))
        return f"（二进制文件，{size}，跳过内容显示）"

    # 解码
    text = _try_decode(raw_bytes)
    if text is None:
        return f"错误：无法解码文件（不是 UTF-8 也不是 GBK）"

    lines = text.splitlines()
    total = len(lines)

    if offset < 0:
        offset = 0
    if offset >= total:
        return f"（文件共 {total} 行，起始行 {offset} 超出范围）"

    end = min(offset + limit, total)
    selected = lines[offset:end]

    result = []
    if offset > 0 or limit < total:
        result.append(f"文件：{target}（共 {total} 行，显示 {offset}-{end} 行）")
    else:
        result.append(f"文件：{target}（共 {total} 行）")
    result.append("")

    for i, line in enumerate(selected, offset + 1):
        result.append(f"{i:>4}| {line}")

    if end < total:
        result.append(f"\n...（仅显示前 {limit} 行，共 {total} 行，用 offset= 参数查看更多）")

    return "\n".join(result)


@tool(description="在文件中搜索正则表达式模式。支持 glob 过滤，最多 50 结果。")
def grep_search(pattern: str, path: str = ".", include: str | None = None) -> str:
    """在文件中搜索文本模式（纯 Python 实现，无需 grep 命令）。

    跳过二进制文件和 1MB 以上的大文件。

    Args:
        pattern: 要搜索的正则表达式（Python re 语法）。
        path: 搜索目录，默认为项目根。
        include: 可选 glob 过滤，如 ``"*.py"`` 只搜索 Python 文件。

    Returns:
        匹配行的列表（文件:行号:内容），最多返回 50 个结果。
    """
    try:
        root = _safe_path(path)
    except ValueError as e:
        return f"错误：{e}"

    if not root.exists():
        return f"错误：目录不存在 — {root}"
    if not root.is_dir():
        return f"错误：路径不是目录 — {root}"

    try:
        compiled = re.compile(pattern)
    except re.error as e:
        return f"错误：正则表达式无效 — {e}"

    MAX_RESULTS = 50
    MAX_FILE_SIZE = 1 * 1024 * 1024  # 1MB
    results: list[str] = []
    searched = 0
    skipped_binary = 0
    skipped_size = 0

    for dirpath, dirnames, filenames in os.walk(root):
        # 跳过 .git、__pycache__、.venv、node_modules
        dirnames[:] = [d for d in dirnames
                       if d not in (".git", "__pycache__", ".venv", "node_modules", ".mypy_cache", ".pytest_cache")]

        for fname in filenames:
            if include and not fnmatch.fnmatch(fname, include):
                continue

            fpath = os.path.join(dirpath, fname)

            # 跳过大文件
            try:
                if os.path.getsize(fpath) > MAX_FILE_SIZE:
                    skipped_size += 1
                    continue
            except OSError:
                continue

            # 读取文件
            try:
                with open(fpath, "rb") as f:
                    head = f.read(8192)
                    f.seek(0)
                    # 跳过二进制
                    if b"\x00" in head:
                        skipped_binary += 1
                        continue
                    # 解码
                    content = _try_decode(head)
                    if content is None:
                        skipped_binary += 1
                        continue
                    # 读完整文件
                    rest = f.read()
                    rest_decoded = _try_decode(rest)
                    if rest_decoded is not None:
                        content += rest_decoded
            except (PermissionError, OSError):
                continue

            searched += 1
            relpath = os.path.relpath(fpath, root)
            for lineno, line in enumerate(content.splitlines(), 1):
                if compiled.search(line):
                    display = line.strip()[:120]
                    results.append(f"{relpath}:{lineno}: {display}")
                    if len(results) >= MAX_RESULTS:
                        break
            if len(results) >= MAX_RESULTS:
                break
        if len(results) >= MAX_RESULTS:
            break

    if not results:
        summary = f'（未找到匹配 "{pattern}"）'
        if include:
            summary += f" 在 {include} 文件中"
        summary += f"，已搜索 {searched} 个文件）"
        if skipped_binary:
            summary += f" 跳过 {skipped_binary} 个二进制文件"
        if skipped_size:
            summary += f" 跳过 {skipped_size} 个大文件"
        return summary

    result_text = "\n".join(results)
    summary = f"找到 {len(results)} 个匹配" + (f"（显示前 {MAX_RESULTS} 个）" if len(results) >= MAX_RESULTS else "")
    if include:
        summary += f" 在 {include} 文件中"
    summary += f"\n{result_text}"
    if skipped_binary:
        summary += f"\n（跳过 {skipped_binary} 个二进制文件）"

    return summary


@tool(description="创建文件夹。支持多级目录，已存在时不报错。")
def create_folder(path: str) -> str:
    """创建文件夹（目录）。支持创建多级目录，已存在时不会报错。

    Args:
        path: 文件夹路径（相对于项目根或绝对路径）。

    Returns:
        操作结果描述。
    """
    try:
        target = _safe_path(path)
    except ValueError as e:
        return f"错误：{e}"

    if target.exists():
        if target.is_dir():
            return f"（文件夹已存在：{target}）"
        return f"错误：路径已存在且不是文件夹 — {target}"

    try:
        target.mkdir(parents=True, exist_ok=True)
    except PermissionError:
        return f"错误：无权限创建文件夹 — {target}"
    except OSError as e:
        return f"错误：创建文件夹失败 — {e}"

    try:
        rel = target.relative_to(_PROJECT_ROOT)
    except ValueError:
        rel = target
    return f"已创建文件夹：{rel}"


# ── 辅助函数 ────────────────────────────────────────────────────


def _format_size(n: int) -> str:
    """人类可读的文件大小。"""
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f}{unit}" if unit == "B" else f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}TB"


def _try_decode(data: bytes) -> str | None:
    """尝试 UTF-8 → GBK 解码，均失败时返回 None。"""
    for enc in ("utf-8", "gbk"):
        try:
            return data.decode(enc)
        except (UnicodeDecodeError, UnicodeError):
            continue
    return None
