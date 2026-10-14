"""P2：任务书生成 / 编辑 / 审批的端到端测试。

用假 LLM 覆盖两条路径：
  * 模型给出合法计划 → 落库 + 状态迁移到 `awaiting_approval`
  * 模型给出垃圾 → 明确的失败事件，且**不留半份任务书**

重点断言的是「接口测不出、但会实质伤害用户」的地方：
  * 校验修正必须回传（issues 不能吞）
  * 用户手改的任务书必须重新过白名单（否则前端自由编辑 = 绕过工具白名单的后门）
  * 状态机不允许从已批准倒着走回未审批
"""

from __future__ import annotations

import io
import json

import pytest

VALID_PLAN = """我先看一下这个需求的边界：需要横向对比三家厂商的公开定价，
再统一折算成每千 token 的成本，最后给出结论。为了可比，我假设都取标准 API 档位。

```json
{
  "goal": "产出三家厂商推理成本对比结论",
  "audience": "技术决策者",
  "constraints": ["中文", "含每千 token 价格"],
  "assumptions": ["均取标准 API 档位"],
  "steps": [
    {
      "kind": "tool",
      "title": "搜索三家厂商的最新定价",
      "action": {"tool": "web_search", "args": {"query": "三家厂商 API 定价 每千 token"}}
    },
    {
      "kind": "llm",
      "title": "把定价折算成统一口径",
      "action": {"prompt_ref": "统一折算成本口径"}
    },
    {
      "kind": "synthesis",
      "title": "整合成对比结论",
      "action": {"prompt_ref": "整合对比结论"},
      "requires_approval": true
    }
  ]
}
```"""

GARBAGE_PLAN = "我觉得这个任务很有意思，但我今天不想输出 JSON。"


class _FakeLLM:
    """可流式的假模型。分片大小可调，用于覆盖「分界点落在分片中间」。"""

    def __init__(self, text: str, chunk_size: int = 11) -> None:
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
def run(client, auth_headers, wf_env):
    workspace = client.post(
        "/workflow/workspaces", json={"name": "计划测试"}, headers=auth_headers
    ).json()
    return client.post(
        "/workflow/runs",
        json={
            "workspace_id": workspace["id"],
            "raw_input": "对比三家大模型的推理成本并给出结论",
        },
        headers=auth_headers,
    ).json()


def _use_llm(monkeypatch, text: str, chunk_size: int = 11):
    fake = _FakeLLM(text, chunk_size=chunk_size)
    monkeypatch.setattr("app.rag.providers.get_llm", lambda model=None: fake)
    return fake


def _events(response) -> list[dict]:
    out: list[dict] = []
    for block in response.text.split("\n\n"):
        block = block.strip()
        if not block.startswith("data: "):
            continue
        out.append(json.loads(block[len("data: ") :]))
    return out


def _plan(client, auth_headers, run_id):
    return client.post(
        f"/workflow/runs/{run_id}/plan/stream", headers=auth_headers
    )


# ---------------------------------------------------------------------------
#  生成任务书
# ---------------------------------------------------------------------------


class TestStreamPlan:
    def test_persists_plan_steps_and_status(self, client, auth_headers, wf_env, run, monkeypatch):
        _use_llm(monkeypatch, VALID_PLAN)
        resp = _plan(client, auth_headers, run["id"])
        assert resp.status_code == 200, resp.text

        final = [e for e in _events(resp) if e["type"] == "final"]
        assert final and final[-1]["content"]["ok"] is True

        detail = client.get(f"/workflow/runs/{run['id']}", headers=auth_headers).json()
        assert detail["status"] == "awaiting_approval"
        assert detail["plan"]["goal"].startswith("产出三家厂商")
        assert len(detail["plan"]["steps"]) == 3
        # steps 表与 plan 必须一致 —— 前端读的是 steps（带真实 id）
        assert [s["idx"] for s in detail["steps"]] == [1, 2, 3]
        assert detail["steps"][0]["action"]["tool"] == "web_search"
        assert detail["steps"][2]["requires_approval"] is True

    def test_emits_phase_thinking_and_plan_ready(self, client, auth_headers, wf_env, run, monkeypatch):
        _use_llm(monkeypatch, VALID_PLAN)
        events = _events(_plan(client, auth_headers, run["id"]))
        kinds = [e["type"] for e in events]

        assert kinds[0] == "workflow_phase"
        assert events[0]["content"]["phase"] == "plan"
        assert events[0]["content"]["status"] == "start"
        # 起始事件带上实际使用的模型，前端可据此提示"用的是哪个模型规划的"
        assert events[0]["content"].get("model")
        assert "thinking_delta" in kinds
        assert "plan_ready" in kinds
        assert kinds[-1] == "final"

        # 推理流里绝不能出现 JSON 花括号 —— 否则用户看到的"思考"是一坨代码
        thinking = "".join(
            e["content"]["text"] for e in events if e["type"] == "thinking_delta"
        )
        assert "我先看一下这个需求的边界" in thinking
        assert "{" not in thinking

    def test_chunk_boundaries_do_not_break_parsing(self, client, auth_headers, wf_env, ws_run, monkeypatch):
        """分片大小不应该影响结果 —— 否则线上表现会随网络抖动而变。"""
        run = ws_run
        for size in (1, 3, 17, 5000):
            _use_llm(monkeypatch, VALID_PLAN, chunk_size=size)
            client.post(
                f"/workflow/runs/{run['id']}/reject",
                json={"comment": "重来"},
                headers=auth_headers,
            )
            resp = _plan(client, auth_headers, run["id"])
            final = [e for e in _events(resp) if e["type"] == "final"][-1]
            assert final["content"]["ok"] is True, f"chunk_size={size} 失败: {final}"

    def test_client_disconnect_rolls_back_status(self, client, auth_headers, wf_env, run, monkeypatch):
        """客户端中途断开（刷新页面）不能把任务永远留在「规划中」。

        断开有两种表现 —— `asyncio.CancelledError` 与 `GeneratorExit`。
        这里用 `aclose()` 触发后者（真实刷新页面走的就是这条），
        因此它覆盖的是「只处理 CancelledError」那个常见漏洞。
        """
        import asyncio

        from app.core.config import get_settings
        from app.repositories import workflow_repo as repo
        from app.services.workflow_service import WorkflowService

        _use_llm(monkeypatch, VALID_PLAN)
        service = WorkflowService(
            settings=get_settings(), graph=client.app.state.workflow_graph
        )

        async def _consume_then_disconnect():
            agen = service.stream_plan(run_id=run["id"], username="admin")
            await agen.__anext__()  # 至少收到一个事件，确保已进入 planning
            assert repo.get_run(run["id"], with_steps=False)["status"] == "planning"
            await agen.aclose()  # 模拟客户端断开

        asyncio.run(_consume_then_disconnect())

        assert repo.get_run(run["id"], with_steps=False)["status"] == "draft"

    def test_garbage_output_fails_without_saving_plan(self, client, auth_headers, wf_env, run, monkeypatch):
        _use_llm(monkeypatch, GARBAGE_PLAN)
        final = [e for e in _events(_plan(client, auth_headers, run["id"])) if e["type"] == "final"][-1]

        assert final["content"]["ok"] is False
        assert "JSON" in final["content"]["error"]

        detail = client.get(f"/workflow/runs/{run['id']}", headers=auth_headers).json()
        assert detail["status"] == "failed"
        # 关键：失败时绝不留下半份任务书
        assert detail["plan"] is None
        assert detail["steps"] == []

    def test_missing_run_returns_error_event(self, client, auth_headers, wf_env):
        """SSE 头已发出，错误只能以 final 事件返回，不能变成 404 断流。"""
        resp = _plan(client, auth_headers, "run_nope")
        assert resp.status_code == 200
        final = [e for e in _events(resp) if e["type"] == "final"][-1]
        assert final["content"]["ok"] is False

    def test_cannot_plan_after_approved(self, client, auth_headers, wf_env, run, monkeypatch):
        _use_llm(monkeypatch, VALID_PLAN)
        _plan(client, auth_headers, run["id"])
        client.post(f"/workflow/runs/{run['id']}/approve", headers=auth_headers)

        final = [e for e in _events(_plan(client, auth_headers, run["id"])) if e["type"] == "final"][-1]
        assert final["content"]["ok"] is False
        assert "批准" in final["content"]["error"] or "驳回" in final["content"]["error"]

    def test_plan_uses_reference_file_context(self, client, auth_headers, wf_env, run, monkeypatch):
        """引用文件必须真的进提示词 —— 否则「上传了但没用」很难被发现。"""
        client.post(
            f"/workflow/runs/{run['id']}/files",
            files={"files": ("note.txt", io.BytesIO("参考内容：单价 0.5 元".encode()), "text/plain")},
            headers=auth_headers,
        )
        captured = {}

        class _Capturing(_FakeLLM):
            def stream(self, prompt):
                captured["prompt"] = prompt
                return super().stream(prompt)

        fake = _Capturing(VALID_PLAN)
        monkeypatch.setattr("app.rag.providers.get_llm", lambda model=None: fake)
        _plan(client, auth_headers, run["id"])

        assert "note.txt" in captured["prompt"]
        assert "单价 0.5 元" in captured["prompt"]


@pytest.fixture
def ws_run(client, auth_headers, wf_env):
    """可复用的任务（供需要多轮的用例）。"""
    workspace = client.post(
        "/workflow/workspaces", json={"name": "多轮"}, headers=auth_headers
    ).json()
    return client.post(
        "/workflow/runs",
        json={"workspace_id": workspace["id"], "raw_input": "对比成本"},
        headers=auth_headers,
    ).json()


# ---------------------------------------------------------------------------
#  保存用户编辑
# ---------------------------------------------------------------------------


class TestSavePlan:
    def test_user_edits_are_revalidated(self, client, auth_headers, wf_env, run):
        """前端自由编辑不能成为绕过工具白名单的后门。"""
        payload = {
            "goal": "用户改过的目标",
            "audience": "",
            "constraints": [],
            "assumptions": [],
            "file_refs": [],
            "steps": [
                {
                    "kind": "tool",
                    "title": "删库",
                    "action": {"tool": "rm_rf", "args": {"path": "/"}},
                }
            ],
        }
        resp = client.put(
            f"/workflow/runs/{run['id']}/plan", json=payload, headers=auth_headers
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()

        step = data["plan"]["steps"][0]
        assert step["kind"] == "llm"  # 不可用的工具被降级
        assert "tool" not in step["action"]
        assert any("不可用" in i for i in data["issues"])
        assert data["run"]["status"] == "awaiting_approval"

    def test_forced_approval_tool_marked_on_save(self, client, auth_headers, wf_env, run):
        payload = {
            "goal": "g",
            "steps": [
                {
                    "kind": "tool",
                    "title": "执行代码",
                    "action": {"tool": "code_executor", "args": {"code": "print(1)"}},
                    "requires_approval": False,
                }
            ],
        }
        data = client.put(
            f"/workflow/runs/{run['id']}/plan", json=payload, headers=auth_headers
        ).json()
        assert data["plan"]["steps"][0]["requires_approval"] is True
        assert any("必须人工确认" in i for i in data["issues"])

    def test_empty_steps_rejected(self, client, auth_headers, wf_env, run):
        resp = client.put(
            f"/workflow/runs/{run['id']}/plan",
            json={"goal": "g", "steps": []},
            headers=auth_headers,
        )
        assert resp.status_code == 400

    def test_approved_plan_is_locked(self, client, auth_headers, wf_env, run, monkeypatch):
        """批准后计划锁定 —— 否则「界面上的任务书」与「将要执行的任务书」
        会悄悄不一致，而用户以为自己批准的就是眼前这份。要改必须先驳回。
        """
        _use_llm(monkeypatch, VALID_PLAN)
        _plan(client, auth_headers, run["id"])
        client.post(f"/workflow/runs/{run['id']}/approve", headers=auth_headers)

        resp = client.put(
            f"/workflow/runs/{run['id']}/plan",
            json={"goal": "偷偷改过的目标", "steps": [{"kind": "llm", "title": "x"}]},
            headers=auth_headers,
        )
        assert resp.status_code == 409

        # 驳回可以解锁，之后又能编辑了 —— 不能让用户卡死
        assert (
            client.post(
                f"/workflow/runs/{run['id']}/reject",
                json={"comment": "要改"},
                headers=auth_headers,
            ).status_code
            == 200
        )
        assert (
            client.put(
                f"/workflow/runs/{run['id']}/plan",
                json={"goal": "目标", "steps": [{"kind": "llm", "title": "x"}]},
                headers=auth_headers,
            ).status_code
            == 200
        )

    def test_steps_synced_to_db(self, client, auth_headers, wf_env, run):
        client.put(
            f"/workflow/runs/{run['id']}/plan",
            json={
                "goal": "目标",
                "steps": [
                    {"kind": "llm", "title": "A"},
                    {"kind": "llm", "title": "B"},
                ],
            },
            headers=auth_headers,
        )
        from app.repositories import workflow_repo as repo

        assert [s["title"] for s in repo.list_steps(run["id"])] == ["A", "B"]


# ---------------------------------------------------------------------------
#  审批
# ---------------------------------------------------------------------------


class TestApproveReject:
    def test_approve_requires_plan(self, client, auth_headers, wf_env, run):
        resp = client.post(f"/workflow/runs/{run['id']}/approve", headers=auth_headers)
        assert resp.status_code == 409

    def test_approve_transitions_to_approved(self, client, auth_headers, wf_env, run, monkeypatch):
        _use_llm(monkeypatch, VALID_PLAN)
        _plan(client, auth_headers, run["id"])

        resp = client.post(f"/workflow/runs/{run['id']}/approve", headers=auth_headers)
        assert resp.status_code == 200
        # 刻意不是 running：执行器尚未接入，宣称"正在执行"是谎报
        assert resp.json()["run"]["status"] == "approved"

    def test_approve_works_in_auto_mode_too(self, client, auth_headers, wf_env, ws_run, monkeypatch):
        """自动模式同样要过任务书审批 —— 这是成本与方向的双重确认点（设计 §7.2）。"""
        run = ws_run
        client.patch(
            f"/workflow/runs/{run['id']}", json={"mode": "auto"}, headers=auth_headers
        )
        _use_llm(monkeypatch, VALID_PLAN)
        _plan(client, auth_headers, run["id"])
        resp = client.post(f"/workflow/runs/{run['id']}/approve", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["run"]["status"] == "approved"

    def test_reject_returns_to_planning_and_keeps_plan(self, client, auth_headers, wf_env, run, monkeypatch):
        _use_llm(monkeypatch, VALID_PLAN)
        _plan(client, auth_headers, run["id"])

        resp = client.post(
            f"/workflow/runs/{run['id']}/reject",
            json={"comment": "第一步的关键词太宽泛"},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["run"]["status"] == "planning"
        assert data["run"]["reject_comment"] == "第一步的关键词太宽泛"
        # 驳回不清空任务书：用户前面的编辑不该白费
        assert data["run"]["plan"]["goal"]

    def test_approve_clears_reject_comment(self, client, auth_headers, wf_env, run, monkeypatch):
        _use_llm(monkeypatch, VALID_PLAN)
        _plan(client, auth_headers, run["id"])
        client.post(
            f"/workflow/runs/{run['id']}/reject",
            json={"comment": "改一下"},
            headers=auth_headers,
        )
        resp = client.post(f"/workflow/runs/{run['id']}/approve", headers=auth_headers)
        # 批准后旧的驳回意见必须清掉，否则会一直挂在界面上误导用户
        assert resp.json()["run"]["reject_comment"] is None

    def test_approved_run_cannot_be_rejected(self, client, auth_headers, wf_env, run, monkeypatch):
        """批准后要改必须先驳回 —— 但已批准状态下驳回会被状态机挡住吗？

        设计取舍：`approved` 允许驳回（用户可能想改主意），
        但 `running` / `done` 不允许。这里钉住的是后者的边界。
        """
        _use_llm(monkeypatch, VALID_PLAN)
        _plan(client, auth_headers, run["id"])
        client.post(f"/workflow/runs/{run['id']}/approve", headers=auth_headers)

        from app.repositories import workflow_repo as repo

        repo.update_run(run["id"], status="running")
        resp = client.post(
            f"/workflow/runs/{run['id']}/reject", json={"comment": "x"}, headers=auth_headers
        )
        assert resp.status_code == 409
