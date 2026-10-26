"""
回答类节点：长文本处理、反问澄清、Prompt 组装、LLM 生成。

**SSE 事件在这里产生。** 设计原则：
  * 每个调用路径**恰好**发出一次 `intent` 事件 ——
    由该路径的「分支终点节点」发出（clarify / long_text / agent / build_prompt），
    而不是在分类节点发出。这样事件里的 intent 一定是**最终值**，
    避免「kb_qa 检索无命中降级为 general」时前端先收到过期意图。
  * 事件顺序为：intent → sources → chunk* → final，与 v3 完全一致。
"""

from __future__ import annotations

import re
import time
from typing import Any

from app.agents.intent_classifier import (
    INTENT_FILE_QA,
    INTENT_GENERAL,
    INTENT_KB_QA,
    INTENT_TASK,
)
from app.core.logging import get_logger
from app.graph.nodes import Deps, bind
from app.graph.state import RAGState, format_docs_from_state
from app.utils.text import extract_text, format_history

logger = get_logger(__name__)


def emit(event: dict[str, Any]) -> None:
    """向 SSE 流推送事件。

    非流式调用（`invoke`）时 `get_stream_writer()` 不可用，
    此时静默跳过 —— 非流式路径由 runner 直接读取最终 State。
    """
    try:
        from langgraph.config import get_stream_writer

        writer = get_stream_writer()
        if writer is not None:
            writer(event)
    except Exception:  # noqa: BLE001  任何异常都不应影响图执行
        pass


def _reasoning_of(chunk: Any) -> str:
    """从流式分片里取推理文本。

    两种来源都要认：
      * `ThinkingChunk.reasoning`（我们自己的推理客户端）
      * `additional_kwargs["reasoning_content"]`（某些 provider 的封装会放这里）
    """
    value = getattr(chunk, "reasoning", "")
    if isinstance(value, str) and value:
        return value
    extra = getattr(chunk, "additional_kwargs", None) or {}
    if isinstance(extra, dict):
        fallback = extra.get("reasoning_content")
        if isinstance(fallback, str):
            return fallback
    return ""


# ---------------------------------------------------------------------------
#  澄清
# ---------------------------------------------------------------------------


def clarify(state: RAGState, deps: Deps) -> dict:
    """问题过于模糊时直接反问（不写回记忆）。"""
    answer = state.get("clarification_prompt") or "能再描述清楚一点吗？"
    _emit_intent(state)
    emit({"type": "chunk", "content": answer})
    return {
        "answer": answer,
        "data_source": "clarification",
        "needs_clarification": True,
    }


# ---------------------------------------------------------------------------
#  长文本
# ---------------------------------------------------------------------------


_LONG_TEXT_OP_RE = re.compile(
    r"^(请)?(总结|摘要|概括|翻译|translate|summarize|summary)", re.IGNORECASE
)


def long_text(state: RAGState, deps: Deps) -> dict:
    """处理用户粘贴的长文本：摘要 / 翻译 / 针对文本问答。

    逻辑逐字对照旧 `router._handle_long_text()`。
    """
    from app.document.long_text import (
        answer_against_text,
        detect_language_hint,
        summarize,
        translate,
    )

    question = state.get("question", "")
    instruction = (question or "").strip()
    detected = detect_language_hint(question)

    answer: str
    data_source = "long_text"

    match = _LONG_TEXT_OP_RE.search(instruction)
    if match:
        rest = instruction[match.end() :].lstrip(" :：")
        verb = match.group(2).lower()
        if verb.startswith(("translate", "翻译")):
            target = rest.strip() or "English"
            answer = translate(question, target_language=target, source_language=detected) or "（无内容）"
            data_source = "long_text_translate"
        else:
            answer = summarize(question, language=detected, max_chars=600) or "（无内容）"
            data_source = "long_text_summary"
    else:
        parts = re.split(r"[?？\n]", instruction, maxsplit=1)
        if len(parts) == 2 and parts[0].strip():
            answer = answer_against_text(parts[1].strip() or question, parts[0].strip()) or "（无内容）"
        else:
            answer = summarize(question, language=detected, max_chars=600) or "（无内容）"

    _emit_intent(state)
    emit({"type": "chunk", "content": answer})
    return {"answer": answer, "data_source": data_source}


# ---------------------------------------------------------------------------
#  Prompt 组装
# ---------------------------------------------------------------------------


def _emit_intent(state: RAGState) -> None:
    emit(
        {
            "type": "intent",
            "content": {
                "intent": state.get("intent"),
                "confidence": state.get("intent_confidence"),
            },
        }
    )


def build_prompt(state: RAGState, deps: Deps) -> dict:
    """按意图组装最终提示词，并推导 data_source。

    sources 事件在此发出：这是所有需要引用来源的路径
    （kb_qa / general / task / file_qa）汇聚后的**唯一**位置，
    因此不会出现多个 sources 事件。
    """
    intent = state.get("intent", INTENT_KB_QA)
    question = state.get("question", "")
    history_block = format_history(state.get("history") or [])
    lang = state.get("lang_instruction", "")
    profile_ctx = state.get("profile_ctx") or ""
    docs = state.get("docs") or []
    search_context = state.get("search_context") or ""
    sources = state.get("sources") or []

    context = ""
    data_source = "rag"

    if intent == INTENT_FILE_QA:
        filename = state.get("target_filename", "")
        context = format_docs_from_state(docs)
        prompt = (
            f"{lang}\n\n"
            f"你将基于【文件 {filename} 的内容】回答用户问题。"
            f"如果内容不足，直接说明。\n\n"
            f"文件内容：\n{context}\n\n"
            f"问题：{question}"
        )
        data_source = "file_qa"

    elif intent == INTENT_TASK:
        prompt = (
            f"{lang}\n\n"
            f"你是一位高质量的写作 / 翻译 / 总结助手。请直接完成用户的任务，"
            f"不要解释你打算做什么。\n\n"
            f"对话历史：\n{history_block}\n\n"
            f"任务：{question}"
        )
        data_source = "task"

    elif intent == INTENT_GENERAL:
        block = f"\n【实时搜索结果】\n{search_context}\n\n" if search_context else ""
        prompt = (
            f"{lang}\n\n"
            f"你是用户的智能助手。基于你的知识和{block or '可用信息'}回答问题。"
            f"如果使用了搜索结果，请在回答末尾用[来源: xxx]标注链接。\n\n"
            f"对话历史：\n{history_block}\n\n"
            f"问题：{question}"
        )
        data_source = "web_search" if sources else "general"

    else:  # kb_qa
        parts: list[str] = []
        if docs:
            parts.append(f"【知识库内容】\n{format_docs_from_state(docs)}")
        if search_context:
            parts.append(f"【实时搜索结果】\n{search_context}")
        context = "\n\n".join(parts)

        prompt = (
            f"{lang}\n"
            f"{profile_ctx}\n\n"
            f"你是用户的智能助手。"
            f"基于【本地知识库】和【实时搜索结果】回答问题。"
            f"如果使用了搜索结果，请在回答末尾用[来源: xxx]标注链接。\n\n"
            f"对话历史：\n{history_block}\n\n"
            f"参考资料：\n{context}\n\n"
            f"问题：{question}"
        )
        has_web = any(
            isinstance(s, dict) and s.get("source") == "tavily" for s in sources
        )
        data_source = "rag_search" if has_web else "rag"

    _emit_intent(state)
    emit({"type": "sources", "content": sources})
    return {"prompt": prompt, "context": context, "data_source": data_source}


# ---------------------------------------------------------------------------
#  生成
# ---------------------------------------------------------------------------


def generate(state: RAGState, deps: Deps) -> dict:
    """调用 LLM 生成回答，逐 token 推送 chunk 事件。

    降级链与 v3 一致：`stream()` 失败 → `invoke()` → 固定兜底文案。

    ★ 三点可观测性改进（用于定位"回答不全"）：
      1. 记录 `finish_reason` —— 唯一能区分「模型自己写完」与「被 max_tokens 掐断」的依据
      2. 记录输出 token 用量 —— 与 `max_tokens` 配置对比即可确认是否顶格
      3. **失败回退返回空内容时不再静默保留半截答案** —— 显式标记 `truncated`，
         避免"看起来正常、其实少了一半"这种最难排查的状态
    """
    from app.rag.providers import get_llm, get_thinking_llm, resolve_model
    from app.utils.text import finish_reason_of, usage_of

    prompt = state.get("prompt") or ""
    requested = (state.get("model") or "").strip() or None

    # ── 深度思考：选模型 ────────────────────────────────────────────
    #
    # 只有「支持 reasoning_content 的模型 + 后端开关打开」才真的走推理链路。
    # 用户当前选的模型若不支持，**明确切到配置的思考模型**并记日志 ——
    # 界面顶部会显示实际模型，这比"开关打开了但什么都不发生"可解释得多。
    model_settings = deps.settings.model
    thinking_on = bool(state.get("thinking")) and bool(
        getattr(model_settings, "thinking_enabled", False)
    )
    thinking_model = ""
    if thinking_on:
        target = requested or model_settings.llm_model
        if not model_settings.is_thinking_capable(target):
            thinking_model = model_settings.thinking_model
            logger.info(
                f"深度思考：{target} 无推理输出，改用 {thinking_model}"
            )
        else:
            thinking_model = target

    # 按请求选择模型（白名单已在 API 层校验；此处再兜一层）
    try:
        used_model = resolve_model(thinking_model or requested)
        llm = (
            get_thinking_llm(thinking_model)
            if thinking_on and thinking_model
            else get_llm(requested)
        )
    except ValueError as exc:
        logger.error(f"模型选择失败: {exc}")
        return {
            "answer": f"（模型不可用：{exc}）",
            "used_model": requested or "",
            "truncated": False,
            "error": "invalid_model",
        }

    if llm is None:
        logger.error("LLM 未初始化，无法生成回答")
        return {
            "answer": "（模型未初始化）",
            "used_model": used_model,
            "truncated": False,
            "error": "llm_unavailable",
        }

    full = ""
    finish_reason: str | None = None
    stream_error: Exception | None = None
    token_usage: dict = {}
    reasoning_parts: list[str] = []
    reasoning_started: float | None = None
    reasoning_ms = 0

    try:
        for chunk in llm.stream(prompt):
            fr = finish_reason_of(chunk)
            if fr:
                finish_reason = fr
            if thinking_on:
                piece = _reasoning_of(chunk)
                if piece:
                    # 从**第一段推理**开始计时，而不是从请求开始：
                    # 用户关心的是「模型想了多久」，不是「网络排队多久」
                    if reasoning_started is None:
                        reasoning_started = time.perf_counter()
                    reasoning_parts.append(piece)
                    emit({"type": "reasoning", "content": piece})
            text = extract_text(chunk)
            if not text:
                continue
            full += text
            emit({"type": "chunk", "content": text})
    except Exception as exc:  # noqa: BLE001
        stream_error = exc
        logger.warning(f"流式生成失败，回退为同步调用: {exc}")

    fallback_used = False
    if stream_error is not None:
        try:
            answer = llm.invoke(prompt)
            text = extract_text(answer)
            token_usage = usage_of(answer)
            finish_reason = finish_reason_of(answer) or finish_reason
            if thinking_on:
                # 同步回退也要保留推理文本，否则界面会从「有思考」突然变成「没思考」
                piece = _reasoning_of(answer)
                if piece:
                    reasoning_parts.append(piece)
            if text:
                if len(full) > len(text) // 2:
                    # 流已产出大半 → 保留流式结果，避免同一段话出现两遍
                    logger.info(
                        f"流式已产出 {len(full)} 字，同步回退结果 {len(text)} 字，"
                        f"保留流式结果"
                    )
                else:
                    full = text
                    emit({"type": "chunk", "content": text})
                fallback_used = True
        except Exception as fallback_exc:  # noqa: BLE001
            logger.error(f"同步生成也失败: {fallback_exc}")
            if not full:
                full = "（生成失败）"
                emit({"type": "chunk", "content": full})

    # ---- 截断判定 -------------------------------------------------------
    # 三种情况都算"可能不完整"：
    #   ① finish_reason == "length"        → 撞上 max_tokens
    #   ② 流报错且最终没有拿到 stop         → 中途断了（网络/风控/超时）
    #   ③ 拿到内容但 finish_reason 缺失     → provider 没给收尾标记
    truncated = finish_reason == "length" or (
        finish_reason is None and bool(full) and full != "（生成失败）"
    )

    if truncated:
        logger.warning(
            f"[截断] model={used_model} finish_reason={finish_reason} "
            f"chars={len(full)} usage={token_usage or 'n/a'} "
            f"stream_error={stream_error} "
            f"→ 回答可能不完整（max_tokens={deps.settings.model.max_tokens or '未设置(provider默认)'}）"
        )
    else:
        logger.info(
            f"[生成完成] model={used_model} finish_reason={finish_reason} "
            f"chars={len(full)} usage={token_usage or 'n/a'}"
            + ("（已用同步回退）" if fallback_used else "")
        )

    return {
        "answer": full,
        "used_model": used_model,
        "truncated": truncated,
        # 推理过程只随本次响应返回，**不写入对话记忆**：
        # 它是模型的草稿，带进下一轮反而会干扰回答
        "reasoning": "".join(reasoning_parts),
        "reasoning_ms": (
            int((time.perf_counter() - reasoning_started) * 1000)
            if reasoning_started is not None
            else 0
        ),
        "thinking": thinking_on,
    }



__all__ = [
    "clarify",
    "long_text",
    "build_prompt",
    "generate",
    "emit",
    "bind",
]
