"""
模型切换与截断检测测试。

模型切换的关键约束（都在这里钉死）：
  1. 候选清单来自配置白名单，**不是任意模型名**
  2. 非法模型 → 400，且错误信息里给出可选值（不静默回退到默认模型 ——
     静默回退会让用户以为自己切成功了）
  3. 切换按请求生效，服务端无状态
  4. 响应体回显实际使用的模型

截断检测：
  `finish_reason=length` → truncated；流中断 → truncated；
  正常 `stop` → 不标记，避免误报。
"""

from __future__ import annotations

import pytest

from app.core.config import clear_settings_cache, get_settings

# 用假白名单，避免测试依赖真实厂商模型名（网络不可用也能跑）
_FAKE_CHOICES = "mock,alpha,beta"


@pytest.fixture
def fake_choices(monkeypatch):
    monkeypatch.setenv("LLM_CHOICES", _FAKE_CHOICES)
    clear_settings_cache()
    yield
    clear_settings_cache()


# ---------------------------------------------------------------------------
#  配置层
# ---------------------------------------------------------------------------


class TestModelChoicesConfig:
    def test_default_model_is_first(self, fake_choices):
        model = get_settings().model
        choices = model.available_llm_models
        assert choices[0] == model.llm_model
        assert set(choices) == {"mock", "alpha", "beta"}

    def test_allowed_only_whitelist(self, fake_choices):
        model = get_settings().model
        assert model.is_llm_allowed("alpha") is True
        assert model.is_llm_allowed("gpt-4o") is False
        assert model.is_llm_allowed("") is False

    def test_builtin_choices_when_env_empty(self, monkeypatch):
        monkeypatch.setenv("LLM_CHOICES", "")
        clear_settings_cache()
        try:
            choices = get_settings().model.available_llm_models
            assert choices, "内置候选不应为空"
            assert choices[0] == get_settings().model.llm_model
        finally:
            clear_settings_cache()

    def test_configured_model_injected_even_if_absent_from_choices(self, monkeypatch):
        """`LLM_MODEL` 指向白名单外的模型时，仍要出现在候选里（否则默认项不可见）。"""
        monkeypatch.setenv("LLM_CHOICES", "alpha,beta")
        monkeypatch.setenv("LLM_MODEL", "gamma")
        clear_settings_cache()
        try:
            model = get_settings().model
            assert model.llm_model == "gamma"
            assert model.available_llm_models[0] == "gamma"
        finally:
            clear_settings_cache()


class TestResolveModel:
    def test_empty_returns_default(self, fake_choices):
        from app.rag.providers import resolve_model

        assert resolve_model(None) == get_settings().model.llm_model
        assert resolve_model("") == get_settings().model.llm_model

    def test_valid_model_passes_through(self, fake_choices):
        from app.rag.providers import resolve_model

        assert resolve_model("alpha") == "alpha"

    def test_invalid_model_raises_with_hint(self, fake_choices):
        from app.rag.providers import resolve_model

        with pytest.raises(ValueError) as exc:
            resolve_model("evil")
        # 错误信息必须告诉用户有哪些可选值
        assert "alpha" in str(exc.value)

    def test_get_llm_caches_per_model(self, fake_choices):
        from app.rag.providers import get_llm, reset_model_singletons

        reset_model_singletons()
        a1 = get_llm("alpha")
        a2 = get_llm("alpha")
        b1 = get_llm("beta")
        assert a1 is a2, "同一模型应复用实例"
        assert a1 is not b1, "不同模型必须是不同实例"
        reset_model_singletons()


# ---------------------------------------------------------------------------
#  接口层
# ---------------------------------------------------------------------------


class TestModelsEndpoint:
    def test_requires_auth(self, client):
        assert client.get("/models").status_code == 401

    def test_returns_choices_and_default(self, live_client, auth_headers, fake_choices):
        response = live_client.get("/models", headers=auth_headers)
        assert response.status_code == 200
        body = response.json()
        ids = [m["id"] for m in body["models"]]
        assert ids == ["mock", "alpha", "beta"]
        assert [m for m in body["models"] if m["is_default"]][0]["id"] == "mock"
        assert body["default_model"] == "mock"
        assert body["backend"]


class TestModelPerRequest:
    # 用 task 意图（「请翻译：…」）—— 它经 build_prompt → generate，
    # 既不检索也不联网，是验证「模型被真正使用」的最短路径。
    # 注意：澄清（clarify）与长文本路径不发 used_model，因为前者不调用 LLM。
    _Q = "请翻译：hello world"

    def test_ask_echoes_used_model(self, live_client, auth_headers, fake_choices):
        response = live_client.post(
            "/ask",
            json={"question": self._Q, "conversation_id": "m-alpha", "model": "alpha"},
            headers=auth_headers,
        )
        assert response.status_code == 200
        assert response.json()["model"] == "alpha"

    def test_ask_without_model_uses_default(self, live_client, auth_headers, fake_choices):
        response = live_client.post(
            "/ask",
            json={"question": self._Q, "conversation_id": "m-default"},
            headers=auth_headers,
        )
        assert response.status_code == 200
        assert response.json()["model"] == "mock"

    def test_clarify_path_has_no_model(self, live_client, auth_headers, fake_choices):
        """澄清不调用 LLM，因此没有「本次使用的模型」—— 不应谎报默认模型。"""
        response = live_client.post(
            "/ask",
            json={"question": "那个", "conversation_id": "m-clarify", "model": "alpha"},
            headers=auth_headers,
        )
        assert response.status_code == 200
        body = response.json()
        assert body["needs_clarification"] is True
        assert body["model"] == ""

    def test_invalid_model_rejected_400(self, live_client, auth_headers, fake_choices):
        response = live_client.post(
            "/ask",
            json={"question": "你好", "model": "evil-model"},
            headers=auth_headers,
        )
        assert response.status_code == 400
        body = response.json()
        # 沿用旧的错误响应形状（前端按 answer 读取）
        assert body["data_source"] == "error"
        assert "evil-model" in body["answer"]
        assert "alpha" in body["answer"]

    def test_stream_carries_model_in_final(self, client, auth_headers, fake_choices):
        response = client.post(
            "/ask/stream",
            json={"question": self._Q, "conversation_id": "m-stream", "model": "beta"},
            headers=auth_headers,
        )
        assert response.status_code == 200
        import json

        events = [
            json.loads(line[6:])
            for line in response.text.splitlines()
            if line.startswith("data: ")
        ]
        final = [e for e in events if e["type"] == "final"][-1]["content"]
        assert final["model"] == "beta"

    def test_stream_invalid_model_yields_error_final(self, client, auth_headers, fake_choices):
        response = client.post(
            "/ask/stream",
            json={"question": "那个", "model": "nope"},
            headers=auth_headers,
        )
        assert response.status_code == 200  # 流已开始，只能靠 final 报错
        import json

        events = [
            json.loads(line[6:])
            for line in response.text.splitlines()
            if line.startswith("data: ")
        ]
        assert events[-1]["content"]["data_source"] == "error"


# ---------------------------------------------------------------------------
#  finish_reason 提取与截断判定
# ---------------------------------------------------------------------------


class _Chunk:
    def __init__(self, content="", meta=None):
        self.content = content
        self.response_metadata = meta or {}


class TestFinishReasonExtraction:
    def test_from_response_metadata(self):
        from app.utils.text import finish_reason_of

        assert finish_reason_of(_Chunk(meta={"finish_reason": "stop"})) == "stop"
        assert finish_reason_of(_Chunk(meta={"finish_reason": "length"})) == "length"

    def test_from_nested_choices(self):
        from app.utils.text import finish_reason_of

        chunk = _Chunk(meta={"choices": [{"finish_reason": "length"}]})
        assert finish_reason_of(chunk) == "length"

    def test_from_generation_info(self):
        from app.utils.text import finish_reason_of

        class C:
            response_metadata = {}
            generation_info = {"finish_reason": "stop"}

        assert finish_reason_of(C()) == "stop"

    def test_missing_returns_none(self):
        from app.utils.text import finish_reason_of

        assert finish_reason_of(_Chunk()) is None

    def test_usage_extraction(self):
        from app.utils.text import usage_of

        class M:
            usage_metadata = {"input_tokens": 1, "output_tokens": 2}

        assert usage_of(M())["output_tokens"] == 2

        class M2:
            usage_metadata = {}
            response_metadata = {"token_usage": {"total_tokens": 9}}

        assert usage_of(M2())["total_tokens"] == 9

        class M3:
            usage_metadata = None
            response_metadata = {}

        assert usage_of(M3()) == {}


# ---------------------------------------------------------------------------
#  generate 节点的截断标记
# ---------------------------------------------------------------------------


class _FakeLLM:
    """可控的假 LLM：可指定流式分片、finish_reason、是否抛错、同步回退结果。"""

    def __init__(self, chunks, finish_reason="stop", stream_error=None, invoke_text="", invoke_error=None):
        self._chunks = chunks
        self._finish_reason = finish_reason
        self._stream_error = stream_error
        self._invoke_text = invoke_text
        self._invoke_error = invoke_error

    def stream(self, _prompt):
        for i, text in enumerate(self._chunks):
            last = i == len(self._chunks) - 1
            meta = {"finish_reason": self._finish_reason} if (last and self._finish_reason) else {}
            yield _Chunk(content=text, meta=meta)
        if self._stream_error:
            raise self._stream_error

    def invoke(self, _prompt):
        if self._invoke_error:
            raise self._invoke_error

        class M:
            content = self._invoke_text
            usage_metadata = {}
            response_metadata = {}

        return M()


class _Deps:
    def __init__(self):
        from app.core.config import get_settings

        self.settings = get_settings()
        self.chain = None
        self.redis = None


def _run_generate(monkeypatch, llm, model="mock"):
    """在受控假 LLM 上跑 generate 节点。"""
    import app.graph.nodes.respond as respond
    import app.rag.providers as providers

    monkeypatch.setattr(providers, "get_llm", lambda m=None: llm)
    monkeypatch.setattr(providers, "resolve_model", lambda m=None: m or "mock")

    return respond.generate({"prompt": "p", "model": model}, _Deps())


class TestGenerateTruncation:
    def test_normal_completion_not_marked(self, monkeypatch, fake_choices):
        llm = _FakeLLM(["你好", "，世界"], finish_reason="stop")
        result = _run_generate(monkeypatch, llm, model="alpha")
        assert result["answer"] == "你好，世界"
        assert result["truncated"] is False
        assert result["used_model"] == "alpha"

    def test_length_finish_reason_marked_truncated(self, monkeypatch, fake_choices):
        llm = _FakeLLM(["被截断的回答"], finish_reason="length")
        result = _run_generate(monkeypatch, llm)
        assert result["truncated"] is True

    def test_stream_error_with_empty_fallback_marks_truncated(self, monkeypatch, fake_choices):
        """★ 回归保护：曾经这里会**静默返回半截答案**。

        流中断 + 同步回退返回空 → 旧代码把半截内容当完整答案返回。
        注意 finish_reason 必须为 None —— 真实的「流中断」不会有收尾标记。
        """
        llm = _FakeLLM(
            ["只写了一半的"],
            finish_reason=None,
            stream_error=RuntimeError("connection reset"),
            invoke_text="",
        )
        result = _run_generate(monkeypatch, llm)
        assert result["answer"] == "只写了一半的"
        assert result["truncated"] is True, "半截答案必须被标记，不能静默当成完整"

    def test_stream_error_with_short_fallback_replaces(self, monkeypatch, fake_choices):
        """流几乎没产出时，用同步结果替换。"""
        llm = _FakeLLM(
            ["嗯"],
            finish_reason=None,
            stream_error=RuntimeError("reset"),
            invoke_text="完整答案在这里",
        )
        result = _run_generate(monkeypatch, llm)
        assert result["answer"] == "完整答案在这里"

    def test_stream_error_with_long_partial_keeps_stream(self, monkeypatch, fake_choices):
        """流已产出大半时保留流式结果，避免同一段话出现两遍。"""
        partial = "流式已经写了很多内容" * 5
        llm = _FakeLLM(
            [partial],
            finish_reason=None,
            stream_error=RuntimeError("reset"),
            invoke_text="短",
        )
        result = _run_generate(monkeypatch, llm)
        assert result["answer"] == partial

    def test_both_fail_gives_explicit_failure_text(self, monkeypatch, fake_choices):
        llm = _FakeLLM([], stream_error=RuntimeError("a"), invoke_error=RuntimeError("b"))
        result = _run_generate(monkeypatch, llm)
        assert result["answer"] == "（生成失败）"
        assert result["truncated"] is False, "明确失败不算截断，不该同时给两个信号"

    def test_invalid_model_returns_error_answer(self, monkeypatch, fake_choices):
        """防御性：即使绕过 API 校验，节点自己也要拦住非法模型。"""
        import app.graph.nodes.respond as respond
        import app.rag.providers as providers

        def _boom(m=None):
            raise ValueError("不支持的模型")

        monkeypatch.setattr(providers, "resolve_model", _boom)
        result = respond.generate({"prompt": "p", "model": "evil"}, _Deps())
        assert result["error"] == "invalid_model"
        assert "模型不可用" in result["answer"]
