"""P7：交付物 / 分享 / TTL 清理的接口级测试。

三件事里各有一条「出错就很难看」的底线：

  * 交付物 —— 必须真的落盘且可下载（用户会把它带走）
  * 分享   —— **绝不能把用户上传的文件内容带出去**（设计 §9.5 明确点出的风险）
  * 清理   —— **绝不能删没到期的文件**（不可逆）
"""

from __future__ import annotations

import io
import json
import time

import pytest

SECRET = "内部敏感数据：定价 0.42 元"

PLAN_STEPS = [
    {"kind": "llm", "title": "第一步", "action": {"prompt_ref": "一"}},
    {"kind": "synthesis", "title": "汇总结论", "action": {"prompt_ref": "汇总"}},
]


class _FakeLLM:
    def __init__(self, text: str = "这一步的产出。", chunk_size: int = 11) -> None:
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
    # 本文件不验证可达性；必须显式关掉，否则会真的发 HEAD 请求，
    # 并把结果写进 reachability 的**模块级缓存**——
    # 那会污染其他文件里对可达性的断言（只在全量运行时复现）。
    monkeypatch.setenv("WORKFLOW_VERIFY_LINKS", "false")
    clear_settings_cache()
    repo.reset_connection()
    yield tmp_path
    repo.reset_connection()
    clear_settings_cache()


@pytest.fixture
def workspace(client, auth_headers, wf_env):
    return client.post(
        "/workflow/workspaces", json={"name": "交付测试"}, headers=auth_headers
    ).json()


def _use_llm(monkeypatch, text: str = "这一步的产出。") -> _FakeLLM:
    fake = _FakeLLM(text)
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


def _executed_run(client, auth_headers, workspace_id, monkeypatch, *, steps=None) -> str:
    """建一个跑完两步的任务。"""
    _use_llm(monkeypatch, "结论：A 家最便宜。")
    run = client.post(
        "/workflow/runs",
        json={"workspace_id": workspace_id, "raw_input": "对比定价", "mode": "auto"},
        headers=auth_headers,
    ).json()
    client.put(
        f"/workflow/runs/{run['id']}/plan",
        json={"goal": "产出定价对比", "audience": "决策者", "steps": steps or PLAN_STEPS},
        headers=auth_headers,
    )
    client.post(f"/workflow/runs/{run['id']}/approve", headers=auth_headers)
    for idx in range(1, len(steps or PLAN_STEPS) + 1):
        _use_llm(monkeypatch, "结论：A 家最便宜。")
        _final(
            client.post(
                f"/workflow/runs/{run['id']}/steps/{idx}/execute", headers=auth_headers
            )
        )
    return run["id"]


# ---------------------------------------------------------------------------
#  交付物
# ---------------------------------------------------------------------------


class TestDeliver:
    def test_generates_artifact_and_downloads(
        self, client, auth_headers, wf_env, workspace, monkeypatch
    ):
        run_id = _executed_run(client, auth_headers, workspace["id"], monkeypatch)
        resp = client.post(f"/workflow/runs/{run_id}/deliver", headers=auth_headers)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["chars"] > 50
        artifact = body["artifact"]
        assert artifact["kind"] == "artifact"
        assert artifact["filename"].endswith(".md")

        # 交付物必须真的能下载下来
        download = client.get(
            f"/workflow/files/{artifact['id']}/download", headers=auth_headers
        )
        assert download.status_code == 200
        text = download.content.decode("utf-8")
        assert "# 产出定价对比" in text
        assert "## 执行明细" in text
        assert "结论：A 家最便宜" in text

    def test_does_not_clobber_run_summary(
        self, client, auth_headers, wf_env, workspace, monkeypatch
    ):
        """`summary` 的语义是「结论」，交付物是另一份东西。

        把整份文档写进 summary，会让界面上的「结论」卡变成整篇文档，
        与「执行明细」重复 —— 用户看到两份一样的东西。
        """
        run_id = _executed_run(client, auth_headers, workspace["id"], monkeypatch)
        before = client.get(f"/workflow/runs/{run_id}", headers=auth_headers).json()
        summary_before = before["summary"]
        assert summary_before  # 执行完成后 summary 就是结论

        client.post(f"/workflow/runs/{run_id}/deliver", headers=auth_headers)
        after = client.get(f"/workflow/runs/{run_id}", headers=auth_headers).json()

        assert after["summary"] == summary_before
        # 交付物不该混进 summary
        assert "## 执行明细" not in (after["summary"] or "")

    def test_artifacts_listed(
        self, client, auth_headers, wf_env, workspace, monkeypatch
    ):
        run_id = _executed_run(client, auth_headers, workspace["id"], monkeypatch)
        assert (
            client.get(
                f"/workflow/runs/{run_id}/artifacts", headers=auth_headers
            ).json()["count"]
            == 0
        )
        client.post(f"/workflow/runs/{run_id}/deliver", headers=auth_headers)
        listed = client.get(
            f"/workflow/runs/{run_id}/artifacts", headers=auth_headers
        ).json()
        assert listed["count"] == 1
        assert listed["artifacts"][0]["kind"] == "artifact"

    def test_refused_before_any_step_finishes(
        self, client, auth_headers, wf_env, workspace, monkeypatch
    ):
        _use_llm(monkeypatch)
        run = client.post(
            "/workflow/runs",
            json={"workspace_id": workspace["id"], "raw_input": "目标"},
            headers=auth_headers,
        ).json()
        client.put(
            f"/workflow/runs/{run['id']}/plan",
            json={"goal": "g", "steps": PLAN_STEPS},
            headers=auth_headers,
        )
        client.post(f"/workflow/runs/{run['id']}/approve", headers=auth_headers)
        resp = client.post(f"/workflow/runs/{run['id']}/deliver", headers=auth_headers)
        assert resp.status_code == 409

    def test_deliver_twice_creates_two_artifacts(
        self, client, auth_headers, wf_env, workspace, monkeypatch
    ):
        """重复生成不应报错（用户可能改了内容想再导一次），但也不能覆盖旧文件。"""
        run_id = _executed_run(client, auth_headers, workspace["id"], monkeypatch)
        client.post(f"/workflow/runs/{run_id}/deliver", headers=auth_headers)
        client.post(f"/workflow/runs/{run_id}/deliver", headers=auth_headers)
        assert (
            client.get(
                f"/workflow/runs/{run_id}/artifacts", headers=auth_headers
            ).json()["count"]
            == 2
        )


# ---------------------------------------------------------------------------
#  分享
# ---------------------------------------------------------------------------


class _UsageLLM(_FakeLLM):
    """带 token 用量的假模型。

    真上游会把 usage 放在**最后一个分片**上（而且那个分片通常不带正文），
    这里刻意复刻这个形状 —— 节点若把取用量的代码放在 `if not text: continue`
    之后，就会永远拿不到。
    """

    def stream(self, _prompt: str):
        from langchain_core.messages import AIMessageChunk

        yield AIMessageChunk(content="这一步的产出。", response_metadata={"finish_reason": "stop"})
        # 纯用量分片：没有正文
        yield AIMessageChunk(
            content="",
            response_metadata={"finish_reason": "stop"},
            usage_metadata={"input_tokens": 30, "output_tokens": 12, "total_tokens": 42},
        )

    def invoke(self, _prompt: str):
        from langchain_core.messages import AIMessage

        return AIMessage(
            content="这一步的产出。",
            response_metadata={"finish_reason": "stop"},
            usage_metadata={"input_tokens": 30, "output_tokens": 12, "total_tokens": 42},
        )


def _use_usage_llm(monkeypatch) -> _UsageLLM:
    fake = _UsageLLM("这一步的产出。")
    monkeypatch.setattr("app.rag.providers.get_llm", lambda model=None: fake)
    return fake


class TestRunUsageExposed:
    """成本可见性：用量要真的从模型流到接口响应里。

    只在聚合函数上做单元测试是不够的 —— 中间任何一环漏传，
    界面上就永远是「没有数据」，而那种 bug 完全不报错。
    """

    def test_none_when_no_usage_available(
        self, client, auth_headers, wf_env, workspace, monkeypatch
    ):
        """★ 拿不到用量时给 None，而不是 0 —— 0 会被读成「不花钱」。"""
        run_id = _executed_run(client, auth_headers, workspace["id"], monkeypatch)
        detail = client.get(f"/workflow/runs/{run_id}", headers=auth_headers).json()
        assert detail["usage"] is None

    def test_step_usage_reaches_the_response(
        self, client, auth_headers, wf_env, workspace, monkeypatch
    ):
        """★ 端到端接线：模型给的 usage 必须出现在任务详情的用量里。"""
        run = client.post(
            "/workflow/runs",
            json={"workspace_id": workspace["id"], "raw_input": "x", "mode": "auto"},
            headers=auth_headers,
        ).json()
        client.put(
            f"/workflow/runs/{run['id']}/plan",
            json={"goal": "g", "steps": [{"kind": "llm", "title": "一步", "action": {"prompt_ref": "p"}}]},
            headers=auth_headers,
        )
        client.post(f"/workflow/runs/{run['id']}/approve", headers=auth_headers)
        _use_usage_llm(monkeypatch)
        _final(
            client.post(f"/workflow/runs/{run['id']}/steps/1/execute", headers=auth_headers)
        )

        detail = client.get(f"/workflow/runs/{run['id']}", headers=auth_headers).json()
        usage = detail["usage"]
        assert usage is not None, "用量没被记录"
        assert usage["calls"] == 1
        assert usage["input_tokens"] == 30
        assert usage["output_tokens"] == 12
        assert usage["total_tokens"] == 42
        assert usage["steps"][0]["idx"] == 1

    def test_usage_accumulates_across_phases_and_steps(
        self, client, auth_headers, wf_env, workspace, monkeypatch
    ):
        """澄清 + 规划 + 步骤都要算进来，否则数字会系统性偏小。"""
        from app.repositories import workflow_repo as repo

        run = client.post(
            "/workflow/runs",
            json={"workspace_id": workspace["id"], "raw_input": "x", "mode": "auto"},
            headers=auth_headers,
        ).json()
        client.put(
            f"/workflow/runs/{run['id']}/plan",
            json={"goal": "g", "steps": [{"kind": "llm", "title": "一步", "action": {"prompt_ref": "p"}}]},
            headers=auth_headers,
        )
        client.post(f"/workflow/runs/{run['id']}/approve", headers=auth_headers)
        # 两轮澄清 + 一次规划（接口层的写入逻辑另有覆盖，这里直接落库）
        repo.add_run_usage(run["id"], "clarify", {"input_tokens": 100, "output_tokens": 20})
        repo.add_run_usage(run["id"], "clarify", {"input_tokens": 100, "output_tokens": 20})
        repo.add_run_usage(run["id"], "plan", {"input_tokens": 50, "output_tokens": 30})

        _use_usage_llm(monkeypatch)
        _final(
            client.post(f"/workflow/runs/{run['id']}/steps/1/execute", headers=auth_headers)
        )

        usage = client.get(f"/workflow/runs/{run['id']}", headers=auth_headers).json()["usage"]
        assert usage["calls"] == 4  # 2 轮澄清 + 1 次规划 + 1 步
        assert usage["input_tokens"] == 200 + 50 + 30
        assert usage["output_tokens"] == 40 + 30 + 12
        assert usage["phases"]["clarify"]["input_tokens"] == 200


class TestShareTruncationAndConclusion:
    def test_share_marks_conclusion_step(
        self, client, auth_headers, wf_env, workspace, monkeypatch
    ):
        """★ 分享页要知道「哪一步的正文已经作为结论出现过了」。"""
        run_id = _executed_run(client, auth_headers, workspace["id"], monkeypatch)
        client.post(f"/workflow/runs/{run_id}/deliver", headers=auth_headers)
        token = client.post(
            f"/workflow/runs/{run_id}/share", json={}, headers=auth_headers
        ).json()["token"]
        data = client.get(f"/share/{token}").json()["data"]

        # 两步计划里第 2 步是 synthesis → 结论应定位到它
        assert data["conclusion_idx"] == 2
        target = [s for s in data["steps"] if s["idx"] == 2][0]
        assert target["summary"]
        # 结论用的必须是**这一步的正文**，而不是被截到 4000 字符的 runs.summary
        assert data["summary"] == target["summary"]
        assert "truncated" in target

    def test_share_marks_truncated_step(
        self, client, auth_headers, wf_env, workspace, monkeypatch
    ):
        """★ 截断要下发给分享页：否则「没写完」的部分被当成完整内容读。"""
        from app.repositories import workflow_repo as repo

        run_id = _executed_run(client, auth_headers, workspace["id"], monkeypatch)
        repo.update_step(run_id, 1, output={"summary": "半截内容", "truncated": True})
        client.post(f"/workflow/runs/{run_id}/deliver", headers=auth_headers)
        token = client.post(
            f"/workflow/runs/{run_id}/share", json={}, headers=auth_headers
        ).json()["token"]
        data = client.get(f"/share/{token}").json()["data"]

        by_idx = {s["idx"]: s for s in data["steps"]}
        assert by_idx[1]["truncated"] is True
        assert by_idx[2]["truncated"] is False

    def test_deliverable_marks_truncation(
        self, client, auth_headers, wf_env, workspace, monkeypatch
    ):
        from app.repositories import workflow_repo as repo

        run_id = _executed_run(client, auth_headers, workspace["id"], monkeypatch)
        repo.update_step(run_id, 1, output={"summary": "半截内容", "truncated": True})
        client.post(f"/workflow/runs/{run_id}/deliver", headers=auth_headers)

        files = client.get(f"/workflow/runs/{run_id}", headers=auth_headers).json()["files"]
        artifact = [f for f in files if f.get("kind") == "artifact"][0]
        body = client.get(
            f"/workflow/files/{artifact['id']}/download", headers=auth_headers
        ).content.decode("utf-8")
        assert "此段可能不完整" in body
        assert "1 步输出被截断" in body


class TestShare:

    def test_share_payload_shape(
        self, client, auth_headers, wf_env, workspace, monkeypatch
    ):
        run_id = _executed_run(client, auth_headers, workspace["id"], monkeypatch)
        client.post(f"/workflow/runs/{run_id}/deliver", headers=auth_headers)

        resp = client.post(
            f"/workflow/runs/{run_id}/share", json={}, headers=auth_headers
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["token"]
        assert body["url_path"].startswith("/shared/")

        # 公开读取（不带 Authorization）
        public = client.get(f"/share/{body['token']}")
        assert public.status_code == 200
        data = public.json()["data"]
        assert data["kind"] == "workflow"
        assert data["goal"] == "产出定价对比"
        assert len(data["steps"]) == 2
        assert data["steps"][1]["summary"]
        assert data["artifacts"][0]["filename"].endswith(".md")

    def test_share_never_leaks_uploaded_file_content(
        self, client, auth_headers, wf_env, workspace, monkeypatch
    ):
        """★ 设计 §9.5 明确点出的风险：分享页不能把用户丢进工作区的本地文件公开出去。"""
        run_id = _executed_run(client, auth_headers, workspace["id"], monkeypatch)
        client.post(
            f"/workflow/runs/{run_id}/files",
            files={"files": ("secret.txt", io.BytesIO(SECRET.encode()), "text/plain")},
            headers=auth_headers,
        )
        client.post(f"/workflow/runs/{run_id}/deliver", headers=auth_headers)

        token = client.post(
            f"/workflow/runs/{run_id}/share", json={}, headers=auth_headers
        ).json()["token"]
        raw = client.get(f"/share/{token}").text

        assert SECRET not in raw
        assert "secret.txt" not in raw
        # 落盘路径同样不能泄漏
        assert "workspaces" not in raw
        assert "stored_path" not in raw

    def test_share_payload_includes_sources(
        self, client, auth_headers, wf_env, workspace, monkeypatch
    ):
        """来源必须进分享页 —— 用受控桩做**确定性**断言。

        真机脚本里这条只能算「如果搜了就校验」，因为模型可能判定不需要搜索；
        分享页是否完整传递 sources 属于本期的职责，必须能确定地测。
        """
        from app.tools import base as tools_base

        sources = [
            {"title": "官方定价页", "href": "https://a.example/pricing", "source": "tavily"},
            {"title": "社区讨论", "href": "https://b.example/t", "source": "tavily"},
        ]

        class _Stub:
            def list_names(self):
                return ["web_search"]

            def to_function_schemas(self):
                return [{"name": "web_search", "parameters": {"type": "object", "required": ["query"]}}]

            def execute(self, name, **_kwargs):
                return {"success": True, "result": "搜索结果", "sources": sources}

        monkeypatch.setattr(tools_base, "get_registry", lambda: _Stub())

        run = client.post(
            "/workflow/runs",
            json={"workspace_id": workspace["id"], "raw_input": "对比定价", "mode": "auto"},
            headers=auth_headers,
        ).json()
        client.put(
            f"/workflow/runs/{run['id']}/plan",
            json={
                "goal": "产出定价对比",
                "steps": [
                    {
                        "kind": "tool",
                        "title": "搜索定价",
                        "action": {"tool": "web_search", "args": {"query": "定价"}},
                    },
                    {"kind": "synthesis", "title": "汇总", "action": {"prompt_ref": "汇总"}},
                ],
            },
            headers=auth_headers,
        )
        client.post(f"/workflow/runs/{run['id']}/approve", headers=auth_headers)
        _use_llm(monkeypatch, "结论 [1]。")
        _final(
            client.post(f"/workflow/runs/{run['id']}/steps/1/execute", headers=auth_headers)
        )
        client.post(f"/workflow/runs/{run['id']}/steps/2/execute", headers=auth_headers)

        token = client.post(
            f"/workflow/runs/{run['id']}/share", json={}, headers=auth_headers
        ).json()["token"]
        data = client.get(f"/share/{token}").json()["data"]

        assert [s["href"] for s in data["sources"]] == [
            "https://a.example/pricing",
            "https://b.example/t",
        ]
        assert [s["ref"] for s in data["sources"]] == [1, 2]
        # 分享页只暴露链接与标题，不带 provider 之类的内部字段
        assert "source" not in data["sources"][0]

    def test_share_before_any_execution_is_allowed(
        self, client, auth_headers, wf_env, workspace, monkeypatch
    ):
        """只分享任务书也是有意义的（让人先看计划）。"""
        run_id = _executed_run(client, auth_headers, workspace["id"], monkeypatch)
        resp = client.post(
            f"/workflow/runs/{run_id}/share", json={}, headers=auth_headers
        )
        assert resp.status_code == 200

    def test_share_requires_ownership(
        self, client, auth_headers, wf_env, workspace, monkeypatch
    ):
        run_id = _executed_run(client, auth_headers, workspace["id"], monkeypatch)
        from app.repositories import workflow_repo as repo

        repo.update_run(run_id, title="x")
        # 直接把 owner 改成别人，模拟越权
        with repo._lock, repo._connect() as conn:  # noqa: SLF001 - 测试内构造越权场景
            conn.execute(
                "UPDATE runs SET username = ? WHERE id = ?", ("someone-else", run_id)
            )
        resp = client.post(
            f"/workflow/runs/{run_id}/share", json={}, headers=auth_headers
        )
        assert resp.status_code == 403

    def test_share_includes_clarify_summary(
        self, client, auth_headers, wf_env, workspace, monkeypatch
    ):
        """分享页也要体现需求是怎么收敛的（澄清补充）。"""
        from app.repositories import workflow_repo as repo

        run_id = _executed_run(client, auth_headers, workspace["id"], monkeypatch)
        run = repo.get_run(run_id, with_steps=False)
        state = run["clarify"] or {}
        state["rounds"] = [
            {
                "round": 1,
                "questions": [
                    {
                        "id": "q1",
                        "question": "读者是谁？",
                        "options": [{"label": "技术决策者", "value": "tech"}],
                    }
                ],
                "answers": {"q1": {"value": "tech", "text": ""}},
            }
        ]
        repo.update_run(run_id, clarify=state)

        token = client.post(
            f"/workflow/runs/{run_id}/share", json={}, headers=auth_headers
        ).json()["token"]
        data = client.get(f"/share/{token}").json()["data"]
        assert data["clarify"] == [{"question": "读者是谁？", "answer": "技术决策者"}]


# ---------------------------------------------------------------------------
#  TTL 清理
# ---------------------------------------------------------------------------


class TestCleanup:
    def _upload(self, client, auth_headers, run_id, name: str, body: str = "x") -> dict:
        return client.post(
            f"/workflow/runs/{run_id}/files",
            files={"files": (name, io.BytesIO(body.encode()), "text/plain")},
            headers=auth_headers,
        ).json()["files"][0]

    def test_expired_file_removed_and_fresh_kept(
        self, client, auth_headers, wf_env, workspace, monkeypatch
    ):
        """★ 最重要的一条：没到期的文件一个都不能删。"""
        from app.services.workflow_cleanup import cleanup_once

        run_id = _executed_run(client, auth_headers, workspace["id"], monkeypatch)
        old = self._upload(client, auth_headers, run_id, "old.txt")
        fresh = self._upload(client, auth_headers, run_id, "fresh.txt")

        # 把 old 的 TTL 改到过去
        from app.repositories import workflow_repo as repo

        with repo._lock, repo._connect() as conn:  # noqa: SLF001
            conn.execute(
                "UPDATE run_files SET ttl_expires_at = ? WHERE id = ?",
                (time.time() - 10, old["id"]),
            )

        stats = cleanup_once()
        assert stats["expired"] == 1
        assert stats["deleted_files"] == 1

        remaining = repo.list_run_files(run_id)
        assert [f["id"] for f in remaining] == [fresh["id"]]
        # 文件真的要能下载（没被误删）
        assert (
            client.get(
                f"/workflow/files/{fresh['id']}/download", headers=auth_headers
            ).status_code
            == 200
        )

    def test_zero_ttl_never_deleted(
        self, client, auth_headers, wf_env, workspace, monkeypatch
    ):
        """`ttl_expires_at = 0` 是「不自动清理」的约定，不能当成「1970 年就过期」。"""
        from app.repositories import workflow_repo as repo
        from app.services.workflow_cleanup import cleanup_once

        run_id = _executed_run(client, auth_headers, workspace["id"], monkeypatch)
        keep = self._upload(client, auth_headers, run_id, "keep.txt")
        with repo._lock, repo._connect() as conn:  # noqa: SLF001
            conn.execute(
                "UPDATE run_files SET ttl_expires_at = 0 WHERE id = ?", (keep["id"],)
            )

        cleanup_once()
        assert repo.get_run_file(keep["id"]) is not None

    def test_orphan_file_removed(
        self, client, auth_headers, wf_env, workspace, monkeypatch
    ):
        """磁盘有、库里没有的僵尸文件必须清掉 —— 否则永远没人处理它。"""
        from pathlib import Path

        from app.repositories import workflow_repo as repo
        from app.services.workflow_cleanup import cleanup_once

        run_id = _executed_run(client, auth_headers, workspace["id"], monkeypatch)
        directory = Path(repo.workspace_dir(workspace["id"])) / "files"
        directory.mkdir(parents=True, exist_ok=True)
        orphan = directory / "orphan.txt"
        orphan.write_text("没有人登记我", encoding="utf-8")

        stats = cleanup_once()
        assert stats["orphans"] >= 1
        assert not orphan.exists()

    def test_artifact_file_also_covered(
        self, client, auth_headers, wf_env, workspace, monkeypatch
    ):
        """产物目录也要扫 —— 只扫 files/ 会漏掉交付物。"""
        from app.repositories import workflow_repo as repo
        from app.services.workflow_cleanup import cleanup_once

        run_id = _executed_run(client, auth_headers, workspace["id"], monkeypatch)
        client.post(f"/workflow/runs/{run_id}/deliver", headers=auth_headers)
        artifact = repo.list_run_files(run_id)[0]
        assert artifact["kind"] == "artifact"

        with repo._lock, repo._connect() as conn:  # noqa: SLF001
            conn.execute(
                "UPDATE run_files SET ttl_expires_at = ? WHERE id = ?",
                (time.time() - 10, artifact["id"]),
            )
        stats = cleanup_once()
        assert stats["expired"] == 1

    def test_cleanup_is_idempotent(self, client, auth_headers, wf_env, workspace, monkeypatch):
        from app.services.workflow_cleanup import cleanup_once

        run_id = _executed_run(client, auth_headers, workspace["id"], monkeypatch)
        first = cleanup_once()
        second = cleanup_once()
        assert second["expired"] == 0
        assert second["orphans"] == 0
        assert first["errors"] == 0 and second["errors"] == 0

    def test_dry_run_does_not_touch_anything(
        self, client, auth_headers, wf_env, workspace, monkeypatch
    ):
        from app.repositories import workflow_repo as repo
        from app.services.workflow_cleanup import cleanup_once

        run_id = _executed_run(client, auth_headers, workspace["id"], monkeypatch)
        record = self._upload(client, auth_headers, run_id, "dry.txt")
        with repo._lock, repo._connect() as conn:  # noqa: SLF001
            conn.execute(
                "UPDATE run_files SET ttl_expires_at = ? WHERE id = ?",
                (time.time() - 10, record["id"]),
            )

        stats = cleanup_once(dry_run=True)
        assert stats["planned_delete_files"] == 1
        assert repo.get_run_file(record["id"]) is not None
