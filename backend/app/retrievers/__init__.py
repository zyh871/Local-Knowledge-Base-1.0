"""
检索器集合。

保留：
  * `bm25_retriever`      —— BM25 关键词检索
  * `vector_retriever`    —— Chroma 向量检索
  * `rrf_fusion`          —— RRF 融合

已移除（原为未被任何路径调用的死代码，见 docs/REFACTOR_*.md 第 0.2 节）：
  * `graph_rag`             —— GraphRAG 多跳检索，未接线
  * `metadata_filter`       —— 元数据过滤检索，未接线
  * `multimodal_retriever`  —— 多模态检索，未接线
  * `query_rewriter`        —— 查询改写，未接线
  * `reranker`              —— 重排序，未接线

如需恢复上述能力，应作为**状态图节点**接入
（挂点见 `app/graph/nodes/retrieval.py` 的 `retrieve` 节点），
而不是继续以"看起来存在、实际不生效"的形式留在仓库里。
"""

from .bm25_retriever import BM25Retriever
from .rrf_fusion import rrf_fusion
from .vector_retriever import VectorRetriever

__all__ = ["BM25Retriever", "VectorRetriever", "rrf_fusion"]
