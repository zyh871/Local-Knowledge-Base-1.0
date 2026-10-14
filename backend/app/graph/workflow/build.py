"""工作流状态图装配。

## 与 `qa_graph` 的三点不同

1. **不复用 `RAGState` 与 `route_by_intent`。** 工作流是「多相位 + 门禁 +
   可中断可恢复」，单轮问答的扁平 State 与意图分发在这里没有意义。
2. **不挂 checkpointer。** 状态的真相源是 `data/workflow.db` 的 `steps` 表 ——
   设计文档 §4.1 明确拒绝了「用 checkpointer 挂起 + 长连接等审批」，
   就是为了避免同一事实存在两份存储、两套更新路径。
3. **一次调用只推进一个相位。** 入口用 `state["phase"]` 选择（条件入口），
   各相位各自连到 END —— 因此可以说「调 plan」或「调 execute」，
   而不是把整条流水线跑完。这正是 HTTP 驱动状态机的落点。

## 当前拓扑

    START ──(phase)──┬─▶ clarify ─▶ END
                     ├─▶ plan    ─▶ END
                     └─▶ execute ─▶ END

后续分期会补上 `deliver` 入口，拓扑形状不变。
"""

from __future__ import annotations

from typing import Any

from langgraph.graph import END, START, StateGraph

from app.core.logging import get_logger
from app.graph.nodes import Deps, bind
from app.graph.workflow.nodes.clarify import clarify
from app.graph.workflow.nodes.execute import execute
from app.graph.workflow.nodes.plan import plan
from app.graph.workflow.state import WorkflowState

logger = get_logger(__name__)

__all__ = ["build_workflow_graph", "PHASES"]

#: 可作为独立入口的相位名。调用方通过 `state["phase"]` 选择。
PHASES = ("clarify", "plan", "execute")

DEFAULT_PHASE = "plan"


def _route_phase(state: dict) -> str:
    """条件入口：按 `phase` 选相位，未知值回落到默认相位。

    刻意**不抛异常** —— 入口名打错这种问题应该在调用方就被挡住，
    而不是让整个图在运行时炸掉。
    """
    phase = str(state.get("phase") or DEFAULT_PHASE)
    if phase not in PHASES:
        logger.warning(f"未知的工作流相位「{phase}」，回落到 {DEFAULT_PHASE}")
        return DEFAULT_PHASE
    return phase


def build_workflow_graph(*, settings: Any, chain: Any, redis: Any = None) -> Any:
    """构建并编译工作流图。

    构建失败即启动失败（与 qa_graph 一致的 fail-fast 语义）——
    带着不可用的编排对外服务比启动失败更糟。
    """
    deps = Deps(chain=chain, settings=settings, redis=redis)

    graph = StateGraph(WorkflowState)
    graph.add_node("clarify", bind(clarify, deps))
    graph.add_node("plan", bind(plan, deps))
    graph.add_node("execute", bind(execute, deps))

    graph.add_conditional_edges(
        START,
        _route_phase,
        {phase: phase for phase in PHASES},
    )
    for phase in PHASES:
        graph.add_edge(phase, END)

    compiled = graph.compile()
    logger.info(
        "工作流状态图已编译"
        f"（相位={','.join(PHASES)}；无 checkpointer：状态真相源为 workflow.db）"
    )
    return compiled
