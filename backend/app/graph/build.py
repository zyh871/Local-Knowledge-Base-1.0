"""
状态图装配。

拓扑（与 docs/REFACTOR_FASTAPI_LANGGRAPH_REDIS.md 第 3.4 节一致）：

    START → ingest → classify_intent
                          │  route_by_intent（唯一分发点）
        ┌────────┬────────┼────────┬──────────┐
        ▼        ▼        ▼        ▼          ▼
     clarify long_text file_qa  agent     retrieve
                          │        │     route_after_retrieve
                          │        │      ┌────┴────┐
                          │        │      ▼         ▼
                          │        │  web_search  build_prompt
                          │        │      └────┬────┘
                          │        │           │
                          │  route_after_file_qa│
                          ├────persist◀────────┤
                          └──────▶ build_prompt → generate → persist → END
"""

from __future__ import annotations

from typing import Any

from langgraph.graph import END, START, StateGraph

from app.core.logging import get_logger
from app.graph.checkpointer import make_checkpointer
from app.graph.edges import (
    route_after_file_qa,
    route_after_retrieve,
    route_by_intent,
)
from app.graph.nodes import Deps, bind
from app.graph.nodes.agent import agent
from app.graph.nodes.persist import persist
from app.graph.nodes.prepare import classify_intent, ingest
from app.graph.nodes.respond import build_prompt, clarify, generate, long_text
from app.graph.nodes.retrieval import file_qa_retrieve, retrieve, web_search
from app.graph.state import RAGState

logger = get_logger(__name__)


def build_qa_graph(*, settings: Any, chain: Any, redis: Any = None) -> Any:
    """构建并编译问答状态图。"""
    deps = Deps(chain=chain, settings=settings, redis=redis)
    checkpointer = make_checkpointer(settings, redis)

    graph = StateGraph(RAGState)

    graph.add_node("ingest", bind(ingest, deps))
    graph.add_node("classify_intent", bind(classify_intent, deps))
    graph.add_node("clarify", bind(clarify, deps))
    graph.add_node("long_text", bind(long_text, deps))
    graph.add_node("file_qa_retrieve", bind(file_qa_retrieve, deps))
    graph.add_node("agent", bind(agent, deps))
    graph.add_node("retrieve", bind(retrieve, deps))
    graph.add_node("web_search", bind(web_search, deps))
    graph.add_node("build_prompt", bind(build_prompt, deps))
    graph.add_node("generate", bind(generate, deps))
    graph.add_node("persist", bind(persist, deps))

    # 主干
    graph.add_edge(START, "ingest")
    graph.add_edge("ingest", "classify_intent")

    # 意图分发
    graph.add_conditional_edges(
        "classify_intent",
        route_by_intent,
        {
            "clarify": "clarify",
            "long_text": "long_text",
            "file_qa_retrieve": "file_qa_retrieve",
            "agent": "agent",
            "retrieve": "retrieve",
            "web_search": "web_search",
            "build_prompt": "build_prompt",
        },
    )

    # 知识库检索后：视情况补联网
    graph.add_conditional_edges(
        "retrieve",
        route_after_retrieve,
        {"web_search": "web_search", "build_prompt": "build_prompt"},
    )
    graph.add_edge("web_search", "build_prompt")

    # 指定文件检索后：未命中则跳过生成
    graph.add_conditional_edges(
        "file_qa_retrieve",
        route_after_file_qa,
        {"build_prompt": "build_prompt", "persist": "persist"},
    )

    # 生成链
    graph.add_edge("build_prompt", "generate")
    graph.add_edge("generate", "persist")

    # 终端节点 → 记忆写回
    graph.add_edge("clarify", "persist")
    graph.add_edge("long_text", "persist")
    graph.add_edge("agent", "persist")
    graph.add_edge("persist", END)

    compiled = graph.compile(checkpointer=checkpointer)
    logger.info(
        f"问答状态图已编译: checkpointer={type(checkpointer).__name__ if checkpointer else 'None'}"
    )
    return compiled
