"""AI 工作流状态图。

与 `app/graph/`（qa_graph）并列的另一张图 —— 拓扑与语义都不同，不复用其 State。
复用其下层资产：`Deps` / `bind` / `emit` / `make_checkpointer`（本图不需要）。
"""

from app.graph.workflow.build import build_workflow_graph  # noqa: F401

__all__ = ["build_workflow_graph", "build_plan_graph"]


def build_plan_graph(*, settings, chain, redis=None):
    """语义别名：P2 阶段图里只有 plan 相位。

    保留这个名字是为了让调用方读起来意图明确；P3 起拓扑会扩展，
    届时应以 `build_workflow_graph` 为准。
    """
    return build_workflow_graph(settings=settings, chain=chain, redis=redis)
