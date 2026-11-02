"""团队空间（Team）与成员角色的持久化层。

## 为什么叫 `teams` 而不是 `workspaces`

项目里**已经存在** `workflow_workspaces` 这个概念（工作区绑定本地文件夹），
它解决的是「任务跑在哪个文件夹」，与「谁能访问哪份知识」完全是两回事。
若两者同名，接口、日志、排查都会长期互相误导，因此这里刻意使用
`team` / `teams.db` 命名，与工作流的工作区彻底区分。

## 为什么独立 `data/teams.db`

团队与成员关系是一张独立的关系表，与 conversations / user_profile /
workflow 都没有外键。混进任何一张既有库都意味着「加一个团队」要迁移
几万行的业务库；独立库也让这个能力可以整体停用而不影响其他部分。

## 角色模型

    owner(4) > admin(3) > editor(2) > viewer(1)

- owner：团队唯一所有者，可删除团队、转让所有权
- admin：可改团队资料、增删成员（但**不能**授予或撤销 admin，防止越权）
- editor：可读写团队内容
- viewer：只读

分层约束：本模块属 repositories 层，**不得** import services / graph / api。
"""

from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Optional

from app.core.config import get_path_settings

__all__ = [
    "add_member",
    "count_members",
    "create_team",
    "delete_team",
    "get_member",
    "get_team",
    "list_members",
    "list_teams_for_user",
    "purge_all",
    "remove_member",
    "reset_connection",
    "update_team",
    "upsert_member",
]

#: 可更新字段（防止把 id / owner / created_at 改掉）
_UPDATABLE = {"name", "description", "updated_at"}

_lock = threading.RLock()
_conn: Optional[sqlite3.Connection] = None
_conn_path: Optional[str] = None


def _db_path() -> str:
    return str(Path(get_path_settings().data_path) / "teams.db")


def _schema_sql() -> str:
    return """
    CREATE TABLE IF NOT EXISTS teams (
        id          TEXT PRIMARY KEY,
        name        TEXT NOT NULL,
        description TEXT NOT NULL DEFAULT '',
        owner       TEXT NOT NULL,
        owner_id    TEXT NOT NULL DEFAULT '',
        created_at  REAL NOT NULL,
        updated_at  REAL NOT NULL
    );
    CREATE TABLE IF NOT EXISTS team_members (
        team_id   TEXT NOT NULL,
        username  TEXT NOT NULL,
        user_id   TEXT NOT NULL DEFAULT '',
        role      TEXT NOT NULL,
        joined_at REAL NOT NULL,
        PRIMARY KEY (team_id, username)
    );
    CREATE INDEX IF NOT EXISTS idx_teams_owner ON teams(owner);
    CREATE INDEX IF NOT EXISTS idx_team_members_user ON team_members(username);
    CREATE INDEX IF NOT EXISTS idx_team_members_uid ON team_members(user_id);
    """


def _migrate(conn: sqlite3.Connection) -> None:
    """给旧库补 `owner_id` / `user_id`，不重建表。"""
    team_cols = {r[1] for r in conn.execute("PRAGMA table_info(teams)").fetchall()}
    if "owner_id" not in team_cols:
        conn.execute("ALTER TABLE teams ADD COLUMN owner_id TEXT NOT NULL DEFAULT ''")
    member_cols = {
        r[1] for r in conn.execute("PRAGMA table_info(team_members)").fetchall()
    }
    if "user_id" not in member_cols:
        conn.execute(
            "ALTER TABLE team_members ADD COLUMN user_id TEXT NOT NULL DEFAULT ''"
        )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_team_members_uid ON team_members(user_id)"
    )
    conn.commit()


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
    _migrate(conn)
    _conn = conn
    _conn_path = path
    return conn


def reset_connection() -> None:
    """丢弃缓存连接（测试切换 DB 路径时用）。"""
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
    import uuid

    return f"team_{uuid.uuid4().hex[:12]}"


def _team_row(row: sqlite3.Row) -> dict[str, Any]:
    data = dict(row)
    data["member_count"] = int(data.get("member_count") or 0)
    data["my_role"] = data.get("my_role") or ""
    return data


def _member_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "team_id": row["team_id"],
        "username": row["username"],
        "user_id": row["user_id"] or "",
        "role": row["role"],
        "joined_at": row["joined_at"],
    }


# ---------------------------------------------------------------------------
#  团队
# ---------------------------------------------------------------------------


def create_team(
    *, name: str, description: str = "", owner: str, owner_id: str = ""
) -> dict[str, Any]:
    now = _now()
    team_id = _new_id()
    with _lock:
        conn = _connect()
        conn.execute(
            "INSERT INTO teams (id, name, description, owner, owner_id,"
            " created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (team_id, name, description, owner, owner_id, now, now),
        )
        conn.commit()
    return get_team(team_id)  # type: ignore[return-value]


def get_team(team_id: str) -> Optional[dict[str, Any]]:
    with _lock:
        row = _connect().execute(
            "SELECT t.*,"
            " (SELECT COUNT(*) FROM team_members m WHERE m.team_id = t.id)"
            " AS member_count"
            " FROM teams t WHERE t.id = ?",
            (team_id,),
        ).fetchone()
    return _team_row(row) if row else None


def list_teams_for_user(username: str) -> list[dict[str, Any]]:
    """列出某用户加入的团队（含「我的角色」与成员数）。"""
    with _lock:
        rows = _connect().execute(
            "SELECT t.*, me.role AS my_role,"
            " (SELECT COUNT(*) FROM team_members m WHERE m.team_id = t.id)"
            " AS member_count"
            " FROM teams t JOIN team_members me ON me.team_id = t.id"
            " WHERE me.username = ?"
            " ORDER BY t.updated_at DESC",
            (username,),
        ).fetchall()
    return [_team_row(r) for r in rows]


def update_team(team_id: str, **fields: Any) -> Optional[dict[str, Any]]:
    patch = {k: v for k, v in fields.items() if k in _UPDATABLE}
    if not patch:
        return get_team(team_id)
    patch["updated_at"] = _now()
    with _lock:
        conn = _connect()
        cur = conn.execute(
            "UPDATE teams SET " + ", ".join(f"{k} = ?" for k in patch) + " WHERE id = ?",
            (*patch.values(), team_id),
        )
        conn.commit()
        if cur.rowcount == 0:
            return None
    return get_team(team_id)


def delete_team(team_id: str) -> bool:
    with _lock:
        conn = _connect()
        conn.execute("DELETE FROM team_members WHERE team_id = ?", (team_id,))
        cur = conn.execute("DELETE FROM teams WHERE id = ?", (team_id,))
        conn.commit()
    return bool(cur.rowcount)


# ---------------------------------------------------------------------------
#  成员
# ---------------------------------------------------------------------------


def list_members(team_id: str) -> list[dict[str, Any]]:
    with _lock:
        rows = _connect().execute(
            "SELECT * FROM team_members WHERE team_id = ? ORDER BY joined_at",
            (team_id,),
        ).fetchall()
    return [_member_row(r) for r in rows]


def get_member(team_id: str, username: str) -> Optional[dict[str, Any]]:
    with _lock:
        row = _connect().execute(
            "SELECT * FROM team_members WHERE team_id = ? AND username = ?",
            (team_id, username),
        ).fetchone()
    return _member_row(row) if row else None


def upsert_member(
    team_id: str,
    username: str,
    role: str,
    *,
    joined_at: Optional[float] = None,
    user_id: str = "",
) -> dict[str, Any]:
    """新增或改角色（保留首次加入时间）。"""
    now = joined_at if joined_at is not None else _now()
    with _lock:
        conn = _connect()
        conn.execute(
            "INSERT INTO team_members (team_id, username, user_id, role, joined_at)"
            " VALUES (?, ?, ?, ?, ?)"
            " ON CONFLICT(team_id, username) DO UPDATE SET"
            " role = excluded.role, user_id = excluded.user_id",
            (team_id, username, user_id, role, now),
        )
        conn.commit()
    return get_member(team_id, username)  # type: ignore[return-value]


#: `add_member` 的语义别名：插入或更新成员。
add_member = upsert_member


def remove_member(team_id: str, username: str) -> bool:
    with _lock:
        conn = _connect()
        cur = conn.execute(
            "DELETE FROM team_members WHERE team_id = ? AND username = ?",
            (team_id, username),
        )
        conn.commit()
    return bool(cur.rowcount)


def count_members(team_id: str) -> int:
    with _lock:
        row = _connect().execute(
            "SELECT COUNT(*) AS n FROM team_members WHERE team_id = ?",
            (team_id,),
        ).fetchone()
    return int(row["n"]) if row else 0


# ---------------------------------------------------------------------------
#  user_id 迁移与转让
# ---------------------------------------------------------------------------


def list_all_teams() -> list[dict[str, Any]]:
    with _lock:
        rows = _connect().execute("SELECT * FROM teams").fetchall()
    return [dict(r) for r in rows]


def list_all_members() -> list[dict[str, Any]]:
    with _lock:
        rows = _connect().execute("SELECT * FROM team_members").fetchall()
    return [_member_row(r) for r in rows]


def set_team_owner_id(team_id: str, owner_id: str) -> None:
    with _lock:
        conn = _connect()
        conn.execute(
            "UPDATE teams SET owner_id = ?, updated_at = ? WHERE id = ?",
            (owner_id, _now(), team_id),
        )
        conn.commit()


def set_member_user_id(team_id: str, username: str, user_id: str) -> None:
    with _lock:
        conn = _connect()
        conn.execute(
            "UPDATE team_members SET user_id = ?"
            " WHERE team_id = ? AND username = ?",
            (user_id, team_id, username),
        )
        conn.commit()


def get_member_by_user_id(team_id: str, user_id: str) -> Optional[dict[str, Any]]:
    with _lock:
        row = _connect().execute(
            "SELECT * FROM team_members WHERE team_id = ? AND user_id = ?",
            (team_id, user_id),
        ).fetchone()
    return _member_row(row) if row else None


def remove_member_by_user_id(team_id: str, user_id: str) -> bool:
    with _lock:
        conn = _connect()
        cur = conn.execute(
            "DELETE FROM team_members WHERE team_id = ? AND user_id = ?",
            (team_id, user_id),
        )
        conn.commit()
    return bool(cur.rowcount)


def update_member_role_by_user_id(team_id: str, user_id: str, role: str) -> bool:
    with _lock:
        conn = _connect()
        cur = conn.execute(
            "UPDATE team_members SET role = ? WHERE team_id = ? AND user_id = ?",
            (role, team_id, user_id),
        )
        conn.commit()
    return bool(cur.rowcount)


def transfer_owner(
    *,
    team_id: str,
    owner_id: str,
    owner_username: str,
    actor_user_id: str,
) -> None:
    """把 owner 转给目标成员，原 owner 降为 admin。"""
    with _lock:
        conn = _connect()
        now = _now()
        conn.execute(
            "UPDATE teams SET owner = ?, owner_id = ?, updated_at = ? WHERE id = ?",
            (owner_username, owner_id, now, team_id),
        )
        conn.execute(
            "UPDATE team_members SET role = 'owner' WHERE team_id = ? AND user_id = ?",
            (team_id, owner_id),
        )
        conn.execute(
            "UPDATE team_members SET role = 'admin' WHERE team_id = ? AND user_id = ?",
            (team_id, actor_user_id),
        )
        conn.commit()


def list_teams_for_user_id(user_id: str) -> list[dict[str, Any]]:
    with _lock:
        rows = _connect().execute(
            "SELECT t.*, me.role AS my_role,"
            " (SELECT COUNT(*) FROM team_members m WHERE m.team_id = t.id)"
            " AS member_count"
            " FROM teams t JOIN team_members me ON me.team_id = t.id"
            " WHERE me.user_id = ?"
            " ORDER BY t.updated_at DESC",
            (user_id,),
        ).fetchall()
    return [_team_row(r) for r in rows]


def purge_all() -> int:
    """清空全部团队与成员（测试用）。"""
    with _lock:
        conn = _connect()
        conn.execute("DELETE FROM team_members")
        cur = conn.execute("DELETE FROM teams")
        conn.commit()
    return int(cur.rowcount or 0)
