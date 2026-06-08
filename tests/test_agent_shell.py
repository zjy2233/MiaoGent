"""Api 单元测试 — HTTP API 桥接层。

不启动 HTTP 服务器；只验证 Api 实例的方法能正确读写磁盘文件。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from frontend.bridge import Api, _parse_tool_files, _SETTINGS_KEY_TO_ENV


class TestApiSessions:
    """get_sessions / delete_session。"""

    def test_get_sessions_empty(self, tmp_path: Path) -> None:
        api = Api(root_dir=tmp_path)
        assert api.get_sessions() == []

    def test_delete_session_removes_entry(self, tmp_path: Path) -> None:
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        sessions_file = data_dir / ".sessions.json"
        sessions_file.write_text(
            json.dumps(
                {
                    "sessions": [
                        {
                            "thread_id": "tid-1",
                            "created_at": "2026-01-01T00:00:00",
                            "last_active": "2026-01-01T00:00:00",
                            "turn_count": 3,
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        api = Api(root_dir=tmp_path)
        assert len(api.get_sessions()) == 1

        deleted = api.delete_session("tid-1")
        assert deleted is True
        assert api.get_sessions() == []

    def test_delete_session_missing_returns_false(self, tmp_path: Path) -> None:
        api = Api(root_dir=tmp_path)
        assert api.delete_session("nonexistent") is False


class TestApiSettings:
    """get_settings / save_settings。"""

    def test_get_settings_returns_defaults(self, tmp_path: Path, monkeypatch) -> None:
        for env_name in _SETTINGS_KEY_TO_ENV.values():
            monkeypatch.delenv(env_name, raising=False)

        api = Api(root_dir=tmp_path)
        settings = api.get_settings()

        assert settings["deepseek_base_url"] == "https://api.deepseek.com"
        assert settings["deepseek_model"] == "deepseek-chat"
        assert settings["shell_high_risk_block"] is True
        assert settings["shell_auto_confirm"] is False
        assert settings["max_turns"] == 10
        assert settings["shell_allowed_patterns"] == []
        assert settings["shell_blocked_patterns"] == []

    def test_save_and_reload_settings(self, tmp_path: Path, monkeypatch) -> None:
        for env_name in _SETTINGS_KEY_TO_ENV.values():
            monkeypatch.delenv(env_name, raising=False)

        api = Api(root_dir=tmp_path)
        new_settings = {
            "deepseek_api_key": "sk-test-123",
            "deepseek_model": "deepseek-coder",
            "shell_auto_confirm": True,
            "max_turns": 20,
            "shell_allowed_patterns": ["git *", "ls *"],
        }
        api.save_settings(new_settings)

        api2 = Api(root_dir=tmp_path)
        reloaded = api2.get_settings()
        assert reloaded["deepseek_api_key"] == "sk-test-123"
        assert reloaded["deepseek_model"] == "deepseek-coder"
        assert reloaded["shell_auto_confirm"] is True
        assert reloaded["max_turns"] == 20
        assert reloaded["shell_allowed_patterns"] == ["git *", "ls *"]

    def test_save_settings_preserves_existing(self, tmp_path: Path) -> None:
        env_path = tmp_path / ".env"
        env_path.write_text(
            "DEEPSEEK_API_KEY=existing-key\n"
            "DEEPSEEK_MODEL=deepseek-chat\n"
            "# 这是一行注释\n"
            "DB_PATH=custom.db\n",
            encoding="utf-8",
        )

        api = Api(root_dir=tmp_path)
        api.save_settings({"max_turns": 50})

        text = env_path.read_text(encoding="utf-8")
        assert "DEEPSEEK_API_KEY=existing-key" in text
        assert "DB_PATH=custom.db" in text
        assert "MAX_TURNS=50" in text


class TestApiSoul:
    """get_soul / save_soul。"""

    def test_get_soul_default(self, tmp_path: Path) -> None:
        api = Api(root_dir=tmp_path)
        assert api.get_soul() == {"version": 1, "description": ""}

    def test_save_and_get_soul(self, tmp_path: Path) -> None:
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        api = Api(root_dir=tmp_path)
        api.save_soul({"version": 1, "description": "温柔、简洁"})
        assert api.get_soul() == {"version": 1, "description": "温柔、简洁"}


class TestApiProfile:
    """get_profile / save_profile。"""

    def test_get_profile_default(self, tmp_path: Path) -> None:
        api = Api(root_dir=tmp_path)
        assert api.get_profile() == {"version": 1}

    def test_save_and_get_profile(self, tmp_path: Path) -> None:
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        api = Api(root_dir=tmp_path)
        api.save_profile({"version": 1, "name": "张三", "city": "北京"})
        assert api.get_profile() == {"version": 1, "name": "张三", "city": "北京"}


class TestApiTools:
    """get_tools — AST 解析 src/tools/*.py 中的 @tool 装饰器。"""

    def test_parse_tool_files_finds_known_tools(self, tmp_path: Path) -> None:
        tools_dir = tmp_path / "tools"
        tools_dir.mkdir()
        (tools_dir / "calc.py").write_text(
            "from langchain_core.tools import tool\n"
            "\n"
            "\n"
            "@tool\n"
            "def calculator(expression: str) -> str:\n"
            '    """执行数学计算。"""\n'
            "    return str(expression)\n",
            encoding="utf-8",
        )
        (tools_dir / "time_tool.py").write_text(
            "from langchain_core.tools import tool\n"
            "\n"
            "\n"
            "@tool\n"
            "def current_time() -> str:\n"
            '    """返回当前时间。"""\n'
            "    return 'now'\n",
            encoding="utf-8",
        )
        (tools_dir / "helper.py").write_text(
            "def helper() -> str:\n"
            '    """不是 @tool。"""\n'
            "    return 'ok'\n",
            encoding="utf-8",
        )

        tools = _parse_tool_files(tools_dir)
        names = [t["name"] for t in tools]
        assert "calculator" in names
        assert "current_time" in names
        assert "helper" not in names

        calc = next(t for t in tools if t["name"] == "calculator")
        assert calc["description"] == "执行数学计算。"
        assert calc["file"].endswith("calc.py")

    def test_parse_tool_files_handles_decorator_with_parens(self, tmp_path: Path) -> None:
        tools_dir = tmp_path / "tools"
        tools_dir.mkdir()
        (tools_dir / "paren.py").write_text(
            "from langchain_core.tools import tool\n"
            "\n"
            "\n"
            "@tool()\n"
            "def foo() -> str:\n"
            '    """带括号的 @tool。"""\n'
            "    return 'x'\n",
            encoding="utf-8",
        )
        tools = _parse_tool_files(tools_dir)
        assert any(t["name"] == "foo" for t in tools)

    def test_parse_tool_files_handles_attribute_decorator(self, tmp_path: Path) -> None:
        tools_dir = tmp_path / "tools"
        tools_dir.mkdir()
        (tools_dir / "attr.py").write_text(
            "from langchain_core.tools import tool as my_tool\n"
            "\n"
            "\n"
            "@my_tool\n"
            "def bar() -> str:\n"
            '    """带别名的 @tool。"""\n'
            "    return 'y'\n",
            encoding="utf-8",
        )
        tools = _parse_tool_files(tools_dir)
        assert all(t["name"] != "bar" for t in tools)

    def test_parse_tool_files_empty_dir(self, tmp_path: Path) -> None:
        tools_dir = tmp_path / "tools"
        tools_dir.mkdir()
        assert _parse_tool_files(tools_dir) == []

    def test_parse_tool_files_missing_dir(self, tmp_path: Path) -> None:
        assert _parse_tool_files(tmp_path / "nope") == []

    def test_get_tools_uses_real_src_tools(self, tmp_path: Path, monkeypatch) -> None:
        api = Api(root_dir=tmp_path)
        (tmp_path / "src" / "tools").mkdir(parents=True)
        (tmp_path / "src" / "tools" / "demo.py").write_text(
            "from langchain_core.tools import tool\n"
            "\n"
            "\n"
            "@tool\n"
            "def demo() -> str:\n"
            '    """演示工具。"""\n'
            "    return 'ok'\n",
            encoding="utf-8",
        )
        api._tools_dir = tmp_path / "src" / "tools"
        tools = api.get_tools()
        assert len(tools) == 1
        assert tools[0]["name"] == "demo"
