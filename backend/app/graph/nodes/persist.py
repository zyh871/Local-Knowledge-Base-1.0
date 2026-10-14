"""持久化节点：把本轮问答写回对话记忆与用户画像。"""

from __future__ import annotations

from app.core.logging import get_logger
from app.graph.nodes import Deps
from app.graph.state import RAGState

logger = get_logger(__name__)


def persist(state: RAGState, deps: Deps) -> dict:
    """写回短期记忆（对话消息）与长期记忆（用户画像事实）。

    与 v3 一致：需要澄清的轮次**不写入**记忆。
    写失败不抛异常 —— 记忆是增强项，不应让已经生成的回答丢失。
    """
    if state.get("needs_clarification"):
        logger.debug("需要澄清的轮次，跳过记忆写回")
        return {}

    conversation_id = state.get("conversation_id") or "default"
    username = state.get("username") or ""
    question = state.get("question", "")
    answer = state.get("answer", "")

    if not answer:
        logger.debug("回答为空，跳过记忆写回")
        return {}

    try:
        from app.memory import save_context

        save_context(conversation_id, question, answer, locked=bool(state.get("locked")))
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"写回对话记忆失败: {exc}")

    if username:
        try:
            from app.memory.long_term import remember_turn

            remember_turn(username, question)
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"长期记忆抽取失败（可忽略）: {exc}")

    return {}
