"""工作流图调用入口。

与 `graph/runner.py` 的差别：这里没有 checkpointer，因此**不能**用
`graph.aget_state()` 取最终状态。改为同时订阅 `values` 通道 ——
LangGraph 每完成一个超级步就推一次完整状态，最后一个即终态。
"""

from __future__ import annotations

from typing import Any, AsyncIterator

from app.core.logging import get_logger

logger = get_logger(__name__)

__all__ = ["stream_phase"]


async def stream_phase(
    graph: Any,
    *,
    node: str,
    state: dict[str, Any],
) -> AsyncIterator[dict[str, Any]]:
    """驱动一个相位节点，依次产出 SSE 事件字典。

    用法：
        async for event in stream_phase(graph, node="plan", state=...):
            yield sse_frame(event)

    `node` 会被写进 `state["phase"]` —— 图用**条件入口**按它选相位，
    因此这里注入而不是要求每个调用方自己带（少一处能写错的地方）。
    """
    payload_state = {**state, "phase": node}

    final_state: dict[str, Any] = {}
    async for mode, payload in graph.astream(
        payload_state, stream_mode=["custom", "values"]
    ):
        if mode == "custom":
            yield payload
        elif mode == "values":
            # values 通道每步都推全量状态，取最后一个即终态
            if isinstance(payload, dict):
                final_state = payload

    logger.debug(f"[workflow] 相位 {node} 结束")
    yield {"type": "_state", "content": final_state}
