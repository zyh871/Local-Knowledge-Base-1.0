"""token 用量的记录与汇总（成本可见性）。

做的事很小，但**这是做其它优化的前提**：没有它，一次 10 次调用、9 万字符的产出
到底花了多少完全是黑盒，也就无从判断"该优化哪一步"。

两条必须守住的语义：
  1. **累加而非覆盖** —— 澄清可能多轮、规划可能被驳回重来，每次都是真实计费。
     覆盖只剩最后一次，那个数字系统性偏小，比「没有这个数」更糟。
  2. **拿不到就不要显示 0** —— 0 会被读成"不花钱"。
"""

from __future__ import annotations

from typing import Any

import pytest

from app.core.config import clear_settings_cache
from app.repositories import workflow_repo as repo
from app.services.workflow_service import WorkflowService


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKFLOW_DB_PATH", str(tmp_path / "workflow.db"))
    monkeypatch.setenv("WORKFLOW_WORKSPACE_ROOT", str(tmp_path / "workspaces"))
    clear_settings_cache()
    repo.reset_connection()
    yield
    repo.reset_connection()
    clear_settings_cache()


@pytest.fixture
def service() -> WorkflowService:
    from app.core.config import get_settings

    return WorkflowService(settings=get_settings(), chain=None, graph=None, redis=None)


def _token_run(run_id: str, steps: list[dict[str, Any]] = ()) -> dict[str, Any]:
    return {"id": run_id, "steps": list(steps)}


class TestNormalizeUsage:
    def test_openai_style(self, service):
        assert service._normalize_usage(
            {"input_tokens": 10, "output_tokens": 20, "total_tokens": 30}
        ) == {"input_tokens": 10, "output_tokens": 20, "total_tokens": 30}

    def test_langchain_style(self, service):
        """上游叫 prompt/completion_tokens 的也要认。"""
        assert service._normalize_usage(
            {"prompt_tokens": 7, "completion_tokens": 3}
        ) == {"input_tokens": 7, "output_tokens": 3, "total_tokens": 10}

    def test_total_derived_when_missing(self, service):
        assert service._normalize_usage({"input_tokens": 5, "output_tokens": 5})[
            "total_tokens"
        ] == 10

    def test_empty_and_garbage(self, service):
        assert service._normalize_usage({}) == {}
        # 布尔是 int 的子类，不能被当成数字累加进去
        assert service._normalize_usage({"input_tokens": True, "output_tokens": "x"}) == {}


class TestUsageSummary:
    def test_none_when_nothing_available(self, service, temp_db):
        """★ 拿不到用量时返回 None，而不是 0 —— 0 会被读成"不花钱"。"""
        assert service._usage_summary(_token_run("run_x")) is None

    def test_sums_phases_and_steps(self, service, temp_db):
        run = {
            "id": "run_1",
            "usage_raw": {
                "phases": {
                    "clarify": {"input_tokens": 100, "output_tokens": 50},
                    "plan": {"input_tokens": 200, "output_tokens": 80},
                }
            },
            "steps": [
                {"idx": 1, "kind": "llm", "title": "甲", "output": {"usage": {"input_tokens": 1000, "output_tokens": 2000}}},
                {"idx": 2, "kind": "synthesis", "title": "乙", "output": {"usage": {"prompt_tokens": 300, "completion_tokens": 700}}},
                # 没有用量的步骤不该干扰计数
                {"idx": 3, "kind": "tool", "title": "丙", "output": {"summary": "x"}},
            ],
        }
        summary = service._usage_summary(run)
        assert summary is not None
        assert summary["calls"] == 4  # 2 个相位 + 2 个有数据的步骤
        assert summary["input_tokens"] == 100 + 200 + 1000 + 300
        assert summary["output_tokens"] == 50 + 80 + 2000 + 700
        assert summary["total_tokens"] == summary["input_tokens"] + summary["output_tokens"]
        assert [s["idx"] for s in summary["steps"]] == [1, 2]
        assert summary["phases"]["clarify"]["input_tokens"] == 100

    def test_phases_only(self, service, temp_db):
        run = {"id": "run_2", "usage_raw": {"phases": {"clarify": {"input_tokens": 10}}}, "steps": []}
        summary = service._usage_summary(run)
        assert summary is not None and summary["calls"] == 1 and summary["total_tokens"] == 10


class TestAddRunUsageAccumulates:
    def _make_run(self) -> str:
        workspace = repo.create_workspace("用量测试", "admin")
        run = repo.create_run(
            workspace_id=workspace["id"], username="admin", raw_input="x", mode="manual"
        )
        return run["id"]

    def test_accumulates_across_calls(self, temp_db):
        """★ 澄清 3 轮 + 规划重来 2 次，总量必须是 5 次调用的和。"""
        run_id = self._make_run()
        for _ in range(3):
            repo.add_run_usage(run_id, "clarify", {"input_tokens": 100, "output_tokens": 40})
        for _ in range(2):
            repo.add_run_usage(run_id, "plan", {"input_tokens": 200, "output_tokens": 60})

        stored = repo.get_run(run_id)["usage_raw"]
        assert stored["phases"]["clarify"]["input_tokens"] == 300
        assert stored["phases"]["plan"]["input_tokens"] == 400
        # 顶层汇总 = 所有相位之和
        assert stored["phase_total"]["input_tokens"] == 700
        assert stored["phase_total"]["output_tokens"] == 3 * 40 + 2 * 60

    def test_empty_usage_is_noop(self, temp_db):
        run_id = self._make_run()
        assert repo.add_run_usage(run_id, "clarify", {}) is None
        assert repo.get_run(run_id)["usage_raw"] == {}

    def test_start_from_clean(self, temp_db):
        run_id = self._make_run()
        assert repo.get_run(run_id)["usage_raw"] == {}
        repo.add_run_usage(run_id, "plan", {"total_tokens": 5})
        assert repo.get_run(run_id)["usage_raw"]["phase_total"]["total_tokens"] == 5
