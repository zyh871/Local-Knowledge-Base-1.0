"""TTL 清理的调度与执行（设计 §9.4）。

判断逻辑在 `app/workflow/cleanup.py`（纯函数、可完整单测），
这里只负责「取数据 / 执行删除 / 周期调度」。

⚠️ **必须自写**：项目里没有现成的周期性扫描机制。Redis 侧的 TTL 只管 Redis 键，
管不到磁盘文件与 SQLite 行 —— 不写这段代码，`data/workspaces/*/files/`
会随着使用无限增长，而用户看不到任何异常。

清理两类东西：
  1. `run_files.ttl_expires_at` 已到期的文件（删文件 + 删记录）
  2. **孤儿文件**：磁盘上有、库里没有的僵尸文件

第 2 条是刻意的：只按数据库记录删，会留下一批"磁盘有、库里没有"的文件，
它们永远不会被任何流程清理掉（典型的「写盘成功但登记失败」残留）。

分享链接的过期由 Redis 原生 TTL 负责，这里不重复处理。
"""

from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path
from typing import Any, Optional

from app.core.config import get_workflow_settings
from app.core.logging import get_logger
from app.repositories import workflow_repo as repo
from app.workflow.cleanup import plan_cleanup

logger = get_logger(__name__)

__all__ = ["cleanup_once", "start_cleanup_task", "stop_cleanup_task"]

_task: Optional[asyncio.Task] = None


def _disk_files() -> list[str]:
    """列出工作区目录下所有已落盘的文件。"""
    root = Path(repo.workspace_root())
    if not root.is_dir():
        return []
    out: list[str] = []
    for child in root.iterdir():
        if not child.is_dir():
            continue
        for name in ("files", "artifacts"):
            directory = child / name
            if not directory.is_dir():
                continue
            for entry in directory.iterdir():
                if entry.is_file():
                    out.append(str(entry))
    return out


def cleanup_once(*, now: Optional[float] = None, dry_run: bool = False) -> dict[str, Any]:
    """执行一次清理，返回统计信息（供日志与测试断言）。"""
    stamp = time.time() if now is None else now
    plan = plan_cleanup(
        expired=repo.list_expired_run_files(now=stamp),
        known_paths=repo.all_stored_paths(),
        disk_files=_disk_files(),
    )

    stats = {
        "expired": len(plan["delete_record_ids"]),
        "deleted_files": 0,
        "missing_files": len(plan["missing_files"]),
        "orphans": len(plan["orphan_files"]),
        "errors": 0,
    }

    if dry_run:
        stats["planned_delete_files"] = len(plan["delete_files"])
        return stats

    for path in plan["delete_files"] + plan["orphan_files"]:
        try:
            if os.path.isfile(path):
                os.remove(path)
                if path in plan["delete_files"]:
                    stats["deleted_files"] += 1
        except OSError as exc:  # noqa: BLE001
            stats["errors"] += 1
            logger.warning(f"删除文件失败 {path}: {exc}")

    for record_id in plan["delete_record_ids"]:
        try:
            repo.delete_run_file(record_id)
        except Exception as exc:  # noqa: BLE001
            stats["errors"] += 1
            logger.warning(f"删除文件记录失败 {record_id}: {exc}")

    if plan["truncated"]:
        logger.info("[TTL 清理] 本轮已达处理上限，剩余记录留待下一轮")

    if stats["expired"] or stats["orphans"] or stats["errors"]:
        logger.info(
            f"[TTL 清理] 过期 {stats['expired']} 条（删文件 {stats['deleted_files']}，"
            f"文件已不存在 {stats['missing_files']}）、孤儿 {stats['orphans']} 个、"
            f"错误 {stats['errors']} 个"
        )
    return stats


async def _loop(interval: int) -> None:
    """周期执行。首轮延迟一个 interval —— 启动瞬间没必要扫。"""
    while True:
        try:
            await asyncio.sleep(interval)
            # 同步 IO 放到线程里，避免阻塞事件循环
            await asyncio.to_thread(cleanup_once)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - 周期任务绝不能因单次失败而退出
            logger.error(f"TTL 清理任务出错: {exc}", exc_info=True)
            await asyncio.sleep(min(interval, 60))


def start_cleanup_task() -> Optional[asyncio.Task]:
    """启动周期清理任务（幂等）。"""
    global _task
    if _task is not None and not _task.done():
        return _task

    cfg = get_workflow_settings()
    if not cfg.enabled:
        logger.info("工作流未启用，跳过 TTL 清理任务")
        return None

    interval = max(int(cfg.cleanup_interval_seconds), 60)
    _task = asyncio.create_task(_loop(interval), name="workflow-ttl-cleanup")
    logger.info(
        f"工作流 TTL 清理任务已启动（每 {interval}s 一次，"
        f"文件保留 {cfg.file_ttl_days} 天）"
    )
    return _task


async def stop_cleanup_task() -> None:
    global _task
    if _task is None:
        return
    _task.cancel()
    try:
        await _task
    except (asyncio.CancelledError, Exception):  # noqa: BLE001
        pass
    _task = None
    logger.info("工作流 TTL 清理任务已停止")
