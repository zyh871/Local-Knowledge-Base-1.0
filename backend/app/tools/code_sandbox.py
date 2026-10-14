"""代码沙箱策略 —— 用 AST 做白名单校验，而不是子串黑名单。

## 为什么不用子串匹配

原实现是 `for d in ["import ", "__import__", "open(", ...]: if d in code: 拒绝`。
它两头都不成立：

* **误伤**：`from math import sqrt`、注释里写 `# import 说明`、
  字符串 `"请 import 该模块"` 全部被拒（实测 4/4 命中）。而这些跟"危险"毫无关系。
* **漏网**：`object.__subclasses__()` 不在名单里 —— 实测可达 542 个类，
  其中包括 `subprocess.Popen`。也就是说它挡不住真想绕的人。

AST 校验解决误伤（只看得见真实的语法结构，看不见注释与字符串），
配合运行时的受限 `__builtins__` 才能谈得上有边界。**它仍然不是安全边界**，
只能防「模型写错」，防不了「有人故意攻击」—— 详见 `docs` 里工作流一节的说明。
"""

from __future__ import annotations

import ast
from typing import Iterable

__all__ = [
    "DEFAULT_ALLOWED_MODULES",
    "SAFE_BUILTINS",
    "build_runtime_builtins",
    "describe_policy",
    "make_restricted_import",
    "parse_allowed_modules",
    "validate_code",
]

#: 默认允许 import 的标准库。
#
# 只有**纯计算 / 数据处理**类：没有任何一个能读写文件、起进程、
# 访问网络或触碰解释器内部。刻意不含 os / sys / subprocess / shutil /
# socket / ctypes / importlib / pickle / marshal / threading / multiprocessing。
#
# 为什么要开这个口子：不开的话生成的代码里只要有一句 `import math` 就整段被拒，
# 用户看到的是「AI 写的代码没法用」。而**拒掉的理由和用户的诉求无关** ——
# 这种"为了安全牺牲掉功能"的做法，实际效果是既没安全也没功能。
DEFAULT_ALLOWED_MODULES: frozenset[str] = frozenset(
    {
        "array",
        "base64",
        "bisect",
        "collections",
        "copy",
        "csv",
        "dataclasses",
        "datetime",
        "decimal",
        "difflib",
        "enum",
        "fractions",
        "functools",
        "hashlib",
        "heapq",
        "html",
        "io",
        "itertools",
        "json",
        "math",
        "numbers",
        "operator",
        "pprint",
        "random",
        "re",
        "statistics",
        "string",
        "textwrap",
        "time",
        "typing",
        "unicodedata",
        "uuid",
        "zoneinfo",
    }
)

#: 逃逸用的属性名。取"逐个点名"而不是"禁掉所有双下划线"——
#: 后者会把 `__init__`、`__name__`（`if __name__ == "__main__"`）一起禁掉，
#: 而那是任何正常 Python 脚本都会写的东西。
FORBIDDEN_ATTRS: frozenset[str] = frozenset(
    {
        "__bases__",
        "__base__",
        "__builtins__",
        "__class__",
        "__closure__",
        "__code__",
        "__dict__",
        "__getattribute__",
        "__globals__",
        "__import__",
        "__init_subclass__",
        "__loader__",
        "__mro__",
        "__reduce__",
        "__reduce_ex__",
        "__self__",
        "__spec__",
        "__subclasshook__",
        "__subclasses__",
        "cr_frame",
        "f_builtins",
        "f_globals",
        "f_locals",
        "gi_frame",
        "tb_frame",
    }
)

#: 直接调用即等于拿到逃逸能力的内置函数。
FORBIDDEN_CALLS: frozenset[str] = frozenset(
    {
        "__import__",
        "breakpoint",
        "compile",
        "delattr",
        "dir",
        "eval",
        "exec",
        "getattr",
        "globals",
        "input",
        "locals",
        "memoryview",
        "open",
        "setattr",
        "vars",
    }
)

#: 运行时注入给被 exec 代码的 builtins 白名单。
#
# ⚠️ 这里比"纯算术"宽：`__build_class__` 不给的话连 `class A: pass` 都会报
# `__build_class__ not found`；异常类型不给的话 `try/except` 全废。
# 生成一段连类都定义不了的代码，对用户毫无价值 —— 那不是安全，是残缺。
SAFE_BUILTINS: dict[str, object] = {}


def _build_safe_builtins() -> dict[str, object]:
    import builtins

    names = (
        # 常用函数
        "abs", "all", "any", "ascii", "bin", "bool", "bytearray", "bytes",
        "callable", "chr", "complex", "dict", "divmod", "enumerate", "filter",
        "float", "format", "frozenset", "hash", "hex", "int", "isinstance",
        "issubclass", "iter", "len", "list", "map", "max", "min", "next",
        "oct", "ord", "pow", "print", "range", "repr", "reversed", "round",
        "set", "slice", "sorted", "str", "sum", "tuple", "type", "zip",
        # OOP 必需
        "object", "property", "staticmethod", "classmethod", "super",
        "__build_class__",
        # 常量
        "True", "False", "None",
        # 异常类型：没有它们 try/except 完全不可用
        "ArithmeticError", "AssertionError", "AttributeError", "Exception",
        "IndexError", "KeyError", "LookupError", "NameError", "NotImplementedError",
        "OverflowError", "RecursionError", "RuntimeError", "StopIteration",
        "TypeError", "UnboundLocalError", "ValueError", "ZeroDivisionError",
    )
    out: dict[str, object] = {}
    for name in names:
        value = getattr(builtins, name, None)
        if value is not None:
            out[name] = value
    return out


SAFE_BUILTINS.update(_build_safe_builtins())


def parse_allowed_modules(raw: str | Iterable[str] | None) -> frozenset[str]:
    """把逗号分隔的配置解析成模块名集合。空/None 表示**沿用默认白名单**。

    刻意不做成"空 = 什么都不允许"：那会让一个手滑的环境变量
    把整个工具悄悄变成不可用，而错误信息只会说「不允许 import」。
    要真正收紧就用 `none`（显式）—— 让"关掉"必须是有意为之。
    """
    if raw is None:
        return DEFAULT_ALLOWED_MODULES
    if isinstance(raw, str):
        items = [part.strip() for part in raw.split(",")]
    else:
        items = [str(part).strip() for part in raw]
    items = [item for item in items if item]
    if not items:
        return DEFAULT_ALLOWED_MODULES
    if len(items) == 1 and items[0].lower() in ("none", "off", "false"):
        return frozenset()
    return frozenset(items)


def describe_policy(allowed_modules: Iterable[str]) -> str:
    modules = sorted(allowed_modules)
    if not modules:
        return "沙箱不允许 import 任何模块，只能使用内置函数。"
    return (
        "沙箱允许 import 这些标准库："
        + "、".join(modules)
        + "。不允许其它模块（尤其是 os/sys/subprocess/socket/ctypes/importlib）。"
    )


def make_restricted_import(allowed_modules: Iterable[str]):
    """造一个只放行白名单模块的 `__import__`。

    ⚠️ **必须要有它**：AST 校验只拦得住"写在源码里的 import"，
    而 `exec` 的 `import` 语句在运行时是靠 `builtins.__import__` 实现的。
    如果 builtins 里根本没有 `__import__`，那么**连白名单里的 `import math`
    都会失败**（报 `__import__ not found`）—— 校验放行、运行时又跑不了，
    用户会以为"程序坏了"。这一层让两边一致。
    """
    import builtins

    allowed = {m.split(".")[0] for m in allowed_modules}
    real_import = builtins.__import__

    def _restricted_import(name, globals=None, locals=None, fromlist=(), level=0):
        if level and level > 0:
            raise ImportError("沙箱不允许相对 import")
        if name.split(".")[0] not in allowed:
            raise ImportError(f"沙箱不允许 import「{name}」")
        return real_import(name, globals, locals, fromlist, level)

    return _restricted_import


def build_runtime_builtins(allowed_modules: Iterable[str]) -> dict[str, object]:
    """运行时注入的 builtins：白名单内置函数 + 受限 `__import__`。"""
    builtins_map = dict(SAFE_BUILTINS)
    builtins_map["__import__"] = make_restricted_import(allowed_modules)
    return builtins_map


def _root_name(node: ast.AST) -> str:
    """取表达式最左侧的名字：`a.b.c` → `a`。"""
    while isinstance(node, ast.Attribute):
        node = node.value
    return node.id if isinstance(node, ast.Name) else ""


def validate_code(
    code: str, allowed_modules: Iterable[str] = DEFAULT_ALLOWED_MODULES
) -> list[str]:
    """静态校验，返回**问题列表**（空列表 = 通过）。

    每条都带行号与可操作的说明 —— 「代码包含禁止的操作: open」这种
    没有行号、也不说该改用什么的报错，用户拿到之后只能靠猜。
    """
    allowed = {m.split(".")[0] for m in allowed_modules}
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        where = f"第 {exc.lineno} 行" if exc.lineno else "未知位置"
        return [f"{where}：语法错误 —— {exc.msg}。请给出完整、可解析的 Python 代码。"]

    issues: list[str] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                top = alias.name.split(".")[0]
                if top not in allowed:
                    issues.append(
                        f"第 {node.lineno} 行：不允许 import「{alias.name}」。"
                        f"{describe_policy(allowed)}"
                    )
        elif isinstance(node, ast.ImportFrom):
            # 相对 import（level > 0）在这个上下文里没有任何意义，直接拒
            if node.level and node.level > 0:
                issues.append(
                    f"第 {node.lineno} 行：不允许相对 import。"
                    f"{describe_policy(allowed)}"
                )
                continue
            top = (node.module or "").split(".")[0]
            if top not in allowed:
                issues.append(
                    f"第 {node.lineno} 行：不允许 import「{node.module}」。"
                    f"{describe_policy(allowed)}"
                )
        elif isinstance(node, ast.Attribute):
            if node.attr in FORBIDDEN_ATTRS:
                issues.append(
                    f"第 {node.lineno} 行：不允许访问属性「{node.attr}」——"
                    f"它可以绕过沙箱限制拿到任意对象。"
                )
        elif isinstance(node, ast.Call):
            func = node.func
            name = ""
            if isinstance(func, ast.Name):
                name = func.id
            elif isinstance(func, ast.Attribute):
                name = func.attr
            if name in FORBIDDEN_CALLS:
                issues.append(
                    f"第 {node.lineno} 行：不允许调用「{name}()」。"
                    + (
                        "要产出文件请改用 file_write 工具（它会把内容写到任务产物目录）。"
                        if name == "open"
                        else ""
                    )
                )
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            issues.append(f"第 {node.lineno} 行：不允许 {type(node).__name__.lower()} 声明。")

    # 去重（同一个属性在多行出现时保留每一条，但完全相同的只留一条）
    seen: set[str] = set()
    unique: list[str] = []
    for item in issues:
        if item not in seen:
            seen.add(item)
            unique.append(item)
    return unique[:8]
