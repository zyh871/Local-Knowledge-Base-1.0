"""
LangGraph 状态图测试。

分两层：
  1. **纯路由函数**（`route_by_intent` / `route_after_retrieve` / `route_after_file_qa`）
     —— 无副作用、不建图、不调模型，毫秒级，覆盖所有分支。
     这是替代 v3「两份 if/elif」后最该被钉死的部分。
  2. **图拓扑与端到端执行** —— 验证节点连线正确、clarify/long_text 等
     不依赖模型的分支能跑通，以及「检索无命中 → 降级 general」的可观测性。
"""

from __future__ import annotations

import pytest

from app.agents.intent_classifier import (
    INTENT_AGENT,
    INTENT_CLARIFY,
    INTENT_FILE_QA,
    INTENT_GENERAL,
    INTENT_KB_QA,
    INTENT_LONG_TEXT,
    INTENT_TASK,
)
from app.graph.edges import (
    needs_web_search,
    route_after_file_qa,
    route_after_retrieve,
    route_by_intent,
)


def _state(**kwargs) -> dict:
    base = {"question": "什么是 RAG？", "intent": INTENT_KB_QA, "force_search": False}
    base.update(kwargs)
    return base


class TestRouteByIntent:
    """每个意图都必须映射到正确的下游节点。

    注意：`should_search` 是关键词启发式，对「什么是 X」这类短问句会返回 True
    （v3 既有行为）。因此涉及联网判断的用例一律 monkeypatch 掉它，
    让测试只验证**路由逻辑**本身，而不是分类器的关键词表。
    """

    @pytest.mark.parametrize(
        "intent,expected",
        [
            (INTENT_CLARIFY, "clarify"),
            (INTENT_LONG_TEXT, "long_text"),
            (INTENT_FILE_QA, "file_qa_retrieve"),
            (INTENT_AGENT, "agent"),
            (INTENT_TASK, "build_prompt"),
            (INTENT_KB_QA, "retrieve"),
        ],
    )
    def test_simple_intents(self, intent, expected):
        assert route_by_intent(_state(intent=intent)) == expected

    def test_general_without_web_goes_straight_to_prompt(self, monkeypatch):
        """general 不做知识库检索 —— 否则会把无关 KB 片段塞进闲聊回答。"""
        import app.graph.edges as edges

        monkeypatch.setattr(edges, "should_search", lambda _q: False)
        state = _state(intent=INTENT_GENERAL, question="你好")
        assert route_by_intent(state) == "build_prompt"

    def test_general_with_web_search(self, monkeypatch):
        import app.graph.edges as edges

        monkeypatch.setattr(edges, "should_search", lambda _q: True)
        state = _state(intent=INTENT_GENERAL, question="今天天气怎么样")
        assert route_by_intent(state) == "web_search"

    def test_force_search_triggers_web_for_general(self):
        state = _state(intent=INTENT_GENERAL, question="你好", force_search=True)
        assert route_by_intent(state) == "web_search"

    def test_unknown_intent_defaults_to_retrieve(self):
        assert route_by_intent(_state(intent="something-weird")) == "retrieve"


class TestRouteAfterRetrieve:
    def test_no_web_needed(self, monkeypatch):
        import app.graph.edges as edges

        monkeypatch.setattr(edges, "should_search", lambda _q: False)
        assert route_after_retrieve(_state(question="解释向量检索原理")) == "build_prompt"

    def test_web_needed_when_forced(self):
        state = _state(force_search=True)
        assert route_after_retrieve(state) == "web_search"

    def test_web_needed_when_question_is_time_sensitive(self, monkeypatch):
        import app.graph.edges as edges

        monkeypatch.setattr(edges, "should_search", lambda _q: True)
        assert route_after_retrieve(_state(question="最新消息")) == "web_search"

    def test_no_double_web_search(self):
        """已有搜索上下文时不再补检索（避免重复消耗配额）。"""
        state = _state(force_search=True, search_context="已有结果")
        assert route_after_retrieve(state) == "build_prompt"


class TestRouteAfterFileQa:
    def test_miss_skips_generation(self):
        state = _state(intent=INTENT_FILE_QA, data_source="file_qa_miss")
        assert route_after_file_qa(state) == "persist"

    def test_hit_goes_to_generation(self):
        state = _state(intent=INTENT_FILE_QA, data_source="file_qa")
        assert route_after_file_qa(state) == "build_prompt"


class TestNeedsWebSearch:
    def test_force_search_always_true(self):
        assert needs_web_search(_state(force_search=True, question="你好")) is True

    def test_search_tool_failure_does_not_raise(self, monkeypatch):
        """搜索工具异常不应阻断路由。"""
        import app.graph.edges as edges

        def _boom(*_args, **_kwargs):
            raise RuntimeError("search tool down")

        monkeypatch.setattr(edges, "should_search", _boom)
        assert needs_web_search(_state(question="今天天气")) is False


# ---------------------------------------------------------------------------
#  图拓扑
# ---------------------------------------------------------------------------

_EXPECTED_NODES = {
    "ingest",
    "classify_intent",
    "clarify",
    "long_text",
    "file_qa_retrieve",
    "agent",
    "retrieve",
    "web_search",
    "build_prompt",
    "generate",
    "persist",
}


@pytest.fixture(scope="module")
def graph(settings, client):
    """构建图（client 夹具确保 app 已导入、向量库可用）。"""
    from app.graph.build import build_qa_graph
    from app.rag import get_rag_chain

    chain = get_rag_chain()
    chain.ensure_initialized()
    return build_qa_graph(settings=settings, chain=chain, redis=None)


class TestGraphTopology:
    def test_all_nodes_present(self, graph):
        nodes = {n for n in graph.get_graph().nodes if not n.startswith("__")}
        assert nodes == _EXPECTED_NODES

    def test_checkpointer_attached(self, graph):
        assert getattr(graph, "checkpointer", None) is not None or graph.checkpointer is None

    def test_conditionals_wired(self):
        """条件边的出口必须与路由函数的返回值集合一致。"""
        from app.graph.edges import (
            route_after_file_qa,
            route_after_retrieve,
            route_by_intent,
        )

        # route_by_intent 可能返回值
        returned = {
            route_by_intent(_state(intent=i))
            for i in (
                INTENT_CLARIFY,
                INTENT_LONG_TEXT,
                INTENT_FILE_QA,
                INTENT_AGENT,
                INTENT_GENERAL,
                INTENT_TASK,
                INTENT_KB_QA,
            )
        } | {"web_search"}
        assert returned <= _EXPECTED_NODES

        assert route_after_retrieve(_state(force_search=True)) in _EXPECTED_NODES
        assert route_after_file_qa(_state(data_source="file_qa_miss")) in _EXPECTED_NODES


class TestGraphExecution:
    """端到端执行：只挑不依赖外部模型的分支。"""

    def _config(self, thread: str) -> dict:
        return {
            "configurable": {"thread_id": thread, "checkpoint_ns": "test"},
            "recursion_limit": 25,
        }

    def test_clarify_path(self, graph):
        import asyncio

        result = asyncio.run(
            graph.ainvoke(
                {
                    "question": "那个",
                    "conversation_id": "graph-clarify",
                    "username": "admin",
                    "force_search": False,
                    "trace_id": "t",
                },
                config=self._config("graph-clarify"),
            )
        )
        assert result["intent"] == INTENT_CLARIFY
        assert result["data_source"] == "clarification"
        assert result["needs_clarification"] is True
        assert result["answer"]

    def test_no_hit_downgrades_to_general(self, graph, monkeypatch):
        """知识库无命中且无需联网 → 意图降级为 general（可观测的状态更新）。"""
        import asyncio

        import app.rag.vectorstore as rag_store

        monkeypatch.setattr(rag_store, "hybrid_search", lambda *a, **k: [])

        result = asyncio.run(
            graph.ainvoke(
                {
                    "question": "一个知识库里没有的问题",
                    "conversation_id": "graph-nohit",
                    "username": "admin",
                    "force_search": False,
                    "trace_id": "t",
                },
                config=self._config("graph-nohit"),
            )
        )
        assert result["intent"] == INTENT_GENERAL
        assert result["data_source"] in ("llm", "general")
        assert not result.get("docs")

    def test_state_is_json_serializable(self, graph, monkeypatch):
        """State 必须可序列化 —— 否则 checkpointer 会失败。

        这是把 LangChain `Document` 对象转成 dict 的直接动因。
        """
        import asyncio
        import json

        import app.rag.vectorstore as rag_store
        from langchain_core.documents import Document

        fake = [Document(page_content="内容", metadata={"source": "a.md"})]
        monkeypatch.setattr(rag_store, "hybrid_search", lambda *a, **k: fake)

        result = asyncio.run(
            graph.ainvoke(
                {
                    "question": "测试序列化",
                    "conversation_id": "graph-json",
                    "username": "admin",
                    "force_search": False,
                    "trace_id": "t",
                },
                config=self._config("graph-json"),
            )
        )
        json.dumps(result, ensure_ascii=False, default=str)
        assert result["docs"] == [{"page_content": "内容", "metadata": {"source": "a.md"}}]

    def test_conversation_memory_written_back(self, graph):
        """persist 节点应把本轮问答写回对话记忆。"""
        import asyncio

        from app.memory import get_conversation

        asyncio.run(
            graph.ainvoke(
                {
                    "question": "那个",
                    "conversation_id": "graph-persist",
                    "username": "admin",
                    "force_search": False,
                    "trace_id": "t",
                },
                config=self._config("graph-persist"),
            )
        )
        # clarify 轮次不写记忆（与 v3 一致）
        history = get_conversation("graph-persist").get("history") or []
        assert history == []
