"""
Tools package — external APIs and utilities.
"""

from .base import BaseTool, ToolRegistry, get_registry
from .web_search import build_search_context, search, should_search, WebSearchToolAdapter
from .calculator import CalculatorTool
from .code_executor import CodeExecutorTool
from .file_write import FileWriteTool
from .knowledge_query import KnowledgeQueryTool
