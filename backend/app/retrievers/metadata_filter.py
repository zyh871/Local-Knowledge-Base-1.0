"""
元数据过滤检索 — 在向量检索时按 source / tag / date 等维度过滤。

Chroma 原生支持 where 过滤，本模块封装为统一接口，并扩展 BM25 侧的内存过滤。

接入点:
  - VectorRetriever.similarity_search_with_score 传入 where 参数
  - hybrid_search 增加 metadata_filter 参数透传
"""

import logging
import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from langchain_core.documents import Document

logger = logging.getLogger(__name__)


def parse_filter_from_query(question: str) -> Optional[Dict[str, Any]]:
    """
    从自然语言问题中解析元数据过滤条件。

    支持:
      - 文件名: "在 xxx.pdf 中" / "关于 xxx.md"
      - 文件类型: "pdf文档" / "word文件" / "txt"
      - 时间: "最近的" / "今天的"（需配合 metadata.created_at）
    """
    where: Dict[str, Any] = {}

    filename_match = re.search(
        r"(?:在|关于|从)\s*([\w\u4e00-\u9fa5\-_.]+\.(?:pdf|docx?|pptx?|xlsx?|txt|md|csv))",
        question, re.IGNORECASE,
    )
    if filename_match:
        where["source"] = filename_match.group(1)

    if not where.get("source"):
        ext_map = {
            "pdf": ".pdf", "word": ".docx", "docx": ".docx",
            "ppt": ".pptx", "pptx": ".pptx", "excel": ".xlsx",
            "xlsx": ".xlsx", "txt": ".txt", "markdown": ".md", "md": ".md",
        }
        for keyword, ext in ext_map.items():
            if re.search(rf"\b{keyword}\b", question, re.IGNORECASE):
                where["_file_type"] = ext
                break

    return where if where else None


def build_chroma_where(filter_dict: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """将统一 filter_dict 转换为 Chroma where 语法。"""
    if not filter_dict:
        return None

    conditions = []
    for key, value in filter_dict.items():
        if key == "source":
            conditions.append({"source": {"$eq": value}})
        elif key == "source_contains":
            conditions.append({"source": {"$like": f"%{value}%"}})
        elif key == "tag":
            conditions.append({"tag": {"$eq": value}})
        elif key == "_file_type":
            conditions.append({"source": {"$like": f"%{value}"}})

    if not conditions:
        return None
    if len(conditions) == 1:
        return conditions[0]
    return {"$and": conditions}


def filter_bm25(docs: List[Tuple[Document, float]], filter_dict: Optional[Dict[str, Any]]) -> List[Tuple[Document, float]]:
    """对 BM25 结果做内存过滤。"""
    if not filter_dict:
        return docs

    result = []
    for doc, score in docs:
        source = doc.metadata.get("source", "")
        match = True
        for key, value in filter_dict.items():
            if key == "source" and source != value:
                match = False
                break
            elif key == "source_contains" and value not in source:
                match = False
                break
            elif key == "_file_type" and not source.endswith(value):
                match = False
                break
            elif key == "tag" and doc.metadata.get("tag") != value:
                match = False
                break
        if match:
            result.append((doc, score))
    return result


def filtered_hybrid_search(
    question: str,
    k: int = 5,
    metadata_filter: Optional[Dict[str, Any]] = None,
    auto_parse: bool = True,
) -> List[Document]:
    """
    带元数据过滤的混合检索。

    参数:
        metadata_filter: 显式过滤条件，如 {"source": "xxx.pdf"}
        auto_parse: 为 True 时自动从问题中解析过滤条件，与显式条件合并

    用法:
        docs = filtered_hybrid_search(question, k=5, metadata_filter={"tag": "技术"})
        # 或自动解析: docs = filtered_hybrid_search("在面试题.md中 Agent 是什么", k=5)
    """
    from app.rag_chain import get_vectorstore, _bm25_retriever
    from app.retrievers.rrf_fusion import rrf_fusion

    final_filter = dict(metadata_filter or {})
    if auto_parse:
        parsed = parse_filter_from_query(question)
        if parsed:
            final_filter.update(parsed)

    chroma_where = build_chroma_where(final_filter or None)
    vs = get_vectorstore()

    vector_results = []
    try:
        if chroma_where:
            raw = vs.vectorstore.similarity_search_with_score(question, k=k, where=chroma_where)
            vector_results = raw
        else:
            vector_results = vs.similarity_search_with_score(question, k=k)
    except Exception as e:
        logger.error(f"过滤向量检索失败: {e}")

    bm25_results = []
    if _bm25_retriever:
        raw_bm25 = _bm25_retriever.retrieve(question, k=k * 2)
        bm25_results = filter_bm25(raw_bm25, final_filter or None)

    if not vector_results and not bm25_results:
        return []

    return rrf_fusion(vector_results, bm25_results, k=60, top_n=k)
