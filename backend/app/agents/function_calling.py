"""
Function Calling Agent — 让 LLM 自主选择并调用工具。

流程:
  1. 将用户问题 + 可用工具 schema 发送给 LLM
  2. LLM 返回 function call（工具名 + 参数）或直接回答
  3. 执行工具调用，将结果回传给 LLM
  4. 循环直到 LLM 给出最终答案或达到最大轮次

支持两种后端:
  - modelscope/openai: 使用原生 function calling API
  - 其他后端: 回退到 ReAct 文本模式（见 react_agent.py）

接入点: router.py 中新增 INTENT_AGENT 路由，或替换复杂问题的处理。
"""

import json
import logging
from typing import Any, Dict, List, Optional

from app.tools.base import get_registry

logger = logging.getLogger(__name__)

_MAX_ITERATIONS = 5

SYSTEM_PROMPT = """你是一个智能助手，可以使用以下工具来回答问题。

可用工具：
{tools_description}

使用规则：
1. 如果问题可以直接回答，不需要使用工具
2. 如果需要查询信息或计算，选择合适的工具
3. 工具调用后，基于返回结果综合回答
4. 回答要准确、简洁，使用与用户相同的语言
"""


def _build_tools_description(registry) -> str:
    lines = []
    for tool in registry.list_all():
        params_desc = ", ".join(
            f"{k}: {v.get('description', '')}"
            for k, v in tool.parameters.get("properties", {}).items()
        )
        lines.append(f"- {tool.name}({params_desc}): {tool.description}")
    return "\n".join(lines)


def _extract_function_call(response) -> Optional[Dict[str, Any]]:
    """从 LLM 响应中提取 function call。"""
    tool_calls = getattr(response, "tool_calls", None)
    if tool_calls:
        tc = tool_calls[0]
        return {
            "name": tc.get("name") or tc.get("function", {}).get("name"),
            "arguments": tc.get("args") or tc.get("arguments") or {},
        }

    content = getattr(response, "content", "")
    if isinstance(content, str) and content.strip().startswith("{"):
        try:
            start, end = content.find("{"), content.rfind("}")
            if 0 <= start < end:
                parsed = json.loads(content[start:end + 1])
                if "tool" in parsed or "function" in parsed:
                    name = parsed.get("tool") or parsed.get("function", {}).get("name")
                    args = parsed.get("arguments") or parsed.get("args") or {}
                    if name:
                        return {"name": name, "arguments": args}
        except (json.JSONDecodeError, ValueError):
            pass
    return None


def _supports_native_function_calling() -> bool:
    from app.core.config import get_settings
    return get_settings().model.backend in ("modelscope", "openai")


def run_function_calling_agent(
    question: str,
    history: Optional[List[Dict[str, str]]] = None,
    max_iterations: int = _MAX_ITERATIONS,
    tool_names: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """
    运行 Function Calling Agent。

    参数:
        question: 用户问题
        history: 对话历史
        max_iterations: 最大工具调用轮次
        tool_names: 限制可用工具列表，None 表示全部

    返回:
        {"answer": str, "tool_calls": [...], "iterations": int}
    """
    from app.rag.providers import get_llm

    registry = get_registry()
    llm = get_llm()
    history = history or []

    all_tool_calls: List[Dict[str, Any]] = []

    if _supports_native_function_calling():
        return _run_native_fc(llm, question, history, registry, max_iterations, tool_names, all_tool_calls)
    else:
        from app.agents.react_agent import run_react_agent
        return run_react_agent(question, history, max_iterations, tool_names)


def _run_native_fc(
    llm,
    question: str,
    history: List[Dict[str, str]],
    registry,
    max_iterations: int,
    tool_names: Optional[List[str]],
    all_tool_calls: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """使用原生 function calling API。"""
    from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

    schemas = registry.to_function_schemas()
    if tool_names:
        schemas = [s for s in schemas if s["name"] in tool_names]

    if not schemas:
        result = llm.invoke(question)
        answer = getattr(result, "content", str(result))
        return {"answer": answer, "tool_calls": [], "iterations": 0}

    try:
        llm_with_tools = llm.bind_tools(schemas)
    except Exception as e:
        logger.warning(f"LLM 不支持 bind_tools，回退到 ReAct: {e}")
        from app.agents.react_agent import run_react_agent
        return run_react_agent(question, history, max_iterations, tool_names)

    messages: List = [
        SystemMessage(content=SYSTEM_PROMPT.format(
            tools_description=_build_tools_description(registry)
        )),
    ]
    for m in history[-6:]:
        if m.get("role") == "user":
            messages.append(HumanMessage(content=m["content"]))
        else:
            messages.append(AIMessage(content=m["content"]))
    messages.append(HumanMessage(content=question))

    for iteration in range(max_iterations):
        try:
            response = llm_with_tools.invoke(messages)
        except Exception as e:
            logger.error(f"FC Agent 第 {iteration} 轮调用失败: {e}")
            break

        tool_call = _extract_function_call(response)
        if not tool_call:
            answer = getattr(response, "content", str(response))
            return {"answer": answer, "tool_calls": all_tool_calls, "iterations": iteration + 1}

        tool_name = tool_call["name"]
        tool_args = tool_call["arguments"]
        if isinstance(tool_args, str):
            try:
                tool_args = json.loads(tool_args)
            except json.JSONDecodeError:
                tool_args = {}

        logger.info(f"FC Agent 调用工具: {tool_name}({tool_args})")
        tool_result = registry.execute(tool_name, **tool_args)
        all_tool_calls.append({
            "tool": tool_name,
            "arguments": tool_args,
            "result_preview": str(tool_result.get("result", tool_result.get("error", "")))[:200],
        })

        messages.append(response)
        messages.append(ToolMessage(
            content=json.dumps(tool_result, ensure_ascii=False, default=str),
            tool_call_id=f"call_{iteration}",
        ))

    try:
        final = llm.invoke(messages + [HumanMessage(content="请基于以上工具调用结果，给出最终回答。")])
        answer = getattr(final, "content", str(final))
    except Exception as e:
        answer = f"Agent 处理完成但最终生成失败: {e}"

    return {"answer": answer, "tool_calls": all_tool_calls, "iterations": max_iterations}
