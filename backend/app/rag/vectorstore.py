"""
向量库与 BM25 索引的生命周期管理。

包含：
  * 线程安全的向量库单例（Chroma）
  * 增量增删（避免每次上传都全量重建）
  * 全量重建
  * 检索缓存的**主动失效**（文档增删后必须调用）

另外导出两个进程级运行时对象：
  * `_executor`     —— 异步端点卸载 CPU 密集任务的线程池
  * `_TOKEN_CACHE`  —— 分词缓存（供 lifespan 清理）
"""

from __future__ import annotations

import os
import re
import shutil
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, List, Optional

from langchain_core.documents import Document

from app.core.config import get_settings
from app.core.logging import get_logger
from app.core.redis import get_keys, sync_cache_delete_pattern
from app.retrievers import BM25Retriever, VectorRetriever, rrf_fusion

logger = get_logger(__name__)

__all__ = [
    "tokenize",
    "tokenize_query",
    "get_vectorstore",
    "get_bm25_retriever",
    "hybrid_search",
    "add_documents_to_store",
    "remove_document_from_store",
    "rebuild_vectorstore",
    "format_docs",
    "get_sources",
    "invalidate_retrieval_cache",
    "_executor",
    "_TOKEN_CACHE",
]

# ---------------------------------------------------------------------------
#  分词（带缓存）
# ---------------------------------------------------------------------------

_TOKEN_CACHE: Dict[str, List[str]] = {}
_TOKEN_CACHE_MAX_SIZE = 10000
_TOKEN_CACHE_LOCK = threading.Lock()

_TOKEN_RE = re.compile(r"[一-鿿]+|[a-zA-Z0-9]+")


def tokenize(text: str, max_tokens: int = 100) -> List[str]:
    """中英混合分词，带 LRU 式缓存。"""
    with _TOKEN_CACHE_LOCK:
        if text in _TOKEN_CACHE:
            return _TOKEN_CACHE[text]

    result = _TOKEN_RE.findall(text.lower())[:max_tokens]

    with _TOKEN_CACHE_LOCK:
        if text in _TOKEN_CACHE:  # 双重检查
            return _TOKEN_CACHE[text]
        if len(_TOKEN_CACHE) > _TOKEN_CACHE_MAX_SIZE:
            for key in list(_TOKEN_CACHE.keys())[: _TOKEN_CACHE_MAX_SIZE // 2]:
                _TOKEN_CACHE.pop(key, None)
        _TOKEN_CACHE[text] = result
    return result


def tokenize_query(text: str) -> List[str]:
    """分词但不截断（BM25 打分用）。"""
    return _TOKEN_RE.findall(text.lower())


# ---------------------------------------------------------------------------
#  单例
# ---------------------------------------------------------------------------

_vectorstore_instance: Optional[VectorRetriever] = None
_bm25_retriever: Optional[BM25Retriever] = None
_instance_lock = threading.Lock()

_executor = ThreadPoolExecutor(max_workers=max(8, (os.cpu_count() or 4) * 2))


def get_vectorstore(force_rebuild: bool = False) -> VectorRetriever:
    """获取（或创建）向量库单例，线程安全。"""
    global _vectorstore_instance, _bm25_retriever

    if _vectorstore_instance is not None and not force_rebuild:
        return _vectorstore_instance

    with _instance_lock:
        if _vectorstore_instance is not None and not force_rebuild:
            return _vectorstore_instance

        from app.rag.providers import get_embeddings

        os.makedirs(get_settings().paths.chroma_persist_dir, exist_ok=True)

        if _vectorstore_instance is None or force_rebuild:
            _vectorstore_instance = VectorRetriever()
            _vectorstore_instance.initialize(get_embeddings())

        if _bm25_retriever is None:
            _bm25_retriever = BM25Retriever()

        return _vectorstore_instance


def get_bm25_retriever() -> Optional[BM25Retriever]:
    return _bm25_retriever


# ---------------------------------------------------------------------------
#  检索缓存
# ---------------------------------------------------------------------------


def invalidate_retrieval_cache() -> int:
    """清空检索缓存。

    **文档增删后必须调用** —— 否则用户上传新文档后提问会拿到旧检索结果。
    TTL 只是兜底，不能替代主动失效。
    """
    keys = get_keys()
    removed = sync_cache_delete_pattern(keys.scan_pattern("cache"))
    if removed:
        logger.info(f"已清空检索/嵌入缓存 {removed} 个键")
    return removed


# ---------------------------------------------------------------------------
#  增删改
# ---------------------------------------------------------------------------


def add_documents_to_store(docs: List[Document]) -> int:
    """增量写入向量库与 BM25。

    按 source 去重：写入前先删除同源的旧分块，避免重复累积。
    返回写入的分块数。
    """
    global _bm25_retriever

    if not docs:
        return 0

    from app.rag.loaders import split_documents

    store = get_vectorstore()
    chunks = split_documents(docs)
    if not chunks:
        return 0

    sources_to_remove: List[str] = []
    seen: set[str] = set()
    for doc in docs:
        src = doc.metadata.get("source", "")
        if src and src not in seen:
            seen.add(src)
            sources_to_remove.append(src)

    for src in sources_to_remove:
        try:
            store.remove_by_source(src)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"写入前清理旧分块失败 (vector, {src}): {exc}")
        if _bm25_retriever:
            try:
                _bm25_retriever.remove_by_source(src)
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"写入前清理旧分块失败 (bm25, {src}): {exc}")

    store.add_documents(chunks)

    if _bm25_retriever:
        _bm25_retriever.add_documents(chunks)
    else:
        _bm25_retriever = BM25Retriever()
        _bm25_retriever.build_index(chunks)

    invalidate_retrieval_cache()
    logger.info(f"增量添加 {len(chunks)} 个文档块到向量库")
    return len(chunks)


def remove_document_from_store(source: str) -> bool:
    """从向量库与 BM25 中删除某个来源的全部内容。"""
    global _bm25_retriever

    store = get_vectorstore()
    removed = False
    try:
        store.remove_by_source(source)
        removed = True
    except Exception as exc:  # noqa: BLE001
        logger.error(f"从向量库删除文档失败: {exc}")

    if _bm25_retriever:
        try:
            _bm25_retriever.remove_by_source(source)
            removed = True
        except Exception as exc:  # noqa: BLE001
            logger.error(f"从 BM25 删除文档失败: {exc}")

    if removed:
        invalidate_retrieval_cache()
    return removed


def rebuild_vectorstore(reindex: bool = True) -> VectorRetriever:
    """全量重建：清空向量库与索引文件，并从 `data/` 重新加载、切分、索引。

    ⚠️ 修复的历史缺陷：旧实现只清空 Chroma 目录就返回，
    **从不重新索引**（`load_documents()` 定义了却没有任何调用点）。
    结果是调用 `/rebuild-vectorstore` 后知识库反而变空，
    而 README 却宣称"首次启动会自动加载 data/ 目录的文档"。
    现在 `reindex=True` 会真正重建；确实只想要清空时传 `reindex=False`。

    返回 (向量库实例, 统计信息)。
    """
    global _vectorstore_instance, _bm25_retriever

    _vectorstore_instance = None
    _bm25_retriever = None
    _TOKEN_CACHE.clear()

    paths = get_settings().paths
    if os.path.exists(paths.chroma_persist_dir):
        shutil.rmtree(paths.chroma_persist_dir, ignore_errors=True)
    os.makedirs(paths.chroma_persist_dir, exist_ok=True)

    # BM25 索引与 TF-IDF 词表也必须清掉，否则会加载到旧索引
    for stale in ("bm25_index.pkl", "tfidf_vocab.pkl"):
        stale_path = os.path.join(paths.data_path, stale)
        if os.path.exists(stale_path):
            try:
                os.remove(stale_path)
            except OSError as exc:  # noqa: BLE001
                logger.warning(f"清理旧索引文件失败 {stale}: {exc}")

    invalidate_retrieval_cache()
    store = get_vectorstore(force_rebuild=True)

    if not reindex:
        return store

    from app.rag.loaders import load_documents

    docs = load_documents()
    if not docs:
        logger.warning(f"data/ 目录下没有可索引的文档: {paths.data_path}")
        return store

    chunks = add_documents_to_store(docs)
    logger.info(f"向量库重建完成: {len(docs)} 个文档 → {chunks} 个分块")
    return store


def ensure_indexed() -> int:
    """向量库为空时自动索引 `data/` 下的文档。

    对应 README 宣称的"首次启动自动加载文档创建向量库" ——
    该行为在旧实现中**并未实现**，此处补齐。
    仅在向量库确实为空时动作，不会重复索引。
    """
    try:
        store = get_vectorstore()
        existing = store.get_document_count() if hasattr(store, "get_document_count") else 0
        if existing > 0:
            return 0

        from app.rag.loaders import load_documents

        docs = load_documents()
        if not docs:
            logger.info("向量库为空且 data/ 无文档，跳过自动索引")
            return 0

        chunks = add_documents_to_store(docs)
        logger.info(f"向量库为空，已自动索引 {len(docs)} 个文档 → {chunks} 个分块")
        return chunks
    except Exception as exc:  # noqa: BLE001
        logger.error(f"自动索引失败: {exc}", exc_info=True)
        return 0


# ---------------------------------------------------------------------------
#  混合检索
# ---------------------------------------------------------------------------


def _cached_hybrid_search(question: str, k: int) -> Optional[List[Document]]:
    """读检索缓存。命中返回 Document 列表，未命中返回 None。"""
    from app.core.redis import sync_cache_get_json
    from app.utils.documents import dict_to_doc

    cached = sync_cache_get_json(get_keys().cache_retrieval(question, k))
    if not isinstance(cached, list) or not cached:
        return None
    try:
        return [dict_to_doc(item) for item in cached]
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"检索缓存反序列化失败，忽略: {exc}")
        return None


def _write_retrieval_cache(question: str, k: int, docs: List[Document]) -> None:
    from app.core.redis import sync_cache_set_json
    from app.utils.documents import doc_to_dict

    if not docs:
        return
    sync_cache_set_json(
        get_keys().cache_retrieval(question, k),
        [doc_to_dict(d) for d in docs],
        get_settings().redis.ttl_retrieval_cache,
    )


def hybrid_search(question: str, k: Optional[int] = None) -> List[Document]:
    """BM25 + 向量检索 → RRF 融合。结果带 Redis 缓存。"""
    k = k or get_settings().retrieval.retriever_k

    cached = _cached_hybrid_search(question, k)
    if cached is not None:
        logger.debug(f"检索缓存命中: {question[:30]}")
        return cached

    store = get_vectorstore()
    bm25 = _bm25_retriever

    try:
        vector_results = store.similarity_search_with_score(question, k=k)
    except Exception as exc:  # noqa: BLE001
        logger.error(f"向量检索失败: {exc}")
        vector_results = []

    try:
        bm25_results = bm25.retrieve(question, k=k) if bm25 else []
    except Exception as exc:  # noqa: BLE001
        logger.error(f"BM25 检索失败: {exc}")
        bm25_results = []

    if not vector_results and not bm25_results:
        return []

    settings = get_settings().retrieval
    fused = rrf_fusion(vector_results, bm25_results, k=settings.rrf_k, top_n=k)
    _write_retrieval_cache(question, k, fused)
    return fused


# ---------------------------------------------------------------------------
#  结果格式化
# ---------------------------------------------------------------------------


def format_docs(docs: List[Document]) -> str:
    return "\n\n".join(d.page_content for d in docs)


def get_sources(docs: List[Document]) -> List[Dict]:
    """去重后的来源清单。"""
    seen: set[str] = set()
    sources: List[Dict] = []
    for doc in docs:
        source = doc.metadata.get("source", "unknown")
        if source in seen:
            continue
        seen.add(source)
        sources.append({"file": source, "content_preview": doc.page_content[:100]})
    return sources
