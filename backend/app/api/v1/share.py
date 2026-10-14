"""
分享链接路由。

⚠️ 与 v3 的一处**有意收紧**：
    创建分享链接时新增了对话归属校验（`PermissionDeniedError` → 403）。
    v3 的实现只判断"对话是否存在"，任何已登录用户都能为他人对话生成分享链接。
    在单管理员部署下该差异不可见，多用户部署下则是一个越权读取漏洞。
    如需回退到旧行为，去掉 `conv_svc.get(...)` 的调用即可。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.deps import ConversationServiceDep, ShareServiceDep
from app.core.deps import CurrentUser, rate_limit
from app.core.exceptions import ValidationError
from app.schemas.conversation import ShareCreateRequest, ShareCreateResult

router = APIRouter()

_TOKEN_MIN_LEN = 8
_TOKEN_MAX_LEN = 128


@router.post(
    "/conversations/{conversation_id}/share",
    response_model=ShareCreateResult,
    summary="为对话创建只读分享链接",
)
async def create_share_link(
    conversation_id: str,
    req: ShareCreateRequest,
    current_user: CurrentUser,
    svc: ShareServiceDep,
    conv_svc: ConversationServiceDep,
) -> dict:
    # 归属校验（v4 新增，防越权）
    conv_svc.get(conversation_id=conversation_id, username=current_user.username)
    # 导出时同样带上归属校验
    payload = conv_svc.export(
        conversation_id=conversation_id, username=current_user.username
    )

    return await svc.create(
        conversation_id=conversation_id,
        username=current_user.username,
        title=payload.get("title") or "对话",
        payload=payload,
        messages=req.messages,
        ttl_hours=req.ttl_hours,
    )


@router.get("/share/{token}", summary="读取分享内容（公开，无需鉴权）")
async def get_shared_conversation(
    token: str,
    svc: ShareServiceDep,
    _: None = Depends(rate_limit("share_read")),
) -> dict:
    if not token or len(token) < _TOKEN_MIN_LEN or len(token) > _TOKEN_MAX_LEN:
        raise ValidationError("无效的分享令牌")
    return await svc.get(token)
