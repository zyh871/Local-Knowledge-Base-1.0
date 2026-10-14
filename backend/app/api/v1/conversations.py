"""对话管理路由。"""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Body, Query

from app.api.deps import ConversationServiceDep
from app.core.deps import CurrentUser
from app.schemas.conversation import (
    BatchDeleteRequest,
    ConversationDetailResponse,
    ConversationListResponse,
    ConversationUpdate,
)

router = APIRouter()


@router.get(
    "/conversations", response_model=ConversationListResponse, summary="对话列表"
)
async def list_conversations_endpoint(
    current_user: CurrentUser,
    svc: ConversationServiceDep,
    search: Optional[str] = Query(None, description="搜索关键词"),
    include_archived: bool = Query(False, description="包含已归档对话"),
    skip: int = Query(0, ge=0, description="跳过条目数"),
    limit: int = Query(100, ge=1, le=500, description="每页条数"),
) -> dict:
    return svc.list(
        username=current_user.username,
        search=search,
        include_archived=include_archived,
        skip=skip,
        limit=limit,
    )


@router.get(
    "/conversations/{conversation_id}",
    response_model=ConversationDetailResponse,
    summary="对话详情",
)
async def get_conversation_endpoint(
    conversation_id: str, current_user: CurrentUser, svc: ConversationServiceDep
) -> dict:
    return svc.get(conversation_id=conversation_id, username=current_user.username)


@router.put(
    "/conversations/{conversation_id}",
    response_model=ConversationDetailResponse,
    summary="更新对话（重命名/置顶/锁定/归档）",
)
async def update_conversation_endpoint(
    conversation_id: str,
    update: ConversationUpdate,
    current_user: CurrentUser,
    svc: ConversationServiceDep,
) -> dict:
    return svc.update(
        conversation_id=conversation_id,
        username=current_user.username,
        patch=update.model_dump(exclude_unset=True),
    )


@router.delete("/conversations/{conversation_id}", summary="删除对话")
async def delete_conversation_endpoint(
    conversation_id: str, current_user: CurrentUser, svc: ConversationServiceDep
) -> dict:
    return svc.delete(conversation_id=conversation_id, username=current_user.username)


@router.post("/conversations/batch-delete", summary="批量删除对话")
async def batch_delete_conversations(
    req: BatchDeleteRequest, current_user: CurrentUser, svc: ConversationServiceDep
) -> dict:
    return svc.batch_delete(
        conversation_ids=req.conversation_ids, username=current_user.username
    )


@router.post(
    "/conversations/{conversation_id}/export", summary="导出对话"
)
async def export_conversation_endpoint(
    conversation_id: str, current_user: CurrentUser, svc: ConversationServiceDep
) -> dict[str, Any]:
    return svc.export(conversation_id=conversation_id, username=current_user.username)


@router.post("/conversations/{conversation_id}/clear", summary="清空对话记忆")
async def clear_conversation_endpoint(
    conversation_id: str, current_user: CurrentUser, svc: ConversationServiceDep
) -> dict:
    return svc.clear(conversation_id=conversation_id, username=current_user.username)
