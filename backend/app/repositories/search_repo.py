"""
联网搜索配置的存储层。

为什么要有这层
    之前搜索 API 的密钥只能写在 `backend/.env` 里，用户要：
    自己去搜供应商 → 注册 → 拿 key → 用记事本改 .env → 重启客户端。
    整个过程跨三个应用、六步，还没有任何反馈，填错格式只有下一次搜索才报错。

    这里把配置收进应用自己的 SQLite：界面上填一次即可，
    **改完立刻生效，不需要重启**（环境变量要重启才生效，这是关键差别）。

表结构很简单：一张 kv 表，主键是配置项名。
"""

from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Optional

_LOCK = threading.Lock()
_DB_PATH: Optional[Path] = None

_SCHEMA = """
CREATE TABLE IF NOT EXISTS search_config (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL DEFAULT '',
    updated_at  REAL NOT NULL DEFAULT 0
)
"""


def _db_path() -> Path:
    global _DB_PATH
    if _DB_PATH is None:
        from app.core.config import get_settings

        base = Path(get_settings().paths.data_path)
        base.mkdir(parents=True, exist_ok=True)
        _DB_PATH = base / "search.db"
    return _DB_PATH


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(_db_path(), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute(_SCHEMA)
    return conn


def get_value(key: str, default: str = "") -> str:
    with _LOCK:
        try:
            conn = _connect()
        except sqlite3.Error:
            return default
        try:
            row = conn.execute(
                "SELECT value FROM search_config WHERE key = ?", (key,)
            ).fetchone()
            return row["value"] if row else default
        finally:
            conn.close()


def set_value(key: str, value: str) -> None:
    with _LOCK:
        conn = _connect()
        try:
            conn.execute(
                "INSERT INTO search_config(key, value, updated_at) VALUES(?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value, "
                "updated_at = excluded.updated_at",
                (key, value, time.time()),
            )
            conn.commit()
        finally:
            conn.close()


def delete_value(key: str) -> None:
    with _LOCK:
        conn = _connect()
        try:
            conn.execute("DELETE FROM search_config WHERE key = ?", (key,))
            conn.commit()
        finally:
            conn.close()


def all_items() -> dict[str, Any]:
    with _LOCK:
        try:
            conn = _connect()
        except sqlite3.Error:
            return {}
        try:
            rows = conn.execute("SELECT key, value, updated_at FROM search_config").fetchall()
            return {r["key"]: {"value": r["value"], "updated_at": r["updated_at"]} for r in rows}
        finally:
            conn.close()


def reset_connection() -> None:
    """测试用：清掉缓存的库路径。"""
    global _DB_PATH
    with _LOCK:
        _DB_PATH = None
