"""文档上传与知识库管理模型。"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field


class UploadResult(BaseModel):
    """单个文件上传结果（`/upload`）。"""

    success: bool = True
    file: str
    chunks_added: int = 0
    trace_id: str = ""


class BatchFileResult(BaseModel):
    file: Optional[str] = None
    success: bool
    chunks_added: Optional[int] = None
    error: Optional[str] = None


class BatchUploadResult(BaseModel):
    """批量上传结果（`/upload-multiple`）。"""

    results: list[BatchFileResult] = Field(default_factory=list)
    trace_id: str = ""


class DocumentItem(BaseModel):
    filename: str
    size: int = 0
    chunks: int = 0

    model_config = {"extra": "allow"}


class DocumentListResponse(BaseModel):
    documents: list[Any] = Field(default_factory=list)
    total: int = 0
    skip: int = 0
    limit: int = 100


class DeleteDocumentResult(BaseModel):
    success: bool = True
    file: str


class RebuildResult(BaseModel):
    success: bool = True
    message: str = "向量库重建完成"
