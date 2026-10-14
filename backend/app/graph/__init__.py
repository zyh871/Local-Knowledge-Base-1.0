"""
LangGraph 状态图 —— 把业务流程表达为显式的 State + Node + Edge。

替代 `app/agents/router.py` 里 `route()` / `route_stream()` 两份几乎重复的
过程式 if/elif 分发：编排只写一次，同步与流式两条调用路径共用同一张图。

模块：
    state.py         State 结构与 reducer（数据契约）
    edges.py         条件边路由函数（唯一的编排决策点）
    nodes/           节点实现
    subgraphs/       工具调用子图
    checkpointer.py  checkpoint 持久化（Redis / 内存 / 关闭）
    build.py         图装配
    runner.py        调用入口：非流式 / 流式（SSE 事件映射）
"""

from app.graph.state import AgentState, RAGState

__all__ = ["RAGState", "AgentState"]
