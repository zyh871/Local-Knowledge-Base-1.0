"""
真实 Redis 实例验证（P4 的收尾）。

⚠️ 这些用例**必须打到真实 Redis**才有效 —— 降级到 fakeredis 时全部跳过。
原因：进程内降级掩盖了本方案要解决的核心问题（多 worker 下
限流/锁/缓存/会话状态各自为政）。用 fakeredis 测"分布式"语义是自欺欺人。

运行前提：
    docker compose up -d redis        # 或任意可达的 Redis
    REDIS_URL=redis://localhost:6379/0

覆盖：
  1. 连接与 Redis Stack 模块（RediSearch 是 checkpoint 模式的前置）
  2. 限流计数落在 Redis（跨客户端可见 + TTL 正确）
  3. 分布式锁真实互斥（模拟两个 worker）
  4. 会话消息缓存跨进程可见（多 worker 一致性）
  5. 分享链接 TTL
  6. 检索/嵌入缓存与主动失效
  7. Redis checkpointer 端到端（建索引 + 图执行 + 线程隔离）
"""

from __future__ import annotations

import asyncio
import time
import uuid

import pytest

from app.core.config import get_redis_settings


# ---------------------------------------------------------------------------
#  前置：探测真实 Redis
# ---------------------------------------------------------------------------


def _probe_real_redis() -> tuple[bool, str]:
    """返回 (是否可用, 说明)。刻意不用 fakeredis —— 那正是本文件要排除的。"""
    try:
        from redis import Redis as SyncRedis

        client = SyncRedis.from_url(
            get_redis_settings().url,
            decode_responses=True,
            socket_connect_timeout=2,
            socket_timeout=2,
        )
        client.ping()
        version = client.info("server").get("redis_version", "unknown")
        modules = client.module_list() if hasattr(client, "module_list") else []
        names = [m.get("name") for m in modules] if modules else []
        has_search = "search" in names
        client.close()
        note = f"Redis {version} modules={names or 'none'}"
        if not has_search:
            note += "（缺 RediSearch，Redis Stack 相关用例将跳过）"
        return True, note
    except Exception as exc:  # noqa: BLE001
        return False, f"Redis 不可达: {exc}"


REAL_REDIS, REDIS_NOTE = _probe_real_redis()

pytestmark = pytest.mark.skipif(
    not REAL_REDIS,
    reason=f"需要真实 Redis 实例（{REDIS_NOTE}）。启动：docker compose up -d redis",
)


def _has_redisearch() -> bool:
    return "RediSearch" not in REDIS_NOTE and "缺 RediSearch" not in REDIS_NOTE


# ⚠️ 必须共用**同一个**事件循环。
# 文档级 fixture 里创建的 async Redis 客户端绑定在该循环上；
# 如果每个用例都 `asyncio.run()`（= 每次新建并关闭一个循环），
# 第二个用例就会报 `RuntimeError: Event loop is closed`。
_LOOP: asyncio.AbstractEventLoop | None = None


def _shared_loop() -> asyncio.AbstractEventLoop:
    global _LOOP
    if _LOOP is None or _LOOP.is_closed():
        _LOOP = asyncio.new_event_loop()
    return _LOOP


def run(coro):
    return _shared_loop().run_until_complete(coro)


@pytest.fixture(scope="module", autouse=True)
def _close_shared_loop():
    """在 manager 关闭之后（逆向 teardown）再关循环。"""
    yield
    global _LOOP
    if _LOOP is not None and not _LOOP.is_closed():
        _LOOP.close()
    _LOOP = None


@pytest.fixture(scope="module")
def manager():
    """真实 RedisManager（非降级）。"""
    from app.core.redis import RedisManager

    mgr = RedisManager()
    run(mgr.connect())
    assert not mgr.is_fallback, f"预期连接真实 Redis，实际降级了：{mgr.state.error}"
    yield mgr
    run(mgr.close())


@pytest.fixture
def ns():
    """每个用例独立的键命名空间，避免相互污染。"""
    return f"test_{uuid.uuid4().hex[:8]}"


# ---------------------------------------------------------------------------
#  1. 连接
# ---------------------------------------------------------------------------


class TestLiveConnection:
    def test_not_in_fallback_mode(self, manager):
        assert manager.is_fallback is False

    def test_sync_client_also_real(self, manager):
        """同步客户端必须也连的是真实 Redis，否则缓存与限流会不一致。"""
        pong = manager.sync_client.ping()
        assert pong is True

    def test_async_and_sync_share_data(self, manager):
        key = f"kbqa:test:shared:{uuid.uuid4().hex[:8]}"
        manager.sync_client.set(key, "from-sync")
        try:
            assert run(manager.client.get(key)) == "from-sync"
        finally:
            manager.sync_client.delete(key)

    def test_health_reports_redis_mode(self, manager):
        health = run(manager.health())
        assert health["ok"] is True
        assert health["mode"] == "redis"
        assert health["version"] != "unknown"


# ---------------------------------------------------------------------------
#  2. 限流
# ---------------------------------------------------------------------------


class TestLiveRateLimit:
    def test_counter_persists_in_redis(self, manager, ns):
        from app.core.redis import check_rate_limit, get_keys

        async def _run():
            return [
                await check_rate_limit(
                    manager.client, scope=ns, identity="u1", limit=3, window=60
                )
                for _ in range(4)
            ]

        results = run(_run())
        assert [r.allowed for r in results] == [True, True, True, False]

        # 键必须真的落在 Redis 里
        stored = manager.sync_client.get(get_keys().ratelimit(ns, "u1", 60))
        assert stored == "4"
        ttl = manager.sync_client.ttl(get_keys().ratelimit(ns, "u1", 60))
        assert 0 < ttl <= 60

        manager.sync_client.delete(get_keys().ratelimit(ns, "u1", 60))

    def test_two_workers_share_the_same_counter(self, manager, ns):
        """★ 这是引入 Redis 的核心目的：多 worker 下限额才是真的。"""
        from redis.asyncio import Redis

        from app.core.redis import check_rate_limit

        async def _run():
            # 用两个独立连接模拟两个 worker
            w1 = Redis.from_url(get_redis_settings().url, decode_responses=True)
            w2 = Redis.from_url(get_redis_settings().url, decode_responses=True)
            try:
                r1 = await check_rate_limit(w1, scope=ns, identity="shared", limit=2, window=60)
                r2 = await check_rate_limit(w2, scope=ns, identity="shared", limit=2, window=60)
                r3 = await check_rate_limit(w1, scope=ns, identity="shared", limit=2, window=60)
                return r1, r2, r3
            finally:
                await w1.aclose()
                await w2.aclose()

        r1, r2, r3 = run(_run())
        assert r1.allowed is True
        assert r2.remaining == 0, "第二个 worker 没看到第一个 worker 的计数"
        assert r3.allowed is False, "跨 worker 限额失效"

        from app.core.redis import get_keys

        manager.sync_client.delete(get_keys().ratelimit(ns, "shared", 60))


# ---------------------------------------------------------------------------
#  3. 分布式锁
# ---------------------------------------------------------------------------


class TestLiveDistributedLock:
    def test_mutual_exclusion_across_connections(self, manager, ns):
        """两个独立连接（模拟两个 worker）不能同时持锁。"""
        from redis.asyncio import Redis

        from app.core.redis import LockNotAcquired, distributed_lock

        async def _run():
            w1 = Redis.from_url(get_redis_settings().url, decode_responses=True)
            w2 = Redis.from_url(get_redis_settings().url, decode_responses=True)
            try:
                async with distributed_lock(w1, ns, ttl=30):
                    try:
                        async with distributed_lock(w2, ns, ttl=30):
                            return "w2-acquired"
                    except LockNotAcquired:
                        return "w2-blocked"
            finally:
                await w1.aclose()
                await w2.aclose()

        assert run(_run()) == "w2-blocked"

    def test_lock_released_on_exit(self, manager, ns):
        from app.core.redis import distributed_lock, get_keys

        async def _run():
            async with distributed_lock(manager.client, ns, ttl=30):
                pass
            return await manager.client.get(get_keys().lock(ns))

        assert run(_run()) is None

    def test_lock_key_has_ttl_no_deadlock(self, manager, ns):
        """锁必须带 TTL —— 否则持锁进程崩溃会导致永久死锁。"""
        from app.core.redis import get_keys

        async def _run():
            from app.core.redis import distributed_lock

            async with distributed_lock(manager.client, ns, ttl=30):
                return await manager.client.ttl(get_keys().lock(ns))

        ttl = run(_run())
        assert 0 < ttl <= 30


# ---------------------------------------------------------------------------
#  4. 会话消息缓存（多 worker 一致性）
# ---------------------------------------------------------------------------


class TestLiveSessionCache:
    def test_history_visible_across_workers(self, manager):
        """★ 一个"worker"写入，另一个"worker"必须能读到。"""
        from app.memory import conversation_memory as cm
        from app.repositories import session_cache
        from app.core.redis import bind_sync_client, unbind_sync_client

        cid = f"live_{uuid.uuid4().hex[:8]}"
        cm.upsert_conversation(cid, username="live_u", title="t")

        # worker A
        bind_sync_client(manager.sync_client)
        cm.save_context(cid, "跨进程问题", "跨进程回答")
        unbind_sync_client()

        # worker B：换一个独立连接，缓存里应已有数据
        from redis import Redis as SyncRedis

        other = SyncRedis.from_url(get_redis_settings().url, decode_responses=True)
        bind_sync_client(other)
        try:
            history = cm.load_history(cid)
            assert [m["content"] for m in history] == ["跨进程问题", "跨进程回答"]
            # 且这份数据来自缓存（不是回源 SQLite 后现填的）
            assert session_cache.get_cached_messages(cid) is not None
        finally:
            unbind_sync_client()
            other.close()
            cm.delete_conversation(cid)

    def test_cache_survives_process_local_loss(self, manager):
        """进程内状态清空后（模拟重启/换 worker），历史仍可从真相源恢复。"""
        from app.memory import conversation_memory as cm
        from app.core.redis import bind_sync_client, unbind_sync_client, get_keys

        cid = f"live2_{uuid.uuid4().hex[:8]}"
        cm.upsert_conversation(cid, username="live_u", title="t")
        bind_sync_client(manager.sync_client)
        try:
            cm.save_context(cid, "q1", "a1")
            cm.save_context(cid, "q2", "a2")
            # 清缓存 → 应回源 SQLite
            manager.sync_client.delete(get_keys().session_msgs(cid))
            assert [m["content"] for m in cm.load_history(cid)] == ["q1", "a1", "q2", "a2"]
        finally:
            unbind_sync_client()
            cm.delete_conversation(cid)

    def test_session_cache_ttl_matches_config(self, manager):
        from app.core.redis import bind_sync_client, unbind_sync_client, get_keys
        from app.memory import conversation_memory as cm

        cid = f"live3_{uuid.uuid4().hex[:8]}"
        cm.upsert_conversation(cid, username="live_u", title="t")
        bind_sync_client(manager.sync_client)
        try:
            cm.save_context(cid, "q", "a")
            ttl = manager.sync_client.ttl(get_keys().session_msgs(cid))
        finally:
            unbind_sync_client()
            cm.delete_conversation(cid)
        expected = get_redis_settings().ttl_session
        assert 0 < ttl <= expected


# ---------------------------------------------------------------------------
#  5. 分享链接
# ---------------------------------------------------------------------------


class TestLiveShare:
    def test_share_roundtrip_and_ttl(self, manager):
        from app.services.share_service import ShareService

        async def _run():
            svc = ShareService(settings=_settings(), redis=manager.client)
            created = await svc.create(
                conversation_id="live-share",
                username="live_u",
                title="测试分享",
                payload={"history": [{"role": "user", "content": "hi"}]},
                messages=None,
                ttl_hours=1,
            )
            fetched = await svc.get(created["token"])
            return created, fetched

        created, fetched = run(_run())
        assert fetched["title"] == "测试分享"

        from app.core.redis import get_keys

        ttl = manager.sync_client.ttl(get_keys().share(created["token"]))
        # 名义 1 小时 + 1 小时宽限（用于区分 404 与 410）
        assert 3600 < ttl <= 7200
        manager.sync_client.delete(get_keys().share(created["token"]))

    def test_expired_share_returns_gone_not_notfound(self, manager):
        """过期必须是 410 而不是 404 —— 靠宽限 TTL 保留记录实现。"""
        from app.core.exceptions import GoneError
        from app.core.redis import cache_set_json, get_keys
        from app.services.share_service import ShareService

        token = f"liveexpired{uuid.uuid4().hex[:8]}"

        async def _run():
            await cache_set_json(
                manager.client,
                get_keys().share(token),
                {"token": token, "expires_at": time.time() - 10, "data": {}},
                ttl=600,
            )
            svc = ShareService(settings=_settings(), redis=manager.client)
            try:
                await svc.get(token)
                return None
            except GoneError:
                return "gone"

        assert run(_run()) == "gone"


def _settings():
    from app.core.config import get_settings

    return get_settings()


# ---------------------------------------------------------------------------
#  6. 缓存与主动失效
# ---------------------------------------------------------------------------


class TestLiveCache:
    def test_retrieval_cache_invalidation(self, manager, ns):
        from app.core.redis import bind_sync_client, get_keys, sync_cache_set_json, unbind_sync_client
        from app.rag.vectorstore import invalidate_retrieval_cache

        bind_sync_client(manager.sync_client)
        try:
            keys = get_keys()
            for i in range(3):
                sync_cache_set_json(keys.cache_retrieval(f"q{i}", 3), [{"i": i}], 300)
            assert manager.sync_client.exists(keys.cache_retrieval("q0", 3)) == 1

            removed = invalidate_retrieval_cache()
            assert removed >= 3
            assert manager.sync_client.exists(keys.cache_retrieval("q0", 3)) == 0
        finally:
            unbind_sync_client()

    def test_embedding_cache_reduces_calls(self, manager):
        """嵌入缓存必须真的减少对模型的调用次数。"""
        from app.core.redis import bind_sync_client, unbind_sync_client, get_keys
        from app.rag.embeddings import CachedEmbeddings

        class CountingEmbeddings:
            def __init__(self):
                self.calls = 0

            def embed_query(self, text):
                self.calls += 1
                return [0.1, 0.2, 0.3]

            def embed_documents(self, texts):
                self.calls += len(texts)
                return [[0.1, 0.2, 0.3] for _ in texts]

        inner = CountingEmbeddings()
        text = f"嵌入缓存测试_{uuid.uuid4().hex[:8]}"

        bind_sync_client(manager.sync_client)
        try:
            wrapped = CachedEmbeddings(inner)
            first = wrapped.embed_query(text)
            second = wrapped.embed_query(text)
            assert first == second == [0.1, 0.2, 0.3]
            assert inner.calls == 1, f"第二次命中缓存却仍调用了模型（calls={inner.calls}）"
            assert wrapped.hits >= 1
            assert manager.sync_client.exists(get_keys().cache_embedding(text)) == 1
        finally:
            unbind_sync_client()

    def test_embedding_cache_handles_partial_hits(self, manager):
        from app.core.redis import bind_sync_client, unbind_sync_client

        from app.rag.embeddings import CachedEmbeddings

        class CountingEmbeddings:
            def __init__(self):
                self.calls = 0

            def embed_documents(self, texts):
                self.calls += len(texts)
                return [[float(len(t))] for t in texts]

            def embed_query(self, text):
                return [float(len(text))]

        inner = CountingEmbeddings()
        tag = uuid.uuid4().hex[:8]
        texts = [f"a_{tag}", f"b_{tag}", f"c_{tag}"]

        bind_sync_client(manager.sync_client)
        try:
            wrapped = CachedEmbeddings(inner)
            wrapped.embed_documents(texts[:2])
            assert inner.calls == 2
            # 混合：2 个命中 + 1 个未命中
            result = wrapped.embed_documents([texts[0], texts[1], texts[2]])
            assert len(result) == 3
            assert inner.calls == 3, f"命中项不该再调模型（calls={inner.calls}）"
        finally:
            unbind_sync_client()


# ---------------------------------------------------------------------------
#  7. Redis checkpointer（端到端）
# ---------------------------------------------------------------------------


class TestLiveCheckpointer:
    def test_redis_saver_available(self):
        from langgraph.checkpoint.redis.aio import AsyncRedisSaver  # noqa: F401

    def test_make_checkpointer_returns_redis_saver(self):
        """走产品代码路径：make_checkpointer 必须真的返回 Redis saver。

        回归保护：曾把它写成 `AsyncRedisSaver(redis_client)`（位置参数是
        `redis_url: str`），真实 Redis 下会抛 AttributeError 而静默降级为内存。
        """
        from langgraph.checkpoint.redis.aio import AsyncRedisSaver

        from app.core.config import GraphSettings, get_redis_settings
        from app.graph.checkpointer import make_checkpointer

        class _S:
            # 桩必须同时提供 graph 与 redis 两个分组 ——
            # make_checkpointer 会用 settings.redis.url 构造 saver
            graph = GraphSettings(_env_file=None, checkpointer="redis")
            redis = get_redis_settings()

        saver = make_checkpointer(_S(), redis_client=None)
        assert isinstance(saver, AsyncRedisSaver), (
            "GRAPH_CHECKPOINTER=redis 未生效，悄悄降级成了 "
            f"{type(saver).__name__}"
        )

    async def _compile_with_redis(self, manager):
        """构建并用 Redis saver 编译图。

        必须是 async —— 因为它会在 `run(_run())` 的事件循环**内部**被调用，
        此时不能再 `run()`（会报 `This event loop is already running`）。
        """
        from langgraph.checkpoint.redis.aio import AsyncRedisSaver

        from app.core.config import get_settings
        from app.graph.build import build_qa_graph
        from app.rag import get_rag_chain

        saver = AsyncRedisSaver(redis_url=get_redis_settings().url)
        await saver.asetup()
        graph = build_qa_graph(
            settings=get_settings(), chain=get_rag_chain(), redis=None
        )
        return graph.builder.compile(checkpointer=saver)

    def test_setup_and_graph_run(self, manager):
        """用 Redis checkpointer 跑一次真实图执行。"""

        async def _run():
            graph = await self._compile_with_redis(manager)
            state_in = {
                "question": "那个",
                "conversation_id": f"ckpt-{uuid.uuid4().hex[:8]}",
                "username": "ckpt_u",
                "force_search": False,
                "trace_id": "t",
            }
            cfg = {
                "configurable": {"thread_id": f"ckpt_u:{state_in['conversation_id']}"},
                "recursion_limit": 25,
            }
            result = await graph.ainvoke(state_in, config=cfg)
            snapshot = await graph.aget_state(cfg)
            return result, snapshot

        result, snapshot = run(_run())
        assert result["intent"] == "clarification_needed"
        assert snapshot.values.get("question") == "那个"
        assert snapshot.next == ()  # 已跑完，无待执行节点

    def test_checkpoint_persisted_in_redis(self, manager):
        """checkpoint 必须真的落到 Redis（而不是留在进程内存里）。"""

        async def _run():
            graph = await self._compile_with_redis(manager)
            thread = f"ckpt_persist_{uuid.uuid4().hex[:8]}"
            await graph.ainvoke(
                {
                    "question": "那个",
                    "conversation_id": "x",
                    "username": "u",
                    "force_search": False,
                    "trace_id": "t",
                },
                config={
                    "configurable": {"thread_id": thread},
                    "recursion_limit": 25,
                },
            )
            return thread

        thread = run(_run())
        # checkpoint 键由 saver 管理，用 SCAN 找该线程的痕迹
        found = 0
        for pattern in (f"*{thread}*", "checkpoint*"):
            cursor = 0
            while True:
                cursor, keys = manager.sync_client.scan(
                    cursor=cursor, match=pattern, count=500
                )
                found += len(keys)
                if cursor == 0:
                    break
            if found:
                break
        assert found > 0, "Redis 中找不到该 thread 的 checkpoint 数据"

    def test_threads_are_isolated(self, manager):
        """不同 thread_id 的 checkpoint 必须互不可见。"""

        async def _run():
            graph = await self._compile_with_redis(manager)
            base = {
                "conversation_id": "x",
                "username": "u",
                "force_search": False,
                "trace_id": "t",
            }

            def cfg(thread):
                return {
                    "configurable": {"thread_id": thread},
                    "recursion_limit": 25,
                }

            tag = uuid.uuid4().hex[:8]
            await graph.ainvoke(
                {**base, "question": f"线程A-{tag}"}, config=cfg(f"tA-{tag}")
            )
            await graph.ainvoke(
                {**base, "question": f"线程B-{tag}"}, config=cfg(f"tB-{tag}")
            )
            a = await graph.aget_state(cfg(f"tA-{tag}"))
            b = await graph.aget_state(cfg(f"tB-{tag}"))
            return a.values.get("question"), b.values.get("question")

        qa, qb = run(_run())
        assert qa.startswith("线程A-")
        assert qb.startswith("线程B-")
        assert qa != qb


class TestRedisStackModules:
    @pytest.mark.skipif(
        not _has_redisearch(), reason="需要 Redis Stack（RediSearch 模块）"
    )
    def test_search_module_present(self, manager):
        """RediSearch 是 Redis checkpoint 模式的前置条件。"""
        modules = manager.sync_client.module_list()
        assert "search" in [m.get("name") for m in modules]
