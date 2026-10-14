"""
对话记忆：会话元数据 + 消息历史。

存储分层：
    Redis  —— 热缓存（`session:{cid}:msgs` List），多 worker 共享
    SQLite —— **真相源**（`data/conversations.db`）

读取顺序：Redis 命中直接返回 → 未命中回源 SQLite 并回填缓存。
Redis 不可用时全程直读 SQLite，功能不受影响（只是慢一点）。

★ v4 变更：**移除了进程内的 `_memory_instances` 与
`ConversationBufferWindowMemory`**。
原因：它们在单 worker 下掩盖了多 worker 的一致性问题 ——
`--workers 4` 时每个 worker 各持一份记忆，同一会话在不同 worker 上
看到的历史不同。窗口裁剪已改由状态图的 `ingest` 节点统一负责
（见 `app/graph/nodes/prepare.py`），此处只管存取。
"""

from __future__ import annotations

import logging
import os
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.core.config import get_settings
from app.repositories import session_cache

logger = logging.getLogger(__name__)

_meta: Dict[str, Dict[str, Any]] = {}
_lock = threading.RLock()
_conn: Optional[sqlite3.Connection] = None

DB_DIR = get_settings().paths.data_path
os.makedirs(DB_DIR, exist_ok=True)
META_DB = os.path.join(DB_DIR, "conversations.db")


def _meta_connect() -> sqlite3.Connection:
    """返回长连接（由 `_lock` 保护）。"""
    global _conn
    if _conn is None:
        _conn = sqlite3.connect(META_DB, check_same_thread=False, timeout=30)
        _conn.row_factory = sqlite3.Row
    return _conn


def _init_meta_db() -> None:
    with _lock, _meta_connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS conversations (
                conversation_id TEXT PRIMARY KEY,
                username        TEXT,
                title           TEXT,
                pinned          INTEGER DEFAULT 0,
                locked          INTEGER DEFAULT 0,
                archived        INTEGER DEFAULT 0,
                created_at      TEXT,
                updated_at      TEXT,
                turn_count      INTEGER DEFAULT 0
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS conversation_messages (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id TEXT NOT NULL,
                role            TEXT NOT NULL,
                content         TEXT NOT NULL,
                turn_index      INTEGER DEFAULT 0,
                created_at      TEXT
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_conv_user ON conversations(username)")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_msg_conv ON conversation_messages(conversation_id, turn_index)"
        )
        conn.commit()


_init_meta_db()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
#  消息历史
# ---------------------------------------------------------------------------


def save_context(
    conversation_id: str, user_text: str, ai_text: str, locked: bool = False
) -> None:
    """写入一轮问答。

    先落 SQLite（真相源），再更新 Redis 缓存。顺序是有意的：
    若缓存写失败，数据仍然正确，只是下次读慢一点；反之若先写缓存、
    后写库失败，就会出现"缓存有、库没有"的幻觉数据。
    """
    now = _now()
    with _lock, _meta_connect() as conn:
        row = conn.execute(
            "SELECT COALESCE(MAX(turn_index), -1) AS idx FROM conversation_messages "
            "WHERE conversation_id = ?",
            (conversation_id,),
        ).fetchone()
        next_idx = (row["idx"] if row and row["idx"] is not None else -1) + 1
        conn.execute(
            "INSERT INTO conversation_messages "
            "(conversation_id, role, content, turn_index, created_at) VALUES (?, ?, ?, ?, ?)",
            (conversation_id, "user", user_text, next_idx, now),
        )
        conn.execute(
            "INSERT INTO conversation_messages "
            "(conversation_id, role, content, turn_index, created_at) VALUES (?, ?, ?, ?, ?)",
            (conversation_id, "assistant", ai_text, next_idx, now),
        )
        conn.commit()

    session_cache.append_messages(
        conversation_id,
        [
            {"role": "user", "content": user_text},
            {"role": "assistant", "content": ai_text},
        ],
    )


def load_history(conversation_id: str) -> List[Dict[str, str]]:
    """读取会话消息（Redis → SQLite 回源）。

    返回条数**不做窗口裁剪** —— 裁剪由状态图的 `ingest` 节点按会话锁定状态
    决定（`conversation_window_size` / `locked_window_size`）。
    """
    cached = session_cache.get_cached_messages(conversation_id)
    if cached is not None:
        return cached

    with _lock, _meta_connect() as conn:
        rows = conn.execute(
            "SELECT role, content FROM conversation_messages "
            "WHERE conversation_id = ? ORDER BY turn_index, id",
            (conversation_id,),
        ).fetchall()

    messages = [{"role": r["role"], "content": r["content"]} for r in rows]
    if messages:
        session_cache.set_cached_messages(conversation_id, messages)
    return messages


def clear_memory(conversation_id: str) -> None:
    """清空某个会话的全部消息（库 + 缓存）。"""
    with _lock, _meta_connect() as conn:
        conn.execute(
            "DELETE FROM conversation_messages WHERE conversation_id = ?",
            (conversation_id,),
        )
        conn.commit()
    session_cache.invalidate_messages(conversation_id)


def invalidate_history_cache(conversation_id: str) -> None:
    """仅清缓存，不动数据库。测试与调试用。"""
    session_cache.invalidate_messages(conversation_id)


# ---------------------------------------------------------------------------
#  会话元数据
# ---------------------------------------------------------------------------


def upsert_conversation(
    conversation_id: str,
    username: str = "",
    title: Optional[str] = None,
    pinned: Optional[bool] = None,
    locked: Optional[bool] = None,
    archived: Optional[bool] = None,
    bump_turn: bool = True,
) -> Dict[str, Any]:
    now = _now()
    with _lock, _meta_connect() as conn:
        row = conn.execute(
            "SELECT * FROM conversations WHERE conversation_id = ?", (conversation_id,)
        ).fetchone()
        if row is None:
            conn.execute(
                """INSERT INTO conversations
                   (conversation_id, username, title, pinned, locked, archived,
                    created_at, updated_at, turn_count)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0)""",
                (
                    conversation_id,
                    username,
                    title or "新对话",
                    int(bool(pinned)) if pinned is not None else 0,
                    int(bool(locked)) if locked is not None else 0,
                    int(bool(archived)) if archived is not None else 0,
                    now,
                    now,
                ),
            )
        else:
            sets, params = [], []
            if title is not None:
                sets.append("title = ?")
                params.append(title)
            if pinned is not None:
                sets.append("pinned = ?")
                params.append(int(bool(pinned)))
            if locked is not None:
                sets.append("locked = ?")
                params.append(int(bool(locked)))
            if archived is not None:
                sets.append("archived = ?")
                params.append(int(bool(archived)))
            if bump_turn:
                sets.append("turn_count = turn_count + 1")
            sets.append("updated_at = ?")
            params.append(now)
            params.append(conversation_id)
            conn.execute(
                f"UPDATE conversations SET {', '.join(sets)} WHERE conversation_id = ?",
                params,
            )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM conversations WHERE conversation_id = ?", (conversation_id,)
        ).fetchone()
    return _row_to_meta(row)


def _row_to_meta(row: Optional[sqlite3.Row]) -> Dict[str, Any]:
    if row is None:
        return {}
    data = dict(row)
    for key in ("pinned", "locked", "archived"):
        data[key] = bool(data.get(key))
    return data


def get_conversation(conversation_id: str) -> Dict[str, Any]:
    with _lock, _meta_connect() as conn:
        row = conn.execute(
            "SELECT * FROM conversations WHERE conversation_id = ?", (conversation_id,)
        ).fetchone()
    meta = _row_to_meta(row)
    meta["history"] = load_history(conversation_id)
    return meta


def verify_owner(conversation_id: str, username: str) -> bool:
    """校验归属；会话尚无归属时视为可访问（与 v3 行为一致）。"""
    if not conversation_id or not username:
        return False
    with _lock, _meta_connect() as conn:
        row = conn.execute(
            "SELECT username FROM conversations WHERE conversation_id = ?",
            (conversation_id,),
        ).fetchone()
    if row is None:
        return True
    owner = row["username"]
    return not owner or owner == username


def list_conversations(
    username: str = "",
    include_archived: bool = False,
    skip: int = 0,
    limit: int = 500,
) -> Dict[str, Any]:
    where_params: List[Any] = []
    clauses: List[str] = []
    if username:
        clauses.append("username = ?")
        where_params.append(username)
    if not include_archived:
        clauses.append("archived = 0")
    where_sql = (" WHERE " + " AND ".join(clauses)) if clauses else ""

    with _lock, _meta_connect() as conn:
        row = conn.execute(
            f"SELECT COUNT(*) AS cnt FROM conversations{where_sql}", where_params
        ).fetchone()
        total = row["cnt"] if row else 0
        rows = conn.execute(
            f"SELECT * FROM conversations{where_sql} "
            "ORDER BY pinned DESC, updated_at DESC LIMIT ? OFFSET ?",
            where_params + [limit, skip],
        ).fetchall()

    return {
        "items": [_row_to_meta(r) for r in rows],
        "total": total,
        "skip": skip,
        "limit": limit,
    }


def delete_conversation(conversation_id: str) -> bool:
    clear_memory(conversation_id)
    with _lock, _meta_connect() as conn:
        cursor = conn.execute(
            "DELETE FROM conversations WHERE conversation_id = ?", (conversation_id,)
        )
        conn.commit()
    return cursor.rowcount > 0


def export_conversation(conversation_id: str) -> Dict[str, Any]:
    meta = get_conversation(conversation_id)
    if not meta:
        return {}
    return {
        "conversation_id": conversation_id,
        "title": meta.get("title"),
        "username": meta.get("username"),
        "pinned": meta.get("pinned"),
        "locked": meta.get("locked"),
        "archived": meta.get("archived"),
        "created_at": meta.get("created_at"),
        "updated_at": meta.get("updated_at"),
        "turn_count": meta.get("turn_count", 0),
        "history": meta.get("history", []),
    }


def is_locked(conversation_id: str) -> bool:
    with _lock, _meta_connect() as conn:
        row = conn.execute(
            "SELECT locked FROM conversations WHERE conversation_id = ?",
            (conversation_id,),
        ).fetchone()
    return bool(row and row["locked"])
