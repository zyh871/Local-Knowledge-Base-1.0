"""
RAG 运行时门面。

**注意**：这里**不再包含任何编排逻辑**。
`route_invoke` / `route_stream` / `invoke` / `stream` 已全部移除 ——
业务流程统一由 `app/graph/` 的状态图承载。

本类只保留三类与"运行时状态"相关的职责：
  1. 懒初始化（向量库 + LLM）
  2. 对外暴露 LLM 实例（图节点通过它生成回答）
  3. 统计与文档清单（`/stats`、`/documents`）
"""

from __future__ import annotations

import os
from collections import Counter
from typing import Any, Dict, List, Optional

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)

# 文档清单的进程内缓存（5 秒）。
# 全量扫描 Chroma metadata 的成本随 chunk 数线性增长，而 `/documents`
# 一次请求要用到两份结果（列表 + 总数）。缓存让一次请求只扫一遍库。
_DOCS_CACHE_TTL = 5.0
_DOCS_CACHE: Dict[str, Any] = {"rows": None, "at": 0.0}


class RAGChain:
    """向量库与模型的运行时门面。"""

    def __init__(self) -> None:
        self._llm: Any = None
        self._ready = False

    # -- 生命周期 --------------------------------------------------------

    def ensure_initialized(self) -> None:
        """懒初始化（首次使用时触发）。"""
        if self._ready:
            return
        from app.rag.providers import get_llm
        from app.rag.vectorstore import get_vectorstore

        get_vectorstore()
        self._llm = get_llm()
        self._ready = True

    @property
    def llm(self) -> Any:
        """当前 LLM 实例（未初始化时返回 None）。"""
        return self._llm

    @property
    def is_ready(self) -> bool:
        return self._ready

    # -- 统计 ------------------------------------------------------------

    def get_stats(self) -> Dict[str, Any]:
        from app.rag.vectorstore import get_bm25_retriever, get_vectorstore

        store = get_vectorstore()
        bm25 = get_bm25_retriever()

        doc_count = store.get_document_count() if hasattr(store, "get_document_count") else 0
        bm25_count = len(bm25.documents) if bm25 else 0

        chroma_exists = False
        try:
            if store and getattr(store, "vectorstore", None) is not None:
                all_data = store.vectorstore.get()
                chroma_exists = bool(all_data and all_data.get("ids"))
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"chroma_exists 检查失败: {exc}")

        return {
            "document_count": doc_count,
            "bm25_document_count": bm25_count,
            "vectorstore_initialized": store is not None,
            "bm25_initialized": bm25 is not None,
            "vector_count": doc_count,
            "chroma_exists": chroma_exists,
        }

    def list_documents(self, limit: int = 500) -> List[Dict[str, Any]]:
        """按来源聚合的文档清单（含分块数与文件大小）。"""
        return self._document_rows()[:limit]

    def _document_rows(self) -> List[Dict[str, Any]]:
        """全量聚合结果，带 5 秒进程内缓存。

        ⚠️ 为什么必须缓存：`vectorstore.get(include=["metadatas"])` 是**全量扫描**，
        成本随 chunk 数线性增长。修复前 `/documents` 一次请求会走两遍
        （`list_documents()` + `count_documents()`），902 个分块下
        p50 685ms / 14.2 rps，比同进程 `/conversations` 慢 82 倍。

        缓存只影响"列表新鲜度"，5 秒内的增删感知延迟可接受；
        增删文档时 `invalidate_documents_cache()` 会立即失效，不必等 TTL。
        """
        import time

        now = time.monotonic()
        cached = _DOCS_CACHE["rows"]
        if cached is not None and (now - _DOCS_CACHE["at"]) < _DOCS_CACHE_TTL:
            return cached

        rows = self._scan_documents()
        _DOCS_CACHE["rows"] = rows
        _DOCS_CACHE["at"] = now
        return rows

    def _scan_documents(self) -> List[Dict[str, Any]]:
        """真正做一次全量扫描并按 source 聚合。"""
        from app.rag.vectorstore import get_vectorstore

        store = get_vectorstore()
        if not store or not store.vectorstore:
            return []

        try:
            all_data = store.vectorstore.get(include=["metadatas"])
            metadatas = all_data.get("metadatas") or []
            if not metadatas:
                return []

            chunk_counts = Counter(
                (m.get("source", "unknown") if m else "unknown") for m in metadatas
            )
            data_path = get_settings().paths.data_path

            seen: set[str] = set()
            docs: List[Dict[str, Any]] = []
            for meta in metadatas:
                if not meta:
                    continue
                source = meta.get("source", "unknown")
                if source in seen:
                    continue
                seen.add(source)
                file_path = os.path.join(data_path, os.path.basename(source))
                size = os.path.getsize(file_path) if os.path.isfile(file_path) else 0
                docs.append(
                    {"filename": source, "size": size, "chunks": chunk_counts[source]}
                )
            docs.sort(key=lambda d: d["filename"])
            return docs
        except Exception as exc:  # noqa: BLE001
            logger.error(f"获取文档列表失败: {exc}", exc_info=True)
            return []

    def count_documents(self) -> int:
        """按 source 去重后的文档总数。

        `list_documents(limit)` 会在 limit 处截断，**不能**用它的长度当总数
        （否则翻页时 total 恒等于页大小）。这里复用同一份聚合结果，
        不再单独扫一遍库。
        """
        return len(self._document_rows())


def invalidate_documents_cache() -> None:
    """文档增删后立即失效，避免列表显示旧数据。"""
    _DOCS_CACHE["rows"] = None
    _DOCS_CACHE["at"] = 0.0


# ---------------------------------------------------------------------------
#  进程级单例
# ---------------------------------------------------------------------------

_rag_chain_instance: Optional[RAGChain] = None


def get_rag_chain() -> RAGChain:
    global _rag_chain_instance
    if _rag_chain_instance is None:
        _rag_chain_instance = RAGChain()
    return _rag_chain_instance
