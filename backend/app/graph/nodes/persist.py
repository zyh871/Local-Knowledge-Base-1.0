"""持久化节点：把本轮问答写回对话记忆与用户画像。"""

from __future__ import annotations

from app.core.exceptions import PermissionDeniedError
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

    username = state.get("username") or ""
    # 不回落到全局字面量 "default" —— 那会让所有"没传 id"的用户共用同一行历史。
    # 这里与 `qa_service.resolve_conversation_id()` 用同一条派生规则。
    conversation_id = state.get("conversation_id") or f"default:{username or 'anon'}"
    question = state.get("question", "")
    answer = state.get("answer", "")

    if not answer:
        logger.debug("回答为空，跳过记忆写回")
        return {}

    try:
        from app.memory import save_context

        # username 必须传下去：写侧要靠它做归属校验并给消息落作者，
        # 否则 `conversation_messages` 里谁写的永远查不出来。
        save_context(
            conversation_id,
            question,
            answer,
            locked=bool(state.get("locked")),
            username=username,
        )
    except PermissionDeniedError:
        # 归属校验在 `qa_service` 入口已经拦过一次；走到这里说明状态里的
        # conversation_id 与请求时的不是同一个（或并发下被人抢先认领）。
        # 不抛：此时回答已经产出，抛出去只会让用户丢掉已经生成的答案。
        logger.error(
            f"拒绝写入他人会话（conversation_id={conversation_id} username={username}）"
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"写回对话记忆失败: {exc}")

    if username:
        try:
            from app.memory.long_term import remember_turn

            remember_turn(username, question)
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"长期记忆抽取失败（可忽略）: {exc}")

    return {}
