"""AI 创作记录的持久化层。

## 为什么单独一个 `data/creations.db`

创作记录（提示词 + 参数 + 落盘的图片路径）与对话、工作流、用户画像之间没有
任何外键关系。混进既有库只会让「加一个创作页」变成「迁移一个几万行的库」；
独立库也让这个功能可以整体删掉而不影响其他部分（与 `model_repo` 同一取舍）。

## 图片本体存哪

存 `data/creations/{id}.png`，库里只记路径。

必须落盘而不是存上游 URL：智谱返回的是**带签名的临时链接（约 7 天失效）**，
直接存 URL 的话，一周后整面历史墙全是裂图 —— 而且失效是静默的，
用户只会觉得「这功能坏了」。

分层约束：本模块属 repositories 层，**不得** import services / graph / api。
"""

from __future__ import annotations

import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Optional

from app.core.config import get_path_settings
from app.core.logging import get_logger

logger = get_logger(__name__)

__all__ = [
    "create_creation",
    "delete_creation",
    "get_creation",
    "image_dir",
    "list_creations",
    "new_id",
    "purge_all",
    "reset_connection",
    "update_creation",
]

_lock = threading.RLock()
_conn: Optional[sqlite3.Connection] = None
#: 缓存连接对应的库路径（`sqlite3.Connection` 没有 `__dict__`，不能挂在连接上）
_conn_path: Optional[str] = None


def _db_path() -> str:
    return str(Path(get_path_settings().data_path) / "creations.db")


def image_dir() -> Path:
    """创作图片的存放目录（按需创建）。"""
    path = Path(get_path_settings().data_path) / "creations"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _schema_sql() -> str:
    return """
    CREATE TABLE IF NOT EXISTS creations (
        id             TEXT PRIMARY KEY,
        username       TEXT NOT NULL,
        prompt         TEXT NOT NULL,
        final_prompt   TEXT NOT NULL,
        model          TEXT NOT NULL,
        size           TEXT NOT NULL,
        ratio          TEXT NOT NULL DEFAULT '',
        style          TEXT NOT NULL DEFAULT '',
        reference_desc TEXT NOT NULL DEFAULT '',
        image_path     TEXT NOT NULL DEFAULT '',
        mime           TEXT NOT NULL DEFAULT 'image/png',
        bytes          INTEGER NOT NULL DEFAULT 0,
        elapsed_ms     INTEGER NOT NULL DEFAULT 0,
        created_at     REAL NOT NULL,
        kind           TEXT NOT NULL DEFAULT 'image',
        status         TEXT NOT NULL DEFAULT 'done',
        task_id        TEXT NOT NULL DEFAULT '',
        provider       TEXT NOT NULL DEFAULT '',
        error          TEXT NOT NULL DEFAULT ''
    );
    CREATE INDEX IF NOT EXISTS idx_creations_user
        ON creations(username, created_at DESC);
    """


#: 后续版本新增的列。老库（本功能上线初期建的）没有这些列，
#: `CREATE TABLE IF NOT EXISTS` 不会补 —— 必须显式 ALTER。
_ADDED_COLUMNS: dict[str, str] = {
    "kind": "TEXT NOT NULL DEFAULT 'image'",
    "status": "TEXT NOT NULL DEFAULT 'done'",
    "task_id": "TEXT NOT NULL DEFAULT ''",
    "provider": "TEXT NOT NULL DEFAULT ''",
    "error": "TEXT NOT NULL DEFAULT ''",
}


def _migrate(conn: sqlite3.Connection) -> None:
    existing = {row[1] for row in conn.execute("PRAGMA table_info(creations)")}
    for column, ddl in _ADDED_COLUMNS.items():
        if column not in existing:
            conn.execute(f"ALTER TABLE creations ADD COLUMN {column} {ddl}")
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


def new_id() -> str:
    return f"cr_{uuid.uuid4().hex[:12]}"


def _row(row: sqlite3.Row) -> dict[str, Any]:
    return {key: row[key] for key in row.keys()}


def create_creation(
    *,
    creation_id: str,
    username: str,
    prompt: str,
    final_prompt: str,
    model: str,
    size: str,
    ratio: str = "",
    style: str = "",
    reference_desc: str = "",
    image_path: str = "",
    mime: str = "image/png",
    size_bytes: int = 0,
    elapsed_ms: int = 0,
    kind: str = "image",
    status: str = "done",
    task_id: str = "",
    provider: str = "",
    error: str = "",
) -> dict[str, Any]:
    now = time.time()
    with _lock:
        _connect().execute(
            "INSERT INTO creations (id, username, prompt, final_prompt, model, size,"
            " ratio, style, reference_desc, image_path, mime, bytes, elapsed_ms,"
            " created_at, kind, status, task_id, provider, error)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                creation_id,
                username,
                prompt,
                final_prompt,
                model,
                size,
                ratio,
                style,
                reference_desc,
                image_path,
                mime,
                int(size_bytes),
                int(elapsed_ms),
                now,
                kind,
                status,
                task_id,
                provider,
                error,
            ),
        )
        _connect().commit()
    row = get_creation(creation_id)
    assert row is not None  # 刚插入，必然存在
    return row


#: 可由任务回写更新的字段（id / username / prompt 这些一旦写入就不该变）
_UPDATABLE = {
    "image_path",
    "mime",
    "bytes",
    "elapsed_ms",
    "status",
    "task_id",
    "provider",
    "error",
    "final_prompt",
}


def update_creation(creation_id: str, **fields: Any) -> Optional[dict[str, Any]]:
    """局部更新一条记录（视频任务完成后回写文件与状态）。

    白名单过滤：避免调用方顺手把 `username` 改掉，那等于把作品移到别人名下。
    """
    patch = {k: v for k, v in fields.items() if k in _UPDATABLE}
    if not patch:
        return get_creation(creation_id)
    with _lock:
        cur = _connect().execute(
            "UPDATE creations SET " + ", ".join(f"{k} = ?" for k in patch) + " WHERE id = ?",
            (*patch.values(), creation_id),
        )
        _connect().commit()
        if cur.rowcount == 0:
            return None
    return get_creation(creation_id)


def get_creation(creation_id: str) -> Optional[dict[str, Any]]:
    with _lock:
        row = _connect().execute(
            "SELECT * FROM creations WHERE id = ?", (creation_id,)
        ).fetchone()
    return _row(row) if row else None


def list_creations(username: str, *, limit: int = 30) -> list[dict[str, Any]]:
    with _lock:
        rows = _connect().execute(
            "SELECT * FROM creations WHERE username = ?"
            " ORDER BY created_at DESC LIMIT ?",
            (username, int(limit)),
        ).fetchall()
    return [_row(r) for r in rows]


def delete_creation(creation_id: str, username: str) -> Optional[dict[str, Any]]:
    """删除一条记录并返回被删的行（调用方据此清理图片文件）。

    `username` 参与 WHERE 条件：没有它，任何人都能靠猜 id 删掉别人的创作。
    """
    row = get_creation(creation_id)
    if row is None or row["username"] != username:
        return None
    with _lock:
        _connect().execute(
            "DELETE FROM creations WHERE id = ? AND username = ?",
            (creation_id, username),
        )
        _connect().commit()
    return row


def purge_all() -> int:
    """清空全部记录（测试用）。"""
    with _lock:
        cur = _connect().execute("DELETE FROM creations")
        _connect().commit()
    return int(cur.rowcount or 0)
