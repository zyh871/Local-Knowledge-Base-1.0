"""
文本处理工具。

这些函数原先散落在 `app/agents/router.py` 内部（带下划线前缀），
被 main.py、graph 节点、services 多处复用，故提升为公开工具函数。

命名去掉了下划线前缀；`router.py` 通过 `import ... as _old_name` 保持向后兼容。
"""

from __future__ import annotations

import os
import re
from typing import Any, Dict, List, Optional

_LANG_INSTRUCTION_ZH = "请用中文回答。"
_LANG_INSTRUCTION_EN = "Please answer in English."
_LANG_INSTRUCTION_AUTO = (
    "请使用与用户输入相同的语言回答。 / Please answer in the same language as the user."
)

# 可被问题中直接引用的文件名后缀
_FILENAME_RE = re.compile(
    r"([\w\u4e00-\u9fa5\-_.]+\.(?:pdf|docx?|pptx?|xlsx?|txt|md|csv))",
    re.IGNORECASE,
)


def extract_text(obj: Any) -> str:
    """
    从 LangChain 的 AIMessage / AIMessageChunk / str / dict 中安全取出纯文本。

    规则：
      * str                              -> 原样返回
      * 有 `.content`（非空）            -> 返回 `.content`
      * 有 `.content`（空）且有 tool_calls -> 返回 ""（跳过工具调用帧）
      * 有 `.content`（空）且有 additional_kwargs 且无文本字段 -> 返回 ""
      * dict 含 content/text             -> 返回该值
      * 其他                             -> 返回 ""

    **绝不返回 `str(obj)`** —— 避免把内部 repr 泄漏到前端。
    """
    if obj is None:
        return ""
    if isinstance(obj, str):
        return obj
    if isinstance(obj, dict):
        value = (
            obj.get("content")
            or obj.get("text")
            or obj.get("answer")
            or obj.get("output_text")
        )
        return value if isinstance(value, str) else ""

    content = getattr(obj, "content", None)
    if isinstance(content, str) and content:
        return content
    if isinstance(content, list):
        parts: List[str] = []
        for part in content:
            if isinstance(part, str):
                parts.append(part)
            elif isinstance(part, dict) and isinstance(part.get("text"), str):
                parts.append(part["text"])
        return "".join(parts)

    if getattr(obj, "tool_calls", None):
        return ""

    additional = getattr(obj, "additional_kwargs", None) or {}
    if additional and not content:
        return ""

    for attr in ("text", "answer", "output_text"):
        value = getattr(obj, attr, None)
        if isinstance(value, str) and value:
            return value

    return ""


def lang_instruction(profile_lang: str, detected_lang: str) -> str:
    """根据用户画像语言偏好与检测到的语言，生成回答语言指令。"""
    lang = (profile_lang or "").lower() if profile_lang else ""
    if lang in ("zh", "zh-cn", "chinese", "中文"):
        return _LANG_INSTRUCTION_ZH
    if lang in ("en", "english"):
        return _LANG_INSTRUCTION_EN
    if detected_lang == "zh":
        return _LANG_INSTRUCTION_ZH
    if detected_lang == "en":
        return _LANG_INSTRUCTION_EN
    return _LANG_INSTRUCTION_AUTO


def format_history(history: Optional[List[Dict[str, str]]], limit: int = 10) -> str:
    """把消息列表渲染成提示词里的对话历史块。"""
    if not history:
        return "（无）"
    lines: List[str] = []
    for message in history[-limit:]:
        role = "用户" if message.get("role") == "user" else "助手"
        lines.append(f"{role}: {message.get('content', '')}")
    return "\n".join(lines)


def extract_filename(question: str) -> Optional[str]:
    """从问题中提取被引用的文件名，含路径遍历防护。"""
    match = _FILENAME_RE.search(question or "")
    if not match:
        return None
    filename = os.path.basename(match.group(1))
    if ".." in filename or filename.startswith(".") or "/" in filename or "\\" in filename:
        return None
    return filename


def detect_language(text: str) -> str:
    """中英语言启发式判断：CJK 字符数超过 ASCII 字母数即判为中文。"""
    if not text:
        return "auto"
    cjk = sum(1 for c in text if "\u4e00" <= c <= "\u9fff")
    ascii_letters = sum(1 for c in text if c.isascii() and c.isalpha())
    if cjk > ascii_letters:
        return "zh"
    if ascii_letters > 0:
        return "en"
    return "auto"


# ---------------------------------------------------------------------------
#  生成质量诊断
# ---------------------------------------------------------------------------


def finish_reason_of(chunk: Any) -> str | None:
    """从流式 chunk 中提取 `finish_reason`。

    不同 provider 放的位置不一致（response_metadata / choices / generation_info），
    这里统一兜住。**这是判断"回答为什么变短"的唯一可靠依据**：

        finish_reason == "length" → 撞到 max_tokens 上限（输出被强制截断）
        finish_reason == "stop"   → 模型正常收尾（内容短是模型自己的选择）
        finish_reason 缺失        → 流在中途断了（网络/风控/超时）

    ⚠️ 智谱在命中内容风控时可能在**流中途**直接断开（错误码 1301），
    此时不会给出 finish_reason。
    """
    meta = getattr(chunk, "response_metadata", None) or {}
    if isinstance(meta, dict):
        if meta.get("finish_reason"):
            return meta["finish_reason"]
        choices = meta.get("choices")
        if isinstance(choices, list) and choices and isinstance(choices[0], dict):
            if choices[0].get("finish_reason"):
                return choices[0]["finish_reason"]

    info = getattr(chunk, "generation_info", None) or {}
    if isinstance(info, dict) and info.get("finish_reason"):
        return info["finish_reason"]

    if isinstance(chunk, dict):
        if chunk.get("finish_reason"):
            return chunk["finish_reason"]
        choices = chunk.get("choices")
        if isinstance(choices, list) and choices and isinstance(choices[0], dict):
            return choices[0].get("finish_reason")
    return None


def usage_of(message: Any) -> dict:
    """提取 token 用量（尽力而为，缺失返回空字典）。"""
    usage = getattr(message, "usage_metadata", None)
    if isinstance(usage, dict) and usage:
        return usage
    meta = getattr(message, "response_metadata", None) or {}
    if isinstance(meta, dict):
        for key in ("token_usage", "usage"):
            if isinstance(meta.get(key), dict):
                return meta[key]
    return {}
