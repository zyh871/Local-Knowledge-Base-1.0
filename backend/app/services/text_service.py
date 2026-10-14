"""长文本工具用例（摘要 / 翻译 / 针对文本问答）。"""

from __future__ import annotations

from typing import Any, Optional

from app.core.exceptions import LongTextOperationError

_SUMMARY_OPS = {"summary", "summarize", "摘要", "总结"}
_TRANSLATE_OPS = {"translate", "翻译"}
_QA_OPS = {"qa", "ask", "问答"}


class LongTextService:
    def __init__(self, *, settings: Any) -> None:
        self.settings = settings

    def run(
        self,
        *,
        text: str,
        operation: str,
        target_language: Optional[str],
        question: Optional[str],
        max_chars: int,
    ) -> dict[str, Any]:
        from app.document import answer_against_text, summarize, translate

        op = (operation or "summary").strip().lower()

        if op in _SUMMARY_OPS:
            result = summarize(text, max_chars=max_chars)
        elif op in _TRANSLATE_OPS:
            result = translate(text, target_language=target_language or "zh")
        elif op in _QA_OPS:
            if not question:
                raise LongTextOperationError("问答模式需要提供 question 参数")
            result = answer_against_text(text, question)
        else:
            raise LongTextOperationError(f"不支持的操作类型: {operation}")

        return {"result": result, "operation": operation}
