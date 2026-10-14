"""系统路由：健康检查、统计、示例问题。"""

from __future__ import annotations

from fastapi import APIRouter, Query

from app.api.deps import DocumentServiceDep, SystemServiceDep
from app.core.deps import CurrentUser

router = APIRouter()


@router.get("/health", summary="健康检查（含 Redis / LangGraph 状态）")
def health_check(svc: SystemServiceDep) -> dict:
    return svc.health()


@router.get("/stats", summary="系统统计")
async def get_stats(current_user: CurrentUser, svc: SystemServiceDep) -> dict:
    return svc.stats()


@router.get("/example-questions", summary="示例问题")
async def get_example_questions(
    current_user: CurrentUser,
    svc: DocumentServiceDep,
    limit: int = Query(5, ge=1, le=10),
) -> dict:
    return {"questions": svc.example_questions(limit=limit)}
