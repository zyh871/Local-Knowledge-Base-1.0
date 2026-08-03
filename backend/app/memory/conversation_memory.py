"""
Per-conversation memory with windowed context + lock & metadata support.

Provides:
  * short-term sliding window (k=5) for normal conversations
  * lock mode: stops the window from being trimmed
  * metadata: title, pinned flag, archived flag, owner username
"""

import json
import logging
import os
import sqlite3
import threading
from datetime import datetime
from typing import Any, Dict, List, Optional

from langchain_classic.memory import ConversationBufferWindowMemory

from app import config

logger = logging.getLogger(__name__)

_memory_instances: Dict[str, ConversationBufferWindowMemory] = {}
_meta: Dict[str, Dict[str, Any]] = {}
_lock = threading.RLock()

DB_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "data")
os.makedirs(DB_DIR, exist_ok=True)
META_DB = os.path.join(DB_DIR, "conversations.db")


def _meta_connect() -> sqlite3.Connection:
    conn = sqlite3.connect(META_DB, check_same_thread=False, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


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
        conn.execute("CREATE INDEX IF NOT EXISTS idx_conv_user ON conversations(username)")
        conn.commit()


_init_meta_db()


def _now() -> str:
    return datetime.utcnow().isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# Memory core
# ---------------------------------------------------------------------------

def get_memory(conversation_id: str, locked: bool = False):
    """Get or create conversation memory. `locked=True` disables window trimming."""
    if conversation_id not in _memory_instances:
        _memory_instances[conversation_id] = ConversationBufferWindowMemory(
            k=10 if locked else config.CONVERSATION_WINDOW_SIZE,
            memory_key="chat_history",
            return_messages=True,
        )
    elif locked:
        existing = _memory_instances[conversation_id]
        if getattr(existing, "k", None) != 10:
            existing.k = 10
    return _memory_instances[conversation_id]


def save_context(conversation_id: str, user_text: str, ai_text: str, locked: bool = False) -> None:
    mem = get_memory(conversation_id, locked=locked)
    mem.save_context({"input": user_text}, {"output": ai_text})


def load_history(conversation_id: str) -> List[Dict[str, str]]:
    mem = get_memory(conversation_id)
    vars_ = mem.load_memory_variables({})
    msgs = vars_.get("chat_history", [])
    out: List[Dict[str, str]] = []
    for m in msgs:
        role = "user" if m.__class__.__name__.lower().startswith("human") else "assistant"
        out.append({"role": role, "content": getattr(m, "content", str(m))})
    return out


def clear_memory(conversation_id: str):
    """Clear short-term memory for a specific conversation."""
    _memory_instances.pop(conversation_id, None)


def clear_all_memory():
    _memory_instances.clear()


# ---------------------------------------------------------------------------
# Metadata (pinned / locked / archived / title)
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
                   (conversation_id, username, title, pinned, locked, archived, created_at, updated_at, turn_count)
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
                sets.append("title = ?"); params.append(title)
            if pinned is not None:
                sets.append("pinned = ?"); params.append(int(bool(pinned)))
            if locked is not None:
                sets.append("locked = ?"); params.append(int(bool(locked)))
            if archived is not None:
                sets.append("archived = ?"); params.append(int(bool(archived)))
            if bump_turn:
                sets.append("turn_count = turn_count + 1")
            sets.append("updated_at = ?"); params.append(now)
            params.append(conversation_id)
            conn.execute(f"UPDATE conversations SET {', '.join(sets)} WHERE conversation_id = ?", params)
        conn.commit()
        row = conn.execute("SELECT * FROM conversations WHERE conversation_id = ?", (conversation_id,)).fetchone()
    return _row_to_meta(row)


def _row_to_meta(row: Optional[sqlite3.Row]) -> Dict[str, Any]:
    if row is None:
        return {}
    d = dict(row)
    for k in ("pinned", "locked", "archived"):
        d[k] = bool(d.get(k))
    return d


def get_conversation(conversation_id: str) -> Dict[str, Any]:
    with _lock, _meta_connect() as conn:
        row = conn.execute(
            "SELECT * FROM conversations WHERE conversation_id = ?", (conversation_id,)
        ).fetchone()
    meta = _row_to_meta(row)
    meta["history"] = load_history(conversation_id)
    return meta


def list_conversations(username: str = "", include_archived: bool = False) -> List[Dict[str, Any]]:
    sql = "SELECT * FROM conversations"
    params: List[Any] = []
    clauses: List[str] = []
    if username:
        clauses.append("username = ?")
        params.append(username)
    if not include_archived:
        clauses.append("archived = 0")
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY pinned DESC, updated_at DESC"
    with _lock, _meta_connect() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [_row_to_meta(r) for r in rows]


def delete_conversation(conversation_id: str) -> bool:
    clear_memory(conversation_id)
    with _lock, _meta_connect() as conn:
        cur = conn.execute("DELETE FROM conversations WHERE conversation_id = ?", (conversation_id,))
        conn.commit()
    return cur.rowcount > 0


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
            "SELECT locked FROM conversations WHERE conversation_id = ?", (conversation_id,)
        ).fetchone()
    return bool(row and row["locked"])
