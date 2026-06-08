"""数学计算工具：基于 AST 白名单的安全表达式求值。

我们不直接用 ``eval``，因为它能执行任意 Python 代码。
这里只允许：数字、算术运算、math 模块的只读函数/常量。
"""

from __future__ import annotations

import ast
import math
import operator
from typing import Any

from langchain_core.tools import tool

# AST 节点 → 实际可调用对象 的白名单映射
_BIN_OPS: dict[type, Any] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_UNARY_OPS: dict[type, Any] = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}

# 仅暴露 math 模块中的安全符号
_ALLOWED_NAMES: dict[str, Any] = {
    name: getattr(math, name)
    for name in (
        "pi",
        "e",
        "tau",
        "inf",
        "nan",
        "sqrt",
        "log",
        "log2",
        "log10",
        "exp",
        "sin",
        "cos",
        "tan",
        "asin",
        "acos",
        "atan",
        "atan2",
        "sinh",
        "cosh",
        "tanh",
        "degrees",
        "radians",
        "ceil",
        "floor",
        "fabs",
        "factorial",
        "gcd",
        "pow",
    )
}


class _SafeEvaluator(ast.NodeVisitor):
    """遍历 AST，遇到任何不在白名单的节点直接拒绝。"""

    def visit(self, node: ast.AST) -> Any:  # type: ignore[override]
        if isinstance(node, ast.Expression):
            return self.visit(node.body)
        if isinstance(node, ast.Constant):
            if not isinstance(node.value, (int, float)):
                raise ValueError(f"常量类型 {type(node.value).__name__} 不被允许")
            return node.value
        if isinstance(node, ast.BinOp) and type(node.op) in _BIN_OPS:
            left = self.visit(node.left)
            right = self.visit(node.right)
            return _BIN_OPS[type(node.op)](left, right)
        if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY_OPS:
            return _UNARY_OPS[type(node.op)](self.visit(node.operand))
        if isinstance(node, ast.Name):
            if node.id not in _ALLOWED_NAMES:
                raise ValueError(f"名称 {node.id!r} 不在白名单中")
            return _ALLOWED_NAMES[node.id]
        if isinstance(node, ast.Call):
            # 只允许形如 func(args) 的 math 函数调用
            if not isinstance(node.func, ast.Name):
                raise ValueError("只允许调用已注册的函数")
            if node.func.id not in _ALLOWED_NAMES:
                raise ValueError(f"函数 {node.func.id!r} 不在白名单中")
            if node.keywords:
                raise ValueError("不支持关键字参数")
            args = [self.visit(a) for a in node.args]
            return _ALLOWED_NAMES[node.func.id](*args)
        raise ValueError(f"AST 节点 {type(node).__name__} 不被允许")


def safe_eval(expression: str) -> float:
    """对 *数学表达式* 字符串求值。

    Raises:
        ValueError: 表达式不是合法的数学表达式，或包含白名单之外的语法/名称。
        ZeroDivisionError: 除零。
    """
    if not expression or not expression.strip():
        raise ValueError("表达式不能为空")
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as exc:
        raise ValueError(f"不是合法的数学表达式: {exc.msg}") from exc
    return _SafeEvaluator().visit(tree)


_TOOL_GUIDE = "所有数学计算必须调用 calculator，不要心算或用 shell（python -c）计算。"


@tool
def calculator(expression: str) -> str:
    """执行数学计算并返回结果字符串。

    Args:
        expression: 合法的数学表达式字符串，例如 ``2 ** 10``、
            ``sqrt(144) + 3 * (1 + 2)``、``sin(pi/2)``。
            支持基本运算 (+ - * / ** %)、括号，以及 math 模块函数
            (sqrt, log, sin, cos, tan, pi, e 等)。

    Returns:
        计算结果的字符串形式。整数结果不带 ``.0``。
        输入非法时返回 ``错误：...`` 形式的说明，agent 可基于此重试。
    """
    try:
        value = safe_eval(expression)
    except ZeroDivisionError:
        return "错误：除数不能为零"
    except ValueError as exc:
        return f"错误：{exc}"
    except Exception as exc:  # noqa: BLE001 — 兜底给 agent 可读的错误信息
        return f"错误：计算失败 ({type(exc).__name__}: {exc})"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)
