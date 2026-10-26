"""
状态图的数据契约。

**关键约束：State 必须可 JSON 序列化。**
LangGraph 的 checkpointer 会把 State 序列化后写入存储；LangChain 的
`Document` 对象不可序列化，因此检索结果统一转成
`{"page_content": str, "metadata": dict}` 的字典形式。

reducer 说明（LangGraph 的并发合并语义，必须显式声明）：
    * `sources`  —— 自定义去重合并：`retrieve` 与 `web_search` 可能先后写入
    * `messages` —— `operator.add` 累加：Agent 子图的标准写法
    * 其余字段    —— 默认「后写覆盖」，符合本流程的线性语义
"""

from __future__ import annotations

import operator
from typing import Annotated, Any, TypedDict


def merge_sources(left: list[dict], right: list[dict]) -> list[dict]:
    """sources 去重合并：按 (file/url, content_preview) 去重且保持顺序。"""
    seen: set[tuple] = set()
    merged: list[dict] = []
    for item in [*(left or []), *(right or [])]:
        if not isinstance(item, dict):
            continue
        key = (
            item.get("file") or item.get("url") or item.get("source"),
            item.get("content_preview"),
        )
        if key in seen:
            continue
        seen.add(key)
        merged.append(item)
    return merged


def merge_timings(left: dict, right: dict) -> dict:
    return {**(left or {}), **(right or {})}


class RAGState(TypedDict, total=False):
    """问答状态图的状态。字段按流转阶段分组注释。"""

    # ── 输入（ingest 之前由 runner 写入，之后只读）──────────────
    question: str
    conversation_id: str
    username: str
    force_search: bool
    trace_id: str
    # 按请求覆盖模型（前端模型切换控件）；空串 = 用服务端默认模型
    model: str
    # 是否展示模型的推理过程（深度思考）。仅对支持 reasoning_content 的模型有效
    thinking: bool

    # ── 上下文装载（ingest 节点）───────────────────────────────
    history: list[dict[str, str]]
    profile_ctx: str
    profile_lang: str
    lang_instruction: str
    locked: bool

    # ── 分类结果（classify_intent 节点）────────────────────────
    intent: str
    intent_confidence: float
    needs_clarification: bool
    clarification_prompt: str
    detected_language: str
    # file_qa 路径需要记住用户引用的文件名
    target_filename: str

    # ── 检索产物（retrieve / web_search / file_qa_retrieve）────
    docs: list[dict[str, Any]]  # ★ Document → dict，保证可序列化
    sources: Annotated[list[dict[str, Any]], merge_sources]
    search_context: str
    context: str

    # ── 生成产物（build_prompt / generate）─────────────────────
    prompt: str
    answer: str
    data_source: str
    # 实际使用的模型名（回显给前端）与是否被截断
    used_model: str
    truncated: bool
    # 推理过程（深度思考）。reasoning 只在本次请求内有效，不写入对话记忆 ——
    # 它属于「模型的草稿」，多轮对话里带上反而会干扰后续回答。
    reasoning: str
    reasoning_ms: int

    # ── Agent 子图产物 ────────────────────────────────────────
    messages: Annotated[list[Any], operator.add]
    tool_calls: list[dict[str, Any]]
    iterations: int

    # ── 可观测性 ─────────────────────────────────────────────
    node_timings: Annotated[dict[str, float], merge_timings]
    error: str | None
    degraded: bool


class AgentState(TypedDict, total=False):
    """工具调用子图状态（复用 RAGState 的 messages 累加语义）。"""

    question: str
    history: list[dict[str, str]]
    lang_instruction: str
    messages: Annotated[list[Any], operator.add]
    tool_calls: list[dict[str, Any]]
    iterations: int
    answer: str
    sources: Annotated[list[dict[str, Any]], merge_sources]


# ---------------------------------------------------------------------------
#  Document <-> dict 转换
# ---------------------------------------------------------------------------
# 实现在 app/utils/documents.py（rag 层也需要，且分层规则禁止 rag 依赖 graph）。
# 此处重新导出以保持 graph 内部的调用习惯。

from app.utils.documents import (  # noqa: E402
    dict_to_doc,
    doc_to_dict,
    docs_to_dicts,
    format_docs_from_state,
    sources_from_state,
)

__all__ = [
    "RAGState",
    "AgentState",
    "merge_sources",
    "merge_timings",
    "doc_to_dict",
    "dict_to_doc",
    "docs_to_dicts",
    "format_docs_from_state",
    "sources_from_state",
]
