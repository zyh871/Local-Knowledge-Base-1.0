"""计算器工具的主路径与护栏。

为什么单独一个文件：原来这里只有 3 个正例 + 3 个拒绝例，
**最常用的普通四则运算、函数调用、常量求值、空表达式、inf/NaN、
float→int 归一** 全都没有断言；`_ALLOWED_NODES` 还是一张没人引用的死代码，
让人误以为节点校验靠它。

变异验证（改实现 → 本文件必须变红）：
  * 把 `_MAX_POW_EXPONENT` 改成 100 或把 `>` 改成 `>=` → test_pow_exponent_bounded 变红
  * 把 `_ALLOWED_FUNCTIONS` 去掉 "sqrt" → test_functions 变红
  * 去掉 float→int 归一 → test_result_normalized_to_int 变红
"""

from __future__ import annotations

import math

import pytest

from app.tools.calculator import CalculatorTool


def calc(expr: str) -> dict:
    return CalculatorTool().execute(expr)


class TestArithmetic:
    @pytest.mark.parametrize(
        "expr,expected",
        [
            ("2 + 3 * 4", 14),
            ("7 // 2", 3),
            ("7 % 3", 1),
            ("1 / 2", 0.5),
            ("-3 + +2", -1),
            ("(1 + 2) * 3", 9),
            ("2 ** 10", 1024),
            ("2 ** -1", 0.5),
        ],
    )
    def test_arithmetic(self, expr, expected):
        out = calc(expr)
        assert out["success"] is True, out
        assert out["result"] == expected

    def test_result_normalized_to_int(self):
        """整值浮点应归一为 int（前端展示与后续计算都依赖这个约定）。"""
        out = calc("10 / 2")
        assert out["success"] is True
        assert out["result"] == 5
        assert isinstance(out["result"], int)


class TestFunctionsAndConstants:
    @pytest.mark.parametrize(
        "expr,expected",
        [
            ("sqrt(16)", 4.0),
            ("abs(-3)", 3),
            ("max(1, 5, 3)", 5),
            ("min(1, 5, 3)", 1),
            ("round(2.567, 2)", 2.57),
            ("ceil(1.2)", 2),
            ("floor(1.8)", 1),
            ("factorial(5)", 120),
            ("log(100, 10)", 2.0),
            ("log10(1000)", 3.0),
        ],
    )
    def test_functions(self, expr, expected):
        out = calc(expr)
        assert out["success"] is True, out
        assert out["result"] == pytest.approx(expected)

    def test_constants(self):
        assert calc("pi")["result"] == pytest.approx(math.pi)
        assert calc("e")["result"] == pytest.approx(math.e)


class TestRejections:
    @pytest.mark.parametrize(
        "expr,error",
        [
            ("", "表达式不能为空"),
            ("a" * 501, "表达式过长"),
            ("1e400 * 1e400", "结果为无穷大"),
            ("sqrt(-1)", "计算失败"),
            ("__import__('os')", "不允许"),
            ("os.getcwd()", "仅支持直接函数调用"),
            ("'abc'", "不允许"),
            ("2 @ 3", "不支持"),
            ("factorial(1.5)", "factorial"),
            ("factorial(1001)", "超限"),
        ],
    )
    def test_rejections(self, expr, error):
        out = calc(expr)
        assert out["success"] is False, out
        assert error in out["error"], out


class TestGuards:
    def test_pow_exponent_bounded(self):
        """指数上限是 DoS 护栏：必须被拒绝，且不能先求值再检查。"""
        out = calc("10 ** 100000")
        assert out["success"] is False
        assert "指数过大" in out["error"]

    def test_pow_non_constant_exponent_rejected(self):
        out = calc("2 ** x")
        assert out["success"] is False
        assert "指数" in out["error"]

    def test_chained_pow_does_not_explode(self):
        """`9**9**9` 若先求右值再检查上限，会直接把 CPU 打满。"""
        out = calc("9 ** 9 ** 9")
        assert out["success"] is False
