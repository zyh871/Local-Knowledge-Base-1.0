"""TTL 清理的**纯决策逻辑**（设计 §9.4）。

⚠️ 「必须自写」的原因：项目里没有现成的周期性扫描机制
（`RedisSettings` 只有 Redis 侧 TTL 与 `checkpoint_ttl`，管不到磁盘文件与 SQLite 行）。
不写的话 `data/workspaces/*/files/` 会随使用无限增长，而用户看不到任何异常。

## 为什么只有决策、没有 IO

本层禁止依赖 `repositories`（分层测试会拦），这其实是个好的约束：
把「该删什么」与「怎么删」分开之后，判断逻辑可以用纯数据完整单测 ——
而清理逻辑写错的后果是**误删用户文件**，恰恰是最需要被测清楚的一类代码。

调用方（`services/workflow_cleanup.py`）负责取数据与真正执行。
"""

from __future__ import annotations

from typing import Any, Iterable

__all__ = ["plan_cleanup"]

#: 单次扫描最多处理的记录数，避免一次删太多把 IO 打满
MAX_PER_RUN = 500


def plan_cleanup(
    *,
    expired: Iterable[dict[str, Any]],
    known_paths: Iterable[str],
    disk_files: Iterable[str],
    limit: int = MAX_PER_RUN,
) -> dict[str, Any]:
    """算出这一轮要删哪些文件、哪些记录。

    参数都是纯数据：
      * `expired`      —— 已过期的文件记录（含 id 与 stored_path）
      * `known_paths`  —— 库里登记过的全部落盘路径
      * `disk_files`   —— 磁盘上实际存在的文件路径

    返回：
        delete_files   实际存在、要删的文件
        delete_record_ids  要删的记录 id（**文件不存在时也删记录**）
        missing_files  记录还在但文件已不在（只清记录）
        orphan_files   磁盘上有、库里没有的僵尸文件
        truncated      是否因为 limit 而截断
    """
    expired_list = [r for r in expired if isinstance(r, dict)]
    truncated = len(expired_list) > limit
    if truncated:
        expired_list = expired_list[:limit]

    on_disk = {str(p) for p in disk_files}
    known = {str(p) for p in known_paths}

    delete_files: list[str] = []
    missing_files: list[str] = []
    delete_record_ids: list[str] = []

    for record in expired_list:
        record_id = str(record.get("id") or "")
        path = str(record.get("stored_path") or "")
        if record_id:
            delete_record_ids.append(record_id)
        if path and path in on_disk:
            delete_files.append(path)
        elif path:
            # 文件已经不在了（被手工删掉 / 上一次删除中途失败）——
            # 记录仍要清掉，否则它会永远留在过期列表里
            missing_files.append(path)

    orphan_files = sorted(
        path for path in on_disk if path and path not in known
    )

    return {
        "delete_files": delete_files,
        "delete_record_ids": delete_record_ids,
        "missing_files": missing_files,
        "orphan_files": orphan_files,
        "truncated": truncated,
    }
