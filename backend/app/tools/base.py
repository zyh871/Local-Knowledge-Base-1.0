"""
工具基类与注册中心 — 统一的工具接口规范。

所有工具继承 BaseTool，实现 name / description / parameters / execute，
注册到 ToolRegistry 后可被 Agent 自动发现和调用。
"""

import logging
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

#: 读取配置失败时的兜底审批清单。
#:
#: 与 `WorkflowSettings.require_approval_tools` 的默认值保持一致 ——
#: 宁可多要求一次审批，也不要在配置异常时**静默放行**危险工具。
_FALLBACK_GATED_TOOLS = ("code_executor", "file_write")


class ApprovalGrant:
    """审批凭据 —— 「服务端已确认本次调用通过审批」的显式声明。

    ## 为什么用对象而不是布尔标志

    `execute(tool, approved=True)` 这类签名会让"传 True"变成下意识动作，
    审计时也看不出**审批来自哪里**。凭据强制调用方回答两个问题：
    哪个工具、谁批的（`source`）—— 日志里能直接读出审批链。

    ## ⚠️ 它不是安全边界

    任何代码都能构造一个 `ApprovalGrant`。它防的是「**新增了一条路径却忘了门禁**」：
    默认拒绝，想放行必须显式声明 —— 于是漏掉门禁的路径会**立刻报错**，
    而不是静默地把危险工具放出去。真正的隔离要靠进程级手段（见
    `code_executor` 的说明）。
    """

    __slots__ = ("tool", "source")

    def __init__(self, tool: str, source: str):
        self.tool = tool
        self.source = source

    def covers(self, tool: str) -> bool:
        """该凭据是否覆盖指定工具。"""
        return self.tool == tool

    def __repr__(self) -> str:  # pragma: no cover - 仅用于日志/调试
        return f"ApprovalGrant(tool={self.tool!r}, source={self.source!r})"


class BaseTool(ABC):
    """工具抽象基类。"""

    @property
    @abstractmethod
    def name(self) -> str:
        """工具唯一标识名（snake_case）。"""

    @property
    @abstractmethod
    def description(self) -> str:
        """工具描述，供 LLM 理解何时使用该工具。"""

    @property
    @abstractmethod
    def parameters(self) -> Dict[str, Any]:
        """JSON Schema 格式的参数定义。"""

    @abstractmethod
    def execute(self, **kwargs) -> Dict[str, Any]:
        """执行工具逻辑，返回包含 success 和 result/error 的字典。"""

    def to_function_schema(self) -> Dict[str, Any]:
        """转换为 OpenAI function calling 格式。"""
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters,
        }


class ToolRegistry:
    """工具注册中心，支持按名称查找和批量导出。"""

    def __init__(self):
        self._tools: Dict[str, BaseTool] = {}

    @property
    def gated_tools(self) -> List[str]:
        """必须先过审批才能执行的工具名。

        来源是 `WorkflowSettings.require_approval_tools`（默认
        `code_executor,file_write`）。

        为什么声明放在注册表、强制点在 `execute()`：
        注册表是**所有调用路径唯一的汇合点**（`/ask` 的 function-calling、
        ReAct、工具子图、工作流执行都要经过它）。在四个调用点各打一个补丁，
        一定会漏掉第六条新路径 —— 而漏掉的那条正是没有门禁的那条。
        """
        try:
            from app.core.config import get_settings

            return list(get_settings().workflow.approval_tool_list)
        except Exception:  # noqa: BLE001 - 配置异常不能变成"静默放行"
            logger.warning("读取审批工具清单失败，回退到内置默认值", exc_info=True)
            return list(_FALLBACK_GATED_TOOLS)

    def register(self, tool: BaseTool) -> None:
        if tool.name in self._tools:
            logger.warning(f"工具 {tool.name} 已存在，将被覆盖")
        self._tools[tool.name] = tool
        logger.info(f"注册工具: {tool.name}")

    def unregister(self, name: str) -> None:
        self._tools.pop(name, None)

    def get(self, name: str) -> Optional[BaseTool]:
        return self._tools.get(name)

    def list_names(self) -> List[str]:
        return list(self._tools.keys())

    def list_all(self) -> List[BaseTool]:
        return list(self._tools.values())

    def to_function_schemas(self) -> List[Dict[str, Any]]:
        """导出所有工具的 function calling schema。"""
        return [t.to_function_schema() for t in self._tools.values()]

    def execute(
        self, name: str, *, _approval: Optional[ApprovalGrant] = None, **kwargs
    ) -> Dict[str, Any]:
        """执行工具；`gated_tools` 里的工具**必须先拿到审批凭据**。

        `_approval` 用下划线前缀是刻意的：它**不是**工具参数，不能被
        `**kwargs` 透传给工具实现；同时也提醒调用方这是一层内部协议。

        为什么把门禁放在这里而不是各调用点：见 `gated_tools` 的说明。
        这里默认**拒绝**，所以任何新增调用路径只要忘了传凭据就会立刻失败 ——
        失败是响的，漏掉是静默的，两者差别很大。
        """
        tool = self.get(name)
        if not tool:
            return {"success": False, "error": f"工具 {name} 不存在"}

        if name in self.gated_tools and not (
            _approval is not None and _approval.covers(name)
        ):
            logger.warning(
                f"工具 {name} 未通过审批门禁，已拒绝执行（凭据：{_approval!r}）"
            )
            return {
                "success": False,
                "tool": name,
                "gated": True,
                "error": (
                    f"工具「{name}」属于高风险工具，必须经人工审批后才能执行。"
                    "请在工作流中逐步确认该步骤，或改用其它方式完成。"
                ),
            }

        try:
            result = tool.execute(**kwargs)
            if "success" not in result:
                result["success"] = True
            result["tool"] = name
            return result
        except Exception as e:
            logger.error(f"工具 {name} 执行失败: {e}", exc_info=True)
            return {"success": False, "error": str(e), "tool": name}


_registry: Optional[ToolRegistry] = None


def get_registry() -> ToolRegistry:
    global _registry
    if _registry is None:
        _registry = ToolRegistry()
        _auto_register()
    return _registry


def _auto_register():
    """自动注册所有内置工具。"""
    from app.tools.calculator import CalculatorTool
    from app.tools.code_executor import CodeExecutorTool
    from app.tools.file_write import FileWriteTool
    from app.tools.knowledge_query import KnowledgeQueryTool

    _registry.register(CalculatorTool())
    _registry.register(CodeExecutorTool())
    # file_write 是**唯一**能产出文件的工具。此前只有配置里写着它的名字，
    # 实现却不存在，于是「让 AI 写个文件」在工作流里没有任何通路。
    _registry.register(FileWriteTool())
    _registry.register(KnowledgeQueryTool())

    try:
        from app.tools.web_search import WebSearchToolAdapter
        _registry.register(WebSearchToolAdapter())
    except Exception as e:
        logger.warning(f"联网搜索工具注册失败: {e}")
