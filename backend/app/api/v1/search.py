"""
联网搜索设置路由。

存在的意义：把「联网搜索」的配置从「改 .env + 重启」变成「界面里填一次」。
详见 app/services/search_service.py 的说明。
"""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.core.deps import AdminUser, CurrentUser, rate_limit
from app.services import search_service

router = APIRouter()


class SearchProviderInfo(BaseModel):
    id: str
    name: str
    requires_key: bool
    docs: str
    hint: str
    free_tier: str
    configured: bool


class SearchConfigResponse(BaseModel):
    provider: str
    provider_name: str
    requires_key: bool
    configured: bool
    key_masked: str
    source: str
    providers: list[SearchProviderInfo]


class SearchConfigUpdate(BaseModel):
    provider: str = Field(..., description="供应商 id")
    api_key: str = Field("", description="API Key；免密钥供应商留空即可")


class SearchTestResponse(BaseModel):
    success: bool
    provider: str
    result_count: int = 0
    elapsed_ms: int = 0
    sample_title: str = ""
    error: str = ""


@router.get(
    "/search/config",
    response_model=SearchConfigResponse,
    summary="联网搜索配置",
)
async def get_search_config(
    current_user: CurrentUser,
    _: None = Depends(rate_limit("models")),
) -> dict[str, Any]:
    return search_service.current_config()


@router.put(
    "/search/config",
    response_model=SearchConfigResponse,
    summary="保存联网搜索配置（立即生效，无需重启）",
)
async def update_search_config(
    payload: SearchConfigUpdate,
    admin: AdminUser,
    _: None = Depends(rate_limit("models")),
) -> dict[str, Any]:
    """搜索配置是全局的（含 API Key），写入只允许部署管理员。"""
    try:
        return search_service.save_config(payload.provider, payload.api_key)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete(
    "/search/config",
    response_model=SearchConfigResponse,
    summary="清除联网搜索配置",
)
async def delete_search_config(
    admin: AdminUser,
    _: None = Depends(rate_limit("models")),
) -> dict[str, Any]:
    return search_service.clear_config()


@router.post(
    "/search/test",
    response_model=SearchTestResponse,
    summary="测试联网搜索是否可用",
)
async def test_search(
    admin: AdminUser,
    _: None = Depends(rate_limit("models")),
) -> dict[str, Any]:
    """拿当前配置真跑一次搜索 —— 填完 key 立刻知道对不对。"""
    import time

    from app.tools.web_search import search_async

    provider_id = search_service.current_provider_id()
    started = time.perf_counter()
    try:
        result = await search_async("智鲸 AI 本地知识库", max_results=3)
    except Exception as exc:  # noqa: BLE001
        return {
            "success": False,
            "provider": provider_id,
            "result_count": 0,
            "elapsed_ms": int((time.perf_counter() - started) * 1000),
            "sample_title": "",
            "error": str(exc),
        }

    elapsed = int((time.perf_counter() - started) * 1000)
    ok = bool(result.get("success"))
    results = result.get("results") or []
    return {
        "success": ok,
        "provider": provider_id,
        "result_count": len(results),
        "elapsed_ms": elapsed,
        "sample_title": (results[0].get("title", "") if results else ""),
        "error": "" if ok else str(result.get("error") or "搜索失败"),
    }
