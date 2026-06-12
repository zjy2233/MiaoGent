"""工具枚举服务 — 使用 AST 解析 ``src/tools/*.py`` 的源代码，识别 ``@tool`` 装饰器。"""

from __future__ import annotations

import ast
from pathlib import Path


def _is_tool_decorator(decorator: ast.expr) -> bool:
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
    if not tools_dir.is_dir():
        return []
    results: list[dict[str, str]] = []
    for py_file in sorted(tools_dir.rglob("*.py")):
        # Only skip the root __init__.py (re-exports), not sub-package ones
        if py_file == tools_dir / "__init__.py":
            continue
        try:
            source = py_file.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(py_file))
        except (OSError, SyntaxError):
            continue

        # ── Extract module-level __category__ ──
        category = ""
        for stmt in tree.body:
            if isinstance(stmt, ast.Assign):
                for target in stmt.targets:
                    if isinstance(target, ast.Name) and target.id == "__category__":
                        if isinstance(stmt.value, ast.Constant):
                            category = stmt.value.value
                        break

        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if not any(_is_tool_decorator(d) for d in node.decorator_list):
                continue
            doc = ast.get_docstring(node) or ""
            results.append({
                "name": node.name,
                "description": doc.strip(),
                "file": str(py_file),
                "category": category,
            })
    return results


class ToolService:
    """工具枚举 — AST 解析 ``src/tools/*.py`` 中的 ``@tool`` 装饰器。"""

    def __init__(self, tools_dir: Path) -> None:
        self._tools_dir = tools_dir

    def get_tools(self) -> list[dict[str, str]]:
        return _parse_tool_files(self._tools_dir)
