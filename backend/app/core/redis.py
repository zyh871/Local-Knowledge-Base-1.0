"""
Redis 基础设施：连接池、键命名规范、TTL 常量、限流与分布式锁原语。

职责边界（详见 docs/REFACTOR_FASTAPI_LANGGRAPH_REDIS.md 第 4 节）：
    ✅ 会话热缓存 / LangGraph checkpoint / 分布式限流 / 分享链接 /
       检索与嵌入缓存 / 用户画像缓存 / 分布式锁 / 任务队列 / JWT 黑名单
    ❌ 向量数据（仍用 Chroma）· 原始文档文件（磁盘）· 业务持久化真相源（SQLite）

降级策略：
    Redis 不可用且 `REDIS_FALLBACK_TO_MEMORY=true` 时，自动切换到进程内的
    fakeredis 实例。此时**多 worker 语义会退化**（限流/锁/缓存各进程独立），
    日志会打 warning 明确提示，避免静默降级。

键命名规范：{prefix}:{domain}:{entity}[:{id}][:{sub}]
    * 全小写、冒号分隔
    * **禁止把用户输入原文写进键名**（一律 sha1 摘要）
    * 键名只在本模块构造，业务代码不得手拼字符串
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Optional

from app.core.config import RedisSettings, get_redis_settings

logger = logging.getLogger(__name__)


def digest(text: str, length: int = 16) -> str:
    """把任意文本压成定长摘要，用于键名（避免用户输入直接进键空间）。"""
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:length]


# ---------------------------------------------------------------------------
#  键命名规范
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RedisKeys:
    """所有 Redis 键的唯一构造点。"""

    prefix: str = "kbqa"

    # ── 内部工具 ──
    def _k(self, *parts: str) -> str:
        return ":".join((self.prefix, *parts))

    def scan_pattern(self, domain: str) -> str:
        return f"{self.prefix}:{domain}:*"

    # ── 会话运行时 ──
    def session_msgs(self, cid: str) -> str:
        """List：会话消息 JSON 数组（业务消息热缓存）。"""
        return self._k("session", cid, "msgs")

    def session_meta(self, cid: str) -> str:
        """Hash：会话元数据镜像（title/pinned/locked/archived）。"""
        return self._k("session", cid, "meta")

    def session_owner(self, cid: str) -> str:
        """String：会话归属缓存，用于快速鉴权。"""
        return self._k("session", cid, "owner")

    # ── 用户画像 ──
    def profile(self, username: str) -> str:
        """String(JSON)：用户画像缓存。"""
        return self._k("profile", username)

    # ── LangGraph checkpoint 辅助 ──
    def ckpt_last(self, thread_id: str) -> str:
        """String(ts)：某线程最后一次 checkpoint 时间，供 TTL 清理任务扫描。"""
        return self._k("ckpt", "last", thread_id)

    def ckpt_index(self) -> str:
        """ZSet(score=时间戳)：活跃线程索引。"""
        return self._k("ckpt", "index")

    # ── 缓存 ──
    def cache_retrieval(self, question: str, k: int) -> str:
        return self._k("cache", "retrieval", f"{digest(question)}:{k}")

    def cache_embedding(self, text: str) -> str:
        return self._k("cache", "embed", digest(text))

    def cache_rerank(self, question: str, doc_digest: str) -> str:
        return self._k("cache", "rerank", f"{digest(question)}:{doc_digest}")

    # ── 分享链接 ──
    def share(self, token: str) -> str:
        """String(JSON)：分享记录，TTL = 链接有效期。"""
        return self._k("share", token)

    def share_by_user(self, username: str) -> str:
        """Set：用户的分享 token 索引（二级索引，用于列举与清理）。"""
        return self._k("share", "by_user", username)

    # ── 限流 ──
    def ratelimit(self, scope: str, identity: str, window: int) -> str:
        return self._k("rl", scope, digest(identity, 12), str(window))

    # ── 分布式锁 ──
    def lock(self, resource: str) -> str:
        return self._k("lock", resource)

    # ── 任务队列 ──
    def stream_ingest(self) -> str:
        return self._k("stream", "ingest")

    def stream_ingest_group(self) -> str:
        return self._k("stream", "ingest", "workers")

    def job_status(self, job_id: str) -> str:
        return self._k("job", job_id)

    # ── 认证 ──
    def jwt_blacklist(self, jti: str) -> str:
        return self._k("jwt", "blacklist", jti)


_keys: Optional[RedisKeys] = None


def get_keys() -> RedisKeys:
    global _keys
    if _keys is None:
        _keys = RedisKeys(prefix=get_redis_settings().key_prefix)
    return _keys


def reset_keys() -> None:
    """测试用。"""
    global _keys
    _keys = None


# ---------------------------------------------------------------------------
#  连接管理
# ---------------------------------------------------------------------------


@dataclass
class RedisState:
    client: Any = None
    sync_client: Any = None
    fallback: bool = False
    error: str = ""
    extra: dict = field(default_factory=dict)


class RedisManager:
    """负责建立/关闭连接，并在失败时降级为进程内实现。

    同时维护**异步**与**同步**两个客户端：
      * `client`      —— 异步。用于 API 层原语（限流、分享链接、分布式锁）
      * `sync_client` —— 同步。用于同步代码路径（检索/嵌入缓存、会话消息缓存）
        这些路径位于 LangGraph 的同步节点内部，拿不到事件循环，无法 await。

    降级时两个 fakeredis 实例**共享同一个 FakeServer**，
    保证同步与异步视图看到同一份数据。
    """

    def __init__(self, cfg: Optional[RedisSettings] = None) -> None:
        self.cfg = cfg or get_redis_settings()
        self.state = RedisState()

    # -- 生命周期 --------------------------------------------------------

    async def connect(self) -> RedisState:
        cfg = self.cfg
        last_err: Exception | None = None

        for attempt in range(cfg.connect_retries + 1):
            try:
                from redis import Redis as SyncRedis
                from redis.asyncio import Redis

                client = Redis.from_url(
                    cfg.url,
                    max_connections=cfg.max_connections,
                    decode_responses=True,
                    socket_timeout=cfg.socket_timeout,
                    socket_connect_timeout=cfg.socket_timeout,
                )
                await client.ping()

                sync_client = SyncRedis.from_url(
                    cfg.url,
                    max_connections=max(4, cfg.max_connections // 4),
                    decode_responses=True,
                    socket_timeout=cfg.socket_timeout,
                    socket_connect_timeout=cfg.socket_timeout,
                )
                sync_client.ping()

                self.state = RedisState(client=client, sync_client=sync_client, fallback=False)
                info = await self._server_info(client)
                self.state.extra = info
                logger.info(
                    f"Redis 已连接: {cfg.url} (版本 {info.get('version', 'unknown')})"
                )
                return self.state
            except Exception as exc:  # noqa: BLE001
                last_err = exc
                if attempt < cfg.connect_retries:
                    logger.debug(
                        f"Redis 连接失败，重试 {attempt + 1}/{cfg.connect_retries}: {exc}"
                    )

        # 连接失败 → 降级
        if not cfg.fallback_to_memory:
            from app.core.exceptions import DependencyUnavailableError

            raise DependencyUnavailableError(
                f"Redis 不可用且已禁用降级: {last_err}"
            ) from last_err

        logger.warning(
            "Redis 不可用（%s），已降级为进程内实现。"
            "⚠️ 多 worker 下限流/分布式锁/缓存将退化为各进程独立，仅建议开发环境使用。"
            " 生产环境请启动 Redis 并设置 REDIS_FALLBACK_TO_MEMORY=false。",
            last_err,
        )
        try:
            import fakeredis
            from fakeredis import aioredis as fake_aioredis

            # 共享 FakeServer：同步与异步客户端看到同一份数据
            server = fakeredis.FakeServer()
            async_client = fake_aioredis.FakeRedis(server=server, decode_responses=True)
            sync_client = fakeredis.FakeRedis(server=server, decode_responses=True)
            await async_client.ping()
            self.state = RedisState(
                client=async_client,
                sync_client=sync_client,
                fallback=True,
                error=str(last_err),
                extra={"mode": "memory"},
            )
        except Exception as fake_exc:  # pragma: no cover
            from app.core.exceptions import DependencyUnavailableError

            raise DependencyUnavailableError(
                f"Redis 与进程内降级均不可用: redis={last_err}; fake={fake_exc}"
            ) from last_err
        return self.state

    async def close(self) -> None:
        for name in ("client", "sync_client"):
            target = getattr(self.state, name, None)
            if target is None:
                continue
            try:
                closer = getattr(target, "aclose", None) or getattr(target, "close", None)
                result = closer() if closer else None
                if hasattr(result, "__await__"):
                    await result
            except Exception as exc:  # noqa: BLE001
                logger.debug(f"关闭 Redis {name} 时出错（可忽略）: {exc}")
        self.state = RedisState()

    # -- 访问器 ----------------------------------------------------------

    @property
    def client(self) -> Any:
        if self.state.client is None:
            raise RuntimeError("Redis 尚未初始化，请先 await manager.connect()")
        return self.state.client

    @property
    def sync_client(self) -> Any:
        """同步客户端。未初始化时返回 None，调用方需容忍。"""
        return self.state.sync_client

    @property
    def is_fallback(self) -> bool:
        return self.state.fallback

    async def health(self) -> dict:
        if self.state.client is None:
            return {"ok": False, "error": "not_connected"}
        try:
            await self.state.client.ping()
            return {
                "ok": True,
                "mode": "memory" if self.state.fallback else "redis",
                "version": self.state.extra.get("version", "unknown"),
                "error": self.state.error or None,
            }
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "mode": "unknown", "error": str(exc)}

    @staticmethod
    async def _server_info(client: Any) -> dict:
        try:
            raw = await client.info("server")
            return {"version": str(raw.get("redis_version", "unknown"))}
        except Exception:  # noqa: BLE001  fakeredis 的 info 结构可能不同
            return {"version": "unknown"}


# ---------------------------------------------------------------------------
#  同步客户端绑定
# ---------------------------------------------------------------------------
#
# 为什么需要模块级绑定：检索缓存 / 嵌入缓存 / 会话消息缓存这几条路径位于
# LangGraph 的**同步节点**内部执行（拿不到事件循环，无法 await），
# 而把它们一路透传会污染大量函数签名。
# 因此在这里做一次进程内绑定，语义等价于 app.state，只是给同步代码用。
#
# 未绑定（如单元测试、无 Redis 环境）时所有同步缓存函数**静默跳过**，
# 缓存是纯优化项，不应让业务失败。

_sync_client_ref: Any = None


def bind_sync_client(client: Any) -> None:
    """lifespan 启动时调用，绑定同步客户端。"""
    global _sync_client_ref
    _sync_client_ref = client


def get_bound_sync_client() -> Any:
    return _sync_client_ref


def unbind_sync_client() -> None:
    global _sync_client_ref
    _sync_client_ref = None


# ---------------------------------------------------------------------------
#  同步缓存原语
# ---------------------------------------------------------------------------


def sync_cache_get_json(key: str) -> Any | None:
    client = get_bound_sync_client()
    if client is None:
        return None
    try:
        raw = client.get(key)
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"同步缓存读取失败 {key}: {exc}")
        return None
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None


def sync_cache_set_json(key: str, value: Any, ttl: int) -> None:
    client = get_bound_sync_client()
    if client is None:
        return
    try:
        client.set(key, json.dumps(value, ensure_ascii=False, default=str), ex=ttl)
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"同步缓存写入失败 {key}: {exc}")


def sync_cache_delete_pattern(pattern: str) -> int:
    """按前缀清理（同步版）。用 SCAN 游标分批，避免 KEYS 阻塞。"""
    client = get_bound_sync_client()
    if client is None:
        return 0
    removed = 0
    try:
        cursor = 0
        while True:
            cursor, keys = client.scan(cursor=cursor, match=pattern, count=200)
            if keys:
                removed += int(client.delete(*keys))
            if cursor == 0:
                break
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"同步缓存清理失败 {pattern}: {exc}")
    return removed


# ---------------------------------------------------------------------------
#  通用缓存原语（异步）
# ---------------------------------------------------------------------------


async def cache_get_json(client: Any, key: str) -> Any | None:
    try:
        raw = await client.get(key)
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"缓存读取失败 {key}: {exc}")
        return None
    if raw is None:
        return None
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None


async def cache_set_json(client: Any, key: str, value: Any, ttl: int) -> None:
    try:
        await client.set(key, json.dumps(value, ensure_ascii=False, default=str), ex=ttl)
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"缓存写入失败 {key}: {exc}")


async def cache_delete_pattern(client: Any, pattern: str) -> int:
    """按前缀清理缓存。用于文档增删后主动失效检索缓存。

    生产环境避免 KEYS（阻塞），改用 SCAN 游标分批删除。
    """
    removed = 0
    try:
        cursor = 0
        while True:
            cursor, keys = await client.scan(cursor=cursor, match=pattern, count=200)
            if keys:
                removed += int(await client.delete(*keys))
            if cursor == 0:
                break
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"缓存清理失败 {pattern}: {exc}")
    return removed


# ---------------------------------------------------------------------------
#  分布式限流
# ---------------------------------------------------------------------------


class RateLimitResult:
    __slots__ = ("allowed", "current", "limit", "retry_after")

    def __init__(self, allowed: bool, current: int, limit: int, retry_after: int) -> None:
        self.allowed = allowed
        self.current = current
        self.limit = limit
        self.retry_after = retry_after

    @property
    def remaining(self) -> int:
        """本窗口内还剩多少次配额（用于 X-RateLimit-Remaining 之类的响应头）。"""
        return max(0, self.limit - self.current)

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"RateLimitResult(allowed={self.allowed}, current={self.current}, "
            f"limit={self.limit}, remaining={self.remaining}, "
            f"retry_after={self.retry_after})"
        )


async def check_rate_limit(
    client: Any,
    *,
    scope: str,
    identity: str,
    limit: int,
    window: int,
) -> RateLimitResult:
    """固定窗口计数限流。

    相比旧实现（slowapi 进程内计数）的价值：多 worker 下限额才是真的。
    """
    key = get_keys().ratelimit(scope, identity, window)
    try:
        current = int(await client.incr(key))
        if current == 1:
            # 仅首次设置过期；部分实现不支持 nx 参数，做一次兜底
            try:
                await client.expire(key, window, nx=True)
            except (TypeError, Exception):  # noqa: BLE001
                await client.expire(key, window)
        ttl = await client.ttl(key)
        ttl = int(ttl) if ttl is not None else window
        if ttl < 0:  # 键存在但无 TTL（异常态）→ 补设，避免永久计数
            await client.expire(key, window)
            ttl = window
    except Exception as exc:  # noqa: BLE001
        # 限流依赖不可用时**放行**，不让基础设施问题阻断业务
        logger.warning(f"限流检查失败，本次放行: {exc}")
        return RateLimitResult(True, 0, limit, window)

    return RateLimitResult(
        allowed=current <= limit,
        current=current,
        limit=limit,
        retry_after=max(ttl, 1),
    )


# ---------------------------------------------------------------------------
#  分布式锁
# ---------------------------------------------------------------------------


class LockNotAcquired(Exception):
    """获取锁失败。"""


_RELEASE_LUA = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('del', KEYS[1])
end
return 0
"""


@asynccontextmanager
async def distributed_lock(
    client: Any,
    resource: str,
    *,
    ttl: Optional[int] = None,
    wait: bool = False,
    wait_timeout: float = 30.0,
    poll_interval: float = 0.2,
) -> AsyncIterator[str]:
    """分布式互斥锁。

    用法：
        async with distributed_lock(redis, "vectorstore:rebuild", ttl=600):
            ...  # 同一时刻只有一个 worker 在重建索引

    wait=False 时获取失败立即抛 LockNotAcquired（默认，接口快速失败）。
    """
    import asyncio
    import time as _time

    cfg = get_redis_settings()
    ttl = ttl or cfg.ttl_lock_default
    key = get_keys().lock(resource)
    token = uuid.uuid4().hex
    deadline = _time.monotonic() + wait_timeout

    while True:
        acquired = False
        try:
            acquired = bool(await client.set(key, token, nx=True, px=ttl * 1000))
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"获取锁 {resource} 失败（基础设施异常）: {exc}")
            raise LockNotAcquired(f"锁服务不可用: {exc}") from exc

        if acquired:
            break
        if not wait or _time.monotonic() >= deadline:
            raise LockNotAcquired(f"资源 {resource} 正被其他任务占用")
        await asyncio.sleep(poll_interval)

    try:
        yield token
    finally:
        try:
            await client.eval(_RELEASE_LUA, 1, key, token)
        except Exception:  # noqa: BLE001  fakeredis 无 Lua 支持时退化为直接删除
            try:
                current = await client.get(key)
                if current == token:
                    await client.delete(key)
            except Exception as exc:  # noqa: BLE001
                logger.debug(f"释放锁 {resource} 失败（将由 TTL 自动回收）: {exc}")
