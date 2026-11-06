"""文档管理与知识库路由。"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, File, Query, Request, UploadFile

from app.api.deps import DocumentServiceDep
from app.utils.files import MAX_UPLOAD_SIZE

#: 单次批量上传的文件数上限
MAX_BATCH_FILES = 50
from app.core.deps import AdminUser, CurrentUser, rate_limit
from app.core.exceptions import ValidationError
from app.core.exceptions import ValidationError
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
    # 只读到「上限 +1 字节」：超限文件在服务层会被拒，
    # 但不会先把几百 MB 读进内存。上传接口是最容易被灌爆的入口。
    contents = await file.read(MAX_UPLOAD_SIZE + 1)
    # 解析 + 切块 + 嵌入是**同步 CPU/IO 密集**操作，直接在事件循环里跑
    # 会让整个服务（含 /health）在文件处理期间无响应。丢到线程池。
    return await asyncio.to_thread(
        svc.upload,
        filename=file.filename or "",
        contents=contents,
        trace_id=get_trace_id(request),
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
    # 一次最多 50 个：没有上限时，一个请求就能塞进几十个 50MB 文件，
    # 内存和索引时间都会被放大到不可控。
    if len(files) > MAX_BATCH_FILES:
        raise ValidationError(f"一次最多上传 {MAX_BATCH_FILES} 个文件，请分批上传")

    payload: list[tuple[str, bytes]] = []
    for file in files:
        try:
            # 同单文件：读取封顶，超限交给服务层报「超过大小限制」
            payload.append(
                (file.filename or "", await file.read(MAX_UPLOAD_SIZE + 1))
            )
        except Exception as exc:  # noqa: BLE001  读取失败的文件单独记错误
            payload.append((file.filename or "", b""))
            _ = exc
    return await asyncio.to_thread(
        svc.upload_many, files=payload, trace_id=get_trace_id(request)
    )


@router.delete(
    "/documents/{file_name}",
    response_model=DeleteDocumentResult,
    summary="删除文档",
)
async def delete_document(
    file_name: str,
    request: Request,
    admin: AdminUser,
    svc: DocumentServiceDep,
) -> dict:
    """删除文档是**全局破坏性操作**，只允许部署管理员。"""
    return svc.delete(filename=file_name, trace_id=get_trace_id(request))


@router.post(
    "/rebuild-vectorstore", response_model=RebuildResult, summary="重建向量库"
)
async def rebuild_vectorstore_endpoint(
    admin: AdminUser, svc: DocumentServiceDep
) -> dict:
    """全量重建会清空并重索引整个知识库，只允许部署管理员。"""
    return await svc.rebuild()


@router.get("/documents", response_model=DocumentListResponse, summary="文档列表")
async def list_documents_endpoint(
    current_user: CurrentUser,
    svc: DocumentServiceDep,
    skip: int = Query(0, ge=0, description="跳过条目数"),
    limit: int = Query(100, ge=1, le=500, description="每页条数"),
) -> dict:
    # 全量扫描 Chroma metadata 是同步阻塞调用，必须挪出事件循环 ——
    # 修复前它直接跑在 async 端点里，把 /health 从 8ms 拖到 94.7ms。
    return await asyncio.to_thread(svc.list_documents, skip=skip, limit=limit)
