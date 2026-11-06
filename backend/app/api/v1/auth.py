"""认证路由。"""

from __future__ import annotations

from datetime import timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.security import OAuth2PasswordRequestForm

from app.core.deps import CurrentUser, rate_limit
from app.core.exceptions import UnauthorizedError
from app.core.logging import get_logger, get_trace_id
from app.core.security import authenticate_user, create_access_token, require_active
from app.schemas.auth import Token, User

logger = get_logger(__name__)
router = APIRouter()


@router.post("/token", response_model=Token, summary="获取访问令牌")
async def login_for_access_token(
    form_data: Annotated[OAuth2PasswordRequestForm, Depends()],
    request: Request,
    _: None = Depends(rate_limit("token", limit=5)),
) -> Token:
    user = authenticate_user(form_data.username, form_data.password)
    if not user:
        logger.warning(
            f"[{get_trace_id(request)}] 登录失败 username={form_data.username}"
        )
        raise UnauthorizedError("用户名或密码错误")

    # 口令对了也要看账号状态。
    # 修复前这里不校验，禁用的账号照样拿到 token（实测 200 + access_token），
    # 客户端会进入"已登录"状态再被后续接口连环 401 —— 表现为莫名其妙的掉线。
    require_active(user)

    from app.core.config import get_security_settings

    token = create_access_token(
        data={"sub": user.id or user.username, "username": user.username},
        expires_delta=timedelta(
            minutes=get_security_settings().access_token_expire_minutes
        ),
    )
    logger.info(f"User {user.username} logged in successfully")
    return Token(access_token=token, token_type="bearer")


@router.get("/users/me/", response_model=User, summary="获取当前用户")
async def read_users_me(current_user: CurrentUser) -> User:
    return User(
        id=current_user.id,
        username=current_user.username,
        display_name=current_user.display_name,
        global_role=current_user.global_role,
        disabled=current_user.disabled,
    )
