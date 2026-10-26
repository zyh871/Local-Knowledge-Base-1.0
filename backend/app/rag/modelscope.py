"""ModelScope（魔搭）AIGC 能力：文生图 / 图生图 / 文生视频 / 图生视频。

## 接口形状（已用真实 token 实测）

    POST {base}/images/generations
         headers: X-ModelScope-Async-Mode: true
         body:    {model, prompt, negative_prompt?, size?, image_url?, ...}
         → {task_id}

    GET  {base}/tasks/{task_id}
         headers: X-ModelScope-Task-Type: image_generation
         → {task_status: RUNNING|SUCCEED|FAILED, output_images: [url]}

## 三个必须记住的坑

1. **终止状态是 `SUCCEED`，不是 `SUCCEEDED`**。只认 `SUCCEEDED` 会一直轮询下去，
   而任务成功后会被回收，最终拿到一个 "task not found" 的假失败。

2. **错误码 `40212` 表示「该模型没对当前账号开放 API」**，不是参数错误。
   实测：`Wan-AI/Wan2.1-T2V-14B` / `Wan2.2-T2V-A14B` / `Wan2.2-I2V-A14B` /
   `CogVideoX` 全部返回 40212，而 `Qwen/Qwen-Image`、`Qwen/Qwen-Image-Edit`
   可以正常出图。所以视频能力是**平台侧未开放**，不是代码问题 ——
   上层据此把视频入口关掉并说明原因，而不是让用户对着必然失败的按钮。

3. **本地图片要用 base64 data URL，且必须是列表**：
   `"image_url": ["data:image/png;base64,..."]`。传裸字符串是能过参数的，
   但编辑模型不一定读得到（文档给的示例就是列表形态）。

产物 URL 同样是临时链接（OSS 签名），必须下载成字节后由上层落盘。
"""

from __future__ import annotations

import asyncio
import base64
import time
from dataclasses import dataclass, field
from typing import Any, Optional

import httpx

from app.core.config import get_model_settings
from app.core.exceptions import UpstreamLLMError, ValidationError
from app.core.logging import get_logger

logger = get_logger(__name__)

__all__ = [
    "MsImage",
    "MsTask",
    "enabled",
    "fetch_task",
    "generate_image",
    "submit_image",
    "submit_video",
]

#: 单张图片 / 单个视频的字节上限
MAX_IMAGE_BYTES = 30 * 1024 * 1024
MAX_VIDEO_BYTES = 200 * 1024 * 1024

#: 成功终止状态。⚠️ 是 SUCCEED，不是 SUCCEEDED（见模块注释第 1 条）。
_OK_STATUS = ("SUCCEED", "SUCCEEDED")
_FAIL_STATUS = ("FAILED", "CANCELED", "UNKNOWN", "TIMEOUT")

#: 错误码 → 人话。40212 是最常见的那个：模型存在，但没对当前账号开 API。
_CODE_HINTS = {
    "40212": "该模型未对当前账号开放 API 调用（错误码 40212）",
    "401": "ModelScope token 无效或已过期",
}


def enabled() -> bool:
    return bool((get_model_settings().modelscope_api_key or "").strip())


def _headers(async_mode: bool = False) -> dict[str, str]:
    settings = get_model_settings()
    key = (settings.modelscope_api_key or "").strip()
    if not key:
        raise ValidationError("未配置 ModelScope token（MODELSCOPE_API_KEY），无法调用魔搭生成能力")
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }
    if async_mode:
        headers["X-ModelScope-Async-Mode"] = "true"
    return headers


def _base() -> str:
    return (get_model_settings().modelscope_base_url or "").rstrip("/")


def _reason(body: Any, status_code: int) -> str:
    """把上游错误体翻成人话。

    形态：`{"errors": {"message": "submit failed with code: 40212"}}`。
    错误信息本身是英文 + 一个数字码，直接抛给用户等于没说。
    """
    message = ""
    if isinstance(body, dict):
        errors = body.get("errors")
        if isinstance(errors, dict):
            message = str(errors.get("message") or "")
        elif isinstance(body.get("message"), str):
            message = body["message"]
    for code, hint in _CODE_HINTS.items():
        if code in message:
            return hint
    if "Model not exists" in message:
        return "模型不存在或已下架，请检查模型 ID"
    if message:
        return message
    return f"上游返回 HTTP {status_code}"


def _data_url(contents: bytes, mime: str = "image/png") -> str:
    return f"data:{mime};base64," + base64.b64encode(contents).decode("ascii")


@dataclass
class MsImage:
    data: bytes
    mime: str = "image/png"


@dataclass
class MsTask:
    status: str
    images: list[str] = field(default_factory=list)
    video_url: str = ""
    error: str = ""

    @property
    def done(self) -> bool:
        return self.status in _OK_STATUS or self.status in _FAIL_STATUS


async def _submit(payload: dict[str, Any], *, timeout: float = 60.0) -> str:
    url = f"{_base()}/images/generations"
    async with httpx.AsyncClient(timeout=timeout) as client:
        try:
            resp = await client.post(url, json=payload, headers=_headers(True))
        except httpx.HTTPError as exc:
            raise UpstreamLLMError(f"无法连接 ModelScope：{exc}") from exc

    try:
        body = resp.json()
    except ValueError:
        body = None

    if resp.status_code != 200:
        reason = _reason(body, resp.status_code)
        logger.warning(f"ModelScope 任务提交失败: {reason} model={payload.get('model')}")
        raise UpstreamLLMError(f"生成任务提交失败：{reason}")

    task_id = (body or {}).get("task_id")
    if not task_id:
        raise UpstreamLLMError("生成任务提交失败：上游未返回 task_id")
    logger.info(f"ModelScope 任务已提交: task_id={task_id} model={payload.get('model')}")
    return str(task_id)


async def _get_task(task_id: str, *, timeout: float = 30.0) -> Optional[dict]:
    url = f"{_base()}/tasks/{task_id}"
    headers = {**_headers(), "X-ModelScope-Task-Type": "image_generation"}
    async with httpx.AsyncClient(timeout=timeout) as client:
        try:
            resp = await client.get(url, headers=headers)
            return resp.json()
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning(f"查询 ModelScope 任务 {task_id} 失败: {exc}")
            return None


def _parse_task(body: Optional[dict], status_code: int = 200) -> MsTask:
    if not isinstance(body, dict):
        return MsTask(status="RUNNING")
    status = str(body.get("task_status") or "RUNNING").upper()
    if status in _OK_STATUS:
        images = [u for u in (body.get("output_images") or []) if isinstance(u, str)]
        video = body.get("output_video") or body.get("video_url") or ""
        if not video:
            videos = body.get("output_videos") or []
            if videos and isinstance(videos[0], str):
                video = videos[0]
        return MsTask(status=status, images=images, video_url=str(video))
    if status in _FAIL_STATUS:
        return MsTask(status=status, error=_reason(body, status_code) or status)
    return MsTask(status=status)


async def _wait(
    task_id: str, *, timeout: float, interval: float
) -> MsTask:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        await asyncio.sleep(interval)
        task = _parse_task(await _get_task(task_id))
        if task.done:
            return task
    return MsTask(status="TIMEOUT", error=f"生成超时（超过 {int(timeout)} 秒仍未完成）")


async def download(url: str, *, timeout: float = 120.0, max_bytes: int = MAX_IMAGE_BYTES) -> bytes:
    if not url:
        raise UpstreamLLMError("生成任务已完成，但上游没有返回文件地址")
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        try:
            resp = await client.get(url)
        except httpx.HTTPError as exc:
            raise UpstreamLLMError(f"结果文件下载失败：{exc}") from exc
    if resp.status_code != 200:
        raise UpstreamLLMError(f"结果文件下载失败（HTTP {resp.status_code}）")
    data = resp.content
    if not data:
        raise UpstreamLLMError("结果文件为空")
    if len(data) > max_bytes:
        raise UpstreamLLMError(f"结果文件超过 {max_bytes // (1024 * 1024)} MB，已拒绝保存")
    return data


def build_payload(
    *,
    model: str,
    prompt: str,
    size: Optional[str] = None,
    source_image: Optional[bytes] = None,
    source_mime: str = "image/png",
    negative_prompt: str = "",
    duration: Optional[int] = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {"model": model, "prompt": prompt}
    if negative_prompt:
        payload["negative_prompt"] = negative_prompt
    if size and not source_image:
        # 文档写的是 `1024x1024`（x 分隔）。图生图时尺寸跟源图走，
        # 硬塞一个 size 只会让上游把画面拉伸。
        payload["size"] = size
    if source_image:
        # ⚠️ 必须是列表，见模块注释第 3 条
        payload["image_url"] = [_data_url(source_image, source_mime)]
    if duration:
        payload["parameters"] = {"duration": int(duration)}
    return payload


async def generate_image(
    prompt: str,
    *,
    model: str,
    size: Optional[str] = None,
    source_image: Optional[bytes] = None,
    source_mime: str = "image/png",
    timeout: Optional[float] = None,
) -> MsImage:
    settings = get_model_settings()
    timeout = timeout or max(30.0, float(settings.image_timeout_seconds))
    task_id = await submit_image(
        prompt, model=model, size=size, source_image=source_image, source_mime=source_mime
    )
    task = await _wait(task_id, timeout=timeout, interval=3.0)
    if task.status not in _OK_STATUS:
        raise UpstreamLLMError(f"图像生成失败：{task.error or task.status}")
    if not task.images:
        raise UpstreamLLMError("图像生成完成，但上游没有返回图片地址")

    data = await download(task.images[0], max_bytes=MAX_IMAGE_BYTES)
    logger.info(f"ModelScope 出图完成: model={model} {len(data) // 1024}KB")
    return MsImage(data=data)


async def submit_image(
    prompt: str,
    *,
    model: str,
    size: Optional[str] = None,
    source_image: Optional[bytes] = None,
    source_mime: str = "image/png",
) -> str:
    """只提交、不等待。

    为什么需要它：ModelScope 的免费推理是**单并发排队**的，实测同一账号
    排队十几分钟才轮到是常事。把这段等待挂在用户请求上，网关会先超时，
    用户看到的是 502 而图片其实成了 —— 所以上层改成「提交 → 落 running 行 →
    前端轮询」，和视频走同一套。
    """
    payload = build_payload(
        model=model, prompt=prompt, size=size, source_image=source_image, source_mime=source_mime
    )
    return await _submit(payload)


async def submit_video(
    prompt: str,
    *,
    model: str,
    size: Optional[str] = None,
    source_image: Optional[bytes] = None,
    source_mime: str = "image/png",
    duration: int = 5,
) -> str:
    """提交视频任务。

    ⚠️ 当前 ModelScope API-Inference 对视频模型返回 40212（未开放），
    所以这条路径默认不会出现在界面上；保留实现是为了平台开放后
    只改一个开关即可启用，不必重写调用链。
    """
    payload = build_payload(
        model=model,
        prompt=prompt,
        size=size,
        source_image=source_image,
        source_mime=source_mime,
        duration=duration,
    )
    return await _submit(payload)


async def fetch_task(task_id: str) -> MsTask:
    """查询一次任务状态（供「按需轮询」使用）。"""
    return _parse_task(await _get_task(task_id))
