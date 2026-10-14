"""
基础设施层测试：配置、Redis 键规范、TTL、限流、分布式锁、缓存。

两种运行模式：
  * Redis 可达   → 打到**真实实例**（推荐，能覆盖 loop 绑定、跨连接共享等真实语义）
  * Redis 不可达 → 走 `RedisManager` 的进程内降级路径

⚠️ 两个必须遵守的测试纪律（都是被真实 Redis 打脸后才加上的）：

1. **共用同一个事件循环**。`redis.asyncio` 的客户端绑定在创建它的事件循环上；
   如果每个用例 `asyncio.run()`（= 每次新建并关闭一个循环），
   第二个用例就会报 `RuntimeError: Event loop is closed`。
2. **键必须按运行随机命名**。真实 Redis 是持久化的，
   上一轮测试遗留的限流计数会让这一轮断言莫名失败
   （表现为 `[True,True,True,True,False]` 而不是 `[True,True,True,False,False]`）。
   用 fakeredis 时进程结束即清空，这个坑根本不会出现。
"""

from __future__ import annotations

import asyncio
import uuid

import pytest

from app.core.config import RedisSettings
from app.core.redis import (
    LockNotAcquired,
    RedisKeys,
    RedisManager,
    cache_delete_pattern,
    cache_get_json,
    cache_set_json,
    check_rate_limit,
    digest,
    distributed_lock,
)


def _uid() -> str:
    """每次调用都不同的键后缀，保证跨运行隔离。"""
    return uuid.uuid4().hex[:10]


# ---------------------------------------------------------------------------
#  共用事件循环
# ---------------------------------------------------------------------------

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
    yield
    global _LOOP
    if _LOOP is not None and not _LOOP.is_closed():
        _LOOP.close()
    _LOOP = None


# ---------------------------------------------------------------------------
#  fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def redis_client():
    """按当前环境连接：真实 Redis 可用则用真实，否则降级为进程内实现。"""

    async def _connect():
        manager = RedisManager()
        await manager.connect()
        return manager

    manager = run(_connect())
    yield manager
    run(manager.close())


@pytest.fixture(scope="module")
def unreachable_manager():
    """指向必然不可达的地址，稳定复现降级路径（与本机是否开 Redis 无关）。"""
    cfg = RedisSettings(
        _env_file=None,
        url="redis://127.0.0.1:1/0",
        fallback_to_memory=True,
        connect_retries=0,
        socket_timeout=1,
    )
    manager = RedisManager(cfg)
    run(manager.connect())
    yield manager
    run(manager.close())


# ---------------------------------------------------------------------------
#  键规范
# ---------------------------------------------------------------------------


class TestRedisKeys:
    """键命名规范：{prefix}:{domain}:{entity}[:{id}][:{sub}]"""

    def test_prefix_is_applied(self):
        keys = RedisKeys(prefix="kbqa")
        assert keys.session_msgs("c1").startswith("kbqa:")
        assert keys.share("tok") == "kbqa:share:tok"

    def test_key_shape(self):
        keys = RedisKeys(prefix="kbqa")
        assert keys.session_msgs("c1") == "kbqa:session:c1:msgs"
        assert keys.session_meta("c1") == "kbqa:session:c1:meta"
        assert keys.lock("vectorstore:rebuild") == "kbqa:lock:vectorstore:rebuild"
        assert keys.ckpt_last("c1") == "kbqa:ckpt:last:c1"
        assert keys.stream_ingest() == "kbqa:stream:ingest"

    def test_user_input_never_appears_in_key(self):
        """用户提问原文绝不能进键空间（用摘要代替）。"""
        keys = RedisKeys(prefix="kbqa")
        question = "什么是 RAG？请用中文详细解释"
        key = keys.cache_retrieval(question, 3)
        assert question not in key
        assert digest(question) in key

    def test_digest_is_stable_and_bounded(self):
        assert digest("abc") == digest("abc")
        assert digest("abc") != digest("abd")
        assert len(digest("x" * 10000)) == 16

    def test_ratelimit_key_is_scoped_by_window(self):
        keys = RedisKeys(prefix="kbqa")
        assert keys.ratelimit("ask", "u:admin", 60) != keys.ratelimit("ask", "u:admin", 30)
        assert keys.ratelimit("ask", "u:admin", 60) != keys.ratelimit("upload", "u:admin", 60)


# ---------------------------------------------------------------------------
#  限流
# ---------------------------------------------------------------------------


class TestRateLimit:
    def test_allows_under_limit_and_blocks_over(self, redis_client):
        scope, identity = f"scope_{_uid()}", f"id_{_uid()}"

        async def _run():
            return [
                await check_rate_limit(
                    redis_client.client,
                    scope=scope,
                    identity=identity,
                    limit=3,
                    window=60,
                )
                for _ in range(5)
            ]

        results = run(_run())
        assert [r.allowed for r in results] == [True, True, True, False, False]
        assert results[0].limit == 3
        assert results[0].remaining == 2
        assert results[-1].remaining == 0
        assert results[3].retry_after >= 1

    def test_ttl_is_set_on_counter(self, redis_client):
        from app.core.redis import get_keys

        scope, identity = f"scope_{_uid()}", f"id_{_uid()}"

        async def _run():
            await check_rate_limit(
                redis_client.client,
                scope=scope,
                identity=identity,
                limit=10,
                window=45,
            )
            return await redis_client.client.ttl(
                get_keys().ratelimit(scope, identity, 45)
            )

        assert 0 < run(_run()) <= 45

    def test_counter_resets_after_window(self, redis_client):
        """窗口极短时，过期后应重新放行（而不是被永久封禁）。"""
        scope, identity = f"scope_{_uid()}", f"id_{_uid()}"

        async def _run():
            first = await check_rate_limit(
                redis_client.client, scope=scope, identity=identity, limit=1, window=1
            )
            await asyncio.sleep(1.3)
            second = await check_rate_limit(
                redis_client.client, scope=scope, identity=identity, limit=1, window=1
            )
            return first, second

        first, second = run(_run())
        assert first.allowed is True
        assert second.allowed is True, "窗口过期后未重置计数"

    def test_fails_open_when_backend_unavailable(self):
        """限流后端故障时必须放行 —— 不能让基础设施问题阻断业务。"""

        class Broken:
            async def incr(self, *a, **k):
                raise RuntimeError("boom")

        async def _run():
            return await check_rate_limit(
                Broken(), scope="s", identity="i", limit=1, window=60
            )

        assert run(_run()).allowed is True


# ---------------------------------------------------------------------------
#  分布式锁
# ---------------------------------------------------------------------------


class TestDistributedLock:
    def test_mutual_exclusion(self, redis_client):
        lock_name = f"test:res:{_uid()}"

        async def _run():
            async with distributed_lock(redis_client.client, lock_name, ttl=30):
                with pytest.raises(LockNotAcquired):
                    async with distributed_lock(redis_client.client, lock_name, ttl=30):
                        pass
            return True

        assert run(_run())

    def test_released_after_exit(self, redis_client):
        from app.core.redis import get_keys

        lock_name = f"test:res2:{_uid()}"
        key = get_keys().lock(lock_name)

        async def _run():
            async with distributed_lock(redis_client.client, lock_name, ttl=30):
                inside = await redis_client.client.get(key)
            after = await redis_client.client.get(key)
            return inside, after

        inside, after = run(_run())
        assert inside is not None
        assert after is None

    def test_key_has_ttl(self, redis_client):
        """锁必须带 TTL —— 否则持锁进程崩溃会导致永久死锁。"""
        from app.core.redis import get_keys

        lock_name = f"test:ttl:{_uid()}"

        async def _run():
            async with distributed_lock(redis_client.client, lock_name, ttl=30):
                return await redis_client.client.ttl(get_keys().lock(lock_name))

        assert 0 < run(_run()) <= 30


# ---------------------------------------------------------------------------
#  缓存
# ---------------------------------------------------------------------------


class TestCache:
    def test_json_roundtrip_and_ttl(self, redis_client):
        key = f"kbqa:cache:retrieval:{_uid()}"

        async def _run():
            await cache_set_json(redis_client.client, key, {"docs": [1, 2]}, ttl=120)
            return (
                await cache_get_json(redis_client.client, key),
                await redis_client.client.ttl(key),
            )

        value, ttl = run(_run())
        assert value == {"docs": [1, 2]}
        assert 0 < ttl <= 120

    def test_get_missing_returns_none(self, redis_client):
        assert run(cache_get_json(redis_client.client, f"kbqa:nope:{_uid()}")) is None

    def test_delete_by_pattern(self, redis_client):
        prefix = f"kbqa:cache:embed:{_uid()}:"

        async def _run():
            for i in range(3):
                await cache_set_json(
                    redis_client.client, f"{prefix}{i}", {"i": i}, ttl=60
                )
            return await cache_delete_pattern(redis_client.client, f"{prefix}*")

        assert run(_run()) >= 3


# ---------------------------------------------------------------------------
#  RedisManager：降级 与 严格模式
# ---------------------------------------------------------------------------


class TestRedisManagerFallback:
    """不可达地址 → 必须降级，而不是抛异常或挂死。"""

    def test_falls_back_when_unreachable(self, unreachable_manager):
        assert unreachable_manager.is_fallback is True
        assert unreachable_manager.state.error

    def test_health_reports_memory_mode(self, unreachable_manager):
        health = run(unreachable_manager.health())
        assert health["ok"] is True
        assert health["mode"] == "memory"

    def test_fallback_still_serves_requests(self, unreachable_manager):
        """降级后限流仍要能用（只是退化为单进程语义）。"""

        async def _run():
            return await check_rate_limit(
                unreachable_manager.client,
                scope=f"s_{_uid()}",
                identity="x",
                limit=2,
                window=60,
            )

        assert run(_run()).allowed is True

    def test_sync_and_async_clients_share_data_when_fallback(self, unreachable_manager):
        """降级时同步/异步两个 fakeredis 必须共享同一份数据。"""
        key = f"kbqa:test:shared:{_uid()}"
        unreachable_manager.sync_client.set(key, "v")
        try:
            assert run(unreachable_manager.client.get(key)) == "v"
        finally:
            unreachable_manager.sync_client.delete(key)


class TestRedisManagerStrictMode:
    """禁用降级时，Redis 不可达必须**启动失败**（fail fast）。"""

    def test_raises_when_fallback_disabled(self):
        from app.core.exceptions import DependencyUnavailableError

        cfg = RedisSettings(
            _env_file=None,
            url="redis://127.0.0.1:1/0",
            fallback_to_memory=False,
            connect_retries=0,
            socket_timeout=1,
        )
        manager = RedisManager(cfg)
        with pytest.raises(DependencyUnavailableError):
            run(manager.connect())
