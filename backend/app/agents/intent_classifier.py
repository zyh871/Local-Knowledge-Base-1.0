"""
Intent classifier + clarification policy.

Detects the user's intent for a single turn and decides whether the
assistant should ask a clarifying question or proceed.

Supported intents:
  * kb_qa        — Question over the user's knowledge base (default).
  * general      — General-domain chat / chitchat / world knowledge.
  * long_text    — User pasted a long document and wants summary/QA/translation.
  * file_qa      — User is asking about a specific uploaded file (referenced
                   by filename or marked as attached).
  * task         — Explicit action request ("write a poem", "translate ...",
                   "summarize this", "give me steps to ...").
  * clarification_needed — Query is too ambiguous; ask for clarification.

The classifier is rule-first (cheap) and falls back to an LLM call only
when the rules produce no confident match.
"""

import logging
import re
from dataclasses import dataclass, field, asdict
from typing import List, Optional

logger = logging.getLogger(__name__)


# ----------------------------- Intent constants -----------------------------

INTENT_KB_QA = "kb_qa"
INTENT_GENERAL = "general"
INTENT_LONG_TEXT = "long_text"
INTENT_FILE_QA = "file_qa"
INTENT_TASK = "task"
INTENT_AGENT = "agent"
INTENT_CLARIFY = "clarification_needed"

ALL_INTENTS = [
    INTENT_KB_QA,
    INTENT_GENERAL,
    INTENT_LONG_TEXT,
    INTENT_FILE_QA,
    INTENT_TASK,
    INTENT_AGENT,
    INTENT_CLARIFY,
]

LONG_TEXT_THRESHOLD = 800  # characters; above this we treat as long text


# ----------------------------- Data model ----------------------------------

@dataclass
class IntentResult:
    intent: str = INTENT_KB_QA
    confidence: float = 0.0
    needs_clarification: bool = False
    clarification_prompt: str = ""
    detected_language: str = "auto"
    rewrite: str = ""
    reasons: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


# ----------------------------- Helpers -------------------------------------

_GENERAL_PATTERNS = [
    r"^(你好|您好|hi|hello|hey|嗨)\b",
    r"^(你是谁|你叫什么|介绍下?你自己)\b",
    r"^(谢谢|thanks|thank you)\b",
]

_LIVE_INFO_PATTERNS = [
    r"(天气|气温|温度|pm2\.5|空气质量|雾霾|降雨|风速|湿度)\b",
    r"((今天|现在|当前|最新|最近|今日|实时|此刻|现在的)\s*.*(情况|数据|价格|行情|新闻|报道|事件|消息|动态|走势|比分|赛果|汇率|金价|油价|股价|股票))",
    r"\b(weather|temperature|news|stock price|latest|breaking)\b",
]
_TASK_PATTERNS = [
    r"^(帮我|请|please)\s*(写|翻译|总结|概括|润色|改写|生成|列|做)\b",
    r"\b(translate|summary|summarize|rewrite|generate|write me)\b",
    r"^(写一?首|写一篇|给我一个)\b",
]
_FILE_PATTERNS = [
    r"(?:文件|文档|资料|附件|上传的|我刚才上传|attachment|file|document)\s*[\w\u4e00-\u9fa5\-_.]+\.\w{1,5}",
    r"[\w\u4e00-\u9fa5\-_.]+\.(pdf|docx?|pptx?|txt|md|xlsx?|csv)\b",
]
_AGENT_PATTERNS = [
    r"(?:先.*再.*然后|第一步|分步骤|逐步|多步)",
    r"(?:调研|对比.*和|分析.*并.*总结|查.*再.*算)",
    r"(?:使用工具|调用工具|用计算器|执行代码)",
    r"(?:计算.*并.*搜索|查.*知识库.*再.*搜索)",
]
_CLARIFY_PATTERNS = [
    r"^(那个|这个|它|他|她|上面|下面|之前|刚才)$",
    r"^(?!\s*$).{0,4}$",  # 0~4 chars, very short
]


def _match_any(patterns, text: str) -> bool:
    for p in patterns:
        if re.search(p, text, re.IGNORECASE | re.MULTILINE):
            return True
    return False


def _detect_language(text: str) -> str:
    """Quick heuristic: zh if many CJK chars, else en."""
    if not text:
        return "auto"
    cjk = sum(1 for c in text if "\u4e00" <= c <= "\u9fff")
    ascii_letters = sum(1 for c in text if c.isascii() and c.isalpha())
    if cjk > ascii_letters:
        return "zh"
    if ascii_letters > 0:
        return "en"
    return "auto"


def _is_long_text(text: str) -> bool:
    return len(text or "") >= LONG_TEXT_THRESHOLD


def _needs_clarification(text: str, has_history: bool) -> bool:
    stripped = (text or "").strip()
    if not stripped:
        return True
    if len(stripped) <= 4 and not has_history:
        return True
    if _match_any(_CLARIFY_PATTERNS, stripped):
        return True
    return False


# ----------------------------- Public API ---------------------------------

_CLASSIFY_PROMPT = """你是一个意图分类器。下面给出一段用户输入和它所处的对话上下文（可能为空）。
请从以下 7 个意图中**只选一个**最匹配的输出：

1. kb_qa               — 询问本地知识库中的资料
2. general             — 闲聊、问候、自我介绍、常识问答
3. long_text           — 用户贴了一段很长的内容（>=800字），希望摘要/翻译/分析
4. file_qa             — 用户引用了具体文件名（xxx.pdf / xxx.docx 等）询问其中内容
5. task                — 明确要求 AI 写/翻译/总结/改写/生成内容
6. agent               — 需要多步推理、使用工具（计算器/代码执行/知识库查询/联网搜索）的复杂问题
7. clarification_needed — 信息明显不足，需要向用户反问澄清

输出严格的 JSON：
{{"intent": "<one of the seven>", "confidence": 0~1, "reason": "..."}}

用户输入："""


def classify(
    user_text: str,
    conversation_history: Optional[List[dict]] = None,
    llm_call=None,
) -> IntentResult:
    """
    Classify a user turn. `llm_call(text) -> str` is an optional
    callable for LLM-based classification (defaults to a no-op).
    """
    text = (user_text or "").strip()
    history = conversation_history or []
    has_history = bool(history)

    res = IntentResult()
    res.detected_language = _detect_language(text)
    res.reasons.append(f"lang={res.detected_language}, len={len(text)}")

    # 1. Long text first — if user pasted a wall of text we treat it specially
    if _is_long_text(text):
        res.intent = INTENT_LONG_TEXT
        res.confidence = 0.95
        res.reasons.append("text length >= long-text threshold")
        return res

    # 2. Rule-based short-circuits
    if _match_any(_GENERAL_PATTERNS, text):
        res.intent = INTENT_GENERAL
        res.confidence = 0.9
        res.reasons.append("matched general pattern")
        return res

    if _match_any(_LIVE_INFO_PATTERNS, text):
        res.intent = INTENT_GENERAL
        res.confidence = 0.88
        res.reasons.append("matched live info pattern -> general for web search")
        return res

    if _match_any(_TASK_PATTERNS, text):
        res.intent = INTENT_TASK
        res.confidence = 0.9
        res.reasons.append("matched task pattern")
        return res

    if _match_any(_FILE_PATTERNS, text):
        res.intent = INTENT_FILE_QA
        res.confidence = 0.85
        res.reasons.append("matched filename pattern")
        return res

    if _match_any(_AGENT_PATTERNS, text):
        res.intent = INTENT_AGENT
        res.confidence = 0.85
        res.reasons.append("matched multi-step / tool-use pattern")
        return res

    # 3. Clarification check
    if _needs_clarification(text, has_history):
        # In a continuing conversation a pronoun alone is fine — the memory
        # supplies the referent. Only flag when there's no usable context.
        res.intent = INTENT_CLARIFY
        res.confidence = 0.85
        res.needs_clarification = True
        res.clarification_prompt = (
            "你的问题有点简短，我不确定你想问什么。能补充一下主题或背景吗？"
            if res.detected_language == "zh"
            else "Your question is a bit short. Could you give me a bit more context?"
        )
        res.reasons.append("input too short / ambiguous")
        return res

    # 4. Default = kb_qa (knowledge base is the system's primary purpose)
    res.intent = INTENT_KB_QA
    res.confidence = 0.6
    res.reasons.append("default -> kb_qa")

    # 5. Optional LLM re-classification when confidence is low
    if res.confidence < 0.7 and llm_call is not None:
        try:
            raw = llm_call(_CLASSIFY_PROMPT + text)
            import json
            start, end = raw.find("{"), raw.rfind("}")
            if 0 <= start < end:
                parsed = json.loads(raw[start:end + 1])
                intent = parsed.get("intent")
                if intent in ALL_INTENTS:
                    res.intent = intent
                    res.confidence = float(parsed.get("confidence", 0.7))
                    res.reasons.append(f"llm override -> {intent}")
        except Exception as e:
            logger.warning(f"LLM classification failed: {e}")

    return res


def build_clarification_message(intent_res: IntentResult) -> str:
    return intent_res.clarification_prompt or "能再描述清楚一点吗？"
