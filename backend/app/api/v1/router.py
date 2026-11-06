"""
v1 路由汇总。

⚠️ 不挂 `/api/v1` 前缀（`Settings.api_prefix` 默认为空）——
   接口路径必须与 v3.0.0 完全一致，前端零改动。
   `api/v1/` 仅作为代码组织目录。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.v1 import (
    admin_identity,
    agent,
    auth,
    community,
    conversations,
    creation,
    documents,
    feedback,
    model_endpoints,
    models,
    profile,
    qa,
    search,
    share,
    system,
    teams,
    workflow,
)
from app.core.deps import rate_limit

api_router = APIRouter()

# 全局默认限流：**业务**端点的兜底配额。
# identity 为「用户名，回退 IP」，计数落在 Redis（多 worker 下才准）。
_global_limit = [Depends(rate_limit("global"))]

# 探针走**独立**的宽松配额。
#
# ⚠️ 修复前 `system.router`（/health、/stats）也挂在 `_global_limit` 上，
# 于是探针与业务共用同一个 60 次/分钟计数：实测 10 并发×30 打 /health
# 得到 60 个 200 + 240 个 429，随后同窗口的 /documents、/conversations、
# /workflow/runs **300/300 全 429**。生产上这等于"监控把业务打死，
# 编排层再判定实例已死并重启"，雪崩。
_probe_limit = [Depends(rate_limit("probe"))]

api_router.include_router(auth.router, tags=["认证"], dependencies=_global_limit)
# 身份管理：部署管理员建号/邀请 + 公开邀请接受
api_router.include_router(
    admin_identity.router, tags=["身份"], dependencies=_global_limit
)
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
api_router.include_router(search.router, tags=["联网搜索"], dependencies=_global_limit)
# 自定义模型端点：让用户接入自己的 OpenAI 兼容服务
api_router.include_router(
    model_endpoints.router, tags=["模型端点"], dependencies=_global_limit
)
api_router.include_router(system.router, tags=["系统"], dependencies=_probe_limit)
# AI 创作（文生图）：独立命名空间 `/creations/*`
api_router.include_router(creation.router, tags=["创作"], dependencies=_global_limit)
# AI 工作流：独立命名空间 `/workflow/*`，既有端点一行未改
api_router.include_router(workflow.router, tags=["工作流"], dependencies=_global_limit)
# 团队空间与角色权限：独立命名空间 `/teams/*`（与 /workflow/workspaces 区分）
api_router.include_router(teams.router, tags=["团队空间"], dependencies=_global_limit)
# 开源社区：项目展示、贡献者与互动
api_router.include_router(
    community.router, tags=["社区"], dependencies=_global_limit
)
