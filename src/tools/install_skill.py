"""Skill 安装/卸载/浏览工具。

允许 Agent 从多种来源安装 Skill：
- **npm**：npm registry 下载（纯 HTTP，无需 Node.js）
- **pip**：PyPI 下载（纯 HTTP，无需 pip CLI）
- **url**：从 URL 下载 tar/zip 包
- **git**：从 git 仓库 clone

安装到 ``~/.miaogent/skills/<name>/`` 并自动注册。
不再维护硬编码的 skill 索引 — 安装时必须提供显式来源。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

__category__ = "system"

from langchain_core.tools import tool

from src.core.miaogent_home import get_miaogent_home
from src.core.skills_index import (
    _download_to_temp,
    _extract_skill_archive,
    _rmtree_force,
    _validate_skill_dir,
    install_git_skill,
    install_npm_skill,
    install_pip_skill,
)

logger = logging.getLogger(__name__)


def _skills_dir() -> Path:
    return get_miaogent_home() / "skills"


def _builtin_skills_dir() -> Path:
    """返回 src/skills/ 路径，内置 skill 所在地。"""
    return Path(__file__).resolve().parent.parent.parent / "src" / "skills"


def _manifest_path() -> Path:
    return _skills_dir() / ".miaogent-index.json"


def _load_manifest() -> dict[str, Any]:
    """读取已安装 Skill 的 manifest。"""
    path = _manifest_path()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _save_manifest(manifest: dict[str, Any]) -> None:
    """写入 manifest。"""
    path = _manifest_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _update_manifest(name: str, info: dict[str, Any]) -> None:
    """向 manifest 添加或更新一条记录。"""
    manifest = _load_manifest()
    manifest[name] = info
    _save_manifest(manifest)


def _remove_from_manifest(name: str) -> bool:
    """从 manifest 中移除一条记录。"""
    manifest = _load_manifest()
    if name in manifest:
        del manifest[name]
        _save_manifest(manifest)
        return True
    return False


def _skill_is_installed(name: str) -> bool:
    """检查 Skill 是否已安装（manifest 或文件系统）。"""
    manifest = _load_manifest()
    if name in manifest:
        return True
    skill_dir = _skills_dir() / name
    if not skill_dir.is_dir():
        return False
    if (skill_dir / "skill.md").exists():
        return True
    if (skill_dir / ".claude-plugin" / "plugin.json").exists():
        return True
    return False


def _list_builtin_skills() -> list[dict[str, str]]:
    """扫描 ``src/skills/`` 列出所有内置 Skill。"""
    skills: list[dict[str, str]] = []
    src = _builtin_skills_dir()
    if not src.is_dir():
        return skills
    for entry in sorted(src.iterdir()):
        if not entry.is_dir() or entry.name.startswith("_"):
            continue
        md = entry / "skill.md"
        if md.exists():
            skills.append({
                "name": entry.name,
                "description": _extract_description(md),
                "source": "built-in",
            })
    return skills


def _extract_description(md_path: Path) -> str:
    """从 skill.md frontmatter 提取 description。"""
    import re
    import yaml
    try:
        text = md_path.read_text(encoding="utf-8")
        m = re.match(r"^---\s*\n(.*?)\n---", text, re.DOTALL)
        if m:
            front = yaml.safe_load(m.group(1))
            if isinstance(front, dict):
                return str(front.get("description", ""))
    except Exception:
        pass
    return ""


def _download_and_extract(url: str, dest: Path) -> str:
    """从 URL 下载并解压 tar/zip 包到目标目录。"""
    suffix = ".tar.gz" if url.endswith((".tar.gz", ".tgz", ".tar")) else ".zip"
    tmp_path = _download_to_temp(url, suffix)
    if tmp_path is None:
        return f"错误：下载失败 — {url}"

    ok = _extract_skill_archive(tmp_path, dest)
    Path(tmp_path).unlink(missing_ok=True)

    if not ok:
        _rmtree_force(dest)
        return "错误：解压失败。"

    if not _validate_skill_dir(dest):
        _rmtree_force(dest)
        return (
            "错误：URL 内容不是有效的 Skill 包"
            "（未找到 skill.md 或 .claude-plugin/plugin.json）。\n"
            "下载的包已清理。"
        )

    return f"已从 URL 下载并安装到 {dest}"


def _name_from_url(url: str) -> str:
    """从 URL 推断 Skill 名称。"""
    path = urlparse(url).path.strip("/")
    name = Path(path).stem
    for suffix in (".tar", ".min", ".master"):
        if name.endswith(suffix):
            name = name[: -len(suffix)]
    return name or "downloaded_skill"


@tool(description="安装 Skill。支持 git: URL、npm:、pip: 前缀或自动检测来源。使用 list_registry 浏览可用 Skill。")
def install_skill(skill_name: str, source: str = "") -> str:
    """安装 Skill 到本地。

    支持多种来源，必须指定来源或使用显式前缀。
    安装后立即可在当前会话中使用。

    Args:
        skill_name: Skill 名称或来源标识。
            有效示例：
            - "git:https://github.com/obra/superpowers"（git 仓库）
            - "url:https://example.com/skill.tar.gz"（URL 下载）
            - "npm:@scope/pkg"（npm 包）
            - "pip:miaogent-skill-xxx"（pip 包）
            - "superpowers"（仅名称 — 仅限已安装或内置的 skill）
        source: 来源类型，空=自动检测。
            "git:仓库地址" = git clone
            "url:地址"     = URL 下载
            "npm:包名"    = npm registry
            "pip:包名"    = PyPI

    Returns:
        安装结果描述。
    """
    name = skill_name.strip()
    if not name:
        return "错误：请提供要安装的 Skill 名称。"

    # ── 检查是否为内置 skill（已在 src/skills/，无需安装）──
    builtin = _list_builtin_skills()
    builtin_names = {s["name"] for s in builtin}
    if name in builtin_names:
        return (
            f"'{name}' 是内置 Skill，已直接可用。\n"
            f"无需安装，可在 list_registry() 中查看。"
        )

    # ── 检查是否已安装 ──
    if _skill_is_installed(name):
        return (
            f"Skill '{name}' 已经安装。\n"
            f"如需重新安装，请先运行 uninstall_skill('{name}') 卸载。"
        )

    # ── 手动指定来源 ──
    src_lower = source.strip().lower()

    if src_lower.startswith("npm:") or src_lower == "npm":
        pkg = name if src_lower == "npm" else source[4:].strip()
        return install_npm_skill(pkg)

    if src_lower.startswith("pip:") or src_lower == "pip":
        pkg = name if src_lower == "pip" else source[4:].strip()
        return install_pip_skill(pkg)

    if src_lower.startswith("url:") or src_lower == "url":
        url = name if src_lower == "url" else source[4:].strip()
        skill_dir = _skills_dir() / _name_from_url(url)
        result = _download_and_extract(url, skill_dir)
        if "错误" not in result:
            _update_manifest(skill_dir.name, {
                "name": skill_dir.name,
                "description": f"从 {url} 安装",
                "version": "0.0.0",
                "source": "url",
                "installed_at": __import__("time").time(),
            })
        return result

    if src_lower.startswith("git:") or src_lower == "git":
        url = name if src_lower == "git" else source[4:].strip()
        name_safe = _name_from_url(url)
        result = install_git_skill(url, name_safe)
        if "错误" not in result:
            _update_manifest(name_safe, {
                "name": name_safe,
                "description": f"从 git 仓库 {url} 安装",
                "version": "0.0.0",
                "source": "git",
                "installed_at": __import__("time").time(),
            })
        return result

    # ── 自动检测（无 source 时） ──

    # 1) npm:@scope/name 格式
    if name.startswith("npm:") or name.startswith("@"):
        pkg = name[4:] if name.startswith("npm:") else name
        return install_npm_skill(pkg)

    # 2) pip:package 格式
    if name.startswith("pip:"):
        return install_pip_skill(name[4:].strip())

    # 3) git: 显式前缀
    if name.startswith("git:"):
        url = name[4:]
        name_safe = _name_from_url(url)
        result = install_git_skill(url, name_safe)
        if "错误" not in result:
            _update_manifest(name_safe, {
                "name": name_safe,
                "description": f"从 git 仓库 {url} 安装",
                "version": "0.0.0",
                "source": "git",
                "installed_at": __import__("time").time(),
            })
        return result

    # 4) git 仓库格式（git@ 开头或 .git 后缀）
    if name.startswith("git@") or name.startswith("git+") or name.endswith(".git"):
        url = name[4:] if name.startswith("git+") else name
        return install_git_skill(url, _name_from_url(url))

    # 5) url: 显式前缀
    if name.startswith("url:"):
        url = name[4:]
        skill_dir = _skills_dir() / _name_from_url(url)
        result = _download_and_extract(url, skill_dir)
        if "错误" not in result:
            _update_manifest(skill_dir.name, {
                "name": skill_dir.name,
                "description": f"从 {url} 安装",
                "version": "0.0.0",
                "source": "url",
                "installed_at": __import__("time").time(),
            })
        return result

    # 6) URL 格式（http/https）
    parsed = urlparse(name)
    if parsed.scheme in ("http", "https"):
        skill_dir = _skills_dir() / _name_from_url(name)
        result = _download_and_extract(name, skill_dir)
        if "错误" not in result:
            _update_manifest(skill_dir.name, {
                "name": skill_dir.name,
                "description": f"从 {name} 安装",
                "version": "0.0.0",
                "source": "url",
                "installed_at": __import__("time").time(),
            })
        return result

    # ── 找不到来源 ──
    hint = ""
    for s in builtin_names:
        if name in s or s in name:
            hint = f"\n您是不是要找内置 Skill：{s}？（无需安装，直接可用）"
            break

    return (
        f"错误：未找到 Skill '{name}'。\n"
        f"内置 Skill：{', '.join(sorted(builtin_names)) if builtin_names else '（无）'}\n"
        f"提示：请提供完整来源，如 install_skill('git:https://...'){hint}"
    )


@tool(description="卸载已安装的第三方 Skill。内置 Skill 无法卸载。")
def uninstall_skill(skill_name: str) -> str:
    """卸载一个已安装的 Skill。

    从 ``~/.miaogent/skills/`` 中删除 Skill 文件并注销注册。
    内置 Skill（位于 ``src/skills/``）无法卸载。

    Args:
        skill_name: 要卸载的 Skill 名称。

    Returns:
        卸载结果描述。
    """
    name = skill_name.strip().lower()
    if not name:
        return "错误：请提供要卸载的 Skill 名称。"

    # 检查是否为内置 skill
    builtin_names = {s["name"] for s in _list_builtin_skills()}
    if name in builtin_names:
        return f"错误：'{name}' 是内置 Skill，无法卸载。只支持卸载从市场安装的 Skill。"

    if not _skill_is_installed(name):
        return f"错误：Skill '{name}' 未安装。使用 list_registry() 查看已安装的 Skill。"

    # 删除 skill 目录
    skill_dir = _skills_dir() / name
    if skill_dir.exists():
        _rmtree_force(skill_dir)
        info = f"已删除目录：{skill_dir}"
    else:
        info = "（目录已不存在）"

    # 更新 manifest
    _remove_from_manifest(name)

    return f"已卸载 Skill '{name}'。{info}"


@tool(description="列出所有可用 Skill（内置 + 已安装的第三方）。")
def list_registry() -> str:
    """浏览可用的 Skill。

    显示内置 Skill（位于 ``src/skills/``）和
    已安装的第三方 Skill（位于 ``~/.miaogent/skills/``）。

    Returns:
        格式化后的可用 Skill 列表。
    """
    builtin = _list_builtin_skills()
    installed = _load_manifest()

    lines: list[str] = []

    if builtin:
        lines.append(f"📦 内置 Skill（共 {len(builtin)} 个）：\n")
        for s in builtin:
            lines.append(f"  - **{s['name']}**")
            if s.get("description"):
                lines.append(f"    描述：{s['description']}")
        lines.append("")

    if installed:
        lines.append(f"📦 已安装 Skill（共 {len(installed)} 个）：\n")
        for name, info in sorted(installed.items()):
            desc = info.get("description", "无描述")
            src = info.get("source", "unknown")
            lines.append(f"  - **{name}** [{src}]")
            lines.append(f"    描述：{desc}")
    else:
        lines.append("（暂无已安装的第三方 Skill）\n")

    lines.append(
        "使用 install_skill('git:https://...') 或 install_skill('url:https://...') 安装。"
    )
    return "\n".join(lines)


# ── _TOOL_GUIDE 供 builder 自动收集 ──
_TOOL_GUIDE = (
    "安装技能时必须提供来源，如 install_skill('git:https://github.com/obra/superpowers')。"
    "使用 list_registry 浏览内置和已安装的技能，使用 uninstall_skill 卸载。"
    "内置 skill 位于 src/skills/ 下，无需安装即可使用。"
)
