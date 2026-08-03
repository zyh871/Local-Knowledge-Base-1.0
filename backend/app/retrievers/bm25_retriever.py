"""
BM25 keyword retriever with incremental document support.
"""

import logging
import os
import re
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

from langchain_core.documents import Document

from rank_bm25 import BM25Okapi

from app import config

logger = logging.getLogger(__name__)


class BM25Retriever:
    """BM25 keyword retriever with incremental document support."""

    def __init__(self):
        self.bm25: Optional[BM25Okapi] = None
        self.documents: List[Document] = []
        self._tokenized: List[List[str]] = []
        self._doc_id_to_index: Dict[str, int] = {}  # source -> index offset
        self.index_path = os.path.join(config.DATA_PATH, "bm25_index.pkl")
        self._load_index()

    def _tokenize(self, text: str) -> List[str]:
        return re.findall(r"[一-鿿]+|[a-zA-Z0-9]+", text.lower())

    def build_index(self, documents: List[Document]):
        """Full index rebuild."""
        self.documents = documents
        self._tokenized = [self._tokenize(d.page_content) for d in documents]
        self.bm25 = BM25Okapi(self._tokenized)
        self._doc_id_to_index = {
            d.metadata.get("source", f"doc_{i}"): i
            for i, d in enumerate(documents)
        }
        self._save_index()

    def add_documents(self, documents: List[Document]):
        """Incremental add — rebuilds index."""
        if not documents:
            return
        self.documents.extend(documents)
        self._tokenized.extend(self._tokenize(d.page_content) for d in documents)
        self.bm25 = BM25Okapi(self._tokenized)
        for i, d in enumerate(self.documents):
            self._doc_id_to_index[d.metadata.get("source", f"doc_{i}")] = i
        self._save_index()

    def remove_by_source(self, source: str):
        """Remove all chunks from a given source file."""
        old_len = len(self.documents)
        keep = [(d, t) for d, t in zip(self.documents, self._tokenized)
                if d.metadata.get("source") != source]
        if len(keep) == old_len:
            return
        self.documents = [d for d, _ in keep]
        self._tokenized = [t for _, t in keep]
        if self.documents:
            self.bm25 = BM25Okapi(self._tokenized)
        else:
            self.bm25 = None
        self._doc_id_to_index = {
            d.metadata.get("source", f"doc_{i}"): i
            for i, d in enumerate(self.documents)
        }
        self._save_index()

    def retrieve(self, query: str, k: int = 3) -> List[Tuple[Document, float]]:
        if not self.bm25 or not self.documents:
            return []
        query_tokens = self._tokenize(query)
        scores = self.bm25.get_scores(query_tokens)
        top_n = min(k, len(self.documents))
        top_indices = scores.argsort()[::-1][:top_n]
        return [(self.documents[i], float(scores[i])) for i in top_indices]

    def _save_index(self):
        """Save BM25 index to disk."""
        try:
            import joblib
            joblib.dump({
                'documents': self.documents,
                '_tokenized': self._tokenized,
                '_doc_id_to_index': self._doc_id_to_index,
                'bm25': self.bm25
            }, self.index_path)
        except Exception as e:
            logger.error(f"Failed to save BM25 index: {e}")

    def _load_index(self):
        """Load BM25 index from disk if it exists."""
        try:
            import joblib
            if os.path.exists(self.index_path):
                data = joblib.load(self.index_path)
                self.documents = data['documents']
                self._tokenized = data['_tokenized']
                self._doc_id_to_index = data['_doc_id_to_index']
                self.bm25 = data['bm25']
                logger.info(f"Loaded BM25 index from {self.index_path}")
        except Exception as e:
            logger.warning(f"Failed to load BM25 index: {e}")