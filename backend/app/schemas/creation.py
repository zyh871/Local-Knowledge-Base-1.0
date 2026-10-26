"""AI 创作的请求与响应模型。

参数校验放在这里（长度、范围）而不是只靠前端：接口是公开的，
前端能拦住的只是「正常路径」，脚本拦不住。
"""

from __future__ import annotations

from pydantic import BaseModel, Field

__all__ = [
    "CreationDeleteResponse",
    "CreationInfo",
    "CreationListResponse",
    "CreationOptionsResponse",
    "CreationRequest",
    "MAX_PROMPT_CHARS",
    "ReferenceDescribeResponse",
    "VideoRequest",
]

#: 提示词上限。CogView / 通义万相实际能吃更长的提示词，但**超过 1000 字之后
#: 质量不再提升**，反而更容易出现互相矛盾的描述。按「有用」而不是「技术上限」定。
MAX_PROMPT_CHARS = 1000


class CreationRequest(BaseModel):
    prompt: str = Field(..., min_length=1, max_length=MAX_PROMPT_CHARS)
    #: 图像模型 id（见 /creations/options 的 models）
    model: str = Field("", max_length=120)
    #: 画幅比例，映射到供应商的具体像素尺寸
    ratio: str = Field("1:1", max_length=16)
    #: 风格 id（见 STYLE_PRESETS），空串 = 不加风格修饰
    style: str = Field("", max_length=32)
    #: 参考图的画面描述（由 `POST /creations/reference` 得到）
    reference_desc: str = Field("", max_length=400)


class VideoRequest(BaseModel):
    """视频请求。

    ⚠️ 没有 `duration` 字段：当前百炼按 5 秒计费，暴露一个只有 5 可选的下拉
    只会让用户以为能选。等上游支持多档再加。
    """

    prompt: str = Field(..., min_length=1, max_length=MAX_PROMPT_CHARS)
    model: str = Field("", max_length=120)
    ratio: str = Field("16:9", max_length=16)
    style: str = Field("", max_length=32)
    reference_desc: str = Field("", max_length=400)


class CreationInfo(BaseModel):
    id: str
    #: image / video
    kind: str = "image"
    #: running（视频任务进行中）/ done / failed
    status: str = "done"
    error: str = ""
    provider: str = ""
    prompt: str
    final_prompt: str = ""
    model: str
    size: str
    ratio: str = ""
    style: str = ""
    reference_desc: str = ""
    mime: str = "image/png"
    bytes: int = 0
    elapsed_ms: int = 0
    created_at: float = 0.0
    #: 产物地址（需要带鉴权头取；前端取成 blob 再显示）
    media_url: str = ""
    image_url: str = ""


class CreationListResponse(BaseModel):
    creations: list[CreationInfo] = Field(default_factory=list)
    #: 当前部署是否支持图像生成
    enabled: bool = True
    message: str = ""


class CreationOptionsResponse(BaseModel):
    enabled: bool = True
    message: str = ""
    models: list[dict] = Field(default_factory=list)
    default_model: str = ""
    #: 图生图 / 图生视频：模型清单里 `needs_image=true` 的那些，
    #: 前端据此显示「上传源图」入口（没有可用模型时整块不出现）
    i2i_enabled: bool = False
    i2i_models: list[dict] = Field(default_factory=list)
    i2v_enabled: bool = False
    i2v_models: list[dict] = Field(default_factory=list)
    ratios: list[dict] = Field(default_factory=list)
    styles: list[dict] = Field(default_factory=list)
    #: 视频能力由哪家供应商提供；未配置或不开放时 `video_enabled=false`，
    #: 界面据此把「视频」标成不可用，**并显示 video_message 说明原因**。
    video_enabled: bool = False
    video_message: str = ""
    video_models: list[dict] = Field(default_factory=list)
    default_video_model: str = ""
    video_ratios: list[dict] = Field(default_factory=list)
    video_duration_seconds: int = 5
    max_prompt_chars: int = MAX_PROMPT_CHARS


class CreationDeleteResponse(BaseModel):
    ok: bool = True
    id: str


class ReferenceDescribeResponse(BaseModel):
    description: str
    filename: str = ""
