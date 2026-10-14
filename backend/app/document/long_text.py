"""
Long-text operations: summarize, translate, and answer questions over
arbitrarily long user-provided text (10k+ characters or even an entire
book) using a map-reduce style strategy.

Public API:
  * summarize(text, language="auto", max_chars=600) -> str
  * translate(text, target_language, source_language="auto") -> str
  * answer_against_text(text, question) -> str
  * chunk_text(text, chunk_size, overlap) -> List[str]
"""

import logging
import re
from typing import List, Optional

from langchain_text_splitters import RecursiveCharacterTextSplitter

logger = logging.getLogger(__name__)


def chunk_text(text: str, chunk_size: int = 1500, overlap: int = 200) -> List[str]:
    if not text:
        return []
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=overlap,
        separators=["\n\n", "\n", "。", "！", "？", ".", "!", "?", " ", ""],
    )
    return splitter.split_text(text)


def _llm_invoke(prompt: str) -> str:
    """Invoke the configured LLM and return plain text content."""
    from app.rag.providers import get_llm
    llm = get_llm()
    if llm is None:
        return ""
    try:
        out = llm.invoke(prompt)
    except Exception as e:
        logger.error(f"LLM invoke failed: {e}")
        return ""
    if hasattr(out, "content"):
        return out.content or ""
    return str(out or "")


# ----------------------------- Operations ----------------------------------

def summarize(text: str, language: str = "auto", max_chars: int = 600) -> str:
    """
    Map-reduce summarization.
    1) Split text into chunks.
    2) Summarize each chunk.
    3) Combine chunk summaries and re-summarize.
    """
    chunks = chunk_text(text)
    if not chunks:
        return ""

    lang_hint = ""
    if language == "zh":
        lang_hint = "请用中文输出摘要。"
    elif language == "en":
        lang_hint = "Please write the summary in English."

    if len(chunks) == 1:
        prompt = (
            f"{lang_hint}\n请阅读下面这段内容并产出一段不超过 {max_chars} 字的摘要，"
            f"保留关键事实、人物、数字、结论。\n\n{text}"
        )
        return _llm_invoke(prompt).strip()

    partials: List[str] = []
    for i, c in enumerate(chunks):
        p = (
            f"{lang_hint}\n"
            f"请将下面【第 {i + 1}/{len(chunks)} 段】内容浓缩成一段不超过 {max_chars // max(1, len(chunks))} 字的摘要，"
            f"保留关键事实、数字、结论。\n\n{c}"
        )
        s = _llm_invoke(p).strip()
        if s:
            partials.append(s)

    combined = "\n".join(partials)
    final_prompt = (
        f"{lang_hint}\n"
        f"以下是对同一篇长文各段的摘要。请合并并去重，再产出一段不超过 {max_chars} 字的整体摘要，"
        f"突出主要论点、关键事实、结论。\n\n{combined}"
    )
    return _llm_invoke(final_prompt).strip()


def translate(text: str, target_language: str, source_language: str = "auto") -> str:
    chunks = chunk_text(text, chunk_size=2000, overlap=100)
    if not chunks:
        return ""
    out: List[str] = []
    src_hint = f"（源语言：{source_language}）" if source_language and source_language != "auto" else ""
    for c in chunks:
        prompt = (
            f"请将下面这段文字翻译成{target_language}{src_hint}。"
            f"保持原文格式与代码块/数字/链接不被修改，只翻译自然语言。\n\n{c}"
        )
        s = _llm_invoke(prompt).strip()
        if s:
            out.append(s)
    return "\n\n".join(out)


_QA_ANSWER_PROMPT = """你将基于下面【长文本内容】回答用户的【问题】。要求：
1) 严格根据长文本回答，不要引入外部知识；
2) 如果长文本中找不到答案，直接说"长文中未提供该信息"；
3) 回答末尾用 [段落 n] 的形式标注引用了第几段。

【长文本内容】
{context}

【问题】
{question}
"""


def answer_against_text(text: str, question: str) -> str:
    chunks = chunk_text(text, chunk_size=1500, overlap=200)
    if not chunks:
        return "（无内容）"

    relevant_idx = list(range(len(chunks)))
    if len(chunks) > 6:
        try:
            from app.rag.vectorstore import tokenize_query
            q_tokens = set(tokenize_query(question))
            scored = []
            for i, c in enumerate(chunks):
                t = set(tokenize_query(c))
                if not t or not q_tokens:
                    scored.append((i, 0.0))
                else:
                    scored.append((i, len(t & q_tokens) / len(q_tokens | t)))
            scored.sort(key=lambda x: x[1], reverse=True)
            relevant_idx = [i for i, _ in scored[:6]]
            relevant_idx.sort()
        except Exception as e:
            logger.warning(f"Relevance-based chunk selection failed: {e}")

    context = "\n\n---\n\n".join(f"[段落 {i + 1}]\n{chunks[i]}" for i in relevant_idx)
    return _llm_invoke(_QA_ANSWER_PROMPT.format(context=context, question=question)).strip()


def detect_language_hint(text: str) -> str:
    if not text:
        return "auto"
    cjk = sum(1 for c in text if "\u4e00" <= c <= "\u9fff")
    ascii_letters = sum(1 for c in text if c.isascii() and c.isalpha())
    if cjk > ascii_letters:
        return "zh"
    if ascii_letters > 0:
        return "en"
    return "auto"
