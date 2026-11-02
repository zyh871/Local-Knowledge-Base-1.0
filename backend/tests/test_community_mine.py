"""社区列表的可见性边界。

`/community/projects` 的默认视图只返回 **published**（`repo.list_projects` 里硬编码），
而 `?mine=1` 要返回「我创建的 + 我是贡献者的」，**含草稿**。

这两条规则必须同时成立，否则会出现两种相反的 bug：
  * 默认视图漏过滤 → 别人的草稿泄给所有人
  * mine 也走同一过滤器 → 自己刚建的项目再也找不回来
"""

from __future__ import annotations

import pytest


@pytest.fixture()
def repos(tmp_path, monkeypatch):
    """把社区 / 团队 / 身份三个库都指向临时目录。

    社区服务通过 `identity_service.resolve_user_id` 解析 user_id，
    身份库不隔离就会在真实 `data/identity.db` 里留下影子账号。
    """
    from app.repositories import community_repo, team_repo, user_repo

    monkeypatch.setattr(community_repo, "_db_path", lambda: str(tmp_path / "c.db"))
    monkeypatch.setattr(team_repo, "_db_path", lambda: str(tmp_path / "t.db"))
    monkeypatch.setattr(user_repo, "_db_path", lambda: str(tmp_path / "i.db"))
    for mod in (community_repo, team_repo, user_repo):
        mod.reset_connection()
    yield community_repo
    for mod in (community_repo, team_repo, user_repo):
        mod.reset_connection()


@pytest.fixture()
def service(repos):
    from app.services.community_service import CommunityService

    return CommunityService(settings=None)


@pytest.fixture()
def team_id(service):
    from app.services.team_service import TeamService

    team = TeamService(settings=None).create_team(
        name="研发组", description="", owner="alice"
    )
    return team["id"]


def test_draft_only_visible_via_mine(service, team_id):
    draft = service.create_project(
        team_id, "alice", name="内部草稿", summary="未发布"
    )
    assert draft["visibility"] == "draft"

    # 默认视图（社区广场）不含草稿
    assert service.list_projects("alice") == []

    # mine 视图含草稿
    mine = service.list_projects("alice", mine=True)
    assert [p["id"] for p in mine] == [draft["id"]]


def test_mine_includes_published(service, team_id):
    project = service.create_project(team_id, "alice", name="开源项目")
    service.publish_project(project["id"], "alice")

    assert len(service.list_projects("alice")) == 1
    assert len(service.list_projects("alice", mine=True)) == 1


def test_mine_is_scoped_to_me(service, team_id):
    """别人的草稿不能出现在我的 mine 里。"""
    service.create_project(team_id, "alice", name="alice 的草稿")
    assert service.list_projects("bob", mine=True) == []


def test_mine_flags_are_not_leaked_for_draft(service, team_id):
    """草稿不参与 star/watch，装饰字段必须为 False 而不是脏数据。"""
    project = service.create_project(team_id, "alice", name="草稿")
    item = service.list_projects("alice", mine=True)[0]
    assert item["is_starred"] is False
    assert item["is_watching"] is False
    assert item["id"] == project["id"]
