"""pywebview 包装器：暴露 Api 类给前端 JS，并通过 ``webview.api`` 访问。

模块布局说明：
- 业务方法（``get_sessions`` / ``save_settings`` 等）都是无状态、无副作用 I/O 包装，
  不缓存磁盘数据，每次调用都重新读取最新值，方便前端实时刷新。
- ``get_tools`` 用 :mod:`ast` 解析 ``src/tools/*.py`` 的源代码，识别 ``@tool``
  装饰器，提取 ``name`` 和 docstring；不依赖 ``import`` 工具模块（避免副作用）。
- 路径均基于 ``root_dir``（默认项目根），便于测试用 ``tmp_path`` 注入。

注意：和 ``src/agent_shell/`` 目录（前端资源）共存——Python 优先解析
``.py`` 文件，不影响包结构。
"""

from __future__ import annotations

import ast
import os
from dataclasses import MISSING, fields
from pathlib import Path
from typing import Any

from src.config import Settings
from src.sessions import SessionRegistry
from src.soul import ProfileManager, SoulManager


# ── Settings 字段 → 环境变量名 映射 ────────────────────────────────────
# Settings dataclass 字段名全是大写转换的（``deepseek_api_key`` → ``DEEPSEEK_API_KEY``），
# 但 list / bool 字段序列化方式不同，这里集中维护避免散落。
_SETTINGS_KEY_TO_ENV: dict[str, str] = {
    "deepseek_api_key": "DEEPSEEK_API_KEY",
    "deepseek_base_url": "DEEPSEEK_BASE_URL",
    "deepseek_model": "DEEPSEEK_MODEL",
    "request_timeout": "REQUEST_TIMEOUT",
    "shell_auto_confirm": "SHELL_AUTO_CONFIRM",
    "shell_high_risk_block": "SHELL_HIGH_RISK_BLOCK",
    "shell_allowed_patterns": "SHELL_ALLOWED_PATTERNS",
    "shell_blocked_patterns": "SHELL_BLOCKED_PATTERNS",
    "db_path": "DB_PATH",
    "max_turns": "MAX_TURNS",
    "max_message_chars": "MAX_MESSAGE_CHARS",
    "compression_model": "COMPRESSION_MODEL",
}

# 字段类型提示（决定如何从字符串反序列化）
_BOOL_KEYS: frozenset[str] = frozenset({"shell_auto_confirm", "shell_high_risk_block"})
_INT_KEYS: frozenset[str] = frozenset({"max_turns", "max_message_chars"})
_FLOAT_KEYS: frozenset[str] = frozenset({"request_timeout"})
_LIST_KEYS: frozenset[str] = frozenset({"shell_allowed_patterns", "shell_blocked_patterns"})

# Settings dataclass 中有 ``default`` 的字段（不需要手动维护）
_DATACLASS_DEFAULTS: dict[str, Any] = {
    f.name: f.default
    for f in fields(Settings)
    if f.default is not MISSING
}
# 兜底：dataclass 中没显式 default 但 ``from_env()`` 里硬编码了默认值的字段
_EXTRA_DEFAULTS: dict[str, Any] = {
    "deepseek_api_key": "",
    "deepseek_base_url": "https://api.deepseek.com",
    "deepseek_model": "deepseek-chat",
}


def _get_default(key: str) -> Any:
    """单个 key 的默认值：优先 ``_EXTRA_DEFAULTS``，再 ``_DATACLASS_DEFAULTS``。"""
    if key in _EXTRA_DEFAULTS:
        return _EXTRA_DEFAULTS[key]
    return _DATACLASS_DEFAULTS.get(key)


def _project_root() -> Path:
    """推断项目根目录：``src/agent_shell.py`` 的父级的父级。"""
    return Path(__file__).resolve().parent.parent


# ── 工具枚举（AST 解析）────────────────────────────────────────────────


def _is_tool_decorator(decorator: ast.expr) -> bool:
    """识别 ``@tool``、``@tool()``、``@module.tool`` 三种写法。"""
    if isinstance(decorator, ast.Name) and decorator.id == "tool":
        return True
    if isinstance(decorator, ast.Attribute) and decorator.attr == "tool":
        return True
    if isinstance(decorator, ast.Call):
        func = decorator.func
        if isinstance(func, ast.Name) and func.id == "tool":
            return True
        if isinstance(func, ast.Attribute) and func.attr == "tool":
            return True
    return False


def _parse_tool_files(tools_dir: Path) -> list[dict[str, str]]:
    """扫描 ``tools_dir`` 下所有 ``.py``，返回 ``@tool`` 装饰函数的元数据列表。

    鲁棒性：
    - 目录不存在 → 返回 ``[]``
    - 语法错误的文件 → 跳过（不抛错）
    - 不依赖 ``import``，所以不需要安装工具依赖
    """
    if not tools_dir.is_dir():
        return []

    results: list[dict[str, str]] = []
    for py_file in sorted(tools_dir.glob("*.py")):
        if py_file.name == "__init__.py":
            # __init__.py 通常是 re-export，不重复计数
            continue
        try:
            source = py_file.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(py_file))
        except (OSError, SyntaxError):
            # 解析失败的文件跳过（避免一个坏文件搞挂整个 API）
            continue

        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if not any(_is_tool_decorator(d) for d in node.decorator_list):
                continue
            doc = ast.get_docstring(node) or ""
            results.append(
                {
                    "name": node.name,
                    "description": doc.strip(),
                    "file": str(py_file),
                }
            )
    return results


# ── Api 类 ─────────────────────────────────────────────────────────────


class Api:
    """pywebview 后端桥接层：所有方法都暴露给前端 ``window.pywebview.api``。"""

    def __init__(self, root_dir: Path | str | None = None) -> None:
        self.root_dir = Path(root_dir) if root_dir else _project_root()
        self._env_path = self.root_dir / ".env"
        self._sessions_path = self.root_dir / ".sessions.json"
        self._soul_path = self.root_dir / "soul.json"
        self._profile_path = self.root_dir / "profile.json"
        self._tools_dir = self.root_dir / "src" / "tools"

    # ── 会话管理 ──────────────────────────────────────────────

    def get_sessions(self) -> list[dict]:
        """读取 ``.sessions.json``，返回所有会话元数据。"""
        return SessionRegistry(self._sessions_path).list()

    def delete_session(self, thread_id: str) -> bool:
        """从注册表删除指定 ``thread_id`` 的会话。返回是否真的删了。"""
        return SessionRegistry(self._sessions_path).remove(thread_id)

    # ── 设置读写 ──────────────────────────────────────────────

    def get_settings(self) -> dict[str, Any]:
        """读取当前配置：``.env`` 文件 + ``os.environ`` 合并，缺值用 dataclass 默认。

        优先级：``os.environ`` > ``.env`` 文件 > dataclass 默认。

        注意：**不**用 ``Settings.from_env()``——后者在缺 API key 时会抛
        ``RuntimeError``，但前端 UI 需要在没配 key 的情况下也能打开设置页。
        """
        file_values = self._read_env_file()
        # 合并：os.environ 优先
        merged: dict[str, str] = dict(file_values)
        for env_name in _SETTINGS_KEY_TO_ENV.values():
            if env_name in os.environ:
                merged[env_name] = os.environ[env_name]

        result: dict[str, Any] = {}
        for key, env_name in _SETTINGS_KEY_TO_ENV.items():
            raw = merged.get(env_name, "")
            if key in _BOOL_KEYS:
                result[key] = (
                    raw.strip().lower() == "true" if raw.strip() else _get_default(key)
                )
            elif key in _INT_KEYS:
                default = _get_default(key)
                result[key] = int(raw) if raw.strip() else default
            elif key in _FLOAT_KEYS:
                default = _get_default(key)
                result[key] = float(raw) if raw.strip() else default
            elif key in _LIST_KEYS:
                result[key] = [s.strip() for s in raw.split(",") if s.strip()]
            else:
                # string：raw 为空时回退到 dataclass 默认
                result[key] = raw if raw else _get_default(key)
        return result

    def save_settings(self, settings: dict[str, Any]) -> None:
        """把 ``settings`` 字典写回 ``.env``，保留文件中未涉及的字段。

        序列化规则：
        - ``bool`` → ``"true"`` / ``"false"``
        - ``list`` → 逗号分隔字符串
        - 其余 → ``str(value)``
        """
        existing = self._read_env_file()
        for key, value in settings.items():
            env_name = _SETTINGS_KEY_TO_ENV.get(key, key.upper())
            if isinstance(value, bool):
                existing[env_name] = "true" if value else "false"
            elif isinstance(value, list):
                existing[env_name] = ",".join(str(v) for v in value)
            else:
                existing[env_name] = str(value)
        self._write_env_file(existing)

    def _read_env_file(self) -> dict[str, str]:
        """解析 ``.env``，跳过注释和空行。"""
        if not self._env_path.exists():
            return {}
        result: dict[str, str] = {}
        for line in self._env_path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if "=" not in stripped:
                continue
            k, v = stripped.split("=", 1)
            result[k.strip()] = v.strip()
        return result

    def _write_env_file(self, data: dict[str, str]) -> None:
        """把 ``data`` 写回 ``.env``，按 key 排序便于 diff。"""
        lines = [f"{k}={v}" for k, v in sorted(data.items())]
        self._env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    # ── Soul / Profile ─────────────────────────────────────────

    def get_soul(self) -> dict:
        """读取 ``soul.json``。"""
        return SoulManager(self._soul_path).load()

    def save_soul(self, soul: dict) -> None:
        """写入 ``soul.json``。"""
        SoulManager(self._soul_path).save(soul)

    def get_profile(self) -> dict:
        """读取 ``profile.json``。"""
        return ProfileManager(self._profile_path).load()

    def save_profile(self, profile: dict) -> None:
        """写入 ``profile.json``。"""
        ProfileManager(self._profile_path).save(profile)

    # ── 工具枚举 ──────────────────────────────────────────────

    def get_tools(self) -> list[dict[str, str]]:
        """枚举 ``src/tools/`` 下的 ``@tool`` 装饰函数。

        返回格式：``[{"name": ..., "description": ..., "file": ...}, ...]``。
        """
        return _parse_tool_files(self._tools_dir)


# ── 窗口工厂 ──────────────────────────────────────────────────────────


def _html_path() -> Path:
    """前端 HTML 路径——和 wrapper 同级的 ``agent_shell/index.html``。"""
    return Path(__file__).resolve().parent / "agent_shell" / "index.html"


def create_window(api: Api) -> Any:
    """创建无边框、置顶的 100x100 浮窗。

    ``width=100, height=100`` 是初始尺寸（mascot 动画），菜单/面板的展开
    由前端 CSS + JS 控制——目前不在 Python 侧动态 resize（保持简单）。
    """
    import webview  # 延迟导入：避免无 webview 环境下 import 失败

    html_file = _html_path()
    html = html_file.read_text(encoding="utf-8") if html_file.exists() else ""
    return webview.create_window(
        title="Agent Shell",
        html=html,
        url=None,
        width=100,
        height=100,
        resizable=False,
        frameless=True,
        always_on_top=True,
        js_api=api,
    )


# ── 入口 ─────────────────────────────────────────────────────────────


if __name__ == "__main__":
    import webview

    _api = Api()
    create_window(_api)
    webview.start()
