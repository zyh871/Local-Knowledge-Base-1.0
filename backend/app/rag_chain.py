"""
RAG pipeline — document loading, embedding, hybrid search, and LLM chaining.

Architecture
------------
- Modularized components: retrievers/, embeddings/, memory/
- Incremental vectorstore updates (no full rebuild on every upload).
- Fixed TF-IDF embedding: sparse vectors with real vocabulary dimensions.
- Proper async support with thread-pool offloading for CPU-bound work.
- Lazy initialization of RAG chain to avoid startup blocking.
"""

import asyncio
import logging
import math
import os
import re
import shutil
import threading
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from typing import Dict, List, Optional, Tuple

from langchain_chroma import Chroma
from langchain_community.document_loaders import TextLoader, PyPDFLoader
from langchain_core.documents import Document
from langchain_core.language_models.llms import BaseLLM
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import RunnablePassthrough
from langchain_text_splitters import RecursiveCharacterTextSplitter

from app import config
from app.document.parser import load_any as _new_load_any, LOADER_MAPPING as _NEW_LOADERS
from app.embeddings import SimpleTFIDFEmbeddings
from app.retrievers import BM25Retriever, VectorRetriever, rrf_fusion
from app.memory import get_memory

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
#  Document loaders
# ---------------------------------------------------------------------------
# Per-format loaders (PDF/DOCX/PPTX/Excel/CSV/image) live in
# `app.document.parser`. The dict below is sourced from there.
LOADER_MAPPING = _NEW_LOADERS

# ---------------------------------------------------------------------------
#  Tokenisation helpers
# ---------------------------------------------------------------------------

_TOKEN_CACHE: Dict[str, List[str]] = {}
_TOKEN_CACHE_MAX_SIZE = 10000

def tokenize(text: str, max_tokens: int = 100) -> List[str]:
    """Chinese + English tokenization with caching (thread-safe)."""
    # 先检查缓存（读操作）
    with _TOKEN_CACHE_LOCK:
        if text in _TOKEN_CACHE:
            return _TOKEN_CACHE[text]

    # 计算token（锁外执行避免持锁时间过长）
    tokens = re.findall(r"[一-鿿]+|[a-zA-Z0-9]+", text.lower())
    result = tokens[:max_tokens]

    # 写入缓存
    with _TOKEN_CACHE_LOCK:
        # 双重检查：可能其他线程已经写入
        if text in _TOKEN_CACHE:
            return _TOKEN_CACHE[text]
        # 清理机制：超过上限时清理最旧的一半
        if len(_TOKEN_CACHE) > _TOKEN_CACHE_MAX_SIZE:
            keys_to_remove = list(_TOKEN_CACHE.keys())[:_TOKEN_CACHE_MAX_SIZE // 2]
            for k in keys_to_remove:
                _TOKEN_CACHE.pop(k, None)
        _TOKEN_CACHE[text] = result

    return result


def tokenize_query(text: str) -> List[str]:
    """Tokenize without token limit (for BM25 scoring)."""
    return re.findall(r"[一-鿿]+|[a-zA-Z0-9]+", text.lower())

# ---------------------------------------------------------------------------
#  Embedding / LLM factories
# ---------------------------------------------------------------------------

def get_embeddings():
    """Factory: return the appropriate embedding model for the configured backend."""
    if config.EMBEDDING_MODEL == "local-tfidf":
        logger.info("使用本地 TF-IDF 嵌入模型")
        return SimpleTFIDFEmbeddings()
    
    backend = config.MODEL_BACKEND
    if backend == "ollama":
        from langchain_ollama import OllamaEmbeddings
        return OllamaEmbeddings(model=config.EMBEDDING_MODEL)
    elif backend == "openai":
        from langchain_openai import OpenAIEmbeddings
        return OpenAIEmbeddings(model=config.EMBEDDING_MODEL)
    elif backend == "baidu_qianfan":
        from langchain_qianfan import QianfanEmbeddingsEndpoint  # type: ignore
        return QianfanEmbeddingsEndpoint(
            model=config.EMBEDDING_MODEL,
            ak=config.QIANFAN_AK,
            sk=config.QIANFAN_SK,
        )
    elif backend == "modelscope":
        logger.info(f"使用 ModelScope HTTP 嵌入服务: {config.EMBEDDING_MODEL}")
        import requests
        
        class ModelScopeHTTPEmbeddings:
            def __init__(self, model_name, api_key):
                self.model_name = model_name
                self.api_key = api_key
                self.base_url = config.MODELSCOPE_BASE_URL
            
            def embed_query(self, text):
                url = f"{self.base_url}/embeddings"
                headers = {
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json"
                }
                data = {
                    "model": self.model_name,
                    "input": text
                }
                try:
                    response = requests.post(url, json=data, timeout=30)
                    response.raise_for_status()
                    result = response.json()
                    return result.get("data", [{}])[0].get("embedding", [])
                except Exception as e:
                    logger.error(f"ModelScope embedding failed: {e}")
                    return []
            
            def embed_documents(self, texts):
                results = []
                for text in texts:
                    results.append(self.embed_query(text))
                return results
        
        return ModelScopeHTTPEmbeddings(config.EMBEDDING_MODEL, config.MODELSCOPE_API_KEY)
    elif backend == "local":
        logger.info(f"使用本地嵌入模型: {config.EMBEDDING_MODEL}")
        if config.EMBEDDING_MODEL == "local-tfidf":
            return SimpleTFIDFEmbeddings()
        else:
            from langchain_community.embeddings import HuggingFaceEmbeddings
            return HuggingFaceEmbeddings(model_name=config.EMBEDDING_MODEL)
    elif backend == "mock":
        logger.warning("使用 Mock 嵌入模型，仅用于测试")
        from langchain_community.embeddings import FakeEmbeddings
        return FakeEmbeddings(size=128)
    raise ValueError(f"不支持的模型后端: {backend}")


def get_llm():
    """Factory: return the appropriate LLM for the configured backend."""
    backend = config.MODEL_BACKEND
    if backend == "ollama":
        from langchain_ollama import ChatOllama
        return ChatOllama(model=config.LLM_MODEL, temperature=0)
    elif backend == "openai":
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(model=config.LLM_MODEL, temperature=0)
    elif backend == "baidu_qianfan":
        from langchain_qianfan import QianfanChatEndpoint  # type: ignore
        return QianfanChatEndpoint(
            model=config.LLM_MODEL,
            ak=config.QIANFAN_AK,
            sk=config.QIANFAN_SK,
        )
    elif backend == "modelscope":
        from langchain_openai import ChatOpenAI
        logger.info("使用 ModelScope LLM")
        return ChatOpenAI(
            model=config.LLM_MODEL,
            temperature=0,
            api_key=str(config.MODELSCOPE_API_KEY),
            base_url=str(config.MODELSCOPE_BASE_URL),
        )
    elif backend in ("local", "mock"):
        logger.info("使用本地 Mock LLM")
        return MockLLM()
    raise ValueError(f"不支持的模型后端: {backend}")


def get_query_llm():
    """Return a smaller / faster LLM for internal helpers (intent, fact extraction).

    Falls back to the main LLM if a dedicated query model is not configured.
    """
    if config.MODEL_BACKEND in ("local", "mock"):
        return get_llm()
    try:
        if config.MODEL_BACKEND == "modelscope":
            from langchain_openai import ChatOpenAI
            return ChatOpenAI(
                model=os.getenv("MODELSCOPE_QUERY_MODEL", config.LLM_MODEL),
                temperature=0,
                api_key=str(config.MODELSCOPE_API_KEY),
                base_url=str(config.MODELSCOPE_BASE_URL),
            )
    except Exception:
        pass
    return get_llm()


_vision_llm_instance = None
_vision_llm_lock = threading.Lock()


def get_vision_llm():
    """Return a multimodal LLM for image+text QA. Cached singleton."""
    global _vision_llm_instance
    if _vision_llm_instance is not None:
        return _vision_llm_instance
    with _vision_llm_lock:
        if _vision_llm_instance is not None:
            return _vision_llm_instance
        backend = config.MODEL_BACKEND
        if backend in ("local", "mock"):
            logger.warning("Vision LLM not available for local/mock backend; falling back to text LLM")
            return get_llm()
        if backend == "modelscope":
            from langchain_openai import ChatOpenAI
            logger.info(f"使用 ModelScope Vision LLM: {config.VISION_MODEL}")
            _vision_llm_instance = ChatOpenAI(
                model=config.VISION_MODEL,
                temperature=0,
                api_key=str(config.MODELSCOPE_API_KEY),
                base_url=str(config.MODELSCOPE_BASE_URL),
                max_retries=2,
            )
        elif backend == "openai":
            from langchain_openai import ChatOpenAI
            _vision_llm_instance = ChatOpenAI(
                model=config.VISION_MODEL,
                temperature=0,
                api_key=str(config.OPENAI_API_KEY),
            )
        elif backend == "ollama":
            from langchain_ollama import ChatOllama
            _vision_llm_instance = ChatOllama(model=config.VISION_MODEL, temperature=0)
        else:
            logger.warning(f"Vision LLM not configured for backend {backend}; falling back to text LLM")
            return get_llm()
        return _vision_llm_instance

# ---------------------------------------------------------------------------
#  Mock LLM (for dev/testing)
# ---------------------------------------------------------------------------

class MockLLM(BaseLLM):
    @property
    def _llm_type(self) -> str:
        return "mock"

    def _generate(self, prompts, stop=None, run_manager=None):
        from langchain_core.outputs import Generation, LLMResult
        results = []
        for prompt in prompts:
            content = prompt.text if hasattr(prompt, "text") else str(prompt)
            if "上下文" in content:
                start = content.find("上下文：")
                end = content.find("\n\n问题：")
                if start != -1 and end != -1:
                    context_text = content[start + 5 : end].strip()
                    if context_text:
                        results.append(context_text[:500])
                        continue
                results.append(f"根据知识库内容回答：\n\n{content[:300]}")
            else:
                results.append(f"直接回答：{content[:50]}...")
        return LLMResult(generations=[[Generation(text=r)] for r in results])

# ---------------------------------------------------------------------------
#  Document loading & splitting
# ---------------------------------------------------------------------------

def load_documents(data_path: Optional[str] = None) -> List[Document]:
    """Load all supported documents from the data directory."""
    data_path = data_path or config.DATA_PATH
    docs: List[Document] = []
    if not os.path.exists(data_path):
        logger.warning(f"数据目录不存在: {data_path}")
        return docs

    for root, _, files in os.walk(data_path):
        for file in files:
            ext = os.path.splitext(file)[1].lower()
            if ext not in LOADER_MAPPING:
                continue
            full_path = os.path.join(root, file)
            try:
                loaded = _new_load_any(full_path)
                docs.extend(loaded)
                logger.info(f"成功加载文档: {full_path}, 页数: {len(loaded)}")
            except Exception as e:
                logger.error(f"加载文档失败: {full_path}, 错误: {e}")
    return docs


def load_single_document(file_path: str) -> List[Document]:
    """Load a single document by path."""
    ext = os.path.splitext(file_path)[1].lower()
    if ext not in LOADER_MAPPING:
        logger.warning(f"不支持的文件类型: {file_path}")
        return []
    try:
        docs = _new_load_any(file_path)
        if not docs:
            logger.error(f"加载文档返回空结果: {file_path}, 文件大小: {os.path.getsize(file_path) if os.path.exists(file_path) else 'N/A'} bytes")
        return docs
    except Exception as e:
        logger.error(f"加载文档失败: {file_path}, 错误: {e}", exc_info=True)
        return []


def split_documents(docs: List[Document]) -> List[Document]:
    """Split documents into chunks using configured settings."""
    if not docs:
        return []
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=config.CHUNK_SIZE,
        chunk_overlap=config.CHUNK_OVERLAP,
        separators=["\n\n", "\n", "。", "！", "？", "，", " ", ""],
    )
    return splitter.split_documents(docs)

# ---------------------------------------------------------------------------
#  Vectorstore & BM25 management
# ---------------------------------------------------------------------------

_vectorstore_instance: Optional[VectorRetriever] = None
_bm25_retriever: Optional[BM25Retriever] = None
_embeddings_instance = None
_executor = ThreadPoolExecutor(max_workers=2)
_instance_lock = threading.Lock()
_TOKEN_CACHE_LOCK = threading.Lock()


def get_vectorstore(force_rebuild: bool = False) -> VectorRetriever:
    """Get or create the vectorstore singleton (thread-safe)."""
    global _vectorstore_instance, _bm25_retriever, _embeddings_instance

    # 快速路径：实例已存在且不需要重建
    if _vectorstore_instance is not None and not force_rebuild:
        return _vectorstore_instance

    with _instance_lock:
        # 双重检查：锁内再次验证
        if _vectorstore_instance is not None and not force_rebuild:
            return _vectorstore_instance

        _embeddings_instance = get_embeddings()
        os.makedirs(config.CHROMA_PERSIST_DIR, exist_ok=True)

        if _vectorstore_instance is None or force_rebuild:
            _vectorstore_instance = VectorRetriever()
            _vectorstore_instance.initialize(_embeddings_instance)

        if _bm25_retriever is None:
            _bm25_retriever = BM25Retriever()

        return _vectorstore_instance


def hybrid_search(question: str, k: Optional[int] = None) -> List[Document]:
    """Hybrid search: BM25 + vector similarity with RRF fusion."""
    k = k or config.RETRIEVER_K
    vs = get_vectorstore()
    bm25 = _bm25_retriever

    vector_results = vs.similarity_search_with_score(question, k=k)
    bm25_results = bm25.retrieve(question, k=k) if bm25 else []

    if not vector_results and not bm25_results:
        return []

    return rrf_fusion(vector_results, bm25_results, k=60, top_n=k)


def add_documents_to_store(docs: List[Document]):
    """Incrementally add documents to vectorstore and BM25."""
    global _vectorstore_instance, _bm25_retriever, _embeddings_instance

    if not docs:
        return

    vs = get_vectorstore()
    chunks = split_documents(docs)

    if not chunks:
        return

    vs.add_documents(chunks)

    # Add to BM25
    if _bm25_retriever:
        _bm25_retriever.add_documents(chunks)
    else:
        _bm25_retriever = BM25Retriever()
        _bm25_retriever.build_index(chunks)

    logger.info(f"增量添加 {len(chunks)} 个文档块到向量库")


def remove_document_from_store(source: str) -> bool:
    """Remove all chunks from a given source file from vectorstore and BM25."""
    global _vectorstore_instance, _bm25_retriever

    vs = get_vectorstore()
    removed = False
    try:
        vs.remove_by_source(source)
        removed = True
    except Exception as e:
        logger.error(f"从向量库删除文档失败: {e}")

    if _bm25_retriever:
        try:
            _bm25_retriever.remove_by_source(source)
            removed = True
        except Exception as e:
            logger.error(f"从 BM25 删除文档失败: {e}")

    return removed


def rebuild_vectorstore():
    """Full rebuild — clears everything and rebuilds from scratch."""
    global _vectorstore_instance, _bm25_retriever, _embeddings_instance
    _vectorstore_instance = None
    _bm25_retriever = None
    _embeddings_instance = None
    _TOKEN_CACHE.clear()
    if os.path.exists(config.CHROMA_PERSIST_DIR):
        shutil.rmtree(config.CHROMA_PERSIST_DIR, ignore_errors=True)
    os.makedirs(config.CHROMA_PERSIST_DIR, exist_ok=True)
    return get_vectorstore(force_rebuild=True)

# ---------------------------------------------------------------------------
#  Prompt template
# ---------------------------------------------------------------------------

RAG_TEMPLATE = """你是一个基于内部知识库的智能助手。请严格根据以下上下文信息和聊天历史回答问题。

聊天历史：
{chat_history}

上下文：
{context}

问题：{question}

请根据上述上下文回答问题。如果上下文没有提供足够信息，请直接使用你的知识回答问题。"""

PROMPT = ChatPromptTemplate.from_template(RAG_TEMPLATE)


def format_docs(docs: List[Document]) -> str:
    return "\n\n".join(d.page_content for d in docs)


def get_sources(docs: List[Document]) -> List[Dict]:
    seen = set()
    sources = []
    for doc in docs:
        source = doc.metadata.get("source", "unknown")
        if source not in seen:
            seen.add(source)
            sources.append({"file": source, "content_preview": doc.page_content[:100]})
    return sources

# ---------------------------------------------------------------------------
#  RAG Chain — unified class
# ---------------------------------------------------------------------------

def _parse_input(input_data):
    """Parse input_data into (question, conversation_id) tuple."""
    if isinstance(input_data, str):
        return input_data, "default"
    elif isinstance(input_data, dict):
        return input_data.get("question", ""), input_data.get("conversation_id", "default")
    return str(input_data), "default"


def _compute_relevance(question: str, context: str) -> float:
    """Compute token-overlap relevance ratio (0.0-1.0)."""
    q_tokens = set(tokenize(question))
    c_tokens = set(tokenize(context))
    if not q_tokens:
        return 0.0
    return len(q_tokens & c_tokens) / len(q_tokens)


class RAGChain:
    """Unified RAG chain supporting both sync and streaming modes."""

    def __init__(self):
        self._llm = None
        self._ready = False

    def ensure_initialized(self):
        """Lazy initialization — called on first use."""
        if self._ready:
            return
        get_vectorstore()
        self._llm = get_llm()
        self._ready = True

    def get_stats(self):
        """Return statistics about the vectorstore and documents."""
        vs = get_vectorstore()
        doc_count = vs.get_document_count() if hasattr(vs, 'get_document_count') else 0
        bm25_count = len(_bm25_retriever.documents) if _bm25_retriever else 0
        chroma_exists = False
        try:
            if vs and hasattr(vs, "vectorstore") and vs.vectorstore is not None:
                all_data = vs.vectorstore.get()
                chroma_exists = bool(all_data and all_data.get("ids"))
        except Exception as e:
            logger.debug(f"chroma_exists check failed: {e}")
        return {
            "document_count": doc_count,
            "bm25_document_count": bm25_count,
            "vectorstore_initialized": vs is not None,
            "bm25_initialized": _bm25_retriever is not None,
            "vector_count": doc_count,
            "chroma_exists": chroma_exists,
        }

    def list_documents(self):
        """Return a list of all documents in the vectorstore."""
        vs = get_vectorstore()
        if not vs or not vs.vectorstore:
            return []
        try:
            all_data = vs.vectorstore.get()
            metadatas = all_data.get("metadatas") or []
            if metadatas:
                seen = set()
                docs = []
                from app import config as _cfg
                data_path = _cfg.DATA_PATH
                for meta in metadatas:
                    if not meta:
                        continue
                    source = meta.get("source", "unknown")
                    if source not in seen:
                        seen.add(source)
                        file_path = os.path.join(data_path, os.path.basename(source))
                        size = os.path.getsize(file_path) if os.path.isfile(file_path) else 0
                        chunks = sum(1 for m in metadatas if m and m.get("source") == source)
                        docs.append({"filename": source, "size": size, "chunks": chunks, **meta})
                return docs
        except Exception as e:
            logger.error(f"Failed to list documents: {e}", exc_info=True)
        return []

    def _get_answer_context(self, question: str) -> dict:
        """Run retrieval: hybrid search → relevance check → format."""
        docs = hybrid_search(question, k=config.RETRIEVER_K)
        if not docs:
            return {"docs": [], "context": "", "sources": [], "has_relevant_context": False}

        context = format_docs(docs)
        sources = get_sources(docs)

        return {
            "docs": docs,
            "context": context,
            "sources": sources,
            "has_relevant_context": True,
        }

    def _fallback_answer(self, question: str) -> str:
        """Answer directly from LLM without context."""
        llm = self._llm
        if llm is None:
            return "抱歉，模型未初始化，无法回答问题。"
        answer = llm.invoke(f"请回答问题：{question}")
        if hasattr(answer, "content"):
            answer = answer.content  # type: ignore
        return answer if isinstance(answer, str) else str(answer)

    def invoke(self, input_data) -> dict:
        """Synchronous RAG chain call."""
        self.ensure_initialized()
        question, conversation_id = _parse_input(input_data)

        if not question.strip():
            return {"answer": "请提供有效的问题", "sources": [], "data_source": "llm"}

        try:
            ctx = self._get_answer_context(question)
            memory = get_memory(conversation_id)
            chat_history = memory.load_memory_variables({}).get("chat_history", "")

            if not ctx["has_relevant_context"]:
                answer = self._fallback_answer(question)
                data_source = "llm" if ctx["docs"] else "llm"
                memory.save_context({"question": question}, {"answer": answer})
                return {"answer": answer, "sources": [], "data_source": data_source}

            inputs = {
                "context": ctx["context"],
                "question": question,
                "chat_history": chat_history,
            }
            answer = (PROMPT | self._llm | StrOutputParser()).invoke(inputs)  # type: ignore
            memory.save_context({"question": question}, {"answer": answer})
            return {"answer": answer, "sources": ctx["sources"], "data_source": "rag"}

        except Exception as e:
            logger.error(f"RAG 链执行失败: {e}")
            try:
                return {"answer": self._fallback_answer(question), "sources": [], "data_source": "llm_fallback"}
            except Exception as fe:
                logger.error(f"LLM 兜底也失败: {fe}")
                return {"answer": f"系统错误: {fe}", "sources": [], "data_source": "error"}

    async def ainvoke(self, input_data) -> dict:
        """Async RAG chain call (offloads sync work to thread pool)."""
        return await asyncio.get_event_loop().run_in_executor(_executor, self.invoke, input_data)

    # ----- Intent-aware routing (new) ---------------------------------

    def route_invoke(self, input_data, username: str = "", conversation_id: str = "default", force_search: bool = False) -> dict:
        """
        Run intent classification + dialog routing on the user turn.
        Returns a dict including the chosen intent and answer.
        """
        self.ensure_initialized()

        from app.agents import classify, route as _route

        if isinstance(input_data, dict):
            question = input_data.get("question", "")
            conversation_id = input_data.get("conversation_id", conversation_id) or conversation_id
            username = input_data.get("username", username) or username
            force_search = bool(input_data.get("force_search", force_search))
        else:
            question = str(input_data or "")

        if not question.strip():
            return {"answer": "请提供有效的问题", "sources": [], "data_source": "error",
                    "intent": "error", "intent_confidence": 0.0}

        try:
            history = get_memory(conversation_id).load_memory_variables({}).get("chat_history", [])
        except Exception:
            history = []

        try:
            intent_res = classify(question, history)
        except Exception as e:
            logger.warning(f"Intent classification failed: {e}")
            from app.agents.intent_classifier import IntentResult, INTENT_KB_QA
            intent_res = IntentResult(intent=INTENT_KB_QA, confidence=0.5)

        try:
            from app.memory.long_term import remember_turn
            remember_turn(username, question)
        except Exception as e:
            logger.debug(f"remember_turn failed: {e}")

        return _route(self, question, intent_res, username=username, conversation_id=conversation_id, force_search=force_search)

    async def aroute_invoke(self, input_data, username: str = "", conversation_id: str = "default") -> dict:
        return await asyncio.get_event_loop().run_in_executor(
            _executor, lambda: self.route_invoke(input_data, username=username, conversation_id=conversation_id)
        )

    async def route_stream(self, input_data, username: str = "", conversation_id: str = "default"):
        """
        Async generator that streams the routed response as SSE event dicts.
        """
        self.ensure_initialized()
        from app.agents import classify, route_stream as _route_stream

        if isinstance(input_data, dict):
            question = input_data.get("question", "")
            conversation_id = input_data.get("conversation_id", conversation_id) or conversation_id
            username = input_data.get("username", username) or username
            force_search = input_data.get("force_search", False)
        else:
            question = str(input_data or "")
            force_search = False

        if not question.strip():
            yield {"type": "final", "content": {
                "answer": "请提供有效的问题", "sources": [], "data_source": "error",
                "intent": "error", "intent_confidence": 0.0,
            }}
            return

        try:
            history = get_memory(conversation_id).load_memory_variables({}).get("chat_history", [])
        except Exception:
            history = []

        try:
            intent_res = classify(question, history)
        except Exception as e:
            logger.warning(f"Intent classification failed: {e}")
            from app.agents.intent_classifier import IntentResult, INTENT_KB_QA
            intent_res = IntentResult(intent=INTENT_KB_QA, confidence=0.5)

        try:
            from app.memory.long_term import remember_turn
            remember_turn(username, question)
        except Exception as e:
            logger.debug(f"remember_turn failed: {e}")

        async for event in _aiter_sync(self, question, intent_res, username, conversation_id, force_search):
            yield event

    async def stream(self, input_data):
        """Async generator yielding SSE events."""
        self.ensure_initialized()
        question, conversation_id = _parse_input(input_data)

        if not question.strip():
            yield {"type": "final", "content": {"answer": "请提供有效的问题", "sources": [], "data_source": "llm"}}
            return

        llm = self._llm

        def _sync_retrieve():
            return self._get_answer_context(question)

        try:
            ctx = await asyncio.get_event_loop().run_in_executor(_executor, _sync_retrieve)
            memory = get_memory(conversation_id)
            chat_history = memory.load_memory_variables({}).get("chat_history", "")

            if not ctx["has_relevant_context"]:
                answer = await asyncio.get_event_loop().run_in_executor(
                    _executor, self._fallback_answer, question
                )
                memory.save_context({"question": question}, {"answer": answer})
                yield {"type": "final", "content": {"answer": answer, "sources": [], "data_source": "llm"}}
                return

            yield {"type": "sources", "content": ctx["sources"]}

            full_answer = ""
            inputs = {
                "context": ctx["context"],
                "question": question,
                "chat_history": chat_history,
            }

            try:
                for chunk in (PROMPT | llm | StrOutputParser()).stream(inputs):  # type: ignore
                    full_answer += chunk  # type: ignore
                    yield {"type": "chunk", "content": chunk}
            except Exception as se:
                logger.warning(f"流式输出失败，切换为同步模式: {se}")
                answer = llm.invoke(inputs)  # type: ignore
                if hasattr(answer, "content"):
                    answer = answer.content  # type: ignore
                full_answer = answer if isinstance(answer, str) else str(answer)
                yield {"type": "chunk", "content": full_answer}

            memory.save_context({"question": question}, {"answer": full_answer})
            yield {"type": "final", "content": {"answer": full_answer, "sources": ctx["sources"], "data_source": "rag"}}

        except Exception as e:
            logger.error(f"流式 RAG 链执行失败: {e}")
            try:
                answer = await asyncio.get_event_loop().run_in_executor(
                    _executor, self._fallback_answer, question
                )
                yield {"type": "final", "content": {"answer": answer, "sources": [], "data_source": "llm_fallback"}}
            except Exception as fe:
                logger.error(f"LLM 兜底也失败: {fe}")
                yield {"type": "final", "content": {"answer": f"系统错误: {fe}", "sources": [], "data_source": "error"}}


async def _aiter_sync(chain, question, intent_res, username, conversation_id, force_search=False):
    from app.agents import route_stream as _route_stream
    for ev in _route_stream(chain, question, intent_res, username=username, conversation_id=conversation_id, force_search=force_search):
        yield ev


# ---------------------------------------------------------------------------
#  Module-level convenience singletons
# ---------------------------------------------------------------------------

_rag_chain_instance: Optional[RAGChain] = None

def get_rag_chain() -> RAGChain:
    global _rag_chain_instance
    if _rag_chain_instance is None:
        _rag_chain_instance = RAGChain()
    return _rag_chain_instance


def get_streaming_rag_chain() -> RAGChain:
    """Same as get_rag_chain — the RAGChain class supports both sync and stream."""
    return get_rag_chain()