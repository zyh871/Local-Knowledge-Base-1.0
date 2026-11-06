"""输入上限与文件名规则。

这些用例守的是两类「不会被编译器发现、但会被用户踩到」的回归：
  * 计算器：短表达式也能构造天文数字（`9**9**9`），必须快速拒绝而不是卡死
  * 文件名：`a..b.txt` 是合法名，不能因为包含 `..` 就误伤
"""

from __future__ import annotations

import time

import pytest


class TestCalculatorLimits:
    def _calc(self):
        from app.tools.calculator import CalculatorTool

        return CalculatorTool()

    def test_normal_power_still_works(self):
        result = self._calc().execute("2**10")
        assert result["success"] is True
        assert result["result"] == 1024

    def test_normal_factorial_still_works(self):
        result = self._calc().execute("factorial(5)")
        assert result["success"] is True
        assert result["result"] == 120

    def test_negative_exponent_still_works(self):
        """`2**-1` 是合法写法，不能因为指数带符号就被误拒。"""
        result = self._calc().execute("2**-1")
        assert result["success"] is True
        assert result["result"] == 0.5

    @pytest.mark.parametrize(
        "expression",
        [
            "9**9**9",          # 嵌套幂：求值前就应拒绝
            "2**100000",        # 指数超上限
            "factorial(1001)",  # 阶乘超上限
        ],
    )
    def test_dangerous_expressions_are_rejected(self, expression):
        started = time.perf_counter()
        result = self._calc().execute(expression)
        elapsed = time.perf_counter() - started

        assert result["success"] is False
        # 关键：必须"快速失败"，而不是算到一半才超时
        assert elapsed < 1.0


class TestFilenameRules:
    def test_double_dot_inside_name_is_allowed(self):
        from app.utils.files import sanitize_filename

        assert sanitize_filename("a..b.txt") == "a..b.txt"
        assert sanitize_filename("report..final.pdf") == "report..final.pdf"

    @pytest.mark.parametrize(
        "name",
        ["..", "../evil.txt", "sub/dir.txt", "a\\b.txt"],
    )
    def test_path_traversal_is_rejected(self, name):
        from app.core.exceptions import IllegalFilenameError
        from app.utils.files import sanitize_filename

        # ⚠️ 不要写 `pytest.raises(Exception)`：实现里任何 bug
        #（比如 TypeError）都会被当成"预期异常"而通过。
        with pytest.raises(IllegalFilenameError):
            sanitize_filename(name)
