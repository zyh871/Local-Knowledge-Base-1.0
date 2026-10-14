"""
SSE 事件契约测试。

前端用 `fetch` + `ReadableStream` 消费 `/ask/stream`，事件顺序与结构是硬契约：
    intent → sources → (tool_calls) → chunk* → final

顺序错乱会导致界面渲染异常（例如先收到 chunk 再收到 intent，
"sources" 面板在回答之后才出现）。因此这里把顺序钉死。

注意：本文件里的用例只走**不依赖外部模型**的分支
（clarify / long_text），保证测试稳定、无网络。
"""

from __future__ import annotations

import json

import pytest

_REQUIRED_FINAL_KEYS = {
    "answer",
    "sources",
    "data_source",
    "intent",
    "intent_confidence",
    "detected_language",
}


def parse_sse(text: str) -> list[dict]:
    events = []
    for line in text.splitlines():
        if line.startswith("data: "):
            events.append(json.loads(line[6:]))
    return events


def post_stream(client, headers, question: str, conversation_id: str):
    response = client.post(
        "/ask/stream",
        json={"question": question, "conversation_id": conversation_id},
        headers=headers,
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    return parse_sse(response.text)


@pytest.fixture(scope="module")
def events_clarify(client, auth_headers):
    return post_stream(client, auth_headers, "那个", "sse-clarify")


@pytest.fixture(scope="module")
def events_long_text(client, auth_headers):
    # 长文本阈值是 800 字符（intent_classifier.LONG_TEXT_THRESHOLD）
    long_text = "请总结这段文字：" + ("人工智能正在改变世界。" * 80)
    assert len(long_text) >= 800, "需超过长文本阈值才会被分类为 long_text"
    return post_stream(client, auth_headers, long_text, "sse-longtext")


@pytest.fixture(scope="module")
def graph_events_clarify(live_client, auth_headers):
    """带 lifespan 的客户端 → 走 LangGraph 路径（app.state.qa_graph 已构建）。"""
    return post_stream(live_client, auth_headers, "那个", "sse-graph-clarify")


class TestEventSequence:
    def test_clarify_sequence(self, events_clarify):
        """澄清路径不发 sources（与 v3 一致）。"""
        assert [e["type"] for e in events_clarify] == ["intent", "chunk", "final"]
        assert events_clarify[0]["content"]["intent"] == "clarification_needed"
        assert events_clarify[-1]["content"]["data_source"] == "clarification"

    def test_long_text_sequence(self, events_long_text):
        assert [e["type"] for e in events_long_text] == ["intent", "chunk", "final"]
        assert events_long_text[0]["content"]["intent"] == "long_text"

    def test_intent_always_precedes_chunk(self, events_clarify, events_long_text):
        for events in (events_clarify, events_long_text):
            types = [e["type"] for e in events]
            assert types.index("intent") < types.index("chunk")

    def test_final_is_last(self, events_clarify, events_long_text):
        for events in (events_clarify, events_long_text):
            assert events[-1]["type"] == "final"
            assert sum(1 for e in events if e["type"] == "final") == 1

    def test_exactly_one_intent(self, events_clarify, events_long_text):
        """每个请求恰好一个 intent 事件 —— 防止分支终点重复发事件。"""
        for events in (events_clarify, events_long_text):
            assert sum(1 for e in events if e["type"] == "intent") == 1


class TestEventShape:
    def test_every_event_carries_trace_id(self, events_clarify, events_long_text):
        for events in (events_clarify, events_long_text):
            for event in events:
                assert event.get("trace_id"), f"事件缺少 trace_id: {event}"

    def test_final_has_all_contract_fields(self, events_clarify, events_long_text):
        for events in (events_clarify, events_long_text):
            content = events[-1]["content"]
            missing = _REQUIRED_FINAL_KEYS - set(content)
            assert not missing, f"final 事件缺少字段: {missing}"

    def test_intent_content_shape(self, events_clarify):
        content = events_clarify[0]["content"]
        assert set(content) >= {"intent", "confidence"}
        assert isinstance(content["intent"], str)
        assert isinstance(content["confidence"], (int, float))

    def test_chunk_content_is_string(self, events_clarify):
        for event in events_clarify:
            if event["type"] == "chunk":
                assert isinstance(event["content"], str)


class TestStreamErrorHandling:
    def test_invalid_question_yields_final_event(self, client, auth_headers):
        """校验失败也要以 final 事件返回 —— 响应头已发出，无法改状态码。"""
        events = post_stream(client, auth_headers, "   ", "sse-invalid")
        assert events[-1]["type"] == "final"
        assert events[-1]["content"]["data_source"] == "error"

    def test_forbidden_content_rejected_in_stream(self, client, auth_headers):
        events = post_stream(client, auth_headers, "<script>alert(1)</script>", "sse-xss")
        assert events[-1]["content"]["data_source"] == "error"
        assert events[-1]["content"]["intent"] == "error"


class TestNonStreamingContract:
    def test_ask_returns_question_response_shape(self, client, auth_headers):
        response = client.post(
            "/ask",
            json={"question": "那个", "conversation_id": "ask-clarify"},
            headers=auth_headers,
        )
        assert response.status_code == 200
        body = response.json()
        assert _REQUIRED_FINAL_KEYS <= set(body)
        assert body["trace_id"]
        assert body["needs_clarification"] is True

    def test_ask_validation_error_uses_legacy_shape(self, client, auth_headers):
        """校验错误沿用 400 + answer 字段（前端按此读取错误文案）。"""
        response = client.post(
            "/ask", json={"question": "  "}, headers=auth_headers
        )
        assert response.status_code == 400
        body = response.json()
        assert body["data_source"] == "error"
        assert body["intent"] == "error"
        assert "answer" in body

    def test_ask_requires_auth(self, client):
        response = client.post("/ask", json={"question": "你好"})
        assert response.status_code == 401


class TestLangGraphPath:
    """状态图是唯一的编排实现，事件契约必须成立。

    这是替换旧过程式 router 后的安全前提：
    图产出的 SSE 序列必须与 v3 逐事件一致，否则前端渲染会异常。
    """

    def test_graph_is_initialized(self, live_client):
        health = live_client.get("/health").json()
        graph = health["graph"]
        assert graph["enabled"] is True, "lifespan 未能构建状态图"
        assert graph["nodes"] and graph["nodes"] >= 11

    def test_graph_clarify_sequence_matches(self, graph_events_clarify):
        assert [e["type"] for e in graph_events_clarify] == ["intent", "chunk", "final"]
        assert graph_events_clarify[0]["content"]["intent"] == "clarification_needed"
        assert graph_events_clarify[-1]["content"]["data_source"] == "clarification"

    def test_graph_final_shape(self, graph_events_clarify):
        content = graph_events_clarify[-1]["content"]
        assert not (_REQUIRED_FINAL_KEYS - set(content))
        assert graph_events_clarify[-1].get("trace_id")

    def test_graph_non_streaming_ask(self, live_client, auth_headers):
        response = live_client.post(
            "/ask",
            json={"question": "那个", "conversation_id": "graph-ask"},
            headers=auth_headers,
        )
        assert response.status_code == 200
        body = response.json()
        assert not (_REQUIRED_FINAL_KEYS - set(body))
        assert body["needs_clarification"] is True
