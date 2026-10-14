"""工作流 state 的键必须在 schema 里声明（静态检查）。

## 这条测试是为一个**静默** bug 加的

LangGraph 会按 `WorkflowState`（TypedDict）的声明**过滤**传入的 state ——
未声明的键被**静默丢弃**，不报错、不打日志。

实测踩到的表现：给服务层的 clarify/plan state 加了 `folder_listing`，
节点里读 `state.get("folder_listing")` 却永远拿到空串 —— 提示词里那一段
从头到尾就没进去过。单独看任何一处代码都是对的，只有把两处连起来才发现。
这类「参数加了但不生效」的问题排查成本极高，所以用静态检查钉死。
"""

from __future__ import annotations

import ast
from pathlib import Path

from app.graph.workflow.state import WorkflowState

SERVICE = (
    Path(__file__).resolve().parent.parent.parent / "app" / "services" / "workflow_service.py"
)


def _declared_keys() -> set[str]:
    return set(WorkflowState.__annotations__)


def _state_literal_keys(path: Path) -> set[str]:
    """收集源码里所有 `state: dict[str, Any] = {...}` 字面量的键。"""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.AnnAssign):
            continue
        if node.value is None or not isinstance(node.value, ast.Dict):
            continue
        # 只看名为 state 的变量，且注解是 dict[...]
        target = node.target
        if not isinstance(target, ast.Name) or target.id != "state":
            continue
        for key in node.value.keys:
            if isinstance(key, ast.Constant) and isinstance(key.value, str):
                found.add(key.value)
    return found


class TestWorkflowStateSchema:
    def test_service_state_keys_are_all_declared(self):
        """★ 服务层写进 state 的每个键都必须在 WorkflowState 里声明。

        没声明 = 被 LangGraph 静默丢掉 = 提示词里少一段而无人知晓。
        """
        used = _state_literal_keys(SERVICE)
        assert used, "没有从 workflow_service.py 里解析到任何 state 字面量（测试本身失效）"
        undeclared = sorted(used - _declared_keys())
        assert not undeclared, (
            f"这些键写进了 state 但没在 WorkflowState 里声明，会被静默丢弃: {undeclared}"
        )

    def test_folder_listing_is_declared(self):
        """具体到这次的功能：工作区文件夹清单必须能传到节点里。"""
        assert "folder_listing" in _declared_keys()
        # 模块用了 `from __future__ import annotations`，注解是 ForwardRef 而非 str 本体
        annotation = WorkflowState.__annotations__["folder_listing"]
        assert getattr(annotation, "__forward_arg__", str(annotation)) == "str"

    def test_context_keys_present(self):
        """上下文装载阶段的关键键一个都不能少。"""
        for key in ("file_refs", "file_summaries", "file_context", "folder_listing", "tools"):
            assert key in _declared_keys(), key

    def test_no_workspace_name_in_state(self):
        """工作区名刻意不进 state —— 它会在原始输入缺主题时被模型当成任务主题。"""
        assert "workspace_name" not in _declared_keys()
