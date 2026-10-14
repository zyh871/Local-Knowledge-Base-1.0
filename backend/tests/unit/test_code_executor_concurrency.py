"""F-010：`code_executor` 并发下的 `sys.stdout` 全局竞态（安全审计，🔴 CRITICAL）。

## 缺陷是什么

`_execute_with_timeout`（`app/tools/code_executor.py:95-97, 104-106`）用
**进程级全局替换**捕获输出：

```python
old_stdout = sys.stdout        # 捕获「此刻」的全局
buf = io.StringIO()
sys.stdout = buf               # 覆盖「全局」
exec(code, global_vars, local_vars)
...
finally:
    sys.stdout = old_stdout    # 恢复「捕获时的那个」
```

**捕获与恢复之间隔着整个 `exec`**。这段时间里若另一个线程改变了
`sys.stdout`，`finally` 写回去的就是**一个已经被别人丢弃的中间 buf**。

实测后果（修复前12/12 命中）：

```
A_got: ""                                ← A 什么都没拿到
B_got: "A_PRIVATE_PAYROLL_123456\n"      ← B 拿到的是 A 的内容
sys.stdout is not original                ← 全局永久损坏
```

## 为什么按「不变量」写断言，而不是枚举故障形态

同一个竞态在不同交错下有三种表现（QA lead 独立发现其中两种，我复现确认）：

| # | 形态 | 触发编排 | 表现 |
|---|---|---|---|
| 1 | 串写型 | A 的 print 在 B 进入**之后** | `A=''` `B='AAA'` |
| 2 | 输出全丢型 | A 先 print 但仍占用窗口 | `B=''`，零串写 |
| 3 | 全局残留型 | A 先进入先完成 | 输出全对，**但全局已损坏** |

**形态 3 最阴险** —— 输出看起来完全正常，只有断言全局状态才能抓到。
按形态枚举容易漏（形态 2 就是因为只测了一种编排才被发现的），
所以下面只断言三条**不变量**，它们是所有形态的推论。

## ⚠️ 关于「全局无污染」这条断言的适用边界（重要，别误读）

`test_no_global_pollution` 断言 `sys.stdout is ORIGINAL_STDOUT`。

**如果修复方改走子进程化，这条断言会变成恒真，失去鉴别力** ——
子进程有自己的 stdout，父进程本来就不受影响，于是「真的修好了」与
「这段代码根本没被执行到」无法区分。

QA lead 已实测确认这一点（`subproc_parent_untouched: true`）。

所以：
* 走 **print 注入** → 三条断言都有效，本文件全部适用
* 走 **子进程化** → 本文件第1 条失效，**鉴别力全部落在第 2、3 条上**

不要因为第 1 条通过就认为 F-010 已修。

## ⚠️ 两个会导致假绿/假红的坑（都实测踩过）

**坑 1：exec 太快，挤不进重叠窗口。**
`print` 在 exec 里只需微秒级，两个线程很容易**串行**跑完而完全不重叠 ——
此时各自独立，**看起来一切正常，测试全绿**。
所以下面所有并发用例都用 `time.sleep()` 强制拉长 exec 窗口。

**坑 2：用 `sys.__stdout__` 检测残留。**
pytest 默认 capture 模式下 `sys.stdout` 已被 pytest 替换，
`sys.stdout is sys.__stdout__` 的结果取决于 pytest 的捕获状态，
**不是可靠的判据**。必须比对模块级捕获的 `ORIGINAL_STDOUT`。

（实测：`pytest -s` 下 `is sys.__stdout__` 为 True；默认 capture 模式下 `sys.stdout`
已被替换。）

**另外**：本文件每个并发用例都在 `finally` 里恢复 `sys.stdout`。
不恢复的话，一次失败会污染后续所有测试的输出 —— 排查时完全失明
（复现本缺陷时 PoC 脚本自己的汇总输出被吞，`exit=0` 但统计消失）。
"""

from __future__ import annotations

import io
import sys
import threading

import pytest

from app.tools.code_executor import _execute_with_timeout

# ★ 必须在任何执行之前捕获。不能用 sys.__stdout__，理由见模块 docstring。
ORIGINAL_STDOUT = sys.stdout

#: 强制拉长 exec 窗口的休眠时长。太短（<0.1s）可能挤不进重叠窗口。
SLEEP = "0.25"


@pytest.fixture(autouse=True)
def _restore_stdout():
    """任何用例失败都不能污染后续测试的输出。"""
    yield
    sys.stdout = ORIGINAL_STDOUT


def _run_concurrently(code_a: str, code_b: str, delay_b: float = 0.08):
    """并发跑两段代码，返回 (结果A, 结果B, 结束后 stdout 是否被污染)。

    编排要点：`delay_b` 让 B 晚一点进入，使两个 exec 阶段重叠。
    """
    results: dict = {}

    def run_a():
        results["A"] = _execute_with_timeout(code_a, 10)

    def run_b():
        import time

        time.sleep(delay_b)
        results["B"] = _execute_with_timeout(code_b, 10)

    ta = threading.Thread(target=run_a)
    tb = threading.Thread(target=run_b)
    ta.start()
    tb.start()
    ta.join()
    tb.join()
    return results["A"], results["B"], sys.stdout is not ORIGINAL_STDOUT


class TestConcurrentStdoutIsolation:
    """F-010 的核心：并发下 stdout 捕获必须互相隔离。"""

    def test_no_cross_talk_between_requests(self):
        """不变量 2：任何方向的输出都不得出现在对方的响应里。

        覆盖形态 1（串写型）与形态 2（全丢型）。
        """
        code = f"import time\ntime.sleep({SLEEP})\nprint('{{}}')"
        res_a, res_b, _ = _run_concurrently(code.format("AAA"), code.format("BBB"))

        assert "AAA" not in (res_b.get("stdout") or ""), (
            f"A 的输出泄漏进了 B 的响应：B 拿到 {res_b.get('stdout')!r}。"
            f"这是跨请求数据污染。"
        )
        assert "BBB" not in (res_a.get("stdout") or ""), (
            f"B 的输出泄漏进了 A 的响应：A 拿到 {res_a.get('stdout')!r}"
        )

    def test_both_requests_get_their_own_output(self):
        """不变量 2 的加强：不仅要不串台，各自的输出还得真的拿到。

        只断言「不串台」是不够的 —— 形态 2（全丢型）零串写，
        但双方的输出全丢，两条「不在对方响应里」都会通过。
        """
        code = f"import time\ntime.sleep({SLEEP})\nprint('{{}}')"
        res_a, res_b, _ = _run_concurrently(code.format("AAA"), code.format("BBB"))

        assert "AAA" in (res_a.get("stdout") or ""), (
            f"A 丢了自己的输出，实际拿到 {res_a.get('stdout')!r}（静默失效）"
        )
        assert "BBB" in (res_b.get("stdout") or ""), (
            f"B 丢了自己的输出，实际拿到 {res_b.get('stdout')!r}（静默失效）"
        )

    def test_no_global_pollution(self):
        """不变量 1：全局 stdout 必须回到原对象。

        ⚠️ **仅当修复走 print 注入方案时有效。** 子进程化下此断言恒真，
        失去鉴别力 —— 见模块 docstring「适用边界」。

        形态 3（全局残留型）只能靠这条抓到：那种情况下两个请求的输出
        都完全正常，只有全局状态能被检出已损坏。
        """
        code = f"import time\ntime.sleep({SLEEP})\nprint('{{}}')"
        _run_concurrently(code.format("AAA"), code.format("BBB"))

        assert sys.stdout is ORIGINAL_STDOUT, (
            f"全局 sys.stdout 被永久污染为 {type(sys.stdout).__name__}。"
            f"此后进程内裸 print() 的输出全部进入无主缓冲区、静默消失。"
            f"注意：logging 与 app.log 不受影响（handler 在配置期已绑定原对象），"
            f"审计链仍完整。"
        )


class TestRepeatedConcurrency:
    """单次并发可能刚好没重叠。用多次压测提高命中率。"""

    def test_repeated_runs_never_pollute_global(self):
        """反复并发后全局必须仍然干净。

        单次命中率高不代表判据完备 —— 这里跑多轮，
        每轮都断言一次，覆盖不同的交错。
        """
        polluted_rounds = []
        code = f"import time\ntime.sleep({SLEEP})\nprint('{{}}')"
        try:
            for i in range(8):
                _, _, polluted = _run_concurrently(
                    code.format(f"A{i}"), code.format(f"B{i}")
                )
                if polluted:
                    polluted_rounds.append(i)
        finally:
            sys.stdout = ORIGINAL_STDOUT

        assert not polluted_rounds, (
            f"第 {polluted_rounds} 轮后全局 stdout 被污染。"
            f"8 轮全部污染说明修复完全未生效；"
            f"偶发则说明仍有残留的竞态窗口。"
        )


class TestSingleThreadStillWorks:
    """★ 反向保护：修F-010 不能把正常捕获功能一起弄坏。

    没有这一组，一个「把 print 注入成空实现」的修复也能让上面全绿 ——
    那是删功能不是修复。
    """

    def test_single_thread_capture_works(self):
        result = _execute_with_timeout("print('hello')", 5)
        assert result["error"] is None
        assert (result["stdout"] or "").strip() == "hello", (
            f"单线程输出捕获失效：{result.get('stdout')!r}。"
            f"修复不能以牺牲正常功能为代价。"
        )

    def test_multiple_prints_all_captured(self):
        result = _execute_with_timeout("print('a')\nprint('b')\nprint('c')", 5)
        out = result["stdout"] or ""
        for ch in "abc":
            assert ch in out, f"print('{ch}') 丢失，输出为 {out!r}"

    def test_stdout_untouched_after_single_run(self):
        _execute_with_timeout("print('x')", 5)
        assert sys.stdout is ORIGINAL_STDOUT

    def test_error_path_also_restores_stdout(self):
        """异常路径同样必须恢复全局。

        `finally` 块里的恢复对正常/异常两条路径都生效；
        这条防止将来有人把恢复逻辑挪到 try 块末尾（异常时就不执行了）。
        """
        result = _execute_with_timeout("raise ValueError('boom')", 5)
        assert result["error"] is not None, "应当报错"
        assert sys.stdout is ORIGINAL_STDOUT, "异常路径未恢复全局 stdout"


class TestTimeoutPathRestoresStdout:
    """超时路径也要恢复 —— 超时是走异常分支的。"""

    def test_timeout_restores_stdout(self):
        result = _execute_with_timeout(
            f"import time\ntime.sleep(3)\nprint('never')", 1
        )
        assert result["error"] is not None, "应当超时"
        assert sys.stdout is ORIGINAL_STDOUT, "超时路径未恢复全局 stdout"
