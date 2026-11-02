"""社区数据层：项目、贡献者、star/watch/评论/关注。

独立 `data/community.db`。项目归属团队（team_id），成员关系用不可变 user_id；
`username` 仅作展示快照，避免用户改名后历史记录变成一串 id。
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Optional

from app.core.config import get_path_settings

__all__ = [
    "add_star",
    "add_watch",
    "count_followers",
    "count_following",
    "create_comment",
    "create_project",
    "delete_comment",
    "delete_project",
    "follow",
    "get_comment",
    "get_member",
    "get_project",
    "get_project_by_slug",
    "has_star",
    "has_watch",
    "is_following",
    "list_comments",
    "list_followers",
    "list_following",
    "list_members",
    "list_projects",
    "list_projects_by_creator",
    "publish_project",
    "purge_all",
    "remove_member",
    "remove_star",
    "remove_watch",
    "reset_connection",
    "unfollow",
    "unpublish_project",
    "update_project",
    "upsert_member",
]

_lock = threading.RLock()
_conn: Optional[sqlite3.Connection] = None
_conn_path: Optional[str] = None

_PROJECT_UPDATABLE = {
    "name",
    "slug",
    "summary",
    "description",
    "language",
    "license",
    "repo_url",
    "homepage",
    "tags",
    "visibility",
    "published_at",
    "updated_at",
}


def _db_path() -> str:
    return str(Path(get_path_settings().data_path) / "community.db")


def _schema_sql() -> str:
    return """
    CREATE TABLE IF NOT EXISTS projects (
        id            TEXT PRIMARY KEY,
        team_id       TEXT NOT NULL,
        slug          TEXT NOT NULL,
        name          TEXT NOT NULL,
        summary       TEXT NOT NULL DEFAULT '',
        description   TEXT NOT NULL DEFAULT '',
        language      TEXT NOT NULL DEFAULT '',
        license       TEXT NOT NULL DEFAULT '',
        repo_url      TEXT NOT NULL DEFAULT '',
        homepage      TEXT NOT NULL DEFAULT '',
        tags          TEXT NOT NULL DEFAULT '[]',
        visibility    TEXT NOT NULL DEFAULT 'draft',
        stars_count   INTEGER NOT NULL DEFAULT 0,
        created_by    TEXT NOT NULL DEFAULT '',
        created_by_id TEXT NOT NULL DEFAULT '',
        created_at    REAL NOT NULL,
        updated_at    REAL NOT NULL,
        published_at  REAL,
        UNIQUE(team_id, slug)
    );
    CREATE TABLE IF NOT EXISTS project_members (
        project_id TEXT NOT NULL,
        user_id    TEXT NOT NULL,
        username   TEXT NOT NULL,
        role       TEXT NOT NULL DEFAULT 'contributor',
        added_at   REAL NOT NULL,
        PRIMARY KEY (project_id, user_id)
    );
    CREATE TABLE IF NOT EXISTS project_stars (
        project_id TEXT NOT NULL,
        user_id    TEXT NOT NULL,
        created_at REAL NOT NULL,
        PRIMARY KEY (project_id, user_id)
    );
    CREATE TABLE IF NOT EXISTS project_watches (
        project_id TEXT NOT NULL,
        user_id    TEXT NOT NULL,
        created_at REAL NOT NULL,
        PRIMARY KEY (project_id, user_id)
    );
    CREATE TABLE IF NOT EXISTS project_comments (
        id         TEXT PRIMARY KEY,
        project_id TEXT NOT NULL,
        user_id    TEXT NOT NULL,
        username   TEXT NOT NULL,
        body       TEXT NOT NULL,
        created_at REAL NOT NULL,
        deleted_at REAL
    );
    CREATE TABLE IF NOT EXISTS user_follows (
        follower_id TEXT NOT NULL,
        followee_id TEXT NOT NULL,
        created_at  REAL NOT NULL,
        PRIMARY KEY (follower_id, followee_id)
    );
    CREATE INDEX IF NOT EXISTS idx_projects_visibility
        ON projects(visibility, published_at);
    CREATE INDEX IF NOT EXISTS idx_projects_team ON projects(team_id);
    CREATE INDEX IF NOT EXISTS idx_comments_project
        ON project_comments(project_id, created_at);
    CREATE INDEX IF NOT EXISTS idx_stars_user ON project_stars(user_id);
    CREATE INDEX IF NOT EXISTS idx_watches_user ON project_watches(user_id);
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


def _project_row(row: sqlite3.Row) -> dict[str, Any]:
    try:
        tags = json.loads(row["tags"] or "[]")
    except Exception:  # noqa: BLE001
        tags = []
    return {
        "id": row["id"],
        "team_id": row["team_id"],
        "slug": row["slug"],
        "name": row["name"],
        "summary": row["summary"] or "",
        "description": row["description"] or "",
        "language": row["language"] or "",
        "license": row["license"] or "",
        "repo_url": row["repo_url"] or "",
        "homepage": row["homepage"] or "",
        "tags": tags if isinstance(tags, list) else [],
        "visibility": row["visibility"] or "draft",
        "stars_count": int(row["stars_count"] or 0),
        "created_by": row["created_by"] or "",
        "created_by_id": row["created_by_id"] or "",
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "published_at": row["published_at"],
    }


# ---------------------------------------------------------------------------
#  项目
# ---------------------------------------------------------------------------


def create_project(
    *,
    team_id: str,
    slug: str,
    name: str,
    summary: str = "",
    description: str = "",
    language: str = "",
    license: str = "",
    repo_url: str = "",
    homepage: str = "",
    tags: Optional[list[str]] = None,
    created_by: str = "",
    created_by_id: str = "",
) -> dict[str, Any]:
    project_id = f"prj_{uuid.uuid4().hex[:12]}"
    now = _now()
    with _lock:
        conn = _connect()
        conn.execute(
            "INSERT INTO projects (id, team_id, slug, name, summary, description,"
            " language, license, repo_url, homepage, tags, visibility,"
            " created_by, created_by_id, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'draft', ?, ?, ?, ?)",
            (
                project_id,
                team_id,
                slug,
                name,
                summary,
                description,
                language,
                license,
                repo_url,
                homepage,
                json.dumps(tags or [], ensure_ascii=False),
                created_by,
                created_by_id,
                now,
                now,
            ),
        )
        conn.commit()
    return get_project(project_id)  # type: ignore[return-value]


def get_project(project_id: str) -> Optional[dict[str, Any]]:
    with _lock:
        row = _connect().execute(
            "SELECT * FROM projects WHERE id = ?", (project_id,)
        ).fetchone()
    return _project_row(row) if row else None


def get_project_by_slug(team_id: str, slug: str) -> Optional[dict[str, Any]]:
    with _lock:
        row = _connect().execute(
            "SELECT * FROM projects WHERE team_id = ? AND slug = ?",
            (team_id, slug),
        ).fetchone()
    return _project_row(row) if row else None


def list_projects(
    *,
    q: str = "",
    language: str = "",
    tag: str = "",
    sort: str = "recent",
    limit: int = 50,
    offset: int = 0,
) -> list[dict[str, Any]]:
    clauses = ["visibility = 'published'"]
    params: list[Any] = []
    if q.strip():
        clauses.append("(name LIKE ? OR summary LIKE ? OR description LIKE ?)")
        like = f"%{q.strip()}%"
        params.extend([like, like, like])
    if language.strip():
        clauses.append("language = ?")
        params.append(language.strip())
    if tag.strip():
        clauses.append("tags LIKE ?")
        params.append(f'%"{tag.strip()}"%')
    order = {
        "stars": "stars_count DESC, published_at DESC",
        "name": "name COLLATE NOCASE ASC",
    }.get(sort, "published_at DESC")
    sql = (
        "SELECT * FROM projects WHERE "
        + " AND ".join(clauses)
        + f" ORDER BY {order} LIMIT ? OFFSET ?"
    )
    params.extend([max(1, min(limit, 100)), max(0, offset)])
    with _lock:
        rows = _connect().execute(sql, params).fetchall()
    return [_project_row(r) for r in rows]


def list_projects_by_creator(user_id: str) -> list[dict[str, Any]]:
    with _lock:
        rows = _connect().execute(
            "SELECT * FROM projects WHERE created_by_id = ?"
            " AND visibility = 'published'"
            " ORDER BY published_at DESC",
            (user_id,),
        ).fetchall()
    return [_project_row(r) for r in rows]


def list_projects_for_user(
    user_id: str, *, limit: int = 100, offset: int = 0
) -> list[dict[str, Any]]:
    """「我的项目」：我创建的 + 我是贡献者的，**含草稿**。

    与 `list_projects`（硬编码 `visibility = 'published'`）刻意分开：
    草稿不能泄漏给无关的人，但创建者/贡献者必须看得到自己还没发布的成果，
    否则「我建了个项目」之后就再也找不回来了。
    """
    sql = (
        "SELECT * FROM projects WHERE created_by_id = ?"
        " OR id IN (SELECT project_id FROM project_members WHERE user_id = ?)"
        " ORDER BY updated_at DESC LIMIT ? OFFSET ?"
    )
    params = (user_id, user_id, max(1, min(limit, 100)), max(0, offset))
    with _lock:
        rows = _connect().execute(sql, params).fetchall()
    return [_project_row(r) for r in rows]


def update_project(project_id: str, **fields: Any) -> Optional[dict[str, Any]]:
    patch = {k: v for k, v in fields.items() if k in _PROJECT_UPDATABLE}
    if not patch:
        return get_project(project_id)
    if "tags" in patch:
        patch["tags"] = json.dumps(patch["tags"] or [], ensure_ascii=False)
    patch["updated_at"] = _now()
    with _lock:
        conn = _connect()
        cur = conn.execute(
            "UPDATE projects SET "
            + ", ".join(f"{k} = ?" for k in patch)
            + " WHERE id = ?",
            (*patch.values(), project_id),
        )
        conn.commit()
        if cur.rowcount == 0:
            return None
    return get_project(project_id)


def publish_project(project_id: str) -> Optional[dict[str, Any]]:
    return update_project(
        project_id, visibility="published", published_at=_now()
    )


def unpublish_project(project_id: str) -> Optional[dict[str, Any]]:
    return update_project(project_id, visibility="draft", published_at=None)


def delete_project(project_id: str) -> bool:
    with _lock:
        conn = _connect()
        for table in (
            "project_members",
            "project_stars",
            "project_watches",
            "project_comments",
        ):
            conn.execute(f"DELETE FROM {table} WHERE project_id = ?", (project_id,))
        cur = conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))
        conn.commit()
    return bool(cur.rowcount)


# ---------------------------------------------------------------------------
#  贡献者
# ---------------------------------------------------------------------------


def upsert_member(
    project_id: str, *, user_id: str, username: str, role: str = "contributor"
) -> dict[str, Any]:
    with _lock:
        conn = _connect()
        conn.execute(
            "INSERT INTO project_members (project_id, user_id, username, role, added_at)"
            " VALUES (?, ?, ?, ?, ?)"
            " ON CONFLICT(project_id, user_id) DO UPDATE SET"
            " role = excluded.role, username = excluded.username",
            (project_id, user_id, username, role, _now()),
        )
        conn.commit()
    return get_member(project_id, user_id)  # type: ignore[return-value]


def get_member(project_id: str, user_id: str) -> Optional[dict[str, Any]]:
    with _lock:
        row = _connect().execute(
            "SELECT * FROM project_members WHERE project_id = ? AND user_id = ?",
            (project_id, user_id),
        ).fetchone()
    if not row:
        return None
    return {
        "project_id": row["project_id"],
        "user_id": row["user_id"],
        "username": row["username"],
        "role": row["role"],
        "added_at": row["added_at"],
    }


def list_members(project_id: str) -> list[dict[str, Any]]:
    with _lock:
        rows = _connect().execute(
            "SELECT * FROM project_members WHERE project_id = ? ORDER BY added_at",
            (project_id,),
        ).fetchall()
    return [
        {
            "user_id": r["user_id"],
            "username": r["username"],
            "role": r["role"],
            "added_at": r["added_at"],
        }
        for r in rows
    ]


def remove_member(project_id: str, user_id: str) -> bool:
    with _lock:
        conn = _connect()
        cur = conn.execute(
            "DELETE FROM project_members WHERE project_id = ? AND user_id = ?",
            (project_id, user_id),
        )
        conn.commit()
    return bool(cur.rowcount)


# ---------------------------------------------------------------------------
#  Star / Watch
# ---------------------------------------------------------------------------


def add_star(project_id: str, user_id: str) -> bool:
    """幂等：第一次 star 才 +1。"""
    with _lock:
        conn = _connect()
        cur = conn.execute(
            "INSERT OR IGNORE INTO project_stars (project_id, user_id, created_at)"
            " VALUES (?, ?, ?)",
            (project_id, user_id, _now()),
        )
        if cur.rowcount:
            conn.execute(
                "UPDATE projects SET stars_count = stars_count + 1 WHERE id = ?",
                (project_id,),
            )
        conn.commit()
    return bool(cur.rowcount)


def remove_star(project_id: str, user_id: str) -> bool:
    with _lock:
        conn = _connect()
        cur = conn.execute(
            "DELETE FROM project_stars WHERE project_id = ? AND user_id = ?",
            (project_id, user_id),
        )
        if cur.rowcount:
            conn.execute(
                "UPDATE projects SET stars_count = MAX(stars_count - 1, 0)"
                " WHERE id = ?",
                (project_id,),
            )
        conn.commit()
    return bool(cur.rowcount)


def has_star(project_id: str, user_id: str) -> bool:
    with _lock:
        row = _connect().execute(
            "SELECT 1 FROM project_stars WHERE project_id = ? AND user_id = ?",
            (project_id, user_id),
        ).fetchone()
    return bool(row)


def add_watch(project_id: str, user_id: str) -> bool:
    with _lock:
        conn = _connect()
        cur = conn.execute(
            "INSERT OR IGNORE INTO project_watches (project_id, user_id, created_at)"
            " VALUES (?, ?, ?)",
            (project_id, user_id, _now()),
        )
        conn.commit()
    return bool(cur.rowcount)


def remove_watch(project_id: str, user_id: str) -> bool:
    with _lock:
        conn = _connect()
        cur = conn.execute(
            "DELETE FROM project_watches WHERE project_id = ? AND user_id = ?",
            (project_id, user_id),
        )
        conn.commit()
    return bool(cur.rowcount)


def has_watch(project_id: str, user_id: str) -> bool:
    with _lock:
        row = _connect().execute(
            "SELECT 1 FROM project_watches WHERE project_id = ? AND user_id = ?",
            (project_id, user_id),
        ).fetchone()
    return bool(row)


# ---------------------------------------------------------------------------
#  评论
# ---------------------------------------------------------------------------


def create_comment(
    project_id: str, *, user_id: str, username: str, body: str
) -> dict[str, Any]:
    comment_id = f"cmt_{uuid.uuid4().hex[:12]}"
    now = _now()
    with _lock:
        conn = _connect()
        conn.execute(
            "INSERT INTO project_comments (id, project_id, user_id, username, body,"
            " created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (comment_id, project_id, user_id, username, body, now),
        )
        conn.commit()
    return get_comment(comment_id)  # type: ignore[return-value]


def get_comment(comment_id: str) -> Optional[dict[str, Any]]:
    with _lock:
        row = _connect().execute(
            "SELECT * FROM project_comments WHERE id = ?", (comment_id,)
        ).fetchone()
    if not row:
        return None
    return {
        "id": row["id"],
        "project_id": row["project_id"],
        "user_id": row["user_id"],
        "username": row["username"],
        "body": row["body"],
        "created_at": row["created_at"],
        "deleted_at": row["deleted_at"],
    }


def list_comments(project_id: str, limit: int = 100) -> list[dict[str, Any]]:
    with _lock:
        rows = _connect().execute(
            "SELECT * FROM project_comments WHERE project_id = ?"
            " AND deleted_at IS NULL ORDER BY created_at ASC LIMIT ?",
            (project_id, max(1, min(limit, 200))),
        ).fetchall()
    return [
        {
            "id": r["id"],
            "project_id": r["project_id"],
            "user_id": r["user_id"],
            "username": r["username"],
            "body": r["body"],
            "created_at": r["created_at"],
            "deleted_at": r["deleted_at"],
        }
        for r in rows
    ]


def delete_comment(comment_id: str) -> bool:
    with _lock:
        conn = _connect()
        cur = conn.execute(
            "UPDATE project_comments SET deleted_at = ?"
            " WHERE id = ? AND deleted_at IS NULL",
            (_now(), comment_id),
        )
        conn.commit()
    return bool(cur.rowcount)


# ---------------------------------------------------------------------------
#  关注
# ---------------------------------------------------------------------------


def follow(follower_id: str, followee_id: str) -> bool:
    with _lock:
        conn = _connect()
        cur = conn.execute(
            "INSERT OR IGNORE INTO user_follows (follower_id, followee_id, created_at)"
            " VALUES (?, ?, ?)",
            (follower_id, followee_id, _now()),
        )
        conn.commit()
    return bool(cur.rowcount)


def unfollow(follower_id: str, followee_id: str) -> bool:
    with _lock:
        conn = _connect()
        cur = conn.execute(
            "DELETE FROM user_follows WHERE follower_id = ? AND followee_id = ?",
            (follower_id, followee_id),
        )
        conn.commit()
    return bool(cur.rowcount)


def is_following(follower_id: str, followee_id: str) -> bool:
    with _lock:
        row = _connect().execute(
            "SELECT 1 FROM user_follows WHERE follower_id = ? AND followee_id = ?",
            (follower_id, followee_id),
        ).fetchone()
    return bool(row)


def list_followers(user_id: str) -> list[str]:
    with _lock:
        rows = _connect().execute(
            "SELECT follower_id FROM user_follows WHERE followee_id = ?",
            (user_id,),
        ).fetchall()
    return [r["follower_id"] for r in rows]


def list_following(user_id: str) -> list[str]:
    with _lock:
        rows = _connect().execute(
            "SELECT followee_id FROM user_follows WHERE follower_id = ?",
            (user_id,),
        ).fetchall()
    return [r["followee_id"] for r in rows]


def count_followers(user_id: str) -> int:
    with _lock:
        row = _connect().execute(
            "SELECT COUNT(*) AS n FROM user_follows WHERE followee_id = ?",
            (user_id,),
        ).fetchone()
    return int(row["n"]) if row else 0


def count_following(user_id: str) -> int:
    with _lock:
        row = _connect().execute(
            "SELECT COUNT(*) AS n FROM user_follows WHERE follower_id = ?",
            (user_id,),
        ).fetchone()
    return int(row["n"]) if row else 0


def purge_all() -> None:
    with _lock:
        conn = _connect()
        for table in (
            "project_members",
            "project_stars",
            "project_watches",
            "project_comments",
            "projects",
            "user_follows",
        ):
            conn.execute(f"DELETE FROM {table}")
        conn.commit()
