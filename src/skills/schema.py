"""Skill 数据模型。

定义 Skill 的运行时表示：名称、描述、提示注入。
采用 Claude Code Plugin 标准格式：
- ``.claude-plugin/plugin.json`` — 插件清单
- ``skills/<name>/SKILL.md`` — 技能定义（YAML frontmatter + markdown）
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class PluginManifest:
    """.claude-plugin/plugin.json 的运行时表示。"""

    name: str = ""
    description: str = ""
    version: str = "0.0.0"
    author: dict[str, str] | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PluginManifest":
        return cls(
            name=data.get("name", ""),
            description=data.get("description", ""),
            version=data.get("version", "0.0.0"),
            author=data.get("author"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "version": self.version,
            "author": self.author,
        }


@dataclass
class SkillDefinition:
    """Skill 的运行时表示。

    采用 Claude Code SKILL.md 格式，技能为纯提示注入，
    不定义自定义工具。MiaoGent 的内置工具（shell、run_python、search 等）
    提供执行能力，skill 通过提示词指导 agent 如何组合使用它们。

    Attributes:
        name: 全局唯一标识符。
        description: LLM 可读的描述（用于意图匹配）。
        prompt_injection: 注入系统提示的文本（SKILL.md body）。
        allowed_tools: 推荐的 MiaoGent 工具名列表（可选，来自 SKILL.md frontmatter）。
    """

    name: str
    description: str
    prompt_injection: str = ""
    allowed_tools: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """序列化为可 JSON 序列化的字典。"""
        return {
            "name": self.name,
            "description": self.description,
            "prompt_injection": self.prompt_injection,
            "has_prompt_injection": bool(self.prompt_injection.strip()),
            "allowed_tools": self.allowed_tools,
        }
