"""
重排序模块 — 在 RRF 融合后对候选文档做二次精排。

两种后端：
  1. cross_encoder: 使用 HuggingFace 交叉编码器（如 bge-reranker-base），本地推理。
  2. llm: 使用 LLM 对每个 (query, doc) 对打分，无需额外模型依赖。

接入点: rag_chain.hybrid_search 返回后、送入 prompt 前，调用 rerank()。
"""

import logging
import os
from typing import List, Optional, Tuple

from langchain_core.documents import Document

logger = logging.getLogger(__name__)

RERANK_LLM_PROMPT = """请判断以下文档片段与用户问题的相关性。
只输出一个 0 到 10 的整数分数，10 表示完全相关，0 表示无关。不要输出其他内容。

问题：{question}

文档片段：{doc_text}

相关性分数："""


class CrossEncoderReranker:
    """基于 HuggingFace 交叉编码器的重排序器。"""

    def __init__(self, model_name: str = "BAAI/bge-reranker-base"):
        self.model_name = model_name
        self._model = None

    def _load(self):
        if self._model is not None:
            return
        try:
            from FlagEmbedding import FlagModel
            self._model = FlagModel(
                self.model_name,
                use_fp16=os.getenv("RERANK_USE_FP16", "true").lower() == "true",
            )
            logger.info(f"加载交叉编码器: {self.model_name}")
        except ImportError:
            logger.warning("FlagEmbedding 未安装，回退到 LLM 重排序。请: pip install FlagEmbedding")
            raise

    def rerank(self, query: str, docs: List[Document], top_n: int = 5) -> List[Document]:
        self._load()
        if not docs:
            return []
        pairs = [[query, d.page_content[:512]] for d in docs]
        scores = self._model.compute_score(pairs, normalize=True)
        if isinstance(scores, float):
            scores = [scores]
        ranked = sorted(zip(docs, scores), key=lambda x: x[1], reverse=True)
        return [d for d, _ in ranked[:top_n]]


class LLMReranker:
    """基于 LLM 打分的重排序器，无需额外模型。"""

    def __init__(self, batch_mode: bool = False):
        self.batch_mode = batch_mode

    def _get_llm(self):
        from app.rag_chain import get_query_llm
        return get_query_llm()

    def _score_single(self, query: str, doc_text: str) -> float:
        llm = self._get_llm()
        prompt = RERANK_LLM_PROMPT.format(question=query, doc_text=doc_text[:800])
        resp = llm.invoke(prompt)
        text = getattr(resp, "content", resp)
        text = text if isinstance(text, str) else str(text)
        try:
            score = float(text.strip())
            return max(0.0, min(10.0, score))
        except ValueError:
            return 0.0

    def _score_batch(self, query: str, docs: List[Document]) -> List[float]:
        """单次 LLM 调用对所有文档排序（适合候选数较少时）。"""
        llm = self._get_llm()
        doc_list = "\n".join(
            f"[{i}] {d.page_content[:200]}..." for i, d in enumerate(docs)
        )
        prompt = f"""请根据与问题的相关性，对以下文档片段排序。
输出最相关的文档编号列表，用逗号分隔，如：2,0,4,1,3

问题：{query}

文档片段：
{doc_list}

排序结果："""
        resp = llm.invoke(prompt)
        text = getattr(resp, "content", resp)
        text = text if isinstance(text, str) else str(text)
        order = []
        for token in text.replace("，", ",").split(","):
            token = token.strip().strip("[]")
            if token.isdigit():
                idx = int(token)
                if 0 <= idx < len(docs) and idx not in order:
                    order.append(idx)
        for i in range(len(docs)):
            if i not in order:
                order.append(i)
        scores = [0.0] * len(docs)
        for rank, idx in enumerate(order):
            scores[idx] = 10.0 - rank * (10.0 / max(len(docs), 1))
        return scores

    def rerank(self, query: str, docs: List[Document], top_n: int = 5) -> List[Document]:
        if not docs:
            return []
        if self.batch_mode and len(docs) <= 10:
            scores = self._score_batch(query, docs)
        else:
            scores = [self._score_single(query, d.page_content) for d in docs]
        ranked = sorted(zip(docs, scores), key=lambda x: x[1], reverse=True)
        logger.info(f"LLM 重排序完成, top分数: {ranked[0][1]:.1f}" if ranked else "")
        return [d for d, _ in ranked[:top_n]]


def rerank(
    query: str,
    docs: List[Document],
    top_n: int = 5,
    backend: str = "llm",
    model_name: str = "BAAI/bge-reranker-base",
) -> List[Document]:
    """
    统一重排序入口。

    参数:
        backend: "cross_encoder" (需安装 FlagEmbedding) 或 "llm" (无额外依赖)
        top_n: 返回的文档数

    用法:
        docs = hybrid_search(question, k=10)        # 先多召回
        docs = rerank(question, docs, top_n=3)       # 再精排
    """
    if not docs:
        return []

    if backend == "cross_encoder":
        try:
            ranker = CrossEncoderReranker(model_name=model_name)
            return ranker.rerank(query, docs, top_n=top_n)
        except Exception as e:
            logger.warning(f"交叉编码器不可用，回退 LLM 重排序: {e}")

    ranker = LLMReranker()
    return ranker.rerank(query, docs, top_n=top_n)
