"""
工具调用（Agent）节点。

策略：
  * 后端支持原生 function calling（modelscope / openai）→ 走 `agent` 子图
  * 否则（local / mock / ollama）→ 复用 v3 的 `run_function_calling_agent`，
    其内部会回退到 ReAct 文本解析模式

这样既拿到了 LangGraph 的标准工具循环，又完整保留了非原生后端的回退能力。
"""

from __future__ import annotations

from typing import Any

from app.agents.intent_classifier import INTENT_AGENT
from app.core.logging import get_logger
from app.graph.nodes import Deps
from app.graph.nodes.respond import emit
from app.graph.state import RAGState

logger = get_logger(__name__)


def _sources_from_tool_calls(tool_calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """与 v3 的 sources 推导规则一致。"""
    sources: list[dict[str, Any]] = []
    for call in tool_calls or []:
        name = call.get("tool")
        if name == "web_search":
            sources.append({"source": "tavily", "tool_call": name})
        elif name == "knowledge_query":
            sources.append({"source": "knowledge_base", "tool_call": name})
    return sources


def _legacy_agent(question: str, history: list[dict]) -> dict[str, Any]:
    from app.agents.function_calling import run_function_calling_agent

    return run_function_calling_agent(question, history=history)


def agent(state: RAGState, deps: Deps) -> dict:
    """执行工具调用循环，产出回答与工具调用轨迹。"""
    question = state.get("question", "")
    history = state.get("history") or []
    llm = deps.llm

    result: dict[str, Any]

    if llm is None or not deps.settings.model.supports_native_tool_calling:
        result = _run_legacy(question, history, deps)
    else:
        try:
            result = _run_subgraph(question, history, llm, deps)
        except Exception as exc:  # noqa: BLE001  子图失败回退到 v3 实现
            logger.error(f"Agent 子图执行失败，回退到 v3 实现: {exc}", exc_info=True)
            result = _run_legacy(question, history, deps)

    tool_calls = result.get("tool_calls") or []
    sources = _sources_from_tool_calls(tool_calls)
    answer = result.get("answer") or "（Agent 未产出回答）"

    emit(
        {
            "type": "intent",
            "content": {
                "intent": INTENT_AGENT,
                "confidence": state.get("intent_confidence"),
            },
        }
    )
    emit({"type": "sources", "content": sources})
    if tool_calls:
        emit({"type": "tool_calls", "content": tool_calls})
    emit({"type": "chunk", "content": answer})

    return {
        "answer": answer,
        "sources": sources,
        "tool_calls": tool_calls,
        "iterations": result.get("iterations", 0),
        "data_source": "agent",
    }


def _run_subgraph(
    question: str, history: list[dict], llm: Any, deps: Deps
) -> dict[str, Any]:
    """使用工具调用子图执行。"""
    from app.graph.subgraphs import build_agent_subgraph, build_initial_messages
    from app.tools.base import get_registry
    from app.utils.text import extract_text

    registry = get_registry()
    schemas = registry.to_function_schemas()
    if not schemas:
        return {"answer": extract_text(llm.invoke(question)), "tool_calls": [], "iterations": 0}

    llm_with_tools = llm.bind_tools(schemas)
    subgraph = build_agent_subgraph(deps, registry, llm_with_tools)

    initial = {
        "messages": build_initial_messages(question, history),
        "tool_calls": [],
        "iterations": 0,
    }
    # recursion_limit 按「每轮 2 个超级步」估算，留 2 倍余量
    config = {"recursion_limit": deps.settings.graph.max_agent_iterations * 4 + 4}
    final_state = subgraph.invoke(initial, config=config)

    messages = final_state.get("messages") or []
    answer = extract_text(messages[-1]) if messages else ""
    if not answer:
        answer = "（Agent 处理完成但未产出文本回答）"

    return {
        "answer": answer,
        "tool_calls": final_state.get("tool_calls") or [],
        "iterations": final_state.get("iterations", 0),
    }


def _run_legacy(question: str, history: list[dict], deps: Deps) -> dict[str, Any]:
    try:
        return _legacy_agent(question, history)
    except Exception as exc:  # noqa: BLE001
        logger.error(f"Agent 处理失败: {exc}", exc_info=True)
        return {
            "answer": f"Agent 处理失败: {exc}",
            "tool_calls": [],
            "iterations": 0,
        }
