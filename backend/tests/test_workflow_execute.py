"""P3：步骤执行与状态机的端到端测试。

设计文档 §4.1 的核心取舍在这里被钉住：**一次 HTTP 请求只推进一步**。
因此重点验证的是「并发与状态一致性」这类接口测不出、但会实质伤害用户的行为：

  * 门禁：需要人工确认的步骤不能被绕过执行，且**被拦下时状态要落成
    awaiting_approval**（让「在等谁」在数据里可见）
  * 强制审批工具：自动模式下也要拦
  * 乐观并发：已完成 / 正在执行的步骤不能被重复推进
  * 失败要停下（不自动重试烧钱），且 run 进入 paused 而不是假装在跑
  * 客户端断流后，步骤不能永远卡在 running
"""

from __future__ import annotations

import json

import pytest

GOOD_TEXT = "这是这一步的产出内容。"


class _FakeLLM:
    def __init__(self, text: str = GOOD_TEXT, chunk_size: int = 9) -> None:
        self._text = text
        self._chunk_size = chunk_size

    def stream(self, _prompt: str):
        from langchain_core.messages import AIMessageChunk

        total = len(self._text)
        for start in range(0, total, self._chunk_size):
            end = start + self._chunk_size
            meta = {"finish_reason": "stop"} if end >= total else {}
            yield AIMessageChunk(content=self._text[start:end], response_metadata=meta)

    def invoke(self, _prompt: str):
        from langchain_core.messages import AIMessage

        return AIMessage(content=self._text, response_metadata={"finish_reason": "stop"})


@pytest.fixture
def wf_env(tmp_path, monkeypatch):
    from app.core.config import clear_settings_cache
    from app.repositories import workflow_repo as repo

    monkeypatch.setenv("WORKFLOW_DB_PATH", str(tmp_path / "workflow.db"))
    monkeypatch.setenv("WORKFLOW_WORKSPACE_ROOT", str(tmp_path / "workspaces"))
    clear_settings_cache()
    repo.reset_connection()
    yield tmp_path
    repo.reset_connection()
    clear_settings_cache()


@pytest.fixture
def workspace(client, auth_headers, wf_env):
    return client.post(
        "/workflow/workspaces", json={"name": "执行测试"}, headers=auth_headers
    ).json()


def _use_text(monkeypatch, text: str = GOOD_TEXT):
    fake = _FakeLLM(text)
    monkeypatch.setattr("app.rag.providers.get_llm", lambda model=None: fake)
    return fake


def _events(response) -> list[dict]:
    out: list[dict] = []
    for block in response.text.split("\n\n"):
        block = block.strip()
        if block.startswith("data: "):
            out.append(json.loads(block[len("data: ") :]))
    return out


def _final(response) -> dict:
    finals = [e for e in _events(response) if e["type"] == "final"]
    assert finals, f"没有收到 final 事件: {response.text[:400]}"
    return finals[-1]["content"]


def _events_of(response, kind: str) -> list[dict]:
    return [e for e in _events(response) if e["type"] == kind]


def _make_run(client, auth_headers, workspace_id, steps, *, mode="manual") -> str:
    """建一个「任务书已批准」的任务，直接返回 run_id。"""
    run = client.post(
        "/workflow/runs",
        json={"workspace_id": workspace_id, "raw_input": "目标", "mode": mode},
        headers=auth_headers,
    ).json()
    resp = client.put(
        f"/workflow/runs/{run['id']}/plan",
        json={"goal": "产出结论", "steps": steps},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    approved = client.post(
        f"/workflow/runs/{run['id']}/approve", headers=auth_headers
    )
    assert approved.status_code == 200, approved.text
    return run["id"]


LLM_STEP = {"kind": "llm", "title": "起草内容", "action": {"prompt_ref": "起草"}}
CALC_STEP = {
    "kind": "tool",
    "title": "算一下",
    "action": {"tool": "calculator", "args": {"expression": "2 + 3 * 4"}},
}


def _exec(client, auth_headers, run_id, idx):
    return client.post(
        f"/workflow/runs/{run_id}/steps/{idx}/execute", headers=auth_headers
    )


# ---------------------------------------------------------------------------
#  正常执行
# ---------------------------------------------------------------------------


class TestExecuteHappyPath:
    def test_llm_step_streams_and_persists(self, client, auth_headers, wf_env, workspace, monkeypatch):
        _use_text(monkeypatch)
        run_id = _make_run(client, auth_headers, workspace["id"], [LLM_STEP])

        resp = _exec(client, auth_headers, run_id, 1)
        kinds = [e["type"] for e in _events(resp)]
        assert kinds[0] == "step_start"
        assert "step_delta" in kinds
        assert "step_done" in kinds
        assert kinds[-1] == "final"

        final = _final(resp)
        assert final["ok"] is True
        assert final["all_done"] is True
        assert final["status"] == "done"

        detail = client.get(f"/workflow/runs/{run_id}", headers=auth_headers).json()
        step = detail["steps"][0]
        assert step["status"] == "done"
        assert GOOD_TEXT in step["output"]["summary"]
        assert detail["status"] == "done"
        # 全部完成时要给出结论，否则「已完成」是个空壳
        assert detail["summary"]

    def test_tool_step_calls_registry(self, client, auth_headers, wf_env, workspace, monkeypatch):
        run_id = _make_run(client, auth_headers, workspace["id"], [CALC_STEP])
        final = _final(_exec(client, auth_headers, run_id, 1))
        assert final["ok"] is True
        assert "14" in str(final["output"]["summary"])

    def test_step_delta_content_not_trimmed(self, client, auth_headers, wf_env, workspace, monkeypatch):
        """流式分片不能 trim —— 会吃掉词间空格，英文会粘成一坨。"""
        _use_text(monkeypatch, "hello world foo")
        run_id = _make_run(client, auth_headers, workspace["id"], [LLM_STEP])
        deltas = "".join(
            e["content"]["text"] for e in _events_of(_exec(client, auth_headers, run_id, 1), "step_delta")
        )
        assert deltas == "hello world foo"

    def test_run_completes_only_after_all_steps(self, client, auth_headers, wf_env, workspace, monkeypatch):
        _use_text(monkeypatch)
        run_id = _make_run(client, auth_headers, workspace["id"], [LLM_STEP, LLM_STEP])

        first = _final(_exec(client, auth_headers, run_id, 1))
        assert first["all_done"] is False
        assert first["status"] == "running"

        second = _final(_exec(client, auth_headers, run_id, 2))
        assert second["all_done"] is True
        assert second["status"] == "done"


# ---------------------------------------------------------------------------
#  门禁
# ---------------------------------------------------------------------------


class TestGate:
    GATED = {
        "kind": "llm",
        "title": "需要把关的一步",
        "action": {"prompt_ref": "把关"},
        "requires_approval": True,
    }

    def test_manual_mode_blocks_and_records_status(self, client, auth_headers, wf_env, workspace, monkeypatch):
        _use_text(monkeypatch)
        run_id = _make_run(client, auth_headers, workspace["id"], [self.GATED], mode="manual")

        final = _final(_exec(client, auth_headers, run_id, 1))
        assert final["ok"] is False
        assert final["code"] == "approval_required"

        # ★ 被拦下时状态要落成 awaiting_approval，
        # 让「在等谁」这件事在数据里可见，而不是只活在前端内存里
        detail = client.get(f"/workflow/runs/{run_id}", headers=auth_headers).json()
        assert detail["steps"][0]["status"] == "awaiting_approval"
        assert detail["steps"][0]["needs_gate"] is True

    def test_approve_then_execute(self, client, auth_headers, wf_env, workspace, monkeypatch):
        _use_text(monkeypatch)
        run_id = _make_run(client, auth_headers, workspace["id"], [self.GATED])

        _exec(client, auth_headers, run_id, 1)
        approved = client.post(
            f"/workflow/runs/{run_id}/steps/1/approve", headers=auth_headers
        )
        assert approved.status_code == 200
        assert approved.json()["step"]["status"] == "approved"
        assert approved.json()["step"]["approved_by"] == "admin"

        assert _final(_exec(client, auth_headers, run_id, 1))["ok"] is True

    def test_auto_mode_does_not_block(self, client, auth_headers, wf_env, workspace, monkeypatch):
        """自动模式下 requires_approval 不再拦 —— 设计 §7.2。"""
        _use_text(monkeypatch)
        run_id = _make_run(client, auth_headers, workspace["id"], [self.GATED], mode="auto")
        assert _final(_exec(client, auth_headers, run_id, 1))["ok"] is True

    def test_forced_tool_gates_even_in_auto(self, client, auth_headers, wf_env, workspace, monkeypatch):
        """★ 唯一一处「模式被覆盖」的地方。把 calculator 配成强制审批工具来测，
        避免真的去执行代码。"""
        from app.core.config import clear_settings_cache
        from app.repositories import workflow_repo as repo

        monkeypatch.setenv("WORKFLOW_REQUIRE_APPROVAL_TOOLS", "calculator,code_executor")
        clear_settings_cache()
        repo.reset_connection()

        run_id = _make_run(client, auth_headers, workspace["id"], [CALC_STEP], mode="auto")
        final = _final(_exec(client, auth_headers, run_id, 1))
        assert final["ok"] is False
        assert final["code"] == "approval_required"

    def test_needs_gate_is_exposed_for_every_step(self, client, auth_headers, wf_env, workspace, monkeypatch):
        """策略由服务端算好下发 —— 前端不该自己再实现一遍。"""
        _use_text(monkeypatch)
        run_id = _make_run(
            client, auth_headers, workspace["id"], [LLM_STEP, self.GATED], mode="manual"
        )
        steps = client.get(f"/workflow/runs/{run_id}", headers=auth_headers).json()["steps"]
        assert steps[0]["needs_gate"] is False
        assert steps[1]["needs_gate"] is True


# ---------------------------------------------------------------------------
#  并发与状态守卫
# ---------------------------------------------------------------------------


class TestStateGuards:
    def test_cannot_execute_done_step(self, client, auth_headers, wf_env, workspace, monkeypatch):
        _use_text(monkeypatch)
        run_id = _make_run(client, auth_headers, workspace["id"], [LLM_STEP])
        assert _final(_exec(client, auth_headers, run_id, 1))["ok"] is True

        again = _final(_exec(client, auth_headers, run_id, 1))
        assert again["ok"] is False
        assert "不能执行" in again["error"]

    def test_cannot_execute_before_plan_approved(self, client, auth_headers, wf_env, workspace, monkeypatch):
        _use_text(monkeypatch)
        run = client.post(
            "/workflow/runs",
            json={"workspace_id": workspace["id"], "raw_input": "目标"},
            headers=auth_headers,
        ).json()
        client.put(
            f"/workflow/runs/{run['id']}/plan",
            json={"goal": "g", "steps": [LLM_STEP]},
            headers=auth_headers,
        )
        final = _final(_exec(client, auth_headers, run["id"], 1))
        assert final["ok"] is False
        assert "批准" in final["error"]

    def test_cannot_skip_ahead(self, client, auth_headers, wf_env, workspace, monkeypatch):
        """禁止跳步：前序产出会拼进提示词，跳过前面会让模型基于不存在的输入继续编。"""
        _use_text(monkeypatch)
        run_id = _make_run(client, auth_headers, workspace["id"], [LLM_STEP, LLM_STEP])
        final = _final(_exec(client, auth_headers, run_id, 2))
        assert final["ok"] is False
        assert "尚未结束" in final["error"]

    def test_next_runnable_idx_is_exposed(self, client, auth_headers, wf_env, workspace, monkeypatch):
        """顺序与门禁策略由服务端下发，前端不自己判断。"""
        _use_text(monkeypatch)
        run_id = _make_run(client, auth_headers, workspace["id"], [LLM_STEP, LLM_STEP])
        detail = client.get(f"/workflow/runs/{run_id}", headers=auth_headers).json()
        assert detail["next_runnable_idx"] == 1

        _exec(client, auth_headers, run_id, 1)
        detail = client.get(f"/workflow/runs/{run_id}", headers=auth_headers).json()
        assert detail["next_runnable_idx"] == 2

    def test_next_runnable_idx_none_when_gated(self, client, auth_headers, wf_env, workspace, monkeypatch):
        _use_text(monkeypatch)
        gated = {
            "kind": "llm",
            "title": "把关",
            "action": {"prompt_ref": "把关"},
            "requires_approval": True,
        }
        run_id = _make_run(client, auth_headers, workspace["id"], [gated], mode="manual")
        detail = client.get(f"/workflow/runs/{run_id}", headers=auth_headers).json()
        # 待审批时不应给出「可以执行」的序号，否则前端会一直撞 409
        assert detail["next_runnable_idx"] is None

    def test_unknown_step_reports_error_event(self, client, auth_headers, wf_env, workspace, monkeypatch):
        """SSE 头已发出，错误只能以 final 事件返回，不能变成 404 断流。"""
        _use_text(monkeypatch)
        run_id = _make_run(client, auth_headers, workspace["id"], [LLM_STEP])
        final = _final(_exec(client, auth_headers, run_id, 99))
        assert final["ok"] is False

    def test_unregistered_tool_fails_step(self, client, auth_headers, wf_env, workspace):
        """执行期再查一次白名单 —— 挡住绕过校验直接写库的步骤。"""
        from app.repositories import workflow_repo as repo

        run_id = _make_run(client, auth_headers, workspace["id"], [LLM_STEP])
        repo.replace_steps(
            run_id,
            [{"kind": "tool", "title": "用不存在的工具", "action": {"tool": "rm_rf"}}],
        )
        final = _final(_exec(client, auth_headers, run_id, 1))
        assert final["ok"] is False
        assert "未注册" in final["error"]

    def test_search_quota_enforced(self, client, auth_headers, wf_env, workspace, monkeypatch):
        """超限时明确失败并要求用户处理，而不是静默烧配额。"""
        from app.core.config import clear_settings_cache
        from app.repositories import workflow_repo as repo

        monkeypatch.setenv("WORKFLOW_SEARCH_MAX_CALLS_PER_RUN", "1")
        clear_settings_cache()
        repo.reset_connection()

        run_id = _make_run(client, auth_headers, workspace["id"], [LLM_STEP])
        repo.replace_steps(
            run_id,
            [
                {
                    "kind": "tool",
                    "title": "已用掉的那次搜索",
                    "action": {"tool": "web_search", "args": {"query": "a"}},
                    "status": "done",
                    "output": {"summary": "已有结果"},
                },
                {
                    "kind": "tool",
                    "title": "第二次搜索",
                    "action": {"tool": "web_search", "args": {"query": "b"}},
                },
            ],
        )
        final = _final(_exec(client, auth_headers, run_id, 2))
        assert final["ok"] is False
        assert "上限" in final["error"]


# ---------------------------------------------------------------------------
#  失败 / 重试 / 跳过
# ---------------------------------------------------------------------------


class TestFailurePaths:
    def test_empty_generation_fails_step_and_pauses_run(self, client, auth_headers, wf_env, workspace, monkeypatch):
        _use_text(monkeypatch, "   ")
        run_id = _make_run(client, auth_headers, workspace["id"], [LLM_STEP, LLM_STEP])

        final = _final(_exec(client, auth_headers, run_id, 1))
        assert final["ok"] is False
        assert final["code"] == "step_failed"

        detail = client.get(f"/workflow/runs/{run_id}", headers=auth_headers).json()
        assert detail["steps"][0]["status"] == "failed"
        # 失败不自动重试（避免烧钱），run 停下来把决定权交给用户
        assert detail["status"] == "paused"

    def test_retry_failed_step(self, client, auth_headers, wf_env, workspace, monkeypatch):
        _use_text(monkeypatch, "   ")
        run_id = _make_run(client, auth_headers, workspace["id"], [LLM_STEP])
        _exec(client, auth_headers, run_id, 1)

        _use_text(monkeypatch)  # 这次给正常输出
        retried = client.post(
            f"/workflow/runs/{run_id}/steps/1/retry", headers=auth_headers
        )
        assert retried.status_code == 200
        step = retried.json()["step"]
        assert step["status"] == "approved"
        assert step["retry_count"] == 1
        assert step["error"] is None
        assert step["output"] is None  # 旧产出必须清掉，否则会看到上次的残留

        assert _final(_exec(client, auth_headers, run_id, 1))["ok"] is True

    def test_retry_requires_failed_status(self, client, auth_headers, wf_env, workspace, monkeypatch):
        _use_text(monkeypatch)
        run_id = _make_run(client, auth_headers, workspace["id"], [LLM_STEP])
        resp = client.post(f"/workflow/runs/{run_id}/steps/1/retry", headers=auth_headers)
        assert resp.status_code == 409

    def test_retry_of_gated_step_needs_reapproval(self, client, auth_headers, wf_env, workspace, monkeypatch):
        """重试不能变成绕过审批的后门。"""
        gated = {
            "kind": "llm",
            "title": "把关",
            "action": {"prompt_ref": "把关"},
            "requires_approval": True,
        }
        _use_text(monkeypatch, "   ")
        run_id = _make_run(client, auth_headers, workspace["id"], [gated])

        client.post(f"/workflow/runs/{run_id}/steps/1/approve", headers=auth_headers)
        _exec(client, auth_headers, run_id, 1)

        step = client.post(
            f"/workflow/runs/{run_id}/steps/1/retry", headers=auth_headers
        ).json()["step"]
        assert step["status"] == "pending"
        # 再执行仍会被拦
        assert _final(_exec(client, auth_headers, run_id, 1))["code"] == "approval_required"

    def test_skip_step_and_complete_run(self, client, auth_headers, wf_env, workspace, monkeypatch):
        _use_text(monkeypatch)
        run_id = _make_run(client, auth_headers, workspace["id"], [LLM_STEP, LLM_STEP])
        assert _final(_exec(client, auth_headers, run_id, 1))["ok"] is True

        skipped = client.post(
            f"/workflow/runs/{run_id}/steps/2/skip", headers=auth_headers
        )
        assert skipped.status_code == 200
        assert skipped.json()["step"]["status"] == "skipped"
        # 跳过最后一步后整体应判定为完成
        assert skipped.json()["run"]["status"] == "done"

    def test_cannot_skip_done_step(self, client, auth_headers, wf_env, workspace, monkeypatch):
        _use_text(monkeypatch)
        run_id = _make_run(client, auth_headers, workspace["id"], [LLM_STEP])
        _exec(client, auth_headers, run_id, 1)
        resp = client.post(f"/workflow/runs/{run_id}/steps/1/skip", headers=auth_headers)
        assert resp.status_code == 409

    def test_skipped_step_is_disclosed_to_later_steps(self, client, auth_headers, wf_env, workspace, monkeypatch):
        """后续步骤必须知道前面被跳过，否则会基于不存在的输入继续编。"""
        captured = {}

        class _Capturing(_FakeLLM):
            def stream(self, prompt):
                captured.setdefault("prompts", []).append(prompt)
                return super().stream(prompt)

        fake = _Capturing()
        monkeypatch.setattr("app.rag.providers.get_llm", lambda model=None: fake)

        run_id = _make_run(client, auth_headers, workspace["id"], [LLM_STEP, LLM_STEP])
        client.post(f"/workflow/runs/{run_id}/steps/1/skip", headers=auth_headers)
        _exec(client, auth_headers, run_id, 2)

        assert "跳过" in captured["prompts"][-1]

    def test_disconnect_does_not_leave_step_running(self, client, auth_headers, wf_env, workspace, monkeypatch):
        """客户端断流后不能留下永远 `running` 的步骤。"""
        import asyncio

        from app.core.config import get_settings
        from app.repositories import workflow_repo as repo
        from app.services.workflow_service import WorkflowService

        _use_text(monkeypatch)
        run_id = _make_run(client, auth_headers, workspace["id"], [LLM_STEP])
        service = WorkflowService(
            settings=get_settings(), graph=client.app.state.workflow_graph
        )

        async def _consume_then_disconnect():
            agen = service.stream_execute_step(run_id=run_id, idx=1, username="admin")
            await agen.__anext__()
            await agen.aclose()

        repo.update_step(run_id, 1, status="approved")
        asyncio.run(_consume_then_disconnect())

        step = repo.get_step(run_id, 1)
        assert step["status"] == "failed"
        assert "中断" in (step["error"] or "")
        assert repo.get_run(run_id, with_steps=False)["status"] == "paused"
