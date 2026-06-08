"""数学计算工具的单元测试。"""

from __future__ import annotations

import math

import pytest

from src.tools.calculator import calculator, safe_eval


class TestSafeEval:
    """safe_eval 应当只接受纯数学表达式，拒绝一切非数学输入。"""

    @pytest.mark.parametrize(
        "expr,expected",
        [
            ("1 + 2", 3),
            ("10 - 4", 6),
            ("3 * 7", 21),
            ("8 / 2", 4.0),
            ("2 ** 10", 1024),
            ("17 % 5", 2),
            ("(1 + 2) * 3", 9),
        ],
    )
    def test_basic_arithmetic(self, expr: str, expected: float) -> None:
        assert safe_eval(expr) == pytest.approx(expected)

    def test_math_module_functions_are_available(self) -> None:
        assert safe_eval("sqrt(16)") == pytest.approx(4.0)
        assert safe_eval("sin(0)") == pytest.approx(0.0)
        assert safe_eval("pi") == pytest.approx(math.pi)

    @pytest.mark.parametrize(
        "bad_input",
        [
            "import os",
            "__import__('os').system('echo pwned')",
            "open('/etc/passwd').read()",
            "().__class__",
            "eval('1+1')",
            "[x for x in range(10)]",
        ],
    )
    def test_unsafe_expressions_are_rejected(self, bad_input: str) -> None:
        # Windows 终端默认 cp1252，无法用中文做正则匹配；只断言类型即可
        with pytest.raises(ValueError):
            safe_eval(bad_input)

    def test_division_by_zero_raises(self) -> None:
        with pytest.raises(ZeroDivisionError):
            safe_eval("1 / 0")


class TestCalculatorTool:
    """@tool 装饰器把函数变成 BaseTool，工具名取自函数名。"""

    def test_is_a_langchain_tool(self) -> None:
        from langchain_core.tools import BaseTool

        assert isinstance(calculator, BaseTool)
        assert calculator.name == "calculator"
        # 装饰器会从 type hint + docstring 自动生成参数 schema
        assert "expression" in calculator.args

    def test_invoke_returns_string(self) -> None:
        result = calculator.invoke({"expression": "2 ** 8"})
        assert "256" in str(result)
