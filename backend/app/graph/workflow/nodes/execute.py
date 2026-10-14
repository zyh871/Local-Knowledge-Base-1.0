"""`execute` 相位节点 —— 执行单个步骤。

**一次调用只推进一步**，由 HTTP 端点反复调用。这是设计文档 §4.1 的核心取舍：
不用 `interrupt()` 挂起等审批，而是让每个步骤成为一次可独立重试、可断点续跑的请求。

两类步骤走两条完全不同的路径：
  * `tool`  —— 调用 `ToolRegistry` 里的工具，返回值归一化成 `{summary, sources}`
  * `llm` / `synthesis` / `unplanned` —— 流式生成，逐片推出 `step_delta`

失败不抛异常（SSE 响应头已发出），而是写进 State 的 `error` 并推
`step_failed`，由 service 层落库并把 run 置为 `paused`。
"""

from __future__ import annotations

from typing import Any

from app.core.logging import get_logger
from app.graph.nodes.respond import emit

logger = get_logger(__name__)

__all__ = ["execute"]


def _emit_step_done(idx: int, output: dict[str, Any]) -> None:
    emit({"type": "step_done", "content": {"idx": idx, "output": output}})


def _emit_step_failed(idx: int, error: str, *, retryable: bool = True) -> None:
    emit(
        {
            "type": "step_failed",
            "content": {"idx": idx, "error": error, "retryable": retryable},
        }
    )


def execute(state: dict, deps: Any) -> dict:
    """执行 `state["step"]` 指定的那一步。"""
    step = state.get("step") or {}
    idx = int(step.get("idx") or 0)
    kind = step.get("kind") or "llm"
    action = step.get("action") or {}
    title = step.get("title") or f"第 {idx} 步"

    emit(
        {
            "type": "step_start",
            "content": {"idx": idx, "title": title, "kind": kind, "action": action},
        }
    )

    try:
        if kind == "tool":
            return _run_tool(state, step, deps)
        return _run_generation(state, step, deps)
    except Exception as exc:  # noqa: BLE001 - 单步失败不应带走整个 run
        logger.error(f"步骤 {idx} 执行异常: {exc}", exc_info=True)
        _emit_step_failed(idx, str(exc))
        return {"step_output": None, "error": str(exc)}


# ---------------------------------------------------------------------------
#  工具步
# ---------------------------------------------------------------------------


def _run_tool(state: dict, step: dict, deps: Any) -> dict:
    from app.tools.base import get_registry
    from app.workflow.executor import normalize_tool_output
    from app.workflow.tool_args import resolve_tool_args

    idx = int(step.get("idx") or 0)
    action = step.get("action") or {}
    tool_name = str(action.get("tool") or "")
    raw_args = action.get("args") if isinstance(action.get("args"), dict) else {}

    # 计划期已校验过白名单；执行期再查一次 —— 计划可能被人工手改过，
    # 而手改路径与计划生成路径的校验时机不同，重复检查成本极低。
    registry = get_registry()
    if tool_name not in registry.list_names():
        message = f"工具「{tool_name}」未注册，无法执行"
        _emit_step_failed(idx, message, retryable=False)
        return {"step_output": None, "error": message}

    # 联网搜索配额：超限时明确失败并要求用户处理，而不是静默烧配额
    if tool_name == "web_search":
        used = int(state.get("search_calls_used") or 0)
        limit = int(state.get("search_limit") or 0)
        if limit and used >= limit:
            message = f"本次任务已达到联网搜索次数上限（{limit} 次）"
            _emit_step_failed(idx, message, retryable=False)
            return {"step_output": None, "error": message}

    # 参数的**归位与引用解析**：模型写 `expression` 而工具要 `code`、
    # `content_from_step` 要换成真实正文 —— 都在这里统一处理。
    # 放在执行期而不是只在计划期，是因为计划可能被人工改过。
    args, arg_notes = resolve_tool_args(tool_name, raw_args, state.get("steps") or [])
    for note in arg_notes:
        logger.info(f"[步骤 {idx}] 参数归位：{note}")

    # 写入上下文只给需要的工具，且**不来自计划** ——
    # 让模型自己指定写到哪里，等于把路径交给它决定。
    call_kwargs = dict(args)
    if tool_name == "file_write":
        call_kwargs["_context"] = state.get("write_ctx") or {}

    logger.info(f"[步骤 {idx}] 调用工具 {tool_name}")
    result = registry.execute(tool_name, **call_kwargs)
    output = normalize_tool_output(tool_name, result)

    if output.get("failed"):
        _emit_step_failed(idx, str(output.get("summary") or "工具执行失败"))
        return {"step_output": None, "error": output.get("summary")}

    # 把「做了哪些修正」带出去：静默替换会让用户没法解释自己看到的结果
    if arg_notes:
        output["arg_notes"] = arg_notes
    # file_write 的真实落点交给服务层登记成产物，用户才能在任务里下载到它
    written = result.get("written") if isinstance(result, dict) else None
    if isinstance(written, dict) and written.get("stored_path"):
        output["written"] = written

    # 可达性标记（设计 §10.2 第 3 条）：只对**检索类**产出做，
    # 且并发 + 带缓存，避免这一步把整体耗时拖长。
    # 结果随 step_done 一起下发（前端据此标注「可能失效」）。
    sources = output.get("sources")
    if isinstance(sources, list) and sources:
        from app.workflow.reachability import check_sources

        enabled = bool(getattr(deps.settings.workflow, "verify_links", True))
        output["sources"] = check_sources(sources, enabled=enabled)

    _emit_step_done(idx, output)
    return {"step_output": output, "error": None}


# ---------------------------------------------------------------------------
#  生成步
# ---------------------------------------------------------------------------


def _run_generation(state: dict, step: dict, deps: Any) -> dict:
    from app.rag.providers import get_llm, resolve_model
    from app.utils.text import extract_text, finish_reason_of, usage_of
    from app.workflow.executor import build_step_prompt

    idx = int(step.get("idx") or 0)
    plan = state.get("plan") or {}
    steps = state.get("steps") or []
    requested = (state.get("model") or "").strip() or None

    try:
        used_model = resolve_model(requested)
        llm = get_llm(requested)
    except ValueError as exc:
        _emit_step_failed(idx, f"模型不可用：{exc}", retryable=False)
        return {"step_output": None, "error": str(exc)}

    if llm is None:
        message = "模型未初始化，无法生成内容"
        _emit_step_failed(idx, message, retryable=False)
        return {"step_output": None, "error": message}

    prompt = build_step_prompt(
        plan=plan,
        step=step,
        steps=steps,
        file_context=state.get("file_context") or "",
    )

    full = ""
    finish_reason: str | None = None
    stream_error: Exception | None = None
    #: token 用量（尽力而为）。通常在最后一个分片上，也可能没有 —— 缺了不影响执行。
    #: ⚠️ 必须在 `if not text: continue` **之前**取，否则承载用量的那个分片
    #: （往往不带正文）会被直接跳过。
    usage: dict[str, Any] = {}

    try:
        for chunk in llm.stream(prompt):
            reason = finish_reason_of(chunk)
            if reason:
                finish_reason = reason
            chunk_usage = usage_of(chunk)
            if chunk_usage:
                usage = chunk_usage
            text = extract_text(chunk)
            if not text:
                continue
            full += text
            # ⚠️ 不做 trim —— 会吃掉词间空格（英文会粘成一坨）
            emit({"type": "step_delta", "content": {"idx": idx, "text": text}})
    except Exception as exc:  # noqa: BLE001
        stream_error = exc
        logger.warning(f"[步骤 {idx}] 流式生成失败，回退同步调用: {exc}")

    if stream_error is not None and not full:
        try:
            answer = llm.invoke(prompt)
            text = extract_text(answer)
            finish_reason = finish_reason_of(answer) or finish_reason
            usage = usage_of(answer) or usage
            if text:
                full = text
                emit({"type": "step_delta", "content": {"idx": idx, "text": text}})
        except Exception as exc:  # noqa: BLE001
            logger.error(f"[步骤 {idx}] 同步生成也失败: {exc}")
            _emit_step_failed(idx, f"生成失败：{exc}")
            return {"step_output": None, "error": str(exc)}

    if not full.strip():
        message = "模型没有产出任何内容"
        _emit_step_failed(idx, message)
        return {"step_output": None, "error": message}

    # 非法链接剔除（设计 §10.2 第 2 条）：模型自编的 URL 一律删掉。
    # 这是「真实可访问链接」的**硬保证** —— 只靠提示词「请勿编造链接」是不可靠的。
    from app.workflow.citation import sanitize_citations
    from app.workflow.executor import collect_sources

    cleaned, removed = sanitize_citations(full, collect_sources(steps))

    truncated = finish_reason == "length" or (finish_reason is None and bool(full))
    output: dict[str, Any] = {"summary": cleaned}
    if removed:
        # 让前端能提示「已剔除 N 个无效链接」，而不是悄悄少几行
        output["stripped_links"] = removed[:10]
    if usage:
        # 落库供成本可见性用：没有它，一次 10 次调用、9 万字符的产出
        # 到底花了多少 token 完全是黑盒，也就无从判断"该优化哪一步"。
        output["usage"] = usage
    if truncated:
        # 步骤卡需要能显示「这段可能不完整」—— 复用与对话侧同一套判定
        output["truncated"] = True
        logger.warning(
            f"[步骤 {idx} 截断] model={used_model} finish_reason={finish_reason} "
            f"chars={len(full)}"
        )
    else:
        logger.info(
            f"[步骤 {idx} 完成] model={used_model} finish_reason={finish_reason} "
            f"chars={len(full)}"
        )

    _emit_step_done(idx, output)
    return {"step_output": output, "used_model": used_model, "error": None}
