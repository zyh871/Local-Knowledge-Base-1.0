"""P4：澄清相位的端到端测试。

重点验证两件容易做错、且会直接伤害用户的事：

  * **澄清结论必须真的进任务书提示词** —— 否则用户前面点的那些选项会被静默丢掉，
    计划又回到「基于原始一句话」的水平，而这个损失完全不可见
  * **轮次上限与跳过都要落地** —— 否则会无限追问，或者用户点了跳过却还在问
"""

from __future__ import annotations

import json

import pytest

CLARIFY_ROUND_1 = """我先确认两个会影响结论口径的点。

```json
{
  "sufficient": false,
  "questions": [
    {
      "question": "这份对比的读者是谁？",
      "options": [
        {"label": "技术决策者", "value": "tech"},
        {"label": "管理层", "value": "exec"}
      ],
      "recommended": "tech",
      "reason": "你提到要做技术选型"
    }
  ]
}
```"""

CLARIFY_SUFFICIENT = """信息已经足够了，我知道要产出什么、给谁看。

```json
{"sufficient": true, "questions": []}
```"""

VALID_PLAN = """来看一下边界。

```json
{
  "goal": "产出对比结论",
  "audience": "技术决策者",
  "constraints": ["中文"],
  "assumptions": [],
  "steps": [{"kind": "llm", "title": "起草", "action": {"prompt_ref": "起草"}}]
}
```"""


class _FakeLLM:
    """按调用顺序依次吐出预置文本，并记录收到的提示词。"""

    def __init__(self, texts: list[str], chunk_size: int = 13) -> None:
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
def run(client, auth_headers, wf_env):
    workspace = client.post(
        "/workflow/workspaces", json={"name": "澄清测试"}, headers=auth_headers
    ).json()
    return client.post(
        "/workflow/runs",
        json={"workspace_id": workspace["id"], "raw_input": "做一份大模型成本对比"},
        headers=auth_headers,
    ).json()


def _use_llm(monkeypatch, texts: list[str]) -> _FakeLLM:
    fake = _FakeLLM(texts)
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
    assert finals, f"没有 final 事件: {response.text[:400]}"
    return finals[-1]["content"]


def _clarify(client, auth_headers, run_id):
    return client.post(f"/workflow/runs/{run_id}/clarify/stream", headers=auth_headers)


def _detail(client, auth_headers, run_id) -> dict:
    return client.get(f"/workflow/runs/{run_id}", headers=auth_headers).json()


# ---------------------------------------------------------------------------
#  产出问题
# ---------------------------------------------------------------------------


class TestStreamClarify:
    def test_generates_and_persists_round(self, client, auth_headers, wf_env, run, monkeypatch):
        _use_llm(monkeypatch, [CLARIFY_ROUND_1])
        resp = _clarify(client, auth_headers, run["id"])
        assert resp.status_code == 200

        kinds = [e["type"] for e in _events(resp)]
        assert kinds[0] == "workflow_phase"
        assert "thinking_delta" in kinds
        assert "clarify_questions" in kinds
        assert kinds[-1] == "final"

        final = _final(resp)
        assert final["ok"] is True
        assert final["round"] == 1
        assert final["sufficient"] is False
        assert len(final["questions"]) == 1
        assert final["questions"][0]["recommended"] == "tech"

        detail = _detail(client, auth_headers, run["id"])
        assert detail["status"] == "clarifying"
        assert detail["clarify_round"] == 1
        assert len(detail["clarify"]["rounds"]) == 1
        assert detail["clarify"]["rounds"][0]["questions"][0]["id"] == "q1"

    def test_thinking_stream_has_no_json(self, client, auth_headers, wf_env, run, monkeypatch):
        _use_llm(monkeypatch, [CLARIFY_ROUND_1])
        thinking = "".join(
            e["content"]["text"]
            for e in _events(_clarify(client, auth_headers, run["id"]))
            if e["type"] == "thinking_delta"
        )
        assert "我先确认两个" in thinking
        assert "{" not in thinking

    def test_sufficient_does_not_consume_a_round(self, client, auth_headers, wf_env, run, monkeypatch):
        """信息已足够时不该白扣一轮 —— 否则用户会在什么都没看到的情况下少一次机会。"""
        _use_llm(monkeypatch, [CLARIFY_SUFFICIENT])
        final = _final(_clarify(client, auth_headers, run["id"]))
        assert final["sufficient"] is True
        assert final["questions"] == []

        detail = _detail(client, auth_headers, run["id"])
        assert detail["clarify_round"] == 0
        assert detail["clarify"]["rounds"] == []
        assert detail["clarify"]["sufficient"] is True

    def test_round_cap_refuses_extra_rounds(self, client, auth_headers, wf_env, run, monkeypatch):
        from app.core.config import clear_settings_cache
        from app.repositories import workflow_repo as repo

        monkeypatch.setenv("WORKFLOW_MAX_CLARIFY_ROUNDS", "1")
        clear_settings_cache()
        repo.reset_connection()

        _use_llm(monkeypatch, [CLARIFY_ROUND_1])
        assert _final(_clarify(client, auth_headers, run["id"]))["ok"] is True

        second = _final(_clarify(client, auth_headers, run["id"]))
        assert second["ok"] is False
        assert "上限" in second["error"]

    def test_cannot_clarify_after_approved(self, client, auth_headers, wf_env, run, monkeypatch):
        _use_llm(monkeypatch, [CLARIFY_ROUND_1, VALID_PLAN])
        _clarify(client, auth_headers, run["id"])
        client.post(f"/workflow/runs/{run['id']}/plan/stream", headers=auth_headers)
        client.post(f"/workflow/runs/{run['id']}/approve", headers=auth_headers)

        final = _final(_clarify(client, auth_headers, run["id"]))
        assert final["ok"] is False
        assert "驳回" in final["error"] or "不能" in final["error"]


# ---------------------------------------------------------------------------
#  回答
# ---------------------------------------------------------------------------


class TestAnswerClarify:
    def test_records_answers(self, client, auth_headers, wf_env, run, monkeypatch):
        _use_llm(monkeypatch, [CLARIFY_ROUND_1])
        _clarify(client, auth_headers, run["id"])

        resp = client.post(
            f"/workflow/runs/{run['id']}/clarify/answer",
            json={"answers": [{"id": "q1", "value": "exec"}]},
            headers=auth_headers,
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["answered"] == 1

        detail = _detail(client, auth_headers, run["id"])
        answers = detail["clarify"]["rounds"][0]["answers"]
        assert answers["q1"]["value"] == "exec"
        assert detail["clarify"]["rounds"][0]["answered_at"]

    def test_partial_answers_allowed(self, client, auth_headers, wf_env, run, monkeypatch):
        """只答一部分是正常的 —— 强行要求全填会把用户推向「跳过」。"""
        two_questions = """```json
{"sufficient": false, "questions": [
  {"question": "A?", "options": ["a1", "a2"]},
  {"question": "B?", "options": ["b1", "b2"]}
]}
```"""
        _use_llm(monkeypatch, [two_questions])
        _clarify(client, auth_headers, run["id"])

        resp = client.post(
            f"/workflow/runs/{run['id']}/clarify/answer",
            json={"answers": [{"id": "q1", "value": "a1"}]},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["answered"] == 1

    def test_unknown_ids_reported_but_not_fatal(self, client, auth_headers, wf_env, run, monkeypatch):
        _use_llm(monkeypatch, [CLARIFY_ROUND_1])
        _clarify(client, auth_headers, run["id"])

        resp = client.post(
            f"/workflow/runs/{run['id']}/clarify/answer",
            json={"answers": [{"id": "q1", "value": "tech"}, {"id": "q999", "text": "x"}]},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["unknown"] == ["q999"]

    def test_all_unknown_rejected(self, client, auth_headers, wf_env, run, monkeypatch):
        _use_llm(monkeypatch, [CLARIFY_ROUND_1])
        _clarify(client, auth_headers, run["id"])
        resp = client.post(
            f"/workflow/runs/{run['id']}/clarify/answer",
            json={"answers": [{"id": "q999", "value": "x"}]},
            headers=auth_headers,
        )
        assert resp.status_code == 400

    def test_double_submit_rejected(self, client, auth_headers, wf_env, run, monkeypatch):
        _use_llm(monkeypatch, [CLARIFY_ROUND_1])
        _clarify(client, auth_headers, run["id"])
        payload = {"answers": [{"id": "q1", "value": "tech"}]}
        assert (
            client.post(
                f"/workflow/runs/{run['id']}/clarify/answer",
                json=payload,
                headers=auth_headers,
            ).status_code
            == 200
        )
        assert (
            client.post(
                f"/workflow/runs/{run['id']}/clarify/answer",
                json=payload,
                headers=auth_headers,
            ).status_code
            == 409
        )

    def test_answer_without_questions_rejected(self, client, auth_headers, wf_env, run):
        resp = client.post(
            f"/workflow/runs/{run['id']}/clarify/answer",
            json={"answers": [{"id": "q1", "value": "x"}]},
            headers=auth_headers,
        )
        assert resp.status_code == 409

    def test_second_round_prompt_includes_first_round_answers(
        self, client, auth_headers, wf_env, run, monkeypatch
    ):
        """不带历史的话，第二轮会换种说法再问一遍同样的事。"""
        fake = _use_llm(monkeypatch, [CLARIFY_ROUND_1])
        _clarify(client, auth_headers, run["id"])
        client.post(
            f"/workflow/runs/{run['id']}/clarify/answer",
            json={"answers": [{"id": "q1", "value": "exec"}]},
            headers=auth_headers,
        )
        _clarify(client, auth_headers, run["id"])

        second_prompt = fake.prompts[-1]
        assert "已经问过并得到答复的内容" in second_prompt
        assert "管理层" in second_prompt  # label 而不是 value


# ---------------------------------------------------------------------------
#  跳过
# ---------------------------------------------------------------------------


class TestSkipClarify:
    def test_skip_marks_state_and_stops_further_rounds(self, client, auth_headers, wf_env, run):
        resp = client.post(
            f"/workflow/runs/{run['id']}/clarify/skip",
            json={"reason": "需求已经很明确"},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        detail = resp.json()["run"]
        assert detail["clarify"]["skipped"] is True
        assert detail["clarify"]["skip_reason"] == "需求已经很明确"

        # 跳过后再问会被挡住（不依赖前端每次都记得检查）
        final = _final(_clarify(client, auth_headers, run["id"]))
        assert final["ok"] is False
        assert "跳过" in final["error"]

    def test_skip_keeps_existing_rounds(self, client, auth_headers, wf_env, run, monkeypatch):
        """用户可能答了一轮才觉得够了，那些答案仍要进任务书提示词。"""
        _use_llm(monkeypatch, [CLARIFY_ROUND_1])
        _clarify(client, auth_headers, run["id"])
        client.post(
            f"/workflow/runs/{run['id']}/clarify/answer",
            json={"answers": [{"id": "q1", "value": "tech"}]},
            headers=auth_headers,
        )
        resp = client.post(
            f"/workflow/runs/{run['id']}/clarify/skip", json={}, headers=auth_headers
        )
        rounds = resp.json()["run"]["clarify"]["rounds"]
        assert len(rounds) == 1
        assert rounds[0]["answers"]["q1"]["value"] == "tech"

    def test_cannot_skip_after_approved(self, client, auth_headers, wf_env, run, monkeypatch):
        _use_llm(monkeypatch, [CLARIFY_ROUND_1, VALID_PLAN])
        _clarify(client, auth_headers, run["id"])
        client.post(f"/workflow/runs/{run['id']}/plan/stream", headers=auth_headers)
        client.post(f"/workflow/runs/{run['id']}/approve", headers=auth_headers)
        resp = client.post(
            f"/workflow/runs/{run['id']}/clarify/skip", json={}, headers=auth_headers
        )
        assert resp.status_code == 409


# ---------------------------------------------------------------------------
#  澄清结论进任务书
# ---------------------------------------------------------------------------


class TestPlanUsesClarifications:
    def test_plan_prompt_contains_clarification_answers(
        self, client, auth_headers, wf_env, run, monkeypatch
    ):
        """★ 这是 P4 的核心价值：用户的选择必须真的影响任务书。

        不带进提示词的话，澄清就只是走个形式，而这个损失完全不可见。
        """
        fake = _use_llm(monkeypatch, [CLARIFY_ROUND_1, VALID_PLAN])
        _clarify(client, auth_headers, run["id"])
        client.post(
            f"/workflow/runs/{run['id']}/clarify/answer",
            json={"answers": [{"id": "q1", "value": "exec"}]},
            headers=auth_headers,
        )
        client.post(f"/workflow/runs/{run['id']}/plan/stream", headers=auth_headers)

        plan_prompt = fake.prompts[-1]
        assert "需求澄清" in plan_prompt
        assert "管理层" in plan_prompt
        assert "这份对比的读者是谁？" in plan_prompt

    def test_freeform_answer_reaches_plan(self, client, auth_headers, wf_env, run, monkeypatch):
        fake = _use_llm(monkeypatch, [CLARIFY_ROUND_1, VALID_PLAN])
        _clarify(client, auth_headers, run["id"])
        client.post(
            f"/workflow/runs/{run['id']}/clarify/answer",
            json={"answers": [{"id": "q1", "text": "给我自己存档用"}]},
            headers=auth_headers,
        )
        client.post(f"/workflow/runs/{run['id']}/plan/stream", headers=auth_headers)
        assert "给我自己存档用" in fake.prompts[-1]

    def test_plan_allowed_right_after_skip(self, client, auth_headers, wf_env, run, monkeypatch):
        """跳过澄清后要能立刻生成任务书，不能卡住。"""
        _use_llm(monkeypatch, [VALID_PLAN])
        client.post(
            f"/workflow/runs/{run['id']}/clarify/skip", json={}, headers=auth_headers
        )
        final = _final(
            client.post(f"/workflow/runs/{run['id']}/plan/stream", headers=auth_headers)
        )
        assert final["ok"] is True

    def test_clarification_exposed_in_run_payload(self, client, auth_headers, wf_env, run, monkeypatch):
        """前端要展示「原话 → 澄清补充 → 任务书」三段对比，数据得能拿到。"""
        _use_llm(monkeypatch, [CLARIFY_ROUND_1])
        _clarify(client, auth_headers, run["id"])
        client.post(
            f"/workflow/runs/{run['id']}/clarify/answer",
            json={"answers": [{"id": "q1", "value": "tech"}]},
            headers=auth_headers,
        )
        detail = _detail(client, auth_headers, run["id"])
        assert detail["raw_input"] == "做一份大模型成本对比"
        round_one = detail["clarify"]["rounds"][0]
        assert round_one["questions"][0]["question"]
        assert round_one["answers"]["q1"]["value"] == "tech"
