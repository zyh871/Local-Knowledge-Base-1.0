"""自定义模型端点的持久化层。

用户可以接入任意 **OpenAI 兼容**的第三方服务（DeepSeek / Moonshot / 自建网关 …），
填接口地址 + API 密钥 + 模型名称即可在问答与工作流里切换使用。

## 为什么独立一个 `data/custom_models.db`

`model_endpoints` 与既有三张库（conversations / user_profile / workflow）没有任何
外键关系，混进去只会让「加一个模型」变成「迁移动辄几万行的库」。
独立库也意味着这个功能可以整体关掉（`ALLOW_CUSTOM_MODELS=false`）而不影响其他部分。

## 关于 API 密钥

**明文存储**。这是一个刻意的取舍：本系统是「跑在自己机器上的本地知识库」，
密钥本来就写在 `.env` 里；换成加密存储需要引入密钥管理，而用来解密的密钥
最终还得放在同一台机器上 —— 只是把问题挪了一层，并没有真正解决。

因此防线放在**输出侧**：
  * `model_repo` 是唯一接触明文的层
  * service 层对外一律走掩码（`sk-…abcd`），**响应里绝不出现完整密钥**
  * 界面上只显示掩码，编辑时留空表示「不修改」

⚠️ 若要把本系统部署到多人环境，应当：要么关掉 `ALLOW_CUSTOM_MODELS`，
要么给本库单独加密 —— 因为任何登录用户都能通过它读到别人填的密钥。

分层约束：本模块属 repositories 层，**不得** import services / graph / api。
"""

from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Optional

from app.core.config import get_model_settings, get_path_settings
from app.core.logging import get_logger

logger = get_logger(__name__)

__all__ = [
    "create_endpoint",
    "delete_endpoint",
    "get_endpoint",
    "list_endpoints",
    "purge_all",
    "reset_connection",
    "set_test_result",
    "update_endpoint",
]

#: 可更新字段（防止把 id / created_at 改掉）
_UPDATABLE = {
    "name",
    "base_url",
    "api_key",
    "model",
    "enabled",
    "updated_at",
}

_lock = threading.RLock()
_conn: Optional[sqlite3.Connection] = None
#: 缓存连接对应的库路径。
#: ⚠️ 不能用「在 Connection 上挂属性」的办法记 —— `sqlite3.Connection` 没有
#: `__dict__`，赋值会直接抛 AttributeError。
_conn_path: Optional[str] = None


def _db_path() -> str:
    override = getattr(get_model_settings(), "custom_models_db_path", "") or ""
    if override:
        return override
    return str(Path(get_path_settings().data_path) / "custom_models.db")


def _schema_sql() -> str:
    return """
    CREATE TABLE IF NOT EXISTS model_endpoints (
        id              TEXT PRIMARY KEY,
        name            TEXT NOT NULL,
        base_url        TEXT NOT NULL,
        api_key         TEXT NOT NULL,
        model           TEXT NOT NULL,
        enabled         INTEGER NOT NULL DEFAULT 1,
        created_by      TEXT NOT NULL DEFAULT '',
        last_test_at    REAL,
        last_test_ok    INTEGER,
        last_test_error TEXT,
        created_at      REAL NOT NULL,
        updated_at      REAL NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_endpoints_enabled ON model_endpoints(enabled);
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

    return f"me_{uuid.uuid4().hex[:12]}"


def _row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "name": row["name"],
        "base_url": row["base_url"],
        "api_key": row["api_key"],
        "model": row["model"],
        "enabled": bool(row["enabled"]),
        "created_by": row["created_by"],
        "last_test_at": row["last_test_at"],
        "last_test_ok": None if row["last_test_ok"] is None else bool(row["last_test_ok"]),
        "last_test_error": row["last_test_error"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def list_endpoints() -> list[dict[str, Any]]:
    with _lock:
        rows = _connect().execute(
            "SELECT * FROM model_endpoints ORDER BY created_at"
        ).fetchall()
    return [_row(r) for r in rows]


def get_endpoint(endpoint_id: str) -> Optional[dict[str, Any]]:
    with _lock:
        row = _connect().execute(
            "SELECT * FROM model_endpoints WHERE id = ?", (endpoint_id,)
        ).fetchone()
    return _row(row) if row else None


def create_endpoint(
    *,
    name: str,
    base_url: str,
    api_key: str,
    model: str,
    created_by: str = "",
    enabled: bool = True,
) -> dict[str, Any]:
    now = _now()
    endpoint_id = _new_id()
    with _lock:
        _connect().execute(
            "INSERT INTO model_endpoints (id, name, base_url, api_key, model, enabled,"
            " created_by, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                endpoint_id,
                name,
                base_url,
                api_key,
                model,
                1 if enabled else 0,
                created_by,
                now,
                now,
            ),
        )
        _connect().commit()
    return get_endpoint(endpoint_id)  # type: ignore[return-value]


def update_endpoint(endpoint_id: str, **fields: Any) -> Optional[dict[str, Any]]:
    patch = {k: v for k, v in fields.items() if k in _UPDATABLE}
    if "enabled" in patch:
        patch["enabled"] = 1 if patch["enabled"] else 0
    patch["updated_at"] = _now()

    with _lock:
        cur = _connect().execute(
            "UPDATE model_endpoints SET "
            + ", ".join(f"{k} = ?" for k in patch)
            + " WHERE id = ?",
            (*patch.values(), endpoint_id),
        )
        _connect().commit()
        if cur.rowcount == 0:
            return None
    return get_endpoint(endpoint_id)


def set_test_result(
    endpoint_id: str, *, ok: bool, error: str = ""
) -> Optional[dict[str, Any]]:
    """记录一次连通性测试的结果（供界面显示「上次测试」）。"""
    with _lock:
        cur = _connect().execute(
            "UPDATE model_endpoints SET last_test_at = ?, last_test_ok = ?,"
            " last_test_error = ? WHERE id = ?",
            (_now(), 1 if ok else 0, (error or "")[:500] or None, endpoint_id),
        )
        _connect().commit()
        if cur.rowcount == 0:
            return None
    return get_endpoint(endpoint_id)


def delete_endpoint(endpoint_id: str) -> bool:
    with _lock:
        cur = _connect().execute(
            "DELETE FROM model_endpoints WHERE id = ?", (endpoint_id,)
        )
        _connect().commit()
    return bool(cur.rowcount)


def purge_all() -> int:
    """清空全部端点（测试用）。"""
    with _lock:
        cur = _connect().execute("DELETE FROM model_endpoints")
        _connect().commit()
    return int(cur.rowcount or 0)
