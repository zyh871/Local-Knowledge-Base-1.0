"""Agent 交互接口模型（供前端预判路由使用）。"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class AgentClassifyRequest(BaseModel):
    question: str
    conversation_id: Optional[str] = None


class AgentClassifyResponse(BaseModel):
    intent: str
    confidence: float
    needs_clarification: bool
    clarification_prompt: str = ""
    detected_language: str = "auto"
    rewrite: str = ""
    reasons: list[str] = Field(default_factory=list)
