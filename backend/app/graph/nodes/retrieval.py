"""
检索节点：知识库混合检索、联网检索、指定文件检索。

对应旧 `router.py` 里的 `_handle_kb_qa` 检索段、web search 段 与 `_handle_file_qa`。
"""

from __future__ import annotations

from app.core.logging import get_logger
from app.graph.edges import needs_web_search
from app.graph.nodes import Deps
from app.graph.state import (
    RAGState,
    docs_to_dicts,
    sources_from_state,
)

logger = get_logger(__name__)

_WEB_MAX_RESULTS = 5
_WEB_TIMEOUT = 15


def retrieve(state: RAGState, deps: Deps) -> dict:
    """知识库混合检索（BM25 + 向量 + RRF）。

    同时承担旧实现里那段隐式降级：
    「无文档且无需联网 → 意图改写为 general」，
    现在是一次显式、可观测的状态更新。

    扩展挂点（当前未接入）：
      * 查询改写 → 在 `hybrid_search` **之前**
      * 结果重排 → 在 `hybrid_search` **之后**、`sources` 生成之前
      * 元数据过滤 / 多模态检索 → 作为独立条件边，而非塞进本节点

    原 v3 仓库里的 `query_rewriter` / `reranker` / `metadata_filter` /
    `graph_rag` / `multimodal_retriever` 五个模块**从未被任何路径调用**，
    已在 P6 删除 —— 与其留着"看起来有、实际不生效"的代码，
    不如在真正需要时按上述挂点写成图节点。
    """
    from app.agents.intent_classifier import INTENT_GENERAL
    from app.rag.vectorstore import hybrid_search

    question = state.get("question", "")
    try:
        docs = hybrid_search(question)
    except Exception as exc:  # noqa: BLE001  检索失败降级为无上下文
        logger.error(f"知识库检索失败: {exc}", exc_info=True)
        docs = []

    doc_dicts = docs_to_dicts(docs)
    update: dict = {
        "docs": doc_dicts,
        "sources": sources_from_state(doc_dicts),
    }

    if not doc_dicts and not needs_web_search(state):
        # 与旧 `route_stream()` 中 `if not docs and not search_context: intent = general` 等价
        update["intent"] = INTENT_GENERAL
        update["data_source"] = "llm"
        logger.info("知识库无命中且无需联网，降级为 general")

    return update


def web_search(state: RAGState, deps: Deps) -> dict:
    """Tavily 联网检索。失败不抛异常，仅留空上下文。"""
    from app.tools.web_search import build_search_context, search

    question = state.get("question", "")
    try:
        logger.info(f"触发联网检索: {question[:50]}")
        result = search(question, max_results=_WEB_MAX_RESULTS, timeout=_WEB_TIMEOUT)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"联网检索失败: {exc}")
        return {"search_context": ""}

    if not result.get("success"):
        logger.debug(f"联网检索无结果: {result.get('error')}")
        return {"search_context": ""}

    context, sources = build_search_context(result)
    logger.debug(f"联网上下文长度: {len(context)}, 来源数: {len(sources)}")
    return {"search_context": context, "sources": sources}


def file_qa_retrieve(state: RAGState, deps: Deps) -> dict:
    """按文件名过滤向量库。

    文件名一定存在 —— 无文件名的情况已由 `classify_intent` 降级为 general。
    """
    from app.rag.vectorstore import get_vectorstore

    filename = state.get("target_filename") or ""
    question = state.get("question", "")
    overshoot_k = max(20, deps.settings.retrieval.retriever_k)

    try:
        store = get_vectorstore()
        candidates = store.similarity_search(question, k=overshoot_k)
    except Exception as exc:  # noqa: BLE001
        logger.error(f"file_qa 检索失败: {exc}")
        candidates = []

    lowered = filename.lower()
    matched = [
        d for d in candidates if str(d.metadata.get("source", "")).lower() == lowered
    ]
    if not matched:
        matched = [
            d for d in candidates if lowered in str(d.metadata.get("source", "")).lower()
        ]

    if not matched:
        # 未命中：直接产出提示语并跳过生成阶段（由 route_after_file_qa 决定去向）
        answer = f"未在知识库中找到文件 {filename}，请确认文件已上传。"
        from app.graph.nodes.respond import emit

        emit(
            {
                "type": "intent",
                "content": {
                    "intent": state.get("intent"),
                    "confidence": state.get("intent_confidence"),
                },
            }
        )
        emit({"type": "sources", "content": []})
        emit({"type": "chunk", "content": answer})
        return {
            "docs": [],
            "sources": [],
            "answer": answer,
            "data_source": "file_qa_miss",
        }

    matched = matched[: max(5, deps.settings.retrieval.retriever_k)]
    doc_dicts = docs_to_dicts(matched)
    return {
        "docs": doc_dicts,
        "sources": sources_from_state(doc_dicts),
    }
