"""
Service 依赖装配。

放在 API 层而不是 core/deps.py —— core 不允许反向依赖 services
（见 docs/REFACTOR_*.md 第 5.1 节依赖矩阵）。

所有 service 都是**按请求构造**的轻量对象：内部只持有对单例（chain / settings /
redis）的引用，不建立新连接，因此无需缓存实例。
"""

from __future__ import annotations

from typing import Annotated, Any, Optional

from fastapi import Depends, Request

from app.core.config import Settings, get_settings
from app.core.deps import get_redis_optional, get_rag_chain_dep
from app.services.agent_service import AgentService
from app.services.conversation_service import ConversationService
from app.services.creation_service import CreationService
from app.services.document_service import DocumentService
from app.services.feedback_service import FeedbackService
from app.services.model_service import ModelService
from app.services.profile_service import ProfileService
from app.services.qa_service import QAService
from app.services.share_service import ShareService
from app.services.system_service import SystemService
from app.services.text_service import LongTextService
from app.services.workflow_service import WorkflowService

SettingsDep = Annotated[Settings, Depends(get_settings)]
ChainDep = Annotated[Any, Depends(get_rag_chain_dep)]
RedisDep = Annotated[Optional[Any], Depends(get_redis_optional)]


async def get_qa_service(
    request: Request,
    chain: ChainDep,
    redis: RedisDep,
    settings: SettingsDep,
) -> QAService:
    """注入 LangGraph 状态图（图是唯一的编排实现）。

    测试中用不带 lifespan 的 TestClient 时 `app.state.qa_graph` 不存在，
    此时 graph 为 None，QAService 会抛出明确错误而非静默走别的路径。
    """
    graph = getattr(request.app.state, "qa_graph", None)
    return QAService(chain=chain, settings=settings, redis=redis, graph=graph)


async def get_document_service(
    chain: ChainDep, redis: RedisDep, settings: SettingsDep
) -> DocumentService:
    return DocumentService(chain=chain, settings=settings, redis=redis)


async def get_conversation_service(
    redis: RedisDep, settings: SettingsDep
) -> ConversationService:
    return ConversationService(settings=settings, redis=redis)


async def get_share_service(redis: RedisDep, settings: SettingsDep) -> ShareService:
    return ShareService(settings=settings, redis=redis)


async def get_profile_service(redis: RedisDep, settings: SettingsDep) -> ProfileService:
    return ProfileService(settings=settings, redis=redis)


async def get_feedback_service(settings: SettingsDep) -> FeedbackService:
    return FeedbackService(settings=settings)


async def get_agent_service(settings: SettingsDep) -> AgentService:
    return AgentService(settings=settings)


async def get_system_service(
    request: Request, chain: ChainDep, redis: RedisDep, settings: SettingsDep
) -> SystemService:
    return SystemService(
        chain=chain,
        settings=settings,
        redis=redis,
        redis_manager=getattr(request.app.state, "redis_manager", None),
        graph_obj=getattr(request.app.state, "qa_graph", None),
    )


async def get_long_text_service(settings: SettingsDep) -> LongTextService:
    return LongTextService(settings=settings)


async def get_workflow_service(
    request: Request, chain: ChainDep, redis: RedisDep, settings: SettingsDep
) -> WorkflowService:
    """工作流 service。

    P1 阶段它不碰 LLM，P2 起 plan 相位需要模型与图，因此这里注入
    `chain` 与 `app.state.workflow_graph`。两者对工作区/任务/文件这些用例
    仍然不是必需的 —— 传 None 时服务会给出明确错误而非静默走别的路径。
    """
    return WorkflowService(
        settings=settings,
        chain=chain,
        graph=getattr(request.app.state, "workflow_graph", None),
        redis=redis,
    )


async def get_model_service(settings: SettingsDep) -> ModelService:
    return ModelService(settings=settings)


async def get_creation_service(settings: SettingsDep) -> CreationService:
    """AI 创作用例。

    不注入 chain / graph：文生图只走一次 HTTP，与检索、编排都无关。
    唯一的模型依赖（参考图的视觉描述）由 rag 层自己取。
    """
    return CreationService(settings=settings.model)


QAServiceDep = Annotated[QAService, Depends(get_qa_service)]
DocumentServiceDep = Annotated[DocumentService, Depends(get_document_service)]
ConversationServiceDep = Annotated[ConversationService, Depends(get_conversation_service)]
ShareServiceDep = Annotated[ShareService, Depends(get_share_service)]
ProfileServiceDep = Annotated[ProfileService, Depends(get_profile_service)]
FeedbackServiceDep = Annotated[FeedbackService, Depends(get_feedback_service)]
AgentServiceDep = Annotated[AgentService, Depends(get_agent_service)]
SystemServiceDep = Annotated[SystemService, Depends(get_system_service)]
LongTextServiceDep = Annotated[LongTextService, Depends(get_long_text_service)]
WorkflowServiceDep = Annotated[WorkflowService, Depends(get_workflow_service)]
ModelServiceDep = Annotated[ModelService, Depends(get_model_service)]
CreationServiceDep = Annotated[CreationService, Depends(get_creation_service)]
