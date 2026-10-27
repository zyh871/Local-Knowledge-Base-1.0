"""团队空间与角色权限测试。

分两层：
  * **服务层**：用任意用户名直接调用 `TeamService`，覆盖多角色与越权路径。
    这是必要的 —— 现有认证只有一个管理员账号，无法通过 HTTP 造出
    「第二个用户」，而越权规则恰恰只有在多用户下才有意义。
  * **HTTP 层**：用管理员令牌跑一遍真实路由，确认契约、鉴权与持久化接通。

每个用例使用独立的临时数据库，避免污染 `data/teams.db`。
"""

from __future__ import annotations

import pytest

from app.core.exceptions import (
    NotFoundError,
    PermissionDeniedError,
    ValidationError,
)


@pytest.fixture()
def team_repo(tmp_path, monkeypatch):
    """把 team_repo 指向临时库，并在用例前后丢弃连接。"""
    from app.repositories import team_repo as repo

    monkeypatch.setattr(repo, "_db_path", lambda: str(tmp_path / "teams.db"))
    repo.reset_connection()
    yield repo
    repo.reset_connection()


@pytest.fixture()
def service(team_repo):
    from app.services.team_service import TeamService

    return TeamService(settings=None)


# ---------------------------------------------------------------------------
#  服务层：角色与越权
# ---------------------------------------------------------------------------


class TestTeamRoles:
    def test_create_team_sets_owner(self, service):
        team = service.create_team(name="研发组", description="d", owner="alice")
        assert team["owner"] == "alice"
        assert team["my_role"] == "owner"
        assert team["member_count"] == 1

    def test_create_rejects_blank_name(self, service):
        with pytest.raises(ValidationError):
            service.create_team(name="   ", description="", owner="alice")

    def test_non_member_gets_404_not_403(self, service):
        """非成员与不存在统一 404，避免枚举探测团队是否存在。"""
        team = service.create_team(name="t", description="", owner="alice")
        with pytest.raises(NotFoundError):
            service.get_team(team["id"], "mallory")

    def test_editor_can_read_but_not_manage(self, service):
        team = service.create_team(name="t", description="", owner="alice")
        service.add_member(team["id"], "alice", target="bob", role="editor")

        assert service.get_team(team["id"], "bob")["my_role"] == "editor"
        caps = service.permissions(team["id"], "bob")["permissions"]
        assert "content:write" in caps
        assert "team:manage_members" not in caps

        with pytest.raises(PermissionDeniedError):
            service.add_member(team["id"], "bob", target="carol", role="viewer")

    def test_viewer_cannot_write(self, service):
        team = service.create_team(name="t", description="", owner="alice")
        service.add_member(team["id"], "alice", target="bob", role="viewer")
        caps = service.permissions(team["id"], "bob")["permissions"]
        assert "content:read" in caps
        assert "content:write" not in caps

    def test_admin_cannot_grant_admin(self, service):
        """核心防越权：管理员不能批量制造管理员。"""
        team = service.create_team(name="t", description="", owner="alice")
        service.add_member(team["id"], "alice", target="bob", role="admin")
        with pytest.raises(PermissionDeniedError):
            service.add_member(team["id"], "bob", target="carol", role="admin")

    def test_admin_cannot_remove_peer_admin(self, service):
        team = service.create_team(name="t", description="", owner="alice")
        service.add_member(team["id"], "alice", target="bob", role="admin")
        service.add_member(team["id"], "alice", target="carol", role="admin")
        with pytest.raises(PermissionDeniedError):
            service.remove_member(team["id"], "bob", "carol")

    def test_admin_cannot_demote_peer_admin(self, service):
        """降级同级 admin 同样等于「撤销」，必须拦住。"""
        team = service.create_team(name="t", description="", owner="alice")
        service.add_member(team["id"], "alice", target="bob", role="admin")
        service.add_member(team["id"], "alice", target="carol", role="admin")
        with pytest.raises(PermissionDeniedError):
            service.add_member(team["id"], "bob", target="carol", role="editor")

    def test_owner_cannot_be_removed_or_demoted(self, service):
        team = service.create_team(name="t", description="", owner="alice")
        service.add_member(team["id"], "alice", target="bob", role="admin")

        with pytest.raises(PermissionDeniedError):
            service.remove_member(team["id"], "bob", "alice")
        with pytest.raises(PermissionDeniedError):
            service.add_member(team["id"], "bob", target="alice", role="viewer")

    def test_cannot_remove_self(self, service):
        team = service.create_team(name="t", description="", owner="alice")
        service.add_member(team["id"], "alice", target="bob", role="editor")
        with pytest.raises(PermissionDeniedError):
            service.remove_member(team["id"], "bob", "bob")

    def test_only_owner_can_delete(self, service):
        team = service.create_team(name="t", description="", owner="alice")
        service.add_member(team["id"], "alice", target="bob", role="admin")
        with pytest.raises(PermissionDeniedError):
            service.delete_team(team["id"], "bob")
        assert service.delete_team(team["id"], "alice")["success"] is True
        with pytest.raises(NotFoundError):
            service.get_team(team["id"], "alice")

    def test_admin_can_update_profile(self, service):
        team = service.create_team(name="old", description="", owner="alice")
        service.add_member(team["id"], "alice", target="bob", role="admin")
        updated = service.update_team(team["id"], "bob", name="new")
        assert updated["name"] == "new"

    def test_editor_cannot_update_profile(self, service):
        team = service.create_team(name="old", description="", owner="alice")
        service.add_member(team["id"], "alice", target="bob", role="editor")
        with pytest.raises(PermissionDeniedError):
            service.update_team(team["id"], "bob", name="hacked")


# ---------------------------------------------------------------------------
#  HTTP 层：真实路由
# ---------------------------------------------------------------------------


class TestTeamHttp:
    def test_team_lifecycle(self, client, auth_headers, team_repo):
        created = client.post(
            "/teams",
            json={"name": "合规项目组", "description": "内部"},
            headers=auth_headers,
        )
        assert created.status_code == 200, created.text
        body = created.json()
        assert body["my_role"] == "owner"
        team_id = body["id"]

        listed = client.get("/teams", headers=auth_headers)
        assert listed.status_code == 200
        assert any(t["id"] == team_id for t in listed.json())

        perms = client.get(f"/teams/{team_id}/permissions", headers=auth_headers)
        assert perms.status_code == 200
        assert "team:delete" in perms.json()["permissions"]

        patched = client.patch(
            f"/teams/{team_id}", json={"name": "改名后"}, headers=auth_headers
        )
        assert patched.status_code == 200
        assert patched.json()["name"] == "改名后"

        deleted = client.delete(f"/teams/{team_id}", headers=auth_headers)
        assert deleted.status_code == 200
        assert deleted.json()["success"] is True

        gone = client.get(f"/teams/{team_id}", headers=auth_headers)
        assert gone.status_code == 404

    def test_requires_auth(self, client, team_repo):
        assert client.get("/teams").status_code == 401
