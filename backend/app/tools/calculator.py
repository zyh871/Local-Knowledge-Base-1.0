"""
计算器工具 — 安全的数学表达式求值。

使用 ast 解析 + 白名单节点校验，杜绝代码注入。
支持四则运算、幂运算、常用数学函数和常量。
"""

import ast
import logging
import math
import operator
from typing import Any, Dict

from app.tools.base import BaseTool

logger = logging.getLogger(__name__)

class CalculatorError(ValueError):
    """面向用户的提示（与 math/ast 的原始异常区分开）。"""


_ALLOWED_NODES = (
    ast.Expression,
    ast.BinOp,
    ast.UnaryOp,
    ast.Constant,
    ast.Load,
)

_ALLOWED_BINOPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}

_ALLOWED_UNARYOPS = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}

_ALLOWED_FUNCTIONS = {
    "abs": abs,
    "round": round,
    "min": min,
    "max": max,
    "sum": sum,
    "sqrt": math.sqrt,
    "sin": math.sin,
    "cos": math.cos,
    "tan": math.tan,
    "log": math.log,
    "log10": math.log10,
    "log2": math.log2,
    "exp": math.exp,
    "ceil": math.ceil,
    "floor": math.floor,
    "factorial": math.factorial,
}

_ALLOWED_CONSTANTS = {
    "pi": math.pi,
    "e": math.e,
    "inf": math.inf,
    "tau": math.tau,
}

#: 幂运算的指数上限。`9**9**9**9` 只有 8 个字符，却会让解释器长时间占满 CPU；
#: 必须在**求值前**用 AST 常量拦住，等算完再检查就晚了。
_MAX_POW_EXPONENT = 1000
#: 阶乘入参上限（`factorial(10**6)` 同样能卡死进程）。
_MAX_FACTORIAL = 1000


def _const_number(node: ast.AST) -> int | float | None:
    """只从 AST 里取「数字字面量（可带正负号）」，**不做任何运算**。

    用于幂指数：既要在求值前拿到值，又不能为了拿值去 visit 一个可能
    已经爆炸的子树（`9**9**9` 的指数是 `9**9`，visit 就已经开始算了）。
    返回 None 表示不是字面量。
    """
    if isinstance(node, ast.Constant):
        value = node.value
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        return value
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
        inner = _const_number(node.operand)
        if inner is None:
            return None
        return -inner if isinstance(node.op, ast.USub) else inner
    return None


class _SafeEval(ast.NodeVisitor):
    """递归求值器，仅允许白名单节点。"""

    def visit_Expression(self, node):
        return self.visit(node.body)

    def visit_BinOp(self, node):
        left = self.visit(node.left)

        if isinstance(node.op, ast.Pow):
            # 指数必须能从 AST 直接读出字面量，且先于求值检查：
            # 若先 visit(node.right)，`9**9**9` 在这一步就已经开始爆算了。
            exponent = _const_number(node.right)
            if exponent is None:
                raise CalculatorError("幂运算的指数必须是数字常量")
            if abs(exponent) > _MAX_POW_EXPONENT:
                raise CalculatorError(f"指数过大（上限 {_MAX_POW_EXPONENT}）")
            if isinstance(left, (int, float)) and abs(left) > 1e6 and abs(exponent) > 100:
                raise CalculatorError("底数过大的高次幂")
            return left ** exponent

        right = self.visit(node.right)
        op_func = _ALLOWED_BINOPS.get(type(node.op))
        if op_func is None:
            raise CalculatorError(f"不支持的运算符: {type(node.op).__name__}")
        return op_func(left, right)

    def visit_UnaryOp(self, node):
        operand = self.visit(node.operand)
        op_func = _ALLOWED_UNARYOPS.get(type(node.op))
        if op_func is None:
            raise CalculatorError(f"不支持的一元运算符: {type(node.op).__name__}")
        return op_func(operand)

    def visit_Call(self, node):
        if not isinstance(node.func, ast.Name):
            raise CalculatorError("仅支持直接函数调用")
        func_name = node.func.id
        if func_name not in _ALLOWED_FUNCTIONS:
            raise CalculatorError(f"不允许的函数: {func_name}")
        args = [self.visit(a) for a in node.args]
        if func_name == "factorial":
            if len(args) != 1 or not isinstance(args[0], int):
                raise CalculatorError("factorial 只接受一个整数")
            if args[0] < 0 or args[0] > _MAX_FACTORIAL:
                raise CalculatorError(f"factorial 入参超限（0–{_MAX_FACTORIAL}）")
        return _ALLOWED_FUNCTIONS[func_name](*args)

    def visit_Name(self, node):
        if node.id in _ALLOWED_CONSTANTS:
            return _ALLOWED_CONSTANTS[node.id]
        raise CalculatorError(f"不允许的变量: {node.id}")

    def visit_Constant(self, node):
        if isinstance(node.value, (int, float)):
            return node.value
        raise CalculatorError(f"不允许的常量类型: {type(node.value)}")

    def generic_visit(self, node):
        raise CalculatorError(f"不允许的表达式节点: {type(node).__name__}")


class CalculatorTool(BaseTool):
    """安全数学表达式计算器。"""

    @property
    def name(self) -> str:
        return "calculator"

    @property
    def description(self) -> str:
        return "计算数学表达式。支持四则运算、幂运算、三角函数、对数、绝对值等。输入数学表达式字符串，返回计算结果。"

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "expression": {
                    "type": "string",
                    "description": "数学表达式，如 '2 + 3 * 4'、'sqrt(16) + pi'、'log(100, 10)'",
                }
            },
            "required": ["expression"],
        }

    def execute(self, expression: str = "", **kwargs) -> Dict[str, Any]:
        expr = (expression or "").strip()
        if not expr:
            return {"success": False, "error": "表达式不能为空"}

        if len(expr) > 500:
            return {"success": False, "error": "表达式过长"}

        try:
            tree = ast.parse(expr, mode="eval")
            evaluator = _SafeEval()
            result = evaluator.visit(tree)

            if isinstance(result, float):
                if math.isinf(result):
                    return {"success": False, "error": "结果为无穷大"}
                if math.isnan(result):
                    return {"success": False, "error": "结果为 NaN"}
                if result == int(result) and abs(result) < 1e15:
                    result = int(result)

            return {"success": True, "result": result, "expression": expr}
        except CalculatorError as e:
            # 我们自己抛的提示，原样返回给用户。
            return {"success": False, "error": str(e), "expression": expr}
        except (ValueError, SyntaxError, TypeError, ZeroDivisionError) as e:
            # math/ast 的原始异常（"math domain error" 之类）对用户没有意义，
            # 还会把内部实现细节带出去 —— 统一成"计算失败"，原文只进日志。
            logger.warning(f"计算器求值失败: {e}")
            return {"success": False, "error": "计算失败", "expression": expr}
        except Exception as e:
            logger.error(f"计算器内部错误: {e}")
            return {"success": False, "error": "计算失败"}
