"""
Reciprocal Rank Fusion (RRF) implementation for hybrid search.
"""

import hashlib
from typing import List, Tuple

from langchain_core.documents import Document


def _doc_signature(doc: Document) -> str:
    """Stable content-based document signature for dedup across retrievers."""
    source = doc.metadata.get("source", "")
    content = doc.page_content
    raw = f"{source}|{len(content)}|{content[:200]}"
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


def rrf_fusion(
    vector_results: List[Tuple[Document, float]],
    bm25_results: List[Tuple[Document, float]],
    k: int = 60,
    top_n: int = 5,
) -> List[Document]:
    """Reciprocal Rank Fusion — merge two ranked lists."""
    scores: dict = {}
    doc_map: dict = {}

    # Process vector results
    for rank, (doc, _) in enumerate(vector_results):
        sig = _doc_signature(doc)
        scores[sig] = scores.get(sig, 0) + 1.0 / (k + rank + 1)
        doc_map.setdefault(sig, doc)

    # Process BM25 results
    for rank, (doc, _) in enumerate(bm25_results):
        sig = _doc_signature(doc)
        scores[sig] = scores.get(sig, 0) + 1.0 / (k + rank + 1)
        doc_map.setdefault(sig, doc)

    # Sort by score descending and return top N documents
    sorted_sigs = sorted(scores.keys(), key=lambda x: scores[x], reverse=True)
    return [doc_map[sig] for sig in sorted_sigs[:top_n]]