"""AI 工作流的 SQLite 持久化层。

**这里是工作流的真相源**，不是缓存：步骤状态、审批记录、任务书都以本库为准。
LangGraph 的 checkpointer 只承载「单次调用内」的中间态，两者职责不重叠 ——
设计文档 §4.1 明确拒绝了「用 checkpointer 挂起 + 长连接等审批」的方案，
就是为了避免同一事实存在两份存储、两套更新路径。

为什么独立一个 `data/workflow.db`：
  * `conversations.db` 的表结构服务于「顺序追加的消息流」，`GET /conversations`
    的返回结构还是对外契约，加字段会撞契约测试；
  * 步骤状态是**状态机数据**，塞进消息 JSON 会让「查所有等待审批的 run」
    退化成全表扫 + 反序列化 —— 而这是人工审查模式的核心查询。

分层约束：本模块属 repositories 层，**不得** import services / graph / api。
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Optional

from app.core.config import get_workflow_settings
from app.core.logging import get_logger

logger = get_logger(__name__)

__all__ = [
    "RUN_STATUSES",
    "STEP_STATUSES",
    "STEP_KINDS",
    "create_workspace",
    "list_workspaces",
    "get_workspace",
    "rename_workspace",
    "delete_workspace",
    "create_run",
    "list_runs",
    "get_run",
    "update_run",
    "delete_run",
    "replace_steps",
    "list_steps",
    "get_step",
    "update_step",
    "add_run_file",
    "list_run_files",
    "get_run_file",
    "delete_run_file",
    "workspace_dir",
    "reset_connection",
]

# ── 枚举（与设计文档 §4 / §6.5 / §7 一致）─────────────────────────────

RUN_STATUSES = (
    "draft",
    "clarifying",
    "planning",
    "awaiting_approval",
    # `approved` 不在设计文档最初的状态列表里，是 P2 补的：
    # 任务书批准后、执行器尚未接入（P3）时若直接置 `running`，
    # 会对外宣称「正在执行」而实际上什么都没发生 —— 那是个谎。
    # 单独一个状态既诚实，也让 P3 的迁移路径清晰：approved → running。
    "approved",
    "running",
    "paused",
    "done",
    "failed",
    "cancelled",
)

STEP_STATUSES = (
    "pending",
    "awaiting_approval",
    "approved",
    "running",
    "done",
    "failed",
    "skipped",
    "rejected",
)

STEP_KINDS = ("tool", "llm", "synthesis", "unplanned")

RUN_MODES = ("manual", "auto")

# 运行中（即未终结）的状态集合 —— 侧边栏与「待审批列表」都靠它筛选
ACTIVE_RUN_STATUSES = (
    "draft",
    "clarifying",
    "planning",
    "awaiting_approval",
    "approved",
    "running",
    "paused",
)


# ── 连接管理 ────────────────────────────────────────────────────────

_conn: Optional[sqlite3.Connection] = None
_conn_path: Optional[str] = None
_lock = threading.RLock()


def _now() -> float:
    return time.time()


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


def new_id(prefix: str) -> str:
    """生成带前缀的短 ID。

    公开出来是为了让 service 层**先拿到 ID 再落盘** ——
    文件名里嵌入 file_id 时可避免「先写临时名、再改名对齐」的中间态。
    """
    return _new_id(prefix)


def _schema_sql() -> str:
    return """
    CREATE TABLE IF NOT EXISTS workspaces (
        id           TEXT PRIMARY KEY,
        name         TEXT NOT NULL,
        owner        TEXT NOT NULL,
        visibility   TEXT NOT NULL DEFAULT 'shared',
        local_dir    TEXT,
        created_at   REAL NOT NULL,
        updated_at   REAL NOT NULL
    );

    CREATE TABLE IF NOT EXISTS runs (
        id             TEXT PRIMARY KEY,
        workspace_id   TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
        username       TEXT NOT NULL,
        title          TEXT NOT NULL DEFAULT '',
        raw_input      TEXT NOT NULL DEFAULT '',
        status         TEXT NOT NULL,
        mode           TEXT NOT NULL,
        model          TEXT NOT NULL DEFAULT '',
        force_search   INTEGER NOT NULL DEFAULT 0,
        clarify_round  INTEGER NOT NULL DEFAULT 0,
        plan_json      TEXT,
        usage_json     TEXT,
        summary        TEXT,
        error          TEXT,
        created_at     REAL NOT NULL,
        updated_at     REAL NOT NULL
    );

    CREATE TABLE IF NOT EXISTS steps (
        id                 TEXT PRIMARY KEY,
        run_id             TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
        idx                INTEGER NOT NULL,
        kind               TEXT NOT NULL,
        title              TEXT NOT NULL DEFAULT '',
        action_json        TEXT NOT NULL DEFAULT '{}',
        requires_approval  INTEGER NOT NULL DEFAULT 0,
        status             TEXT NOT NULL DEFAULT 'pending',
        output_json        TEXT,
        error              TEXT,
        retry_count        INTEGER NOT NULL DEFAULT 0,
        approved_by        TEXT,
        approved_at        REAL,
        started_at         REAL,
        ended_at           REAL,
        UNIQUE (run_id, idx)
    );

    CREATE TABLE IF NOT EXISTS run_files (
        id             TEXT PRIMARY KEY,
        run_id         TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
        filename       TEXT NOT NULL,
        stored_path    TEXT NOT NULL,
        size_bytes     INTEGER NOT NULL DEFAULT 0,
        sha256         TEXT NOT NULL DEFAULT '',
        kind           TEXT NOT NULL DEFAULT 'input',
        parsed_chars   INTEGER,
        ttl_expires_at REAL NOT NULL,
        created_at     REAL NOT NULL
    );

    CREATE INDEX IF NOT EXISTS idx_runs_username ON runs(username, updated_at DESC);
    CREATE INDEX IF NOT EXISTS idx_runs_status   ON runs(status);
    CREATE INDEX IF NOT EXISTS idx_runs_ws       ON runs(workspace_id);
    CREATE INDEX IF NOT EXISTS idx_steps_run     ON steps(run_id, idx);
    CREATE INDEX IF NOT EXISTS idx_files_run     ON run_files(run_id);
    CREATE INDEX IF NOT EXISTS idx_files_ttl     ON run_files(ttl_expires_at);
    """


# 增量补列。`CREATE TABLE IF NOT EXISTS` 对**已存在**的表不会生效，
# 因此后续分期新增字段必须写在这里，否则老库上会出现
# `no such column` —— 而且只在老库上复现，新库一切正常，极难排查。
_COLUMN_MIGRATIONS: dict[str, dict[str, str]] = {
    "runs": {
        # 驳回任务书时用户写的意见，供下一轮重新规划参考
        "reject_comment": "TEXT",
        # 澄清轮次：{"rounds": [{round, questions, answers}], "skipped": bool}
        "clarify_json": "TEXT",
        # token 用量累计：{"phases": {clarify: {...}, plan: {...}}, "phase_total": {...}}
        "usage_json": "TEXT",
    },
    "workspaces": {
        # 绑定到用户本机的一个文件夹（空 = 用服务端分配的隐藏目录）
        "local_dir": "TEXT",
    },
}


def _ensure_columns(conn: sqlite3.Connection) -> None:
    for table, columns in _COLUMN_MIGRATIONS.items():
        existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
        for name, ddl in columns.items():
            if name not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")
                logger.info(f"workflow.db 迁移：{table} 新增列 {name}")


def _connect() -> sqlite3.Connection:
    """惰性建立连接；配置里的库路径变了会自动重连（测试需要）。"""
    global _conn, _conn_path
    path = get_workflow_settings().resolved_db_path
    if _conn is not None and _conn_path == path:
        return _conn

    if _conn is not None:
        try:
            _conn.close()
        except Exception:  # noqa: BLE001 - 关闭旧连接失败不影响新建
            pass
        _conn = None

    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False, timeout=30)
    conn.row_factory = sqlite3.Row
    # WAL：读写并发更好；外键必须**逐连接**开启，否则 ON DELETE CASCADE 静默失效
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(_schema_sql())
    _ensure_columns(conn)
    conn.commit()

    _conn = conn
    _conn_path = path
    logger.debug(f"workflow.db 就绪: {path}")
    return conn


def reset_connection() -> None:
    """测试用：丢弃连接（配合切库路径）。"""
    global _conn, _conn_path
    with _lock:
        if _conn is not None:
            try:
                _conn.close()
            except Exception:  # noqa: BLE001
                pass
        _conn = None
        _conn_path = None


# ── 行 → dict ──────────────────────────────────────────────────────


def _json_load(raw: Any, fallback: Any = None) -> Any:
    if raw in (None, ""):
        return fallback
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        logger.warning("工作流 JSON 字段解析失败，已忽略该字段")
        return fallback


def _json_dump(value: Any) -> Optional[str]:
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False, default=str)


def _workspace_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "name": row["name"],
        "owner": row["owner"],
        "visibility": row["visibility"],
        #: 绑定的本地文件夹（空 = 服务端分配的隐藏目录）
        "local_dir": row["local_dir"] or "",
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _step_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "run_id": row["run_id"],
        "idx": row["idx"],
        "kind": row["kind"],
        "title": row["title"],
        "action": _json_load(row["action_json"], {}) or {},
        "requires_approval": bool(row["requires_approval"]),
        "status": row["status"],
        "output": _json_load(row["output_json"]),
        "error": row["error"],
        "retry_count": row["retry_count"],
        "approved_by": row["approved_by"],
        "approved_at": row["approved_at"],
        "started_at": row["started_at"],
        "ended_at": row["ended_at"],
    }


def _run_row(row: sqlite3.Row, *, with_steps: bool = False) -> dict[str, Any]:
    data = {
        "id": row["id"],
        "workspace_id": row["workspace_id"],
        "username": row["username"],
        "title": row["title"],
        "raw_input": row["raw_input"],
        "status": row["status"],
        "mode": row["mode"],
        "model": row["model"],
        "force_search": bool(row["force_search"]),
        "clarify_round": row["clarify_round"],
        "plan": _json_load(row["plan_json"]),
        # 内部原始结构（phases/phase_total）。**刻意不叫 usage** ——
        # 响应字段 `usage` 是服务层算好的汇总，形状不同；用同一个名字
        # 会让不经服务层的列表接口把内部形状直接下发，撞 schema 校验。
        "usage_raw": _json_load(row["usage_json"]) or {},
        "summary": row["summary"],
        "error": row["error"],
        "reject_comment": row["reject_comment"],
        "clarify": _json_load(row["clarify_json"]) or {"rounds": [], "skipped": False},
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }
    if with_steps:
        data["steps"] = list_steps(row["id"])
    return data


def _file_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "run_id": row["run_id"],
        "filename": row["filename"],
        "stored_path": row["stored_path"],
        "size_bytes": row["size_bytes"],
        "sha256": row["sha256"],
        "kind": row["kind"],
        "parsed_chars": row["parsed_chars"],
        "ttl_expires_at": row["ttl_expires_at"],
        "created_at": row["created_at"],
    }


# ── 工作区 ──────────────────────────────────────────────────────────


def workspace_dir(workspace_id: str) -> str:
    """工作区磁盘根目录（逻辑容器 → 实际存储的落点）。"""
    return str(Path(get_workflow_settings().resolved_workspace_root) / workspace_id)


def create_workspace(name: str, owner: str, local_dir: str = "") -> dict[str, Any]:
    now = _now()
    wid = _new_id("ws")
    with _lock, _connect() as conn:
        conn.execute(
            "INSERT INTO workspaces (id, name, owner, visibility, local_dir,"
            " created_at, updated_at) VALUES (?, ?, ?, 'shared', ?, ?, ?)",
            (wid, name, owner, local_dir or None, now, now),
        )
    return get_workspace(wid)  # type: ignore[return-value]


def list_workspaces() -> list[dict[str, Any]]:
    """全局共享：所有登录用户可见，因此不按 username 过滤。

    统计 run 数与文件数供选择器直接展示，省掉前端 N+1 请求。
    """
    with _lock, _connect() as conn:
        rows = conn.execute(
            """
            SELECT w.*,
                   (SELECT COUNT(*) FROM runs r WHERE r.workspace_id = w.id) AS run_count,
                   (SELECT COUNT(*) FROM run_files f
                      JOIN runs r2 ON r2.id = f.run_id
                     WHERE r2.workspace_id = w.id) AS file_count
              FROM workspaces w
             ORDER BY w.updated_at DESC
            """
        ).fetchall()
    out = []
    for row in rows:
        item = _workspace_row(row)
        item["run_count"] = row["run_count"]
        item["file_count"] = row["file_count"]
        out.append(item)
    return out


def get_workspace(workspace_id: str) -> Optional[dict[str, Any]]:
    with _lock, _connect() as conn:
        row = conn.execute(
            "SELECT * FROM workspaces WHERE id = ?", (workspace_id,)
        ).fetchone()
    return _workspace_row(row) if row else None


def rename_workspace(workspace_id: str, name: str) -> Optional[dict[str, Any]]:
    with _lock, _connect() as conn:
        cur = conn.execute(
            "UPDATE workspaces SET name = ?, updated_at = ? WHERE id = ?",
            (name, _now(), workspace_id),
        )
        if cur.rowcount == 0:
            return None
    return get_workspace(workspace_id)


def set_workspace_local_dir(
    workspace_id: str, local_dir: str
) -> Optional[dict[str, Any]]:
    """绑定 / 解绑本地文件夹（空串 = 解绑）。"""
    with _lock, _connect() as conn:
        cur = conn.execute(
            "UPDATE workspaces SET local_dir = ?, updated_at = ? WHERE id = ?",
            (local_dir or None, _now(), workspace_id),
        )
        if cur.rowcount == 0:
            return None
    return get_workspace(workspace_id)


def delete_workspace(workspace_id: str) -> list[str]:
    """删除工作区及其全部 run/step/file。

    返回被删除的 run_files 落盘路径，交给 service 层删磁盘文件 ——
    数据库层不碰文件系统，避免事务回滚后文件已被删的中间态。
    """
    with _lock, _connect() as conn:
        paths = [
            row["stored_path"]
            for row in conn.execute(
                "SELECT f.stored_path FROM run_files f"
                " JOIN runs r ON r.id = f.run_id WHERE r.workspace_id = ?",
                (workspace_id,),
            ).fetchall()
        ]
        # runs / steps / run_files 依赖 ON DELETE CASCADE（已开启外键）
        conn.execute("DELETE FROM workspaces WHERE id = ?", (workspace_id,))
    return paths


# ── 运行记录 ────────────────────────────────────────────────────────


def create_run(
    *,
    workspace_id: str,
    username: str,
    raw_input: str,
    title: str = "",
    mode: str = "manual",
    model: str = "",
    force_search: bool = False,
    status: str = "draft",
) -> dict[str, Any]:
    if mode not in RUN_MODES:
        raise ValueError(f"未知的执行模式: {mode}")
    if status not in RUN_STATUSES:
        raise ValueError(f"未知的 run 状态: {status}")
    now = _now()
    rid = _new_id("run")
    with _lock, _connect() as conn:
        conn.execute(
            "INSERT INTO runs (id, workspace_id, username, title, raw_input, status,"
            " mode, model, force_search, clarify_round, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)",
            (
                rid,
                workspace_id,
                username,
                title or raw_input[:40],
                raw_input,
                status,
                mode,
                model,
                1 if force_search else 0,
                now,
                now,
            ),
        )
        conn.execute(
            "UPDATE workspaces SET updated_at = ? WHERE id = ?", (now, workspace_id)
        )
    return get_run(rid)  # type: ignore[return-value]


def list_runs(
    *,
    username: Optional[str] = None,
    workspace_id: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
) -> list[dict[str, Any]]:
    sql = "SELECT * FROM runs WHERE 1 = 1"
    params: list[Any] = []
    if username:
        sql += " AND username = ?"
        params.append(username)
    if workspace_id:
        sql += " AND workspace_id = ?"
        params.append(workspace_id)
    if status:
        sql += " AND status = ?"
        params.append(status)
    sql += " ORDER BY updated_at DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])

    with _lock, _connect() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [_run_row(row) for row in rows]


def get_run(run_id: str, *, with_steps: bool = True) -> Optional[dict[str, Any]]:
    with _lock, _connect() as conn:
        row = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
    return _run_row(row, with_steps=with_steps) if row else None


_RUN_UPDATABLE = {
    "title",
    "status",
    "mode",
    "model",
    "force_search",
    "clarify_round",
    "plan_json",
    "usage_json",
    "summary",
    "error",
    "raw_input",
    "reject_comment",
    "clarify_json",
}


def add_run_usage(run_id: str, phase: str, usage: dict[str, Any]) -> Optional[dict[str, Any]]:
    """把某个相位的 token 用量**累加**到 `runs.usage_json`。

    ⚠️ 必须累加，不能覆盖：一个 run 的澄清可能有多轮、规划可能被驳回重来，
    每次都是一次真实计费调用。覆盖会只剩最后一次 —— 那个数字系统性偏小，
    比「没有这个数」更糟：用户会据此以为很便宜。
    """
    if not usage:
        return None
    record = get_run(run_id, with_steps=False)
    if not record:
        return None

    stored = record.get("usage_raw") or {}
    phases = dict(stored.get("phases") or {})
    bucket = dict(phases.get(phase) or {})

    for key, value in usage.items():
        if key.startswith("_"):
            continue
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            bucket[key] = int(bucket.get(key) or 0) + int(value)
        else:
            # 非数值字段（如模型名）保留最后一次，便于事后核对是哪次调用
            bucket[key] = value
    # ★ 调用次数必须单独记：token 是累加的，但「累加了多少次」这个信息
    #   累加完就丢了 —— 同一个相位调三次和调一次在桶里长得一模一样，
    #   只按桶计数会让澄清多轮 / 规划驳回重来的成本系统性偏小。
    bucket["_calls"] = int(bucket.get("_calls") or 0) + 1
    phases[phase] = bucket

    totals: dict[str, int] = {}
    calls = 0
    for bucket_value in phases.values():
        calls += int(bucket_value.get("_calls") or 0)
        for key, value in bucket_value.items():
            if key.startswith("_"):
                continue
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                totals[key] = totals.get(key, 0) + int(value)

    return update_run(
        run_id,
        usage_json=_json_dump({"phases": phases, "phase_total": totals, "phase_calls": calls}),
    )


def update_run(run_id: str, **fields: Any) -> Optional[dict[str, Any]]:
    """局部更新。`plan` 以 dict 传入，内部转 JSON 落库。"""
    patch = dict(fields)
    if "plan" in patch:
        patch["plan_json"] = _json_dump(patch.pop("plan"))
    if "clarify" in patch:
        patch["clarify_json"] = _json_dump(patch.pop("clarify"))
    if "force_search" in patch:
        patch["force_search"] = 1 if patch["force_search"] else 0

    invalid = set(patch) - _RUN_UPDATABLE
    if invalid:
        raise ValueError(f"不可更新的 run 字段: {sorted(invalid)}")
    if not patch:
        return get_run(run_id)

    if "status" in patch and patch["status"] not in RUN_STATUSES:
        raise ValueError(f"未知的 run 状态: {patch['status']}")
    if "mode" in patch and patch["mode"] not in RUN_MODES:
        raise ValueError(f"未知的执行模式: {patch['mode']}")

    patch["updated_at"] = _now()
    assignments = ", ".join(f"{key} = ?" for key in patch)
    with _lock, _connect() as conn:
        cur = conn.execute(
            f"UPDATE runs SET {assignments} WHERE id = ?",
            (*patch.values(), run_id),
        )
        if cur.rowcount == 0:
            return None
    return get_run(run_id)


def delete_run(run_id: str) -> list[str]:
    """删除 run 及其 steps/files，返回需清理的落盘文件路径。"""
    with _lock, _connect() as conn:
        paths = [
            row["stored_path"]
            for row in conn.execute(
                "SELECT stored_path FROM run_files WHERE run_id = ?", (run_id,)
            ).fetchall()
        ]
        conn.execute("DELETE FROM runs WHERE id = ?", (run_id,))
    return paths


# ── 步骤 ────────────────────────────────────────────────────────────


def _step_rows(run_id: str, steps: list[dict[str, Any]]) -> list[tuple]:
    """把步骤字典列表转成 INSERT 用的行元组。"""
    rows = []
    for offset, step in enumerate(steps, start=1):
        idx = int(step.get("idx") or offset)
        kind = step.get("kind") or "llm"
        if kind not in STEP_KINDS:
            raise ValueError(f"未知的步骤类型: {kind}")
        status = step.get("status") or "pending"
        if status not in STEP_STATUSES:
            raise ValueError(f"未知的步骤状态: {status}")
        rows.append(
            (
                step.get("id") or _new_id("st"),
                run_id,
                idx,
                kind,
                step.get("title") or "",
                _json_dump(step.get("action") or {}) or "{}",
                1 if step.get("requires_approval") else 0,
                status,
                _json_dump(step.get("output")),
                step.get("error"),
                int(step.get("retry_count") or 0),
                step.get("approved_by"),
                step.get("approved_at"),
                step.get("started_at"),
                step.get("ended_at"),
            )
        )
    return rows


_STEP_INSERT_SQL = (
    "INSERT INTO steps (id, run_id, idx, kind, title, action_json,"
    " requires_approval, status, output_json, error, retry_count,"
    " approved_by, approved_at, started_at, ended_at)"
    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
)


def replace_steps(run_id: str, steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """整体替换某 run 的步骤列表（首次生成计划 / 用户编辑任务书时调用）。

    用户编辑后也走这里 —— 「以用户版本为准」，不做增量 diff。
    """
    now = _now()
    rows = _step_rows(run_id, steps)

    with _lock, _connect() as conn:
        conn.execute("DELETE FROM steps WHERE run_id = ?", (run_id,))
        if rows:
            conn.executemany(_STEP_INSERT_SQL, rows)
        conn.execute("UPDATE runs SET updated_at = ? WHERE id = ?", (now, run_id))
    return list_steps(run_id)


def delete_steps_from(run_id: str, from_idx: int) -> int:
    """删除 `idx >= from_idx` 的步骤，返回删除行数。

    用于「从第 N 步起重新规划」：已完成/跳过的前序步骤保留，被驳回那步及其后续清掉。
    """
    with _lock, _connect() as conn:
        cursor = conn.execute(
            "DELETE FROM steps WHERE run_id = ? AND idx >= ?", (run_id, int(from_idx))
        )
        conn.execute("UPDATE runs SET updated_at = ? WHERE id = ?", (_now(), run_id))
        return int(cursor.rowcount or 0)


def append_steps(run_id: str, steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """在已有步骤之后**追加**步骤（重新规划时用，保留已完成步骤的产物）。

    ⚠️ 刻意要求追加的 idx 必须**全部大于**现有最大 idx，否则抛错。
    静默允许 idx 冲突会产生两行同 idx 的步骤：时间线看起来正常，
    但 `get_step(idx)` 只会取到其中一行，另一行永远无法被批准或执行。
    """
    if not steps:
        return list_steps(run_id)

    existing = list_steps(run_id)
    max_idx = max((s["idx"] for s in existing), default=0)
    incoming = [int(s.get("idx") or 0) for s in steps]
    if min(incoming) <= max_idx:
        raise ValueError(
            f"追加步骤的 idx 必须大于现有最大 idx（{max_idx}），"
            f"实际最小为 {min(incoming)}"
        )

    rows = _step_rows(run_id, steps)
    with _lock, _connect() as conn:
        conn.executemany(_STEP_INSERT_SQL, rows)
        conn.execute(
            "UPDATE runs SET updated_at = ? WHERE id = ?", (_now(), run_id)
        )
    return list_steps(run_id)


def list_steps(run_id: str) -> list[dict[str, Any]]:
    with _lock, _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM steps WHERE run_id = ? ORDER BY idx", (run_id,)
        ).fetchall()
    return [_step_row(row) for row in rows]


def get_step(run_id: str, idx: int) -> Optional[dict[str, Any]]:
    with _lock, _connect() as conn:
        row = conn.execute(
            "SELECT * FROM steps WHERE run_id = ? AND idx = ?", (run_id, idx)
        ).fetchone()
    return _step_row(row) if row else None


_STEP_UPDATABLE = {
    "title",
    "kind",
    "action_json",
    "requires_approval",
    "status",
    "output_json",
    "error",
    "retry_count",
    "approved_by",
    "approved_at",
    "started_at",
    "ended_at",
}


def update_step(run_id: str, idx: int, **fields: Any) -> Optional[dict[str, Any]]:
    patch = dict(fields)
    if "action" in patch:
        patch["action_json"] = _json_dump(patch.pop("action")) or "{}"
    if "output" in patch:
        patch["output_json"] = _json_dump(patch.pop("output"))
    if "requires_approval" in patch:
        patch["requires_approval"] = 1 if patch["requires_approval"] else 0

    invalid = set(patch) - _STEP_UPDATABLE
    if invalid:
        raise ValueError(f"不可更新的步骤字段: {sorted(invalid)}")
    if "status" in patch and patch["status"] not in STEP_STATUSES:
        raise ValueError(f"未知的步骤状态: {patch['status']}")
    if not patch:
        return get_step(run_id, idx)

    assignments = ", ".join(f"{key} = ?" for key in patch)
    with _lock, _connect() as conn:
        cur = conn.execute(
            f"UPDATE steps SET {assignments} WHERE run_id = ? AND idx = ?",
            (*patch.values(), run_id, idx),
        )
        if cur.rowcount == 0:
            return None
        conn.execute(
            "UPDATE runs SET updated_at = ? WHERE id = ?", (_now(), run_id)
        )
    return get_step(run_id, idx)


# ── 临时文件引用 ────────────────────────────────────────────────────


def add_run_file(
    *,
    run_id: str,
    filename: str,
    stored_path: str,
    size_bytes: int,
    sha256: str = "",
    kind: str = "input",
    parsed_chars: Optional[int] = None,
    ttl_expires_at: float,
    file_id: Optional[str] = None,
) -> dict[str, Any]:
    """登记一个临时文件引用。

    `file_id` 由调用方预先给出时，落盘文件名可以与之对齐，
    省掉「写完再改名」的中间态。
    """
    fid = file_id or _new_id("rf")
    now = _now()
    with _lock, _connect() as conn:
        conn.execute(
            "INSERT INTO run_files (id, run_id, filename, stored_path, size_bytes,"
            " sha256, kind, parsed_chars, ttl_expires_at, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                fid,
                run_id,
                filename,
                stored_path,
                size_bytes,
                sha256,
                kind,
                parsed_chars,
                ttl_expires_at,
                now,
            ),
        )
        conn.execute("UPDATE runs SET updated_at = ? WHERE id = ?", (now, run_id))
    return get_run_file(fid)  # type: ignore[return-value]


def list_run_files(run_id: str) -> list[dict[str, Any]]:
    with _lock, _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM run_files WHERE run_id = ? ORDER BY created_at", (run_id,)
        ).fetchall()
    return [_file_row(row) for row in rows]


def get_run_file(file_id: str) -> Optional[dict[str, Any]]:
    with _lock, _connect() as conn:
        row = conn.execute(
            "SELECT * FROM run_files WHERE id = ?", (file_id,)
        ).fetchone()
    return _file_row(row) if row else None


def delete_run_file(file_id: str) -> Optional[str]:
    """删除记录并返回落盘路径（由 service 层删文件）。"""
    with _lock, _connect() as conn:
        row = conn.execute(
            "SELECT stored_path FROM run_files WHERE id = ?", (file_id,)
        ).fetchone()
        if not row:
            return None
        conn.execute("DELETE FROM run_files WHERE id = ?", (file_id,))
    return row["stored_path"]


# ── TTL 清理支撑 ────────────────────────────────────────────────────


def list_expired_run_files(*, now: float, limit: int = 500) -> list[dict[str, Any]]:
    """已过期的文件记录。

    `ttl_expires_at <= 0` 表示「不自动清理」，因此这里显式排除 ——
    否则 0 会被当成「1970 年就过期了」，把不该删的文件删掉。
    """
    with _lock, _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM run_files WHERE ttl_expires_at > 0 AND ttl_expires_at <= ?"
            " ORDER BY ttl_expires_at LIMIT ?",
            (float(now), int(limit)),
        ).fetchall()
    return [_file_row(row) for row in rows]


def all_stored_paths() -> set[str]:
    """库里登记过的全部落盘路径 —— 供孤儿文件扫描比对。"""
    with _lock, _connect() as conn:
        rows = conn.execute("SELECT stored_path FROM run_files").fetchall()
    return {str(row["stored_path"]) for row in rows}


def workspace_root() -> str:
    """所有工作区的父目录。"""
    return str(get_workflow_settings().resolved_workspace_root)
