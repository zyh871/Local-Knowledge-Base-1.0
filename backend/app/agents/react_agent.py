"""
ReAct Agent — 思考-行动-观察循环，适用于不支持 function calling 的后端。

流程:
  1. Thought: LLM 分析问题，决定下一步行动
  2. Action: 选择工具并给出参数
  3. Observation: 执行工具，获得结果
  4. 重复直到 LLM 给出最终答案

纯文本协议，不依赖 function calling API。
"""

import json
import logging
import re
from typing import Any, Dict, List, Optional

from app.tools.base import get_registry

logger = logging.getLogger(__name__)

_MAX_ITERATIONS = 5

REACT_SYSTEM_PROMPT = """你是一个能使用工具的智能助手。请严格按照以下格式回答：

Thought: 思考你需要做什么
Action: 工具名称
Action Input: {{"key": "value"}}

或者当你已经得到足够信息时：

Thought: 我已经获得了足够的信息来回答问题
Final Answer: 最终回答

可用工具：
{tools_description}

示例：
Thought: 我需要计算 2 的 10 次方
Action: calculator
Action Input: {{"expression": "2**10"}}

注意：
- 每次只使用一个工具
- Action Input 必须是合法 JSON
- 获得足够信息后必须给出 Final Answer
- 使用与用户相同的语言回答
"""


def _parse_react_response(text: str) -> Dict[str, Any]:
    """解析 ReAct 格式响应。"""
    result: Dict[str, Any] = {"thought": "", "action": None, "action_input": {}, "final_answer": None}

    thought_match = re.search(r"Thought:\s*(.+?)(?=\n(?:Action:|Final Answer:)|$)", text, re.DOTALL)
    if thought_match:
        result["thought"] = thought_match.group(1).strip()

    final_match = re.search(r"Final Answer:\s*(.+)", text, re.DOTALL)
    if final_match:
        result["final_answer"] = final_match.group(1).strip()
        return result

    action_match = re.search(r"Action:\s*(\w+)", text)
    if action_match:
        result["action"] = action_match.group(1).strip()

    input_match = re.search(r"Action Input:\s*(\{.*\})", text, re.DOTALL)
    if input_match:
        raw = input_match.group(1).strip()
        try:
            start, end = raw.find("{"), raw.rfind("}")
            if 0 <= start < end:
                result["action_input"] = json.loads(raw[start:end + 1])
        except (json.JSONDecodeError, ValueError):
            result["action_input"] = {}

    return result


def _build_tools_description(registry, tool_names: Optional[List[str]] = None) -> str:
    lines = []
    for tool in registry.list_all():
        if tool_names and tool.name not in tool_names:
            continue
        params = tool.parameters.get("properties", {})
        params_str = ", ".join(f'"{k}": {v.get("type", "string")}' for k, v in params.items())
        lines.append(f'- {tool.name}: {tool.description}\n  参数: {params_str}')
    return "\n".join(lines)


def run_react_agent(
    question: str,
    history: Optional[List[Dict[str, str]]] = None,
    max_iterations: int = _MAX_ITERATIONS,
    tool_names: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """
    运行 ReAct Agent。

    返回:
        {"answer": str, "tool_calls": [...], "iterations": int}
    """
    from app.rag.providers import get_query_llm

    registry = get_registry()
    llm = get_query_llm()
    history = history or []

    tools_desc = _build_tools_description(registry, tool_names)
    system = REACT_SYSTEM_PROMPT.format(tools_description=tools_desc)

    history_block = ""
    for m in history[-4:]:
        role = "用户" if m.get("role") == "user" else "助手"
        history_block += f"{role}: {m.get('content', '')[:200]}\n"

    scratchpad = ""
    all_tool_calls: List[Dict[str, Any]] = []

    for iteration in range(max_iterations):
        prompt = f"{system}\n\n对话历史：\n{history_block}\n问题：{question}\n\n{scratchpad}"

        try:
            response = llm.invoke(prompt)
            text = getattr(response, "content", str(response))
            if not isinstance(text, str):
                text = str(text)
        except Exception as e:
            logger.error(f"ReAct Agent 第 {iteration} 轮 LLM 调用失败: {e}")
            break

        parsed = _parse_react_response(text)

        if parsed["final_answer"]:
            return {
                "answer": parsed["final_answer"],
                "tool_calls": all_tool_calls,
                "iterations": iteration + 1,
            }

        if not parsed["action"]:
            final_text = text.strip()
            if final_text:
                return {"answer": final_text, "tool_calls": all_tool_calls, "iterations": iteration + 1}
            break

        tool_name = parsed["action"]
        tool_args = parsed["action_input"]

        logger.info(f"ReAct Agent 调用工具: {tool_name}({tool_args})")
        tool_result = registry.execute(tool_name, **tool_args)

        result_preview = str(tool_result.get("result", tool_result.get("error", "")))[:500]
        all_tool_calls.append({
            "tool": tool_name,
            "arguments": tool_args,
            "result_preview": result_preview[:200],
        })

        scratchpad += f"Thought: {parsed['thought']}\n"
        scratchpad += f"Action: {tool_name}\n"
        scratchpad += f"Action Input: {json.dumps(tool_args, ensure_ascii=False)}\n"
        scratchpad += f"Observation: {result_preview}\n\n"

    try:
        final_prompt = f"{system}\n\n问题：{question}\n\n{scratchpad}\n请基于以上信息给出最终回答。"
        final_resp = llm.invoke(final_prompt)
        answer = getattr(final_resp, "content", str(final_resp))
        if not isinstance(answer, str):
            answer = str(answer)
    except Exception as e:
        answer = f"Agent 未能完成任务: {e}"

    return {"answer": answer, "tool_calls": all_tool_calls, "iterations": max_iterations}
