"""
状态图调用入口 —— 把图执行结果翻译成 v3 的响应契约。

两条调用路径共用同一张图：
    run_graph    非流式：`ainvoke` → 最终 State → 响应字典
    stream_graph 流式：`astream(stream_mode=["updates","custom"])`
                  → custom 通道即节点内 `get_stream_writer()` 推出的事件
                  → 结束时补一个 `final` 事件

这样「编排只写一次」——`route()` 与 `route_stream()` 的双份维护问题被消除。
"""

from __future__ import annotations

from typing import Any, AsyncIterator, Optional

from app.core.logging import get_logger

logger = get_logger(__name__)


def thread_id_for(conversation_id: str, username: str) -> str:
    """线程标识 = `{username}:{conversation_id}`。

    ⚠️ 不要用 `checkpoint_ns` 做用户隔离 ——
    LangGraph 把 `checkpoint_ns` 解释为**子图命名空间**，
    传入 "admin" 会直接报 `ValueError: Subgraph admin not found`。
    用户维度必须并入 `thread_id`。
    """
    return f"{username or 'anon'}:{conversation_id or 'default'}"


def graph_config(settings: Any, conversation_id: str, username: str) -> dict:
    """构造图执行配置。

    同一 conversation_id 天然形成一条可恢复的执行线；
    `thread_id` 前缀用户名，隔离不同用户的同名会话 ID。
    """
    return {
        "configurable": {"thread_id": thread_id_for(conversation_id, username)},
        "recursion_limit": settings.graph.recursion_limit,
    }


def initial_state(
    *,
    question: str,
    conversation_id: str,
    username: str,
    force_search: bool,
    trace_id: str,
    model: Optional[str] = None,
) -> dict:
    return {
        "question": question,
        "conversation_id": conversation_id or "default",
        "username": username or "",
        "force_search": bool(force_search),
        "trace_id": trace_id,
        "model": model or "",
    }


def final_answer(values: dict) -> dict:
    """从最终 State 提取 v3 契约要求的响应字段。"""
    return {
        "answer": values.get("answer", ""),
        "sources": values.get("sources", []) or [],
        "data_source": values.get("data_source") or values.get("intent", ""),
        "intent": values.get("intent", ""),
        "intent_confidence": values.get("intent_confidence", 0.0),
        "detected_language": values.get("detected_language", "auto"),
        "needs_clarification": bool(values.get("needs_clarification", False)),
        # 新增（前端可选消费）：本次实际使用的模型，以及回答是否可能被截断
        "model": values.get("used_model", "") or "",
        "truncated": bool(values.get("truncated", False)),
    }


async def run_graph(
    graph: Any,
    settings: Any,
    *,
    question: str,
    conversation_id: str,
    username: str,
    force_search: bool = False,
    trace_id: str = "-",
    model: Optional[str] = None,
    redis: Any = None,
) -> dict:
    """非流式执行。异常向上抛，由 service 层翻译为旧错误响应体。"""
    config = graph_config(settings, conversation_id, username)
    state = initial_state(
        question=question,
        conversation_id=conversation_id,
        username=username,
        force_search=force_search,
        trace_id=trace_id,
        model=model,
    )

    values = await graph.ainvoke(state, config=config)
    await _record_activity(redis, settings, conversation_id, username)
    logger.debug(f"[{trace_id}] 图执行完成: intent={values.get('intent')}")
    return final_answer(values)


async def stream_graph(
    graph: Any,
    settings: Any,
    *,
    question: str,
    conversation_id: str,
    username: str,
    force_search: bool = False,
    trace_id: str = "-",
    model: Optional[str] = None,
    redis: Any = None,
) -> AsyncIterator[dict]:
    """流式执行，产出 SSE 事件字典（不含 trace_id，由 service 层统一附加）。"""
    config = graph_config(settings, conversation_id, username)
    state = initial_state(
        question=question,
        conversation_id=conversation_id,
        username=username,
        force_search=force_search,
        trace_id=trace_id,
        model=model,
    )

    async for mode, payload in graph.astream(
        state, config=config, stream_mode=["updates", "custom"]
    ):
        if mode == "custom":
            # 节点内通过 get_stream_writer() 推出的事件（intent/sources/chunk/...）
            yield payload
        elif mode == "updates":
            for node_name in (payload or {}).keys():
                logger.debug(f"[{trace_id}] 节点完成: {node_name}")

    snapshot = await graph.aget_state(config)
    values = dict(getattr(snapshot, "values", {}) or {})
    await _record_activity(redis, settings, conversation_id, username)
    yield {"type": "final", "content": final_answer(values)}


async def _record_activity(
    redis: Any, settings: Any, conversation_id: str, username: str
) -> None:
    """登记 checkpoint 活跃线程（供 TTL 清理任务扫描）。"""
    if redis is None:
        return
    from app.graph.checkpointer import record_checkpoint_activity

    await record_checkpoint_activity(
        redis,
        thread_id=thread_id_for(conversation_id, username),
        ttl=settings.redis.checkpoint_ttl,
    )
