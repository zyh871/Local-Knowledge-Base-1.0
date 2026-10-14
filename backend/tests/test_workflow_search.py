"""P6：联网搜索接线的接口级测试。

三件事各自都是「接口测不出、但用户体验直接受损」的：

  * 步骤提示词必须把来源**编号化**并禁止输出 URL —— 否则模型会自己编链接
  * 生成结果必须做**非法链接剔除** —— 这是「真实可访问链接」的硬保证
  * 工具产出必须带**可达性标记**，且网络不确定时标 `None` 而不是 `false`

「澄清结论进提示词」那类断言在单元测试里更精确；这里聚焦端到端的落库结果。
"""

from __future__ import annotations

import json

import pytest

FIXTURE_SOURCES = [
    {
        "title": "官方定价页",
        "href": "https://a.example/pricing",
        "snippet": "标准档 1 元/千 token",
        "source": "tavily",
    },
    {
        "title": "社区讨论",
        "href": "https://b.example/thread/1",
        "snippet": "实测对比",
        "source": "tavily",
    },
]

SEARCH_TEXT = "【来源 1】官方定价页 — 标准档 1 元/千 token"

#: 模型"顺手"写了个假链接 —— 必须在落库前被剔除
GENERATED_WITH_FAKE_LINK = (
    "综合来看，标准档约 1 元/千 token [1]。\n"
    "另外可参考 https://totally-fake.example/blog/2026 的补充说明。\n"
    "社区讨论里还提到 https://b.example/thread/1 的实测数据。"
)


class _FakeLLM:
    def __init__(self, text: str, chunk_size: int = 21) -> None:
        self._text = text
        self._chunk_size = chunk_size
        self.prompts: list[str] = []

    def stream(self, prompt: str):
        from langchain_core.messages import AIMessageChunk

        self.prompts.append(prompt)
        total = len(self._text)
        for start in range(0, total, self._chunk_size):
            end = start + self._chunk_size
            meta = {"finish_reason": "stop"} if end >= total else {}
            yield AIMessageChunk(content=self._text[start:end], response_metadata=meta)

    def invoke(self, prompt: str):
        from langchain_core.messages import AIMessage

        self.prompts.append(prompt)
        return AIMessage(content=self._text, response_metadata={"finish_reason": "stop"})


class _Resp:
    def __init__(self, code: int) -> None:
        self.status_code = code


class _StubHttpxClient:
    codes: dict[str, int] = {}

    def __init__(self, **_kwargs) -> None:
        pass

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def head(self, url: str):
        return _Resp(self.codes.get(url, 404))


def _stub_registry(monkeypatch, sources=FIXTURE_SOURCES):
    """把工具注册表换成一个受控的桩，避免测试里真的去调 Tavily。"""
    from app.tools import base as tools_base

    class _Stub:
        def list_names(self):
            return ["web_search", "calculator"]

        def to_function_schemas(self):
            return [
                {
                    "name": "web_search",
                    "parameters": {"type": "object", "required": ["query"]},
                },
                {
                    "name": "calculator",
                    "parameters": {"type": "object", "required": ["expression"]},
                },
            ]

        def execute(self, name, **kwargs):
            if name == "web_search":
                return {"success": True, "result": SEARCH_TEXT, "sources": sources}
            return {"success": True, "result": "ok"}

    monkeypatch.setattr(tools_base, "get_registry", lambda: _Stub())


@pytest.fixture
def wf_env(tmp_path, monkeypatch):
    from app.core.config import clear_settings_cache
    from app.repositories import workflow_repo as repo

    monkeypatch.setenv("WORKFLOW_DB_PATH", str(tmp_path / "workflow.db"))
    monkeypatch.setenv("WORKFLOW_WORKSPACE_ROOT", str(tmp_path / "workspaces"))
    # 默认关掉真实网络探测；需要验证标记的用例再单独打开
    monkeypatch.setenv("WORKFLOW_VERIFY_LINKS", "false")
    clear_settings_cache()
    repo.reset_connection()
    yield tmp_path
    repo.reset_connection()
    clear_settings_cache()


@pytest.fixture
def workspace(client, auth_headers, wf_env):
    return client.post(
        "/workflow/workspaces", json={"name": "搜索测试"}, headers=auth_headers
    ).json()


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


def _run_with_search_plan(client, auth_headers, workspace_id) -> str:
    """建一个「先搜后写」的两步任务并批准。"""
    run = client.post(
        "/workflow/runs",
        json={"workspace_id": workspace_id, "raw_input": "对比大模型定价", "mode": "auto"},
        headers=auth_headers,
    ).json()
    resp = client.put(
        f"/workflow/runs/{run['id']}/plan",
        json={
            "goal": "产出定价对比",
            "audience": "决策者",
            "constraints": ["中文"],
            "steps": [
                {
                    "kind": "tool",
                    "title": "搜索定价",
                    "action": {"tool": "web_search", "args": {"query": "大模型 定价"}},
                },
                {
                    "kind": "synthesis",
                    "title": "整合结论",
                    "action": {"prompt_ref": "整合结论"},
                },
            ],
        },
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    client.post(f"/workflow/runs/{run['id']}/approve", headers=auth_headers)
    return run["id"]


def _exec(client, auth_headers, run_id, idx):
    return client.post(
        f"/workflow/runs/{run_id}/steps/{idx}/execute", headers=auth_headers
    )


# ---------------------------------------------------------------------------
#  工具产出：来源落库 + 可达性标记
# ---------------------------------------------------------------------------


class TestToolSources:
    def test_sources_persisted_with_refs_and_reachable(
        self, client, auth_headers, wf_env, workspace, monkeypatch
    ):
        _stub_registry(monkeypatch)
        run_id = _run_with_search_plan(client, auth_headers, workspace["id"])
        final = _final(_exec(client, auth_headers, run_id, 1))
        assert final["ok"] is True, final

        sources = final["output"]["sources"]
        assert [s["href"] for s in sources] == [
            "https://a.example/pricing",
            "https://b.example/thread/1",
        ]
        # 探测关闭时应当是「未校验」，而不是 false
        assert all(s["reachable"] is None for s in sources)
        # 标题与 provider 必须原样保留（前端要展示域名 + 标题）
        assert sources[0]["title"] == "官方定价页"
        assert sources[0]["source"] == "tavily"

        detail = client.get(f"/workflow/runs/{run_id}", headers=auth_headers).json()
        assert detail["steps"][0]["output"]["sources"] == sources

    def test_reachability_marked_when_enabled(
        self, client, auth_headers, wf_env, workspace, monkeypatch
    ):
        from app.core.config import clear_settings_cache
        from app.repositories import workflow_repo as repo

        # ★ 必须清掉可达性缓存：它是模块级全局的，
        # 任何一次真实探测（比如别的文件里没关掉 VERIFY_LINKS 的用例）
        # 都会把结果留在这里，让本用例读到与本例无关的结论。
        # 单独跑本文件时不会复现，只有全量运行才暴露 —— 典型的隐藏全局状态。
        from app.workflow.reachability import clear_cache

        clear_cache()

        monkeypatch.setenv("WORKFLOW_VERIFY_LINKS", "true")
        clear_settings_cache()
        repo.reset_connection()

        import httpx

        _StubHttpxClient.codes = {
            "https://a.example/pricing": 200,
            "https://b.example/thread/1": 404,
        }
        monkeypatch.setattr(httpx, "Client", _StubHttpxClient)
        _stub_registry(monkeypatch)

        run_id = _run_with_search_plan(client, auth_headers, workspace["id"])
        final = _final(_exec(client, auth_headers, run_id, 1))
        assert final["ok"] is True, final

        sources = final["output"]["sources"]
        assert sources[0]["reachable"] is True
        assert sources[1]["reachable"] is False
        clear_cache()


# ---------------------------------------------------------------------------
#  生成步：来源编号进提示词 + 非法链接剔除
# ---------------------------------------------------------------------------


class TestCitationEnforcement:
    def test_step_prompt_carries_numbered_sources(
        self, client, auth_headers, wf_env, workspace, monkeypatch
    ):
        _stub_registry(monkeypatch)
        run_id = _run_with_search_plan(client, auth_headers, workspace["id"])
        _exec(client, auth_headers, run_id, 1)

        fake = _FakeLLM(GENERATED_WITH_FAKE_LINK)
        monkeypatch.setattr("app.rag.providers.get_llm", lambda model=None: fake)
        _exec(client, auth_headers, run_id, 2)

        prompt = fake.prompts[-1]
        assert "【来源 1】" in prompt
        assert "【来源 2】" in prompt
        assert "https://a.example/pricing" in prompt
        assert "官方定价页" in prompt
        # 明确的禁令 + 允许的引用形式
        assert "绝对不要输出任何 URL" in prompt
        assert "[1]" in prompt

    def test_fake_link_stripped_and_reported(
        self, client, auth_headers, wf_env, workspace, monkeypatch
    ):
        """★ 需求 4 的硬保证：模型自编的链接不能落库。"""
        _stub_registry(monkeypatch)
        run_id = _run_with_search_plan(client, auth_headers, workspace["id"])
        _exec(client, auth_headers, run_id, 1)

        fake = _FakeLLM(GENERATED_WITH_FAKE_LINK)
        monkeypatch.setattr("app.rag.providers.get_llm", lambda model=None: fake)
        final = _final(_exec(client, auth_headers, run_id, 2))
        assert final["ok"] is True, final

        summary = final["output"]["summary"]
        assert "totally-fake.example" not in summary
        # 真实来源的链接要保留
        assert "https://b.example/thread/1" in summary
        # 编号引用不受影响
        assert "[1]" in summary
        # 剔除要可见，而不是悄悄少几行
        assert final["output"]["stripped_links"] == [
            "https://totally-fake.example/blog/2026"
        ]

    def test_no_sources_prompt_forbids_fabrication(
        self, client, auth_headers, wf_env, workspace, monkeypatch
    ):
        """没有任何检索结果时，提示词要明确说「不要编造链接」。"""
        _stub_registry(monkeypatch)
        run = client.post(
            "/workflow/runs",
            json={"workspace_id": workspace["id"], "raw_input": "写点东西", "mode": "auto"},
            headers=auth_headers,
        ).json()
        client.put(
            f"/workflow/runs/{run['id']}/plan",
            json={
                "goal": "g",
                "steps": [{"kind": "llm", "title": "写", "action": {"prompt_ref": "写"}}],
            },
            headers=auth_headers,
        )
        client.post(f"/workflow/runs/{run['id']}/approve", headers=auth_headers)

        fake = _FakeLLM("纯生成内容。")
        monkeypatch.setattr("app.rag.providers.get_llm", lambda model=None: fake)
        _exec(client, auth_headers, run["id"], 1)

        prompt = fake.prompts[-1]
        assert "没有可引用的检索结果" in prompt
        assert "请勿编造链接" in prompt

    def test_clean_output_has_no_stripped_field(
        self, client, auth_headers, wf_env, workspace, monkeypatch
    ):
        _stub_registry(monkeypatch)
        run_id = _run_with_search_plan(client, auth_headers, workspace["id"])
        _exec(client, auth_headers, run_id, 1)

        fake = _FakeLLM("结论：约 1 元/千 token [1]。")
        monkeypatch.setattr("app.rag.providers.get_llm", lambda model=None: fake)
        final = _final(_exec(client, auth_headers, run_id, 2))
        assert "stripped_links" not in final["output"]
