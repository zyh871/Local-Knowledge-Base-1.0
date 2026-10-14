"""
预处理节点：输入规范化与意图分类。

`ingest` 承担原来散落在 `router.route()` / `route_stream()` 开头的上下文装载，
并在此完成**会话窗口裁剪**（替代 v3 里 `ConversationBufferWindowMemory`
的进程内裁剪 —— 那套在多 worker 下不成立）。
"""

from __future__ import annotations

from app.core.logging import get_logger, new_trace_id
from app.graph.nodes import Deps
from app.graph.state import RAGState
from app.utils.text import lang_instruction

logger = get_logger(__name__)


def ingest(state: RAGState, deps: Deps) -> dict:
    """装载对话历史、用户画像、语言指令；判定会话锁定状态。"""
    from app.memory import get_profile, get_profile_context, is_locked, load_history

    conversation_id = state.get("conversation_id") or "default"
    username = state.get("username") or ""

    try:
        locked = bool(is_locked(conversation_id))
    except Exception as exc:  # noqa: BLE001  SQLite 故障不应阻断问答
        logger.warning(f"读取会话锁定状态失败，按未锁定处理: {exc}")
        locked = False

    try:
        history = load_history(conversation_id) or []
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"加载对话历史失败，按空历史处理: {exc}")
        history = []

    # 窗口裁剪：直接按「消息条数」切片，而非 trim_messages 的 token 策略——
    # 本项目窗口语义是 k 轮对话（2k 条消息），按条数切片语义更直接。
    window = deps.locked_window_size if locked else deps.window_size
    trimmed = history[-(window * 2) :] if window > 0 else []

    profile_lang = "auto"
    profile_ctx = ""
    if username:
        try:
            profile_lang = (get_profile(username) or {}).get("language", "auto")
        except Exception:  # noqa: BLE001
            profile_lang = "auto"
        try:
            profile_ctx = get_profile_context(username)
        except Exception:  # noqa: BLE001
            profile_ctx = ""

    return {
        "history": trimmed,
        "locked": locked,
        "profile_ctx": profile_ctx,
        "profile_lang": profile_lang,
        "lang_instruction": lang_instruction(
            profile_lang, state.get("detected_language", "auto")
        ),
        "trace_id": state.get("trace_id") or new_trace_id(),
    }


def classify_intent(state: RAGState, deps: Deps) -> dict:
    """意图分类（规则优先 + LLM 回退），复用既有分类器实现。"""
    from app.agents import classify
    from app.agents.intent_classifier import (
        INTENT_FILE_QA,
        INTENT_GENERAL,
        INTENT_KB_QA,
        IntentResult,
    )
    from app.utils.text import extract_filename

    question = state.get("question", "")
    history = state.get("history") or []

    try:
        result = classify(question, history)
    except Exception as exc:  # noqa: BLE001  分类失败降级为 kb_qa（与 v3 一致）
        logger.warning(f"意图分类失败，降级为 kb_qa: {exc}")
        result = IntentResult(intent=INTENT_KB_QA, confidence=0.5)

    # file_qa 但问题里没有可识别的文件名 → 回退为 general。
    # 与旧 `_handle_file_qa()` 中 `if not filename: return _handle_general(...)` 等价，
    # 但把判断上移到分类节点，使条件边保持为纯函数（不写 State）。
    target_filename = ""
    if result.intent == INTENT_FILE_QA:
        target_filename = extract_filename(question) or ""
        if not target_filename:
            result = IntentResult(
                intent=INTENT_GENERAL,
                confidence=result.confidence,
                detected_language=result.detected_language,
                reasons=[*result.reasons, "file_qa 未识别到文件名 -> general"],
            )

    return {
        "intent": result.intent,
        "intent_confidence": result.confidence,
        "needs_clarification": result.needs_clarification,
        "clarification_prompt": result.clarification_prompt,
        "detected_language": result.detected_language,
        "target_filename": target_filename,
        "lang_instruction": lang_instruction(
            state.get("profile_lang", "auto"), result.detected_language
        ),
    }
