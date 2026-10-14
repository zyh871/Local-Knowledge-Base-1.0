"""
工具基类与注册中心 — 统一的工具接口规范。

所有工具继承 BaseTool，实现 name / description / parameters / execute，
注册到 ToolRegistry 后可被 Agent 自动发现和调用。
"""

import logging
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


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

    def execute(self, name: str, **kwargs) -> Dict[str, Any]:
        tool = self.get(name)
        if not tool:
            return {"success": False, "error": f"工具 {name} 不存在"}
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
