"""AI 创作（文生图 / 文生视频）路由。

命名空间 `/creations/*`，与对话、工作流并列。

⚠️ 媒体接口必须**带鉴权**：作品是私有内容，不能像 CDN 一样裸奔。
因此前端不能把 URL 直接塞进 `<img src>` / `<video src>`（浏览器不会带
Authorization 头），而是用 fetch 取成 blob 再显示 —— 见 `api/creations.js`。

⚠️ 视频是**两段式**：`POST /creations/videos` 只提交任务并立刻返回，
前端轮询 `GET /creations/{id}`，后端在查询时顺带向上游推进状态。
详见 `services/creation_service.py` 顶部说明。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, UploadFile
from fastapi.responses import FileResponse

from app.api.deps import CreationServiceDep
from app.core.deps import CurrentUser, rate_limit
from app.schemas.creation import (
    CreationDeleteResponse,
    CreationInfo,
    CreationListResponse,
    CreationOptionsResponse,
    CreationRequest,
    ReferenceDescribeResponse,
    VideoRequest,
)

router = APIRouter()


def _username(user) -> str:
    return getattr(user, "username", "") or ""


@router.get(
    "/creations/options",
    response_model=CreationOptionsResponse,
    summary="AI 创作参数选项",
)
async def creation_options(
    current_user: CurrentUser,
    svc: CreationServiceDep,
) -> dict:
    """模型 / 比例 / 风格 / 视频能力，一次拿全。

    参数面板的三组下拉必须**互相自洽**（例如视频不可用时整组置灰），
    分开取很容易出现一半可用一半不可用。
    """
    return svc.options()


@router.get(
    "/creations",
    response_model=CreationListResponse,
    summary="创作历史",
)
async def list_creations(
    current_user: CurrentUser,
    svc: CreationServiceDep,
    limit: int = 30,
) -> dict:
    return svc.list(_username(current_user), limit=max(1, min(limit, 100)))


@router.post(
    "/creations",
    response_model=CreationInfo,
    summary="生成图片",
)
async def create_creation(
    payload: CreationRequest,
    current_user: CurrentUser,
    svc: CreationServiceDep,
    _: None = Depends(rate_limit("creation")),
) -> dict:
    """同步等待出图（通常 10-40 秒）。

    没有做成两段式：单张图是一条短请求，多一层任务表只会多一处可以不同步的状态。
    视频不同 —— 1-3 分钟必然撞网关超时，所以走 `POST /creations/videos`。
    """
    return await svc.create(
        username=_username(current_user),
        prompt=payload.prompt,
        model=payload.model,
        ratio=payload.ratio,
        style=payload.style,
        reference_desc=payload.reference_desc,
    )


@router.post(
    "/creations/videos",
    response_model=CreationInfo,
    summary="提交文生视频任务",
)
async def create_video(
    payload: VideoRequest,
    current_user: CurrentUser,
    svc: CreationServiceDep,
    _: None = Depends(rate_limit("creation")),
) -> dict:
    """**立即返回**（status=running）。之后用 `GET /creations/{id}` 轮询进度。"""
    return await svc.create_video(
        username=_username(current_user),
        prompt=payload.prompt,
        model=payload.model,
        ratio=payload.ratio,
        style=payload.style,
        reference_desc=payload.reference_desc,
    )


async def _read_source_image(file: UploadFile) -> tuple[bytes, str]:
    """读取并校验上传的源图，返回 (字节, mime)。

    校验不放宽的理由：这张图会被 base64 编码后直接塞进上游请求体，
    放大到几十兆会让请求被网关在途中掐断 —— 报错还发生在别人的服务器上。
    """
    from app.utils.files import image_mime, validate_image_upload

    contents = await file.read()
    ext = validate_image_upload(file.filename or "source.png", len(contents))
    return contents, image_mime(ext)


@router.post(
    "/creations/edits",
    response_model=CreationInfo,
    summary="图生图（图像编辑）",
)
async def create_edit(
    current_user: CurrentUser,
    svc: CreationServiceDep,
    file: UploadFile = File(..., description="源图"),
    prompt: str = Form(...),
    model: str = Form(""),
    ratio: str = Form("1:1"),
    style: str = Form(""),
    reference_desc: str = Form(""),
    _: None = Depends(rate_limit("creation")),
) -> dict:
    """上传源图 + 编辑指令，同步返回结果。

    与 `POST /creations` 的分工：那条是「从零生成」，这条是「改一张已有图」。
    分成两个路由而不是给 JSON 接口加个可选文件字段，是因为 multipart 与 JSON
    的请求体形态完全不同，混在一起会让 OpenAPI 里的描述变成一锅粥。
    """
    contents, mime = await _read_source_image(file)
    return await svc.create(
        username=_username(current_user),
        prompt=prompt,
        model=model,
        ratio=ratio,
        style=style,
        reference_desc=reference_desc,
        source_image=contents,
        source_mime=mime,
    )


@router.post(
    "/creations/videos/from-image",
    response_model=CreationInfo,
    summary="图生视频",
)
async def create_video_from_image(
    current_user: CurrentUser,
    svc: CreationServiceDep,
    file: UploadFile = File(..., description="源图"),
    prompt: str = Form(""),
    model: str = Form(""),
    ratio: str = Form("16:9"),
    style: str = Form(""),
    reference_desc: str = Form(""),
    _: None = Depends(rate_limit("creation")),
) -> dict:
    contents, mime = await _read_source_image(file)
    return await svc.create_video(
        username=_username(current_user),
        prompt=prompt,
        model=model,
        ratio=ratio,
        style=style,
        reference_desc=reference_desc,
        source_image=contents,
        source_mime=mime,
    )


@router.post(
    "/creations/reference",
    response_model=ReferenceDescribeResponse,
    summary="读取参考图的画面描述",
)
async def describe_reference(
    current_user: CurrentUser,
    svc: CreationServiceDep,
    file: UploadFile = File(...),
    _: None = Depends(rate_limit("creation")),
) -> dict:
    contents = await file.read()
    return await svc.describe_reference(
        filename=file.filename or "reference.png", contents=contents
    )


@router.get(
    "/creations/{creation_id}",
    response_model=CreationInfo,
    summary="查询单个作品（视频进度）",
)
async def get_creation(
    creation_id: str,
    current_user: CurrentUser,
    svc: CreationServiceDep,
) -> dict:
    return await svc.get(_username(current_user), creation_id)


@router.get(
    "/creations/{creation_id}/file",
    summary="读取作品文件（图片 / 视频）",
    response_class=FileResponse,
)
async def get_creation_file(
    creation_id: str,
    current_user: CurrentUser,
    svc: CreationServiceDep,
):
    info = svc.get_media(_username(current_user), creation_id)
    return FileResponse(
        info["path"],
        media_type=info["mime"],
        filename=info["filename"],
        headers={"Cache-Control": "private, max-age=86400"},
    )


@router.get(
    "/creations/{creation_id}/image",
    summary="读取作品图片（兼容旧地址）",
    response_class=FileResponse,
)
async def get_creation_image(
    creation_id: str,
    current_user: CurrentUser,
    svc: CreationServiceDep,
):
    """等价于 `/creations/{id}/file`。

    保留是因为上一版前端已经在用这个地址；改名会让缓存里的旧页面直接裂图。
    """
    info = svc.get_media(_username(current_user), creation_id)
    return FileResponse(
        info["path"],
        media_type=info["mime"],
        filename=info["filename"],
        headers={"Cache-Control": "private, max-age=86400"},
    )


@router.delete(
    "/creations/{creation_id}",
    response_model=CreationDeleteResponse,
    summary="删除作品",
)
async def delete_creation(
    creation_id: str,
    current_user: CurrentUser,
    svc: CreationServiceDep,
) -> dict:
    return svc.delete(_username(current_user), creation_id)
