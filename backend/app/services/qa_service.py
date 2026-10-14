"""
问答用例编排。

覆盖端点：`/ask`、`/ask/stream`、`/ask/image`

契约约束（**不得修改**）：
  * 非流式 `/ask` 出错时返回 HTTP 400/500 + 旧响应体
    `{"answer": ..., "sources": [], "data_source": "error", "trace_id": ...}`，
    而不是统一错误响应 —— 前端依赖该形状读取错误文案。
    因此本模块返回 `AnswerOutcome`，由路由层原样转成 JSONResponse。
  * 流式端点一旦发出响应头，状态码不可再改：所有异常须转成 `final` 事件。
    这是 SSE 端点必须自行兜底的原因，不能依赖全局异常处理器。
"""

from __future__ import annotations

import asyncio
import base64
import os
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Optional

from app.core.logging import get_logger
from app.schemas.qa import sse_frame
from app.utils.files import image_mime, validate_image_upload
from app.utils.text import extract_text, format_history

logger = get_logger(__name__)


@dataclass
class AnswerOutcome:
    """非流式应答结果：显式表达「HTTP 状态码 + 响应体」。"""

    status_code: int = 200
    payload: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return 200 <= self.status_code < 300


def _classify_error(error: Exception) -> str:
    """沿用旧实现的错误归类，供前端展示不同提示。"""
    text = str(error).lower()
    if "timeout" in text or "timed out" in text:
        return "timeout"
    if "token" in text or "auth" in text:
        return "auth"
    if "vector" in text or "chroma" in text:
        return "vectorstore"
    if "file" in text or "format" in text:
        return "file"
    return "unknown"


class QAService:
    def __init__(
        self,
        *,
        chain: Any,
        settings: Any,
        redis: Any = None,
        graph: Any = None,
    ) -> None:
        self.chain = chain
        self.settings = settings
        self.redis = redis
        self.graph = graph

    # ------------------------------------------------------------------
    #  非流式问答
    # ------------------------------------------------------------------

    async def ask(
        self,
        *,
        question: str,
        conversation_id: Optional[str],
        username: str,
        force_search: bool,
        trace_id: str,
        model: Optional[str] = None,
    ) -> AnswerOutcome:
        conv_id = conversation_id or "default"
        try:
            logger.info(f"[{trace_id}] 收到问题: {question[:50]}... (model={model or '默认'})")
            result = await self._invoke(
                question, conv_id, username, force_search, trace_id, model
            )
            result["trace_id"] = trace_id
            logger.info(
                f"[{trace_id}] 回答完成，data_source={result.get('data_source')}, "
                f"model={result.get('model')}, truncated={result.get('truncated')}, "
                f"sources={len(result.get('sources', []))}"
            )
            return AnswerOutcome(200, result)
        except Exception as exc:  # noqa: BLE001
            logger.error(f"[{trace_id}] 处理问题失败: {exc}", exc_info=True)
            return AnswerOutcome(
                500,
                {
                    "answer": f"系统错误: {exc}",
                    "sources": [],
                    "data_source": "error",
                    "trace_id": trace_id,
                    "intent": "error",
                    "error_type": _classify_error(exc),
                },
            )

    async def _invoke(
        self,
        question: str,
        conversation_id: str,
        username: str,
        force_search: bool,
        trace_id: str,
        model: Optional[str] = None,
    ) -> dict[str, Any]:
        """执行状态图。图是唯一的编排实现（旧 router 已删除）。"""
        if self.graph is None:
            raise RuntimeError("问答状态图未初始化，服务不可用")

        from app.graph.runner import run_graph

        return await run_graph(
            self.graph,
            self.settings,
            question=question,
            conversation_id=conversation_id,
            username=username,
            force_search=force_search,
            trace_id=trace_id,
            model=model,
            redis=self.redis,
        )

    # ------------------------------------------------------------------
    #  流式问答
    # ------------------------------------------------------------------

    async def stream_ask(
        self,
        *,
        question: str,
        conversation_id: Optional[str],
        username: str,
        force_search: bool,
        trace_id: str,
        model: Optional[str] = None,
    ) -> AsyncIterator[str]:
        """产出 SSE 帧。异常一律转成 final 事件（响应头已发出，无法改状态码）。"""
        conv_id = conversation_id or "default"
        try:
            logger.info(
                f"[{trace_id}] 收到流式问题: {question[:50]}... (model={model or '默认'})"
            )
            self._maybe_set_auto_title(conv_id, question, username)

            async for event in self._stream(
                question, conv_id, username, force_search, trace_id, model
            ):
                yield sse_frame({**event, "trace_id": trace_id})

        except asyncio.CancelledError:
            logger.info(f"[{trace_id}] 流式请求被客户端取消")
            raise
        except Exception as exc:  # noqa: BLE001
            logger.error(f"[{trace_id}] 流式处理失败: {exc}", exc_info=True)
            yield sse_frame(
                {
                    "type": "final",
                    "content": {
                        "answer": f"系统错误: {exc}",
                        "sources": [],
                        "data_source": "error",
                        "trace_id": trace_id,
                        "intent": "error",
                        "error_type": _classify_error(exc),
                    },
                    "trace_id": trace_id,
                }
            )

    async def _stream(
        self,
        question: str,
        conversation_id: str,
        username: str,
        force_search: bool,
        trace_id: str,
        model: Optional[str] = None,
    ) -> AsyncIterator[dict[str, Any]]:
        if self.graph is None:
            raise RuntimeError("问答状态图未初始化，服务不可用")

        from app.graph.runner import stream_graph

        async for event in stream_graph(
            self.graph,
            self.settings,
            question=question,
            conversation_id=conversation_id,
            username=username,
            force_search=force_search,
            trace_id=trace_id,
            model=model,
            redis=self.redis,
        ):
            yield event

    def _maybe_set_auto_title(
        self, conversation_id: str, question: str, username: str
    ) -> None:
        """首条消息时用问题前 30 字作标题（临时对话 `temp-` 前缀跳过）。"""
        if not conversation_id or conversation_id.startswith("temp-"):
            return
        try:
            from app.memory import get_conversation, upsert_conversation

            meta = get_conversation(conversation_id)
            if not meta or not meta.get("title") or meta.get("title") == "新对话":
                title = question.strip().split("\n")[0][:30]
                if title:
                    upsert_conversation(
                        conversation_id, username=username, title=title, bump_turn=False
                    )
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"自动设置标题失败（可忽略）: {exc}")

    # ------------------------------------------------------------------
    #  图片问答
    # ------------------------------------------------------------------

    async def stream_image(
        self,
        *,
        filename: str,
        contents: bytes,
        question: str,
        conversation_id: str,
        username: str,
        trace_id: str,
    ) -> AsyncIterator[str]:
        """图片问答流。校验失败同样以 final 事件返回（响应头已发出）。"""
        q = (question or "").strip() or "请描述这张图片的内容。"
        if len(q) > 2000:
            q = q[:2000]

        try:
            ext = validate_image_upload(filename, len(contents))
        except Exception as exc:  # noqa: BLE001
            yield self._image_error(str(exc), trace_id)
            return

        from langchain_core.messages import HumanMessage

        from app.memory import load_history
        from app.rag.providers import get_vision_llm

        data_url = f"data:{image_mime(ext)};base64,{base64.b64encode(contents).decode('ascii')}"

        try:
            history = load_history(conversation_id)
        except Exception:  # noqa: BLE001
            history = []

        history_block = format_history(history) if history else ""
        user_prompt = f"对话历史：\n{history_block}\n\n问题：{q}" if history_block else q

        message = HumanMessage(
            content=[
                {"type": "image_url", "image_url": {"url": data_url}},
                {"type": "text", "text": user_prompt},
            ]
        )

        try:
            vision_llm = get_vision_llm()
        except Exception as exc:  # noqa: BLE001
            yield self._image_error(f"视觉模型初始化失败: {exc}", trace_id)
            return

        logger.info(
            f"[{trace_id}] 收到图片问答: file={filename}, question={q[:50]}, "
            f"model={getattr(vision_llm, 'model_name', '')}"
        )
        self._maybe_set_image_title(conversation_id, q, username)

        try:
            yield sse_frame(
                {
                    "type": "intent",
                    "content": {"intent": "image_qa", "confidence": 1.0},
                    "trace_id": trace_id,
                }
            )
            yield sse_frame({"type": "sources", "content": [], "trace_id": trace_id})

            full = ""
            try:
                for chunk in vision_llm.stream([message]):
                    text = extract_text(chunk)
                    if not text:
                        continue
                    full += text
                    yield sse_frame(
                        {"type": "chunk", "content": text, "trace_id": trace_id}
                    )
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"[{trace_id}] vision stream 失败，回退 invoke: {exc}")
                try:
                    answer = vision_llm.invoke([message])
                    text = extract_text(answer)
                    if text:
                        full = text
                        yield sse_frame(
                            {"type": "chunk", "content": text, "trace_id": trace_id}
                        )
                except Exception as fallback_exc:  # noqa: BLE001
                    logger.error(f"[{trace_id}] vision invoke 回退失败: {fallback_exc}")
                    full = f"（图片识别失败: {fallback_exc}）"
                    yield sse_frame(
                        {"type": "chunk", "content": full, "trace_id": trace_id}
                    )

            try:
                from app.memory import save_context

                save_context(conversation_id, f"[图片] {filename}\n{q}", full)
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"[{trace_id}] 保存图片问答上下文失败: {exc}")

            yield sse_frame(
                {
                    "type": "final",
                    "content": {
                        "answer": full,
                        "sources": [],
                        "data_source": "vision",
                        "intent": "image_qa",
                        "intent_confidence": 1.0,
                        "detected_language": "auto",
                        "trace_id": trace_id,
                    },
                    "trace_id": trace_id,
                }
            )

        except asyncio.CancelledError:
            logger.info(f"[{trace_id}] 图片问答流被客户端取消")
            raise
        except Exception as exc:  # noqa: BLE001
            logger.error(f"[{trace_id}] 图片问答失败: {exc}", exc_info=True)
            yield self._image_error(f"系统错误: {exc}", trace_id)

    def _maybe_set_image_title(
        self, conversation_id: str, question: str, username: str
    ) -> None:
        if not conversation_id or conversation_id.startswith("temp-"):
            return
        try:
            from app.memory import get_conversation, upsert_conversation

            meta = get_conversation(conversation_id)
            if not meta or not meta.get("title") or meta.get("title") == "新对话":
                upsert_conversation(
                    conversation_id,
                    username=username,
                    title=f"[图片问答] {question.strip()[:25]}",
                    bump_turn=False,
                )
        except Exception:  # noqa: BLE001
            pass

    @staticmethod
    def _image_error(message: str, trace_id: str) -> str:
        return sse_frame(
            {
                "type": "final",
                "content": {
                    "answer": message,
                    "sources": [],
                    "data_source": "error",
                    "trace_id": trace_id,
                    "intent": "image_qa_error",
                },
                "trace_id": trace_id,
            }
        )
