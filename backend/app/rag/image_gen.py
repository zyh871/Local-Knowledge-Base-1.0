"""图像生成能力（AI 创作页）。

与 `providers.py` 的分工很清楚：那边是「对话 / 嵌入」的模型工厂，返回 LangChain
实例、带单例与缓存；这边是**一进一出**的 HTTP 调用 —— 给一段提示词，拿一份
图片字节。两者的生命周期与失败模式都不一样，混进 providers 只会让那边的缓存
逻辑难以解释。

⚠️ 两个必须知道的实现事实：

1. **上游返回的是带签名的临时 URL**（智谱的链接约 7 天后失效）。
   直接把 URL 存进历史记录，几天后整面墙就全是裂图。所以本模块负责把图片
   **下载成字节**，由上层落盘，历史才真正留得住。

2. **不是所有后端都能画图**。`local / mock / ollama / baidu_qianfan` 没有对应的
   图像接口，此时给出明确错误（400），而不是让用户对着一个永远转圈的占位块。
"""

from __future__ import annotations

import base64
from typing import Any

import httpx

from app.core.config import get_model_settings
from app.core.exceptions import UpstreamLLMError, ValidationError
from app.core.logging import get_logger
from app.rag.providers import CUSTOM_PREFIX, get_custom_endpoint

logger = get_logger(__name__)

__all__ = [
    "SUPPORTED_BACKENDS",
    "describe_unsupported",
    "generate_image",
    "supports_image_generation",
]

#: 支持 `images/generations` 的后端。
#: 智谱与 OpenAI 兼容网关的请求结构一致，只有 base_url / 鉴权不同。
SUPPORTED_BACKENDS = ("zhipu", "openai", "modelscope")

#: 单张图片的字节上限。上游一般 1-4 MB，20 MB 已经足够宽松；
#: 没有上限的话，一个异常响应可能把磁盘写满。
MAX_IMAGE_BYTES = 20 * 1024 * 1024


def supports_image_generation(model: str = "") -> bool:
    """当前部署能否画图。

    `model` 传自定义端点 id（`custom:xxx`）时，只要有该端点就允许 —— 端点本身
    是否真的支持 `/images/generations` 只能由上游回答，这里不替它猜。
    """
    if model and model.startswith(CUSTOM_PREFIX):
        return get_custom_endpoint(model) is not None
    return get_model_settings().backend in SUPPORTED_BACKENDS


def describe_unsupported() -> str:
    backend = get_model_settings().backend
    return (
        f"当前模型后端（{backend}）不支持图像生成。"
        "请在 .env 中把 MODEL_BACKEND 设为 zhipu（或 openai / modelscope 兼容端点）后重启服务。"
    )


def _endpoint(model: str) -> tuple[str, str, str]:
    """解析出 (url, api_key, upstream_model)。"""
    settings = get_model_settings()

    if model.startswith(CUSTOM_PREFIX):
        item = get_custom_endpoint(model)
        if item is None:
            raise ValidationError(f"自定义模型「{model}」不存在或已停用")
        url = item["base_url"].rstrip("/") + "/images/generations"
        return url, item["api_key"], item["model"]

    backend = settings.backend
    if backend == "zhipu":
        return (
            settings.zhipu_base_url.rstrip("/") + "/images/generations",
            settings.zhipu_api_key,
            model,
        )
    if backend == "openai":
        base = (settings.openai_base_url or "https://api.openai.com/v1").rstrip("/")
        return base + "/images/generations", settings.openai_api_key, model
    if backend == "modelscope":
        base = settings.modelscope_base_url.rstrip("/")
        return base + "/images/generations", settings.modelscope_api_key, model

    raise ValidationError(describe_unsupported())


def _upstream_error(body: Any, status_code: int) -> str:
    """从上游错误体里挖出人能读的原因。

    智谱是 `{"error": {"code": "1301", "message": "..."}}`，OpenAI 兼容网关
    多为 `{"error": {"message": "..."}}`，也见过 `{"message": "..."}`。
    挖不到就退回状态码 —— 至少能区分「鉴权失败」和「服务挂了」。
    """
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict):
            message = str(error.get("message") or "").strip()
            code = str(error.get("code") or "").strip()
            if message:
                return f"{message}（{code}）" if code else message
        if isinstance(error, str) and error.strip():
            return error.strip()
        message = str(body.get("message") or "").strip()
        if message:
            return message
    return f"上游返回 HTTP {status_code}"


async def _download(client: httpx.AsyncClient, url: str) -> bytes:
    response = await client.get(url, follow_redirects=True)
    if response.status_code != 200:
        raise ValidationError(
            f"图片已生成但下载失败（HTTP {response.status_code}），请重试"
        )
    data = response.content
    if not data:
        raise ValidationError("图片已生成但内容为空，请重试")
    if len(data) > MAX_IMAGE_BYTES:
        raise ValidationError(
            f"生成的图片超过 {MAX_IMAGE_BYTES // (1024 * 1024)} MB，已拒绝保存"
        )
    return data


async def generate_image(
    prompt: str,
    *,
    model: str,
    size: str,
    timeout: float = 180.0,
) -> bytes:
    """生成一张图并返回图片字节。

    超时给得比较宽（默认 180s）：CogView 在高峰期单张 20-60 秒很常见，
    超时太短会让用户以为「功能坏了」。真正的长尾由上游自己兜住。
    """
    url, api_key, upstream_model = _endpoint(model)
    if not api_key:
        raise ValidationError("未配置图像生成的 API Key，请检查 .env")

    payload = {
        "model": upstream_model,
        "prompt": prompt,
        "size": size,
        # 只需要一张：多张由前端「再来一次」累积，避免一次请求放大四倍成本
        "n": 1,
    }

    logger.info(f"图像生成: model={upstream_model} size={size} prompt={prompt[:60]}")

    async with httpx.AsyncClient(timeout=timeout) as client:
        try:
            response = await client.post(
                url,
                json=payload,
                headers={"Authorization": f"Bearer {api_key}"},
            )
        except httpx.TimeoutException as exc:
            raise UpstreamLLMError(
                f"图像生成超时（{int(timeout)} 秒），上游可能繁忙，请稍后重试"
            ) from exc
        except httpx.HTTPError as exc:
            raise UpstreamLLMError(f"无法连接图像服务：{exc}") from exc

        try:
            body = response.json()
        except ValueError:
            body = None

        if response.status_code != 200:
            reason = _upstream_error(body, response.status_code)
            logger.warning(f"图像生成失败: HTTP {response.status_code} {reason}")
            raise UpstreamLLMError(f"图像生成失败：{reason}")

        items = (body or {}).get("data") or []
        if not items:
            raise UpstreamLLMError("图像生成失败：上游未返回图片")

        item = items[0]
        if item.get("url"):
            return await _download(client, item["url"])
        if item.get("b64_json"):
            try:
                return base64.b64decode(item["b64_json"], validate=False)
            except (ValueError, TypeError) as exc:
                raise UpstreamLLMError("图像生成失败：返回的图片数据无法解码") from exc

    raise UpstreamLLMError("图像生成失败：上游返回结构无法识别")


async def describe_reference(image_bytes: bytes, mime: str, *, timeout: float = 60.0) -> str:
    """用视觉模型把参考图读成一段画面描述。

    为什么要绕这一圈：CogView 的 `images/generations` 只吃文本，
    没有图生图入口。与其做一个「上传了但不起作用」的假入口，
    不如把参考图变成提示词的一部分 —— 用户能看到它到底被读成了什么。
    """
    from langchain_core.messages import HumanMessage

    from app.rag.providers import get_vision_llm

    data_url = f"data:{mime};base64,{base64.b64encode(image_bytes).decode('ascii')}"
    message = HumanMessage(
        content=[
            {"type": "image_url", "image_url": {"url": data_url}},
            {
                "type": "text",
                "text": (
                    "请用一段中文描述这张图，供文生图模型复用："
                    "主体、构图、镜头、光线、色彩、材质与画风。"
                    "只输出描述本身，不要解释，不要分点，不超过 160 字。"
                ),
            },
        ]
    )
    llm = get_vision_llm()
    result = await llm.ainvoke([message])
    text = getattr(result, "content", result)
    if isinstance(text, list):  # 部分实现返回 content 片段数组
        text = "".join(str(part.get("text", "")) if isinstance(part, dict) else str(part) for part in text)
    return str(text).strip()[:400]
