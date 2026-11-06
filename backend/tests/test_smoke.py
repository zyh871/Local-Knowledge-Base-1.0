"""冒烟集：部署后 90 秒内给出「系统是否可测」的二值结论。

为什么单独需要这一层：
  * 仓库原有 900+ 条用例，但没有"每次改动都要跑的那几条"；
  * 每跑一次全量都要几分钟，于是大家干脆不跑 —— 回归无从谈起；
  * 探针与业务还会互相挤占限流配额（已修：探针独立 scope）。

用法：
    pytest -m smoke -q
    pytest -m smoke -q --no-header -x        # 更快的失败退出

判据：5 条全绿 + 总耗时 < 90s 即视为「系统可测」。
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.smoke


def test_health_ok(client):
    """依赖就绪：健康检查 200 且关键子系统已初始化。"""
    resp = client.get("/health")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body.get("status") in ("ok", "degraded"), body
    assert "chroma" in body, body
    assert "redis" in body, body


def test_login_works(client):
    """鉴权与用户库可写：能签发令牌。"""
    import os

    resp = client.post(
        "/token",
        data={
            "username": os.environ["ADMIN_USERNAME"],
            "password": os.environ["ADMIN_PASSWORD"],
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json().get("access_token")


def test_conversations_readable(client, auth_headers):
    """只读库通路：会话列表可读且结构完整。"""
    resp = client.get("/conversations", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "conversations" in body
    assert isinstance(body.get("total"), int)


def test_model_catalog_available(client, auth_headers):
    """模型注册表没崩（不触发任何真实模型调用）。"""
    resp = client.get("/models/catalog", headers=auth_headers)
    assert resp.status_code == 200, resp.text


def test_sse_contract(client, auth_headers):
    """SSE 主链路通：intent → chunk* → final，且零外部依赖。"""
    with client.stream(
        "POST",
        "/ask/stream",
        json={"question": "冒烟：自我介绍", "conversation_id": "smoke-conv"},
        headers=auth_headers,
    ) as resp:
        assert resp.status_code == 200, resp.text
        types: list[str] = []
        for line in resp.iter_lines():
            if not line or not line.startswith("data:"):
                continue
            import json

            payload = json.loads(line[5:].strip())
            if payload.get("type"):
                types.append(payload["type"])
            if payload.get("type") == "final":
                break

    assert types, "SSE 未收到任何事件"
    assert types[0] == "intent", types
    assert types[-1] == "final", types
    assert types.count("final") == 1, types
