"""`plan` 相位节点 —— 生成可审批的任务书。

流程：组装提示词 → 流式调用 LLM → 推理与 JSON 分道 → 宽容解析 → 严格校验 → 落库。

**为什么流式分道在这里做**：我们把提示词设计成「先讲 2-4 句思路，再给 JSON」。
如果把整段（含花括号）直接当"思考过程"推给前端，用户会看到一大坨 JSON。
`ReasoningSplitter` 在流式过程中就把两者分开，前端拿到的推理文本是干净的。

**失败不抛异常**：SSE 响应头发出后无法改状态码，异常会变成前端的一根断线。
因此这里把错误写进 State 的 `error` 并推一个终止事件，由 service 层转成
`final` 事件（与 `qa_service` 的既有约定一致）。
"""

from __future__ import annotations

from typing import Any

from app.core.logging import get_logger
from app.graph.nodes.respond import emit
from app.graph.workflow.state import WorkflowState
from app.workflow.planner import (
    PlanParseError,
    ReasoningSplitter,
    build_plan_prompt,
    extract_json_object,
    normalize_plan,
)

logger = get_logger(__name__)

__all__ = ["plan"]


def _phase(status: str, **extra: Any) -> None:
    emit({"type": "workflow_phase", "content": {"phase": "plan", "status": status, **extra}})


def plan(state: WorkflowState, deps: Any) -> dict:
    """产出任务书。"""
    from app.rag.providers import get_llm, resolve_model
    from app.utils.text import extract_text, finish_reason_of, usage_of
    from app.workflow.clarifier import format_clarifications
    from app.workflow.executor import prior_context, settled_steps

    cfg = deps.settings.workflow
    requested = (state.get("model") or "").strip() or None

    try:
        used_model = resolve_model(requested)
        llm = get_llm(requested)
    except ValueError as exc:
        logger.error(f"工作流计划：模型选择失败 {exc}")
        return _fail(state, f"模型不可用：{exc}", used_model=requested or "")

    if llm is None:
        return _fail(state, "模型未初始化，无法生成任务书", used_model=used_model)

    # 已终结的前序步骤要保留并告知模型 —— 否则「驳回第 N 步重规划」
    # 会把前面已经跑出来的产出丢掉，用户得从头再等一遍。
    preserved = settled_steps(state.get("steps") or [])

    listing = state.get("folder_listing") or ""
    if listing:
        # 同澄清节点：让「清单有没有进提示词」可观测，便于排查
        logger.info(f"[任务书] 已带上工作区文件夹清单（{len(listing)} 字符）")

    prompt = build_plan_prompt(
        raw_input=state.get("raw_input", ""),
        mode=state.get("mode", "manual"),
        force_search=bool(state.get("force_search")),
        tools=state.get("tools") or [],
        max_steps=cfg.max_steps,
        file_summaries=state.get("file_summaries") or [],
        file_context=state.get("file_context") or "",
        folder_listing=state.get("folder_listing") or "",
        # 澄清结论必须进提示词，否则用户前面花时间做的选择会被丢掉
        clarifications=format_clarifications(state.get("clarify_history") or []),
        prior_steps=prior_context(preserved) if preserved else "",
        replan_from=len(preserved) + 1 if preserved else 0,
        reject_comment=state.get("reject_comment") or "",
    )

    _phase("start", model=used_model)

    splitter = ReasoningSplitter()
    finish_reason: str | None = None
    stream_error: Exception | None = None
    #: token 用量（尽力而为，缺失 = 上游没给）
    usage: dict[str, Any] = {}

    try:
        for chunk in llm.stream(prompt):
            reason = finish_reason_of(chunk)
            if reason:
                finish_reason = reason
            # 用量必须在 `if not text: continue` 之前取：承载用量的分片往往不带正文
            chunk_usage = usage_of(chunk)
            if chunk_usage:
                usage = chunk_usage
            text = extract_text(chunk)
            if not text:
                continue
            for channel, piece in splitter.feed(text):
                if channel == "reasoning":
                    # 推理段逐块推送，前端边收边渲染成思考流。
                    # ⚠️ 不做 trim —— 会吃掉词间空格（英文会粘成一坨）
                    emit({"type": "thinking_delta", "content": {"text": piece}})
    except Exception as exc:  # noqa: BLE001
        stream_error = exc
        logger.warning(f"任务书流式生成失败，回退为同步调用: {exc}")

    if stream_error is not None:
        try:
            fallback = llm.invoke(prompt)
            text = extract_text(fallback)
            finish_reason = finish_reason_of(fallback) or finish_reason
            if text:
                for channel, piece in splitter.feed(text):
                    if channel == "reasoning":
                        emit({"type": "thinking_delta", "content": {"text": piece}})
        except Exception as exc:  # noqa: BLE001
            return _fail(state, f"生成任务书失败：{exc}", used_model=used_model)

    splitter.finish()

    # 分道可能判断失误（例如 JSON 与推理写在同一行），此时回退到完整输出
    payload_text = splitter.json_text or splitter.raw_output
    try:
        from app.workflow.executor import tool_required_args

        raw_plan = extract_json_object(payload_text)
        normalized, issues = normalize_plan(
            raw_plan,
            available_tools=state.get("tools") or [],
            max_steps=cfg.max_steps,
            fallback_goal=state.get("raw_input", ""),
            file_refs=state.get("file_refs") or [],
            forced_approval_tools=cfg.approval_tool_list,
            tool_required_args=tool_required_args(),
        )
    except PlanParseError as exc:
        logger.error(
            f"[任务书解析失败] model={used_model} finish_reason={finish_reason} "
            f"chars={len(splitter.raw_output)} error={exc}"
        )
        truncated = finish_reason is None and bool(splitter.raw_output)
        hint = "（输出可能被截断，可重试或换更强的模型）" if truncated else ""
        return _fail(
            state,
            f"{exc.message}{hint}",
            used_model=used_model,
            reasoning=splitter.reasoning,
            raw_output=splitter.raw_output,
        )

    if finish_reason is None and splitter.raw_output:
        logger.warning(
            f"[任务书] model={used_model} 缺少 finish_reason，输出可能被截断 "
            f"chars={len(splitter.raw_output)}"
        )
    else:
        logger.info(
            f"[任务书已生成] model={used_model} finish_reason={finish_reason} "
            f"steps={len(normalized['steps'])} issues={len(issues)}"
        )

    emit(
        {
            "type": "plan_ready",
            "content": {"plan": normalized, "issues": issues, "reasoning": splitter.reasoning},
        }
    )
    _phase("end")

    return {
        "plan": normalized,
        "issues": issues,
        "reasoning": splitter.reasoning,
        "raw_output": splitter.raw_output,
        "used_model": used_model,
        "usage": usage,
        "error": None,
    }


def _fail(
    state: WorkflowState,
    message: str,
    *,
    used_model: str = "",
    reasoning: str = "",
    raw_output: str = "",
) -> dict:
    emit({"type": "workflow_phase", "content": {"phase": "plan", "status": "error"}})
    return {
        "plan": None,
        "issues": [],
        "reasoning": reasoning,
        "raw_output": raw_output,
        "used_model": used_model,
        "error": message,
    }
