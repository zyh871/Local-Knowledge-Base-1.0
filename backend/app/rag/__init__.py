"""
RAG 领域能力包。

从原来的单体 `app/rag_chain.py`（858 行）拆分而来：

    providers.py    模型工厂：LLM / Embedding / Vision（含智谱接入）
    embeddings.py   OpenAI 兼容 HTTP 嵌入 + Redis 缓存包装
    loaders.py      文档加载与切分
    vectorstore.py  向量库/BM25 生命周期、混合检索、检索缓存失效
    chain.py        运行时门面（RAGChain）
    prompts.py      提示词模板

**本包不含任何编排逻辑** —— 流程编排在 `app/graph/`。
"""

from app.rag.chain import RAGChain, get_rag_chain
from app.rag.embeddings import (
    CachedEmbeddings,
    OpenAICompatEmbeddings,
    SimpleTFIDFEmbeddings,
)
from app.rag.loaders import (
    LOADER_MAPPING,
    load_documents,
    load_single_document,
    split_documents,
)
from app.rag.prompts import RAG_TEMPLATE
from app.rag.providers import (
    MockLLM,
    get_embeddings,
    get_llm,
    get_query_llm,
    get_vision_llm,
    list_llm_models,
    resolve_model,
)
from app.rag.vectorstore import (
    _TOKEN_CACHE,
    _executor,
    add_documents_to_store,
    ensure_indexed,
    format_docs,
    get_bm25_retriever,
    get_sources,
    get_vectorstore,
    hybrid_search,
    invalidate_retrieval_cache,
    rebuild_vectorstore,
    remove_document_from_store,
    tokenize,
    tokenize_query,
)

__all__ = [
    # chain
    "RAGChain",
    "get_rag_chain",
    # providers
    "MockLLM",
    "get_embeddings",
    "get_llm",
    "get_query_llm",
    "get_vision_llm",
    "list_llm_models",
    "resolve_model",
    # embeddings
    "CachedEmbeddings",
    "OpenAICompatEmbeddings",
    "SimpleTFIDFEmbeddings",
    # loaders
    "LOADER_MAPPING",
    "load_documents",
    "load_single_document",
    "split_documents",
    # vectorstore
    "add_documents_to_store",
    "ensure_indexed",
    "format_docs",
    "get_bm25_retriever",
    "get_sources",
    "get_vectorstore",
    "hybrid_search",
    "invalidate_retrieval_cache",
    "rebuild_vectorstore",
    "remove_document_from_store",
    "tokenize",
    "tokenize_query",
    "_executor",
    "_TOKEN_CACHE",
    # prompts
    "RAG_TEMPLATE",
]
