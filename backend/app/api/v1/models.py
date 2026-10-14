"""
模型清单接口 —— 供前端「模型切换」控件使用。

只读：列出**允许切换**的模型（白名单来自配置），不提供运行时增删。
真正的切换通过 `/ask` 与 `/ask/stream` 的 `model` 字段按请求生效，
因此切换是「无状态」的：不需要服务端存会话偏好，也不会影响其他用户。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.deps import SettingsDep
from app.core.deps import CurrentUser, rate_limit
from app.schemas.qa import ModelInfo, ModelListResponse

router = APIRouter()


@router.get("/models", response_model=ModelListResponse, summary="可选模型清单")
async def list_models(
    current_user: CurrentUser,
    settings: SettingsDep,
    _: None = Depends(rate_limit("models")),
) -> ModelListResponse:
    from app.rag.providers import list_llm_models

    model_cfg = settings.model
    return ModelListResponse(
        models=[ModelInfo(**item) for item in list_llm_models()],
        default_model=model_cfg.resolved_llm_choice,
        backend=model_cfg.backend,
    )
