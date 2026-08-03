"""
查询改写与多路召回 — 在 hybrid_search 之前对原始查询做改写/扩展。

策略：
  1. multi_query: 用 LLM 生成多个语义等价的改写查询，分别召回后去重合并。
  2. hyde: 让 LLM 先生成一段"假设性回答"，再用该回答做向量召回（弥补 query-doc 表达差异）。
  3. 指代消解: 结合对话历史将代词替换为实体。

接入点: rag_chain.hybrid_search 调用前，先用 QueryRewriter 改写。
"""

import logging
from typing import List, Optional

from langchain_core.documents import Document

logger = logging.getLogger(__name__)

MULTI_QUERY_PROMPT = """你是一个查询改写助手。请将下面的用户问题改写为 {n} 个语义相同但表达不同的变体，用于提升检索召回率。
要求：
- 每行一个改写，不要编号
- 保持原意，只换表达方式
- 不要解释

原始问题：{question}

改写："""

HYDE_PROMPT = """请针对以下问题写一段可能的答案段落（不需要正确，只要符合该问题主题的文档风格），
这段文字将用于检索知识库中语义相似的文档。

问题：{question}

答案段落："""


class QueryRewriter:
    """查询改写器，支持 multi_query / hyde / 指代消解三种模式。"""

    def __init__(self, mode: str = "multi_query", n_variants: int = 3):
        self.mode = mode
        self.n_variants = n_variants

    def _llm_call(self, prompt: str) -> str:
        from app.rag_chain import get_query_llm
        llm = get_query_llm()
        resp = llm.invoke(prompt)
        text = getattr(resp, "content", resp)
        return text if isinstance(text, str) else str(text)

    def multi_query(self, question: str) -> List[str]:
        """生成多个改写查询。"""
        prompt = MULTI_QUERY_PROMPT.format(n=self.n_variants, question=question)
        raw = self._llm_call(prompt)
        queries = [line.strip() for line in raw.strip().split("\n") if line.strip()]
        queries = [q for q in queries if len(q) > 2][: self.n_variants]
        return queries or [question]

    def hyde(self, question: str) -> str:
        """生成假设性文档，返回扩充后的查询文本。"""
        prompt = HYDE_PROMPT.format(question=question)
        hypothetical = self._llm_call(prompt).strip()
        return f"{question}\n{hypothetical[:500]}" if hypothetical else question

    def resolve_coreference(self, question: str, history: List[dict]) -> str:
        """基于对话历史的简易指代消解。"""
        if not history:
            return question
        recent = history[-3:]
        context_entities = []
        for msg in recent:
            if msg.get("role") == "user":
                context_entities.append(msg.get("content", ""))
        if not context_entities:
            return question
        pronouns = ["它", "他", "她", "这个", "那个", "上面", "之前提到的"]
        if any(p in question for p in pronouns):
            last_topic = context_entities[-1][:50]
            return f"{question}（指代：{last_topic}）"
        return question

    def rewrite(self, question: str, history: Optional[List[dict]] = None) -> List[str]:
        """返回改写后的查询列表，供多路召回使用。"""
        history = history or []
        resolved = self.resolve_coreference(question, history)

        if self.mode == "hyde":
            return [self.hyde(resolved)]
        if self.mode == "multi_query":
            return self.multi_query(resolved)
        if self.mode == "hybrid":
            queries = self.multi_query(resolved)
            queries.append(self.hyde(resolved))
            return list(dict.fromkeys(queries))
        return [resolved]


def multi_query_retrieve(
    question: str,
    history: Optional[List[dict]] = None,
    k: int = 3,
    mode: str = "multi_query",
) -> List[Document]:
    """
    多路召回入口：对每个改写查询执行 hybrid_search，合并去重后返回 top-k。

    用法（替换 rag_chain.hybrid_search 调用）:
        docs = multi_query_retrieve(question, history=history, k=config.RETRIEVER_K)
    """
    rewriter = QueryRewriter(mode=mode)
    queries = rewriter.rewrite(question, history)
    logger.info(f"查询改写: {question[:30]} -> {len(queries)} 路召回")

    seen_ids = set()
    merged: List[tuple] = []
    from app.rag_chain import hybrid_search
    for q in queries:
        docs = hybrid_search(q, k=k)
        for doc in docs:
            doc_id = id(doc)
            if doc_id not in seen_ids:
                seen_ids.add(doc_id)
                merged.append(doc)

    return merged[: k * 2]
