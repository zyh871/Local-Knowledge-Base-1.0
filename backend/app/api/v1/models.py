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
from app.schemas.model_catalog import CatalogModel, ModelCatalogResponse
from app.schemas.qa import ModelInfo, ModelListResponse

router = APIRouter()

#: 可完全离线运行的后端。
_OFFLINE_BACKENDS = ("local", "ollama", "mock")

#: 成本档位的启发式关键词（仅用于展示，不参与计费）。
_LOW_COST_HINTS = ("flash", "mini", "air", "turbo", "lite", "small", "3b", "0.5b", "1.5b")
_HIGH_COST_HINTS = ("plus", "pro", "max", "4o", "opus", "long")


def _cost_tier(model_id: str) -> str:
    """按模型名推断成本档位。

    刻意保持「启发式」而非精确计费：不同供应商的计价单位差异很大
    （按 token / 按张 / 按秒），精确数字只能来自账单；这里要的是
    用户在切换模型前能看出「这个会贵一些」。
    """
    name = (model_id or "").lower()
    if any(hint in name for hint in _LOW_COST_HINTS):
        return "low"
    if any(hint in name for hint in _HIGH_COST_HINTS):
        return "high"
    return "medium"


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
        thinking_enabled=bool(getattr(model_cfg, "thinking_enabled", False)),
        thinking_model=getattr(model_cfg, "thinking_model", "") or "",
        thinking_models=[
            m for m in model_cfg.available_llm_models
            if model_cfg.is_thinking_capable(m)
        ],
    )


@router.get(
    "/models/catalog",
    response_model=ModelCatalogResponse,
    summary="模型目录（本地/云端、离线可用、成本档位）",
)
async def model_catalog(
    current_user: CurrentUser,
    settings: SettingsDep,
    _: None = Depends(rate_limit("models")),
) -> ModelCatalogResponse:
    """治理视图：把「能不能离线、大概多贵、能否推理」摆到台面上。

    数据全部来自配置与 providers 注册表，**不发起任何上游请求** ——
    这个端点的用途是部署选型，不应该因为查一次目录就产生费用或延迟。
    """
    from app.rag.providers import list_llm_models

    model_cfg = settings.model
    backend = model_cfg.backend
    offline = backend in _OFFLINE_BACKENDS

    models: list[CatalogModel] = []
    custom_count = 0
    for item in list_llm_models():
        is_custom = bool(item.get("custom"))
        if is_custom:
            custom_count += 1
        models.append(
            CatalogModel(
                id=item["id"],
                label=item.get("label") or item["id"],
                kind="llm",
                source="custom" if is_custom else "builtin",
                provider=item.get("provider") or "",
                # 自定义端点的可达性无法从配置推断（可能指向 localhost，
                # 也可能是境外 API），因此保守标记为不可离线
                offline_capable=offline and not is_custom,
                cost_tier=_cost_tier(item["id"]),
                thinking_capable=model_cfg.is_thinking_capable(item["id"]),
            )
        )

    for image in model_cfg.available_image_models:
        models.append(
            CatalogModel(
                id=image["id"],
                label=image.get("label") or image["id"],
                kind="image",
                source="builtin",
                provider=backend,
                offline_capable=False,
                cost_tier="medium",
                thinking_capable=False,
            )
        )

    if offline:
        note = f"当前后端 {backend} 可离线运行，知识数据不会离开本机。"
    else:
        note = (
            f"当前后端 {backend} 为云端模型，问答内容会发送到上游；"
            "如需零出域请切换 MODEL_BACKEND=ollama 或 local。"
        )
    if custom_count:
        note += f" 另有 {custom_count} 个自定义端点，其数据流向取决于你填写的地址。"

    return ModelCatalogResponse(
        backend=backend,
        offline_capable=offline,
        default_model=model_cfg.resolved_llm_choice,
        models=models,
        note=note,
    )
