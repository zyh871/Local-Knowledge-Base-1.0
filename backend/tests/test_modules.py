"""
单元测试 — 覆盖修改过的核心模块的关键逻辑。
运行: python -m pytest tests/test_modules.py -v
"""

import os
import sys
import uuid
import tempfile

import pytest

# 确保 backend 目录在 sys.path 中
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), "..", "..", ".env"))


# ---------------------------------------------------------------------------
# rrf_fusion
# ---------------------------------------------------------------------------

class TestRRFFusion:
    def test_doc_signature_stable(self):
        from langchain_core.documents import Document
        from app.retrievers.rrf_fusion import _doc_signature

        d1 = Document(page_content="hello world", metadata={"source": "a.pdf"})
        d2 = Document(page_content="hello world", metadata={"source": "a.pdf"})
        assert _doc_signature(d1) == _doc_signature(d2)

    def test_doc_signature_differs_for_different_content(self):
        from langchain_core.documents import Document
        from app.retrievers.rrf_fusion import _doc_signature

        d1 = Document(page_content="hello", metadata={"source": "a.pdf"})
        d2 = Document(page_content="world", metadata={"source": "a.pdf"})
        assert _doc_signature(d1) != _doc_signature(d2)

    def test_fusion_deduplicates(self):
        from langchain_core.documents import Document
        from app.retrievers.rrf_fusion import rrf_fusion

        d = Document(page_content="shared content", metadata={"source": "x.pdf"})
        vec_results = [(d, 0.9)]
        bm25_results = [(d, 0.8)]
        merged = rrf_fusion(vec_results, bm25_results, k=60, top_n=5)
        assert len(merged) == 1

    def test_fusion_merges_different_docs(self):
        from langchain_core.documents import Document
        from app.retrievers.rrf_fusion import rrf_fusion

        d1 = Document(page_content="doc one", metadata={"source": "a.pdf"})
        d2 = Document(page_content="doc two", metadata={"source": "b.pdf"})
        merged = rrf_fusion([(d1, 0.9)], [(d2, 0.8)], k=60, top_n=5)
        assert len(merged) == 2

    def test_fusion_rank_boost(self):
        """同一文档在两个检索器中都出现时，得分应高于只出现一次的。"""
        from langchain_core.documents import Document
        from app.retrievers.rrf_fusion import rrf_fusion

        shared = Document(page_content="shared", metadata={"source": "s.pdf"})
        only_vec = Document(page_content="vec_only", metadata={"source": "v.pdf"})
        only_bm25 = Document(page_content="bm25_only", metadata={"source": "b.pdf"})
        vec_results = [(shared, 0.9), (only_vec, 0.5)]
        bm25_results = [(shared, 0.8), (only_bm25, 0.5)]
        merged = rrf_fusion(vec_results, bm25_results, k=60, top_n=3)
        assert merged[0].page_content == "shared"


# ---------------------------------------------------------------------------
# web_search.should_search
# ---------------------------------------------------------------------------

class TestShouldSearch:
    def test_empty_returns_false(self):
        from app.tools.web_search import should_search
        assert should_search("") is False
        assert should_search(None) is False

    def test_time_trigger(self):
        from app.tools.web_search import should_search
        assert should_search("今天的新闻") is True
        assert should_search("2025年最新动态") is True

    def test_fact_trigger(self):
        from app.tools.web_search import should_search
        assert should_search("今天北京天气怎么样") is True
        assert should_search("股价多少") is True

    def test_strong_trigger(self):
        from app.tools.web_search import should_search
        assert should_search("帮我搜索一下") is True
        assert should_search("谷歌查找") is True

    def test_no_trigger_for_normal_question(self):
        from app.tools.web_search import should_search
        assert should_search("写一个Python脚本处理Excel") is False
        assert should_search("请总结这段文字") is False


# ---------------------------------------------------------------------------
# conversation_memory
# ---------------------------------------------------------------------------

class TestConversationMemory:
    def test_pagination(self):
        from app.memory.conversation_memory import (
            upsert_conversation, list_conversations, delete_conversation
        )
        cids = [f"test_pg_{uuid.uuid4().hex[:8]}" for _ in range(3)]
        for cid in cids:
            upsert_conversation(conversation_id=cid, username="testuser_pg", title=f"对话 {cid[:4]}")
        page1 = list_conversations(username="testuser_pg", skip=0, limit=2)
        assert len(page1["items"]) <= 2
        assert page1["total"] >= 3
        page2 = list_conversations(username="testuser_pg", skip=2, limit=10)
        assert page1["total"] == page2["total"]
        for cid in cids:
            delete_conversation(cid)

    def test_message_persistence(self):
        from app.memory.conversation_memory import (
            upsert_conversation, save_context, load_history,
            delete_conversation, invalidate_history_cache
        )
        cid = f"test_msg_{uuid.uuid4().hex[:8]}"
        upsert_conversation(conversation_id=cid, username="testuser_msg", title="Msg Test")
        save_context(conversation_id=cid, user_text="Hello 1", ai_text="AI 1")
        save_context(conversation_id=cid, user_text="Hello 2", ai_text="AI 2")
        hist = load_history(cid)
        assert len(hist) == 4
        # 清掉缓存后应能正确回源 SQLite（真相源）
        invalidate_history_cache(cid)
        hist2 = load_history(cid)
        assert len(hist2) == 4
        assert hist2[0]["content"] == "Hello 1"
        assert hist2[1]["content"] == "AI 1"
        delete_conversation(cid)

    def test_verify_owner(self):
        from app.memory.conversation_memory import (
            upsert_conversation, verify_owner, delete_conversation
        )
        cid = f"test_owner_{uuid.uuid4().hex[:8]}"
        upsert_conversation(conversation_id=cid, username="alice", title="Alice's conv")
        assert verify_owner(cid, "alice") is True
        assert verify_owner(cid, "bob") is False
        assert verify_owner(cid, "") is False
        assert verify_owner("", "alice") is False
        delete_conversation(cid)

    def test_clear_memory_removes_messages(self):
        from app.memory.conversation_memory import (
            upsert_conversation, save_context, clear_memory, load_history,
            delete_conversation, invalidate_history_cache, _meta_connect, _lock
        )
        cid = f"test_clear_{uuid.uuid4().hex[:8]}"
        upsert_conversation(conversation_id=cid, username="testuser_clr", title="Clear Test")
        save_context(conversation_id=cid, user_text="msg1", ai_text="resp1")
        clear_memory(cid)
        invalidate_history_cache(cid)
        with _lock, _meta_connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS cnt FROM conversation_messages WHERE conversation_id = ?",
                (cid,),
            ).fetchone()
        assert row["cnt"] == 0
        assert load_history(cid) == []
        delete_conversation(cid)


# ---------------------------------------------------------------------------
# user_profile
# ---------------------------------------------------------------------------

class TestUserProfile:
    def test_get_profile_nonexistent(self):
        from app.memory.user_profile import get_profile, delete_profile
        username = f"nonexistent_{uuid.uuid4().hex[:8]}"
        profile = get_profile(username)
        assert profile.get("username") == username
        assert profile.get("facts") == []
        delete_profile(username)

    def test_update_and_read_profile(self):
        from app.memory.user_profile import update_profile, get_profile, delete_profile
        username = f"testuser_{uuid.uuid4().hex[:8]}"
        update_profile(username, {
            "display_name": "Test User",
            "profession": "Engineer",
            "language": "zh",
            "facts": ["likes Python", "uses FastAPI"],
            "preferences": {"theme": "dark"},
        })
        profile = get_profile(username)
        assert profile["display_name"] == "Test User"
        assert profile["profession"] == "Engineer"
        assert profile["language"] == "zh"
        assert len(profile["facts"]) == 2
        assert profile["preferences"]["theme"] == "dark"
        delete_profile(username)

    def test_fact_deduplication(self):
        from app.memory.user_profile import update_profile, get_profile, delete_profile
        username = f"testuser_dedup_{uuid.uuid4().hex[:8]}"
        update_profile(username, {"facts": ["likes Python"]})
        update_profile(username, {"facts": ["likes Python", "likes Go"]})
        profile = get_profile(username)
        facts_text = [f["fact"] for f in profile["facts"]]
        assert "likes Python" in facts_text
        assert "likes Go" in facts_text
        assert len(facts_text) == 2
        delete_profile(username)


# ---------------------------------------------------------------------------
# tfidf_embeddings (no infinite recursion)
# ---------------------------------------------------------------------------

class TestTFIDFEmbeddings:
    def test_embed_documents_no_recursion(self):
        """确保 embed_documents 在 _ready=False 时不会无限递归。"""
        from app.embeddings.tfidf_embeddings import SimpleTFIDFEmbeddings
        import tempfile, os
        with tempfile.TemporaryDirectory() as tmpdir:
            emb = SimpleTFIDFEmbeddings.__new__(SimpleTFIDFEmbeddings)
            emb.vocab = {}
            emb.idf_cache = {}
            emb._tokenized_corpus = []
            emb._ready = False
            emb.vocab_path = os.path.join(tmpdir, "tfidf_vocab.pkl")
            emb._projection_matrix = []
            emb.FIXED_DIM = 768
            result = emb.embed_documents(["hello world", "foo bar"])
            assert len(result) == 2
            assert len(result[0]) == 768

    def test_embed_query_sets_ready(self):
        from app.embeddings.tfidf_embeddings import SimpleTFIDFEmbeddings
        import tempfile, os
        with tempfile.TemporaryDirectory() as tmpdir:
            emb = SimpleTFIDFEmbeddings.__new__(SimpleTFIDFEmbeddings)
            emb.vocab = {}
            emb.idf_cache = {}
            emb._tokenized_corpus = []
            emb._ready = False
            emb.vocab_path = os.path.join(tmpdir, "tfidf_vocab.pkl")
            emb._projection_matrix = []
            emb.FIXED_DIM = 768
            vec = emb.embed_query("test query")
            assert len(vec) == 768
            assert emb._ready is True

    def test_vocab_reuse_on_duplicate(self):
        """重复调用 embed_documents 时词表不应增长。"""
        from app.embeddings.tfidf_embeddings import SimpleTFIDFEmbeddings
        import tempfile, os
        with tempfile.TemporaryDirectory() as tmpdir:
            emb = SimpleTFIDFEmbeddings.__new__(SimpleTFIDFEmbeddings)
            emb.vocab = {}
            emb.idf_cache = {}
            emb._tokenized_corpus = []
            emb._ready = False
            emb.vocab_path = os.path.join(tmpdir, "tfidf_vocab.pkl")
            emb._projection_matrix = []
            emb.FIXED_DIM = 768
            emb.embed_documents(["hello world", "foo bar"])
            size1 = len(emb.vocab)
            emb.embed_documents(["hello world"])
            size2 = len(emb.vocab)
            assert size1 == size2


# ---------------------------------------------------------------------------
# bm25_retriever incremental add
# ---------------------------------------------------------------------------

class TestBM25Retriever:
    def _make_retriever(self, tmpdir):
        from app.retrievers.bm25_retriever import BM25Retriever
        r = BM25Retriever.__new__(BM25Retriever)
        r.bm25 = None
        r.documents = []
        r._tokenized = []
        r._doc_id_to_index = {}
        r.index_path = os.path.join(tmpdir, "bm25.pkl")
        return r

    def test_add_documents_preserves_existing(self):
        from langchain_core.documents import Document
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            r = self._make_retriever(tmpdir)
            docs1 = [
                Document(page_content="python programming language", metadata={"source": "a.txt"}),
                Document(page_content="java programming language", metadata={"source": "b.txt"}),
                Document(page_content="cooking recipes food", metadata={"source": "c.txt"}),
            ]
            r.build_index(docs1)
            assert len(r.documents) == 3
            docs2 = [Document(page_content="new content here", metadata={"source": "d.txt"})]
            r.add_documents(docs2)
            assert len(r.documents) == 4
            results = r.retrieve("python programming", k=2)
            assert len(results) == 2
            top_doc = results[0][0]
            assert "python" in top_doc.page_content

    def test_remove_by_source(self):
        from langchain_core.documents import Document
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            r = self._make_retriever(tmpdir)
            docs = [
                Document(page_content="content one", metadata={"source": "a.txt"}),
                Document(page_content="content two", metadata={"source": "b.txt"}),
            ]
            r.build_index(docs)
            r.remove_by_source("a.txt")
            assert len(r.documents) == 1
            assert r.documents[0].metadata["source"] == "b.txt"


# ---------------------------------------------------------------------------
# config
# ---------------------------------------------------------------------------

class TestConfig:
    """配置读取走 `app.core.config`（旧 `app.config` 兼容外壳已删除）。"""

    def test_vision_model_defined(self):
        from app.core.config import get_settings

        assert get_settings().model.vision_model

    def test_retriever_k_positive(self):
        from app.core.config import get_settings

        assert get_settings().retrieval.retriever_k >= 1

    def test_llm_model_defined(self):
        from app.core.config import get_settings

        assert get_settings().model.llm_model

    def test_no_legacy_config_module(self):
        """回归保护：旧兼容外壳若被重新引入，说明迁移未彻底。"""
        import importlib

        with pytest.raises(ModuleNotFoundError):
            importlib.import_module("app.config")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
