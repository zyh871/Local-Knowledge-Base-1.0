"""
Long-term memory — extract and persist stable user facts from conversation turns.

Heuristics:
  1. Match explicit "I am / my name is / I work as / call me X" patterns.
  2. Match preference patterns "I prefer / I like / I don't like / always use X".
  3. Hand any unmatched user turn to the LLM with a strict extraction prompt.

Extracted items are appended to the user profile via `user_profile.update_profile`.
"""

import logging
import re
from typing import List, Optional

from app.memory import user_profile

logger = logging.getLogger(__name__)

# 注意：不要在此模块顶层 `from app.rag.providers import ...`。
# app.rag 会 import app.memory（会话记忆），顶层导入会形成
# app.rag <-> app.memory 的循环依赖（表现为
# `ImportError: cannot import name 'get_llm' from partially initialized module`）。
# LLM 仅在抽取事实时才需要，故改为函数内延迟导入。

_NAME_PATTERNS = [
    r"(?:我叫|我的名字是|我是|叫我|you can call me|my name is|i am|i'm)\s*([A-Za-z\u4e00-\u9fa5]{2,20})",
]
_PROFESSION_PATTERNS = [
    r"(?:我是一名?|我是做|我的职业是|我的工作是|i work as|i am a|i'm a|my job is)\s*([A-Za-z\u4e00-\u9fa5·\-\s]{2,30})",
]
_PREFERENCE_PATTERNS = [
    r"(?:我喜欢|我更喜欢|我偏好|我习惯|i (?:like|prefer|love|always use))\s+([^.。!?\n]{2,40})",
    r"(?:我不喜欢|我讨厌|i (?:don't like|hate|dislike))\s+([^.。!?\n]{2,40})",
]


def _match_first(patterns: List[str], text: str) -> Optional[str]:
    for p in patterns:
        m = re.search(p, text, re.IGNORECASE)
        if m:
            return m.group(1).strip()
    return None


def _rule_based_extract(text: str) -> dict:
    """Return a dict of {display_name, profession, language, facts, preferences} from one turn."""
    extracted = {"facts": [], "preferences": {}}

    name = _match_first(_NAME_PATTERNS, text)
    if name:
        extracted["display_name"] = name

    prof = _match_first(_PROFESSION_PATTERNS, text)
    if prof:
        extracted["profession"] = prof

    for p in _PREFERENCE_PATTERNS:
        for m in re.finditer(p, text, re.IGNORECASE):
            value = m.group(1).strip()
            if not value:
                continue
            key = "dislikes" if "不" in m.group(0) or "don't" in m.group(0).lower() or "hate" in m.group(0).lower() or "dislike" in m.group(0).lower() else "likes"
            existing = extracted["preferences"].get(key, [])
            if isinstance(existing, list):
                existing.append(value)
            else:
                extracted["preferences"][key] = [existing, value]
            extracted["preferences"][key] = list(dict.fromkeys(extracted["preferences"][key]))

    return extracted


_EXTRACTION_PROMPT = """你是一个用户画像抽取助手。请从下面这条用户消息中提取**稳定**的个人信息。
只抽取"会长期不变"的事实，例如：姓名、昵称、职业、所在城市、母语、长期偏好、过敏、重要人物关系等。
**不要**抽取临时性事件（今天的会议、刚才的搜索等）。
**不要**编造信息。无法判断则保持空。

输出严格的 JSON：
{{
  "display_name": "...",
  "profession": "...",
  "language": "...",   // 仅在用户明确提及偏好语言时填写
  "facts": ["...", "..."],
  "preferences": {{"key": "value"}}
}}

用户消息："""


def _llm_extract(text: str) -> dict:
    """Use the LLM to extract stable facts from one user turn."""
    try:
        from app.rag.providers import get_llm, get_query_llm

        llm = get_query_llm() or get_llm()
    except Exception:
        logger.warning("LLM not available for fact extraction; falling back to rules.")
        return {}

    if llm is None:
        return {}

    try:
        resp = llm.invoke(_EXTRACTION_PROMPT + text)
        content = getattr(resp, "content", str(resp))
    except Exception as e:
        logger.warning(f"LLM extraction failed: {e}")
        return {}

    return _safe_parse_json(content)


def _safe_parse_json(content: str) -> dict:
    if not content:
        return {}
    start = content.find("{")
    end = content.rfind("}")
    if start < 0 or end < 0 or end <= start:
        return {}
    import json
    try:
        return json.loads(content[start:end + 1])
    except Exception:
        return {}


def remember_turn(username: Optional[str], user_text: str) -> dict:
    """
    Extract stable facts from `user_text` and merge into the user's profile.
    Returns the profile after the merge.
    """
    if not username or not user_text or not user_text.strip():
        return user_profile.get_profile(username or "")

    extracted = _rule_based_extract(user_text)
    if not extracted.get("display_name") and not extracted.get("profession") and not extracted.get("preferences"):
        extracted = _llm_extract(user_text)

    cleaned = {k: v for k, v in extracted.items() if v}
    if not cleaned:
        return user_profile.get_profile(username)

    return user_profile.update_profile(username, cleaned)


def get_profile_context(username: Optional[str]) -> str:
    """
    Build a short text block describing what we know about the user.
    Injected into the system prompt for personalization.
    """
    if not username:
        return ""
    p = user_profile.get_profile(username)
    if not p:
        return ""

    lines = []
    if p.get("display_name"):
        lines.append(f"用户姓名/称呼: {p['display_name']}")
    if p.get("profession"):
        lines.append(f"用户职业: {p['profession']}")
    if p.get("language") and p["language"] not in ("", "auto"):
        lines.append(f"用户偏好语言: {p['language']}")
    facts = p.get("facts") or []
    if facts:
        fact_strs = []
        for f in facts[:10]:
            if isinstance(f, dict):
                fact_strs.append(str(f.get("fact") or ""))
            else:
                fact_strs.append(str(f))
        fact_strs = [s for s in fact_strs if s]
        if fact_strs:
            lines.append("已知事实: " + "；".join(fact_strs))
    prefs = p.get("preferences") or {}
    if prefs:
        pretty = "；".join(f"{k}={v}" for k, v in prefs.items())
        lines.append("用户偏好: " + pretty)
    if not lines:
        return ""
    return "\n[用户画像]\n" + "\n".join(lines)
