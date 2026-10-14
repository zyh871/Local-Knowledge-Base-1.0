"""澄清与「生成任务书」是两条独立 SSE 流 —— 后写的一方不能把状态拉回去。

★ 实测复现过的竞态（日志原文）：

    09:58:14  任务书已生成 run=... （status → awaiting_approval）
    09:58:25  澄清第 1 轮（status → clarifying）   ← 晚了 11 秒

用户在澄清还没回来时就点了「生成任务书」，于是两条流同时在跑。
澄清那条流把已进入「待批准」的任务改回「澄清中」，结果是
**批准按钮直接消失，界面上没有任何错误** —— 用户看到的就是"卡住了"。

状态推进必须是**单调**的。这个文件钉住这一点，并确认
「不回退状态」不等于「丢掉澄清数据」。
"""

from __future__ import annotations

import json

import pytest

PLAN_STEPS = [
    {"kind": "llm", "title": "第一步", "action": {"prompt_ref": "第一步"}},
]


class _FakeLLM:
    def __init__(self, text: str = '{"sufficient": false, "questions": [{"id": "q1", "text": "范围?"}]}') -> None:
        self._text = text

    def stream(self, _prompt: str):
        from langchain_core.messages import AIMessageChunk

        yield AIMessageChunk(content=self._text, response_metadata={"finish_reason": "stop"})

    def invoke(self, _prompt: str):
        from langchain_core.messages import AIMessage

        return AIMessage(content=self._text, response_metadata={"finish_reason": "stop"})


@pytest.fixture
def wf_env(tmp_path, monkeypatch):
    from app.core.config import clear_settings_cache
    from app.repositories import workflow_repo as repo

    monkeypatch.setenv("WORKFLOW_DB_PATH", str(tmp_path / "workflow.db"))
    monkeypatch.setenv("WORKFLOW_WORKSPACE_ROOT", str(tmp_path / "workspaces"))
    monkeypatch.setenv("WORKFLOW_VERIFY_LINKS", "false")
    clear_settings_cache()
    repo.reset_connection()
    yield tmp_path
    repo.reset_connection()
    clear_settings_cache()


@pytest.fixture
def workspace(client, auth_headers, wf_env):
    return client.post(
        "/workflow/workspaces", json={"name": "竞态测试"}, headers=auth_headers
    ).json()


def _final(response) -> dict:
    for block in response.text.split("\n\n"):
        for line in block.strip().splitlines():
            if not line.startswith("data:"):
                continue
            raw = line[5:].strip()
            if not raw or raw == "[DONE]":
                continue
            try:
                event = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if event.get("type") == "final":
                return event.get("content") or {}
    return {}


def _new_run(client, auth_headers, workspace):
    return client.post(
        "/workflow/runs",
        json={"workspace_id": workspace["id"], "raw_input": "做个东西", "mode": "auto"},
        headers=auth_headers,
    ).json()


def _plan(client, auth_headers, run_id):
    client.put(
        f"/workflow/runs/{run_id}/plan",
        json={"goal": "目标", "steps": PLAN_STEPS},
        headers=auth_headers,
    )
    return client.get(f"/workflow/runs/{run_id}", headers=auth_headers).json()


class TestClarifyDoesNotRegressStatus:
    def test_late_clarify_keeps_awaiting_approval(
        self, client, auth_headers, wf_env, workspace, monkeypatch
    ):
        """★ 任务书已就绪时，迟到的澄清流不得把状态改回 clarifying。"""
        monkeypatch.setattr("app.rag.providers.get_llm", lambda model=None: _FakeLLM())
        run = _new_run(client, auth_headers, workspace)
        run_id = run["id"]

        detail = _plan(client, auth_headers, run_id)
        assert detail["status"] == "awaiting_approval"

        # 迟到的澄清流（真实时序里它可能晚十几秒才返回）
        _final(
            client.post(f"/workflow/runs/{run_id}/clarify/stream", headers=auth_headers)
        )

        after = client.get(f"/workflow/runs/{run_id}", headers=auth_headers).json()
        assert after["status"] == "awaiting_approval", (
            f"澄清流把状态拉回去了：{after['status']} —— "
            f"这会让界面上的批准按钮消失且不报任何错"
        )

    def test_late_clarify_still_stores_its_data(
        self, client, auth_headers, wf_env, workspace, monkeypatch
    ):
        """不回退状态 ≠ 丢掉澄清结果 —— 数据仍要落库。"""
        monkeypatch.setattr("app.rag.providers.get_llm", lambda model=None: _FakeLLM())
        run = _new_run(client, auth_headers, workspace)
        run_id = run["id"]
        _plan(client, auth_headers, run_id)

        content = _final(
            client.post(f"/workflow/runs/{run_id}/clarify/stream", headers=auth_headers)
        )
        assert content.get("ok") is True, content
        assert content.get("questions"), content

        after = client.get(f"/workflow/runs/{run_id}", headers=auth_headers).json()
        clarify = after.get("clarify") or {}
        rounds = clarify.get("rounds") or []
        assert rounds, "澄清轮次没有落库"

    def test_normal_clarify_still_sets_clarifying(
        self, client, auth_headers, wf_env, workspace, monkeypatch
    ):
        """★ 反向保证：正常路径下澄清**必须**还是把状态置成 clarifying。

        只想拦"回退"，不能顺手把正常功能也拦掉 ——
        那种"修好了这个、坏了那个"的改动比原 bug 更难发现。
        """
        monkeypatch.setattr("app.rag.providers.get_llm", lambda model=None: _FakeLLM())
        run = _new_run(client, auth_headers, workspace)
        run_id = run["id"]

        _final(
            client.post(f"/workflow/runs/{run_id}/clarify/stream", headers=auth_headers)
        )
        after = client.get(f"/workflow/runs/{run_id}", headers=auth_headers).json()
        assert after["status"] == "clarifying"

    def test_answer_endpoint_does_not_regress_either(
        self, client, auth_headers, wf_env, workspace, monkeypatch
    ):
        monkeypatch.setattr("app.rag.providers.get_llm", lambda model=None: _FakeLLM())
        run = _new_run(client, auth_headers, workspace)
        run_id = run["id"]
        content = _final(
            client.post(f"/workflow/runs/{run_id}/clarify/stream", headers=auth_headers)
        )
        qid = (content.get("questions") or [{}])[0].get("id")
        assert qid

        _plan(client, auth_headers, run_id)
        assert (
            client.get(f"/workflow/runs/{run_id}", headers=auth_headers).json()["status"]
            == "awaiting_approval"
        )

        resp = client.post(
            f"/workflow/runs/{run_id}/clarify/answer",
            json={"answers": [{"id": qid, "value": "全部范围"}]},
            headers=auth_headers,
        )
        assert resp.status_code == 200, resp.text
        after = client.get(f"/workflow/runs/{run_id}", headers=auth_headers).json()
        assert after["status"] == "awaiting_approval"


class TestErrorPathDoesNotRegressStatus:
    """★ 上面那组覆盖「迟到的**成功**结果」；这一组覆盖「迟到的**错误**」。

    这是同一个根因的另一半：澄清流与规划流在拿到 `final_state["error"]` 时
    原本是**无条件**写 `status="failed"`。于是只要用户先点了「生成任务书」，
    随后澄清/规划那条流带着错误回来，就会把已经就绪的任务**静默降级**成
    失败态 —— 批准按钮消失，界面上没有任何错误提示。

    修复后状态回写统一走 `_write_status_monotonic()`：状态照旧单调，
    错误信息照旧落库。下面三条分别验证「不许回退」「数据不丢」「不许过度保护」。
    """

    def test_clarify_error_does_not_regress_status(
        self, client, auth_headers, wf_env, workspace
    ):
        """澄清流报错，不得把 awaiting_approval 拉回 failed。"""
        from app.repositories import workflow_repo as repo
        from app.services.workflow_service import (
            _CLARIFY_WRITABLE_STATUSES,
            WorkflowService,
        )

        run = _new_run(client, auth_headers, workspace)
        repo.update_run(run["id"], status="awaiting_approval")

        WorkflowService()._write_status_monotonic(
            run["id"],
            status="failed",
            writable=_CLARIFY_WRITABLE_STATUSES,
            error="模拟澄清流报错",
        )

        after = repo.get_run(run["id"], with_steps=False)
        assert after["status"] == "awaiting_approval", (
            "错误路径把已就绪的状态拉回了 —— 批准按钮会消失且无任何提示"
        )
        assert after["error"] == "模拟澄清流报错", "错误信息必须照常落库，不能丢"

    def test_plan_error_does_not_regress_status(
        self, client, auth_headers, wf_env, workspace
    ):
        """规划流报错，同样不得回退已就绪的状态。"""
        from app.repositories import workflow_repo as repo
        from app.services.workflow_service import (
            _PLAN_WRITABLE_STATUSES,
            WorkflowService,
        )

        run = _new_run(client, auth_headers, workspace)
        repo.update_run(run["id"], status="awaiting_approval")

        WorkflowService()._write_status_monotonic(
            run["id"],
            status="failed",
            writable=_PLAN_WRITABLE_STATUSES,
            error="模拟规划流报错",
        )

        after = repo.get_run(run["id"], with_steps=False)
        assert after["status"] == "awaiting_approval"
        assert after["error"] == "模拟规划流报错"

    def test_error_still_marks_failed_when_not_advanced(
        self, client, auth_headers, wf_env, workspace
    ):
        """★ 反向保护：还没推进到后续阶段时，错误**仍然要**把任务标成失败。

        单调保护只该拦「迟到的错误」，不该把正常的失败记录一起吞掉 ——
        那会让真正失败的任务看起来还在进行中。
        """
        from app.repositories import workflow_repo as repo
        from app.services.workflow_service import (
            _PLAN_WRITABLE_STATUSES,
            WorkflowService,
        )

        run = _new_run(client, auth_headers, workspace)
        repo.update_run(run["id"], status="draft")

        WorkflowService()._write_status_monotonic(
            run["id"],
            status="failed",
            writable=_PLAN_WRITABLE_STATUSES,
            error="规划真的失败了",
        )

        after = repo.get_run(run["id"], with_steps=False)
        assert after["status"] == "failed", (
            "未推进的任务出错时必须标记 failed —— 否则失败会被静默吞掉"
        )
        assert after["error"] == "规划真的失败了"
