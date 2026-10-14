"""单步执行的纯逻辑测试。

这一层的价值就在于「能脱离图与数据库完整单测」—— 因此这里不建 app、
不起服务，只测函数。执行策略（谁需要审批）与产出归一化是重点：
策略写错会让门禁形同虚设，归一化写错会让真实链接丢失。
"""

from __future__ import annotations

import pytest

from app.workflow.executor import (
    build_step_prompt,
    needs_gate,
    normalize_tool_output,
    prior_context,
    runnable_next_step,
    search_calls_used,
)

FORCED = ["code_executor", "file_write"]


def _step(**kwargs):
    base = {
        "idx": 1,
        "kind": "llm",
        "title": "起草",
        "action": {"prompt_ref": "起草"},
        "requires_approval": False,
        "status": "pending",
    }
    base.update(kwargs)
    return base


# ---------------------------------------------------------------------------
#  工具产出归一化 —— 真实链接必须原样保留
# ---------------------------------------------------------------------------


class TestNormalizeToolOutput:
    def test_keeps_sources_untouched(self):
        """sources 里是 Tavily 返回的真实 url，是需求 4 的唯一来源，不能裁。"""
        sources = [
            {"title": "A", "href": "https://a.example/1", "snippet": "x"},
            {"title": "B", "href": "https://b.example/2", "snippet": "y"},
        ]
        out = normalize_tool_output(
            "web_search", {"success": True, "result": "摘要", "sources": sources}
        )
        assert out["sources"] == sources
        assert out["summary"] == "摘要"

    def test_failure_marked(self):
        out = normalize_tool_output("web_search", {"success": False, "error": "超时"})
        assert out["failed"] is True
        assert "超时" in out["summary"]

    def test_non_dict_result(self):
        out = normalize_tool_output("calculator", "42")
        assert out["summary"] == "42"

    def test_long_output_truncated(self):
        out = normalize_tool_output(
            "knowledge_query", {"success": True, "result": "字" * 20000}
        )
        assert len(out["summary"]) < 20000
        assert "已截断" in out["summary"]

    def test_empty_output_gets_placeholder(self):
        out = normalize_tool_output("knowledge_query", {"success": True, "result": ""})
        assert out["summary"]

    def test_instant_answer_kept(self):
        out = normalize_tool_output(
            "web_search",
            {"success": True, "result": "r", "instant_answer": "42", "sources": []},
        )
        assert out["instant_answer"] == "42"


# ---------------------------------------------------------------------------
#  执行策略：唯一的一份实现
# ---------------------------------------------------------------------------


class TestNeedsGate:
    def test_manual_mode_honours_requires_approval(self):
        assert needs_gate(
            step=_step(requires_approval=True), mode="manual", forced_tools=FORCED
        )

    def test_auto_mode_skips_requires_approval(self):
        """自动模式下 `requires_approval` 不再拦 —— 设计 §7.2 明确如此。"""
        assert not needs_gate(
            step=_step(requires_approval=True), mode="auto", forced_tools=FORCED
        )

    def test_forced_tool_always_gates_even_in_auto(self):
        """★ 唯一一处「模式被覆盖」的地方，也是必须的一处。"""
        step = _step(
            kind="tool",
            action={"tool": "code_executor", "args": {}},
            requires_approval=False,
        )
        assert needs_gate(step=step, mode="auto", forced_tools=FORCED)

    def test_plain_step_needs_no_gate(self):
        assert not needs_gate(step=_step(), mode="manual", forced_tools=FORCED)

    def test_forced_tool_list_is_configurable(self):
        step = _step(kind="tool", action={"tool": "web_search", "args": {}})
        assert not needs_gate(step=step, mode="auto", forced_tools=FORCED)
        assert needs_gate(step=step, mode="auto", forced_tools=["web_search"])


# ---------------------------------------------------------------------------
#  前序产出回填
# ---------------------------------------------------------------------------


class TestPriorContext:
    def test_includes_done_outputs(self):
        steps = [
            _step(idx=1, status="done", output={"summary": "价格是 1 元"}),
            _step(idx=2, status="pending"),
        ]
        text = prior_context(steps)
        assert "价格是 1 元" in text
        assert "第 1 步" in text

    def test_failed_step_is_disclosed(self):
        """必须让后续步骤知道「前面没做成」，否则它会基于不存在的输入继续编。"""
        steps = [_step(idx=1, status="failed", error="超时")]
        text = prior_context(steps)
        assert "失败" in text and "超时" in text

    def test_skipped_step_is_disclosed(self):
        text = prior_context([_step(idx=1, status="skipped")])
        assert "跳过" in text

    def test_first_step_placeholder(self):
        assert "第一步" in prior_context([_step(idx=1, status="pending")])

    def test_long_output_truncated(self):
        steps = [_step(idx=1, status="done", output={"summary": "字" * 5000})]
        text = prior_context(steps, max_chars=100)
        assert len(text) < 400
        assert "已截断" in text


class TestSearchCallsUsed:
    def test_counts_settled_search_steps(self):
        steps = [
            _step(idx=1, kind="tool", action={"tool": "web_search"}, status="done"),
            _step(idx=2, kind="tool", action={"tool": "web_search"}, status="pending"),
            _step(idx=3, kind="tool", action={"tool": "calculator"}, status="done"),
        ]
        assert search_calls_used(steps) == 1

    def test_counts_running_and_approved(self):
        steps = [
            _step(idx=1, kind="tool", action={"tool": "web_search"}, status="running"),
            _step(idx=2, kind="tool", action={"tool": "web_search"}, status="approved"),
        ]
        assert search_calls_used(steps) == 2


class TestRunnableNextStep:
    def test_skipped_step_does_not_block_the_rest(self):
        """★ 回归：`skipped` 曾经没被当作已终结，导致跳过中间某步后
        后面所有步骤再也拿不到执行许可（整个计划卡死）。

        真实场景：第 3 步执行失败被跳过，第 4、5 步就永远跑不了了。
        """
        steps = [
            _step(idx=1, status="done"),
            _step(idx=2, status="done"),
            _step(idx=3, status="skipped"),
            _step(idx=4, status="pending"),
            _step(idx=5, status="pending"),
        ]
        assert runnable_next_step(steps)["idx"] == 4

    def test_all_skipped_returns_none(self):
        steps = [_step(idx=1, status="skipped"), _step(idx=2, status="skipped")]
        assert runnable_next_step(steps) is None

    def test_picks_first_unfinished(self):
        steps = [
            _step(idx=1, status="done"),
            _step(idx=2, status="pending"),
            _step(idx=3, status="pending"),
        ]
        assert runnable_next_step(steps)["idx"] == 2

    def test_stops_at_blocked_step(self):
        """只向前走 —— 步骤之间有依赖，跳步会产出基于缺失输入的结果。"""
        steps = [
            _step(idx=1, status="awaiting_approval"),
            _step(idx=2, status="pending"),
        ]
        assert runnable_next_step(steps) is None

    def test_returns_none_when_all_settled(self):
        assert runnable_next_step([_step(idx=1, status="done")]) is None

    def test_gated_pending_step_is_not_runnable(self):
        """门禁步骤现在是**执行不了**的，所以不能下发它的序号 ——
        否则前端会拿到一个必然撞 409 的序号，表现成「按钮能点但总报错」。
        """
        steps = [_step(idx=1, requires_approval=True, status="pending")]
        assert runnable_next_step(steps, mode="manual", forced_tools=FORCED) is None
        # 自动模式下不需要确认，就能跑
        assert runnable_next_step(steps, mode="auto", forced_tools=FORCED)["idx"] == 1

    def test_approved_step_is_runnable(self):
        steps = [_step(idx=1, requires_approval=True, status="approved")]
        assert runnable_next_step(steps, mode="manual", forced_tools=FORCED)["idx"] == 1

    def test_forced_tool_pending_is_not_runnable_in_auto(self):
        steps = [
            _step(
                idx=1,
                kind="tool",
                action={"tool": "code_executor", "args": {}},
                status="pending",
            )
        ]
        assert runnable_next_step(steps, mode="auto", forced_tools=FORCED) is None


# ---------------------------------------------------------------------------
#  单步提示词
# ---------------------------------------------------------------------------


class TestBuildStepPrompt:
    PLAN = {
        "goal": "产出对比报告",
        "audience": "技术决策者",
        "constraints": ["中文", "含价格表"],
    }

    def test_carries_goal_and_constraints(self):
        prompt = build_step_prompt(
            plan=self.PLAN, step=_step(idx=2, title="汇总"), steps=[_step(idx=1), _step(idx=2)]
        )
        assert "产出对比报告" in prompt
        assert "技术决策者" in prompt
        assert "中文；含价格表" in prompt
        assert "第 2 步" in prompt and "共 2 步" in prompt

    def test_includes_prior_output(self):
        steps = [
            _step(idx=1, status="done", output={"summary": "发现 A 家最便宜"}),
            _step(idx=2, title="汇总"),
        ]
        prompt = build_step_prompt(plan=self.PLAN, step=steps[1], steps=steps)
        assert "发现 A 家最便宜" in prompt

    def test_includes_file_context_when_present(self):
        prompt = build_step_prompt(
            plan=self.PLAN,
            step=_step(),
            steps=[_step()],
            file_context="参考内容：单价 0.5 元",
        )
        assert "单价 0.5 元" in prompt

    def test_last_step_asks_for_deliverable(self):
        steps = [_step(idx=1), _step(idx=2, kind="synthesis", title="交付")]
        prompt = build_step_prompt(plan=self.PLAN, step=steps[1], steps=steps)
        assert "可交付的最终内容" in prompt

    def test_middle_step_does_not_ask_for_deliverable(self):
        steps = [_step(idx=1), _step(idx=2), _step(idx=3)]
        prompt = build_step_prompt(plan=self.PLAN, step=steps[0], steps=steps)
        assert "可交付的最终内容" not in prompt
