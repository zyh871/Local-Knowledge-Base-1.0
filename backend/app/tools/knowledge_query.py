"""
知识库查询工具 — 将 RAG 检索封装为标准工具。

供 Function Calling Agent 和 ReAct Agent 在推理过程中
主动查询本地知识库，而非依赖路由器的单次检索。
"""

import logging
from typing import Any, Dict, List

from app.tools.base import BaseTool

logger = logging.getLogger(__name__)


class KnowledgeQueryTool(BaseTool):
    """本地知识库检索工具。"""

    @property
    def name(self) -> str:
        return "knowledge_query"

    @property
    def description(self) -> str:
        return "从本地知识库中检索与查询相关的文档片段。当需要查找已上传的文档、资料、知识库内容时使用。返回匹配的文档片段及其来源文件。"

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "检索查询语句",
                },
                "top_k": {
                    "type": "integer",
                    "description": "返回的文档片段数量，默认5",
                    "default": 5,
                },
            },
            "required": ["query"],
        }

    def execute(self, query: str = "", top_k: int = 5, **kwargs) -> Dict[str, Any]:
        query = (query or "").strip()
        if not query:
            return {"success": False, "error": "查询不能为空"}

        try:
            from app.rag_chain import hybrid_search

            docs = hybrid_search(query, k=top_k)
            if not docs:
                return {
                    "success": True,
                    "result": "未找到相关文档",
                    "documents": [],
                    "count": 0,
                }

            documents: List[Dict[str, Any]] = []
            for doc in docs:
                documents.append({
                    "content": doc.page_content[:500],
                    "source": doc.metadata.get("source", "unknown"),
                    "score": doc.metadata.get("score", 0),
                })

            combined = "\n\n---\n\n".join(d["content"] for d in documents)
            return {
                "success": True,
                "result": combined,
                "documents": documents,
                "count": len(documents),
            }
        except Exception as e:
            logger.error(f"知识库查询失败: {e}", exc_info=True)
            return {"success": False, "error": str(e)}
