"""引用规范化 + 可达性探测的单元测试。

这一层是需求 4「真实可访问链接」的技术落点，两类错误都会实质伤害用户：

  * **漏剔**：模型自编的 URL 留在正文里 → 用户点开 404，连带怀疑整份产出
  * **误标**：把网络超时当成「链接失效」→ 一批好链接被划掉，用户不再信任标注
"""

from __future__ import annotations

import pytest

from app.workflow.citation import (
    dedupe_sources,
    number_sources,
    render_footer,
    render_sources_block,
    sanitize_citations,
)

SOURCES = [
    {"title": "官方定价页", "href": "https://a.example/pricing", "source": "tavily"},
    {"title": "社区讨论", "href": "https://b.example/thread/1", "source": "tavily"},
    {"title": "重复项", "href": "https://a.example/pricing", "source": "tavily"},
]


class TestDedupeAndNumber:
    def test_dedupe_by_href_keeps_order(self):
        out = dedupe_sources(SOURCES)
        assert [s["href"] for s in out] == [
            "https://a.example/pricing",
            "https://b.example/thread/1",
        ]

    def test_numbering_starts_at_one(self):
        out = number_sources(SOURCES)
        assert [s["ref"] for s in out] == [1, 2]

    def test_ignores_malformed(self):
        out = dedupe_sources([None, "x", {"title": "无链接"}, {"href": "https://c.example"}])
        assert [s["href"] for s in out] == ["https://c.example"]

    def test_empty(self):
        assert dedupe_sources(None) == []
        assert number_sources([]) == []


class TestRenderBlocks:
    def test_prompt_block_is_numbered_and_carries_href(self):
        text = render_sources_block(SOURCES)
        assert "【来源 1】" in text
        assert "【来源 2】" in text
        assert "https://a.example/pricing" in text
        assert "官方定价页" in text

    def test_prompt_block_empty_when_no_sources(self):
        assert render_sources_block([]) == ""

    def test_footer_is_markdown(self):
        text = render_footer(SOURCES)
        assert "1. [官方定价页](https://a.example/pricing)" in text


class TestSanitizeCitations:
    def test_keeps_links_from_sources(self):
        text = "详见 https://a.example/pricing 的说明。"
        cleaned, removed = sanitize_citations(text, SOURCES)
        assert "https://a.example/pricing" in cleaned
        assert removed == []

    def test_strips_invented_link(self):
        """★ 核心保证：模型自编的链接必须被剔除。"""
        text = "参考 https://fake.example/made-up 与 https://a.example/pricing。"
        cleaned, removed = sanitize_citations(text, SOURCES)
        assert "fake.example" not in cleaned
        assert "https://a.example/pricing" in cleaned
        assert removed == ["https://fake.example/made-up"]

    def test_strips_when_no_sources_at_all(self):
        cleaned, removed = sanitize_citations("见 https://whatever.example/x", [])
        assert "whatever.example" not in cleaned
        assert len(removed) == 1

    def test_trailing_chinese_punctuation_not_part_of_url(self):
        """中文句号紧贴链接时，不能把标点算进 URL 里 —— 否则比对会失败。"""
        text = "参考 https://a.example/pricing。"
        cleaned, removed = sanitize_citations(text, SOURCES)
        assert removed == []
        assert "https://a.example/pricing。" in cleaned

    def test_keeps_surrounding_text(self):
        text = "第一段。\nhttps://fake.example/x\n第二段。"
        cleaned, _ = sanitize_citations(text, SOURCES)
        assert "第一段" in cleaned and "第二段" in cleaned

    def test_strips_bare_host_with_path(self):
        """没有 scheme 的裸链接同样是假链接（模型很爱这么写）。"""
        cleaned, removed = sanitize_citations("来源：fake.example/p/123", SOURCES)
        assert "fake.example" not in cleaned
        assert removed

    def test_keeps_bare_host_that_is_allowed(self):
        cleaned, removed = sanitize_citations("来源：a.example/pricing", SOURCES)
        assert "a.example" in cleaned
        assert removed == []

    def test_does_not_touch_plain_words(self):
        text = "这里提到 com 和 org 两个词，还有 1.5 这个数字。"
        cleaned, removed = sanitize_citations(text, SOURCES)
        assert cleaned == text
        assert removed == []

    def test_numbered_citations_are_untouched(self):
        """`[1]` 是允许的引用形式，不能被当成链接处理掉。"""
        text = "价格见 [1]，讨论见 [2]。"
        cleaned, removed = sanitize_citations(text, SOURCES)
        assert cleaned == text
        assert removed == []

    def test_markdown_link_target_is_stripped(self):
        text = "参考 [官方页](https://fake.example/x)"
        cleaned, _ = sanitize_citations(text, SOURCES)
        assert "fake.example" not in cleaned

    def test_empty_text(self):
        assert sanitize_citations("", SOURCES) == ("", [])


# ---------------------------------------------------------------------------
#  可达性
# ---------------------------------------------------------------------------


class _Resp:
    def __init__(self, code: int) -> None:
        self.status_code = code


class _StubClient:
    codes: dict[str, int] = {}
    raise_for: set[str] = set()

    def __init__(self, **_kwargs) -> None:
        pass

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def head(self, url: str):
        if url in self.raise_for:
            raise RuntimeError("connection reset")
        return _Resp(self.codes.get(url, 404))


@pytest.fixture
def stub_http(monkeypatch):
    import httpx

    from app.workflow import reachability

    reachability.clear_cache()
    monkeypatch.setattr(httpx, "Client", _StubClient)
    yield
    reachability.clear_cache()


class TestProbeUrl:
    def test_ok(self, stub_http):
        from app.workflow.reachability import probe_url

        _StubClient.codes = {"https://a.example": 200}
        assert probe_url("https://a.example") is True

    def test_not_found_is_false(self, stub_http):
        from app.workflow.reachability import probe_url

        _StubClient.codes = {"https://x.example": 404}
        assert probe_url("https://x.example") is False

    def test_405_is_unknown_not_false(self, stub_http):
        """有些站点不支持 HEAD 或拦爬虫 —— 不能据此判定链接失效。"""
        from app.workflow.reachability import probe_url

        _StubClient.codes = {"https://n.example": 405}
        assert probe_url("https://n.example") is None

    def test_network_error_is_unknown(self, stub_http):
        """★ 网络不通与链接失效是两回事。把超时当 False 会误伤一批好链接。"""
        from app.workflow.reachability import probe_url

        _StubClient.raise_for = {"https://timeout.example"}
        assert probe_url("https://timeout.example") is None
        _StubClient.raise_for = set()

    def test_non_http_returns_none(self, stub_http):
        from app.workflow.reachability import probe_url

        assert probe_url("ftp://a.example") is None
        assert probe_url("") is None

    def test_cached_second_call(self, stub_http, monkeypatch):
        """同一 URL 半小时内不重复探测 —— 否则每次执行都要多等几秒。"""
        from app.workflow.reachability import probe_url

        _StubClient.codes = {"https://c.example": 200}
        assert probe_url("https://c.example") is True

        calls = {"n": 0}
        original = _StubClient.head

        def _counting(self, url):
            calls["n"] += 1
            return original(self, url)

        monkeypatch.setattr(_StubClient, "head", _counting)
        assert probe_url("https://c.example") is True
        assert calls["n"] == 0  # 命中缓存，没有再发请求


class TestCheckSources:
    def test_adds_reachable_field(self, stub_http):
        from app.workflow.reachability import check_sources

        _StubClient.codes = {"https://a.example/pricing": 200, "https://b.example/t": 500}
        out = check_sources(
            [
                {"href": "https://a.example/pricing"},
                {"href": "https://b.example/t"},
            ]
        )
        assert out[0]["reachable"] is True
        assert out[1]["reachable"] is False

    def test_disabled_leaves_unknown(self, stub_http):
        from app.workflow.reachability import check_sources

        out = check_sources([{"href": "https://a.example"}], enabled=False)
        assert out[0]["reachable"] is None

    def test_does_not_mutate_input(self, stub_http):
        from app.workflow.reachability import check_sources

        _StubClient.codes = {"https://a.example": 200}
        original = [{"href": "https://a.example"}]
        out = check_sources(original)
        assert "reachable" not in original[0]
        assert out[0]["reachable"] is True

    def test_limit_skips_extra(self, stub_http):
        from app.workflow.reachability import check_sources

        _StubClient.codes = {f"https://s{i}.example": 200 for i in range(12)}
        out = check_sources(
            [{"href": f"https://s{i}.example"} for i in range(12)], limit=3
        )
        assert out[0]["reachable"] is True
        assert out[-1]["reachable"] is None  # 超出 limit 的不探测

    def test_missing_href_is_unknown(self, stub_http):
        from app.workflow.reachability import check_sources

        out = check_sources([{"title": "无链接"}])
        assert out[0]["reachable"] is None

    def test_empty(self, stub_http):
        from app.workflow.reachability import check_sources

        assert check_sources([]) == []
        assert check_sources(None) == []
