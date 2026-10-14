"""文档管理与知识库路由。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, Query, Request, UploadFile

from app.api.deps import DocumentServiceDep
from app.core.deps import CurrentUser, rate_limit
from app.core.logging import get_trace_id
from app.schemas.document import (
    BatchUploadResult,
    DeleteDocumentResult,
    DocumentListResponse,
    RebuildResult,
    UploadResult,
)

router = APIRouter()


@router.post("/upload", response_model=UploadResult, summary="上传单个文档")
async def upload_file(
    request: Request,
    current_user: CurrentUser,
    svc: DocumentServiceDep,
    file: UploadFile = File(...),
    _: None = Depends(rate_limit("upload")),
) -> dict:
    contents = await file.read()
    return svc.upload(
        filename=file.filename or "", contents=contents, trace_id=get_trace_id(request)
    )


@router.post(
    "/upload-multiple", response_model=BatchUploadResult, summary="批量上传文档"
)
async def upload_multiple_files(
    request: Request,
    current_user: CurrentUser,
    svc: DocumentServiceDep,
    files: list[UploadFile] = File(...),
    _: None = Depends(rate_limit("upload")),
) -> dict:
    payload: list[tuple[str, bytes]] = []
    for file in files:
        try:
            payload.append((file.filename or "", await file.read()))
        except Exception as exc:  # noqa: BLE001  读取失败的文件单独记错误
            payload.append((file.filename or "", b""))
            _ = exc
    return svc.upload_many(files=payload, trace_id=get_trace_id(request))


@router.delete(
    "/documents/{file_name}",
    response_model=DeleteDocumentResult,
    summary="删除文档",
)
async def delete_document(
    file_name: str,
    request: Request,
    current_user: CurrentUser,
    svc: DocumentServiceDep,
) -> dict:
    return svc.delete(filename=file_name, trace_id=get_trace_id(request))


@router.post(
    "/rebuild-vectorstore", response_model=RebuildResult, summary="重建向量库"
)
async def rebuild_vectorstore_endpoint(
    current_user: CurrentUser, svc: DocumentServiceDep
) -> dict:
    return await svc.rebuild()


@router.get("/documents", response_model=DocumentListResponse, summary="文档列表")
async def list_documents_endpoint(
    current_user: CurrentUser,
    svc: DocumentServiceDep,
    skip: int = Query(0, ge=0, description="跳过条目数"),
    limit: int = Query(100, ge=1, le=500, description="每页条数"),
) -> dict:
    return svc.list_documents(skip=skip, limit=limit)
