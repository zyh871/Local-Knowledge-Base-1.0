"""用户反馈路由。"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Query

from app.api.deps import FeedbackServiceDep
from app.core.deps import CurrentUser
from app.schemas.misc import FeedbackListResponse, FeedbackRequest, FeedbackStatsResponse

router = APIRouter()


@router.post("/feedback", summary="提交反馈")
async def submit_feedback(
    req: FeedbackRequest, current_user: CurrentUser, svc: FeedbackServiceDep
) -> dict:
    return svc.submit(
        message_id=req.message_id,
        conversation_id=req.conversation_id,
        username=current_user.username,
        rating=req.rating,
        comment=req.comment,
    )


@router.get(
    "/feedback/stats", response_model=FeedbackStatsResponse, summary="反馈聚合统计"
)
async def get_feedback_stats(current_user: CurrentUser, svc: FeedbackServiceDep) -> dict:
    return svc.stats()


@router.get("/feedback/list", response_model=FeedbackListResponse, summary="反馈列表")
async def list_feedback(
    current_user: CurrentUser,
    svc: FeedbackServiceDep,
    rating_filter: Optional[int] = Query(None, ge=1, le=5, description="按评分过滤"),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
) -> dict:
    return svc.list(rating=rating_filter, skip=skip, limit=limit)
