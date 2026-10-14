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


def sources_from_state(docs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """从 State 中的 docs 生成 sources（等价于 `rag.get_sources`）。"""
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for doc in docs or []:
        source = (doc.get("metadata") or {}).get("source", "unknown")
        if source in seen:
            continue
        seen.add(source)
        out.append(
            {
                "file": source,
                "content_preview": (doc.get("page_content") or "")[:100],
            }
        )
    return out
