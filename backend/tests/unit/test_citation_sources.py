"""引用溯源字段测试（F3）。

重点验证三件事：
  1. `sources_from_state` 补齐 index/id/confidence/content，且按文件去重
  2. 置信度是按**排序名次**推导的（high/medium/low），不是伪造的相似度
  3. 知识库与联网来源合并后必须**统一重编号**，否则会出现重复的 [1]
"""

from __future__ import annotations

from app.graph.state import merge_sources
from app.utils.documents import (
    annotate_sources,
    renumber_sources,
    sources_from_state,
)


def _doc(source: str, text: str, chunk_id: str = "c1") -> dict:
    return {
        "page_content": text,
        "metadata": {"source": source, "chunk_id": chunk_id},
    }


def test_sources_from_state_annotates_fields():
    docs = [
        _doc("a.md", "A" * 200),
        _doc("a.md", "重复文件应被去重"),
        _doc("b.md", "B" * 50),
    ]
    out = sources_from_state(docs)

    assert len(out) == 2, "同一文件只保留首个分块"
    assert [s["index"] for s in out] == [1, 2]
    assert [s["id"] for s in out] == ["src_1", "src_2"]
    assert out[0]["confidence"] == "high"
    assert out[1]["confidence"] == "medium"
    assert out[0]["chunk_id"] == "c1"
    assert out[0]["content_preview"] == ("A" * 200)[:100]
    assert out[0]["content"].startswith("A")
    assert len(out[0]["content"]) == 200


def test_confidence_follows_rank():
    docs = [_doc(f"f{i}.md", "x") for i in range(5)]
    out = sources_from_state(docs)
    assert [s["confidence"] for s in out] == [
        "high",
        "medium",
        "medium",
        "low",
        "low",
    ]


def test_renumber_overwrites_duplicate_ids():
    items = [
        {"file": "kb.md", "index": 1, "id": "src_1"},
        {"title": "web", "href": "http://x", "index": 1, "id": "src_1"},
    ]
    out = renumber_sources(items)
    assert [s["index"] for s in out] == [1, 2]
    assert [s["id"] for s in out] == ["src_1", "src_2"]


def test_merge_sources_renumbers_combined_list():
    """知识库 [1..] + 联网 [1..] 合并后不能出现两个 [1]。"""
    kb = [
        {
            "file": "a.md",
            "content_preview": "a",
            "index": 1,
            "id": "src_1",
            "confidence": "high",
        }
    ]
    web = [
        {
            "title": "t",
            "href": "http://example.com",
            "index": 1,
            "id": "src_1",
            "confidence": "high",
        }
    ]
    merged = merge_sources(kb, web)

    assert len(merged) == 2
    assert [s["index"] for s in merged] == [1, 2]
    assert len({s["id"] for s in merged}) == 2


def test_annotate_keeps_existing_fields():
    out = annotate_sources(
        [{"file": "a.md", "index": 7, "id": "custom", "confidence": "low"}]
    )
    assert out[0]["index"] == 7
    assert out[0]["id"] == "custom"
    assert out[0]["confidence"] == "low"
