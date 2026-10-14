"""
条件边路由函数 —— 状态图里**唯一**的编排决策点。

对照旧实现：`app/agents/router.py` 的 `route()` 里有 7 个分支的 if/elif，
`route_stream()` 里又重复了一遍；本模块把这份决策收敛为一个函数。
"""

from __future__ import annotations

from app.agents.intent_classifier import (
    INTENT_AGENT,
    INTENT_CLARIFY,
    INTENT_FILE_QA,
    INTENT_GENERAL,
    INTENT_KB_QA,
    INTENT_LONG_TEXT,
    INTENT_TASK,
)
from app.graph.state import RAGState
from app.tools.web_search import should_search


def needs_web_search(state: RAGState) -> bool:
    """是否应触发联网检索（强制联网开关 OR 问题属实时性话题）。"""
    if state.get("force_search"):
        return True
    try:
        return bool(should_search(state.get("question", "")))
    except Exception:  # noqa: BLE001  搜索工具故障不应阻断路由
        return False


def route_by_intent(state: RAGState) -> str:
    """意图 → 下一个节点。图中唯一的「分发器」。"""
    intent = state.get("intent", INTENT_KB_QA)

    if intent == INTENT_CLARIFY:
        return "clarify"
    if intent == INTENT_LONG_TEXT:
        return "long_text"
    if intent == INTENT_FILE_QA:
        return "file_qa_retrieve"
    if intent == INTENT_AGENT:
        return "agent"

    if intent == INTENT_GENERAL:
        # 与旧 `_handle_general` 一致：general **不做知识库检索**，
        # 只在需要实时信息时联网（否则会把无关的 KB 片段塞进闲聊回答）。
        return "web_search" if needs_web_search(state) else "build_prompt"

    if intent == INTENT_TASK:
        # 与旧 `_handle_task` 一致：显式任务请求不检索、不联网
        return "build_prompt"

    # 默认 kb_qa：先做混合检索
    return "retrieve"


def route_after_retrieve(state: RAGState) -> str:
    """知识库检索之后：还需要联网补一次吗？

    对应旧代码里
    `if not docs and not search_context: intent_res.intent = INTENT_GENERAL`
    这种运行时改写意图的隐式跳转 —— 现在是一个可读、可测、可观测的显式函数。

    注意：`retrieve` 节点已在「无文档且无需联网」时把 intent 降级为 general，
    本函数只负责决定是否补联网。
    """
    if needs_web_search(state) and not state.get("search_context"):
        return "web_search"
    return "build_prompt"


def route_after_file_qa(state: RAGState) -> str:
    """文件未命中时跳过生成：`file_qa_retrieve` 已直接把提示语写入 answer。"""
    if state.get("data_source") == "file_qa_miss":
        return "persist"
    return "build_prompt"
