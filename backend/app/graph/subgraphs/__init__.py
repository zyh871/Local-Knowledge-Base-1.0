"""
工具调用子图。

把 v3 里 `agents/function_calling.py` 手写的 `for iteration in range(5)` 循环
替换为标准 ReAct 结构：`model ⇄ tools`，由条件边决定继续还是结束。

     ┌─────────┐   有 tool_calls    ┌─────────┐
     │  model  │ ─────────────────▶ │  tools  │
     └────┬────┘                    └────┬────┘
          │ 无 tool_calls                │
          ▼                              │
         END ◀───────────────────────────┘
"""

from __future__ import annotations

import json
from typing import Any

from langgraph.graph import END, START, StateGraph

from app.core.logging import get_logger
from app.graph.state import AgentState

logger = get_logger(__name__)


def _should_continue(state: AgentState, max_iterations: int) -> str:
    """条件边：还有工具调用且未超轮次 → 继续执行工具；否则结束。"""
    if state.get("iterations", 0) >= max_iterations:
        logger.info(f"Agent 达到最大轮次 {max_iterations}，结束工具循环")
        return END
    last = (state.get("messages") or [None])[-1]
    return "tools" if getattr(last, "tool_calls", None) else END


def build_agent_subgraph(deps: Any, registry: Any, llm_with_tools: Any):
    """构建并编译工具调用子图。"""
    from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage

    system_prompt = _system_prompt(registry)
    max_iterations = deps.settings.graph.max_agent_iterations

    def call_model(state: AgentState) -> dict:
        messages = state.get("messages") or []
        response = llm_with_tools.invoke([SystemMessage(content=system_prompt), *messages])
        return {"messages": [response]}

    def run_tools(state: AgentState) -> dict:
        last = (state.get("messages") or [None])[-1]
        tool_messages: list[Any] = []
        calls: list[dict[str, Any]] = []
        for index, call in enumerate(getattr(last, "tool_calls", None) or []):
            name = call.get("name")
            args = call.get("args") or {}
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except json.JSONDecodeError:
                    args = {}
            logger.info(f"Agent 调用工具: {name}({args})")
            result = registry.execute(name, **args)
            calls.append(
                {
                    "tool": name,
                    "arguments": args,
                    "result_preview": str(
                        result.get("result", result.get("error", ""))
                    )[:200],
                }
            )
            tool_messages.append(
                ToolMessage(
                    content=json.dumps(result, ensure_ascii=False, default=str),
                    tool_call_id=call.get("id") or f"call_{index}",
                )
            )
        return {
            "messages": tool_messages,
            "tool_calls": [*(state.get("tool_calls") or []), *calls],
            "iterations": state.get("iterations", 0) + 1,
        }

    def _route(state: AgentState) -> str:
        return _should_continue(state, max_iterations)

    graph = StateGraph(AgentState)
    graph.add_node("model", call_model)
    graph.add_node("tools", run_tools)
    graph.add_edge(START, "model")
    graph.add_conditional_edges("model", _route, {"tools": "tools", END: END})
    graph.add_edge("tools", "model")
    return graph.compile()


def build_initial_messages(
    question: str, history: list[dict[str, str]], limit: int = 6
) -> list[Any]:
    """把对话历史 + 当前问题转成 LangChain 消息序列（与 v3 取最近 6 条一致）。"""
    from langchain_core.messages import AIMessage, HumanMessage

    messages: list[Any] = []
    for item in (history or [])[-limit:]:
        content = item.get("content", "")
        if item.get("role") == "user":
            messages.append(HumanMessage(content=content))
        else:
            messages.append(AIMessage(content=content))
    messages.append(HumanMessage(content=question))
    return messages


def _system_prompt(registry: Any) -> str:
    lines: list[str] = []
    for tool in registry.list_all():
        params = ", ".join(
            f"{key}: {value.get('description', '')}"
            for key, value in (tool.parameters.get("properties") or {}).items()
        )
        lines.append(f"- {tool.name}({params}): {tool.description}")
    tools_description = "\n".join(lines)
    return f"""你是一个智能助手，可以使用以下工具来回答问题。

可用工具：
{tools_description}

使用规则：
1. 如果问题可以直接回答，不需要使用工具
2. 如果需要查询信息或计算，选择合适的工具
3. 工具调用后，基于返回结果综合回答
4. 回答要准确、简洁，使用与用户相同的语言
"""
