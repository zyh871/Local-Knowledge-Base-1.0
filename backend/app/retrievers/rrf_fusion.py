"""
Reciprocal Rank Fusion (RRF) implementation for hybrid search.
"""

from typing import List, Tuple

from langchain_core.documents import Document


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
        doc_id = id(doc)
        scores[doc_id] = scores.get(doc_id, 0) + 1.0 / (k + rank + 1)
        doc_map[doc_id] = doc

    # Process BM25 results
    for rank, (doc, _) in enumerate(bm25_results):
        doc_id = id(doc)
        scores[doc_id] = scores.get(doc_id, 0) + 1.0 / (k + rank + 1)
        doc_map[doc_id] = doc

    # Sort by score descending and return top N documents
    sorted_ids = sorted(scores.keys(), key=lambda x: scores[x], reverse=True)
    return [doc_map[doc_id] for doc_id in sorted_ids[:top_n]]