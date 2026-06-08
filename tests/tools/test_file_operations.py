"""Tests for file_operations tools (list_files, read_file, grep_search).

工具函数被 @tool 装饰器包装为 StructuredTool，
同步工具用 .invoke() 调用，异步工具用 .ainvoke() 调用。
"""

import os
from pathlib import Path

import pytest

from src.tools.file_operations import (
    _safe_path,
    _format_size,
    list_files,
    read_file,
    grep_search,
    create_folder,
)


class TestSafePath:
    """_safe_path 路径安全校验。"""

    def test_project_path_ok(self):
        p = _safe_path("src/tools")
        assert p.exists()

    def test_path_traversal_blocked(self):
        with pytest.raises(ValueError):
            _safe_path("../../etc/passwd")

    def test_system_dir_blocked(self):
        if os.name == "nt":
            bad = "C:\\Windows\\System32"
        else:
            bad = "/etc"
        with pytest.raises(ValueError):
            _safe_path(bad)

    def test_user_dir_allowed(self):
        """用户目录（Desktop 等）应该允许访问。"""
        if os.name == "nt":
            desktop = os.path.join(os.environ.get("USERPROFILE", "C:\\Users\\Default"), "Desktop")
            if os.path.exists(desktop):
                p = _safe_path(desktop)
                assert p.exists()

    def test_absolute_path_outside_project_allowed(self):
        """项目外的普通目录（非系统路径）应该允许。"""
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            p = _safe_path(tmpdir)
            assert str(p) == str(Path(tmpdir).resolve())


class TestFormatSize:
    def test_bytes(self):
        assert _format_size(500) == "500B"

    def test_kb(self):
        assert "KB" in _format_size(2048)

    def test_mb(self):
        assert "MB" in _format_size(2_000_000)


class TestListFiles:
    def test_list_root(self):
        result = list_files.invoke({"path": "src/tools"})
        assert "calculator.py" in result

    def test_list_with_pattern(self):
        result = list_files.invoke({"path": "src/tools", "pattern": "*.py"})
        assert "calculator.py" in result

    def test_list_nonexistent(self):
        result = list_files.invoke({"path": "nonexistent_dir_xyz"})
        assert "错误" in result

    def test_list_file_path(self):
        result = list_files.invoke({"path": "src/tools/__init__.py"})
        assert "错误" in result  # 不是目录


class TestReadFile:
    def test_read_existing(self):
        result = read_file.invoke({"path": "src/tools/__init__.py"})
        assert "file_operations" in result or "calculator" in result

    def test_read_with_offset(self):
        result = read_file.invoke({"path": "src/tools/__init__.py", "offset": 0, "limit": 3})
        assert "Agent" in result  # content is present
        assert len(result) > 10  # not empty

    def test_read_nonexistent(self):
        result = read_file.invoke({"path": "nonexistent.py"})
        assert "错误" in result

    def test_read_directory(self):
        result = read_file.invoke({"path": "src/tools"})
        assert "错误" in result

    def test_read_binary(self):
        """读取二进制文件应提示跳过。"""
        # 找一个已知二进制文件
        result = read_file.invoke({"path": ".venv/Scripts/python.exe"})
        assert "二进制" in result or "跳过" in result


class TestCreateFolder:
    def test_create_new(self):
        result = create_folder.invoke({"path": "tests/_test_create_folder"})
        assert "已创建" in result
        # cleanup
        import os, shutil
        p = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "tests", "_test_create_folder")
        if os.path.exists(p):
            shutil.rmtree(p)

    def test_create_existing(self):
        result = create_folder.invoke({"path": "tests"})
        assert "已存在" in result

    def test_create_nested(self):
        result = create_folder.invoke({"path": "tests/_test_create/a/b/c"})
        assert "已创建" in result
        import os, shutil
        p = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "tests", "_test_create")
        if os.path.exists(p):
            shutil.rmtree(p)

    def test_path_traversal_blocked(self):
        result = create_folder.invoke({"path": "../../etc"})
        assert "错误" in result


class TestGrepSearch:
    def test_grep_found(self):
        result = grep_search.invoke({"pattern": "calculator", "path": "src/tools", "include": "*.py"})
        assert "calculator.py" in result

    def test_grep_not_found(self):
        result = grep_search.invoke({"pattern": "XYZZYX_DOES_NOT_EXIST_12345", "path": "src/tools", "include": "*.py"})
        assert "未找到" in result or "未匹配" in result

    def test_grep_invalid_regex(self):
        result = grep_search.invoke({"pattern": "[invalid", "path": "src/tools"})
        assert "错误" in result
