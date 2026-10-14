"""对话管理模型。"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field


class ConversationUpdate(BaseModel):
    title: Optional[str] = None
    pinned: Optional[bool] = None
    locked: Optional[bool] = None
    archived: Optional[bool] = None


class BatchDeleteRequest(BaseModel):
    conversation_ids: list[str]


class ConversationListResponse(BaseModel):
    conversations: list[Any] = Field(default_factory=list)
    total: int = 0
    skip: int = 0
    limit: int = 100


class ConversationDetailResponse(BaseModel):
    conversation: dict[str, Any] = Field(default_factory=dict)


class BatchDeleteResult(BaseModel):
    success: bool = True
    deleted_count: int = 0


class ShareCreateRequest(BaseModel):
    ttl_hours: Optional[int] = 24
    messages: Optional[list[dict[str, Any]]] = None


class ShareCreateResult(BaseModel):
    success: bool = True
    token: str
    url_path: str
    expires_at: float
    ttl_hours: int
