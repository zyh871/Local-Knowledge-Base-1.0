"""
TF-IDF embedding with fixed 768-dimensional projection.
"""

import math
import os
import re
import random
from collections import defaultdict
from typing import List

from app.core.config import get_settings


class SimpleTFIDFEmbeddings:
    """TF-IDF embedding with fixed 768-dimensional projection."""

    FIXED_DIM = 768

    def __init__(self):
        self.vocab: dict = {}
        self.idf_cache: dict = {}
        self._tokenized_corpus: List[List[str]] = []
        self._ready = False
        self.vocab_path = os.path.join(get_settings().paths.data_path, "tfidf_vocab.pkl")
        self._projection_matrix: List[List[float]] = []
        self._load_vocab()

    def _tokenize(self, text: str) -> List[str]:
        return re.findall(r"[一-鿿]+|[a-zA-Z0-9]+", text.lower())

    def _init_projection(self, input_dim: int):
        if len(self._projection_matrix) == 0:
            random.seed(42)
            self._projection_matrix = [
                [random.gauss(0, 1 / math.sqrt(input_dim)) for _ in range(input_dim)]
                for _ in range(self.FIXED_DIM)
            ]
        elif len(self._projection_matrix[0]) < input_dim:
            random.seed(42)
            new_cols = input_dim - len(self._projection_matrix[0])
            for row in self._projection_matrix:
                row.extend([random.gauss(0, 1 / math.sqrt(input_dim)) for _ in range(new_cols)])

    def _project(self, vec: List[float]) -> List[float]:
        if len(vec) == 0:
            return [0.0] * self.FIXED_DIM
        if len(self._projection_matrix[0]) < len(vec):
            self._init_projection(len(vec))
        result = [0.0] * self.FIXED_DIM
        for i in range(self.FIXED_DIM):
            for j in range(min(len(vec), len(self._projection_matrix[i]))):
                result[i] += vec[j] * self._projection_matrix[i][j]
        norm = sum(v * v for v in result) ** 0.5
        if norm > 0:
            result = [v / norm for v in result]
        return result

    def _build_vocab(self, texts: List[str]):
        self.vocab = {}
        self._tokenized_corpus = [self._tokenize(t) for t in texts]
        for doc_tokens in self._tokenized_corpus:
            for token in doc_tokens:
                if token not in self.vocab:
                    self.vocab[token] = len(self.vocab)

        doc_count = len(texts)
        for token, idx in self.vocab.items():
            df = sum(1 for dt in self._tokenized_corpus if token in dt)
            self.idf_cache[token] = math.log((doc_count + 1) / (df + 1)) + 1

        self._init_projection(len(self.vocab))
        self._save_vocab()

    def _embed(self, tokens: List[str]) -> List[float]:
        vec = [0.0] * len(self.vocab) if self.vocab else []
        if not vec:
            return [0.0] * self.FIXED_DIM
        tf = defaultdict(int)
        for token in tokens:
            if token in self.vocab:
                tf[token] += 1
        token_count = len(tokens) or 1
        for token, count in tf.items():
            idx = self.vocab[token]
            vec[idx] = (count / token_count) * self.idf_cache.get(token, 1.0)
        norm = sum(v * v for v in vec) ** 0.5
        if norm > 0:
            vec = [v / norm for v in vec]
        return self._project(vec)

    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        if not self._ready or not self.vocab:
            self._build_vocab(texts)
            self._ready = True
        else:
            new_tokens_list = [self._tokenize(t) for t in texts]
            existing_texts_set = {" ".join(tokens) for tokens in self._tokenized_corpus}
            to_add = [t for t, tokens in zip(texts, new_tokens_list) if " ".join(tokens) not in existing_texts_set]
            if to_add:
                self._build_vocab(
                    [" ".join(t) for t in self._tokenized_corpus] + to_add
                )
        return [self._embed(self._tokenize(t)) for t in texts]

    def embed_query(self, text: str) -> List[float]:
        if not self._ready or not self.vocab:
            if not text.strip():
                return [0.0] * self.FIXED_DIM
            self._build_vocab([text])
            self._ready = True
        return self._embed(self._tokenize(text))

    def add_texts(self, texts: List[str]):
        if not self._ready:
            self._build_vocab(texts)
            self._ready = True
            return
        old_texts = [" ".join(tokens) for tokens in self._tokenized_corpus]
        self._build_vocab(old_texts + texts)

    def _save_vocab(self):
        try:
            import joblib
            joblib.dump({
                'vocab': self.vocab,
                'idf_cache': self.idf_cache,
                '_tokenized_corpus': self._tokenized_corpus,
                '_projection_matrix': self._projection_matrix
            }, self.vocab_path)
        except Exception as e:
            print(f"Failed to save TF-IDF vocabulary: {e}")

    def _load_vocab(self):
        try:
            import joblib
            if os.path.exists(self.vocab_path):
                data = joblib.load(self.vocab_path)
                self.vocab = data['vocab']
                self.idf_cache = data['idf_cache']
                self._tokenized_corpus = data['_tokenized_corpus']
                self._projection_matrix = data.get('_projection_matrix', [])
                self._ready = True
                print(f"Loaded TF-IDF vocabulary from {self.vocab_path}")
        except Exception as e:
            print(f"Failed to load TF-IDF vocabulary: {e}")
