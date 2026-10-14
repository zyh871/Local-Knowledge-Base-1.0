"""用户画像与长文本工具路由。"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.deps import LongTextServiceDep, ProfileServiceDep
from app.core.deps import CurrentUser, rate_limit
from app.schemas.misc import (
    LongTextRequest,
    LongTextResponse,
    ProfileResponse,
    UserProfileUpdate,
)

router = APIRouter()


@router.get("/profile", summary="获取用户画像")
async def get_profile_endpoint(current_user: CurrentUser, svc: ProfileServiceDep) -> dict:
    return svc.get(current_user.username)


@router.put("/profile", response_model=ProfileResponse, summary="更新用户画像")
async def update_profile_endpoint(
    update: UserProfileUpdate, current_user: CurrentUser, svc: ProfileServiceDep
) -> dict:
    return svc.update(current_user.username, update.model_dump(exclude_unset=True))


@router.post("/remember-turn", response_model=ProfileResponse, summary="从一轮对话学习用户信息")
async def remember_turn_endpoint(
    text: str, current_user: CurrentUser, svc: ProfileServiceDep
) -> dict:
    """`text` 为 query 参数（与 v3 保持一致，非请求体）。"""
    return svc.remember_turn(current_user.username, text)


@router.post("/long-text", response_model=LongTextResponse, summary="长文本摘要/翻译/问答")
async def long_text_tool(
    req: LongTextRequest,
    current_user: CurrentUser,
    svc: LongTextServiceDep,
    _: None = Depends(rate_limit("long_text")),
) -> dict:
    return svc.run(
        text=req.text,
        operation=req.operation,
        target_language=req.target_language,
        question=req.question,
        max_chars=req.max_chars,
    )
