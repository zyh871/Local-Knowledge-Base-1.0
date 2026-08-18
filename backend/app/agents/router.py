"""
Dialogue router — decides which handler a user turn should go to and
returns a unified result dict.

Handlers:
  * kb_qa        — RAG over local knowledge base
  * general      — Direct LLM answer (world knowledge)
  * long_text    — Operate on the long text the user pasted in this turn
  * file_qa      — Filter the vector store by a specific filename
  * task         — Direct LLM with no retrieval (for "write/translate/...")
  * clarification_needed — Echo a clarification prompt back to the user
"""

import logging
import os
import re
from typing import Any, Dict, List, Optional

from app.agents.intent_classifier import (
    INTENT_AGENT,
    INTENT_CLARIFY,
    INTENT_FILE_QA,
    INTENT_GENERAL,
    INTENT_KB_QA,
    INTENT_LONG_TEXT,
    INTENT_TASK,
    IntentResult,
    build_clarification_message,
)
from app.memory import get_profile_context, is_locked, load_history, save_context
from app.memory.user_profile import get_profile
from app.rag_chain import (
    RAGChain,
    format_docs,
    get_sources,
    get_vectorstore,
    hybrid_search,
)
from app.tools.web_search import build_search_context, search as web_search, should_search

logger = logging.getLogger(__name__)


def _extract_text(obj) -> str:
    """
    Safely pull plain text from a LangChain AIMessage / AIMessageChunk /
    str / dict-like chunk.

    Rules:
      * str -> as-is
      * has `.content` (non-empty) -> return `.content`
      * has `.content` (empty/None) AND `.tool_calls` -> return "" (skip)
      * has `.content` (empty/None) AND `.additional_kwargs` and
        no obvious text-bearing fields -> return ""
      * dict with "content"/"text" key -> return that
      * anything else -> return ""
    Never return `str(obj)` so internal repr never leaks to the UI.
    """
    if obj is None:
        return ""
    if isinstance(obj, str):
        return obj
    if isinstance(obj, dict):
        v = obj.get("content") or obj.get("text") or obj.get("answer") or obj.get("output_text")
        return v if isinstance(v, str) else ""

    content = getattr(obj, "content", None)
    if isinstance(content, str) and content:
        return content
    if isinstance(content, list):
        parts: List[str] = []
        for p in content:
            if isinstance(p, str):
                parts.append(p)
            elif isinstance(p, dict) and isinstance(p.get("text"), str):
                parts.append(p["text"])
        return "".join(parts)

    if hasattr(obj, "tool_calls") and getattr(obj, "tool_calls"):
        return ""

    additional = getattr(obj, "additional_kwargs", None) or {}
    if additional and not content:
        return ""

    # Last resort: try other common attributes
    for attr in ("text", "answer", "output_text"):
        v = getattr(obj, attr, None)
        if isinstance(v, str) and v:
            return v

    return ""


_LANG_INSTRUCTION_ZH = "请用中文回答。"
_LANG_INSTRUCTION_EN = "Please answer in English."


def _lang_instruction(profile_lang: str, detected_lang: str) -> str:
    lang = (profile_lang or "").lower() if profile_lang else ""
    if lang in ("zh", "zh-cn", "chinese", "中文"):
        return _LANG_INSTRUCTION_ZH
    if lang in ("en", "english"):
        return _LANG_INSTRUCTION_EN
    if detected_lang == "zh":
        return _LANG_INSTRUCTION_ZH
    if detected_lang == "en":
        return _LANG_INSTRUCTION_EN
    return "请使用与用户输入相同的语言回答。 / Please answer in the same language as the user."


def _format_history(history: List[Dict[str, str]]) -> str:
    if not history:
        return "（无）"
    lines: List[str] = []
    for m in history[-10:]:
        role = "用户" if m.get("role") == "user" else "助手"
        lines.append(f"{role}: {m.get('content', '')}")
    return "\n".join(lines)


def _extract_filename(question: str) -> Optional[str]:
    """Pull a filename out of a question if present, with path traversal protection."""
    m = re.search(r"([\w\u4e00-\u9fa5\-_.]+\.(?:pdf|docx?|pptx?|xlsx?|txt|md|csv))", question, re.IGNORECASE)
    if not m:
        return None
    filename = m.group(1)
    # 路径遍历防护：只保留基本文件名
    filename = os.path.basename(filename)
    # 过滤危险字符
    if ".." in filename or filename.startswith(".") or "/" in filename or "\\" in filename:
        logger.warning(f"Blocked suspicious filename pattern: {filename}")
        return None
    return filename


def _handle_clarification(_question: str, intent_res: IntentResult) -> Dict[str, Any]:
    return {
        "answer": build_clarification_message(intent_res),
        "sources": [],
        "data_source": "clarification",
        "intent": INTENT_CLARIFY,
        "needs_clarification": True,
    }


def _handle_general(question: str, history: List[Dict[str, str]], chain: RAGChain, lang_instruction: str, force_search: bool = False) -> Dict[str, Any]:
    use_search = force_search or should_search(question)
    search_context = ""
    search_sources: List[Dict[str, str]] = []

    if use_search:
        try:
            logger.info(f"Triggering web search for: {question[:50]}")
            sr = web_search(question, max_results=5, timeout=15)
            if sr.get("success"):
                search_context, search_sources = build_search_context(sr)
                logger.debug(f"Search context length: {len(search_context)}, sources: {len(search_sources)}")
            else:
                logger.debug(f"Search returned no results: {sr.get('error')}")
        except Exception as e:
            logger.warning(f"Web search failed: {e}")

    context_block = ""
    if search_context:
        context_block = f"\n【实时搜索结果】\n{search_context}\n\n"

    try:
        answer = chain._llm.invoke(
            f"{lang_instruction}\n\n"
            f"你是用户的智能助手。基于你的知识和{context_block or '可用信息'}回答问题。"
            f"如果使用了搜索结果，请在回答末尾用[来源: xxx]标注链接。\n\n"
            f"对话历史：\n{_format_history(history)}\n\n"
            f"问题：{question}"
        )
        if hasattr(answer, "content"):
            answer = _extract_text(answer)
    except Exception as e:
        logger.error(f"General handler failed: {e}")
        answer = "抱歉，我现在无法回答这个问题。"

    data_source = "web_search" if search_sources else "general"
    return {"answer": answer, "sources": search_sources, "data_source": data_source, "intent": INTENT_GENERAL}


def _handle_task(question: str, history: List[Dict[str, str]], chain: RAGChain, lang_instruction: str) -> Dict[str, Any]:
    try:
        answer = chain._llm.invoke(
            f"{lang_instruction}\n\n"
            f"你是一位高质量的写作 / 翻译 / 总结助手。请直接完成用户的任务，"
            f"不要解释你打算做什么。\n\n"
            f"对话历史：\n{_format_history(history)}\n\n"
            f"任务：{question}"
        )
        if hasattr(answer, "content"):
            answer = _extract_text(answer)
    except Exception as e:
        logger.error(f"Task handler failed: {e}")
        answer = "抱歉，任务执行失败。"
    return {"answer": answer, "sources": [], "data_source": "task", "intent": INTENT_TASK}


def _handle_long_text(question: str, lang_instruction: str) -> Dict[str, Any]:
    """Operate on the long text the user just pasted."""
    from app.document.long_text import (
        answer_against_text,
        detect_language_hint,
        summarize,
        translate,
    )

    detected = detect_language_hint(question)
    text = question
    instr = (question or "").strip()

    # Decide operation based on instruction prefix or a one-sentence hint
    op_match = re.search(r"^(请)?(总结|摘要|概括|翻译|translate|summarize|summary)", instr, re.IGNORECASE)
    if op_match:
        # Strip the instruction and the trailing colon
        rest = instr[op_match.end():].lstrip(" :：")
        if op_match.group(2).lower().startswith(("translate", "翻译")):
            target = rest.strip() or "English"
            result = translate(text, target_language=target, source_language=detected)
            return {
                "answer": result or "（无内容）",
                "sources": [],
                "data_source": "long_text_translate",
                "intent": INTENT_LONG_TEXT,
            }
        if op_match.group(2).lower().startswith(("summarize", "summary", "总结", "摘要", "概括")):
            result = summarize(text, language=detected, max_chars=600)
            return {
                "answer": result or "（无内容）",
                "sources": [],
                "data_source": "long_text_summary",
                "intent": INTENT_LONG_TEXT,
            }

    # Default: treat the user's long text as context and try to answer the first question in it
    parts = re.split(r"[?？\n]", instr, maxsplit=1)
    if len(parts) == 2 and parts[0].strip():
        q = parts[0].strip()
        ctx = parts[1].strip() or text
        result = answer_against_text(ctx, q)
    else:
        result = summarize(text, language=detected, max_chars=600)
    return {
        "answer": result or "（无内容）",
        "sources": [],
        "data_source": "long_text",
        "intent": INTENT_LONG_TEXT,
    }


def _handle_file_qa(question: str, chain: RAGChain, lang_instruction: str, history: List[Dict[str, str]]) -> Dict[str, Any]:
    filename = _extract_filename(question)
    if not filename:
        return _handle_general(question, history, chain, lang_instruction)

    vs = get_vectorstore()
    try:
        all_docs = vs.similarity_search(question, k=config_topk(20))
    except Exception as e:
        logger.error(f"file_qa retrieval failed: {e}")
        all_docs = []
    matched = [d for d in all_docs if d.metadata.get("source", "").lower() == filename.lower()]
    if not matched:
        matched = [d for d in all_docs if filename.lower() in d.metadata.get("source", "").lower()]
    if not matched:
        return {
            "answer": f"未在知识库中找到文件 {filename}，请确认文件已上传。",
            "sources": [],
            "data_source": "file_qa_miss",
            "intent": INTENT_FILE_QA,
        }

    matched = matched[: config_topk(5)]
    context = format_docs(matched)
    sources = get_sources(matched)
    try:
        answer = chain._llm.invoke(
            f"{lang_instruction}\n\n"
            f"你将基于【文件 {filename} 的内容】回答用户问题。"
            f"如果内容不足，直接说明。\n\n"
            f"文件内容：\n{context}\n\n"
            f"问题：{question}"
        )
        if hasattr(answer, "content"):
            answer = _extract_text(answer)
    except Exception as e:
        logger.error(f"file_qa LLM failed: {e}")
        answer = "（文件问答生成失败）"
    return {"answer": answer, "sources": sources, "data_source": "file_qa", "intent": INTENT_FILE_QA}


def config_topk(n: int) -> int:
    """Return the max between requested n and RETRIEVER_K so callers can overshoot."""
    from app import config as cfg
    return max(n, getattr(cfg, "RETRIEVER_K", n))


def _handle_kb_qa(question: str, history: List[Dict[str, str]], chain: RAGChain, lang_instruction: str, profile_ctx: str, force_search: bool = False) -> Dict[str, Any]:
    """Standard RAG over the knowledge base, with user profile + chat history."""
    docs = hybrid_search(question)
    search_context = ""
    search_sources: List[Dict[str, str]] = []

    use_search = force_search or should_search(question)
    if use_search:
        try:
            logger.info(f"KB QA triggering web search for: {question[:50]}")
            sr = web_search(question, max_results=5, timeout=15)
            if sr.get("success"):
                search_context, search_sources = build_search_context(sr)
                logger.debug(f"Search context length: {len(search_context)}, sources: {len(search_sources)}")
        except Exception as e:
            logger.warning(f"KB QA web search failed: {e}")

    context_parts = []
    if docs:
        context_parts.append(f"【知识库内容】\n{format_docs(docs)}")
    if search_context:
        context_parts.append(f"【实时搜索结果】\n{search_context}")

    if not context_parts:
        return _handle_general(question, history, chain, lang_instruction, force_search=force_search)

    context = "\n\n".join(context_parts)
    sources = get_sources(docs) if docs else []
    sources.extend(search_sources)

    prompt = (
        f"{lang_instruction}\n"
        f"{profile_ctx}\n\n"
        f"你是用户的智能助手。"
        f"基于【本地知识库】和【实时搜索结果】回答问题。"
        f"如果使用了搜索结果，请在回答末尾用[来源: xxx]标注链接。\n\n"
        f"对话历史：\n{_format_history(history)}\n\n"
        f"参考资料：\n{context}\n\n"
        f"问题：{question}"
    )

    try:
        answer = chain._llm.invoke(prompt)
        if hasattr(answer, "content"):
            answer = _extract_text(answer)
    except Exception as e:
        logger.error(f"kb_qa LLM failed: {e}")
        return {
            "answer": f"知识库检索完成，但生成失败：{e}",
            "sources": sources,
            "data_source": "rag_error",
            "intent": INTENT_KB_QA,
        }

    data_source = "rag_search" if search_sources else "rag"
    return {"answer": answer, "sources": sources, "data_source": data_source, "intent": INTENT_KB_QA}


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def _handle_agent(question: str, history: List[Dict[str, str]], lang_instruction: str) -> Dict[str, Any]:
    """使用 Function Calling Agent 处理需要多步推理或工具调用的复杂问题。"""
    from app.agents.function_calling import run_function_calling_agent

    try:
        result = run_function_calling_agent(question, history=history)
        sources = []
        for tc in result.get("tool_calls", []):
            if tc.get("tool") == "web_search":
                sources.append({"source": "tavily", "tool_call": tc["tool"]})
            elif tc.get("tool") == "knowledge_query":
                sources.append({"source": "knowledge_base", "tool_call": tc["tool"]})
        return {
            "answer": result["answer"],
            "sources": sources,
            "data_source": "agent",
            "intent": INTENT_AGENT,
            "tool_calls": result.get("tool_calls", []),
            "iterations": result.get("iterations", 0),
        }
    except Exception as e:
        logger.error(f"Agent handler failed: {e}", exc_info=True)
        return {
            "answer": f"Agent 处理失败，回退到普通问答: {e}",
            "sources": [],
            "data_source": "agent_error",
            "intent": INTENT_AGENT,
        }


def route(
    chain: RAGChain,
    question: str,
    intent_res: IntentResult,
    username: str = "",
    conversation_id: str = "default",
    force_search: bool = False,
) -> Dict[str, Any]:
    """Route `question` to the right handler and return a result dict."""
    chain.ensure_initialized()
    locked = is_locked(conversation_id)
    history = load_history(conversation_id)
    profile = get_profile(username) if username else {}
    lang_instruction = _lang_instruction(profile.get("language", "auto"), intent_res.detected_language)
    profile_ctx = get_profile_context(username)

    if intent_res.intent == INTENT_CLARIFY:
        result = _handle_clarification(question, intent_res)
    elif intent_res.intent == INTENT_GENERAL:
        result = _handle_general(question, history, chain, lang_instruction, force_search=force_search)
    elif intent_res.intent == INTENT_TASK:
        result = _handle_task(question, history, chain, lang_instruction)
    elif intent_res.intent == INTENT_LONG_TEXT:
        result = _handle_long_text(question, lang_instruction)
    elif intent_res.intent == INTENT_FILE_QA:
        result = _handle_file_qa(question, chain, lang_instruction, history)
    elif intent_res.intent == INTENT_AGENT:
        result = _handle_agent(question, history, lang_instruction)
    else:
        result = _handle_kb_qa(question, history, chain, lang_instruction, profile_ctx, force_search=force_search)

    if not intent_res.needs_clarification:
        try:
            save_context(conversation_id, question, result["answer"], locked=locked)
        except Exception as e:
            logger.warning(f"save_context failed: {e}")

    result["intent"] = intent_res.intent
    result["intent_confidence"] = intent_res.confidence
    result["detected_language"] = intent_res.detected_language
    return result


def route_stream(
    chain: RAGChain,
    question: str,
    intent_res: IntentResult,
    username: str = "",
    conversation_id: str = "default",
    force_search: bool = False,
):
    """
    Streaming variant of route(). Yields SSE event dicts.
    Falls back to the non-streaming route() for non-RAG handlers.
    """
    chain.ensure_initialized()
    locked = is_locked(conversation_id)
    history = load_history(conversation_id)
    profile = get_profile(username) if username else {}
    lang_instruction = _lang_instruction(profile.get("language", "auto"), intent_res.detected_language)
    profile_ctx = get_profile_context(username)

    sources: list = []

    if intent_res.intent in (INTENT_CLARIFY, INTENT_LONG_TEXT):
        result = route(chain, question, intent_res, username=username, conversation_id=conversation_id)
        yield {"type": "intent", "content": {"intent": result["intent"], "confidence": result.get("intent_confidence")}}
        yield {"type": "chunk", "content": result["answer"]}
        yield {"type": "final", "content": result}
        return

    if intent_res.intent == INTENT_FILE_QA:
        result = _handle_file_qa(question, chain, lang_instruction, history)
        yield {"type": "intent", "content": {"intent": result["intent"], "confidence": intent_res.confidence}}
        yield {"type": "sources", "content": result.get("sources", [])}
        yield {"type": "chunk", "content": result["answer"]}
        yield {"type": "final", "content": result}
        if not intent_res.needs_clarification:
            save_context(conversation_id, question, result["answer"], locked=locked)
        return

    if intent_res.intent == INTENT_AGENT:
        result = _handle_agent(question, history, lang_instruction)
        yield {"type": "intent", "content": {"intent": INTENT_AGENT, "confidence": intent_res.confidence}}
        yield {"type": "sources", "content": result.get("sources", [])}
        if result.get("tool_calls"):
            yield {"type": "tool_calls", "content": result["tool_calls"]}
        yield {"type": "chunk", "content": result["answer"]}
        yield {"type": "final", "content": result}
        if not intent_res.needs_clarification:
            save_context(conversation_id, question, result["answer"], locked=locked)
        return

    # For KB_QA / GENERAL / TASK we stream the LLM response.
    sources: List[Any] = []
    if intent_res.intent == INTENT_KB_QA:
        docs = hybrid_search(question)
        search_context = ""
        search_sources: List[Dict[str, str]] = []
        use_search = force_search or should_search(question)
        if use_search:
            try:
                logger.info(f"Streaming KB QA triggering web search for: {question[:50]}")
                sr = web_search(question, max_results=5, timeout=15)
                if sr.get("success"):
                    search_context, search_sources = build_search_context(sr)
            except Exception as e:
                logger.warning(f"Streaming KB QA web search failed: {e}")

        if not docs and not search_context:
            intent_res.intent = INTENT_GENERAL
            result = _handle_general(question, history, chain, lang_instruction, force_search=force_search)
            yield {"type": "intent", "content": {"intent": result["intent"], "confidence": intent_res.confidence}}
            yield {"type": "sources", "content": result.get("sources", [])}
            yield {"type": "chunk", "content": result["answer"]}
            yield {"type": "final", "content": result}
            if not intent_res.needs_clarification:
                save_context(conversation_id, question, result["answer"], locked=locked)
            return

        context_parts = []
        if docs:
            context_parts.append(f"【知识库内容】\n{format_docs(docs)}")
            sources = get_sources(docs)
        if search_context:
            context_parts.append(f"【实时搜索结果】\n{search_context}")
            sources.extend(search_sources)

        context = "\n\n".join(context_parts)
        prompt = (
            f"{lang_instruction}\n{profile_ctx}\n\n"
            f"你是用户的智能助手。"
            f"基于【本地知识库】和【实时搜索结果】回答问题。"
            f"如果使用了搜索结果，请在回答末尾用[来源: xxx]标注链接。\n\n"
            f"对话历史：\n{_format_history(history)}\n\n"
            f"参考资料：\n{context}\n\n"
            f"问题：{question}"
        )
        yield {"type": "intent", "content": {"intent": INTENT_KB_QA, "confidence": intent_res.confidence}}
        yield {"type": "sources", "content": sources}
    elif intent_res.intent == INTENT_GENERAL:
        search_context = ""
        search_sources: List[Dict[str, str]] = []
        use_search = force_search or should_search(question)
        if use_search:
            try:
                logger.info(f"Streaming: triggering web search for: {question[:50]}")
                sr = web_search(question, max_results=5, timeout=15)
                if sr.get("success"):
                    search_context, search_sources = build_search_context(sr)
            except Exception as e:
                logger.warning(f"Streaming search failed: {e}")

        context_block = f"\n【实时搜索结果】\n{search_context}\n\n" if search_context else ""
        prompt = (
            f"{lang_instruction}\n"
            f"你是用户的智能助手。基于你的知识和{context_block or '可用信息'}回答问题。"
            f"如果使用了搜索结果，请在回答末尾用[来源: xxx]标注链接。\n\n"
            f"对话历史：\n{_format_history(history)}\n\n"
            f"问题：{question}"
        )
        yield {"type": "intent", "content": {"intent": INTENT_GENERAL, "confidence": intent_res.confidence}}
        sources = search_sources
        yield {"type": "sources", "content": sources}
    else:  # INTENT_TASK
        prompt = (
            f"{lang_instruction}\n"
            f"你是写作/翻译/总结助手，请直接完成任务，不要解释你将做什么。\n\n"
            f"对话历史：\n{_format_history(history)}\n\n"
            f"任务：{question}"
        )
        yield {"type": "intent", "content": {"intent": INTENT_TASK, "confidence": intent_res.confidence}}
        yield {"type": "sources", "content": sources}

    full = ""
    try:
        for chunk in chain._llm.stream(prompt):
            text = _extract_text(chunk)
            if not text:
                continue
            full += text
            yield {"type": "chunk", "content": text}
    except Exception as e:
        logger.warning(f"LLM stream failed, switching to invoke: {e}")
        try:
            ans = chain._llm.invoke(prompt)
            text = _extract_text(ans)
            if text:
                full += text
                yield {"type": "chunk", "content": text}
        except Exception as fe:
            logger.error(f"LLM invoke fallback failed: {fe}")
            full = "（生成失败）"
            yield {"type": "chunk", "content": full}

    if not intent_res.needs_clarification:
        save_context(conversation_id, question, full, locked=locked)

    # 确定正确的 data_source
    if intent_res.intent == INTENT_KB_QA:
        has_web_sources = any(isinstance(s, dict) and s.get("source") == "tavily" for s in sources) if sources else False
        data_source = "rag_search" if has_web_sources else "rag"
    elif intent_res.intent == INTENT_GENERAL and sources:
        data_source = "web_search"
    else:
        data_source = intent_res.intent

    yield {
        "type": "final",
        "content": {
            "answer": full,
            "sources": sources,
            "data_source": data_source,
            "intent": intent_res.intent,
            "intent_confidence": intent_res.confidence,
            "detected_language": intent_res.detected_language,
        },
    }
