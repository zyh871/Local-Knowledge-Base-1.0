"""自定义模型端点路由。

用户可以接入任意 OpenAI 兼容的第三方服务，并在模型选择器里切换使用。

⚠️ 安全边界（务必清楚）：
  * 响应**只回掩码密钥**，完整 `api_key` 永不出库
  * 允许填任意 `base_url` 意味着服务器会携密钥向该地址发请求。
    这是「本机单用户」部署下的有意设计；多人部署应设 `ALLOW_CUSTOM_MODELS=false`
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.deps import ModelServiceDep
from app.core.deps import CurrentUser, rate_limit
from app.schemas.model_endpoint import (
    ModelEndpointCreate,
    ModelEndpointDeleteResponse,
    ModelEndpointInfo,
    ModelEndpointListResponse,
    ModelEndpointTestInline,
    ModelEndpointTestInlineResult,
    ModelEndpointUpdate,
)

router = APIRouter()


@router.get(
    "/model-endpoints",
    response_model=ModelEndpointListResponse,
    summary="自定义模型端点列表",
)
async def list_model_endpoints(
    current_user: CurrentUser,
    svc: ModelServiceDep,
    _: None = Depends(rate_limit("models")),
) -> dict:
    return svc.list_endpoints()


@router.post(
    "/model-endpoints",
    response_model=ModelEndpointInfo,
    summary="添加自定义模型端点",
)
async def create_model_endpoint(
    payload: ModelEndpointCreate,
    current_user: CurrentUser,
    svc: ModelServiceDep,
    _: None = Depends(rate_limit("models")),
) -> dict:
    return svc.create(
        name=payload.name,
        base_url=payload.base_url,
        api_key=payload.api_key,
        model=payload.model,
        username=getattr(current_user, "username", "") or "",
        enabled=payload.enabled,
    )


@router.post(
    "/model-endpoints/test-connection",
    response_model=ModelEndpointTestInlineResult,
    summary="保存前测试连通性（不落库）",
)
async def test_model_endpoint_inline(
    payload: ModelEndpointTestInline,
    current_user: CurrentUser,
    svc: ModelServiceDep,
    _: None = Depends(rate_limit("models")),
) -> dict:
    return svc.test_inline(
        name=payload.name,
        base_url=payload.base_url,
        api_key=payload.api_key,
        model=payload.model,
    )


@router.patch(
    "/model-endpoints/{endpoint_id}",
    response_model=ModelEndpointInfo,
    summary="修改自定义模型端点",
)
async def update_model_endpoint(
    endpoint_id: str,
    payload: ModelEndpointUpdate,
    current_user: CurrentUser,
    svc: ModelServiceDep,
    _: None = Depends(rate_limit("models")),
) -> dict:
    """局部更新。`api_key` 留空表示保持原值 —— 改个名字不必重贴一遍密钥。"""
    return svc.update(endpoint_id, **payload.model_dump(exclude_unset=True))


@router.delete(
    "/model-endpoints/{endpoint_id}",
    response_model=ModelEndpointDeleteResponse,
    summary="删除自定义模型端点",
)
async def delete_model_endpoint(
    endpoint_id: str,
    current_user: CurrentUser,
    svc: ModelServiceDep,
    _: None = Depends(rate_limit("models")),
) -> dict:
    return svc.delete(endpoint_id)


@router.post(
    "/model-endpoints/{endpoint_id}/test",
    response_model=ModelEndpointInfo,
    summary="测试已保存端点的连通性",
)
async def test_model_endpoint(
    endpoint_id: str,
    current_user: CurrentUser,
    svc: ModelServiceDep,
    _: None = Depends(rate_limit("models")),
) -> dict:
    return svc.test_endpoint(endpoint_id)
