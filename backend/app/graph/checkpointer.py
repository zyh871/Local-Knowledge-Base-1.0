"""
Checkpointer 工厂 —— 图执行状态的持久化。

**职责边界（关键）**：checkpointer 管的是「图执行到哪一步」，
不是「用户看到了什么对话」。后者是业务数据，走
`conversation_messages` 表 + Redis `session:*` 缓存。两者不可互相替代。

三种模式（`GRAPH_CHECKPOINTER`）：
    memory —— `InMemorySaver`。单进程、重启即失。默认值，零依赖。
    redis  —— `langgraph-checkpoint-redis` 的 `AsyncRedisSaver`。
              ⚠️ 依赖 **Redis Stack**（RediSearch 模块），普通 redis 镜像会初始化失败。
              ⚠️ 必须显式调用 `asetup()` 建索引，否则首次写入报「索引不存在」。
    none   —— 关闭。失去中断恢复与时间旅行能力。

**降级**：redis 模式初始化失败时回退 memory 并打 warning，不阻断服务启动。
**清理**：官方 saver **不会**自动回收过期数据；活跃线程登记在
`ckpt:index`（ZSet）中，由清理任务按 TTL 扫描删除。
"""

from __future__ import annotations

from typing import Any, Optional

from app.core.logging import get_logger

logger = get_logger(__name__)


def make_checkpointer(settings: Any, redis_client: Any = None) -> Optional[Any]:
    """按配置构建 checkpointer；返回 None 表示关闭。"""
    mode = getattr(settings.graph, "checkpointer", "memory")

    if mode == "none":
        logger.info("Checkpointer 已关闭（GRAPH_CHECKPOINTER=none）")
        return None

    if mode == "memory":
        from langgraph.checkpoint.memory import InMemorySaver

        logger.info("Checkpointer: InMemorySaver（重启后图状态丢失）")
        return InMemorySaver()

    # mode == "redis"
    try:
        from langgraph.checkpoint.redis.aio import AsyncRedisSaver
    except ImportError as exc:
        logger.warning(
            "未安装 langgraph-checkpoint-redis，Checkpointer 降级为内存模式。"
            " 启用方式：pip install langgraph-checkpoint-redis（需 Redis Stack 镜像）。"
            f" 原因: {exc}"
        )
        return _memory_fallback()

    try:
        # ⚠️ 必须用 `redis_url`（而非把已有客户端当位置参数传）。
        # AsyncRedisSaver 的第一个位置参数是 `redis_url: str`，
        # 传客户端对象会走到 redisvl 里做 `url.startswith(...)` 而报
        # `AttributeError: 'Redis' object has no attribute 'startswith'`。
        #
        # 也**不建议**用 `redis_client=` 复用我们的连接池：我们的客户端是
        # `decode_responses=True`，而 checkpoint 存的是 msgpack 二进制，
        # 字符串解码会破坏数据。让 saver 自己建连接（默认 decode_responses=False）。
        saver = AsyncRedisSaver(redis_url=settings.redis.url)

        # 官方 saver 不会自动建索引，必须显式调用 asetup()，否则首次写入失败
        import asyncio

        try:
            asyncio.get_running_loop()
            # 已在事件循环内 → 无法同步等待，标记给 setup_checkpointer() 处理
            saver._needs_setup = True  # type: ignore[attr-defined]
        except RuntimeError:
            asyncio.run(saver.asetup())

        logger.info("Checkpointer: AsyncRedisSaver（Redis Stack）")
        return saver
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"AsyncRedisSaver 初始化失败，降级为内存模式: {exc}")
        return _memory_fallback()


async def setup_checkpointer(saver: Any) -> Any:
    """在事件循环中补齐 Redis saver 的索引初始化。"""
    if saver is None:
        return None
    setup = getattr(saver, "asetup", None)
    if setup is None:
        return saver
    if getattr(saver, "_needs_setup", False):
        try:
            await setup()
            logger.info("Redis checkpoint 索引已就绪")
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"Redis checkpoint 索引初始化失败: {exc}")
    return saver


def _memory_fallback() -> Any:
    from langgraph.checkpoint.memory import InMemorySaver

    return InMemorySaver()


async def record_checkpoint_activity(
    redis_client: Any, thread_id: str, ttl: Optional[int] = None
) -> None:
    """登记活跃线程，供 TTL 清理任务扫描。

    官方 saver 不提供自动清理，必须由业务侧维护索引 + 定期回收，
    否则 Redis 会被 checkpoint 数据持续撑大。
    """
    if redis_client is None:
        return
    import time as _time

    from app.core.redis import get_keys

    keys = get_keys()
    try:
        now = _time.time()
        await redis_client.zadd(keys.ckpt_index(), {thread_id: now})
        await redis_client.set(keys.ckpt_last(thread_id), str(now), ex=ttl)
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"登记 checkpoint 活跃线程失败（可忽略）: {exc}")


async def purge_stale_checkpoints(redis_client: Any, ttl: int, saver: Any = None) -> int:
    """删除超过 TTL 未活动的线程 checkpoint。

    由定时任务调用（例如每小时一次）。
    """
    if redis_client is None:
        return 0
    import time as _time

    from app.core.redis import get_keys

    keys = get_keys()
    cutoff = _time.time() - ttl
    removed = 0
    try:
        stale = await redis_client.zrangebyscore(keys.ckpt_index(), "-inf", cutoff)
        for thread_id in stale or []:
            if saver is not None and hasattr(saver, "adelete_thread"):
                try:
                    await saver.adelete_thread(thread_id)
                except Exception:  # noqa: BLE001
                    pass
            await redis_client.delete(keys.ckpt_last(thread_id))
            await redis_client.zrem(keys.ckpt_index(), thread_id)
            removed += 1
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"清理过期 checkpoint 失败: {exc}")
    if removed:
        logger.info(f"已清理 {removed} 个过期 checkpoint 线程")
    return removed
