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
                # ⚠️ 不要在这里 `vectorstore.get(include=[])`：那是**全量扫描**，
                # 902 个分块就要 94.7ms，语料涨到十万级探针会直接超时、
                # 被编排层判死并反复重启。存活检查只需要"集合能打开"。
                details["chroma"] = {"ok": True, "initialized": True}
            else:
                details["chroma"] = {"ok": True, "initialized": False}
        except Exception as exc:  # noqa: BLE001
            # 不把异常原文回吐：可能含本地路径。日志里带 trace_id。
            logger.warning(f"chroma 健康检查失败: {exc}")
            details["chroma"] = {"ok": False, "initialized": False}
            details["status"] = "degraded"

    def _check_sqlite(self, details: dict[str, Any]) -> None:
        try:
            from app.memory.conversation_memory import _meta_connect

            conn = _meta_connect()
            # 只探"能读"，不 COUNT(*)（大库上是全表扫）。
            conn.execute("SELECT 1 FROM conversations LIMIT 1").fetchone()
            # 计数属于运营指标，放 `/stats`（需登录），不在匿名探针里暴露规模。
            details["sqlite_conversations"] = {"ok": True}
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"sqlite 健康检查失败: {exc}")
            details["sqlite_conversations"] = {"ok": False}
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
                # 生产环境里静默降级很危险：
                # 多 worker 下限流额度变成 N 倍、`vectorstore:rebuild` 锁每进程一把
                # （可能并发重建同一个 Chroma → 索引损坏）。必须让编排层看得见。
                if self.settings.env == "prod":
                    details["status"] = "degraded"
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
