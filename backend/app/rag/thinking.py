"""推理流客户端：把「思考过程」与「答案」分成两条通道。

## 为什么不用 ChatOpenAI

`langchain-openai` 1.x 在源码顶部就写明了：第三方 provider 的非标准字段
（`reasoning_content`、`reasoning_details`）**不会被提取**。实测确实如此 ——
智谱 GLM-4.5 的流里明明有 `reasoning_content`，经过 `ChatOpenAI` 之后只剩
`content`，思考过程被静默丢弃。

所以这里直接用 `openai` SDK 的流式接口（智谱同样是 OpenAI 兼容协议），
把 delta 里的两个字段分别暴露出来。**这不是重复造轮子，是绕开一个明确的
能力缺口**；其余路径（无 thinking）仍然走 ChatOpenAI。

## 为什么保留 invoke()

`generate` 节点在流式失败时会回退为同步调用。如果这条路径只支持流式，
「深度思考」就会变成「一次网络抖动 = 整段回答丢失」的脆弱链路。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterator, Optional

from app.core.logging import get_logger

logger = get_logger(__name__)

__all__ = ["ThinkingChunk", "ThinkingChat"]


@dataclass
class ThinkingChunk:
    """一个流式分片。

    字段刻意与 LangChain 的 `AIMessageChunk` 对齐（`content` /
    `response_metadata` / `usage_metadata`），这样 `extract_text()`、
    `finish_reason_of()`、`usage_of()` 这些既有工具能原样复用，
    不需要在节点里写两套取值逻辑。
    """

    content: str = ""
    reasoning: str = ""
    finish_reason: Optional[str] = None
    response_metadata: dict[str, Any] = field(default_factory=dict)
    usage_metadata: dict[str, Any] = field(default_factory=dict)


def _delta_reasoning(delta: Any) -> str:
    """从 delta 里取 `reasoning_content`。

    不同 SDK 版本对「非标准字段」的暴露方式不同：有的直接挂成属性，
    有的塞进 `model_extra`。两个都试，取不到就当没有 —— 绝不猜。
    """
    value = getattr(delta, "reasoning_content", None)
    if not value:
        extra = getattr(delta, "model_extra", None) or {}
        if isinstance(extra, dict):
            value = extra.get("reasoning_content")
    return value if isinstance(value, str) else ""


class ThinkingChat:
    """最小可用的「带推理通道」的对话客户端。"""

    def __init__(
        self,
        *,
        model: str,
        api_key: str,
        base_url: str,
        timeout: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> None:
        from openai import OpenAI

        self.model_name = model
        self._max_tokens = max_tokens
        # 超时必须有：推理模型的「想很久」与「已经卡死」在界面上长得一样，
        # 没有超时就没法区分，只能一直转圈。
        self._client = OpenAI(
            api_key=api_key or "not-needed",
            base_url=base_url,
            timeout=timeout or 600.0,
        )

    # ------------------------------------------------------------------

    def stream(self, prompt: str, *, thinking: bool = True) -> Iterator[ThinkingChunk]:
        kwargs: dict[str, Any] = {
            "model": self.model_name,
            "messages": [{"role": "user", "content": prompt}],
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if thinking:
            # 这是智谱的开关名；OpenAI 官方模型会忽略未知的 extra_body 字段。
            kwargs["extra_body"] = {"thinking": {"type": "enabled"}}
        if self._max_tokens:
            kwargs["max_tokens"] = self._max_tokens

        stream = self._client.chat.completions.create(**kwargs)
        for chunk in stream:
            choices = getattr(chunk, "choices", None) or []
            usage = getattr(chunk, "usage", None)
            if not choices:
                # 末帧（仅携带 usage）也要把用量带出去，否则「输出被截断」
                # 这类问题会失去唯一的量化依据
                if usage is not None:
                    yield ThinkingChunk(usage_metadata=_usage_dict(usage))
                continue

            choice = choices[0]
            delta = choice.delta
            yield ThinkingChunk(
                content=(getattr(delta, "content", None) or ""),
                reasoning=_delta_reasoning(delta),
                finish_reason=getattr(choice, "finish_reason", None),
                response_metadata=(
                    {"finish_reason": choice.finish_reason} if choice.finish_reason else {}
                ),
                usage_metadata=_usage_dict(usage) if usage is not None else {},
            )

    def invoke(self, prompt: str, *, thinking: bool = False) -> ThinkingChunk:
        """非流式回退。

        `thinking=False`：走到这里通常是因为流式已经失败，重试时再花
        30 秒推理往往是白等 —— 先拿到答案比拿到思考过程重要。
        """
        kwargs: dict[str, Any] = {
            "model": self.model_name,
            "messages": [{"role": "user", "content": prompt}],
        }
        if self._max_tokens:
            kwargs["max_tokens"] = self._max_tokens
        if thinking:
            kwargs["extra_body"] = {"thinking": {"type": "enabled"}}

        response = self._client.chat.completions.create(**kwargs)
        choice = (getattr(response, "choices", None) or [None])[0]
        if choice is None:
            return ThinkingChunk()
        message = choice.message
        return ThinkingChunk(
            content=getattr(message, "content", None) or "",
            reasoning=_delta_reasoning(message),
            finish_reason=getattr(choice, "finish_reason", None),
            response_metadata=(
                {"finish_reason": choice.finish_reason} if choice.finish_reason else {}
            ),
            usage_metadata=_usage_dict(getattr(response, "usage", None)),
        )


def _usage_dict(usage: Any) -> dict[str, Any]:
    if usage is None:
        return {}
    data: dict[str, Any] = {}
    for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
        value = getattr(usage, key, None)
        if value is not None:
            data[key] = value
    return data
