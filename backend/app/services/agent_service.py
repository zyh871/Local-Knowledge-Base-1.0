"""Agent 交互接口用例（意图清单 / 意图分类 / 能力清单）。"""

from __future__ import annotations

from typing import Any

from app.core.exceptions import ValidationError
from app.utils.files import IMAGE_EXTENSIONS

INTENT_DESCRIPTIONS: dict[str, str] = {
    "kb_qa": "知识库问答 — 基于本地知识库的 RAG 检索回答",
    "general": "通用对话 — 日常闲聊、世界知识等无需检索的场景",
    "long_text": "长文本处理 — 用户粘贴长文档进行摘要/翻译/问答",
    "file_qa": "文件问答 — 针对特定上传文件的问答",
    "task": "任务执行 — 写作、翻译、总结等显式任务请求",
    "agent": "多步Agent — 需要分步骤、多工具协作的复杂任务",
    "clarification_needed": "需要澄清 — 问题过于模糊，需要用户补充信息",
}

DATA_SOURCES = [
    "rag",
    "rag_search",
    "web_search",
    "llm",
    "long_text",
    "file_qa",
    "vision",
]


class AgentService:
    def __init__(self, *, settings: Any) -> None:
        self.settings = settings

    # ------------------------------------------------------------------
    #  意图清单
    # ------------------------------------------------------------------

    def intents(self) -> dict[str, Any]:
        return {
            "intents": [
                {"name": name, "description": desc}
                for name, desc in INTENT_DESCRIPTIONS.items()
            ],
            "data_sources": list(DATA_SOURCES),
        }

    # ------------------------------------------------------------------
    #  意图分类（不执行实际回答）
    # ------------------------------------------------------------------

    def classify(
        self, *, question: str, conversation_id: Any
    ) -> dict[str, Any]:
        from app.agents import classify as classify_intent
        from app.memory import load_history

        text = (question or "").strip()
        if not text:
            raise ValidationError("问题不能为空")
        if len(text) > 2000:
            raise ValidationError("问题长度不能超过2000字符")

        history: list[dict[str, str]] = []
        if conversation_id:
            try:
                history = load_history(conversation_id)
            except Exception:  # noqa: BLE001
                history = []

        result = classify_intent(text, history)
        return {
            "intent": result.intent,
            "confidence": result.confidence,
            "needs_clarification": result.needs_clarification,
            "clarification_prompt": result.clarification_prompt,
            "detected_language": result.detected_language,
            "rewrite": result.rewrite,
            "reasons": result.reasons,
        }

    # ------------------------------------------------------------------
    #  能力清单
    # ------------------------------------------------------------------

    def capabilities(self) -> dict[str, Any]:
        from app.agents.intent_classifier import ALL_INTENTS  # noqa: F401  保持模块可导入性

        capabilities: list[dict[str, Any]] = [
            {
                "name": "knowledge_base",
                "description": "本地知识库检索问答",
                "endpoint": "/ask",
                "methods": ["POST"],
                "stream_endpoint": "/ask/stream",
            },
            {
                "name": "web_search",
                "description": "联网搜索实时信息",
                "auto_trigger": True,
                "trigger_keywords": ["最新", "新闻", "天气", "股价", "汇率"],
            },
            {
                "name": "image_qa",
                "description": "图片视觉问答（多模态）",
                "endpoint": "/ask/image",
                "methods": ["POST"],
                "supported_formats": sorted(IMAGE_EXTENSIONS),
            },
            {
                "name": "long_text",
                "description": "长文本摘要/翻译/问答",
                "endpoint": "/long-text",
                "methods": ["POST"],
                "operations": ["summary", "translate", "qa"],
            },
            {
                "name": "document_management",
                "description": "文档上传、删除、列表管理",
                "endpoints": [
                    "/upload",
                    "/upload-multiple",
                    "/documents",
                    "/documents/{file_name}",
                ],
            },
            {
                "name": "conversation_management",
                "description": "对话管理：创建、列表、删除、分享、导出",
                "endpoints": [
                    "/conversations",
                    "/conversations/{id}",
                    "/conversations/{id}/share",
                ],
            },
        ]
        model = self.settings.model
        return {
            "capabilities": capabilities,
            "model_backend": model.backend,
            "llm_model": model.llm_model,
            "vision_model": model.vision_model,
            "embedding_model": model.embedding_model,
        }
