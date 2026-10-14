"""`clarify` 相位节点 —— 产出一轮结构化澄清问题。

与 `plan` 节点同样的流式分道：先讲思路（`thinking_delta`），再给 JSON。
推理文本对用户有实际价值 —— 它解释了「为什么问这几个问题」。

**信息足够时不硬凑问题**：模型声明 `sufficient=true` 就结束澄清，
直接把用户推到规划阶段。反过来（声明够了却给不出问题）不会被误判为「够了」，
而是报解析失败 —— 否则用户会在毫无察觉的情况下跳过澄清。
"""

from __future__ import annotations

from typing import Any

from app.core.logging import get_logger
from app.graph.nodes.respond import emit
from app.workflow.clarifier import (
    ClarifyParseError,
    MAX_QUESTIONS_PER_ROUND,
    build_clarify_prompt,
    normalize_questions,
)
from app.workflow.planner import ReasoningSplitter, extract_json_object

logger = get_logger(__name__)

__all__ = ["clarify"]


def clarify(state: dict, deps: Any) -> dict:
    """产出一轮澄清问题。"""
    from app.rag.providers import get_llm, resolve_model
    from app.utils.text import extract_text, finish_reason_of, usage_of

    cfg = deps.settings.workflow
    requested = (state.get("model") or "").strip() or None

    emit({"type": "workflow_phase", "content": {"phase": "clarify", "status": "start"}})

    try:
        used_model = resolve_model(requested)
        llm = get_llm(requested)
    except ValueError as exc:
        return _fail(f"模型不可用：{exc}", requested or "")

    if llm is None:
        return _fail("模型未初始化，无法生成澄清问题", used_model)

    listing = state.get("folder_listing") or ""
    if listing:
        # 打一行可见的日志：这样「清单到底有没有进到提示词」是可观测的，
        # 而不是只能靠猜 —— 这类「提示词里少了某段」的问题排查成本极高。
        logger.info(f"[澄清] 已带上工作区文件夹清单（{len(listing)} 字符）")

    prompt = build_clarify_prompt(
        raw_input=state.get("raw_input", ""),
        file_summaries=state.get("file_summaries") or [],
        folder_listing=state.get("folder_listing") or "",
        history=state.get("clarify_history") or [],
        round_no=int(state.get("round_no") or 1),
        max_rounds=int(state.get("max_rounds") or cfg.max_clarify_rounds),
    )

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
                    # ⚠️ 不做 trim —— 会吃掉词间空格
                    emit({"type": "thinking_delta", "content": {"text": piece}})
    except Exception as exc:  # noqa: BLE001
        stream_error = exc
        logger.warning(f"澄清流式生成失败，回退同步调用: {exc}")

    if stream_error is not None:
        try:
            answer = llm.invoke(prompt)
            text = extract_text(answer)
            finish_reason = finish_reason_of(answer) or finish_reason
            usage = usage_of(answer) or usage
            if text:
                for channel, piece in splitter.feed(text):
                    if channel == "reasoning":
                        emit({"type": "thinking_delta", "content": {"text": piece}})
        except Exception as exc:  # noqa: BLE001
            return _fail(f"生成澄清问题失败：{exc}", used_model)

    splitter.finish()

    payload_text = splitter.json_text or splitter.raw_output
    try:
        raw = extract_json_object(payload_text)
        sufficient, questions, issues = normalize_questions(
            raw, max_questions=min(MAX_QUESTIONS_PER_ROUND, cfg.max_clarify_rounds + 3)
        )
    except ClarifyParseError as exc:
        logger.error(
            f"[澄清解析失败] model={used_model} finish_reason={finish_reason} "
            f"chars={len(splitter.raw_output)} error={exc}"
        )
        return _fail(
            exc.message,
            used_model,
            reasoning=splitter.reasoning,
            raw_output=splitter.raw_output,
        )

    logger.info(
        f"[澄清已生成] model={used_model} sufficient={sufficient} "
        f"questions={len(questions)} issues={len(issues)} finish_reason={finish_reason}"
    )

    emit(
        {
            "type": "clarify_questions",
            "content": {
                "sufficient": sufficient,
                "questions": questions,
                "issues": issues,
                "reasoning": splitter.reasoning,
            },
        }
    )
    emit({"type": "workflow_phase", "content": {"phase": "clarify", "status": "end"}})

    return {
        "sufficient": sufficient,
        "questions": questions,
        "issues": issues,
        "reasoning": splitter.reasoning,
        "raw_output": splitter.raw_output,
        "used_model": used_model,
        "usage": usage,
        "error": None,
    }


def _fail(
    message: str,
    used_model: str = "",
    *,
    reasoning: str = "",
    raw_output: str = "",
) -> dict:
    emit({"type": "workflow_phase", "content": {"phase": "clarify", "status": "error"}})
    return {
        "sufficient": False,
        "questions": [],
        "issues": [],
        "reasoning": reasoning,
        "raw_output": raw_output,
        "used_model": used_model,
        "error": message,
    }
