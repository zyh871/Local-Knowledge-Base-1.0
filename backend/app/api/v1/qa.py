"""
问答路由：`/ask`、`/ask/stream`、`/ask/image`。

契约要点：
  * `/ask` 的校验失败与内部错误沿用旧响应体（400/500 + answer 字段），
    故此处直接返回 JSONResponse 而非抛异常。
  * 流式端点返回 `text/event-stream`，异常已在 service 内转为 final 事件。
"""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import JSONResponse, StreamingResponse

from app.api.deps import QAServiceDep
from app.core.deps import CurrentUser, rate_limit
from app.core.logging import get_logger, get_trace_id
from app.schemas.qa import QuestionRequest, QuestionResponse

logger = get_logger(__name__)
router = APIRouter()

SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


def _legacy_error(status_code: int, message: str, trace_id: str, **extra: Any):
    """构造旧形状的错误响应体（前端依赖 `answer` 字段读取错误文案）。"""
    return JSONResponse(
        status_code=status_code,
        content={
            "answer": message,
            "sources": [],
            "data_source": "error",
            "trace_id": trace_id,
            "intent": "error",
            **extra,
        },
    )


@router.post("/ask", response_model=QuestionResponse, summary="普通问答")
async def ask_question(
    req: QuestionRequest,
    request: Request,
    current_user: CurrentUser,
    svc: QAServiceDep,
    _: None = Depends(rate_limit("ask")),
):
    trace_id = get_trace_id(request)
    try:
        question = req.validate_question()
        model = req.resolve_model()
    except ValueError as exc:
        return _legacy_error(400, str(exc), trace_id)

    outcome = await svc.ask(
        question=question,
        conversation_id=req.conversation_id,
        username=current_user.username,
        force_search=req.force_search,
        trace_id=trace_id,
        model=model,
    )
    if outcome.ok:
        return outcome.payload
    return JSONResponse(status_code=outcome.status_code, content=outcome.payload)


@router.post("/ask/stream", summary="流式问答（SSE）")
async def ask_question_stream(
    req: QuestionRequest,
    request: Request,
    current_user: CurrentUser,
    svc: QAServiceDep,
    _: None = Depends(rate_limit("ask_stream")),
):
    trace_id = get_trace_id(request)
    try:
        question = req.validate_question()
        model = req.resolve_model()
    except ValueError as exc:
        # ⚠️ 必须先取出为普通局部变量：Python 会在 except 块结束时删除 `exc`，
        # 下方的 async generator 是**延迟执行**的，直接捕获 `exc` 会在
        # 生成器被消费时抛 NameError（cannot access free variable 'exc'）。
        message = str(exc)

        async def _err():
            import json

            yield (
                "data: "
                + json.dumps(
                    {
                        "type": "final",
                        "content": {
                            "answer": message,
                            "sources": [],
                            "data_source": "error",
                            "trace_id": trace_id,
                            "intent": "error",
                        },
                    },
                    ensure_ascii=False,
                )
                + "\n\n"
            )

        return StreamingResponse(
            _err(), media_type="text/event-stream", headers=SSE_HEADERS
        )

    return StreamingResponse(
        svc.stream_ask(
            question=question,
            conversation_id=req.conversation_id,
            username=current_user.username,
            force_search=req.force_search,
            trace_id=trace_id,
            model=model,
        ),
        media_type="text/event-stream",
        headers=SSE_HEADERS,
    )


@router.post("/ask/image", summary="图片问答（SSE）")
async def ask_image(
    request: Request,
    current_user: CurrentUser,
    svc: QAServiceDep,
    file: UploadFile = File(...),
    question: str = Form(""),
    conversation_id: str = Form("default"),
    _: None = Depends(rate_limit("ask_image")),
):
    trace_id = get_trace_id(request)

    if not file or not file.filename:
        return StreamingResponse(
            _single_final("未收到图片文件", trace_id),
            media_type="text/event-stream",
            headers=SSE_HEADERS,
        )

    try:
        contents = await file.read()
    except Exception as exc:  # noqa: BLE001
        return StreamingResponse(
            _single_final(f"读取图片失败: {exc}", trace_id),
            media_type="text/event-stream",
            headers=SSE_HEADERS,
        )

    return StreamingResponse(
        svc.stream_image(
            filename=file.filename,
            contents=contents,
            question=question,
            conversation_id=conversation_id or "default",
            username=current_user.username,
            trace_id=trace_id,
        ),
        media_type="text/event-stream",
        headers=SSE_HEADERS,
    )


def _single_final(message: str, trace_id: str):
    """构造只含一个 final 事件的 SSE 流。"""
    import json

    async def _gen():
        yield (
            "data: "
            + json.dumps(
                {
                    "type": "final",
                    "content": {
                        "answer": message,
                        "sources": [],
                        "data_source": "error",
                        "trace_id": trace_id,
                        "intent": "image_qa_error",
                    },
                },
                ensure_ascii=False,
            )
            + "\n\n"
        )

    return _gen()
