"""代码沙箱：AST 校验的**该拦的拦、不该拦的不拦**。

这个文件存在的意义是钉死一次真实事故：旧实现用子串黑名单
（`if "import " in code`），结果

  * 正当代码全被拒：`from math import sqrt`、注释里写 `# import 说明`、
    字符串 `"请 import 该模块"` —— 实测 4/4 误伤；
  * 真逃逸却放行：`object.__subclasses__()` 不在名单里。

所以断言分两组：**误伤组**（必须放行）与**拦截组**（必须拒绝）。
只写"拦得住"是不够的 —— 一个把所有代码都拒掉的校验器也能通过那种测试。
"""

from __future__ import annotations

import pytest

from app.tools.code_sandbox import (
    DEFAULT_ALLOWED_MODULES,
    build_runtime_builtins,
    describe_policy,
    make_restricted_import,
    parse_allowed_modules,
    validate_code,
)


class TestAllowedCode:
    """不该拦的：这些是最普通的 Python，拦掉就等于"AI 写的代码没一个能用"。"""

    @pytest.mark.parametrize(
        "code",
        [
            "print(sum(range(10)))",
            "import math\nprint(math.sqrt(16))",
            "from statistics import mean\nprint(mean([1, 2, 3]))",
            "import json, re\nprint(json.dumps({'a': 1}))",
            "# import 说明：这里不导入\nprint(1)",
            'msg = "请 import 该模块"\nprint(len(msg))',
            "class Point:\n    def __init__(self, x):\n        self.x = x",
            "try:\n    1 / 0\nexcept ZeroDivisionError as e:\n    print(e)",
            'if __name__ == "__main__":\n    print("ok")',
            "print([x * 2 for x in range(3)])",
            "data = {'a': [1, 2]}\nprint(sorted(data.items()))",
        ],
    )
    def test_common_code_passes(self, code):
        assert validate_code(code, DEFAULT_ALLOWED_MODULES) == []


class TestBlockedCode:
    """该拦的：每一条都能突破沙箱或造成副作用。"""

    def test_import_outside_whitelist(self):
        issues = validate_code("import os\nprint(os.getcwd())", DEFAULT_ALLOWED_MODULES)
        assert issues and "os" in issues[0]
        # 报错要能指导下一步，而不是只说"不行"
        assert "允许" in issues[0]

    @pytest.mark.parametrize("module", ["sys", "subprocess", "socket", "ctypes", "importlib", "shutil"])
    def test_dangerous_modules_blocked(self, module):
        assert validate_code(f"import {module}", DEFAULT_ALLOWED_MODULES)

    def test_open_is_blocked_and_points_to_file_write(self):
        issues = validate_code("f = open('a.txt', 'w')", DEFAULT_ALLOWED_MODULES)
        assert issues
        assert "file_write" in issues[0], "拒绝 open 时必须告诉用户该改用什么"

    @pytest.mark.parametrize("code", ["print(getattr(object, 'x'))", "eval('1')", "exec('a=1')"])
    def test_dynamic_access_blocked(self, code):
        assert validate_code(code, DEFAULT_ALLOWED_MODULES)

    def test_class_chain_escape_blocked(self):
        """★ 旧黑名单漏掉的就是这一条：`().__class__.__bases__[0].__subclasses__()`。"""
        code = "subs = ().__class__.__bases__[0].__subclasses__()\nprint(len(subs))"
        issues = validate_code(code, DEFAULT_ALLOWED_MODULES)
        assert issues
        assert any("__subclasses__" in i for i in issues)

    def test_relative_import_blocked(self):
        assert validate_code("from . import x", DEFAULT_ALLOWED_MODULES)

    def test_syntax_error_reports_line(self):
        issues = validate_code("def f(:\n    pass", DEFAULT_ALLOWED_MODULES)
        assert issues
        assert "第 1 行" in issues[0] and "语法错误" in issues[0]

    def test_syntax_error_line_number_is_accurate(self):
        issues = validate_code("x = 1\ny = 2\ndef f(:\n    pass", DEFAULT_ALLOWED_MODULES)
        assert "第 3 行" in issues[0]

    def test_global_declaration_blocked(self):
        assert validate_code("def f():\n    global g\n    g = 1", DEFAULT_ALLOWED_MODULES)


class TestAllowedModuleConfig:
    def test_empty_means_default_not_nothing(self):
        """空配置**不能**变成"什么都不许" —— 一次手滑会让整个工具静默不可用。"""
        assert parse_allowed_modules("") == DEFAULT_ALLOWED_MODULES
        assert parse_allowed_modules(None) == DEFAULT_ALLOWED_MODULES
        assert parse_allowed_modules("   ") == DEFAULT_ALLOWED_MODULES

    def test_none_explicitly_disables(self):
        assert parse_allowed_modules("none") == frozenset()

    def test_custom_list(self):
        assert parse_allowed_modules("math, json") == frozenset({"math", "json"})

    def test_custom_list_is_actually_enforced(self):
        assert validate_code("import numpy", {"math"})
        assert validate_code("import math", {"math"}) == []


class TestRuntimeBuiltins:
    def test_whitelisted_import_works_at_runtime(self):
        """★ AST 放行 + 运行时跑不了 = 最坏的一种：校验过了，执行报错。

        `exec` 的 import 走 `builtins.__import__`，所以必须注入一个**受限但存在**的
        import；否则连白名单里的 `import math` 都报 `__import__ not found`。
        """
        import_ = make_restricted_import({"math"})
        assert import_("math").sqrt(9) == 3.0

    def test_non_whitelisted_import_raises(self):
        import_ = make_restricted_import({"math"})
        with pytest.raises(ImportError):
            import_("os")

    def test_relative_import_raises(self):
        with pytest.raises(ImportError):
            make_restricted_import({"math"})("math", None, None, (), 1)

    def test_builtins_has_no_open(self):
        builtins_map = build_runtime_builtins(DEFAULT_ALLOWED_MODULES)
        assert "open" not in builtins_map
        assert "__import__" in builtins_map

    def test_builtins_keeps_class_and_exception_support(self):
        """`__build_class__` / 异常类型缺了，class 与 try/except 全都不可用。"""
        builtins_map = build_runtime_builtins(DEFAULT_ALLOWED_MODULES)
        for name in ("__build_class__", "Exception", "ValueError", "object", "super"):
            assert name in builtins_map, name


def test_describe_policy_mentions_modules():
    text = describe_policy({"math", "json"})
    assert "math" in text and "json" in text

    empty = describe_policy(set())
    assert "不允许" in empty
