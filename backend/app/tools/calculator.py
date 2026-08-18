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

_ALLOWED_NODES = (
    ast.Expression,
    ast.BinOp,
    ast.UnaryOp,
    ast.Num,
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


class _SafeEval(ast.NodeVisitor):
    """递归求值器，仅允许白名单节点。"""

    def visit_Expression(self, node):
        return self.visit(node.body)

    def visit_BinOp(self, node):
        left = self.visit(node.left)
        right = self.visit(node.right)
        op_func = _ALLOWED_BINOPS.get(type(node.op))
        if op_func is None:
            raise ValueError(f"不支持的运算符: {type(node.op).__name__}")
        return op_func(left, right)

    def visit_UnaryOp(self, node):
        operand = self.visit(node.operand)
        op_func = _ALLOWED_UNARYOPS.get(type(node.op))
        if op_func is None:
            raise ValueError(f"不支持的一元运算符: {type(node.op).__name__}")
        return op_func(operand)

    def visit_Call(self, node):
        if not isinstance(node.func, ast.Name):
            raise ValueError("仅支持直接函数调用")
        func_name = node.func.id
        if func_name not in _ALLOWED_FUNCTIONS:
            raise ValueError(f"不允许的函数: {func_name}")
        args = [self.visit(a) for a in node.args]
        return _ALLOWED_FUNCTIONS[func_name](*args)

    def visit_Name(self, node):
        if node.id in _ALLOWED_CONSTANTS:
            return _ALLOWED_CONSTANTS[node.id]
        raise ValueError(f"不允许的变量: {node.id}")

    def visit_Constant(self, node):
        if isinstance(node.value, (int, float)):
            return node.value
        raise ValueError(f"不允许的常量类型: {type(node.value)}")

    def generic_visit(self, node):
        raise ValueError(f"不允许的表达式节点: {type(node).__name__}")


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
        except (ValueError, SyntaxError, TypeError, ZeroDivisionError) as e:
            return {"success": False, "error": str(e), "expression": expr}
        except Exception as e:
            logger.error(f"计算器内部错误: {e}")
            return {"success": False, "error": f"计算失败: {e}"}
