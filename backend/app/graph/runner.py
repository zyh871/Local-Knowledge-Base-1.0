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

import asyncio
from typing import Any, AsyncIterator, Optional

from app.core.logging import get_logger

logger = get_logger(__name__)

#: 流式执行时，两个事件之间的最长等待时间（秒）。
#:
#: 为什么是「空闲超时」而不是「总超时」：一次问答里真正耗时的是 LLM 生成，
#: 而它的长度取决于回答篇幅，没法给一个既安全又不误杀的**总**时限。
#: 卡死则不同 —— 卡死表现为**长时间没有任何事件**。所以盯「两次事件之间的
#: 间隔」既能兜住卡死，又不会因为"回答写得太长"而被误杀。
#:
#: 取 180s：远大于正常分片间隔（毫秒级），也大于单个 LLM 调用的常见时长。
_STREAM_IDLE_TIMEOUT_SECONDS = 180.0


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
    thinking: bool = False,
) -> dict:
    return {
        "question": question,
        "conversation_id": conversation_id or "default",
        "username": username or "",
        "force_search": bool(force_search),
        "trace_id": trace_id,
        "model": model or "",
        "thinking": bool(thinking),
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
        # 深度思考：推理原文与耗时。老前端忽略这两个字段，行为不变
        "thinking": bool(values.get("thinking", False)),
        "reasoning": values.get("reasoning", "") or "",
        "reasoning_ms": int(values.get("reasoning_ms", 0) or 0),
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
    thinking: bool = False,
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
        thinking=thinking,
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
    thinking: bool = False,
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
        thinking=thinking,
    )

    # 用「空闲超时」包住迭代：`graph.astream()` 只是异步迭代器，
    # 没法直接丢进 `asyncio.wait_for`，所以按「取下一个事件」逐个计时。
    # 若某个节点卡住（网络挂起 / 死循环），这里会中断并抛出，
    # 而不是让整条 SSE 静默挂在那里 —— 后者用户看到的是"一直在转圈"。
    stream = graph.astream(state, config=config, stream_mode=["updates", "custom"])
    try:
        while True:
            try:
                mode, payload = await asyncio.wait_for(
                    stream.__anext__(), timeout=_STREAM_IDLE_TIMEOUT_SECONDS
                )
            except StopAsyncIteration:
                break
            if mode == "custom":
                # 节点内通过 get_stream_writer() 推出的事件（intent/sources/chunk/...）
                yield payload
            elif mode == "updates":
                for node_name in (payload or {}).keys():
                    logger.debug(f"[{trace_id}] 节点完成: {node_name}")
    except asyncio.TimeoutError as exc:
        logger.error(
            f"[{trace_id}] 图执行超过 {_STREAM_IDLE_TIMEOUT_SECONDS:.0f} 秒无事件，已中断"
        )
        raise TimeoutError(
            f"生成超时（{_STREAM_IDLE_TIMEOUT_SECONDS:.0f} 秒内没有进展），请重试"
        ) from exc
    finally:
        # 善后：中断时把底层异步生成器关掉，避免它继续挂在后台
        aclose = getattr(stream, "aclose", None)
        if aclose is not None:
            try:
                await aclose()
            except Exception:  # noqa: BLE001 - 善后失败不影响主流程
                pass

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
