"""P5：执行控制的端到端测试。

三件事各自都有一处容易做错、且会静默伤害用户的地方：

  * **驳回重规划**：必须**保留**已完成步骤的产出。若整表重规划，用户驳回第 3 步
    会把前两步已经花过钱的产出一起丢掉 —— 而且界面上看起来"正常"。
  * **任务书编辑锁**：执行已开始后整表替换会把 done 步骤重置为 pending，
    产出永久丢失。必须挡住。
  * **终止**：只是"不再推进"，已完成步骤的产出仍要保留。
"""

from __future__ import annotations

import json

import pytest

PLAN_THREE = """思路说明。

```json
{
  "goal": "产出结论",
  "audience": "决策者",
  "constraints": ["中文"],
  "assumptions": [],
  "steps": [
    {"kind": "llm", "title": "第一步", "action": {"prompt_ref": "第一步"}},
    {"kind": "llm", "title": "第二步", "action": {"prompt_ref": "第二步"}},
    {"kind": "llm", "title": "第三步", "action": {"prompt_ref": "第三步"}}
  ]
}
```"""

PLAN_SUFFIX = """重新规划思路。

```json
{
  "goal": "产出结论",
  "audience": "决策者",
  "constraints": ["中文"],
  "assumptions": [],
  "steps": [
    {"kind": "llm", "title": "重规划后的第一步", "action": {"prompt_ref": "新a"}},
    {"kind": "synthesis", "title": "重规划后的第二步", "action": {"prompt_ref": "新b"}}
  ]
}
```"""

OUTPUT_TEXT = "这一步的产出内容。"


class _FakeLLM:
    def __init__(self, texts: list[str], chunk_size: int = 17) -> None:
        self._texts = texts
        self._chunk_size = chunk_size
        self.prompts: list[str] = []
        self._index = 0

    def _next(self, prompt: str) -> str:
        self.prompts.append(prompt)
        text = self._texts[min(self._index, len(self._texts) - 1)]
        self._index += 1
        return text

    def stream(self, prompt: str):
        from langchain_core.messages import AIMessageChunk

        text = self._next(prompt)
        total = len(text)
        for start in range(0, total, self._chunk_size):
            end = start + self._chunk_size
            meta = {"finish_reason": "stop"} if end >= total else {}
            yield AIMessageChunk(content=text[start:end], response_metadata=meta)

    def invoke(self, prompt: str):
        from langchain_core.messages import AIMessage

        return AIMessage(content=self._next(prompt), response_metadata={"finish_reason": "stop"})


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
        "/workflow/workspaces", json={"name": "控制测试"}, headers=auth_headers
    ).json()


def _use_llm(monkeypatch, texts: list[str]) -> _FakeLLM:
    fake = _FakeLLM(texts)
    monkeypatch.setattr("app.rag.providers.get_llm", lambda model=None: fake)
    return fake


def _events(response) -> list[dict]:
    out = []
    for block in response.text.split("\n\n"):
        block = block.strip()
        if block.startswith("data: "):
            out.append(json.loads(block[len("data: ") :]))
    return out


def _final(response) -> dict:
    finals = [e for e in _events(response) if e["type"] == "final"]
    assert finals, f"没有 final 事件: {response.text[:400]}"
    return finals[-1]["content"]


def _approved_run(client, auth_headers, workspace_id, monkeypatch, plan=PLAN_THREE) -> str:
    run_id = _planned_run(client, auth_headers, workspace_id, monkeypatch, plan)
    assert (
        client.post(f"/workflow/runs/{run_id}/approve", headers=auth_headers).status_code
        == 200
    )
    return run_id


def _planned_run(client, auth_headers, workspace_id, monkeypatch, plan=PLAN_THREE) -> str:
    """只生成任务书、**不批准**（用于验证「执行开始前」的行为）。"""
    _use_llm(monkeypatch, [plan])
    run = client.post(
        "/workflow/runs",
        json={"workspace_id": workspace_id, "raw_input": "目标"},
        headers=auth_headers,
    ).json()
    final = _final(
        client.post(f"/workflow/runs/{run['id']}/plan/stream", headers=auth_headers)
    )
    assert final["ok"] is True, final
    return run["id"]


def _exec(client, auth_headers, run_id, idx):
    return client.post(
        f"/workflow/runs/{run_id}/steps/{idx}/execute", headers=auth_headers
    )


def _detail(client, auth_headers, run_id) -> dict:
    return client.get(f"/workflow/runs/{run_id}", headers=auth_headers).json()


# ---------------------------------------------------------------------------
#  驳回并重新规划
# ---------------------------------------------------------------------------


class TestRejectStep:
    def test_reject_clears_from_step_and_sets_planning(
        self, client, auth_headers, wf_env, workspace, monkeypatch
    ):
        run_id = _approved_run(client, auth_headers, workspace["id"], monkeypatch)
        # 必须在 _approved_run 之后：它内部会替换掉假模型
        _use_llm(monkeypatch, [OUTPUT_TEXT])
        assert _final(_exec(client, auth_headers, run_id, 1))["ok"] is True

        resp = client.post(
            f"/workflow/runs/{run_id}/steps/2/reject",
            json={"comment": "第二步的切入角度不对"},
            headers=auth_headers,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["rejected_from"] == 2
        assert body["removed"] == 2  # 第 2、3 步被清掉
        assert body["preserved"] == 1  # 第 1 步保留
        assert body["run"]["status"] == "planning"
        assert body["run"]["reject_comment"] == "第二步的切入角度不对"

        steps = body["run"]["steps"]
        assert [s["idx"] for s in steps] == [1]
        assert steps[0]["status"] == "done"

    def test_replan_preserves_done_output_and_ids(
        self, client, auth_headers, wf_env, workspace, monkeypatch
    ):
        """★ 本动作的核心价值：驳回第 N 步不能把前 N-1 步的产出丢掉。

        若整表重规划，用户会白白损失已经花过钱的那几步 —— 而且界面看起来正常。
        """
        run_id = _approved_run(client, auth_headers, workspace["id"], monkeypatch)
        # 必须在 _approved_run 之后：它内部会替换掉假模型
        _use_llm(monkeypatch, [OUTPUT_TEXT])
        _exec(client, auth_headers, run_id, 1)
        before = _detail(client, auth_headers, run_id)
        first_id = before["steps"][0]["id"]
        first_output = before["steps"][0]["output"]

        client.post(
            f"/workflow/runs/{run_id}/steps/2/reject",
            json={"comment": "换个做法"},
            headers=auth_headers,
        )

        # 重新规划（模型返回 2 步）
        _use_llm(monkeypatch, [PLAN_SUFFIX])
        final = _final(
            client.post(f"/workflow/runs/{run_id}/plan/stream", headers=auth_headers)
        )
        assert final["ok"] is True, final

        after = _detail(client, auth_headers, run_id)
        assert [s["idx"] for s in after["steps"]] == [1, 2, 3]

        kept = after["steps"][0]
        assert kept["id"] == first_id  # 行没被重建
        assert kept["status"] == "done"
        assert kept["output"] == first_output  # 产出没丢

        assert after["steps"][1]["title"] == "重规划后的第一步"
        assert after["steps"][2]["title"] == "重规划后的第二步"
        assert after["status"] == "awaiting_approval"

    def test_run_plan_stays_in_sync_with_steps_table(
        self, client, auth_headers, wf_env, workspace, monkeypatch
    ):
        """run.plan.steps 与 steps 表分叉会让「界面上的任务书」与
        「会被执行的步骤」悄悄不一致 —— 这是最难发现的一类错误。"""
        run_id = _approved_run(client, auth_headers, workspace["id"], monkeypatch)
        # 必须在 _approved_run 之后：它内部会替换掉假模型
        _use_llm(monkeypatch, [OUTPUT_TEXT])
        _exec(client, auth_headers, run_id, 1)
        client.post(
            f"/workflow/runs/{run_id}/steps/2/reject",
            json={"comment": "改"},
            headers=auth_headers,
        )
        _use_llm(monkeypatch, [PLAN_SUFFIX])
        client.post(f"/workflow/runs/{run_id}/plan/stream", headers=auth_headers)

        detail = _detail(client, auth_headers, run_id)
        from_table = [(s["idx"], s["kind"], s["status"]) for s in detail["steps"]]
        from_plan = [
            (s["idx"], s["kind"], s["status"]) for s in detail["plan"]["steps"]
        ]
        assert from_table == from_plan

    def test_replan_prompt_carries_prior_output_and_comment(
        self, client, auth_headers, wf_env, workspace, monkeypatch
    ):
        """不带这两样的话，模型会重复规划已完成的事，也无从知道要改什么。"""
        run_id = _approved_run(client, auth_headers, workspace["id"], monkeypatch)
        # 必须在 _approved_run 之后：它内部会替换掉假模型
        _use_llm(monkeypatch, [OUTPUT_TEXT])
        _exec(client, auth_headers, run_id, 1)
        client.post(
            f"/workflow/runs/{run_id}/steps/2/reject",
            json={"comment": "必须换成表格形式"},
            headers=auth_headers,
        )

        fake = _use_llm(monkeypatch, [PLAN_SUFFIX])
        client.post(f"/workflow/runs/{run_id}/plan/stream", headers=auth_headers)

        prompt = fake.prompts[-1]
        assert "必须换成表格形式" in prompt
        assert "重新规划" in prompt
        assert OUTPUT_TEXT in prompt  # 前序产出
        assert "不要重复规划" in prompt

    def test_cannot_reject_done_step(self, client, auth_headers, wf_env, workspace, monkeypatch):
        run_id = _approved_run(client, auth_headers, workspace["id"], monkeypatch)
        # 必须在 _approved_run 之后：它内部会替换掉假模型
        _use_llm(monkeypatch, [OUTPUT_TEXT])
        _exec(client, auth_headers, run_id, 1)
        resp = client.post(
            f"/workflow/runs/{run_id}/steps/1/reject",
            json={"comment": "x"},
            headers=auth_headers,
        )
        assert resp.status_code == 409

    def test_cannot_reject_skipped_step(self, client, auth_headers, wf_env, workspace, monkeypatch):
        run_id = _approved_run(client, auth_headers, workspace["id"], monkeypatch)
        client.post(f"/workflow/runs/{run_id}/steps/1/skip", headers=auth_headers)
        resp = client.post(
            f"/workflow/runs/{run_id}/steps/1/reject",
            json={"comment": "x"},
            headers=auth_headers,
        )
        assert resp.status_code == 409

    def test_reject_all_from_first_step_clears_everything(
        self, client, auth_headers, wf_env, workspace, monkeypatch
    ):
        run_id = _approved_run(client, auth_headers, workspace["id"], monkeypatch)
        resp = client.post(
            f"/workflow/runs/{run_id}/steps/1/reject",
            json={"comment": "整体重来"},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["removed"] == 3
        assert resp.json()["run"]["steps"] == []


# ---------------------------------------------------------------------------
#  任务书编辑锁
# ---------------------------------------------------------------------------


class TestSavePlanLock:
    def test_cannot_replace_plan_after_execution_started(
        self, client, auth_headers, wf_env, workspace, monkeypatch
    ):
        """整表替换会把 done 步骤重置成 pending，产出永久丢失。"""
        run_id = _approved_run(client, auth_headers, workspace["id"], monkeypatch)
        # 必须在 _approved_run 之后：它内部会替换掉假模型
        _use_llm(monkeypatch, [OUTPUT_TEXT])
        _exec(client, auth_headers, run_id, 1)

        resp = client.put(
            f"/workflow/runs/{run_id}/plan",
            json={"goal": "g", "steps": [{"kind": "llm", "title": "换掉全部"}]},
            headers=auth_headers,
        )
        assert resp.status_code == 409
        assert "驳回并重新规划" in resp.json()["error"]["message"]

        # 产出确实还在
        detail = _detail(client, auth_headers, run_id)
        assert detail["steps"][0]["status"] == "done"
        assert detail["steps"][0]["output"]

    def test_save_plan_still_allowed_before_execution(
        self, client, auth_headers, wf_env, workspace, monkeypatch
    ):
        """尚未批准时应能自由编辑任务书 —— 锁只在执行开始后生效。"""
        run_id = _planned_run(client, auth_headers, workspace["id"], monkeypatch)
        resp = client.put(
            f"/workflow/runs/{run_id}/plan",
            json={"goal": "改过的目标", "steps": [{"kind": "llm", "title": "新步骤"}]},
            headers=auth_headers,
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["plan"]["goal"] == "改过的目标"

    def test_reject_step_works_from_approved_run(
        self, client, auth_headers, wf_env, workspace, monkeypatch
    ):
        """已批准但尚未执行时，驳回单步应直接可用（无需先整体驳回）。"""
        run_id = _approved_run(client, auth_headers, workspace["id"], monkeypatch)
        resp = client.post(
            f"/workflow/runs/{run_id}/steps/2/reject",
            json={"comment": "这步改一下"},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["preserved"] == 1


# ---------------------------------------------------------------------------
#  终止
# ---------------------------------------------------------------------------


class TestCancelRun:
    def test_cancel_keeps_finished_outputs(self, client, auth_headers, wf_env, workspace, monkeypatch):
        """终止 = 不再推进，不是抹掉已经拿到的结果。"""
        run_id = _approved_run(client, auth_headers, workspace["id"], monkeypatch)
        # 必须在 _approved_run 之后：它内部会替换掉假模型
        _use_llm(monkeypatch, [OUTPUT_TEXT])
        _exec(client, auth_headers, run_id, 1)

        resp = client.post(
            f"/workflow/runs/{run_id}/cancel",
            json={"comment": "方向不对，先停"},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        detail = resp.json()["run"]
        assert detail["status"] == "cancelled"
        assert detail["steps"][0]["status"] == "done"
        assert detail["steps"][0]["output"]

    def test_cannot_execute_after_cancel(self, client, auth_headers, wf_env, workspace, monkeypatch):
        run_id = _approved_run(client, auth_headers, workspace["id"], monkeypatch)
        # 必须在 _approved_run 之后：它内部会替换掉假模型
        _use_llm(monkeypatch, [OUTPUT_TEXT])
        client.post(
            f"/workflow/runs/{run_id}/cancel", json={}, headers=auth_headers
        )
        final = _final(_exec(client, auth_headers, run_id, 1))
        assert final["ok"] is False

    def test_cannot_cancel_done_run(self, client, auth_headers, wf_env, workspace, monkeypatch):
        run_id = _approved_run(client, auth_headers, workspace["id"], monkeypatch)
        # 必须在 _approved_run 之后：它内部会替换掉假模型
        _use_llm(monkeypatch, [OUTPUT_TEXT])
        for idx in (1, 2, 3):
            _exec(client, auth_headers, run_id, idx)
        assert _detail(client, auth_headers, run_id)["status"] == "done"

        resp = client.post(
            f"/workflow/runs/{run_id}/cancel", json={}, headers=auth_headers
        )
        assert resp.status_code == 409

    def test_cannot_cancel_twice(self, client, auth_headers, wf_env, workspace, monkeypatch):
        run_id = _approved_run(client, auth_headers, workspace["id"], monkeypatch)
        assert (
            client.post(
                f"/workflow/runs/{run_id}/cancel", json={}, headers=auth_headers
            ).status_code
            == 200
        )
        assert (
            client.post(
                f"/workflow/runs/{run_id}/cancel", json={}, headers=auth_headers
            ).status_code
            == 409
        )


# ---------------------------------------------------------------------------
#  执行中切换模式（设计 §7.3）
# ---------------------------------------------------------------------------


class TestModeSwitch:
    GATED_PLAN = """```json
{
  "goal": "g", "audience": "", "constraints": [], "assumptions": [],
  "steps": [
    {"kind": "llm", "title": "需要把关的一步", "action": {"prompt_ref": "x"},
     "requires_approval": true}
  ]
}
```"""

    def test_switching_to_auto_unblocks_gate_immediately(
        self, client, auth_headers, wf_env, workspace, monkeypatch
    ):
        """用户常在看到前两步质量后决定放权 —— 把模式锁死在整个 run 上是过度约束。"""
        _use_llm(monkeypatch, [self.GATED_PLAN])
        run = client.post(
            "/workflow/runs",
            json={"workspace_id": workspace["id"], "raw_input": "目标", "mode": "manual"},
            headers=auth_headers,
        ).json()
        _final(client.post(f"/workflow/runs/{run['id']}/plan/stream", headers=auth_headers))
        client.post(f"/workflow/runs/{run['id']}/approve", headers=auth_headers)

        detail = _detail(client, auth_headers, run["id"])
        assert detail["steps"][0]["needs_gate"] is True
        assert detail["next_runnable_idx"] is None

        patched = client.patch(
            f"/workflow/runs/{run['id']}", json={"mode": "auto"}, headers=auth_headers
        )
        assert patched.status_code == 200
        # 改完立刻生效：门禁消失、下一步可执行
        assert patched.json()["steps"][0]["needs_gate"] is False
        assert patched.json()["next_runnable_idx"] == 1

        _use_llm(monkeypatch, [OUTPUT_TEXT])
        assert _final(_exec(client, auth_headers, run["id"], 1))["ok"] is True

    def test_switching_to_manual_adds_gate(
        self, client, auth_headers, wf_env, workspace, monkeypatch
    ):
        _use_llm(monkeypatch, [self.GATED_PLAN])
        run = client.post(
            "/workflow/runs",
            json={"workspace_id": workspace["id"], "raw_input": "目标", "mode": "auto"},
            headers=auth_headers,
        ).json()
        _final(client.post(f"/workflow/runs/{run['id']}/plan/stream", headers=auth_headers))
        client.post(f"/workflow/runs/{run['id']}/approve", headers=auth_headers)
        assert _detail(client, auth_headers, run["id"])["steps"][0]["needs_gate"] is False

        patched = client.patch(
            f"/workflow/runs/{run['id']}", json={"mode": "manual"}, headers=auth_headers
        )
        assert patched.json()["steps"][0]["needs_gate"] is True
        assert patched.json()["next_runnable_idx"] is None
