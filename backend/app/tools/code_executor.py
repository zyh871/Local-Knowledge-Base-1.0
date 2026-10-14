"""
代码执行工具 — 在受限沙箱中执行 Python 代码。

安全措施:
  1. **AST 校验**（`code_sandbox.validate_code`）：拦 import（白名单外）、
     危险调用与逃逸属性，报错带行号。
  2. 受限 `__builtins__`：只注入白名单内置函数 + 只放行白名单模块的 `__import__`。
  3. **真超时**：超时后向工作线程抛异常，而不是只停止等待。
  4. 输出长度截断。

⚠️ 这**不是**安全边界：它防的是"模型写错/写出跑不通的代码"，
防不了"有人故意攻击"。要在多用户/对外环境暴露，必须换成
子进程 + 资源限制 + 只读文件系统。
"""

import ctypes
import io
import logging
import os
import threading
from typing import Any, Dict

from app.tools.base import BaseTool
from app.tools.code_sandbox import (
    DEFAULT_ALLOWED_MODULES,
    build_runtime_builtins,
    describe_policy,
    parse_allowed_modules,
    validate_code,
)

logger = logging.getLogger(__name__)

_CODE_TIMEOUT = int(os.getenv("CODE_EXEC_TIMEOUT", "10"))
#: 允许 import 的标准库白名单（逗号分隔；`none` = 完全禁止 import）。
#: 空/未设置 → 用默认白名单。见 code_sandbox.DEFAULT_ALLOWED_MODULES。
_ALLOWED_MODULES = parse_allowed_modules(os.getenv("CODE_EXEC_ALLOWED_MODULES"))

_MAX_OUTPUT = 4096
_MAX_CODE_CHARS = 10000
#: 抛完异步异常之后，再给线程多久收尾。收尾不了就如实报告，不假装成功。
_ABORT_GRACE_SECONDS = 2.0


class _TimeoutError(Exception):
    """在超时的工作线程里抛出，用于把它从死循环里拽出来。"""


def _abort_thread(thread_ident: int) -> bool:
    """向指定线程注入 `_TimeoutError`，让它自己停下来。

    为什么必须这么做：`thread.join(timeout)` **只是不再等待**，
    线程还在跑。旧实现据此报「执行超时」，实际上是"我不管了"——
    daemon 线程会继续占着一个核，在长驻服务里就是资源泄漏。
    `PyThreadState_SetAsyncExc` 是 CPython 提供的标准手段。
    """
    try:
        res = ctypes.pythonapi.PyThreadState_SetAsyncExc(
            ctypes.c_ulong(thread_ident), ctypes.py_object(_TimeoutError)
        )
    except Exception as exc:  # noqa: BLE001 - 平台不支持时如实降级
        logger.warning(f"沙箱超时终止失败（{exc}），该次执行将无法被打断")
        return False
    if res > 1:
        # 返回值 > 1 表示影响到了多个线程 —— 必须回滚，否则会误伤别的线程
        ctypes.pythonapi.PyThreadState_SetAsyncExc(ctypes.c_ulong(thread_ident), None)
        logger.warning("沙箱超时终止命中多个线程，已回滚")
        return False
    return res == 1


def _execute_with_timeout(
    code: str, timeout: int, allowed_modules: Any = None
) -> Dict[str, Any]:
    """在子线程中执行代码，超时后**真的打断它**。"""
    allowed = (
        _ALLOWED_MODULES if allowed_modules is None else allowed_modules
    )
    result: Dict[str, Any] = {"stdout": "", "error": None, "variables": {}}

    def _run():
        local_vars: Dict[str, Any] = {}
        # 输出重定向走**局部注入**，而不是替换 `sys.stdout`：
        # 后者是进程级全局，并发请求会互相踩（实测 12/12 轮出现一侧丢失、
        # 换编排后还会串台）。局部注入让每个请求有自己的 buffer，天然隔离。
        # 详见 code_sandbox._make_redirected_print 的说明。
        buf = io.StringIO()
        builtins_map = build_runtime_builtins(allowed, print_target=buf)
        # `__name__` 必须给：`exec` 不会自动带上它，而
        # `if __name__ == "__main__":` 是生成脚本里最常见的一句 ——
        # 不给就报 `NameError: name '__name__' is not defined`，
        # 用户会以为"AI 写的代码是错的"。
        global_vars = {
            "__builtins__": builtins_map,
            "__name__": "__main__",
            # 两层都注入：`__builtins__['print']` 这类取法也要被重定向，
            # 否则那条路仍会写真实终端（输出逃逸、不被捕获）。
            "print": builtins_map["print"],
        }
        try:
            exec(code, global_vars, local_vars)
            result["stdout"] = buf.getvalue()[:_MAX_OUTPUT]
        except _TimeoutError:
            result["error"] = f"执行超时（{timeout}秒），已终止"
        except Exception as e:
            result["error"] = f"{type(e).__name__}: {e}"
        finally:
            for k, v in local_vars.items():
                if not k.startswith("_"):
                    try:
                        repr(v)[:200]
                        result["variables"][k] = {
                            "type": type(v).__name__,
                            "value": repr(v)[:200],
                        }
                    except Exception:
                        pass

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    thread.join(timeout=timeout)

    if thread.is_alive():
        aborted = _abort_thread(thread.ident or 0)
        thread.join(timeout=_ABORT_GRACE_SECONDS)
        if thread.is_alive():
            # 还在跑（多半卡在 C 层阻塞调用里）—— 不能假装已经停了
            result["error"] = (
                f"执行超过 {timeout} 秒，且无法中断（可能卡在阻塞式调用上）。"
                f"该线程仍在后台运行。"
            )
            result["stdout"] = ""
        elif not aborted:
            result["error"] = f"执行超时（{timeout}秒）"
            result["stdout"] = ""

    return result


class CodeExecutorTool(BaseTool):
    """沙箱 Python 代码执行器。"""

    @property
    def name(self) -> str:
        return "code_executor"

    @property
    def description(self) -> str:
        return (
            "在受限沙箱中执行 Python 代码，用于真正的计算或数据处理。"
            f"{describe_policy(_ALLOWED_MODULES)}"
            "**不能写文件**（要产出文件请用 file_write 工具）。"
            "执行结果返回 stdout 与变量列表。"
        )

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "code": {
                    "type": "string",
                    "description": (
                        "要执行的 Python 代码。可用模块："
                        + ("、".join(sorted(_ALLOWED_MODULES)) or "（无）")
                    ),
                }
            },
            "required": ["code"],
        }

    def execute(self, code: str = "", **kwargs) -> Dict[str, Any]:
        code = (code or "").strip()
        if not code:
            return {"success": False, "error": "代码不能为空"}

        if len(code) > _MAX_CODE_CHARS:
            return {
                "success": False,
                "error": f"代码过长（{len(code)} 字符，超过 {_MAX_CODE_CHARS} 上限）",
            }

        issues = validate_code(code, _ALLOWED_MODULES)
        if issues:
            # 逐条给出「第几行、为什么、改用什么」—— 旧的「代码包含禁止的操作: open」
            # 既没有行号也不说替代方案，用户只能靠猜。
            return {
                "success": False,
                "error": "代码未通过沙箱校验：\n" + "\n".join(f"· {i}" for i in issues),
                "issues": issues,
            }

        try:
            result = _execute_with_timeout(code, _CODE_TIMEOUT)
            success = result["error"] is None
            return {
                "success": success,
                "result": result["stdout"] if success else result["error"],
                "stdout": result["stdout"],
                "variables": result.get("variables", {}),
            }
        except Exception as e:
            logger.error(f"代码执行内部错误: {e}")
            return {"success": False, "error": str(e)}


__all__ = [
    "CodeExecutorTool",
    "_execute_with_timeout",
    "DEFAULT_ALLOWED_MODULES",
]
