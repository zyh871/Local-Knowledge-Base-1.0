"""
会话消息与用户画像的 Redis 热缓存。

**真相源仍是 SQLite**（`data/conversations.db`）；Redis 只是加速层。
因此本模块所有函数在 Redis 不可用时都**静默降级为 no-op** ——
调用方拿到 None 后会自然回源数据库，缓存失效不会导致数据丢失。

为什么用同步客户端：
    本模块被 `app/memory` 调用，而 `app/memory` 又被 LangGraph 的**同步节点**
    调用（拿不到事件循环）。见 `app/core/redis.py` 的 `bind_sync_client`。

多 worker 语义：
    这正是引入 Redis 的核心动机之一 —— v3 用进程内 dict 缓存会话记忆，
    `--workers 4` 时每个 worker 各持一份，导致同一会话在不同 worker 上
    看到不同的历史。改为 Redis 后所有 worker 共享同一份缓存。
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from app.core.config import get_redis_settings
from app.core.logging import get_logger
from app.core.redis import (
    get_bound_sync_client,
    get_keys,
    sync_cache_get_json,
    sync_cache_set_json,
)

logger = get_logger(__name__)

# 单会话缓存的消息条数上限。
# 不设上限的话 List 会随会话时长无限增长；窗口裁剪由图节点负责，
# 这里只做一个防止内存失控的兜底。
SESSION_MESSAGE_CAP = 200

__all__ = [
    "SESSION_MESSAGE_CAP",
    "get_cached_messages",
    "set_cached_messages",
    "append_messages",
    "invalidate_messages",
    "get_cached_profile",
    "set_cached_profile",
    "invalidate_profile",
    "is_enabled",
]


def is_enabled() -> bool:
    return get_bound_sync_client() is not None


# ---------------------------------------------------------------------------
#  会话消息
# ---------------------------------------------------------------------------


def get_cached_messages(conversation_id: str) -> Optional[List[Dict[str, str]]]:
    """读取缓存的会话消息。未命中或缓存不可用时返回 None。"""
    client = get_bound_sync_client()
    if client is None or not conversation_id:
        return None

    key = get_keys().session_msgs(conversation_id)
    try:
        raw_items = client.lrange(key, 0, -1)
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"读取会话缓存失败 {conversation_id}: {exc}")
        return None

    if not raw_items:
        return None

    messages: List[Dict[str, str]] = []
    for item in raw_items:
        try:
            decoded = json.loads(item)
        except (json.JSONDecodeError, TypeError):
            # 缓存损坏（理论上不会发生）→ 当作未命中，回源数据库
            logger.warning(f"会话缓存条目解析失败，放弃缓存 {conversation_id}")
            return None
        if isinstance(decoded, dict):
            messages.append(
                {
                    "role": str(decoded.get("role", "user")),
                    "content": str(decoded.get("content", "")),
                }
            )
    return messages or None


def set_cached_messages(
    conversation_id: str, messages: List[Dict[str, str]]
) -> None:
    """整体覆盖缓存（回源数据库后回填）。"""
    client = get_bound_sync_client()
    if client is None or not conversation_id or not messages:
        return

    keys = get_keys()
    key = keys.session_msgs(conversation_id)
    ttl = get_redis_settings().ttl_session
    try:
        pipe = client.pipeline(transaction=True)
        pipe.delete(key)
        for message in messages[-SESSION_MESSAGE_CAP:]:
            pipe.rpush(key, json.dumps(message, ensure_ascii=False))
        pipe.ltrim(key, -SESSION_MESSAGE_CAP, -1)
        pipe.expire(key, ttl)
        pipe.execute()
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"回填会话缓存失败 {conversation_id}: {exc}")


def append_messages(
    conversation_id: str, messages: List[Dict[str, str]]
) -> None:
    """追加两条消息（一轮问答）并刷新 TTL。

    使用 pipeline 保证「追加 + 裁剪 + 续期」原子完成，
    避免并发写入时把 List 撑破上限。
    """
    client = get_bound_sync_client()
    if client is None or not conversation_id or not messages:
        return

    keys = get_keys()
    key = keys.session_msgs(conversation_id)
    ttl = get_redis_settings().ttl_session
    try:
        pipe = client.pipeline(transaction=True)
        for message in messages:
            pipe.rpush(key, json.dumps(message, ensure_ascii=False))
        pipe.ltrim(key, -SESSION_MESSAGE_CAP, -1)
        pipe.expire(key, ttl)
        pipe.execute()
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"追加会话缓存失败 {conversation_id}: {exc}")


def invalidate_messages(conversation_id: str) -> None:
    """删除会话消息缓存（会话被清空/删除时调用）。"""
    client = get_bound_sync_client()
    if client is None or not conversation_id:
        return
    try:
        client.delete(
            get_keys().session_msgs(conversation_id),
            get_keys().session_meta(conversation_id),
            get_keys().session_owner(conversation_id),
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"清理会话缓存失败 {conversation_id}: {exc}")


# ---------------------------------------------------------------------------
#  用户画像
# ---------------------------------------------------------------------------


def get_cached_profile(username: str) -> Optional[Dict[str, Any]]:
    if not username:
        return None
    cached = sync_cache_get_json(get_keys().profile(username))
    return cached if isinstance(cached, dict) else None


def set_cached_profile(username: str, profile: Dict[str, Any]) -> None:
    if not username or not isinstance(profile, dict):
        return
    sync_cache_set_json(
        get_keys().profile(username), profile, get_redis_settings().ttl_profile_cache
    )


def invalidate_profile(username: str) -> None:
    """画像更新后主动失效 —— 不等 TTL 自然过期。"""
    client = get_bound_sync_client()
    if client is None or not username:
        return
    try:
        client.delete(get_keys().profile(username))
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"清理画像缓存失败 {username}: {exc}")
