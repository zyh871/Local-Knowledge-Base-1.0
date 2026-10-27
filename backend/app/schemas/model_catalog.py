"""模型目录（治理视图）契约。

与 `/models` 的区别：
  * `/models` —— 只回答「能切到哪些模型」，给选择器用
  * `/models/catalog` —— 额外回答「离线可用吗 / 成本档位多高 / 能不能推理 /
    来源是内置还是自定义」，给「合规选型」与「成本治理」用

两者并存而不是改造 `/models`：后者的响应结构是 v3 契约的一部分，
前端已经依赖；治理字段只应出现在新端点上。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class CatalogModel(BaseModel):
    id: str
    label: str = ""
    kind: Literal["llm", "image"] = "llm"
    #: builtin = 后端内置候选；custom = 用户自行接入的端点
    source: Literal["builtin", "custom"] = "builtin"
    provider: str = ""
    #: 该模型是否能在**完全无外网**的环境下工作
    offline_capable: bool = False
    cost_tier: Literal["low", "medium", "high"] = "medium"
    thinking_capable: bool = False


class ModelCatalogResponse(BaseModel):
    backend: str = ""
    #: 当前后端整体是否可离线
    offline_capable: bool = False
    default_model: str = ""
    models: list[CatalogModel] = Field(default_factory=list)
    #: 面向部署者的治理提示（数据是否出域、自定义端点是否开启）
    note: str = ""
