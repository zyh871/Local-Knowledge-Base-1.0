"""工作流图节点。

文件与节点对应关系（随分期推进逐步补齐）：

    plan.py       plan                    ← P2
    clarify.py    clarify                 ← P4
    execute.py    execute_step            ← P3
    deliver.py    deliver                 ← P7

节点签名统一为 `(state, deps)`，由 build 用 `bind()` 绑定依赖 ——
与 `app/graph/nodes/` 保持同一套约定，节点本身因此是纯函数、可单测。
"""

from __future__ import annotations

from app.graph.nodes import Deps, bind  # noqa: F401  重新导出，便于子模块统一引用

__all__ = ["Deps", "bind"]
