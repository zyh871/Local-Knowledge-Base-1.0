"""身份底座测试：引导管理员、建号、登录、邀请、管理员边界。"""

from __future__ import annotations

import pytest

from app.core.exceptions import ConflictError, ValidationError


@pytest.fixture()
def identity_db(tmp_path, monkeypatch):
    from app.repositories import user_repo

    monkeypatch.setattr(user_repo, "_db_path", lambda: str(tmp_path / "identity.db"))
    user_repo.reset_connection()
    yield user_repo
    user_repo.reset_connection()


@pytest.fixture()
def team_db(tmp_path, monkeypatch):
    from app.repositories import team_repo

    monkeypatch.setattr(team_repo, "_db_path", lambda: str(tmp_path / "teams.db"))
    team_repo.reset_connection()
    yield team_repo
    team_repo.reset_connection()


@pytest.fixture()
def identity_service(identity_db):
    from app.services import identity_service

    return identity_service


@pytest.fixture()
def admin_headers():
    """直接签管理员令牌，避免 /token 的 5 次/分钟限流被用例累计打满。"""
    from app.core.config import get_security_settings
    from app.core.security import create_access_token

    cfg = get_security_settings()
    token = create_access_token({"sub": "usr_admin", "username": cfg.admin_username})
    return {"Authorization": f"Bearer {token}"}


def _login(client, username: str, password: str):
    return client.post(
        "/token",
        data={"username": username, "password": password},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )


class TestIdentityService:
    def test_bootstrap_is_idempotent(self, identity_service):
        first = identity_service.bootstrap_identity()
        second = identity_service.bootstrap_identity()
        assert first["id"] == "usr_admin"
        assert second["id"] == "usr_admin"
        assert second["global_role"] == "admin"

    def test_create_user_and_duplicate(self, identity_service):
        user = identity_service.create_user(
            username="alice", password="password123", display_name="Alice"
        )
        assert user["id"].startswith("usr_")
        assert user["display_name"] == "Alice"
        with pytest.raises(ConflictError):
            identity_service.create_user(username="alice", password="password123")

    def test_password_policy(self, identity_service):
        with pytest.raises(ValidationError):
            identity_service.create_user(username="bob", password="short")

    def test_username_policy(self, identity_service):
        with pytest.raises(ValidationError):
            identity_service.create_user(username="a b", password="password123")

    def test_account_invitation_flow(self, identity_service):
        inv = identity_service.create_invitation(
            kind="account", created_by="admin", ttl_hours=24
        )
        result = identity_service.accept_invitation(
            token=inv["token"],
            username="carol",
            password="password123",
            display_name="Carol",
        )
        assert result["user"]["username"] == "carol"
        with pytest.raises(ConflictError):
            identity_service.accept_invitation(
                token=inv["token"], username="dave", password="password123"
            )

    def test_invalid_token_rejected(self, identity_service):
        from app.core.exceptions import NotFoundError

        with pytest.raises(NotFoundError):
            identity_service.accept_invitation(
                token="not-a-real-token", username="eve", password="password123"
            )


class TestIdentityHttp:
    def test_new_user_can_login_and_token_sub_is_user_id(
        self, client, identity_db, identity_service
    ):
        user = identity_service.create_user(
            username="alice", password="password123", display_name="Alice"
        )
        response = _login(client, "alice", "password123")
        assert response.status_code == 200, response.text

        from app.core.security import decode_access_token

        payload = decode_access_token(response.json()["access_token"])
        assert payload["sub"] == user["id"]
        assert payload["username"] == "alice"

        me = client.get(
            "/users/me/",
            headers={"Authorization": f"Bearer {response.json()['access_token']}"},
        )
        assert me.status_code == 200
        assert me.json()["id"] == user["id"]
        assert me.json()["global_role"] == "user"

    def test_admin_endpoints_require_admin(self, client, identity_db, identity_service):
        user = identity_service.create_user(username="alice", password="password123")
        from app.core.security import create_access_token

        token = create_access_token({"sub": user["id"], "username": "alice"})
        response = client.get(
            "/admin/users", headers={"Authorization": f"Bearer {token}"}
        )
        assert response.status_code == 403

    def test_admin_creates_user(self, client, admin_headers, identity_db):
        response = client.post(
            "/admin/users",
            json={
                "username": "bob",
                "password": "password123",
                "display_name": "Bob",
                "global_role": "user",
            },
            headers=admin_headers,
        )
        assert response.status_code == 200, response.text
        assert response.json()["username"] == "bob"

        listed = client.get("/admin/users", headers=admin_headers)
        assert any(u["username"] == "bob" for u in listed.json())

    def test_team_invitation_joins_team(
        self, client, admin_headers, identity_db, team_db, identity_service
    ):
        created = client.post(
            "/teams", json={"name": "研发组", "description": ""}, headers=admin_headers
        )
        team_id = created.json()["id"]

        inv = client.post(
            "/admin/invitations",
            json={"kind": "team", "team_id": team_id, "role": "viewer"},
            headers=admin_headers,
        )
        assert inv.status_code == 200, inv.text
        token = inv.json()["token"]

        accepted = client.post(
            "/invitations/accept",
            json={
                "token": token,
                "username": "carol",
                "password": "password123",
                "display_name": "Carol",
            },
        )
        assert accepted.status_code == 200, accepted.text

        # 直接签令牌，避免 /token 的 5 次/分钟限流被前面用例累计打满
        from app.core.security import create_access_token

        carol_id = accepted.json()["user"]["id"]
        carol_token = create_access_token({"sub": carol_id, "username": "carol"})
        detail = client.get(
            f"/teams/{team_id}",
            headers={"Authorization": f"Bearer {carol_token}"},
        )
        assert detail.status_code == 200
        assert detail.json()["my_role"] == "viewer"
