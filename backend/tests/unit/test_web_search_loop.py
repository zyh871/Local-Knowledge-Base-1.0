"""联网搜索的事件循环回归测试。

背景（真实故障）：工作流里工具是**同步**执行的（`registry.execute`），
`search()` 当时用 `asyncio.run()` 包了一层——每调一次就新建并关闭一个事件循环。
而 `httpx.AsyncClient` 的连接池绑定在创建它的循环上，第二次调用复用了
连着死循环的客户端，于是抛 `RuntimeError: Event loop is closed`。

表现：工作流第一次联网搜索能成功，之后每次必失败；设置页的「测试连接」也跟着挂。

这两个用例分别锁住修复的两个点：
  1. 同步调用共享同一个常驻循环（不再每次新建）
  2. 循环变了要重建客户端（不能拿着旧循环的池子继续用）
"""

import asyncio

from app.tools import web_search as ws


def test_sync_search_shares_one_loop(monkeypatch):
    """连续两次同步调用必须跑在同一个、且仍然打开的循环上。"""
    seen: list[asyncio.AbstractEventLoop] = []

    async def fake_search_async(query, max_results=5, timeout=15, search_depth="basic"):
        seen.append(asyncio.get_running_loop())
        return {"success": True, "results": [], "query": query, "engines_used": []}

    monkeypatch.setattr(ws, "search_async", fake_search_async)

    first = ws.search("第一个问题")
    second = ws.search("第二个问题")

    assert first["success"] and second["success"]
    assert len(seen) == 2
    assert seen[0] is seen[1], "两次同步搜索用了不同的循环，说明又在每次 asyncio.run"
    assert not seen[0].is_closed(), "循环被关掉了，下次调用必然 Event loop is closed"

    ws.stop_sync_loop()


def test_client_rebuilt_when_loop_changes():
    """换了循环就必须换客户端；否则旧连接池会把请求打成 Event loop is closed。"""

    async def get_client():
        return await ws._get_client()

    first = asyncio.run(get_client())
    second = asyncio.run(get_client())

    assert first is not second, "跨循环复用了同一个 AsyncClient"
    assert second is not None

    ws.stop_sync_loop()


def test_sync_search_inside_running_loop_reports_clearly():
    """在事件循环里误用同步接口时，要返回明确错误而不是死锁。"""

    async def misuse():
        return ws.search("在协程里调同步接口")

    result = asyncio.run(misuse())
    assert result["success"] is False
    assert "事件循环" in result["error"]
