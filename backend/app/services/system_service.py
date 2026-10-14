"""
系统诊断用例：`/health` 与 `/stats`。

`/health` 的响应键与 v3.0.0 保持一致（chroma / sqlite_conversations / llm），
并新增 redis / graph 两个键（新增键不破坏既有消费方）。

关于 LLM 探活：旧实现每次健康检查都向模型发一条 "ping"，
这既产生真实开销，又会因为部分服务商拒绝裸 "ping" 提示词而**误报 degraded**
（实测 ModelScope 返回 400）。因此改为由 `HEALTH_CHECK_LLM=true` 显式开启，
默认关闭。
"""

from __future__ import annotations

from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)


class SystemService:
    def __init__(
        self,
        *,
        chain: Any,
        settings: Any,
        redis: Any = None,
        redis_manager: Any = None,
        graph_obj: Any = None,
    ) -> None:
        self.chain = chain
        self.settings = settings
        self.redis = redis
        self.redis_manager = redis_manager
        self.graph_obj = graph_obj

    # ------------------------------------------------------------------
    #  统计
    # ------------------------------------------------------------------

    def stats(self) -> dict[str, Any]:
        return self.chain.get_stats()

    # ------------------------------------------------------------------
    #  健康检查
    # ------------------------------------------------------------------

    def health(self) -> dict[str, Any]:
        details: dict[str, Any] = {
            "status": "ok",
            "service": self.settings.app_name,
            "version": self.settings.app_version,
        }

        self._check_chroma(details)
        self._check_sqlite(details)
        self._check_redis(details)
        self._check_graph(details)
        self._check_llm(details)

        return details

    # -- 各子项 ---------------------------------------------------------

    def _check_chroma(self, details: dict[str, Any]) -> None:
        try:
            from app.rag.vectorstore import get_vectorstore

            store = get_vectorstore()
            if store and getattr(store, "vectorstore", None):
                info = store.vectorstore.get(include=[])
                details["chroma"] = {
                    "ok": True,
                    "chunks": len(info.get("ids") or []) if info else 0,
                }
            else:
                details["chroma"] = {"ok": True, "note": "not_initialized"}
        except Exception as exc:  # noqa: BLE001
            details["chroma"] = {"ok": False, "error": str(exc)}
            details["status"] = "degraded"

    def _check_sqlite(self, details: dict[str, Any]) -> None:
        try:
            from app.memory.conversation_memory import _meta_connect

            conn = _meta_connect()
            row = conn.execute("SELECT COUNT(*) AS cnt FROM conversations").fetchone()
            details["sqlite_conversations"] = {
                "ok": True,
                "conversations": row["cnt"] if row else 0,
            }
        except Exception as exc:  # noqa: BLE001
            details["sqlite_conversations"] = {"ok": False, "error": str(exc)}
            details["status"] = "degraded"

    def _check_redis(self, details: dict[str, Any]) -> None:
        if self.redis_manager is not None and self.redis_manager.state.client is not None:
            fallback = self.redis_manager.is_fallback
            details["redis"] = {
                "ok": True,
                "mode": "memory" if fallback else "redis",
                "version": self.redis_manager.state.extra.get("version", "unknown"),
            }
            if fallback:
                details["redis"]["note"] = "进程内降级：多 worker 下限流/锁/缓存不共享"
        else:
            details["redis"] = {"ok": False, "error": "not_initialized"}

    def _check_graph(self, details: dict[str, Any]) -> None:
        """报告状态图状态。图是唯一编排实现，故 enabled 恒为 True。"""
        graph = self.settings.graph
        node_count = None
        if self.graph_obj is not None:
            try:
                node_count = len(
                    [n for n in self.graph_obj.get_graph().nodes if not n.startswith("__")]
                )
            except Exception:  # noqa: BLE001
                node_count = None
        details["graph"] = {
            "enabled": self.graph_obj is not None,
            "checkpointer": graph.checkpointer,
            "nodes": node_count,
        }
        if self.graph_obj is None:
            details["status"] = "degraded"

    def _check_llm(self, details: dict[str, Any]) -> None:
        if not getattr(self.settings, "health_check_llm", False):
            details["llm"] = {"ok": True, "note": "skipped"}
            return
        try:
            llm = getattr(self.chain, "_llm", None)
            if llm is None:
                details["llm"] = {"ok": True, "note": "deferred_init"}
                return
            response = llm.invoke("ping")
            _ = getattr(response, "content", None)
            details["llm"] = {"ok": True}
        except Exception as exc:  # noqa: BLE001
            details["llm"] = {"ok": False, "error": str(exc)}
            details["status"] = "degraded"
