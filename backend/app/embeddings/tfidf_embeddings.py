"""
TF-IDF embedding —— 固定 768 维，用特征哈希投影。

为什么改（2026-11-05）
    旧实现把一个 768 × 词表大小 的随机投影矩阵和 TF-IDF 向量做稠密乘法，
    而且是**纯 Python 双重循环**。词表涨到 2 万时，单个文档要做
    768 × 20650 ≈ 1580 万次浮点运算 —— 实测一篇短文要 86 秒。
    表现就是「批量上传时每个文件卡一分多钟」，10 个文件直接十几分钟。

    更糟的是：投影矩阵的宽度等于词表大小，词表一涨，**所有历史向量的含义都变了**，
    但维度仍是 768，Chroma 不会报错，只会悄悄地把新旧向量混在一起检索。

现在的做法（hashing trick）
    每个 token 稳定哈希到 768 个桶之一，权重直接累加：
        vec[hash(token) % 768] += (tf) * idf
    代价是 O(词数)，与词表大小无关；投影不再依赖词表，历史向量始终一致。

⚠️ 哈希投影与旧的稠密投影**不兼容** —— 升级后必须重建一次向量库
   （`POST /rebuild-vectorstore`，或在客户端里重建）。重建很便宜：纯哈希，毫秒级。
"""

from __future__ import annotations

import hashlib
import math
import os
import re
from collections import defaultdict
from typing import List

from app.core.config import get_settings


class SimpleTFIDFEmbeddings:
    """TF-IDF + 特征哈希，固定 768 维。"""

    FIXED_DIM = 768

    def __init__(self) -> None:
        #: token -> 文档频次（只用于算 idf，不再决定向量维度）
        self._df: dict[str, int] = {}
        self._doc_count = 0
        self._ready = False
        self.vocab_path = os.path.join(get_settings().paths.data_path, "tfidf_vocab.pkl")
        self._load_vocab()

    # -- 基础 ---------------------------------------------------------------
    @property
    def vocab(self) -> dict[str, int]:
        """兼容旧字段：token -> 文档频次（仅作查看）。"""
        return self._df

    def _tokenize(self, text: str) -> List[str]:
        return re.findall(r"[一-鿿]+|[a-zA-Z0-9]+", text.lower())

    @staticmethod
    def _bucket(token: str) -> int:
        """稳定桶号。用 blake2b 而不是内置 hash()：后者每个进程随机加盐。"""
        digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
        return int.from_bytes(digest, "big") % SimpleTFIDFEmbeddings.FIXED_DIM

    def _idf(self, token: str) -> float:
        df = self._df.get(token, 0)
        # 平滑：没见过的词给最大权重（它们往往是最有区分度的）
        return math.log((self._doc_count + 1) / (df + 1)) + 1.0

    # -- 统计（增量，不再每次重算全语料）------------------------------------
    def _observe(self, token_lists: List[List[str]]) -> None:
        if not token_lists:
            return
        added = 0
        for tokens in token_lists:
            for token in set(tokens):
                if token not in self._df:
                    added += 1
                self._df[token] = self._df.get(token, 0) + 1
        self._doc_count += len(token_lists)
        # 只有出现新词才落盘：每上传一个文件就重写一次 pickle，
        # 词表上万时是几十 MB 的磁盘写，纯属浪费。
        if added:
            self._save_vocab()

    # -- 投影 ---------------------------------------------------------------
    def _embed_tokens(self, tokens: List[str]) -> List[float]:
        vec = [0.0] * self.FIXED_DIM
        if not tokens:
            return vec
        counts: dict[str, int] = defaultdict(int)
        for token in tokens:
            counts[token] += 1
        total = len(tokens)
        for token, count in counts.items():
            weight = (count / total) * self._idf(token)
            idx = self._bucket(token)
            # 桶号的奇偶决定符号，减少哈希冲突带来的系统性偏置
            sign = 1.0 if (idx & 1) == 0 else -1.0
            vec[idx] += sign * weight
        norm = math.sqrt(sum(v * v for v in vec))
        if norm > 0:
            vec = [v / norm for v in vec]
        return vec

    # -- 对外接口（与 LangChain / Chroma 约定一致）--------------------------
    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        token_lists = [self._tokenize(t) for t in texts]
        self._ready = True
        self._observe(token_lists)
        return [self._embed_tokens(tokens) for tokens in token_lists]

    def embed_query(self, text: str) -> List[float]:
        # 查询也算「已经开始工作」：标记 ready，避免上层误判成冷启动
        self._ready = True
        if not text.strip():
            return [0.0] * self.FIXED_DIM
        return self._embed_tokens(self._tokenize(text))

    def __call__(self, input):  # noqa: A002
        texts = [input] if isinstance(input, str) else list(input)
        return self.embed_documents(texts)

    def add_texts(self, texts: List[str]) -> None:
        self._observe([self._tokenize(t) for t in texts])

    # -- 持久化（只存 df 统计，几百 KB）------------------------------------
    def _save_vocab(self) -> None:
        try:
            import joblib

            joblib.dump({"df": self._df, "doc_count": self._doc_count}, self.vocab_path)
        except Exception as exc:  # noqa: BLE001
            print(f"Failed to save TF-IDF vocabulary: {exc}")

    def _load_vocab(self) -> None:
        try:
            import joblib

            if not os.path.exists(self.vocab_path):
                return
            data = joblib.load(self.vocab_path)
            # 旧格式（vocab / idf_cache / _projection_matrix）直接丢弃：
            # 投影方式已经变了，旧统计留着也没用，当作冷启动。
            df = data.get("df")
            if isinstance(df, dict):
                self._df = df
                self._doc_count = int(data.get("doc_count") or 0)
                self._ready = True
                print(f"Loaded TF-IDF stats: {len(self._df)} tokens / {self._doc_count} docs")
            else:
                print("检测到旧版 TF-IDF 词表，已忽略（需要重建向量库）")
        except Exception as exc:  # noqa: BLE001
            print(f"Failed to load TF-IDF vocabulary: {exc}")
