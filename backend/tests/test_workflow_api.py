"""AI 工作流 P1 测试：工作区 / 任务 / 临时文件引用。

重点验证的是「容易做错但不会被编译器发现」的地方：

  * 级联删除真的生效（`PRAGMA foreign_keys` 必须**逐连接**开启，
    漏了的话 `ON DELETE CASCADE` 会静默失效，留下孤儿行）
  * run 的状态机字段有校验（非法 mode/status 必须报错，不能默默写库）
  * 磁盘与数据库的一致性顺序：先落盘再记库 / 先删库再删盘
  * 文件解析失败**不阻断上传**，而是以 warning 回报原因
"""

from __future__ import annotations

import io

import pytest


@pytest.fixture
def wf_env(tmp_path, monkeypatch):
    """把 workflow 的库与工作区目录指到临时目录，避免污染 data/。"""
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
def ws(client, auth_headers, wf_env):
    resp = client.post(
        "/workflow/workspaces", json={"name": "测试工作区"}, headers=auth_headers
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


@pytest.fixture
def run(client, auth_headers, wf_env, ws):
    resp = client.post(
        "/workflow/runs",
        json={"workspace_id": ws["id"], "raw_input": "对比三家大模型的推理成本"},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


# ---------------------------------------------------------------------------
#  能力与配置
# ---------------------------------------------------------------------------


class TestCapabilities:
    def test_reports_modes_and_limits(self, client, auth_headers, wf_env):
        resp = client.get("/workflow/capabilities", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["enabled"] is True
        assert [m["value"] for m in data["modes"]] == ["manual", "auto"]
        assert data["max_steps"] >= 1
        # 高风险工具白名单必须下发，前端要据此标注「此步无法跳过审批」
        assert "code_executor" in data["require_approval_tools"]

    def test_reports_allowed_extensions(self, client, auth_headers, wf_env):
        """★ 允许引用的类型必须下发给前端。

        否则选择器只能列出**所有**文件，用户看得见、点得动、一导入就被拒 ——
        会合理地怀疑「程序访问不了我的本地文件」。能不能引用要看得见。
        """
        data = client.get("/workflow/capabilities", headers=auth_headers).json()
        exts = data["allowed_extensions"]
        assert isinstance(exts, list) and exts
        assert all(e.startswith(".") for e in exts)
        # 与后端白名单是同一份
        from app.document import SUPPORTED_EXTENSIONS

        assert set(exts) == {e.lower() for e in SUPPORTED_EXTENSIONS}
        # 做前端类任务时必须能引用已有的页面
        assert ".html" in exts


# ---------------------------------------------------------------------------
#  工作区
# ---------------------------------------------------------------------------


class TestWorkspaces:
    def test_create_list_rename_delete(self, client, auth_headers, wf_env):
        created = client.post(
            "/workflow/workspaces", json={"name": "分析"}, headers=auth_headers
        ).json()
        assert created["name"] == "分析"
        assert created["id"].startswith("ws_")
        assert created["run_count"] == 0

        listed = client.get("/workflow/workspaces", headers=auth_headers).json()
        assert any(w["id"] == created["id"] for w in listed["workspaces"])

        renamed = client.patch(
            f"/workflow/workspaces/{created['id']}",
            json={"name": "分析（改）"},
            headers=auth_headers,
        ).json()
        assert renamed["name"] == "分析（改）"

        deleted = client.delete(
            f"/workflow/workspaces/{created['id']}", headers=auth_headers
        ).json()
        assert deleted["success"] is True

        after = client.get("/workflow/workspaces", headers=auth_headers).json()
        assert not any(w["id"] == created["id"] for w in after["workspaces"])

    def test_empty_name_rejected(self, client, auth_headers, wf_env):
        resp = client.post(
            "/workflow/workspaces", json={"name": "   "}, headers=auth_headers
        )
        assert resp.status_code == 400

    def test_missing_workspace_404(self, client, auth_headers, wf_env):
        resp = client.get("/workflow/runs", headers=auth_headers, params={})
        assert resp.status_code == 200
        resp = client.patch(
            "/workflow/workspaces/ws_notexist",
            json={"name": "x"},
            headers=auth_headers,
        )
        assert resp.status_code == 404

    def test_delete_workspace_cascades_runs_and_files(
        self, client, auth_headers, wf_env, run, ws
    ):
        """级联删除：工作区没了，其下 run 与 run_files 必须一起消失。

        这类 bug 不会报错 —— `PRAGMA foreign_keys` 忘了开的话，
        删除只会留下孤儿行，接口看起来一切正常。
        """
        import os

        uploaded = client.post(
            f"/workflow/runs/{run['id']}/files",
            files={"files": ("note.txt", io.BytesIO("你好".encode()), "text/plain")},
            headers=auth_headers,
        ).json()
        file_id = uploaded["files"][0]["id"]

        from app.repositories import workflow_repo as repo

        assert repo.list_runs(workspace_id=ws["id"])
        file_path = repo.list_run_files(run["id"])[0]["stored_path"]
        assert os.path.exists(file_path)

        client.delete(f"/workflow/workspaces/{ws['id']}", headers=auth_headers)

        assert repo.get_run(run["id"]) is None
        assert repo.list_steps(run["id"]) == []
        assert repo.get_run_file(file_id) is None
        assert not os.path.exists(file_path)


# ---------------------------------------------------------------------------
#  任务
# ---------------------------------------------------------------------------


class TestRuns:
    def test_create_defaults_to_config_mode(self, client, auth_headers, wf_env, run):
        assert run["id"].startswith("run_")
        assert run["status"] == "draft"
        assert run["mode"] == "manual"
        assert run["raw_input"].startswith("对比三家")
        # 标题默认取输入前 40 字，便于侧边栏直接展示
        assert run["title"]
        assert run["steps"] == []
        assert run["files"] == []

    def test_create_with_auto_mode(self, client, auth_headers, wf_env, ws):
        resp = client.post(
            "/workflow/runs",
            json={
                "workspace_id": ws["id"],
                "raw_input": "自动跑一遍",
                "mode": "auto",
                "force_search": True,
            },
            headers=auth_headers,
        )
        data = resp.json()
        assert data["mode"] == "auto"
        assert data["force_search"] is True

    def test_invalid_mode_422(self, client, auth_headers, wf_env, ws):
        resp = client.post(
            "/workflow/runs",
            json={"workspace_id": ws["id"], "raw_input": "x", "mode": "乱填"},
            headers=auth_headers,
        )
        assert resp.status_code == 422

    def test_empty_input_rejected(self, client, auth_headers, wf_env, ws):
        resp = client.post(
            "/workflow/runs",
            json={"workspace_id": ws["id"], "raw_input": "   "},
            headers=auth_headers,
        )
        assert resp.status_code == 400

    def test_unknown_workspace_rejected(self, client, auth_headers, wf_env):
        resp = client.post(
            "/workflow/runs",
            json={"workspace_id": "ws_nope", "raw_input": "x"},
            headers=auth_headers,
        )
        assert resp.status_code == 404

    def test_mode_can_be_switched_mid_flight(self, client, auth_headers, wf_env, run):
        """执行中允许改模式 —— 用户常在看到前两步质量后决定放权（设计 §7.3）。"""
        resp = client.patch(
            f"/workflow/runs/{run['id']}", json={"mode": "auto"}, headers=auth_headers
        )
        assert resp.status_code == 200
        assert resp.json()["mode"] == "auto"
        assert resp.json()["status"] == "draft"  # 改模式不影响状态机位置

    def test_list_filters_by_workspace(self, client, auth_headers, wf_env, run, ws):
        other = client.post(
            "/workflow/workspaces", json={"name": "另一个"}, headers=auth_headers
        ).json()
        client.post(
            "/workflow/runs",
            json={"workspace_id": other["id"], "raw_input": "另一个任务"},
            headers=auth_headers,
        )

        mine = client.get(
            "/workflow/runs", params={"workspace_id": ws["id"]}, headers=auth_headers
        ).json()
        assert [r["id"] for r in mine["runs"]] == [run["id"]]

    def test_delete_run_removes_files_from_disk(
        self, client, auth_headers, wf_env, run
    ):
        import os

        client.post(
            f"/workflow/runs/{run['id']}/files",
            files={"files": ("a.txt", io.BytesIO("内容".encode()), "text/plain")},
            headers=auth_headers,
        )
        from app.repositories import workflow_repo as repo

        path = repo.list_run_files(run["id"])[0]["stored_path"]
        assert os.path.exists(path)

        assert (
            client.delete(f"/workflow/runs/{run['id']}", headers=auth_headers).status_code
            == 200
        )
        assert repo.get_run(run["id"]) is None
        assert not os.path.exists(path)

    def test_get_missing_run_404(self, client, auth_headers, wf_env):
        assert (
            client.get("/workflow/runs/run_nope", headers=auth_headers).status_code == 404
        )


# ---------------------------------------------------------------------------
#  临时文件引用
# ---------------------------------------------------------------------------


class TestRunFiles:
    def test_upload_parses_text_and_records_char_count(
        self, client, auth_headers, wf_env, run
    ):
        content = "这是一段测试内容，用于验证解析字符数。"
        resp = client.post(
            f"/workflow/runs/{run['id']}/files",
            files={"files": ("note.txt", io.BytesIO(content.encode()), "text/plain")},
            headers=auth_headers,
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["success"] is True
        assert len(data["files"]) == 1

        item = data["files"][0]
        assert item["id"].startswith("rf_")
        assert item["filename"] == "note.txt"
        assert item["kind"] == "input"
        # 解析成功 → 有字符数、无告警
        assert item["parsed_chars"] == len(content)
        assert data["warnings"] == []
        assert item["ttl_expires_at"] > 0

    def test_upload_stores_file_under_workspace_dir(
        self, client, auth_headers, wf_env, run, ws
    ):
        import os

        client.post(
            f"/workflow/runs/{run['id']}/files",
            files={"files": ("x.txt", io.BytesIO(b"abc"), "text/plain")},
            headers=auth_headers,
        )
        from app.repositories import workflow_repo as repo

        record = repo.list_run_files(run["id"])[0]
        assert os.path.isfile(record["stored_path"])
        # 落盘目录必须落在该工作区下，而不是全局 data/
        assert ws["id"] in record["stored_path"]
        # 文件名与 file_id 对齐（不需要「先写临时名再改名」）
        assert record["id"] in os.path.basename(record["stored_path"])

    def test_unparseable_file_warns_but_still_uploads(
        self, client, auth_headers, wf_env, run
    ):
        """解析不出来是**环境/内容问题**，不是上传失败 ——
        文件仍是用户的资产，但要明确告知它不会贡献上下文。
        """
        resp = client.post(
            f"/workflow/runs/{run['id']}/files",
            files={"files": ("empty.txt", io.BytesIO(b""), "text/plain")},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["files"]) == 1
        assert data["files"][0]["parsed_chars"] is None
        assert any("empty.txt" in w for w in data["warnings"])

    def test_illegal_filename_rejected(self, client, auth_headers, wf_env, run):
        resp = client.post(
            f"/workflow/runs/{run['id']}/files",
            files={"files": ("../evil.txt", io.BytesIO(b"x"), "text/plain")},
            headers=auth_headers,
        )
        assert resp.status_code == 400

    def test_unsupported_extension_rejected(self, client, auth_headers, wf_env, run):
        resp = client.post(
            f"/workflow/runs/{run['id']}/files",
            files={"files": ("x.exe", io.BytesIO(b"x"), "application/octet-stream")},
            headers=auth_headers,
        )
        assert resp.status_code == 400

    def test_list_and_delete_file(self, client, auth_headers, wf_env, run):
        import os

        uploaded = client.post(
            f"/workflow/runs/{run['id']}/files",
            files={"files": ("d.txt", io.BytesIO(b"hello"), "text/plain")},
            headers=auth_headers,
        ).json()
        file_id = uploaded["files"][0]["id"]

        listed = client.get(
            f"/workflow/runs/{run['id']}/files", headers=auth_headers
        ).json()
        assert len(listed["files"]) == 1

        from app.repositories import workflow_repo as repo

        path = repo.get_run_file(file_id)["stored_path"]

        assert (
            client.delete(
                f"/workflow/runs/{run['id']}/files/{file_id}", headers=auth_headers
            ).status_code
            == 200
        )
        assert repo.get_run_file(file_id) is None
        assert not os.path.exists(path)

    def test_download_returns_bytes(self, client, auth_headers, wf_env, run):
        body = b"download-me"
        file_id = client.post(
            f"/workflow/runs/{run['id']}/files",
            files={"files": ("dl.txt", io.BytesIO(body), "text/plain")},
            headers=auth_headers,
        ).json()["files"][0]["id"]

        resp = client.get(f"/workflow/files/{file_id}/download", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.content == body

    def test_download_missing_file_404(self, client, auth_headers, wf_env):
        assert (
            client.get("/workflow/files/rf_nope/download", headers=auth_headers).status_code
            == 404
        )

    def test_delete_file_of_other_run_404(self, client, auth_headers, wf_env, run, ws):
        """文件必须与 run 匹配 —— 否则可以用任意 run_id 删掉别人的文件。"""
        other = client.post(
            "/workflow/runs",
            json={"workspace_id": ws["id"], "raw_input": "另一个"},
            headers=auth_headers,
        ).json()
        file_id = client.post(
            f"/workflow/runs/{run['id']}/files",
            files={"files": ("f.txt", io.BytesIO(b"x"), "text/plain")},
            headers=auth_headers,
        ).json()["files"][0]["id"]

        resp = client.delete(
            f"/workflow/runs/{other['id']}/files/{file_id}", headers=auth_headers
        )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
#  仓库层：步骤状态机（P2/P3 的基座，先把契约钉住）
# ---------------------------------------------------------------------------


class TestStepsRepo:
    def test_replace_and_read_steps(self, wf_env, run):
        from app.repositories import workflow_repo as repo

        steps = repo.replace_steps(
            run["id"],
            [
                {
                    "idx": 1,
                    "kind": "tool",
                    "title": "搜索定价",
                    "action": {"tool": "web_search", "args": {"query": "定价"}},
                },
                {
                    "idx": 2,
                    "kind": "synthesis",
                    "title": "整合报告",
                    "requires_approval": True,
                },
            ],
        )
        assert [s["idx"] for s in steps] == [1, 2]
        assert steps[0]["action"]["tool"] == "web_search"
        assert steps[1]["requires_approval"] is True
        assert all(s["status"] == "pending" for s in steps)

        # 整体替换（用户编辑任务书后走这里），不留旧行
        again = repo.replace_steps(run["id"], [{"kind": "llm", "title": "只留一步"}])
        assert len(again) == 1
        assert repo.list_steps(run["id"])[0]["title"] == "只留一步"

    def test_update_step_status_and_output(self, wf_env, run):
        from app.repositories import workflow_repo as repo

        repo.replace_steps(run["id"], [{"kind": "llm", "title": "起草"}])
        updated = repo.update_step(
            run["id"],
            1,
            status="done",
            output={"summary": "写完了", "sources": [{"title": "t", "href": "u"}]},
            approved_by="admin",
        )
        assert updated["status"] == "done"
        assert updated["output"]["sources"][0]["href"] == "u"
        assert updated["approved_by"] == "admin"

    def test_invalid_status_rejected(self, wf_env, run):
        from app.repositories import workflow_repo as repo

        with pytest.raises(ValueError):
            repo.replace_steps(run["id"], [{"kind": "llm", "status": "乱填"}])
        repo.replace_steps(run["id"], [{"kind": "llm", "title": "x"}])
        with pytest.raises(ValueError):
            repo.update_step(run["id"], 1, status="乱填")

    def test_invalid_kind_rejected(self, wf_env, run):
        from app.repositories import workflow_repo as repo

        with pytest.raises(ValueError):
            repo.replace_steps(run["id"], [{"kind": "乱填", "title": "x"}])

    def test_invalid_run_field_rejected(self, wf_env, run):
        from app.repositories import workflow_repo as repo

        with pytest.raises(ValueError):
            repo.update_run(run["id"], nonexistent_field=1)
