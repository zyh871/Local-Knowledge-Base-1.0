"""`code_executor` 的两条底线：**超时真的会停**、错误信息要能照着改。

## 为什么单测"超时"

旧实现是 `thread.join(timeout)` 之后报一句「执行超时」——
线程其实还在跑。那种实现能通过"返回了超时错误"的断言，
却完全没解决"死循环会永久占一个核"的问题（长驻服务里的资源泄漏）。

所以这里断言的**不是返回值**，而是**执行确实停下来了**：
用共享计数器观察线程有没有继续推进。
"""

from __future__ import annotations

import time

import pytest

from app.tools.code_executor import CodeExecutorTool, _execute_with_timeout
from app.tools.code_sandbox import DEFAULT_ALLOWED_MODULES


class TestRealTimeout:
    def test_infinite_loop_is_actually_stopped(self):
        """★ 断的是"线程真的被打断了"，不是"返回了超时字样"。

        判据：`variables` 是在 `finally` 里收集的。
        如果线程只是"不再等"（旧实现），返回时它还在跑，
        `variables` 必然是空的；只有异常真的被抛进那个线程、
        让它从死循环里退出并走完收尾，才会看到变量。

        顺带钉住 `_TimeoutError` 不再是死代码。
        """
        result = _execute_with_timeout(
            "i = 0\nwhile True:\n    i = i + 1", 1, DEFAULT_ALLOWED_MODULES
        )
        assert "超时" in (result["error"] or "")
        assert result["variables"], (
            "线程没有被真正打断（variables 为空 = 收尾代码没跑）—— "
            "只停止等待的实现会留下一个永久占核的线程"
        )
        # 计数器应当停在某个正数上，说明它确实跑过、也确实停了
        assert int(result["variables"]["i"]["value"]) > 0

    def test_loop_stops_soon_after_timeout(self):
        """超时后线程应当很快消失，而不是继续占着 CPU。"""
        import threading

        before = threading.active_count()
        start = time.time()
        _execute_with_timeout("t = 0\nwhile True:\n    t = t + 1", 1)
        elapsed = time.time() - start
        assert elapsed < 8, f"超时处理拖了 {elapsed:.1f}s"
        # 给被中断的线程一点收尾时间
        time.sleep(0.5)
        assert threading.active_count() <= before + 1

    def test_normal_code_unaffected(self):
        result = _execute_with_timeout("print(1 + 1)", 5)
        assert result["error"] is None
        assert result["stdout"].strip() == "2"


class TestErrorMessagesAreActionable:
    def test_whitelisted_import_runs(self):
        tool = CodeExecutorTool()
        result = tool.execute(code="import math\nprint(int(math.sqrt(144)))")
        assert result["success"] is True
        assert result["stdout"].strip() == "12"

    def test_open_error_points_to_file_write(self):
        tool = CodeExecutorTool()
        result = tool.execute(code="f = open('x.txt', 'w')")
        assert result["success"] is False
        assert "file_write" in result["error"]

    def test_import_error_lists_allowed_modules(self):
        tool = CodeExecutorTool()
        result = tool.execute(code="import os")
        assert result["success"] is False
        assert "允许" in result["error"]
        assert result["issues"]

    def test_class_and_main_guard_run(self):
        """生成脚本里最常见的两句 —— 它们跑不了的话「代码可用」就是空话。"""
        tool = CodeExecutorTool()
        code = (
            "class Counter:\n"
            "    def __init__(self):\n"
            "        self.n = 0\n"
            "    def add(self):\n"
            "        self.n += 1\n"
            "        return self.n\n"
            "if __name__ == '__main__':\n"
            "    c = Counter()\n"
            "    c.add()\n"
            "    print(c.add())\n"
        )
        result = tool.execute(code=code)
        assert result["success"] is True, result.get("error")
        assert result["stdout"].strip() == "2"

    def test_description_says_it_cannot_write_files(self):
        """工具描述要直说不能写文件，否则模型会一直拿它去写文件。"""
        assert "不能写文件" in CodeExecutorTool().description
        assert "file_write" in CodeExecutorTool().description
