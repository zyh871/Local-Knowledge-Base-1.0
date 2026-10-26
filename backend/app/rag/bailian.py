"""阿里云百炼（DashScope）图像 / 视频生成能力。

## 接口形状（实测）

两者都是**异步任务**：

    POST /api/v1/services/aigc/text2image/image-synthesis   (X-DashScope-Async: enable)
    POST /api/v1/services/aigc/video-generation/video-synthesis
        → {"output": {"task_id": "...", "task_status": "PENDING"}}

    GET  /api/v1/tasks/{task_id}
        → SUCCEEDED 时：图片在 `output.results[0].url`，视频在 `output.video_url`
        → FAILED 时：`output.code` / `output.message`（例如尺寸不在白名单）

## 两个必须记住的坑

1. **尺寸用 `*` 分隔，且是白名单制**。传 `1280*720` 给 `wan2.2-t2v-plus` 会
   直接 FAILED（"size must be in 1080*1920,1920*1080,..."），而不是自动纠正。
   映射表见 `app/services/creation_service.py::VIDEO_RATIOS`。

2. **产物 URL 带签名、会过期**（`Expires=`）。和智谱一样，必须立刻下载成字节，
   把 URL 存进历史等于给作品设定期自毁。

分层：本模块属 rag 层（对外部能力的封装），只依赖 core。
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any, Optional

import httpx

from app.core.config import get_bailian_settings
from app.core.exceptions import UpstreamLLMError, ValidationError
from app.core.logging import get_logger

logger = get_logger(__name__)

__all__ = [
    "BailianImage",
    "TaskState",
    "download",
    "enabled",
    "fetch_task",
    "generate_image",
    "submit_video",
    "to_dashscope_size",
]

#: 单张图片 / 单个视频的字节上限。视频 1080p 5 秒约 5-15 MB，
#: 给 200 MB 足够宽松，同时能挡住异常响应把磁盘写满。
MAX_IMAGE_BYTES = 30 * 1024 * 1024
MAX_VIDEO_BYTES = 200 * 1024 * 1024


def enabled() -> bool:
    return get_bailian_settings().enabled


def to_dashscope_size(size: str) -> str:
    """把内部统一用的 `1024x1024` 转成 DashScope 的 `1024*1024`。

    只做分隔符替换，**不改数值** —— 数值来自映射表，改数值会让比例失真。
    """
    return (size or "").lower().replace("×", "*").replace("x", "*").strip()


def _headers() -> dict[str, str]:
    settings = get_bailian_settings()
    if not settings.api_key:
        raise ValidationError(
            "未配置百炼 API Key（BAILIAN_API_KEY），无法调用通义万相生成能力"
        )
    return {
        "Authorization": f"Bearer {settings.api_key}",
        "Content-Type": "application/json",
    }


def _reason(body: Any, status_code: int) -> str:
    """从 DashScope 错误体里挖出人能读的原因。

    形态有三种（都见过）：
      * `{"code": "...", "message": "..."}`（400 参数错误）
      * `{"output": {"code": "...", "message": "..."}}`（任务 FAILED）
      * `{"error": {"code": "...", "message": "..."}}`（网关层）
    """
    if isinstance(body, dict):
        for holder in (body, body.get("output"), body.get("error")):
            if isinstance(holder, dict) and holder.get("message"):
                code = str(holder.get("code") or "").strip()
                message = str(holder["message"]).strip()
                # 上游的英文原文保留，但补一句中文，免得用户只看到英文
                return f"{message}（{code}）" if code else message
    return f"上游返回 HTTP {status_code}"


@dataclass
class BailianImage:
    data: bytes
    actual_prompt: str = ""


@dataclass
class TaskState:
    """一次任务轮询的结果。"""

    status: str  # PENDING / RUNNING / SUCCEEDED / FAILED / TIMEOUT
    url: str = ""
    actual_prompt: str = ""
    error: str = ""

    @property
    def done(self) -> bool:
        return self.status in ("SUCCEEDED", "FAILED", "CANCELED", "UNKNOWN", "TIMEOUT")


async def _submit(path: str, payload: dict, *, timeout: float) -> str:
    settings = get_bailian_settings()
    url = settings.base_url.rstrip("/") + path
    headers = {**_headers(), "X-DashScope-Async": "enable"}
    async with httpx.AsyncClient(timeout=timeout) as client:
        try:
            resp = await client.post(url, json=payload, headers=headers)
        except httpx.TimeoutException as exc:
            raise UpstreamLLMError("提交生成任务超时，请稍后重试") from exc
        except httpx.HTTPError as exc:
            raise UpstreamLLMError(f"无法连接百炼服务：{exc}") from exc

    try:
        body = resp.json()
    except ValueError:
        body = None

    if resp.status_code != 200:
        reason = _reason(body, resp.status_code)
        logger.warning(f"百炼任务提交失败: HTTP {resp.status_code} {reason}")
        raise UpstreamLLMError(f"生成任务提交失败：{reason}")

    task_id = ((body or {}).get("output") or {}).get("task_id")
    if not task_id:
        raise UpstreamLLMError("生成任务提交失败：上游未返回 task_id")
    logger.info(f"百炼任务已提交: task_id={task_id} model={payload.get('model')}")
    return str(task_id)


async def _poll(
    task_id: str, *, timeout: float, interval: float, want_video: bool
) -> TaskState:
    settings = get_bailian_settings()
    url = f"{settings.base_url.rstrip('/')}/api/v1/tasks/{task_id}"
    deadline = time.monotonic() + timeout
    status = "PENDING"

    async with httpx.AsyncClient(timeout=30.0) as client:
        while time.monotonic() < deadline:
            await asyncio.sleep(interval)
            try:
                resp = await client.get(url, headers=_headers())
                body = resp.json()
            except (httpx.HTTPError, ValueError) as exc:
                # 单次轮询失败不终止任务：网络抖动不该让一次已付费的生成白跑
                logger.warning(f"轮询任务 {task_id} 失败（将继续重试）: {exc}")
                continue

            if resp.status_code != 200:
                return TaskState(
                    status="FAILED", error=_reason(body, resp.status_code)
                )

            output = (body or {}).get("output") or {}
            status = str(output.get("task_status") or "RUNNING")

            if status == "SUCCEEDED":
                if want_video:
                    return TaskState(
                        status=status,
                        url=str(output.get("video_url") or ""),
                        actual_prompt=str(output.get("actual_prompt") or ""),
                    )
                results = output.get("results") or []
                first = results[0] if results and isinstance(results[0], dict) else {}
                return TaskState(
                    status=status,
                    url=str(first.get("url") or ""),
                    actual_prompt=str(first.get("actual_prompt") or ""),
                )

            if status in ("FAILED", "CANCELED", "UNKNOWN"):
                return TaskState(status=status, error=_reason(body, resp.status_code))

    return TaskState(status="TIMEOUT", error=f"生成超时（超过 {int(timeout)} 秒仍未完成）")


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


async def generate_image(
    prompt: str,
    *,
    model: Optional[str] = None,
    size: str = "1024x1024",
    timeout: Optional[float] = None,
) -> BailianImage:
    """文生图（同步等待）。"""
    settings = get_bailian_settings()
    model = model or settings.image_model
    timeout = timeout or settings.image_timeout_seconds

    payload = {
        "model": model,
        "input": {"prompt": prompt},
        "parameters": {
            "size": to_dashscope_size(size),
            "n": 1,
            # 让上游改写提示词：实测对构图与风格帮助很大，
            # 改写后的文本会随 actual_prompt 一起返回，用户看得到
            "prompt_extend": True,
        },
    }
    task_id = await _submit(
        "/api/v1/services/aigc/text2image/image-synthesis", payload, timeout=60.0
    )
    state = await _poll(
        task_id,
        timeout=timeout,
        interval=settings.poll_interval_seconds,
        want_video=False,
    )
    if state.status != "SUCCEEDED":
        raise UpstreamLLMError(f"图像生成失败：{state.error or state.status}")

    data = await download(state.url, max_bytes=MAX_IMAGE_BYTES)
    logger.info(
        f"百炼出图完成: model={model} size={payload['parameters']['size']} "
        f"{len(data) // 1024}KB"
    )
    return BailianImage(data=data, actual_prompt=state.actual_prompt)


async def submit_video(
    prompt: str,
    *,
    model: Optional[str] = None,
    size: str = "1920x1080",
    duration: int = 5,
) -> str:
    """提交视频任务，立即返回 task_id（视频要跑 1-3 分钟）。"""
    settings = get_bailian_settings()
    model = model or settings.video_model
    payload = {
        "model": model,
        "input": {"prompt": prompt},
        "parameters": {
            "size": to_dashscope_size(size),
            "duration": int(duration),
        },
    }
    return await _submit(
        "/api/v1/services/aigc/video-generation/video-synthesis",
        payload,
        timeout=60.0,
    )


async def fetch_task(task_id: str, *, timeout: float = 5.0) -> TaskState:
    """查询一次任务状态（供「按需轮询」使用，不阻塞线程）。"""
    settings = get_bailian_settings()
    url = f"{settings.base_url.rstrip('/')}/api/v1/tasks/{task_id}"
    async with httpx.AsyncClient(timeout=timeout) as client:
        try:
            resp = await client.get(url, headers=_headers())
            body = resp.json()
        except (httpx.HTTPError, ValueError) as exc:
            return TaskState(status="RUNNING", error=f"状态查询失败：{exc}")

    if resp.status_code != 200:
        return TaskState(status="FAILED", error=_reason(body, resp.status_code))

    output = (body or {}).get("output") or {}
    status = str(output.get("task_status") or "RUNNING")
    if status == "SUCCEEDED":
        return TaskState(
            status=status,
            url=str(output.get("video_url") or ""),
            actual_prompt=str(output.get("actual_prompt") or ""),
        )
    if status in ("FAILED", "CANCELED", "UNKNOWN"):
        return TaskState(status=status, error=_reason(body, resp.status_code))
    return TaskState(status=status)
