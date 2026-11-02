"""身份数据层：用户与邀请令牌。

独立 `data/identity.db`：
  * `users` 是**唯一**的身份真相源（id 不可变，username 仅登录名）
  * `invitations` 存一次性令牌的 **sha256 哈希**，明文只在创建时返回一次

分层约束：repositories 只能依赖 core，不得 import services/api。
密码哈希由上层（services）计算后传入，避免 core.security ↔ repositories 循环依赖。
"""

from __future__ import annotations

import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Optional

from app.core.config import get_path_settings

__all__ = [
    "count_users",
    "create_invitation",
    "create_user",
    "get_by_id",
    "get_by_username",
    "get_invitation_by_hash",
    "list_invitations",
    "list_users",
    "mark_invitation_used",
    "purge_all",
    "reset_connection",
    "update_user",
]

_lock = threading.RLock()
_conn: Optional[sqlite3.Connection] = None
_conn_path: Optional[str] = None

_USER_UPDATABLE = {"display_name", "global_role", "status", "password_hash", "updated_at"}


def _db_path() -> str:
    return str(Path(get_path_settings().data_path) / "identity.db")


def _schema_sql() -> str:
    return """
    CREATE TABLE IF NOT EXISTS users (
        id            TEXT PRIMARY KEY,
        username      TEXT NOT NULL UNIQUE,
        password_hash TEXT NOT NULL,
        display_name  TEXT NOT NULL DEFAULT '',
        global_role   TEXT NOT NULL DEFAULT 'user',
        status        TEXT NOT NULL DEFAULT 'active',
        created_at    REAL NOT NULL,
        updated_at    REAL NOT NULL
    );
    CREATE TABLE IF NOT EXISTS invitations (
        id         TEXT PRIMARY KEY,
        token_hash TEXT NOT NULL UNIQUE,
        kind       TEXT NOT NULL,
        team_id    TEXT,
        role       TEXT,
        created_by TEXT NOT NULL DEFAULT '',
        expires_at REAL NOT NULL,
        used_at    REAL,
        used_by    TEXT,
        created_at REAL NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_users_status ON users(status);
    CREATE INDEX IF NOT EXISTS idx_invitations_expires ON invitations(expires_at);
    """


def _connect() -> sqlite3.Connection:
    global _conn, _conn_path
    path = _db_path()
    if _conn is not None and _conn_path == path:
        return _conn
    if _conn is not None:
        try:
            _conn.close()
        except Exception:  # noqa: BLE001
            pass
        _conn = None
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(_schema_sql())
    conn.commit()
    _conn = conn
    _conn_path = path
    return conn


def reset_connection() -> None:
    global _conn, _conn_path
    with _lock:
        if _conn is not None:
            try:
                _conn.close()
            except Exception:  # noqa: BLE001
                pass
        _conn = None
        _conn_path = None


def _now() -> float:
    return time.time()


def _new_id() -> str:
    return f"usr_{uuid.uuid4().hex[:12]}"


def _user_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "username": row["username"],
        "password_hash": row["password_hash"],
        "display_name": row["display_name"] or "",
        "global_role": row["global_role"] or "user",
        "status": row["status"] or "active",
        "disabled": (row["status"] or "active") != "active",
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


# ---------------------------------------------------------------------------
#  用户
# ---------------------------------------------------------------------------


def create_user(
    *,
    username: str,
    password_hash: str,
    display_name: str = "",
    global_role: str = "user",
    status: str = "active",
    user_id: Optional[str] = None,
) -> dict[str, Any]:
    now = _now()
    uid = user_id or _new_id()
    with _lock:
        conn = _connect()
        conn.execute(
            "INSERT INTO users (id, username, password_hash, display_name,"
            " global_role, status, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                uid,
                username,
                password_hash,
                display_name or username,
                global_role,
                status,
                now,
                now,
            ),
        )
        conn.commit()
    return get_by_id(uid)  # type: ignore[return-value]


def get_by_id(user_id: str) -> Optional[dict[str, Any]]:
    with _lock:
        row = _connect().execute(
            "SELECT * FROM users WHERE id = ?", (user_id,)
        ).fetchone()
    return _user_row(row) if row else None


def get_by_username(username: str) -> Optional[dict[str, Any]]:
    with _lock:
        row = _connect().execute(
            "SELECT * FROM users WHERE username = ? COLLATE NOCASE", (username,)
        ).fetchone()
    return _user_row(row) if row else None


def list_users() -> list[dict[str, Any]]:
    with _lock:
        rows = _connect().execute(
            "SELECT * FROM users ORDER BY created_at"
        ).fetchall()
    return [_user_row(r) for r in rows]


def update_user(user_id: str, **fields: Any) -> Optional[dict[str, Any]]:
    patch = {k: v for k, v in fields.items() if k in _USER_UPDATABLE}
    if not patch:
        return get_by_id(user_id)
    patch["updated_at"] = _now()
    with _lock:
        conn = _connect()
        cur = conn.execute(
            "UPDATE users SET " + ", ".join(f"{k} = ?" for k in patch) + " WHERE id = ?",
            (*patch.values(), user_id),
        )
        conn.commit()
        if cur.rowcount == 0:
            return None
    return get_by_id(user_id)


def count_users() -> int:
    with _lock:
        row = _connect().execute("SELECT COUNT(*) AS n FROM users").fetchone()
    return int(row["n"]) if row else 0


# ---------------------------------------------------------------------------
#  邀请
# ---------------------------------------------------------------------------


def create_invitation(
    *,
    token_hash: str,
    kind: str,
    expires_at: float,
    team_id: Optional[str] = None,
    role: str = "",
    created_by: str = "",
) -> dict[str, Any]:
    inv_id = f"inv_{uuid.uuid4().hex[:12]}"
    now = _now()
    with _lock:
        conn = _connect()
        conn.execute(
            "INSERT INTO invitations (id, token_hash, kind, team_id, role,"
            " created_by, expires_at, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (inv_id, token_hash, kind, team_id, role, created_by, expires_at, now),
        )
        conn.commit()
    return {
        "id": inv_id,
        "kind": kind,
        "team_id": team_id,
        "role": role,
        "created_by": created_by,
        "expires_at": expires_at,
        "used_at": None,
        "used_by": None,
        "created_at": now,
    }


def get_invitation_by_hash(token_hash: str) -> Optional[dict[str, Any]]:
    with _lock:
        row = _connect().execute(
            "SELECT * FROM invitations WHERE token_hash = ?", (token_hash,)
        ).fetchone()
    if not row:
        return None
    return {
        "id": row["id"],
        "kind": row["kind"],
        "team_id": row["team_id"],
        "role": row["role"],
        "created_by": row["created_by"],
        "expires_at": row["expires_at"],
        "used_at": row["used_at"],
        "used_by": row["used_by"],
        "created_at": row["created_at"],
    }


def mark_invitation_used(invitation_id: str, used_by: str) -> bool:
    with _lock:
        conn = _connect()
        cur = conn.execute(
            "UPDATE invitations SET used_at = ?, used_by = ?"
            " WHERE id = ? AND used_at IS NULL",
            (_now(), used_by, invitation_id),
        )
        conn.commit()
    return bool(cur.rowcount)


def list_invitations(limit: int = 100) -> list[dict[str, Any]]:
    with _lock:
        rows = _connect().execute(
            "SELECT * FROM invitations ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
    return [
        {
            "id": r["id"],
            "kind": r["kind"],
            "team_id": r["team_id"],
            "role": r["role"],
            "created_by": r["created_by"],
            "expires_at": r["expires_at"],
            "used_at": r["used_at"],
            "used_by": r["used_by"],
            "created_at": r["created_at"],
        }
        for r in rows
    ]


def purge_all() -> None:
    """测试用：清空用户与邀请。"""
    with _lock:
        conn = _connect()
        conn.execute("DELETE FROM invitations")
        conn.execute("DELETE FROM users")
        conn.commit()
