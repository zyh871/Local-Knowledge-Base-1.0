"""
Vector retriever using Chroma for semantic search.
"""

import logging
import os
from typing import List, Optional, Tuple

from langchain_chroma import Chroma
from langchain_core.documents import Document

from app.core.config import get_settings

logger = logging.getLogger(__name__)


class VectorRetriever:
    """Vector retriever using Chroma."""

    def __init__(self):
        self.vectorstore: Optional[Chroma] = None
        self._embeddings = None
        self._persist_dir = get_settings().paths.chroma_persist_dir
        self._ready = False

    def initialize(self, embeddings):
        """Initialize vectorstore with embeddings."""
        self._embeddings = embeddings
        self._load_vectorstore()
        self._ready = True

    def _load_vectorstore(self):
        """Load existing vectorstore or create new one."""
        if not os.path.exists(self._persist_dir):
            os.makedirs(self._persist_dir, exist_ok=True)
            return

        try:
            self.vectorstore = Chroma(
                persist_directory=self._persist_dir,
                embedding_function=self._embeddings,
            )
            logger.info(f"Loaded existing vectorstore from {self._persist_dir}")
        except Exception as e:
            logger.warning(f"Failed to load vectorstore: {e}, creating new one")
            self.vectorstore = None

    def add_documents(self, documents: List[Document]):
        """Add documents to vectorstore."""
        if not self.vectorstore:
            self._load_vectorstore()
            if not self.vectorstore:
                self.vectorstore = Chroma(
                    persist_directory=self._persist_dir,
                    embedding_function=self._embeddings,
                )
        self.vectorstore.add_documents(documents)
        logger.info(f"Added {len(documents)} documents to vectorstore")

    def remove_by_source(self, source: str):
        """Remove all chunks from a given source file."""
        if not self.vectorstore:
            return
        try:
            all_data = self.vectorstore.get()
            metadatas = all_data.get("metadatas") or []
            if metadatas:
                ids_to_remove = [
                    all_data["ids"][i]
                    for i, meta in enumerate(metadatas)
                    if meta and meta.get("source") == source
                ]
                if ids_to_remove:
                    self.vectorstore.delete(ids_to_remove)
                    logger.info(f"Removed {len(ids_to_remove)} chunks from vectorstore (source: {source})")
        except Exception as e:
            logger.error(f"Failed to remove documents from vectorstore: {e}")

    def similarity_search_with_score(self, query: str, k: int = 3) -> List[Tuple[Document, float]]:
        """Perform similarity search with scores."""
        if not self.vectorstore:
            return []
        return self.vectorstore.similarity_search_with_score(query, k=k)

    def get_document_count(self) -> int:
        """Get total number of documents in vectorstore."""
        if not self.vectorstore:
            return 0
        try:
            return self.vectorstore._collection.count()
        except Exception:
            return 0