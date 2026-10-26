"""生成能力注册表：把「模型 id」映射到「哪家供应商、怎么调、要不要源图」。

四类能力都在这里登记：

    t2i 文生图 · i2i 图生图 · t2v 文生视频 · i2v 图生视频

`needs_image=True` 的模型（i2i / i2v）必须由调用方提供一张源图；
缺少源图时直接报错，而不是让上游去猜「用空图生成」。

⚠️ 只登记**实测可调通**的模型。多列一个名字的代价不是「多一个选项」，
而是「用户选中它必然失败，且失败原因看起来像我们的 bug」。
当前 ModelScope 的视频模型就属于这种情况（模型存在，API 返回 40212），
所以它们默认不出现在清单里，由 `MODELSCOPE_VIDEO_ENABLED` 控制。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from app.core.config import get_model_settings
from app.core.exceptions import ValidationError
from app.core.logging import get_logger
from app.rag import bailian, image_gen, modelscope, zhipu_video

logger = get_logger(__name__)

__all__ = [
    "IMAGE_MODELS",
    "VIDEO_MODELS",
    "MediaResult",
    "VideoTask",
    "available_image_models",
    "available_video_models",
    "default_image_model",
    "default_video_model",
    "download_file",
    "fetch_image_task",
    "fetch_video_task",
    "generate_image",
    "model_kind",
    "needs_source_image",
    "provider_of",
    "submit_image_task",
    "supported_video_ratios",
    "submit_video",
]

#: 生成模型注册表。
#: 顺序 = 展示顺序，第一个可用者即默认值。
IMAGE_MODELS: tuple[dict[str, Any], ...] = (
    {
        "id": "Qwen/Qwen-Image",
        "label": "Qwen-Image",
        "provider": "modelscope",
        "category": "t2i",
        "needs_image": False,
        "hint": "ModelScope · 文生图",
    },
    {
        "id": "Qwen/Qwen-Image-Edit",
        "label": "Qwen-Image-Edit",
        "provider": "modelscope",
        "category": "i2i",
        "needs_image": True,
        "hint": "ModelScope · 图生图（需源图）",
    },
    {
        "id": "wanx2.1-t2i-turbo",
        "label": "通义万相 2.1 Turbo",
        "provider": "bailian",
        "category": "t2i",
        "needs_image": False,
        "hint": "阿里百炼 · 文生图",
    },
    {
        "id": "cogview-3-flash",
        "label": "CogView 3 Flash",
        "provider": "zhipu",
        "category": "t2i",
        "needs_image": False,
        "hint": "智谱 · 文生图",
    },
    {
        "id": "cogview-4-250304",
        "label": "CogView 4",
        "provider": "zhipu",
        "category": "t2i",
        "needs_image": False,
        "hint": "智谱 · 文生图",
    },
)

VIDEO_MODELS: tuple[dict[str, Any], ...] = (
    {
        "id": "cogvideox-3",
        "label": "CogVideoX-3",
        "provider": "zhipu",
        "category": "t2v",
        "needs_image": False,
        "hint": "智谱 · 文生视频（带音效）",
    },
    {
        "id": "Wan-AI/Wan2.2-T2V-A14B",
        "label": "Wan 2.2 文生视频",
        "provider": "modelscope",
        "category": "t2v",
        "needs_image": False,
        "hint": "ModelScope · 文生视频",
    },
    {
        "id": "Wan-AI/Wan2.2-I2V-A14B",
        "label": "Wan 2.2 图生视频",
        "provider": "modelscope",
        "category": "i2v",
        "needs_image": True,
        "hint": "ModelScope · 图生视频（需源图）",
    },
    {
        "id": "wan2.2-t2v-plus",
        "label": "通义万相 2.2 视频 Plus",
        "provider": "bailian",
        "category": "t2v",
        "needs_image": False,
        "hint": "阿里百炼 · 文生视频",
    },
)

#: 各供应商支持的视频比例。智谱只认 16:9 / 9:16 / 1:1 三种尺寸，
#: 把 4:3 摆在它面前只是给用户一个必然失败的选项。
_VIDEO_RATIO_IDS: dict[str, tuple[str, ...]] = {
    "zhipu": ("16:9", "9:16", "1:1"),
}

IMAGE_IDS = {str(item["id"]) for item in IMAGE_MODELS}
VIDEO_IDS = {str(item["id"]) for item in VIDEO_MODELS}


@dataclass
class MediaResult:
    """一次生成产出的媒体文件。"""

    data: bytes
    provider: str


@dataclass
class VideoTask:
    """统一的媒体任务状态（屏蔽各家字段差异）。图片与视频共用。"""

    status: str  # running / done / failed
    url: str = ""
    error: str = ""


def _allowed_providers(kind: str = "image") -> list[str]:
    settings = get_model_settings()
    raw = (settings.creation_providers or "").strip()
    if kind == "video":
        # 视频可以单独配置：图像供应商能出图不代表能出视频（反之亦然）
        raw = (settings.creation_video_providers or "").strip() or raw
    if not raw:
        return ["modelscope"]
    return [p.strip().lower() for p in raw.split(",") if p.strip()]


def _provider_ready(provider: str, *, kind: str) -> bool:
    settings = get_model_settings()
    if provider not in _allowed_providers(kind):
        return False
    if provider == "modelscope":
        if not modelscope.enabled():
            return False
        # 视频还要看平台是否给这个账号开了 API（实测默认没有）
        if kind == "video" and not bool(settings.modelscope_video_enabled):
            return False
        return True
    if provider == "bailian":
        return bailian.enabled()
    if provider == "zhipu":
        if kind == "video":
            # 视频走 /videos/generations，与聊天用的 MODEL_BACKEND 无关，
            # 只要配了智谱 key 就能用
            return zhipu_video.enabled()
        return settings.backend == "zhipu" and bool(settings.zhipu_api_key)
    return False


def available_image_models() -> list[dict]:
    return [dict(m) for m in IMAGE_MODELS if _provider_ready(str(m["provider"]), kind="image")]


def available_video_models() -> list[dict]:
    return [dict(m) for m in VIDEO_MODELS if _provider_ready(str(m["provider"]), kind="video")]


def default_image_model() -> str:
    models = available_image_models()
    return str(models[0]["id"]) if models else ""


def supported_video_ratios(provider: str) -> tuple[str, ...] | None:
    """该供应商支持的视频比例；None 表示不做限制。"""
    return _VIDEO_RATIO_IDS.get(provider)


def default_video_model() -> str:
    models = available_video_models()
    return str(models[0]["id"]) if models else ""


def _entry(model: str) -> dict[str, Any]:
    for item in (*IMAGE_MODELS, *VIDEO_MODELS):
        if item["id"] == model:
            return dict(item)
    raise ValidationError(f"未知的生成模型: {model}")


def model_kind(model: str) -> str:
    """"image" / "video"；未知模型返回空串。"""
    if model in VIDEO_IDS:
        return "video"
    if model in IMAGE_IDS:
        return "image"
    return ""


def provider_of(model: str) -> str:
    return str(_entry(model)["provider"])


def needs_source_image(model: str) -> bool:
    return bool(_entry(model).get("needs_image"))


async def generate_image(
    prompt: str,
    *,
    model: str,
    size: Optional[str] = None,
    source_image: Optional[bytes] = None,
    source_mime: str = "image/png",
    timeout: Optional[float] = None,
) -> MediaResult:
    """按模型分派到对应供应商，统一返回字节。"""
    if model not in IMAGE_IDS:
        raise ValidationError(f"不是图像模型: {model}")
    provider = provider_of(model)
    if not _provider_ready(provider, kind="image"):
        raise ValidationError(
            f"模型 {model} 所属供应商当前不可用（{provider}），"
            "请检查 .env 中的密钥与 CREATION_PROVIDERS 配置"
        )
    if needs_source_image(model) and not source_image:
        raise ValidationError(f"模型 {model} 是图生图，需要先上传一张源图")
    if not needs_source_image(model) and source_image:
        raise ValidationError(f"模型 {model} 不支持图生图，请改选 Qwen-Image-Edit")

    if provider == "modelscope":
        result = await modelscope.generate_image(
            prompt,
            model=model,
            size=size,
            source_image=source_image,
            source_mime=source_mime,
            timeout=timeout,
        )
        return MediaResult(data=result.data, provider=provider)

    if provider == "bailian":
        result = await bailian.generate_image(prompt, model=model, size=size or "1024x1024")
        return MediaResult(data=result.data, provider=provider)

    settings = get_model_settings()
    data = await image_gen.generate_image(
        prompt,
        model=model,
        size=size or "1024x1024",
        timeout=float(timeout or settings.image_timeout_seconds),
    )
    return MediaResult(data=data, provider=provider)


async def submit_image_task(
    prompt: str,
    *,
    model: str,
    size: Optional[str] = None,
    source_image: Optional[bytes] = None,
    source_mime: str = "image/png",
) -> tuple[str, str]:
    """提交图像任务（仅异步供应商支持），返回 (task_id, provider)。

    同步供应商（智谱 / 百炼）直接走 `generate_image()`，不需要这条路径。
    """
    if model not in IMAGE_IDS:
        raise ValidationError(f"不是图像模型: {model}")
    provider = provider_of(model)
    if provider != "modelscope":
        raise ValidationError(f"供应商 {provider} 不支持异步图像任务")
    if needs_source_image(model) and not source_image:
        raise ValidationError(f"模型 {model} 是图生图，需要先上传一张源图")

    task_id = await modelscope.submit_image(
        prompt, model=model, size=size, source_image=source_image, source_mime=source_mime
    )
    return task_id, provider


async def fetch_image_task(model: str, task_id: str) -> VideoTask:
    """查询一次图像任务状态。"""
    provider = provider_of(model)
    if provider != "modelscope":
        raise ValidationError(f"供应商 {provider} 不支持异步图像任务")

    task = await modelscope.fetch_task(task_id)
    if task.status in ("SUCCEED", "SUCCEEDED"):
        url = task.images[0] if task.images else ""
        return VideoTask(status="done" if url else "failed", url=url,
                         error="" if url else "上游没有返回图片地址")
    if task.status in ("FAILED", "CANCELED", "UNKNOWN", "TIMEOUT"):
        return VideoTask(status="failed", error=task.error or task.status)
    return VideoTask(status="running")


async def submit_video(
    prompt: str,
    *,
    model: str,
    size: Optional[str] = None,
    source_image: Optional[bytes] = None,
    source_mime: str = "image/png",
    duration: int = 5,
) -> tuple[str, str]:
    """提交视频任务，返回 (task_id, provider)。"""
    if model not in VIDEO_IDS:
        raise ValidationError(f"不是视频模型: {model}")
    provider = provider_of(model)
    if not _provider_ready(provider, kind="video"):
        raise ValidationError(
            f"视频模型 {model} 当前不可用。"
            "ModelScope 视频能力需平台开放（实测返回 40212），"
            "开放后可设 MODELSCOPE_VIDEO_ENABLED=true 启用"
        )
    if needs_source_image(model) and not source_image:
        raise ValidationError(f"模型 {model} 是图生视频，需要先上传一张源图")
    if not needs_source_image(model) and source_image:
        raise ValidationError(f"模型 {model} 不支持图生视频")

    if provider == "modelscope":
        task_id = await modelscope.submit_video(
            prompt,
            model=model,
            size=size,
            source_image=source_image,
            source_mime=source_mime,
            duration=duration,
        )
        return task_id, provider

    if provider == "zhipu":
        settings = get_model_settings()
        task_id = await zhipu_video.submit_video(
            prompt,
            model=model,
            size=size,
            quality=settings.zhipu_video_quality,
            with_audio=settings.zhipu_video_with_audio,
            source_image=source_image,
            source_mime=source_mime,
        )
        return task_id, provider

    task_id = await bailian.submit_video(
        prompt, model=model, size=size or "1920x1080", duration=duration
    )
    return task_id, provider


async def fetch_video_task(model: str, task_id: str) -> VideoTask:
    """查询一次视频任务，返回统一状态。"""
    provider = provider_of(model)
    if provider == "modelscope":
        task = await modelscope.fetch_task(task_id)
        if task.status in ("SUCCEED", "SUCCEEDED"):
            return VideoTask(status="done", url=task.video_url)
        if task.status in ("FAILED", "CANCELED", "UNKNOWN", "TIMEOUT"):
            return VideoTask(status="failed", error=task.error or task.status)
        return VideoTask(status="running")

    if provider == "zhipu":
        task = await zhipu_video.fetch_task(task_id)
        return VideoTask(status=task.status, url=task.url, error=task.error)

    state = await bailian.fetch_task(task_id)
    if state.status == "SUCCEEDED":
        return VideoTask(status="done", url=state.url)
    if state.status in ("FAILED", "CANCELED", "UNKNOWN", "TIMEOUT"):
        return VideoTask(status="failed", error=state.error or state.status)
    return VideoTask(status="running")


async def download_file(provider: str, url: str) -> bytes:
    """下载视频产物（按供应商各自的体积上限）。"""
    if provider == "modelscope":
        return await modelscope.download(
            url, timeout=180.0, max_bytes=modelscope.MAX_VIDEO_BYTES
        )
    if provider == "zhipu":
        return await zhipu_video.download(url, timeout=180.0,
                                          max_bytes=zhipu_video.MAX_VIDEO_BYTES)
    return await bailian.download(url, timeout=180.0, max_bytes=bailian.MAX_VIDEO_BYTES)


async def download_image(provider: str, url: str) -> bytes:
    """下载图片产物。上限比视频小一个数量级 —— 图片不该有十几兆。"""
    if provider == "modelscope":
        return await modelscope.download(url, max_bytes=modelscope.MAX_IMAGE_BYTES)
    return await bailian.download(url, max_bytes=bailian.MAX_IMAGE_BYTES)
