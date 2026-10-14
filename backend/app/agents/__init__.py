"""
Agent 能力包。

保留：
  * `intent_classifier` —— 意图分类器（规则优先 + LLM 回退），被状态图的
    `classify_intent` 节点使用
  * `function_calling` / `react_agent` —— 工具调用 Agent 的**非原生工具调用后端**
    回退实现。原生后端（zhipu / openai / modelscope）走
    `app/graph/subgraphs` 的 ReAct 子图。

已移除：
  * `router.py` —— 过程式 if/elif 分发，已被 `app/graph` 状态图取代
  * `plan_execute.py` —— 从未被接线的死代码
"""

from .function_calling import run_function_calling_agent
from .intent_classifier import (
    ALL_INTENTS,
    INTENT_AGENT,
    INTENT_CLARIFY,
    INTENT_FILE_QA,
    INTENT_GENERAL,
    INTENT_KB_QA,
    INTENT_LONG_TEXT,
    INTENT_TASK,
    LONG_TEXT_THRESHOLD,
    IntentResult,
    build_clarification_message,
    classify,
)
from .react_agent import run_react_agent

__all__ = [
    "ALL_INTENTS",
    "INTENT_AGENT",
    "INTENT_CLARIFY",
    "INTENT_FILE_QA",
    "INTENT_GENERAL",
    "INTENT_KB_QA",
    "INTENT_LONG_TEXT",
    "INTENT_TASK",
    "LONG_TEXT_THRESHOLD",
    "IntentResult",
    "build_clarification_message",
    "classify",
    "run_function_calling_agent",
    "run_react_agent",
]
