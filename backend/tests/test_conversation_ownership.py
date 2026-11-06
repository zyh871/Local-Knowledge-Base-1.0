"""会话归属契约。

修复前的实际行为（都被实测确认过）：
  * `verify_owner` 对**不存在**的 id 直接 `return True`，于是
    `PUT /conversations/<任意 id>` 返回 200 并凭空建库；
  * `/ask` 写侧完全不校验归属，可以往别人的会话里塞消息；
  * 缺省 `conversation_id` 回落成全局字面量 `"default"`，
    所有"没传 id"的请求共用同一行历史；
  * 导出对不存在的 id 返回 200 + 空壳。

这里把它们钉住。
"""

from __future__ import annotations

import pytest


def _headers(sub: str, username: str) -> dict[str, str]:
    from app.core.security import create_access_token

    return {
        "Authorization": f"Bearer {create_access_token({'sub': sub, 'username': username})}"
    }


@pytest.fixture()
def users():
    """保证库里有 admin 与一个普通成员 bob。"""
    from app.services import identity_service
    from app.repositories import user_repo

    identity_service.bootstrap_identity()
    if user_repo.get_by_username("bob") is None:
        user_repo.create_user(
            user_id="usr_bob",
            username="bob",
            password_hash="x",
            display_name="Bob",
            global_role="user",
        )
    return {
        "admin": _headers("usr_admin", "admin"),
        "bob": _headers("usr_bob", "bob"),
    }


def test_ghost_put_creates_and_claims(client, users):
    """PUT 一个不存在的 id 允许创建（前端首次命名），并认领归属。"""
    resp = client.put(
        "/conversations/conv-claim-1", json={"title": "我的会话"}, headers=users["admin"]
    )
    assert resp.status_code == 200
    assert resp.json()["conversation"]["username"] == "admin"


def test_other_user_cannot_read_update_delete(client, users):
    client.put("/conversations/conv-owned", json={"title": "admin 的"}, headers=users["admin"])

    assert client.get("/conversations/conv-owned", headers=users["bob"]).status_code == 403
    assert (
        client.put(
            "/conversations/conv-owned", json={"title": "劫持"}, headers=users["bob"]
        ).status_code
        == 403
    )
    assert client.post("/conversations/conv-owned/export", headers=users["bob"]).status_code == 403
    assert client.delete("/conversations/conv-owned", headers=users["bob"]).status_code == 403
    # 原主数据未被动过
    body = client.get("/conversations/conv-owned", headers=users["admin"]).json()
    assert body["conversation"]["title"] == "admin 的"


def test_ghost_export_returns_404(client, users):
    """导出不存在的会话必须失败，不能 200 + 空壳。

    归属校验在存在性校验之前（fail-closed），所以这里拿到的是 403 而不是
    404 —— 两种都可以接受，**唯一不可接受的是 200**。前者还顺带避免了
    "用 404/403 的差异枚举他人会话 id"。
    """
    resp = client.post("/conversations/conv-does-not-exist/export", headers=users["admin"])
    assert resp.status_code in (403, 404), resp.text


def test_ask_rejects_writing_to_others_conversation(client, users):
    """写侧闸门：往别人的会话提问 -> 403。"""
    client.put("/conversations/conv-secret", json={"title": "admin 的"}, headers=users["admin"])

    resp = client.post(
        "/ask",
        json={"question": "你好", "conversation_id": "conv-secret"},
        headers=users["bob"],
    )
    assert resp.status_code == 403


def test_default_conversation_id_is_per_user():
    """缺省 id 按用户派生，不能是共享字面量。"""
    from app.services.qa_service import QAService

    assert QAService.resolve_conversation_id("", "alice") == "default:alice"
    assert QAService.resolve_conversation_id(None, "bob") == "default:bob"
    assert QAService.resolve_conversation_id("conv-1", "alice") == "conv-1"


def test_search_total_does_not_depend_on_page_size(client, users):
    """搜索的 total 必须与 page_size 无关（过滤要下推到 SQL）。"""
    for i in range(3):
        client.put(
            f"/conversations/conv-search-{i}",
            json={"title": f"RAG 笔记 {i}"},
            headers=users["admin"],
        )

    narrow = client.get(
        "/conversations", params={"search": "RAG", "limit": 1}, headers=users["admin"]
    ).json()
    wide = client.get(
        "/conversations", params={"search": "RAG", "limit": 100}, headers=users["admin"]
    ).json()

    assert narrow["total"] == wide["total"] >= 3
    assert len(narrow["conversations"]) == 1


def test_probe_scope_is_separate_from_global():
    """探针与业务必须**分桶**，否则监控会把业务打成 429。

    分桶靠的是计数键里的 scope，而不是两套不同的配额值：
    修复前 `system.router`（/health、/stats）和业务端点都挂在 `scope="global"`
    上，于是探针 60 次就能把业务的桶吃光（实测同窗口其余端点 300/300 全 429）。
    """
    from app.core.redis import get_keys

    keys = get_keys()
    assert keys.ratelimit("probe", "u:usr_admin", 60) != keys.ratelimit(
        "global", "u:usr_admin", 60
    )


def test_system_router_does_not_share_global_bucket():
    """结构护栏：system.router 不能挂在 global 桶上。"""
    import inspect

    from app.api.v1 import router as v1_router

    source = inspect.getsource(v1_router)
    assert 'system.router, tags=["系统"], dependencies=_probe_limit' in source
    assert 'system.router, tags=["系统"], dependencies=_global_limit' not in source


def test_health_does_not_leak_scale(client, users):
    """匿名探针不暴露语料规模。"""
    body = client.get("/health").json()
    assert "chunks" not in (body.get("chroma") or {})
    assert "conversations" not in (body.get("sqlite_conversations") or {})
