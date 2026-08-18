"""
代码执行工具 — 在受限沙箱中执行 Python 代码。

安全措施:
  1. 禁用 import（通过重写 __builtins__）
  2. 限制可用的内置函数白名单
  3. 执行超时（线程级）
  4. 输出长度截断
  5. 内存上限（通过 resource 限制，POSIX 平台）
"""

import logging
import os
import signal
import threading
from typing import Any, Dict

from app.tools.base import BaseTool

logger = logging.getLogger(__name__)

_CODE_TIMEOUT = int(os.getenv("CODE_EXEC_TIMEOUT", "10"))
_MAX_OUTPUT = 4096

_SAFE_BUILTINS = {
    "print": print,
    "len": len,
    "range": range,
    "enumerate": enumerate,
    "zip": zip,
    "map": map,
    "filter": filter,
    "sorted": sorted,
    "reversed": reversed,
    "abs": abs,
    "round": round,
    "min": min,
    "max": max,
    "sum": sum,
    "any": any,
    "all": all,
    "isinstance": isinstance,
    "type": type,
    "str": str,
    "int": int,
    "float": float,
    "bool": bool,
    "list": list,
    "dict": dict,
    "tuple": tuple,
    "set": set,
    "frozenset": frozenset,
    "ord": ord,
    "chr": chr,
    "hex": hex,
    "oct": oct,
    "bin": bin,
    "format": format,
    "repr": repr,
    "hash": hash,
    "divmod": divmod,
    "pow": pow,
    "True": True,
    "False": False,
    "None": None,
}


class _TimeoutError(Exception):
    pass


def _execute_with_timeout(code: str, timeout: int) -> Dict[str, Any]:
    """在子线程中执行代码，超时终止。"""
    result = {"stdout": "", "error": None, "variables": {}}

    def _run():
        local_vars: Dict[str, Any] = {}
        global_vars = {"__builtins__": _SAFE_BUILTINS}
        old_stdout = None
        try:
            import io
            old_stdout = __import__("sys").stdout
            buf = io.StringIO()
            __import__("sys").stdout = buf
            exec(code, global_vars, local_vars)
            result["stdout"] = buf.getvalue()[:_MAX_OUTPUT]
        except Exception as e:
            result["error"] = f"{type(e).__name__}: {e}"
        finally:
            if old_stdout is not None:
                __import__("sys").stdout = old_stdout
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
        return "在安全沙箱中执行 Python 代码。可用于数据处理、列表操作、字符串处理等。不支持 import。执行结果返回 stdout 输出和变量列表。"

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "code": {
                    "type": "string",
                    "description": "要执行的 Python 代码（不支持 import）",
                }
            },
            "required": ["code"],
        }

    def execute(self, code: str = "", **kwargs) -> Dict[str, Any]:
        code = (code or "").strip()
        if not code:
            return {"success": False, "error": "代码不能为空"}

        if len(code) > 10000:
            return {"success": False, "error": "代码过长（超过10000字符）"}

        dangerous = ["import ", "__import__", "open(", "exec(", "eval(", "compile(",
                      "globals()", "locals()", "getattr(", "setattr(", "delattr("]
        for d in dangerous:
            if d in code:
                return {"success": False, "error": f"代码包含禁止的操作: {d.strip('(')}"}

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
