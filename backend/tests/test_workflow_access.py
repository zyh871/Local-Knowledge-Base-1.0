"""工作流任务的读权限隔离。

价值在于「权限在服务端生效」：即使前端不隐藏入口、直接构造 `run_id` /
`file_id`，也必须被拒绝。

覆盖三条规则：
  * 普通成员：列表只含自己的任务，读他人任务 / 文件 / 产物 / 下载一律 403
  * 管理员：可读所有用户的任务（但写操作仍只归创建者）
  * 遗留数据（owner 为空）：fail-closed，仅管理员可见
"""

from __future__ import annotations

import pytest


def _headers(sub: str, username: str) -> dict[str, str]:
    from app.core.security import create_access_token

    token = create_access_token({"sub": sub, "username": username})
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture()
def wf_env(tmp_path, monkeypatch):
    """把 workflow / identity 两个库都指到临时目录。"""
    from app.core.config import clear_settings_cache
    from app.repositories import user_repo
    from app.repositories import workflow_repo as repo
    from app.services import identity_service

    monkeypatch.setenv("WORKFLOW_DB_PATH", str(tmp_path / "workflow.db"))
    monkeypatch.setenv("WORKFLOW_WORKSPACE_ROOT", str(tmp_path / "workspaces"))
    monkeypatch.setattr(user_repo, "_db_path", lambda: str(tmp_path / "identity.db"))
    clear_settings_cache()
    repo.reset_connection()
    user_repo.reset_connection()
    # 会话级 client 的 lifespan 可能已在真实库里引导过管理员，
    # 切库后必须在临时库里重建一次，否则 usr_admin 解析不到。
    identity_service.bootstrap_identity()
    yield tmp_path
    repo.reset_connection()
    user_repo.reset_connection()
    clear_settings_cache()


@pytest.fixture()
def admin_headers():
    """直接签令牌，避开 /token 的限流被多个用例累计打满。"""
    from app.core.config import get_security_settings
    from app.core.security import create_access_token

    cfg = get_security_settings()
    token = create_access_token({"sub": "usr_admin", "username": cfg.admin_username})
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture()
def member(wf_env):
    from app.services import identity_service

    rec = identity_service.create_user(username="member", password="member-pass-123")
    return {
        "id": rec["id"],
        "username": "member",
        "headers": _headers(rec["id"], "member"),
    }


@pytest.fixture()
def workspace(client, admin_headers, wf_env):
    resp = client.post(
        "/workflow/workspaces", json={"name": "权限测试"}, headers=admin_headers
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


@pytest.fixture()
def admin_run(client, admin_headers, workspace):
    resp = client.post(
        "/workflow/runs",
        json={"workspace_id": workspace["id"], "raw_input": "管理员的任务"},
        headers=admin_headers,
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _create_member_run(client, member, workspace) -> dict:
    resp = client.post(
        "/workflow/runs",
        json={"workspace_id": workspace["id"], "raw_input": "成员的任务"},
        headers=member["headers"],
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


class TestRunReadIsolation:
    def test_member_cannot_read_other_run(self, client, member, admin_run):
        resp = client.get(
            f"/workflow/runs/{admin_run['id']}", headers=member["headers"]
        )
        assert resp.status_code == 403, resp.text

    def test_member_list_contains_only_own(self, client, member, admin_run):
        resp = client.get("/workflow/runs", headers=member["headers"])
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert admin_run["id"] not in {r["id"] for r in body["runs"]}
        assert body["total"] == 0

    def test_member_list_total_counts_all_own_runs(
        self, client, member, workspace, admin_run
    ):
        for _ in range(3):
            _create_member_run(client, member, workspace)
        resp = client.get("/workflow/runs", headers=member["headers"])
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["runs"]) == 3
        # total 必须是真实总数，而不是页大小
        assert body["total"] == 3

    def test_admin_can_read_member_run(self, client, admin_headers, member, workspace):
        run_id = _create_member_run(client, member, workspace)["id"]
        resp = client.get(f"/workflow/runs/{run_id}", headers=admin_headers)
        assert resp.status_code == 200, resp.text
        assert resp.json()["id"] == run_id

    def test_admin_list_includes_every_user(
        self, client, admin_headers, member, workspace, admin_run
    ):
        member_run = _create_member_run(client, member, workspace)
        resp = client.get("/workflow/runs", headers=admin_headers)
        assert resp.status_code == 200
        ids = {r["id"] for r in resp.json()["runs"]}
        assert {admin_run["id"], member_run["id"]} <= ids
        assert resp.json()["total"] >= 2

    def test_member_cannot_list_other_files(self, client, member, admin_run):
        resp = client.get(
            f"/workflow/runs/{admin_run['id']}/files", headers=member["headers"]
        )
        assert resp.status_code == 403, resp.text

    def test_member_cannot_list_other_artifacts(self, client, member, admin_run):
        resp = client.get(
            f"/workflow/runs/{admin_run['id']}/artifacts", headers=member["headers"]
        )
        assert resp.status_code == 403, resp.text

    def test_member_cannot_download_other_file(
        self, client, admin_headers, member, admin_run
    ):
        uploaded = client.post(
            f"/workflow/runs/{admin_run['id']}/files",
            files=[("files", ("note.txt", b"hello", "text/plain"))],
            headers=admin_headers,
        )
        assert uploaded.status_code == 200, uploaded.text
        file_id = uploaded.json()["files"][0]["id"]

        resp = client.get(
            f"/workflow/files/{file_id}/download", headers=member["headers"]
        )
        assert resp.status_code == 403, resp.text

        # 创建者本人仍可下载，确认不是把功能整体关掉
        ok = client.get(
            f"/workflow/files/{file_id}/download", headers=admin_headers
        )
        assert ok.status_code == 200, ok.text

    def test_member_cannot_mutate_other_run(self, client, member, admin_run):
        patch = client.patch(
            f"/workflow/runs/{admin_run['id']}",
            json={"title": "被篡改"},
            headers=member["headers"],
        )
        assert patch.status_code == 403, patch.text

        delete = client.delete(
            f"/workflow/runs/{admin_run['id']}", headers=member["headers"]
        )
        assert delete.status_code == 403, delete.text

    def test_legacy_run_without_owner_is_admin_only(
        self, client, admin_headers, member, workspace
    ):
        """owner 为空的历史数据必须 fail-closed，不能对所有人开放。"""
        from app.repositories import workflow_repo as repo

        legacy = repo.create_run(
            workspace_id=workspace["id"],
            username="",
            raw_input="遗留任务",
            status="draft",
        )
        run_id = legacy["id"]

        denied = client.get(f"/workflow/runs/{run_id}", headers=member["headers"])
        assert denied.status_code == 403, denied.text

        allowed = client.get(f"/workflow/runs/{run_id}", headers=admin_headers)
        assert allowed.status_code == 200, allowed.text


class TestAdminOnlyEndpoints:
    """全局 / 敏感能力必须只对部署管理员开放。"""

    def test_member_cannot_list_model_endpoints(self, client, member):
        resp = client.get("/model-endpoints", headers=member["headers"])
        assert resp.status_code == 403, resp.text

    def test_member_cannot_write_search_config(self, client, member):
        resp = client.put(
            "/search/config",
            json={"provider": "duckduckgo", "api_key": ""},
            headers=member["headers"],
        )
        assert resp.status_code == 403, resp.text

    def test_member_cannot_read_all_feedback(self, client, member):
        resp = client.get("/feedback/list", headers=member["headers"])
        assert resp.status_code == 403, resp.text

    def test_member_cannot_delete_global_documents(self, client, member):
        resp = client.delete("/documents/whatever.txt", headers=member["headers"])
        assert resp.status_code == 403, resp.text

    def test_member_cannot_rebuild_vectorstore(self, client, member):
        resp = client.post("/rebuild-vectorstore", headers=member["headers"])
        assert resp.status_code == 403, resp.text

    def test_admin_still_has_access(self, client, admin_headers):
        assert (
            client.get("/model-endpoints", headers=admin_headers).status_code == 200
        )
        assert client.get("/feedback/list", headers=admin_headers).status_code == 200
