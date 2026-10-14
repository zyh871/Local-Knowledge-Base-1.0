"""端到端：让工作流真的产出一个可下载的文件。

这是本次修复的**验收测试**。此前这条链路整体不存在：

  * 工具清单里没有写文件的工具（配置里写着 `file_write`，实现和注册都没有）；
  * `code_executor` 禁止 `open(`，所以"沙箱里写文件"这条路也堵死；
  * 生成步的代码只是字符串，不落盘。

所以断言必须落在**磁盘上真有这个文件**、以及**任务产物里能下载到它**，
而不是"步骤返回了 success"—— 后者在只返回一句"已写入"时也会成立。
"""

from __future__ import annotations

import json

import pytest

PLAN_STEPS = [
    {"kind": "llm", "title": "写脚本", "action": {"prompt_ref": "写一个脚本"}},
    {
        "kind": "tool",
        "title": "保存为 fib.py",
        "action": {"tool": "file_write", "args": {"path": "fib.py", "content_from_step": 1}},
    },
]

CODE_TEXT = "```python\ndef fib(n):\n    return n if n < 2 else fib(n - 1) + fib(n - 2)\n```"


class _FakeLLM:
    def __init__(self, text: str = CODE_TEXT) -> None:
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
        "/workflow/workspaces", json={"name": "产出文件测试"}, headers=auth_headers
    ).json()


def _use_llm(monkeypatch, text: str = CODE_TEXT) -> None:
    monkeypatch.setattr("app.rag.providers.get_llm", lambda model=None: _FakeLLM(text))


def _final(response) -> dict:
    for block in response.text.split("\n\n"):
        for line in block.strip().splitlines():
            if line.startswith("data:"):
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


def _run_with_plan(client, auth_headers, workspace, steps=None, monkeypatch=None):
    run = client.post(
        "/workflow/runs",
        json={"workspace_id": workspace["id"], "raw_input": "写个脚本存成文件", "mode": "auto"},
        headers=auth_headers,
    ).json()
    plan = client.put(
        f"/workflow/runs/{run['id']}/plan",
        json={"goal": "产出一个脚本文件", "steps": steps or PLAN_STEPS},
        headers=auth_headers,
    ).json()
    client.post(f"/workflow/runs/{run['id']}/approve", headers=auth_headers)
    return run, plan


def _execute(client, auth_headers, run_id, idx):
    return client.post(
        f"/workflow/runs/{run_id}/steps/{idx}/execute", headers=auth_headers
    )


class TestFileWriteEndToEnd:
    def test_workflow_actually_produces_a_downloadable_file(
        self, client, auth_headers, wf_env, workspace, monkeypatch
    ):
        _use_llm(monkeypatch)
        run, _ = _run_with_plan(client, auth_headers, workspace)
        run_id = run["id"]

        # 第 1 步：模型写代码（只是文本）
        step1 = _final(_execute(client, auth_headers, run_id, 1))
        assert step1.get("ok") is True, step1

        # 第 2 步：file_write。它在强制审批清单里 —— auto 模式也要人工点一次
        gated = _final(_execute(client, auth_headers, run_id, 2))
        assert gated.get("code") == "approval_required", gated
        client.post(f"/workflow/runs/{run_id}/steps/2/approve", headers=auth_headers)

        step2 = _final(_execute(client, auth_headers, run_id, 2))
        assert step2.get("ok") is True, step2

        # ★ 磁盘上真的有这个文件，内容是第 1 步的产出
        detail = client.get(f"/workflow/runs/{run_id}", headers=auth_headers).json()
        step2_detail = next(s for s in detail["steps"] if s["idx"] == 2)
        written = step2_detail["output"]["written"]
        assert written["filename"] == "fib.py"
        assert written["size_bytes"] > 0

        # ★ 出现在产物列表里，并且能下载到
        artifacts = client.get(
            f"/workflow/runs/{run_id}/artifacts", headers=auth_headers
        ).json()
        names = [a["filename"] for a in artifacts["artifacts"]]
        assert "fib.py" in names, artifacts

        target = next(a for a in artifacts["artifacts"] if a["filename"] == "fib.py")
        download = client.get(
            f"/workflow/files/{target['id']}/download", headers=auth_headers
        )
        assert download.status_code == 200
        body = download.content.decode("utf-8")
        assert "def fib(n)" in body

        # ★ 落盘的文件必须**真的能用**：不能带 markdown 围栏、要能编译。
        #   只断言"文件存在且非空"是不够的 —— 带 ```python 的文件同样满足那两条，
        #   而它一运行就语法错误。这正是用户说的"生成的代码无法正常使用"。
        assert not body.lstrip().startswith("```"), f"落盘文件带着代码围栏:\n{body[:60]}"
        assert "```" not in body
        compile(body, "fib.py", "exec")

    def test_arg_alias_is_reported_in_step_output(
        self, client, auth_headers, wf_env, workspace, monkeypatch
    ):
        """模型写错键名时，修正动作必须能被看到，而不是悄悄生效。"""
        _use_llm(monkeypatch)
        steps = [
            {"kind": "llm", "title": "写脚本", "action": {"prompt_ref": "写"}},
            {
                "kind": "tool",
                "title": "存文件",
                "action": {
                    "tool": "file_write",
                    "args": {"filename": "out.py", "content_ref": 1},
                },
            },
        ]
        run, plan = _run_with_plan(client, auth_headers, workspace, steps=steps)
        run_id = run["id"]
        # 计划期就完成了 filename → path / content_ref → content_from_step 的归位，
        # 且必须写在 issues 里（静默替换会让用户没法解释自己看到的结果）
        joined = " ".join(plan["issues"])
        assert "filename" in joined and "path" in joined, plan["issues"]
        assert "content_ref" in joined, plan["issues"]

        _execute(client, auth_headers, run_id, 1)
        client.post(f"/workflow/runs/{run_id}/steps/2/approve", headers=auth_headers)
        _execute(client, auth_headers, run_id, 2)

        detail = client.get(f"/workflow/runs/{run_id}", headers=auth_headers).json()
        step2 = next(s for s in detail["steps"] if s["idx"] == 2)
        notes = step2["output"].get("arg_notes") or []
        assert any("第 1 步" in n for n in notes), notes
        assert step2["output"]["written"]["filename"] == "out.py"

    def test_plan_flags_missing_code_with_rename_note(
        self, client, auth_headers, wf_env, workspace
    ):
        """`expression` → `code` 的归位要在**计划期**就说清楚。"""
        run = client.post(
            "/workflow/runs",
            json={"workspace_id": workspace["id"], "raw_input": "算一下", "mode": "auto"},
            headers=auth_headers,
        ).json()
        plan = client.put(
            f"/workflow/runs/{run['id']}/plan",
            json={
                "goal": "算个数",
                "steps": [
                    {
                        "kind": "tool",
                        "title": "计算",
                        "action": {
                            "tool": "code_executor",
                            "args": {"expression": "print(1 + 1)"},
                        },
                    }
                ],
            },
            headers=auth_headers,
        ).json()
        joined = " ".join(plan["issues"])
        assert "expression" in joined and "code" in joined, plan["issues"]
        step = plan["plan"]["steps"][0]
        assert step["action"]["args"]["code"] == "print(1 + 1)"

    def test_bound_folder_target_refused_without_binding(
        self, client, auth_headers, wf_env, workspace, monkeypatch
    ):
        """未绑定文件夹时要点名拒绝（而不是写到一个猜出来的路径）。"""
        _use_llm(monkeypatch)
        steps = [
            {"kind": "llm", "title": "写", "action": {"prompt_ref": "写"}},
            {
                "kind": "tool",
                "title": "存到绑定目录",
                "action": {
                    "tool": "file_write",
                    "args": {"path": "a.py", "content_from_step": 1, "target": "bound_folder"},
                },
            },
        ]
        run, _ = _run_with_plan(client, auth_headers, workspace, steps=steps)
        run_id = run["id"]
        _execute(client, auth_headers, run_id, 1)
        client.post(f"/workflow/runs/{run_id}/steps/2/approve", headers=auth_headers)
        result = _final(_execute(client, auth_headers, run_id, 2))

        assert result.get("ok") is False
        assert "绑定" in json.dumps(result, ensure_ascii=False)
