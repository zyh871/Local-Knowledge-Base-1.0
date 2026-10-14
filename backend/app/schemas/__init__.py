"""
对外契约层（Pydantic 模型）。

本层是 **API 与内部实现的唯一契约边界**：
    * API 层只读写本层模型，不直接暴露内部 State / dict
    * 字段名与响应结构**必须与 v3.0.0 完全一致**（前端零改动是硬约束）

⚠️ 已知的有意保留项：
    `QuestionRequest.validate_question()` 保留为**方法**而非 field_validator。
    旧 `/ask` 的校验失败返回 400 + `{"answer":..., "data_source":"error"}`，
    改用 field_validator 会变成 FastAPI 原生的 422 + 另一种响应体 —— 属于契约破坏。
    因此校验逻辑保持在方法内，由 service 层调用并映射为旧响应形状。
"""

from app.schemas.agent import AgentClassifyRequest, AgentClassifyResponse
from app.schemas.auth import Token, TokenData, User, UserInDB
from app.schemas.common import ErrorBody, ErrorResponse, SourceItem
from app.schemas.conversation import (
    BatchDeleteRequest,
    ConversationListResponse,
    ConversationUpdate,
)
from app.schemas.document import (
    BatchUploadResult,
    DocumentItem,
    DocumentListResponse,
    UploadResult,
)
from app.schemas.misc import (
    FeedbackListResponse,
    FeedbackRequest,
    FeedbackStatsResponse,
    LongTextRequest,
    LongTextResponse,
    ProfileResponse,
    UserProfileUpdate,
)
from app.schemas.qa import (
    SSEEvent,
    SSEChunk,
    SSEFinal,
    SSEIntent,
    SSESources,
    SSEToolCalls,
    ModelInfo,
    ModelListResponse,
    QuestionRequest,
    QuestionResponse,
)

__all__ = [
    # common
    "ErrorBody",
    "ErrorResponse",
    "SourceItem",
    # auth
    "Token",
    "TokenData",
    "User",
    "UserInDB",
    # qa
    "QuestionRequest",
    "QuestionResponse",
    "ModelInfo",
    "ModelListResponse",
    "SSEEvent",
    "SSEIntent",
    "SSESources",
    "SSEChunk",
    "SSEToolCalls",
    "SSEFinal",
    # document
    "UploadResult",
    "BatchUploadResult",
    "DocumentItem",
    "DocumentListResponse",
    # conversation
    "ConversationUpdate",
    "BatchDeleteRequest",
    "ConversationListResponse",
    # misc
    "UserProfileUpdate",
    "ProfileResponse",
    "LongTextRequest",
    "LongTextResponse",
    "FeedbackRequest",
    "FeedbackStatsResponse",
    "FeedbackListResponse",
    # agent
    "AgentClassifyRequest",
    "AgentClassifyResponse",
]
