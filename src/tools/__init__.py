"""Agent 可调用的工具集合。"""

from src.tools.calculator import calculator
from src.tools.current_time import current_time
from src.tools.weather import weather
from src.tools.search import search
from src.tools.web_fetch import web_fetch
from src.tools.file_operations import list_files, read_file, grep_search, create_folder
from src.tools.write_file import write_file
from src.tools.run_python import run_python
from src.tools.shell import shell
from src.tools.install_skill import install_skill, uninstall_skill, list_registry

__all__ = [
    "calculator", "current_time", "weather", "search", "web_fetch",
    "list_files", "read_file", "grep_search", "create_folder", "write_file", "run_python",
    "shell",
    "install_skill", "uninstall_skill", "list_registry",
]

# ── Tool _TOOL_GUIDE 发现映射 ──
# 工具名 → 定义 _TOOL_GUIDE 的模块路径
# 用于 builder._build_tool_guide() 自动收集使用指南
_TOOL_GUIDE_MODULES: dict[str, str] = {
    "calculator": "src.tools.calculator",
    "current_time": "src.tools.current_time",
    "weather": "src.tools.weather",
    "search": "src.tools.search",
    "web_fetch": "src.tools.web_fetch",
    "list_files": "src.tools.file_operations",
    "read_file": "src.tools.file_operations",
    "grep_search": "src.tools.file_operations",
    "create_folder": "src.tools.file_operations",
    "write_file": "src.tools.write_file",
    "run_python": "src.tools.run_python",
    "shell": "src.tools.shell.tool",
    "install_skill": "src.tools.install_skill",
    "uninstall_skill": "src.tools.install_skill",
    "list_registry": "src.tools.install_skill",
}
