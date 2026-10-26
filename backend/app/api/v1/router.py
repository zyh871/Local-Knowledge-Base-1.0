"""
v1 路由汇总。

⚠️ 不挂 `/api/v1` 前缀（`Settings.api_prefix` 默认为空）——
   接口路径必须与 v3.0.0 完全一致，前端零改动。
   `api/v1/` 仅作为代码组织目录。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.v1 import (
    agent,
    auth,
    conversations,
    creation,
    documents,
    feedback,
    model_endpoints,
    models,
    profile,
    qa,
    share,
    system,
    workflow,
)
from app.core.deps import rate_limit

api_router = APIRouter()

# 全局默认限流：未单独声明 scope 的端点（如 /health、/stats）都受此约束。
# identity 为「用户名，回退 IP」，计数落在 Redis（多 worker 下才准）。
_global_limit = [Depends(rate_limit("global"))]

api_router.include_router(auth.router, tags=["认证"], dependencies=_global_limit)
api_router.include_router(qa.router, tags=["问答"], dependencies=_global_limit)
api_router.include_router(
    documents.router, tags=["文档"], dependencies=_global_limit
)
api_router.include_router(
    conversations.router, tags=["对话"], dependencies=_global_limit
)
api_router.include_router(share.router, tags=["分享"], dependencies=_global_limit)
api_router.include_router(profile.router, tags=["用户画像"], dependencies=_global_limit)
api_router.include_router(feedback.router, tags=["反馈"], dependencies=_global_limit)
api_router.include_router(agent.router, tags=["Agent"], dependencies=_global_limit)
api_router.include_router(models.router, tags=["模型"], dependencies=_global_limit)
# 自定义模型端点：让用户接入自己的 OpenAI 兼容服务
api_router.include_router(
    model_endpoints.router, tags=["模型端点"], dependencies=_global_limit
)
api_router.include_router(system.router, tags=["系统"], dependencies=_global_limit)
# AI 创作（文生图）：独立命名空间 `/creations/*`
api_router.include_router(creation.router, tags=["创作"], dependencies=_global_limit)
# AI 工作流：独立命名空间 `/workflow/*`，既有端点一行未改
api_router.include_router(workflow.router, tags=["工作流"], dependencies=_global_limit)
