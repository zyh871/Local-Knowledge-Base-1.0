"""问答相关模型：请求、响应 与 SSE 事件契约。"""

from __future__ import annotations

from typing import Annotated, Any, Literal, Optional, Union

from pydantic import BaseModel, Field

from app.schemas.common import SourceItem

# 与旧 main.py 完全一致的非法内容黑名单
_FORBIDDEN_PATTERNS = ("<script", "javascript:", "data:", "vbscript:")
_MAX_QUESTION_LEN = 2000


class QuestionRequest(BaseModel):
    question: str
    conversation_id: Optional[str] = None
    force_search: bool = False
    # 按请求覆盖模型（前端「模型切换」控件）。
    # 留空 → 用服务端配置的默认模型。取值必须命中白名单，否则 400。
    model: Optional[str] = None

    def validate_question(self) -> str:
        """校验并返回清理后的问题。

        保留为方法而非 field_validator —— 见 schemas/__init__.py 顶部说明。
        抛 ValueError，由 service 层转为旧的 400 响应形状。
        """
        q = (self.question or "").strip()
        if not q:
            raise ValueError("问题不能为空")
        if len(q) > _MAX_QUESTION_LEN:
            raise ValueError(f"问题长度不能超过{_MAX_QUESTION_LEN}字符")
        lowered = q.lower()
        for pattern in _FORBIDDEN_PATTERNS:
            if pattern in lowered:
                raise ValueError("问题包含非法内容")
        return q

    def resolve_model(self) -> Optional[str]:
        """校验模型可用性。非法即抛 ValueError（→ 400），不静默回退。

        可用性 = 内置白名单 ∪ 已启用的自定义端点 —— 判据只有一处
        （`providers.is_model_available`），避免「这边认、那边不认」的分叉。
        """
        if not self.model:
            return None
        from app.rag.providers import describe_available_models, is_model_available

        if not is_model_available(self.model):
            raise ValueError(
                f"不支持的模型「{self.model}」。可选：{describe_available_models()}"
            )
        return self.model


class ModelInfo(BaseModel):
    """模型切换控件的一个候选项。"""

    id: str
    label: str = ""
    is_default: bool = False
    #: 是否为用户自行接入的端点
    custom: bool = False
    #: 自定义端点的显示名（内置模型为空）
    provider: str = ""


class ModelListResponse(BaseModel):
    models: list[ModelInfo] = Field(default_factory=list)
    # 服务端当前默认模型，前端首次加载时用它作为选中项
    default_model: str = ""
    # 当前后端（zhipu / openai / ...），便于前端按后端展示不同提示
    backend: str = ""



class QuestionResponse(BaseModel):
    answer: str
    sources: list[Any] = Field(default_factory=list)
    data_source: str = "rag"
    trace_id: str = ""
    intent: str = ""
    intent_confidence: float = 0.0
    detected_language: str = "auto"
    needs_clarification: bool = False
    # 实际产出这条回答的模型（前端用于回显"本次由谁回答"）
    model: str = ""
    # 生成是否被截断（finish_reason=length 或流中途断开）。
    # 前端可据此提示"回答可能不完整"，而不是让用户以为模型就这么短。
    truncated: bool = False


class ErrorAnswerResponse(BaseModel):
    """`/ask` 出错时沿用的旧响应形状（HTTP 400 / 500）。"""

    answer: str
    sources: list[Any] = Field(default_factory=list)
    data_source: str = "error"
    trace_id: str = ""
    intent: str = "error"
    error_type: Optional[str] = None


# ---------------------------------------------------------------------------
#  SSE 事件契约
#  前端消费顺序：intent -> sources -> (tool_calls) -> chunk* -> final
#  结构必须与 v3.0.0 一致，否则前端渲染会失效。
# ---------------------------------------------------------------------------


class SSEIntent(BaseModel):
    type: Literal["intent"] = "intent"
    content: dict[str, Any]
    trace_id: str = ""


class SSESources(BaseModel):
    type: Literal["sources"] = "sources"
    content: list[Any] = Field(default_factory=list)
    trace_id: str = ""


class SSEChunk(BaseModel):
    type: Literal["chunk"] = "chunk"
    content: str = ""
    trace_id: str = ""


class SSEToolCalls(BaseModel):
    type: Literal["tool_calls"] = "tool_calls"
    content: list[Any] = Field(default_factory=list)
    trace_id: str = ""


class SSEFinal(BaseModel):
    type: Literal["final"] = "final"
    content: dict[str, Any]
    trace_id: str = ""


SSEEvent = Annotated[
    Union[SSEIntent, SSESources, SSEChunk, SSEToolCalls, SSEFinal],
    Field(discriminator="type"),
]


def sse_frame(event: dict[str, Any]) -> str:
    """把事件字典序列化成 SSE 帧（`data: {...}\\n\\n`）。

    所有流式端点统一走这里，避免各处手拼字符串导致格式漂移。
    """
    import json

    return f"data: {json.dumps(event, ensure_ascii=False, default=str)}\n\n"
