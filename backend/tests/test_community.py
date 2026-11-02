"""社区模块测试：草稿可见性、发布、贡献者、互动与越权。"""

from __future__ import annotations

import pytest


@pytest.fixture()
def community_env(tmp_path, monkeypatch):
    from app.repositories import community_repo, team_repo, user_repo

    monkeypatch.setattr(
        community_repo, "_db_path", lambda: str(tmp_path / "community.db")
    )
    monkeypatch.setattr(team_repo, "_db_path", lambda: str(tmp_path / "teams.db"))
    monkeypatch.setattr(
        user_repo, "_db_path", lambda: str(tmp_path / "identity.db")
    )
    for module in (community_repo, team_repo, user_repo):
        module.reset_connection()
    yield
    for module in (community_repo, team_repo, user_repo):
        module.reset_connection()


@pytest.fixture()
def people(community_env):
    from app.services import identity_service

    return {
        "alice": identity_service.create_user(
            username="alice", password="password123", display_name="Alice"
        ),
        "bob": identity_service.create_user(
            username="bob", password="password123", display_name="Bob"
        ),
        "carol": identity_service.create_user(
            username="carol", password="password123", display_name="Carol"
        ),
    }


def _headers(user: dict) -> dict[str, str]:
    from app.core.security import create_access_token

    token = create_access_token({"sub": user["id"], "username": user["username"]})
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture()
def admin_h():
    from app.core.config import get_security_settings
    from app.core.security import create_access_token

    cfg = get_security_settings()
    token = create_access_token({"sub": "usr_admin", "username": cfg.admin_username})
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture()
def team_id(client, admin_h, people):
    created = client.post(
        "/teams", json={"name": "研发组", "description": ""}, headers=admin_h
    )
    tid = created.json()["id"]
    client.post(
        f"/teams/{tid}/members",
        json={"username": "alice", "role": "editor"},
        headers=admin_h,
    )
    client.post(
        f"/teams/{tid}/members",
        json={"username": "bob", "role": "viewer"},
        headers=admin_h,
    )
    return tid


def _create_project(client, headers, team_id, name="智鲸插件"):
    return client.post(
        "/community/projects",
        json={
            "team_id": team_id,
            "name": name,
            "summary": "把知识库能力接入 IDE",
            "language": "Vue",
            "license": "MIT",
            "repo_url": "https://github.com/example/zhiwhale",
            "tags": ["RAG", "Vue"],
        },
        headers=headers,
    )


class TestProjectLifecycle:
    def test_draft_is_team_only(self, client, people, team_id):
        created = _create_project(client, _headers(people["alice"]), team_id)
        assert created.status_code == 200, created.text
        project_id = created.json()["id"]
        assert created.json()["visibility"] == "draft"
        assert created.json()["my_project_role"] == "maintainer"

        # 团队成员可读
        assert (
            client.get(
                f"/community/projects/{project_id}", headers=_headers(people["bob"])
            ).status_code
            == 200
        )
        # 非成员统一 404
        assert (
            client.get(
                f"/community/projects/{project_id}",
                headers=_headers(people["carol"]),
            ).status_code
            == 404
        )

    def test_publish_makes_it_visible(self, client, people, team_id):
        project_id = _create_project(
            client, _headers(people["alice"]), team_id
        ).json()["id"]

        published = client.post(
            f"/community/projects/{project_id}/publish",
            headers=_headers(people["alice"]),
        )
        assert published.status_code == 200, published.text
        assert published.json()["visibility"] == "published"

        listing = client.get(
            "/community/projects", headers=_headers(people["carol"])
        )
        assert any(p["id"] == project_id for p in listing.json())

    def test_viewer_cannot_create_or_publish(self, client, people, team_id):
        project_id = _create_project(
            client, _headers(people["alice"]), team_id
        ).json()["id"]

        denied_create = _create_project(
            client, _headers(people["bob"]), team_id, name="越权项目"
        )
        assert denied_create.status_code == 403

        denied_publish = client.post(
            f"/community/projects/{project_id}/publish",
            headers=_headers(people["bob"]),
        )
        assert denied_publish.status_code == 403


class TestInteraction:
    def test_star_watch_comment_follow(self, client, people, team_id):
        project_id = _create_project(
            client, _headers(people["alice"]), team_id
        ).json()["id"]
        client.post(
            f"/community/projects/{project_id}/publish",
            headers=_headers(people["alice"]),
        )

        carol_h = _headers(people["carol"])
        starred = client.post(
            f"/community/projects/{project_id}/star", headers=carol_h
        )
        assert starred.status_code == 200
        assert starred.json()["is_starred"] is True
        assert starred.json()["stars_count"] == 1

        # 幂等：重复 star 不再 +1
        again = client.post(
            f"/community/projects/{project_id}/star", headers=carol_h
        )
        assert again.json()["stars_count"] == 1

        watched = client.post(
            f"/community/projects/{project_id}/watch", headers=carol_h
        )
        assert watched.json()["is_watching"] is True

        comment = client.post(
            f"/community/projects/{project_id}/comments",
            json={"body": "这个项目不错"},
            headers=carol_h,
        )
        assert comment.status_code == 200, comment.text
        comments = client.get(
            f"/community/projects/{project_id}/comments", headers=carol_h
        )
        assert len(comments.json()) == 1

        followed = client.post(
            "/community/users/alice/follow", headers=carol_h
        )
        assert followed.status_code == 200, followed.text
        profile = client.get("/community/users/alice", headers=carol_h)
        assert profile.json()["followers"] == 1
        assert profile.json()["is_following"] is True

    def test_cannot_comment_on_draft_as_outsider(self, client, people, team_id):
        project_id = _create_project(
            client, _headers(people["alice"]), team_id
        ).json()["id"]
        response = client.post(
            f"/community/projects/{project_id}/comments",
            json={"body": "看不到我"},
            headers=_headers(people["carol"]),
        )
        assert response.status_code == 404

    def test_comment_delete_permissions(self, client, people, team_id):
        project_id = _create_project(
            client, _headers(people["alice"]), team_id
        ).json()["id"]
        client.post(
            f"/community/projects/{project_id}/publish",
            headers=_headers(people["alice"]),
        )

        alice_h = _headers(people["alice"])
        carol_h = _headers(people["carol"])
        comment = client.post(
            f"/community/projects/{project_id}/comments",
            json={"body": "alice 的评论"},
            headers=alice_h,
        ).json()

        denied = client.delete(
            f"/community/projects/{project_id}/comments/{comment['id']}",
            headers=carol_h,
        )
        assert denied.status_code == 403

        deleted = client.delete(
            f"/community/projects/{project_id}/comments/{comment['id']}",
            headers=alice_h,
        )
        assert deleted.status_code == 200


class TestContributors:
    def test_maintainer_adds_team_member(self, client, people, team_id):
        project_id = _create_project(
            client, _headers(people["alice"]), team_id
        ).json()["id"]
        alice_h = _headers(people["alice"])

        added = client.post(
            f"/community/projects/{project_id}/contributors",
            json={"username": "bob", "role": "contributor"},
            headers=alice_h,
        )
        assert added.status_code == 200, added.text
        assert added.json()["role"] == "contributor"

        listing = client.get(
            f"/community/projects/{project_id}/contributors", headers=alice_h
        )
        assert {m["username"] for m in listing.json()} == {"alice", "bob"}

        # contributor 不能再加人
        denied = client.post(
            f"/community/projects/{project_id}/contributors",
            json={"username": "carol"},
            headers=_headers(people["bob"]),
        )
        assert denied.status_code == 403

    def test_cannot_add_outsider(self, client, people, team_id):
        project_id = _create_project(
            client, _headers(people["alice"]), team_id
        ).json()["id"]
        response = client.post(
            f"/community/projects/{project_id}/contributors",
            json={"username": "carol"},
            headers=_headers(people["alice"]),
        )
        assert response.status_code == 400
