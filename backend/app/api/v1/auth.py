"""认证路由。"""

from __future__ import annotations

from datetime import timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.security import OAuth2PasswordRequestForm

from app.core.deps import CurrentUser, rate_limit
from app.core.exceptions import UnauthorizedError
from app.core.logging import get_logger, get_trace_id
from app.core.security import authenticate_user, create_access_token
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

    from app.core.config import get_security_settings

    token = create_access_token(
        data={"sub": user.username},
        expires_delta=timedelta(
            minutes=get_security_settings().access_token_expire_minutes
        ),
    )
    logger.info(f"User {user.username} logged in successfully")
    return Token(access_token=token, token_type="bearer")


@router.get("/users/me/", response_model=User, summary="获取当前用户")
async def read_users_me(current_user: CurrentUser) -> User:
    return User(username=current_user.username, disabled=current_user.disabled)
