"""Tests for write_file tool.

write_file 使用 StructuredTool 包装，用 .invoke() 调用。
"""

import os

from src.tools.write_file import write_file


class TestWriteFile:
    def test_write_empty_content(self):
        """空内容应该被拒绝。"""
        result = write_file.invoke({"path": "tests/test_tmp.txt", "content": ""})
        assert "错误" in result

    def test_path_traversal_blocked(self):
        """路径穿越应该被拒绝。"""
        result = write_file.invoke({"path": "../../etc/passwd", "content": "evil"})
        assert "错误" in result
