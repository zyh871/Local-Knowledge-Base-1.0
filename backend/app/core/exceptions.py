"""
领域异常族 + 全局异常处理器。

设计原则：
  * 业务代码（services / graph / repositories）只抛 `AppError` 子类，
    **不抛 HTTPException** —— 保持领域层与 HTTP 语义解耦。
  * 所有异常到 HTTP 响应的翻译集中在本模块，响应体结构统一为：
        {"error": {"code": str, "message": str, "detail": any, "trace_id": str}}
  * SSE 是特例：响应头发出后无法再改状态码，流式端点内部须自行兜底
    （见 `app/services/qa_service.py`），不能依赖本模块。

注意兼容性：旧实现的部分端点把错误塞在 200 响应体里
（如 `/ask` 用 `{"answer": "...", "data_source": "error"}`）。
这类**业务级错误**不是异常，仍由 service 层返回正常响应，本模块不介入。
"""

from __future__ import annotations

from typing import Any, Optional


class AppError(Exception):
    """所有业务异常的基类。"""

    status_code: int = 500
    code: str = "internal_error"
    message: str = "服务内部错误"
    # 默认附加的响应头；子类可覆盖（如 401 需要 WWW-Authenticate）
    default_headers: dict[str, str] = {}

    def __init__(
        self,
        message: Optional[str] = None,
        *,
        detail: Any = None,
        headers: Optional[dict[str, str]] = None,
    ) -> None:
        self.message = message or self.__class__.message
        self.detail = detail
        self.headers = headers if headers is not None else dict(self.default_headers)
        super().__init__(self.message)

    def to_payload(self, trace_id: str = "-") -> dict[str, Any]:
        """统一错误体。

        顶层 `detail` 与 `error` 同时存在：
          * `detail` 兼容旧 `HTTPException(detail=...)` 的响应形状，前端按老方式读不破
          * `error` 为 v4 结构化错误，供新客户端按 code 分支处理
        """
        return {
            "detail": self.message,
            "error": {
                "code": self.code,
                "message": self.message,
                "detail": self.detail,
                "trace_id": trace_id,
            },
        }


# ── 4xx ──────────────────────────────────────────────────────────────


class ValidationError(AppError):
    status_code = 400
    code = "validation_error"
    message = "请求参数不合法"


class UnauthorizedError(AppError):
    status_code = 401
    code = "unauthorized"
    message = "无法验证凭据"
    default_headers = {"WWW-Authenticate": "Bearer"}


class InvalidTokenError(UnauthorizedError):
    code = "invalid_token"
    message = "令牌无效或已过期"


class PermissionDeniedError(AppError):
    status_code = 403
    code = "permission_denied"
    message = "无权访问该资源"


class NotFoundError(AppError):
    status_code = 404
    code = "not_found"
    message = "资源不存在"


class ConflictError(AppError):
    status_code = 409
    code = "conflict"
    message = "资源状态冲突"


class GoneError(AppError):
    status_code = 410
    code = "gone"
    message = "资源已过期"


class RateLimitError(AppError):
    status_code = 429
    code = "rate_limited"
    message = "请求过于频繁，请稍后再试"

    def __init__(self, message: Optional[str] = None, *, retry_after: int = 60) -> None:
        super().__init__(message, detail={"retry_after": retry_after})
        self.retry_after = retry_after


# ── 领域细分（继承上面的状态码）─────────────────────────────────────


class UnsupportedFileTypeError(ValidationError):
    code = "unsupported_file_type"
    message = "不支持的文件类型"


class FileTooLargeError(ValidationError):
    code = "file_too_large"
    message = "文件大小超过限制"


class IllegalFilenameError(ValidationError):
    code = "illegal_filename"
    message = "文件名包含非法字符"


class DocumentParseError(AppError):
    status_code = 422
    code = "document_parse_failed"
    message = "无法解析该文件内容"


class OcrUnavailableError(DocumentParseError):
    """OCR 引擎或语言包不可用。

    与 `DocumentParseError` 同为 422（都是「这个文件这次处理不了」），
    但 `code` 独立，便于前端区分「文件内容有问题」与「环境缺少 OCR」——
    后者是可以修的，前者通常只能换文件。
    """

    code = "ocr_unavailable"
    message = "OCR 引擎不可用，无法解析图片类文件"


class LongTextOperationError(ValidationError):
    code = "unsupported_operation"
    message = "不支持的操作类型"


# ── 5xx ──────────────────────────────────────────────────────────────


class UpstreamLLMError(AppError):
    status_code = 502
    code = "upstream_llm_error"
    message = "上游模型服务异常"


class UpstreamSearchError(AppError):
    status_code = 502
    code = "upstream_search_error"
    message = "联网搜索服务异常"


class VectorStoreError(AppError):
    status_code = 503
    code = "vectorstore_unavailable"
    message = "向量库不可用"


class DependencyUnavailableError(AppError):
    status_code = 503
    code = "dependency_unavailable"
    message = "依赖服务不可用"


class ConfigurationError(AppError):
    status_code = 500
    code = "configuration_error"
    message = "服务配置错误"


# ---------------------------------------------------------------------------
#  全局处理器注册
# ---------------------------------------------------------------------------


def register_exception_handlers(app) -> None:
    """把领域异常与框架异常统一翻译成标准错误响应。"""
    from fastapi import Request
    from fastapi.exceptions import RequestValidationError
    from fastapi.responses import JSONResponse
    from starlette.exceptions import HTTPException as StarletteHTTPException

    from app.core.logging import get_logger, get_trace_id

    logger = get_logger(__name__)

    @app.exception_handler(AppError)
    async def _handle_app_error(request: Request, exc: AppError) -> JSONResponse:
        trace_id = get_trace_id(request)
        if exc.status_code >= 500:
            logger.error(f"AppError[{exc.code}]: {exc.message}", exc_info=True)
        else:
            logger.warning(f"AppError[{exc.code}]: {exc.message}")
        headers = dict(exc.headers) if exc.headers else {}
        if isinstance(exc, RateLimitError):
            headers["Retry-After"] = str(exc.retry_after)
        return JSONResponse(
            status_code=exc.status_code,
            content=exc.to_payload(trace_id),
            headers=headers or None,
        )

    @app.exception_handler(RequestValidationError)
    async def _handle_request_validation(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        trace_id = get_trace_id(request)
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "validation_error",
                    "message": "请求参数校验失败",
                    "detail": _serializable_errors(exc.errors()),
                    "trace_id": trace_id,
                }
            },
        )

    @app.exception_handler(StarletteHTTPException)
    async def _handle_http_exception(
        request: Request, exc: StarletteHTTPException
    ) -> JSONResponse:
        """统一框架层 HTTPException 的响应体形状。

        为兼容前端，`detail` 同时保留在顶层 `detail` 与 `error.message`，
        这样新旧两种读取方式都不会失效。
        """
        trace_id = get_trace_id(request)
        message = exc.detail if isinstance(exc.detail, str) else "请求失败"
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "detail": exc.detail,
                "error": {
                    "code": _code_for_status(exc.status_code),
                    "message": message,
                    "trace_id": trace_id,
                },
            },
            headers=getattr(exc, "headers", None),
        )

    @app.exception_handler(Exception)
    async def _handle_unhandled(request: Request, exc: Exception) -> JSONResponse:
        trace_id = get_trace_id(request)
        logger.error(f"未捕获异常: {exc}", exc_info=True)
        return JSONResponse(
            status_code=500,
            content={
                "error": {
                    "code": "internal_error",
                    "message": "服务内部错误",
                    "detail": str(exc) if get_settings_debug() else None,
                    "trace_id": trace_id,
                }
            },
        )


def _code_for_status(status_code: int) -> str:
    return {
        400: "bad_request",
        401: "unauthorized",
        403: "permission_denied",
        404: "not_found",
        405: "method_not_allowed",
        409: "conflict",
        410: "gone",
        413: "payload_too_large",
        422: "validation_error",
        429: "rate_limited",
        503: "dependency_unavailable",
    }.get(status_code, "http_error")


def _serializable_errors(errors: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """pydantic 的 errors() 可能含不可序列化的 ctx，做一次净化。"""
    out: list[dict[str, Any]] = []
    for e in errors:
        item = {k: v for k, v in e.items() if k != "ctx"}
        loc = item.get("loc")
        if isinstance(loc, (list, tuple)):
            item["loc"] = [str(x) for x in loc]
        ctx = e.get("ctx")
        if ctx:
            item["ctx"] = {k: str(v) for k, v in ctx.items()}
        out.append(item)
    return out


def get_settings_debug() -> bool:
    try:
        from app.core.config import get_settings

        return get_settings().env != "prod"
    except Exception:
        return False
