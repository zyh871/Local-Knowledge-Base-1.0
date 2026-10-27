"""
Document ↔ dict 转换。

为什么单独放在 utils：
  * LangGraph 的 State 必须可 JSON 序列化（checkpointer 会序列化），
    LangChain `Document` 对象不可序列化 —— 这是转换存在的根本原因。
  * `app/graph`（State 契约）与 `app/rag`（检索缓存）都需要它，
    而分层规则禁止 rag 依赖 graph，因此下沉到公共 utils 层。
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "doc_to_dict",
    "dict_to_doc",
    "docs_to_dicts",
    "format_docs_from_state",
    "annotate_sources",
    "renumber_sources",
    "sources_from_state",
]


def doc_to_dict(doc: Any) -> dict[str, Any]:
    """LangChain Document → 可 JSON 序列化字典。"""
    if isinstance(doc, dict):
        return {
            "page_content": doc.get("page_content", ""),
            "metadata": dict(doc.get("metadata") or {}),
        }
    return {
        "page_content": getattr(doc, "page_content", ""),
        "metadata": dict(getattr(doc, "metadata", None) or {}),
    }


def dict_to_doc(payload: dict[str, Any]) -> Any:
    """字典 → LangChain Document。"""
    from langchain_core.documents import Document

    return Document(
        page_content=payload.get("page_content", ""),
        metadata=dict(payload.get("metadata") or {}),
    )


def docs_to_dicts(docs: Any) -> list[dict[str, Any]]:
    return [doc_to_dict(d) for d in (docs or [])]


def format_docs_from_state(docs: list[dict[str, Any]]) -> str:
    return "\n\n".join(d.get("page_content", "") for d in (docs or []))


def _confidence_for_position(position: int) -> str:
    """按**排序名次**给出置信度，而不是伪造一个相似度分数。

    RRF 融合会丢弃原始分数（只保留排序），因此这里能诚实表达的只有
    「第几条」。前端把 high/medium/low 显示为「高/中/低相关」，
    不会被误解为精确的语义相似度。
    """
    if position <= 1:
        return "high"
    if position <= 3:
        return "medium"
    return "low"


def annotate_sources(items: Any) -> list[dict[str, Any]]:
    """给来源列表补上编号、稳定 id 与置信度。

    `setdefault` 使这个函数可以安全地重复调用：知识库来源已经带好字段，
    联网来源只有 title/href/snippet，两者都能得到统一结构。
    """
    out: list[dict[str, Any]] = []
    for position, item in enumerate(items or [], 1):
        entry = dict(item)
        entry.setdefault("index", position)
        entry.setdefault("id", f"src_{position}")
        entry.setdefault("confidence", _confidence_for_position(position))
        out.append(entry)
    return out


def renumber_sources(items: Any) -> list[dict[str, Any]]:
    """按最终顺序**强制重排**编号与 id。

    必须在「知识库来源 + 联网来源」合并之后调用：两个节点各自从 1 开始编号，
    直接拼接会出现两个 `[1]`，点击引用就会跳到错误卡片。
    与 `annotate_sources` 的区别是这里**覆盖**已有 index/id 而不是保留。
    """
    out: list[dict[str, Any]] = []
    for position, item in enumerate(items or [], 1):
        entry = dict(item)
        entry["index"] = position
        entry["id"] = f"src_{position}"
        out.append(entry)
    return out


def sources_from_state(docs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """从 State 中的 docs 生成 sources（等价于 `rag.get_sources`）。

    在原有 `file` / `content_preview` 之外补充溯源所需字段：

      * `index` / `id` —— 与答案里的 `[1]` 引用一一对应
      * `confidence`  —— 排序置信度（high / medium / low）
      * `content`     —— 完整分块（截断到 1200 字），供「展开原文」使用
      * `chunk_id`    —— 文档分块 id（来自元数据，可能为 None）
      * `score`       —— 原始分数；RRF 不保留，故为 None

    这些都是**新增字段**，原有读取方式不受影响。
    """
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for doc in docs or []:
        metadata = doc.get("metadata") or {}
        source = metadata.get("source", "unknown")
        if source in seen:
            continue
        seen.add(source)
        content = doc.get("page_content") or ""
        out.append(
            {
                "file": source,
                "content_preview": content[:100],
                "content": content[:1200],
                "chunk_id": metadata.get("chunk_id"),
                "score": None,
            }
        )
    return annotate_sources(out)
