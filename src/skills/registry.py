"""SkillRegistry — Skill 注册中心。

负责扫描 ``src/skills/`` 和 ``~/.miaogent/skills/`` 下的目录，
支持两种格式：

1. **Claude Code 插件格式**（标准）：
   ``.claude-plugin/plugin.json`` + ``skills/<name>/SKILL.md``

2. **MiaoGent 旧格式**（兼容）：
   ``skill.md``（根目录，YAML frontmatter + body）

每次 ``discover()`` 全量重建，不缓存，保证文件系统增删即时反映。

Skill 为纯提示注入，不定义自定义工具。
MiaoGent 的内置工具（shell、run_python、search 等）提供执行能力。
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

import yaml

from src.core.miaogent_home import get_miaogent_home
from src.skills.schema import PluginManifest, SkillDefinition

logger = logging.getLogger(__name__)


def _parse_skill_md(path: Path) -> dict[str, Any] | None:
    """解析旧格式 skill.md，返回 frontmatter 字典 + body。

    兼容 MiaoGent 旧格式（根目录 skill.md）。
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        logger.warning("Failed to read %s: %s", path, exc)
        return None

    m = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)", text, re.DOTALL)
    if not m:
        logger.warning("Missing frontmatter in %s", path)
        return None

    front_raw, body = m.group(1), m.group(2).strip()
    try:
        front = yaml.safe_load(front_raw)
    except yaml.YAMLError as exc:
        logger.warning("Invalid frontmatter YAML in %s: %s", path, exc)
        return None

    if not isinstance(front, dict):
        logger.warning("Frontmatter in %s is not a dict", path)
        return None

    front["prompt_injection"] = body
    return front


def _parse_skill_md_standard(path: Path) -> dict[str, Any] | None:
    """解析标准 SKILL.md（Claude Code 格式），返回 frontmatter 字典 + body。

    YAML frontmatter 支持字段：
    - name（必填）
    - description（必填）
    - version（可选）
    - allowed-tools（可选，列表）
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        logger.warning("Failed to read %s: %s", path, exc)
        return None

    m = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)", text, re.DOTALL)
    if not m:
        # 没有 frontmatter 也算合法，用目录名当 name
        return {
            "name": path.parent.name,
            "description": "",
            "prompt_injection": text.strip(),
            "allowed_tools": [],
        }

    front_raw, body = m.group(1), m.group(2).strip()
    try:
        front = yaml.safe_load(front_raw)
    except yaml.YAMLError as exc:
        logger.warning("Invalid YAML in %s: %s", path, exc)
        return None

    if not isinstance(front, dict):
        logger.warning("Frontmatter in %s is not a dict", path)
        return None

    front["prompt_injection"] = body
    # 转写 allowed-tools → allowed_tools
    if "allowed-tools" in front and "allowed_tools" not in front:
        front["allowed_tools"] = front.pop("allowed-tools")
    return front


class SkillRegistry:
    """Skill 注册中心。

    每次 ``discover()`` 全量重建，反映当前文件系统状态。
    不缓存已删除的 Skill。

    扫描以下目录（按优先级，同名以后扫描的为准）：
    1. ``skills_dir`` 参数指定的目录（默认 ``src/skills``，内置）
    2. ``~/.miaogent/skills/``（用户安装的第三方 Skill）

    支持格式：
    - ``.claude-plugin/plugin.json`` + ``skills/<name>/SKILL.md``
    - ``skill.md``（旧格式兼容）
    """

    def __init__(
        self,
        skills_dir: str | Path = "src/skills",
        user_skills_dir: str | Path | None = None,
    ) -> None:
        self._skills_dir = Path(skills_dir).resolve()
        self._user_skills_dir = (
            Path(user_skills_dir) if user_skills_dir is not None
            else get_miaogent_home() / "skills"
        )
        self._skills: dict[str, SkillDefinition] = {}

    # ── 发现与加载 ──────────────────────────────────────────────────────

    def discover(self) -> dict[str, SkillDefinition]:
        """全量扫描所有目录，每次重建，不缓存。

        扫描顺序：用户目录（低优先级）→ 内置目录（高优先级，同名覆盖）。
        每次清空 ``self._skills`` 保证增删即时反映。

        Returns:
            ``{name: SkillDefinition}`` 字典。
        """
        self._skills = {}  # ← 全量重建，不缓存
        self._scan_dir(self._user_skills_dir)
        self._scan_dir(self._skills_dir)
        return dict(self._skills)

    def _scan_dir(self, scan_path: Path) -> None:
        """扫描单个目录下的所有 Skill。"""
        if not scan_path.is_dir():
            logger.debug("Skills dir %s does not exist", scan_path)
            return

        for entry in sorted(scan_path.iterdir()):
            if not entry.is_dir():
                continue
            if entry.name.startswith("_"):
                continue

            # 1. 检查 Claude Code 插件格式
            plugin_json = entry / ".claude-plugin" / "plugin.json"
            if plugin_json.exists():
                self._scan_plugin_skills(entry)
                continue

            # 2. 检查旧格式 skill.md
            md_path = entry / "skill.md"
            if md_path.exists():
                definition = self._load_skill_legacy(entry, md_path)
                if definition is not None:
                    self._skills[definition.name] = definition
                continue

            # 3. 检查子目录中是否有 SKILL.md（展平的 skill 目录）
            for sub in entry.iterdir():
                if not sub.is_dir() or sub.name.startswith("_"):
                    continue
                skill_md = sub / "SKILL.md"
                if not skill_md.exists():
                    skill_md = sub / "skill.md"
                if skill_md.exists():
                    definition = self._load_skill_standard(sub, skill_md)
                    if definition is not None:
                        self._skills[definition.name] = definition

    def _scan_plugin_skills(self, plugin_dir: Path) -> None:
        """扫描 Claude Code 插件目录下的所有 skill。

        结构：plugin_dir/skills/<name>/SKILL.md
        """
        manifest_path = plugin_dir / ".claude-plugin" / "plugin.json"
        try:
            manifest_data = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest = PluginManifest.from_dict(manifest_data)
        except Exception as exc:
            logger.debug("Invalid plugin.json in %s: %s", plugin_dir, exc)
            manifest = PluginManifest(name=plugin_dir.name)

        skills_root = plugin_dir / "skills"
        if not skills_root.is_dir():
            return

        for skill_dir in sorted(skills_root.iterdir()):
            if not skill_dir.is_dir():
                continue
            if skill_dir.name.startswith("_"):
                continue

            skill_md = skill_dir / "SKILL.md"
            if not skill_md.exists():
                skill_md = skill_dir / "skill.md"
            if not skill_md.exists():
                continue

            definition = self._load_skill_standard(skill_dir, skill_md)
            if definition is not None:
                # 插件名作为前缀避免冲突
                prefixed_name = f"{manifest.name}:{definition.name}"
                definition.name = prefixed_name
                self._skills[prefixed_name] = definition

    def _load_skill_legacy(
        self, skill_dir: Path, md_path: Path
    ) -> SkillDefinition | None:
        """从旧格式 skill.md 加载 Skill。"""
        raw = _parse_skill_md(md_path)
        if raw is None:
            return None
        return SkillDefinition(
            name=raw.get("name") or skill_dir.name,
            description=str(raw.get("description", "")),
            prompt_injection=str(raw.get("prompt_injection", "")),
        )

    def _load_skill_standard(
        self, skill_dir: Path, md_path: Path
    ) -> SkillDefinition | None:
        """从标准 SKILL.md 加载 Skill。"""
        raw = _parse_skill_md_standard(md_path)
        if raw is None:
            return None
        return SkillDefinition(
            name=raw.get("name") or skill_dir.name,
            description=str(raw.get("description", "")),
            prompt_injection=str(raw.get("prompt_injection", "")),
            allowed_tools=raw.get("allowed_tools", []),
        )

    def get_plugin_manifest(self, plugin_dir: Path) -> PluginManifest | None:
        """读取插件目录的 plugin.json。"""
        manifest_path = plugin_dir / ".claude-plugin" / "plugin.json"
        if not manifest_path.exists():
            return None
        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
            return PluginManifest.from_dict(data)
        except Exception as exc:
            logger.warning("Failed to read plugin.json: %s", exc)
            return None

    # ── 查询 ────────────────────────────────────────────────────────────

    def get(self, name: str) -> SkillDefinition | None:
        """按名称获取 Skill。"""
        return self._skills.get(name)

    def list_all(self) -> list[SkillDefinition]:
        """返回所有已注册的 Skill 定义列表。"""
        return list(self._skills.values())

    def names(self) -> list[str]:
        """返回所有已注册的 Skill 名称列表（已排序）。"""
        return sorted(self._skills.keys())

    def __repr__(self) -> str:
        return f"SkillRegistry(skills={len(self._skills)})"
