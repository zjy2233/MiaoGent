import pytest
from src.tools.shell.danger import ConfirmationError, check_danger
from src.tools.shell.patterns import DangerLevel


class TestConfirmationError:
    def test_init(self):
        err = ConfirmationError("rm -rf /", "递归删除根目录", "high_risk")
        assert err.command == "rm -rf /"
        assert err.reason == "递归删除根目录"
        assert err.danger_level == "high_risk"

    def test_str(self):
        err = ConfirmationError("rm foo", "删除文件", "confirm")
        assert "rm foo" in str(err)
        assert "删除文件" in str(err)


class TestCheckDanger:
    def test_safe_command_returns_none(self):
        result = check_danger("ls -la")
        assert result is None

    def test_rm_is_confirm(self):
        result = check_danger("rm file.txt")
        assert result is not None
        assert result.danger_level == "confirm"

    def test_rm_rf_root_is_high_risk(self):
        result = check_danger("rm -rf /")
        assert result is not None
        assert result.danger_level == "high_risk"