"""自定义模型端点的请求与响应模型。

⚠️ **响应里绝不包含 `api_key` 字段** —— 只给掩码（`api_key_masked`）与
「有没有填」（`has_api_key`）。把完整密钥回传给前端等于让它进日志、
进浏览器历史、进截图，而用户完全无从察觉。
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field

__all__ = [
    "ModelEndpointCreate",
    "ModelEndpointDeleteResponse",
    "ModelEndpointInfo",
    "ModelEndpointListResponse",
    "ModelEndpointTestInline",
    "ModelEndpointTestInlineResult",
    "ModelEndpointUpdate",
]


class ModelEndpointInfo(BaseModel):
    id: str
    name: str
    base_url: str
    model: str
    enabled: bool = True
    #: 在模型选择器里使用的 id（形如 `custom:me_xxx`）
    model_id: str = ""
    #: 掩码后的密钥，例如 `…abcd`
    api_key_masked: str = ""
    has_api_key: bool = False
    last_test_at: Optional[float] = None
    last_test_ok: Optional[bool] = None
    last_test_error: Optional[str] = None
    created_at: float = 0.0
    updated_at: float = 0.0


class ModelEndpointListResponse(BaseModel):
    endpoints: list[ModelEndpointInfo] = Field(default_factory=list)
    #: 当前部署是否允许配置（对应 ALLOW_CUSTOM_MODELS）
    enabled: bool = True


class ModelEndpointCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=64, description="显示名称")
    base_url: str = Field(..., min_length=1, max_length=500, description="接口地址")
    api_key: str = Field(..., min_length=1, max_length=500, description="API 密钥")
    model: str = Field(..., min_length=1, max_length=200, description="模型名称")
    enabled: bool = True


class ModelEndpointUpdate(BaseModel):
    """局部更新。`api_key` 留空表示保持原值。"""

    name: Optional[str] = Field(None, max_length=64)
    base_url: Optional[str] = Field(None, max_length=500)
    api_key: Optional[str] = Field(None, max_length=500)
    model: Optional[str] = Field(None, max_length=200)
    enabled: Optional[bool] = None


class ModelEndpointTestInline(BaseModel):
    """保存前先测一次，参数直接来自表单（不落库）。"""

    name: str = Field("", max_length=64)
    base_url: str = Field(..., min_length=1, max_length=500)
    api_key: str = Field(..., min_length=1, max_length=500)
    model: str = Field(..., min_length=1, max_length=200)


class ModelEndpointTestInlineResult(BaseModel):
    ok: bool = False
    #: 失败原因（已翻译成可操作的中文提示）
    error: str = ""
    name: str = ""


class ModelEndpointDeleteResponse(BaseModel):
    success: bool = True
    id: str = ""
    name: str = ""
