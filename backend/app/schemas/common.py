"""通用模型：错误响应、分页、来源条目。"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field


class SourceItem(BaseModel):
    """引用来源。

    字段刻意保持宽松：RAG 来源用 `file`/`content_preview`，
    联网来源用 `source`/`tool_call` —— 两者历史上共用同一列表，
    收紧类型会破坏现有响应结构。
    """

    file: Optional[str] = None
    content_preview: Optional[str] = None
    source: Optional[str] = None
    tool_call: Optional[str] = None
    url: Optional[str] = None

    model_config = {"extra": "allow"}


class ErrorBody(BaseModel):
    code: str
    message: str
    detail: Any = None
    trace_id: str = "-"


class ErrorResponse(BaseModel):
    """统一错误响应（app/core/exceptions.py 产出）。

    同时保留 `detail` 顶层字段，兼容前端旧有读取方式。
    """

    error: ErrorBody
    detail: Any = None


class PageMeta(BaseModel):
    total: int = 0
    skip: int = 0
    limit: int = 100


class OkResponse(BaseModel):
    success: bool = True
    trace_id: str = Field("", description="请求链路 ID")
