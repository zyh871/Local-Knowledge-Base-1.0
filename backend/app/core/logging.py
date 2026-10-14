"""
结构化日志 + 请求链路追踪。

trace_id 使用 contextvars 保存，因此：
  * 日志过滤器在任何协程/线程内都能取到当前请求的 trace_id
  * 不需要把 trace_id 一层层当参数传下去

兼容旧行为：`request.state.trace_id` 仍会被中间件写入。
"""

from __future__ import annotations

import contextvars
import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from typing import Optional

from app.core.config import LogSettings, get_log_settings, get_settings

_trace_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("trace_id", default="-")

_configured = False


def new_trace_id() -> str:
    import uuid

    return str(uuid.uuid4())[:8]


def set_trace_id(trace_id: Optional[str]) -> contextvars.Token:
    return _trace_id_var.set(trace_id or new_trace_id())


def reset_trace_id(token: contextvars.Token) -> None:
    try:
        _trace_id_var.reset(token)
    except (ValueError, LookupError):  # 跨上下文 reset 时忽略
        pass


def get_trace_id(request: object | None = None) -> str:
    """优先取请求上的 trace_id，其次取当前上下文的。"""
    if request is not None:
        state = getattr(request, "state", None)
        tid = getattr(state, "trace_id", None)
        if tid:
            return str(tid)
    return _trace_id_var.get()


class TraceIDFilter(logging.Filter):
    """把 trace_id 注入每条日志记录；`record.trace_id` 供 format 使用。"""

    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "trace_id"):
            record.trace_id = _trace_id_var.get()
        return True


def setup_logging(cfg: LogSettings | None = None, force: bool = False) -> None:
    """配置根 logger。重复调用是幂等的（除非 force=True）。"""
    global _configured
    if _configured and not force:
        return

    cfg = cfg or get_log_settings()
    log_dir = get_settings().paths.log_dir
    os.makedirs(log_dir, exist_ok=True)

    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]

    if cfg.file_enabled:
        handlers.append(
            RotatingFileHandler(
                os.path.join(log_dir, "app.log"),
                maxBytes=cfg.max_file_size_mb * 1024 * 1024,
                backupCount=cfg.max_backup_count,
                encoding="utf-8",
            )
        )

    logging.basicConfig(
        level=cfg.level_int,
        format=cfg.format,
        datefmt=cfg.datefmt,
        handlers=handlers,
        force=True,
    )

    filt = TraceIDFilter()
    for handler in logging.getLogger().handlers:
        handler.addFilter(filt)

    _configured = True


def get_logger(name: str) -> logging.Logger:
    """统一获取 logger；确保日志系统已初始化。"""
    if not _configured:
        setup_logging()
    return logging.getLogger(name)
