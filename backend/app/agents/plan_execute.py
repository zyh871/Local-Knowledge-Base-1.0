"""
Plan-and-Execute Agent — 先规划再执行，适用于复杂多步任务。

流程:
  1. Planner: LLM 分析问题，生成有序步骤列表
  2. Executor: 逐步执行，每步可调用工具或直接 LLM 生成
  3. Replanner: 根据执行结果动态调整剩余计划
  4. Synthesizer: 汇总各步结果给出最终答案

与 ReAct 的区别: 先全局规划再执行，减少中间 LLM 调用次数，
适合步骤明确的复杂任务（如"调研 X 并对比 Y"）。
"""

import json
import logging
import re
from typing import Any, Dict, List, Optional

from app.tools.base import get_registry

logger = logging.getLogger(__name__)

_MAX_STEPS = 8

PLANNER_PROMPT = """你是一个任务规划器。请将用户的复杂问题分解为有序的执行步骤。

可用工具：{tools_description}

规则：
1. 每步只能是一个具体操作
2. 每步标注是否需要工具：need_tool=true/false
3. 如果需要工具，指定 tool_name 和 tool_input
4. 步骤数不超过 {_max_steps}

输出严格 JSON：
{{
  "steps": [
    {{
      "step": 1,
      "description": "步骤描述",
      "need_tool": true,
      "tool_name": "工具名",
      "tool_input": {{"key": "value"}}
    }},
    {{
      "step": 2,
      "description": "步骤描述",
      "need_tool": false
    }}
  ]
}}

用户问题：{question}

计划："""

EXECUTOR_NO_TOOL_PROMPT = """请根据以下信息执行这一步：

任务上下文：{task_context}
当前步骤：{step_description}
前序结果：{previous_results}

请直接给出这一步的执行结果，不要解释。"""

SYNTHESIZER_PROMPT = """你是一个结果汇总器。请基于以下各步执行结果，给出用户问题的完整回答。

用户问题：{question}

执行步骤与结果：
{step_results}

请综合所有结果，给出准确、完整的回答。使用与用户相同的语言。"""

REPLANNER_PROMPT = """你是一个计划调整器。根据已执行的步骤和结果，决定是否需要调整剩余计划。

原始计划：
{original_plan}

已执行步骤与结果：
{executed_steps}

剩余步骤：
{remaining_steps}

规则：
- 如果已有足够信息回答问题，输出 {{"action": "finish", "reason": "..."}}
- 如果需要调整剩余步骤，输出 {{"action": "replan", "new_steps": [...], "reason": "..."}}
- 格式同原始计划的步骤结构

输出 JSON："""


def _parse_json(text: str) -> Optional[Dict]:
    if not text:
        return None
    start, end = text.find("{"), text.rfind("}")
    if 0 <= start < end:
        try:
            return json.loads(text[start:end + 1])
        except (json.JSONDecodeError, ValueError):
            pass
    return None


def _build_tools_desc(registry, tool_names: Optional[List[str]] = None) -> str:
    lines = []
    for tool in registry.list_all():
        if tool_names and tool.name not in tool_names:
            continue
        lines.append(f"- {tool.name}: {tool.description}")
    return "\n".join(lines) or "（无可用工具）"


def run_plan_execute_agent(
    question: str,
    history: Optional[List[Dict[str, str]]] = None,
    max_steps: int = _MAX_STEPS,
    tool_names: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """
    运行 Plan-and-Execute Agent。

    返回:
        {"answer": str, "plan": [...], "step_results": [...], "steps_executed": int}
    """
    from app.rag_chain import get_query_llm

    registry = get_registry()
    llm = get_query_llm()
    history = history or []

    tools_desc = _build_tools_desc(registry, tool_names)

    plan_prompt = PLANNER_PROMPT.format(
        tools_description=tools_desc,
        _max_steps=max_steps,
        question=question,
    )

    try:
        resp = llm.invoke(plan_prompt)
        text = getattr(resp, "content", str(resp))
    except Exception as e:
        logger.error(f"Planner 调用失败: {e}")
        return {"answer": f"规划失败: {e}", "plan": [], "step_results": [], "steps_executed": 0}

    plan_data = _parse_json(text)
    if not plan_data or "steps" not in plan_data:
        logger.warning(f"Planner 输出解析失败，回退到直接回答: {text[:200]}")
        try:
            resp = llm.invoke(question)
            answer = getattr(resp, "content", str(resp))
        except Exception as e:
            answer = f"处理失败: {e}"
        return {"answer": answer, "plan": [], "step_results": [], "steps_executed": 0}

    steps = plan_data["steps"][:max_steps]
    logger.info(f"Plan-Execute Agent 生成 {len(steps)} 步计划")

    step_results: List[Dict[str, Any]] = []
    task_context = question

    for i, step in enumerate(steps):
        step_desc = step.get("description", "")
        need_tool = step.get("need_tool", False)

        if need_tool:
            tool_name = step.get("tool_name", "")
            tool_input = step.get("tool_input", {})

            if not tool_name or not registry.get(tool_name):
                result_text = f"工具 {tool_name} 不可用，跳过此步"
            else:
                logger.info(f"Plan-Execute 步骤 {i+1}: 调用 {tool_name}({tool_input})")
                tool_result = registry.execute(tool_name, **tool_input)
                result_text = str(tool_result.get("result", tool_result.get("error", "")))[:800]
        else:
            prev_text = "\n".join(
                f"步骤{r['step']}: {r['result'][:200]}" for r in step_results
            )
            exec_prompt = EXECUTOR_NO_TOOL_PROMPT.format(
                task_context=task_context,
                step_description=step_desc,
                previous_results=prev_text or "（无）",
            )
            try:
                resp = llm.invoke(exec_prompt)
                result_text = getattr(resp, "content", str(resp))
                if not isinstance(result_text, str):
                    result_text = str(result_text)
                result_text = result_text[:800]
            except Exception as e:
                result_text = f"执行失败: {e}"

        step_results.append({"step": i + 1, "description": step_desc, "result": result_text})

        if i < len(steps) - 1 and _should_replan(llm, steps, step_results, steps[i + 1:]):
            new_plan = _replan(llm, steps, step_results, steps[i + 1:])
            if new_plan:
                remaining = new_plan.get("new_steps", [])
                steps = steps[:i + 1] + remaining
                logger.info(f"计划调整: 剩余 {len(remaining)} 步")

    step_results_text = "\n\n".join(
        f"步骤{r['step']}（{r['description']}）:\n{r['result']}"
        for r in step_results
    )

    synth_prompt = SYNTHESIZER_PROMPT.format(
        question=question,
        step_results=step_results_text,
    )

    try:
        resp = llm.invoke(synth_prompt)
        answer = getattr(resp, "content", str(resp))
        if not isinstance(answer, str):
            answer = str(answer)
    except Exception as e:
        answer = f"汇总失败: {e}\n\n各步结果:\n{step_results_text}"

    return {
        "answer": answer,
        "plan": steps,
        "step_results": step_results,
        "steps_executed": len(step_results),
    }


def _should_replan(llm, original_steps, executed, remaining) -> bool:
    if not remaining or len(executed) < 2:
        return False
    return True


def _replan(llm, original_steps, executed, remaining) -> Optional[Dict]:
    executed_text = json.dumps(executed, ensure_ascii=False, default=str)
    remaining_text = json.dumps(remaining, ensure_ascii=False, default=str)
    original_text = json.dumps(original_steps, ensure_ascii=False, default=str)

    prompt = REPLANNER_PROMPT.format(
        original_plan=original_text,
        executed_steps=executed_text,
        remaining_steps=remaining_text,
    )

    try:
        resp = llm.invoke(prompt)
        text = getattr(resp, "content", str(resp))
        result = _parse_json(text)
        if result and result.get("action") == "replan":
            return result
    except Exception as e:
        logger.warning(f"Replanner 调用失败: {e}")
    return None
