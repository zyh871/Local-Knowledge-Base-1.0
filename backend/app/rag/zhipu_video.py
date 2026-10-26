"""智谱 CogVideoX 视频生成（异步任务）。

## 接口形状（已用真实 key 实测）

    POST {base}/videos/generations
         {model, prompt, quality?, size?, with_audio?, fps?, image_url?}
         → {"id": "...", "task_status": "PROCESSING"}

    GET  {base}/async-result/{id}
         → {"task_status": "PROCESSING|SUCCESS|FAIL", "video_result": [{"url", "cover_image_url"}]}

## 需要注意的点

1. **这是"提交-查询"两段式**，提交返回的 `id` 就是任务 id（同时也是 request_id）。
   与 ModelScope 不同，这里没有 `X-*-Async-Mode` 头，靠路径区分。

2. **`video_result[0].url` 是临时链接**，必须下载落盘 —— 智谱的文件一般保留 30 天，
   而我们的历史记录是长期留存的。

3. 常见错误码：`1001` 鉴权失败、`1113` 余额不足、`1305` 触发限流/并发上限、
   `1211` 模型不存在。这些直接抛给用户等于没说，这里翻成人话。

4. `quality` 只有 `quality` / `speed` 两档；`with_audio` 打开后视频自带音效
   （CogVideoX-3 的特性），体积也会更大。
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any, Optional

import httpx

from app.core.config import get_model_settings
from app.core.exceptions import UpstreamLLMError, ValidationError
from app.core.logging import get_logger

logger = get_logger(__name__)

__all__ = [
    "MAX_VIDEO_BYTES",
    "ZhipuTask",
    "build_payload",
    "download",
    "enabled",
    "fetch_task",
    "submit_video",
]

#: 单个视频的体积上限。CogVideoX-3 带音效的 1080p 约 10-30MB，200MB 已经很宽松。
MAX_VIDEO_BYTES = 200 * 1024 * 1024

#: 智谱允许的分辨率（官方枚举，传别的会被 400 拒掉）
ALLOWED_SIZES = (
    "1280x720",
    "720x1280",
    "1024x1024",
    "1920x1080",
    "1080x1920",
    "2048x1080",
    "3840x2160",
)

#: 提示词上限：智谱文档写的是 512 字符，超了直接 400
MAX_PROMPT_CHARS = 512


def _fit_size(size: Optional[str]) -> Optional[str]:
    """把外部尺寸收敛到智谱支持的枚举。

    我们的比例表里有 4:3 / 3:4（为百炼准备的），智谱并不支持这两个尺寸。
    与其把 400 丢给用户（错误信息还是「参数不合法」），不如按长宽比挑一个
    最接近的官方尺寸 —— 比例表本身也做了对应裁剪，正常路径不会走到这里。
    """
    if not size:
        return None
    if size in ALLOWED_SIZES:
        return size
    try:
        w, h = (int(x) for x in size.lower().replace("*", "x").split("x"))
    except (ValueError, AttributeError):
        logger.warning(f"无法解析尺寸 {size}，改用默认尺寸")
        return None
    ratio = w / h
    if ratio > 1.5:
        return "1920x1080"
    if ratio > 1.15:
        return "1920x1080"
    if ratio > 0.87:
        return "1024x1024"
    if ratio > 0.66:
        return "1080x1920"
    return "1080x1920"

#: 智谱错误码 → 人话
_CODE_HINTS = {
    "1001": "鉴权失败：ZHIPU_API_KEY 无效或已过期",
    "1002": "鉴权失败：缺少 API Key",
    "1113": "账户余额不足，视频生成会按次计费",
    "1211": "模型不存在或未开通",
    "1305": "触发并发/频率限制，请稍后重试",
    "1301": "内容安全审核未通过",
}


def _settings():
    return get_model_settings()


def enabled() -> bool:
    return bool((_settings().zhipu_api_key or "").strip())


def _headers() -> dict[str, str]:
    settings = _settings()
    key = (settings.zhipu_api_key or "").strip()
    if not key:
        raise ValidationError("未配置智谱 API Key（ZHIPU_API_KEY），无法调用视频生成")
    return {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}


def _base() -> str:
    return (_settings().zhipu_base_url or "https://open.bigmodel.cn/api/paas/v4").rstrip("/")


def _reason(body: Any, status_code: int) -> str:
    code = ""
    message = ""
    if isinstance(body, dict):
        err = body.get("error")
        if isinstance(err, dict):
            code = str(err.get("code") or "")
            message = str(err.get("message") or "")
        code = code or str(body.get("code") or "")
        message = message or str(body.get("message") or "")
    if code in _CODE_HINTS:
        return _CODE_HINTS[code]
    if message:
        return message
    return f"上游返回 HTTP {status_code}"


@dataclass
class ZhipuTask:
    status: str  # running / done / failed
    url: str = ""
    cover: str = ""
    error: str = ""


def build_payload(
    prompt: str,
    *,
    model: str,
    size: Optional[str] = None,
    quality: str = "quality",
    with_audio: bool = True,
    source_image: Optional[bytes] = None,
    source_mime: str = "image/png",
) -> dict[str, Any]:
    """组装请求体。

    单独抽出来是为了能断言「哪些参数会被发出去」——
    这类参数的错误（尺寸不在枚举、提示词超长）在真实调用里表现为 400，
    而 400 的原文通常只说「参数不合法」，排查成本远高于一张断言。
    """
    text = (prompt or "").strip()
    if len(text) > MAX_PROMPT_CHARS:
        logger.warning(f"提示词 {len(text)} 字超过智谱上限，已截断到 {MAX_PROMPT_CHARS}")
        text = text[:MAX_PROMPT_CHARS]

    payload: dict[str, Any] = {"model": model, "prompt": text}
    if quality:
        payload["quality"] = quality
    fitted = _fit_size(size)
    if fitted:
        payload["size"] = fitted
    if with_audio:
        payload["with_audio"] = True
    if source_image:
        # 图生视频：智谱接受 base64 或公网 URL
        import base64 as _b64

        payload["image_url"] = (
            f"data:{source_mime};base64," + _b64.b64encode(source_image).decode("ascii")
        )
    return payload


async def submit_video(
    prompt: str,
    *,
    model: str,
    size: Optional[str] = None,
    quality: str = "quality",
    with_audio: bool = True,
    source_image: Optional[bytes] = None,
    source_mime: str = "image/png",
) -> str:
    """提交视频任务，返回任务 id。"""

    payload = build_payload(
        prompt,
        model=model,
        size=size,
        quality=quality,
        with_audio=with_audio,
        source_image=source_image,
        source_mime=source_mime,
    )

    url = f"{_base()}/videos/generations"
    async with httpx.AsyncClient(timeout=60.0) as client:
        try:
            resp = await client.post(url, json=payload, headers=_headers())
        except httpx.HTTPError as exc:
            raise UpstreamLLMError(f"无法连接智谱开放平台：{exc}") from exc

    try:
        body = resp.json()
    except ValueError:
        body = None
    if resp.status_code != 200:
        reason = _reason(body, resp.status_code)
        logger.warning(f"智谱视频任务提交失败: {reason}")
        raise UpstreamLLMError(f"视频任务提交失败：{reason}")

    task_id = (body or {}).get("id") or (body or {}).get("request_id")
    if not task_id:
        raise UpstreamLLMError("视频任务提交失败：上游未返回任务 id")
    logger.info(f"智谱视频任务已提交: id={task_id} model={model} size={size or '默认'}")
    return str(task_id)


async def fetch_task(task_id: str) -> ZhipuTask:
    """查询一次任务状态。"""
    url = f"{_base()}/async-result/{task_id}"
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            resp = await client.get(url, headers=_headers())
        except httpx.HTTPError as exc:
            logger.warning(f"查询智谱任务 {task_id} 失败: {exc}")
            return ZhipuTask(status="running")

    try:
        body = resp.json()
    except ValueError:
        return ZhipuTask(status="running")

    if resp.status_code != 200:
        return ZhipuTask(status="failed", error=_reason(body, resp.status_code))

    status = str((body or {}).get("task_status") or "").upper()
    if status == "SUCCESS":
        results = (body or {}).get("video_result") or []
        first = results[0] if results and isinstance(results[0], dict) else {}
        video_url = str(first.get("url") or "")
        if not video_url:
            return ZhipuTask(status="failed", error="任务成功但未返回视频地址")
        return ZhipuTask(
            status="done", url=video_url, cover=str(first.get("cover_image_url") or "")
        )
    if status in ("FAIL", "FAILED", "CANCELED"):
        return ZhipuTask(status="failed", error=_reason(body, resp.status_code) or "生成失败")
    return ZhipuTask(status="running")


async def download(url: str, *, timeout: float = 180.0, max_bytes: int = MAX_VIDEO_BYTES) -> bytes:
    if not url:
        raise UpstreamLLMError("任务已完成，但上游没有返回文件地址")
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        try:
            resp = await client.get(url)
        except httpx.HTTPError as exc:
            raise UpstreamLLMError(f"视频下载失败：{exc}") from exc
    if resp.status_code != 200:
        raise UpstreamLLMError(f"视频下载失败（HTTP {resp.status_code}）")
    data = resp.content
    if not data:
        raise UpstreamLLMError("视频文件为空")
    if len(data) > max_bytes:
        raise UpstreamLLMError(f"视频超过 {max_bytes // (1024 * 1024)} MB，已拒绝保存")
    return data


async def wait_for(task_id: str, *, timeout: float = 600.0, interval: float = 6.0) -> ZhipuTask:
    """同步等待（仅用于脚本/调试；服务端走「按需轮询」）。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        await asyncio.sleep(interval)
        task = await fetch_task(task_id)
        if task.status != "running":
            return task
    return ZhipuTask(status="failed", error="生成超时")

