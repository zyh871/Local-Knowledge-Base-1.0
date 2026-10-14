"""
通用工具层 —— 无业务语义、无状态、可自由被任何上层模块依赖。
"""

from app.utils.text import (
    detect_language,
    extract_filename,
    extract_text,
    format_history,
    lang_instruction,
)

__all__ = [
    "extract_text",
    "format_history",
    "lang_instruction",
    "extract_filename",
    "detect_language",
]
