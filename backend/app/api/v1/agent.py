"""Agent 交互接口路由（供前端预判路由与展示能力）。"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.deps import AgentServiceDep
from app.core.deps import CurrentUser, rate_limit
from app.schemas.agent import AgentClassifyRequest, AgentClassifyResponse

router = APIRouter()


@router.get("/agent/intents", summary="系统支持的意图类型")
async def list_agent_intents(current_user: CurrentUser, svc: AgentServiceDep) -> dict:
    return svc.intents()


@router.post(
    "/agent/classify", response_model=AgentClassifyResponse, summary="意图分类（不执行回答）"
)
async def classify_question(
    req: AgentClassifyRequest,
    current_user: CurrentUser,
    svc: AgentServiceDep,
    _: None = Depends(rate_limit("agent_classify")),
) -> dict:
    return svc.classify(question=req.question, conversation_id=req.conversation_id)


@router.get("/agent/capabilities", summary="系统可用能力与当前模型")
async def get_agent_capabilities(
    current_user: CurrentUser, svc: AgentServiceDep
) -> dict:
    return svc.capabilities()
