"""代码沙箱逃逸的**回归测试**（安全审计 F-001 / F-002）。

## 这些测试在修复前是**失败**的 —— 这是它们的 purpose

2026-10-14 上线前安全审计发现沙箱可被完全绕过。这里每个用例都**曾经真实成功过**
（PoC 实跑复现：读到 `os` 模块、执行 `os.system` 写盘、列出全部密钥型环境变量）。
修复之后它们必须全部转绿。**若你在修复前看到一片红，那是正确状态，不是测试坏了。**

## 为什么必须断言「两层」而不是只测静态校验

审计过程中确认了一件事：静态层和运行时层是**两次独立的失守**，只测一层会漏掉
真正危险的那个组合 ——「静态放行 + 运行时也放行」。

* `validate_code()` 是 AST 静态校验（`code_sandbox.py:245`）
* `_execute_with_timeout()` 是真实 `exec` 执行（`code_executor.py:72`）

F-001 的 PoC 在这两层**同时失守**：`import uuid` 通过了白名单检查，
`uuid.os` 又不在 `FORBIDDEN_ATTRS` 里，于是执行时真的拿到了 `os`。
只测 `validate_code` 会漏掉「静态过了但运行时照样跑」这一类。

## ⚠️ 为什么哨兵用「文件」而不是 stdout（踩过的坑）

审计里实测确认：`_execute_with_timeout` 用 `StringIO` 接管 `sys.stdout`，
但 `os.system('echo MARKER')` 是**直接写 fd 1**，完全绕过这个重定向：

    >>> _execute_with_timeout("import uuid; uuid.os.system('echo LEAK')", 10)
    LEAK_TEST_MARKER          ← 打印在真实终端上
    {'stdout': '', 'error': None}   ← 但捕获到的 stdout 是空字符串

也就是说，如果按「stdout 里不该出现 marker」来断言，**逃逸成功时这个断言照样通过**
（因为 stdout 本来就是空的）—— 一个永远绿的假阴性测试比没有测试更危险。

所以这里用**文件系统哨兵**：让 payload 真的去创建一个临时文件，
断言「文件不存在」。文件创建是进程外的、不可被 stdout 重定向掩盖的副作用。

## 那个「不依赖具体 payload」的通用哨兵

`TestNoWhitelistedModuleLeaksDangerousRefs` 不写死任何 payload，
而是**遍历当前白名单里的每一个模块**，断言它没有暴露危险引用。

这是为了自动捕获**未来**新增的逃逸路径：现在只需要人工发现 `uuid`/`enum`，
但以后有人往白名单里加一个 `zoneinfo` 或别的什么，测试会自动变红。
只锁死当前三个 payload 的测试做不到这一点。
"""

from __future__ import annotations

import os
import sys

import pytest

from app.tools.code_executor import _execute_with_timeout
from app.tools.code_sandbox import (
    DEFAULT_ALLOWED_MODULES,
    make_restricted_import,
    validate_code,
)

#: 绝不允许被沙箱代码拿到的模块引用。
#:
#: 判定标准：这些模块里的任意一个能拿到，就等于沙箱不存在。
DANGEROUS_REFS = (
    "os",
    "sys",
    "subprocess",
    "shutil",
    "socket",
    "ctypes",
    "importlib",
    "builtins",
    "pickle",
    "marshal",
    "multiprocessing",
)


# ===========================================================================
# 通用哨兵：不依赖具体 payload，自动覆盖未来新增的模块
# ===========================================================================


class TestNoWhitelistedModuleLeaksDangerousRefs:
    """★ 与 F-001 的根因正面对撞。

    F-001 的机制是：白名单里的某些标准库模块在 CPython 内部 `import os/sys`，
    于是**模块对象上直接挂着了 `os` / `sys` 属性**。`import uuid` 通过了白名单检查，
    `uuid.os` 又不在 `FORBIDDEN_ATTRS` 黑名单里 —— 两道闸门同时放行。

    修复可以是「把这些模块移出白名单」，也可以是「导入后剥离它们的危险属性」。
    本测试对两种修法都成立：**只看结果，不关心怎么修的**。
    """

    @pytest.mark.parametrize("module_name", sorted(DEFAULT_ALLOWED_MODULES))
    def test_module_has_no_dangerous_attribute(self, module_name):
        try:
            module = __import__(module_name)
        except ImportError:
            pytest.skip(f"{module_name} 在当前解释器不可用")

        leaked = [name for name in DANGEROUS_REFS if hasattr(module, name)]
        assert not leaked, (
            f"沙箱白名单模块 {module_name!r} 暴露了危险引用 {leaked}。"
            f"被沙箱代码 import 后即可拿到它们（例：`import {module_name}` 后"
            f"`{module_name}.{leaked[0]}`），沙箱边界等于不存在。"
            f"修复：把该模块移出 DEFAULT_ALLOWED_MODULES，"
            f"或在 make_restricted_import() 里剥离这些属性。"
        )

    def test_restricted_import_also_hides_them(self):
        """受限 import 这条路同样不能漏。

        `make_restricted_import` 是 `exec` 里 import 语句的实际入口。
        只在 AST 层拦住、而运行时 import 仍能拿到模块，等于没修。
        """
        import_ = make_restricted_import(DEFAULT_ALLOWED_MODULES)
        for module_name in ("uuid", "enum", "fractions", "statistics", "typing"):
            if module_name not in DEFAULT_ALLOWED_MODULES:
                continue
            module = import_(module_name)
            leaked = [name for name in DANGEROUS_REFS if hasattr(module, name)]
            assert not leaked, (
                f"make_restricted_import({module_name!r}) 返回的模块仍暴露 {leaked}"
            )


# ===========================================================================
# 已复现的逃逸 payload —— 静态层
# ===========================================================================

#: 审计中实测通过的 payload。注释里是**真实的成功输出**，不是设想。
ESCAPE_PAYLOADS = {
    # 实测 stdout: 'C:\\Users\\a1731\\Desktop\\...\\backend'
    "uuid_os_getcwd": "import uuid\nprint(uuid.os.getcwd())",
    # 实测: uuid.os.system('echo MARKER') 在宿主终端真实打印了 MARKER
    "uuid_os_system": "import uuid\nuuid.os.system('echo ESCAPE_MARKER')",
    # 实测 stdout: 'True'（拿到了 subprocess 模块对象）
    "enum_sys_modules": "import enum\nprint(enum.sys.modules.get('subprocess'))",
    # 实测 stdout: ['BOCHA_API_KEY', 'DASHSCOPE_API_KEY', ...]（全部密钥型变量名）
    "uuid_env_exfiltration": (
        "import uuid\n"
        "print([k for k in uuid.os.environ if 'KEY' in k or 'TOKEN' in k])"
    ),
    # 实测: uuid.os.popen('echo X').read() 返回 'X'
    "uuid_popen": "import uuid\nprint(uuid.os.popen('echo ESCAPE_MARKER').read())",
    # 实测: 可列出任意目录（backend/ 下含 .env）
    "uuid_listdir": "import uuid\nprint(sorted(uuid.os.listdir('.'))[:5])",
    # 实测: fractions / statistics / typing 同样挂着 sys
    "fractions_sys": "import fractions\nprint(fractions.sys.version_info[:2])",
    "statistics_sys": "import statistics\nprint(statistics.sys.maxsize > 0)",
    "typing_sys": "import typing\nprint(typing.sys.platform)",
}


class TestEscapePayloadsBlockedStatically:
    """静态层：`validate_code()` 必须拒绝每一个逃逸 payload。"""

    @pytest.mark.parametrize("name", sorted(ESCAPE_PAYLOADS))
    def test_static_rejects(self, name):
        issues = validate_code(ESCAPE_PAYLOADS[name], DEFAULT_ALLOWED_MODULES)
        assert issues, (
            f"{name} 通过了 AST 校验。"
            f"沙箱被绕过时用户看到的是「校验过了但执行报错」或更糟——直接执行成功。"
            f"payload: {ESCAPE_PAYLOADS[name]!r}"
        )


# ===========================================================================
# 已复现的逃逸 payload —— 运行时层（真正的安全断言）
# ===========================================================================


class TestEscapePayloadsBlockedAtRuntime:
    """★ 运行时层：真正 `exec` 一遍，断言**副作用没有发生**。

    这是真正闭环的一层。只测 `validate_code` 会漏掉
    「静态放行 + 运行时也放行」这个组合 —— 而那正是 F-001 的实际形态。
    """

    @pytest.mark.parametrize("name", sorted(ESCAPE_PAYLOADS))
    def test_runtime_does_not_execute(self, name):
        code = ESCAPE_PAYLOADS[name]
        # 静态层先过一遍：静态都拦不住时，运行时断言单独失败会让定位变困难
        result = _execute_with_timeout(code, 10, DEFAULT_ALLOWED_MODULES)
        assert result["error"] is not None, (
            f"{name} 在沙箱里**执行成功**了（error=None）。"
            f"payload 拿到了它不该拿到的能力。"
        )

    def test_filesystem_sentinel_not_created(self, tmp_path):
        """★ 用真实的文件副作用做判据，而不是 stdout。

        原因见模块 docstring：`os.system` 写 fd 1，绕过 `sys.stdout` 重定向，
        所以 `result['stdout']` 在逃逸成功时反而是**空的**。
        只看 stdout 会得到一个永远绿的假阴性测试。

        文件创建是进程外副作用，没法被 stdout 重定向掩盖 —— 这才是可靠判据。

        ⚠️ 这里踩过两个坑，都写进注释了，因为它们都会让本测试**假通过**：

        1. Windows 路径的反斜杠必须转成正斜杠。直接插 `C:\\Users\\...` 进
           Python 源码字符串，`\\U` 会被解释成 unicode 转义 →
           `SyntaxError: truncated \\UXXXXXXXX escape` → payload 根本没跑，
           测试因为「文件不存在」而通过 —— 但那是语法错误，不是沙箱拦住了它。
        2. `os.system` 走 cmd.exe，**cmd.exe 不把单引号当引号**。
           用 `'...'` 包路径会生成一个名字里带引号的文件，判据同样失效。

        所以：一律用 `.as_posix()` + 双引号。
        """
        marker = tmp_path / "pwned.txt"
        # 正斜杠：避免 \U 被当成 unicode 转义
        target = marker.as_posix()
        assert "\\" not in target, "路径含反斜杠，payload 会被转义破坏"

        code = (
            "import uuid\n"
            f'uuid.os.system("echo pwned > \\"{target}\\"")\n'
            "print('done')"
        )
        # 先自检：payload 本身必须是合法 Python，否则测试会假通过
        import ast as _ast

        _ast.parse(code)  # 语法错会直接抛，不会静默通过

        result = _execute_with_timeout(code, 10, DEFAULT_ALLOWED_MODULES)

        assert not marker.exists(), (
            f"沙箱代码在宿主磁盘上创建了 {marker} —— "
            f"任意文件写入已成立。stdout={result.get('stdout')!r}"
        )
        assert result["error"] is not None, (
            f"payload 执行成功（error=None），stdout={result.get('stdout')!r}"
        )

    def test_sentinel_would_detect_a_real_escape(self, tmp_path):
        """★ 元测试：证明上面那个哨兵**确实能抓到逃逸**，而不是恒绿。

        这一条是专门为「测试本身有 bug」写的。实测中本文件第一版的哨兵
        因为 payload 存在语法错误而**假通过** —— 一个恒绿的测试比没有测试更危险。

        做法：先用未经保护的 `os.system` 直接跑一遍同样的 payload，
        断言 marker **确实被创建了**。若这条失败，说明哨兵判据本身有问题
        （路径转义 / 引号 / 临时目录权限），先修判据再谈沙箱。
        """
        marker = tmp_path / "control.txt"
        target = marker.as_posix()
        code = f'import os\nos.system("echo pwned > \\"{target}\\"")\nprint(1)'

        # 直接用受限 import 放行 os，模拟"沙箱没拦住"的情形
        import_ = make_restricted_import(DEFAULT_ALLOWED_MODULES | {"os"})
        namespace: dict = {
            "__builtins__": __builtins__ if not isinstance(__builtins__, dict)
            else {**__builtins__, "__import__": import_}
        }
        exec(code, namespace)  # noqa: S102 - 元测试，故意执行

        assert marker.exists(), (
            "哨兵判据本身失效：未受保护的 os.system 都没能创建 marker。"
            "说明是判据写错了（路径转义/引号/权限），"
            "此时上面那些断言即使全绿也没有意义。"
        )

    def test_environment_is_not_readable(self):
        """环境变量里放着 ZHIPU / TAVILY / BOCHA 等全部服务密钥。

        PoC 实测能列出所有含 KEY / TOKEN 的变量名，并读出单个密钥的长度与首尾。
        这里断言拿不到任何变量 —— 注意用「一个都拿不到」而不是「拿不到某个具体的」，
        否则环境里恰好没配某个变量时测试会假绿。
        """
        code = (
            "import uuid\n"
            "leaked = [k for k in uuid.os.environ "
            "if 'KEY' in k.upper() or 'SECRET' in k.upper() or 'TOKEN' in k.upper()]\n"
            "print('LEAKED_COUNT', len(leaked))"
        )
        result = _execute_with_timeout(code, 10, DEFAULT_ALLOWED_MODULES)

        assert result["error"] is not None, "payload 执行成功"
        assert "LEAKED_COUNT 0" not in (result["stdout"] or ""), (
            f"沙箱代码读到了 {result['stdout']!r} —— 环境变量可被枚举"
        )

    def test_reads_are_denied_not_silently_empty(self):
        """拒绝必须是**显式报错**，不是静默返回空。

        静默失败会让人误以为「沙箱只是查不到东西」，
        而真实情况是「这里本该有东西但被挡住了」—— 两种情况的可信度完全不同。
        """
        result = _execute_with_timeout(
            "import uuid\nprint(uuid.os.listdir('.'))", 10, DEFAULT_ALLOWED_MODULES
        )
        assert result["error"] is not None, (
            f"listdir 执行成功并返回 {result['stdout']!r}"
        )


# ===========================================================================
# 反向保护：修 F-001 不能把正常代码一起拦掉
# ===========================================================================


class TestFixDoesNotBreakLegitimateCode:
    """修白名单很容易顺手拦过头。这里钉住「不该拦的仍然放行」。

    没有这一组，一个「把所有 import 都拒掉」的修复也能让上面全绿 ——
    那不是修复，是把功能删了。
    """

    @pytest.mark.parametrize(
        "code",
        [
            "print(sum(range(10)))",
            "import math\nprint(int(math.sqrt(16)))",
            "import json\nprint(json.dumps({'a': 1}))",
            "import re\nprint(bool(re.match(r'a', 'abc')))",
            "import datetime\nprint(datetime.date(2026, 1, 1).year)",
            "import collections\nprint(collections.Counter('abca').most_common(1))",
            "class Point:\n    def __init__(self, x):\n        self.x = x\nprint(Point(1).x)",
            "try:\n    1 / 0\nexcept ZeroDivisionError as e:\n    print(type(e).__name__)",
        ],
    )
    def test_normal_code_still_runs(self, code):
        result = _execute_with_timeout(code, 10, DEFAULT_ALLOWED_MODULES)
        assert result["error"] is None, (
            f"正常代码被误拦：{code!r} -> {result['error']!r}"
        )


# ===========================================================================
# F-002：工具调用必须在注册层有审批感知
# ===========================================================================


class TestToolRegistryApprovalBoundary:
    """★ F-002：`ToolRegistry.execute()` 目前对审批**完全无感知**。

    审计实测：`registry.execute("code_executor", code="print(2)")` 返回
    `success=True, stdout='2'` —— 无任何审批即真实执行。

    工作流路径（`workflow_service.py:967`）有服务端强制门禁，
    但 `/ask` 的 Agent 路径（`react_agent.py:152`、`function_calling.py:176`）
    直接调这个注册表，形成一条**无门禁的平行链路**。

    注意 `file_write` 会被「缺少写入上下文」挡住 —— 那是**副作用式的偶然阻断**，
    不是审批机制。所以真正裸奔的是 `code_executor`。

    这些断言在修复前会失败。修复方式建议把门禁下沉到 `ToolRegistry.execute()` ——
    那是两条路径唯一的汇合点，在两个调用点各打补丁会漏掉第三条新路径。
    """

    def test_registry_reports_gated_tools(self):
        """注册层应当能说出「哪些工具需要审批」。"""
        from app.tools.base import get_registry

        registry = get_registry()
        assert hasattr(registry, "gated_tools"), (
            "ToolRegistry 缺少 gated_tools 之类的声明，"
            "调用方无法知道哪些工具必须先过审批。"
            "修复：给注册表加危险工具声明，并在 execute() 里强制。"
        )

    def test_execute_without_approval_context_is_denied(self):
        """无审批上下文时，危险工具必须被拒 —— 而不是真的执行。"""
        from app.tools.base import get_registry

        registry = get_registry()
        if not getattr(registry, "gated_tools", None):
            pytest.fail(
                "ToolRegistry 未声明 gated_tools，无法验证审批边界（F-002 未修复）"
            )

        for tool in registry.gated_tools:
            result = registry.execute(tool, code="print(1)", path="x.py", content="x")
            assert result.get("success") is not True, (
                f"工具 {tool!r} 在无审批上下文下执行成功：{result!r}。"
                f"任何调用方（含 /ask Agent 路径）都能借此执行危险工具。"
            )

    def test_tool_names_filter_excludes_gated_tools(self):
        """`tool_names` 显式指定时，过滤逻辑必须真的生效。

        `function_calling.py:128-129` 在 `tool_names=None` 时不过滤 ——
        于是 LLM 可见全部工具。作为第二道护栏，这里验证「传了列表时确实按列表过滤」。
        """
        from app.agents.function_calling import _run_native_fc
        from app.tools.base import get_registry

        registry = get_registry()
        gated = set(getattr(registry, "gated_tools", None) or ())
        if not gated:
            pytest.skip("注册表未声明 gated_tools（F-002 未修复），跳过过滤断言")

        # 只检查过滤这一段逻辑，不真正跑 LLM
        import inspect

        src = inspect.getsource(_run_native_fc)
        assert "tool_names" in src, "过滤逻辑被移除，tool_names 参数已失效"
        assert "schemas = [s for s in schemas if s[\"name\"] in tool_names]" in src, (
            "tool_names 过滤逻辑不再对 schemas 生效 —— "
            "即使上层传参正确，LLM 也会看到全部工具"
        )
