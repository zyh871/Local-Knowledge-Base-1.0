"""用户画像、长文本工具、反馈模型。"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
#  用户画像
# ---------------------------------------------------------------------------


class UserProfileUpdate(BaseModel):
    display_name: Optional[str] = None
    profession: Optional[str] = None
    language: Optional[str] = None
    facts: Optional[list] = None
    preferences: Optional[dict] = None


class ProfileResponse(BaseModel):
    profile: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
#  长文本工具
# ---------------------------------------------------------------------------


class LongTextRequest(BaseModel):
    text: str
    operation: str = "summary"  # summary | translate | qa
    target_language: Optional[str] = None
    question: Optional[str] = None
    max_chars: int = 600


class LongTextResponse(BaseModel):
    result: str
    operation: str


# ---------------------------------------------------------------------------
#  反馈
# ---------------------------------------------------------------------------


class FeedbackRequest(BaseModel):
    message_id: str
    conversation_id: str
    rating: int
    comment: Optional[str] = None


class FeedbackStatsResponse(BaseModel):
    total: int = 0
    ratings: dict[str, int] = Field(default_factory=dict)
    badcase_count: int = 0
    recent: list[Any] = Field(default_factory=list)


class FeedbackListResponse(BaseModel):
    items: list[Any] = Field(default_factory=list)
    total: int = 0
    skip: int = 0
    limit: int = 50
