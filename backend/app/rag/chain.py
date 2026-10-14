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
                if len(docs) >= limit:
                    break
            return docs
        except Exception as exc:  # noqa: BLE001
            logger.error(f"获取文档列表失败: {exc}", exc_info=True)
            return []


# ---------------------------------------------------------------------------
#  进程级单例
# ---------------------------------------------------------------------------

_rag_chain_instance: Optional[RAGChain] = None


def get_rag_chain() -> RAGChain:
    global _rag_chain_instance
    if _rag_chain_instance is None:
        _rag_chain_instance = RAGChain()
    return _rag_chain_instance
