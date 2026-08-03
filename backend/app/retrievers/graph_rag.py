"""
GraphRAG 模块 — 构建实体关系图谱，支持多跳检索。

流程:
  1. build_graph: 遍历知识库文档，用 LLM 抽取实体与关系，存入 networkx 有向图。
  2. retrieve: 从问题中抽取实体，在图中做 N 跳邻居检索，返回相关子图文本。
  3. 与 hybrid_search 结果融合后送入 LLM 生成。

接入点: rag_chain._get_answer_context 中，将 graph_context 追加到 context_parts。
依赖: pip install networkx
"""

import json
import logging
import os
import pickle
from typing import Dict, List, Optional, Set, Tuple

from langchain_core.documents import Document

logger = logging.getLogger(__name__)

EXTRACT_PROMPT = """从以下文本中抽取实体和关系，输出严格 JSON 格式：
{{
  "entities": ["实体1", "实体2", ...],
  "relations": [["主体", "关系", "客体"], ...]
}}

只输出 JSON，不要解释。

文本：
{text}

JSON："""

QUERY_ENTITY_PROMPT = """从以下问题中提取关键实体（名词/专有名词），用逗号分隔。
只输出实体列表，不要解释。

问题：{question}

实体："""


class GraphRAG:
    """基于 networkx 的轻量级 GraphRAG。"""

    def __init__(self, persist_path: Optional[str] = None):
        import networkx as nx
        self.nx = nx
        from app import config
        self.persist_path = persist_path or os.path.join(config.DATA_PATH, "graph.pkl")
        self.graph = self.nx.DiGraph()
        self._entity_index: Dict[str, Set[str]] = {}
        self._load()

    def _get_llm(self):
        from app.rag_chain import get_query_llm
        return get_query_llm()

    def _llm_call(self, prompt: str) -> str:
        llm = self._get_llm()
        resp = llm.invoke(prompt)
        text = getattr(resp, "content", resp)
        return text if isinstance(text, str) else str(text)

    def _extract_from_text(self, text: str) -> Tuple[List[str], List[Tuple[str, str, str]]]:
        prompt = EXTRACT_PROMPT.format(text=text[:1500])
        raw = self._llm_call(prompt)
        try:
            start, end = raw.find("{"), raw.rfind("}")
            if 0 <= start < end:
                data = json.loads(raw[start:end + 1])
                entities = [str(e).strip() for e in data.get("entities", []) if str(e).strip()]
                relations = []
                for r in data.get("relations", []):
                    if isinstance(r, list) and len(r) == 3:
                        relations.append((str(r[0]).strip(), str(r[1]).strip(), str(r[2]).strip()))
                return entities, relations
        except (json.JSONDecodeError, ValueError) as e:
            logger.debug(f"实体抽取 JSON 解析失败: {e}")
        return [], []

    def add_document(self, doc: Document):
        """将单个文档的实体与关系加入图谱。"""
        entities, relations = self._extract_from_text(doc.page_content)
        source = doc.metadata.get("source", "unknown")

        for entity in entities:
            if not self.graph.has_node(entity):
                self.graph.add_node(entity, sources=set(), mentions=0)
            self.graph.nodes[entity]["mentions"] += 1
            self.graph.nodes[entity]["sources"].add(source)
            self._entity_index.setdefault(entity, set()).add(source)

        for subj, rel, obj in relations:
            if self.graph.has_edge(subj, obj):
                self.graph[subj][obj]["relations"].add(rel)
                self.graph[subj][obj]["sources"].add(source)
            else:
                self.graph.add_edge(subj, obj, relations={rel}, sources={source})

    def build_graph(self, documents: List[Document], batch_size: int = 50):
        """批量构建图谱。"""
        self.graph = self.nx.DiGraph()
        self._entity_index = {}
        for i, doc in enumerate(documents):
            try:
                self.add_document(doc)
                if (i + 1) % batch_size == 0:
                    logger.info(f"图谱构建进度: {i + 1}/{len(documents)}, 节点: {self.graph.number_of_nodes()}")
            except Exception as e:
                logger.warning(f"文档 {i} 图谱构建失败: {e}")
        self._save()
        logger.info(f"图谱构建完成: {self.graph.number_of_nodes()} 节点, {self.graph.number_of_edges()} 边")

    def extract_query_entities(self, question: str) -> List[str]:
        """从问题中抽取实体。"""
        prompt = QUERY_ENTITY_PROMPT.format(question=question)
        raw = self._llm_call(prompt)
        entities = [e.strip() for e in raw.replace("，", ",").split(",") if e.strip() and len(e.strip()) > 1]
        return entities[:10]

    def retrieve(self, question: str, hop: int = 2, max_nodes: int = 20) -> str:
        """
        从问题实体出发，做 N 跳邻居检索，返回子图文本。

        参数:
            hop: 遍历跳数（默认2跳）
            max_nodes: 返回的最大节点数
        返回:
            拼接后的子图描述文本，可直接作为 RAG 上下文
        """
        if self.graph.number_of_nodes() == 0:
            return ""

        query_entities = self.extract_query_entities(question)
        if not query_entities:
            return ""

        visited: Set[str] = set()
        subgraph_edges: List[str] = []

        for entity in query_entities:
            if entity not in self.graph:
                continue
            if entity not in visited:
                visited.add(entity)

            for h in range(hop):
                frontier = [n for n in visited if n in self.graph]
                next_frontier: Set[str] = set()
                for node in frontier:
                    for neighbor in self.graph.successors(node):
                        if neighbor not in visited:
                            next_frontier.add(neighbor)
                        edge_data = self.graph.get_edge_data(node, neighbor, {})
                        rels = ", ".join(edge_data.get("relations", set()))
                        if rels:
                            subgraph_edges.append(f"{node} -[{rels}]-> {neighbor}")
                    for neighbor in self.graph.predecessors(node):
                        if neighbor not in visited:
                            next_frontier.add(neighbor)
                        edge_data = self.graph.get_edge_data(neighbor, node, {})
                        rels = ", ".join(edge_data.get("relations", set()))
                        if rels:
                            subgraph_edges.append(f"{neighbor} -[{rels}]-> {node}")
                visited.update(next_frontier)
                if len(visited) >= max_nodes:
                    break
            if len(visited) >= max_nodes:
                break

        if not subgraph_edges:
            return ""

        unique_edges = list(dict.fromkeys(subgraph_edges))[:max_nodes]
        return "【知识图谱关系】\n" + "\n".join(unique_edges)

    def get_stats(self) -> dict:
        return {
            "nodes": self.graph.number_of_nodes(),
            "edges": self.graph.number_of_edges(),
        }

    def _save(self):
        try:
            serializable_graph = self.nx.DiGraph()
            for node, data in self.graph.nodes(data=True):
                serializable_graph.add_node(node, mentions=data.get("mentions", 0),
                                            sources=list(data.get("sources", set())))
            for u, v, data in self.graph.edges(data=True):
                serializable_graph.add_edge(u, v,
                                            relations=list(data.get("relations", set())),
                                            sources=list(data.get("sources", set())))
            with open(self.persist_path, "wb") as f:
                pickle.dump({"graph": serializable_graph, "entity_index": {
                    k: list(v) for k, v in self._entity_index.items()
                }}, f)
            logger.info(f"图谱已保存: {self.persist_path}")
        except Exception as e:
            logger.error(f"图谱保存失败: {e}")

    def _load(self):
        try:
            if os.path.exists(self.persist_path):
                with open(self.persist_path, "rb") as f:
                    data = pickle.load(f)
                self.graph = data["graph"]
                for node, ndata in self.graph.nodes(data=True):
                    ndata["sources"] = set(ndata.get("sources", []))
                for u, v, edata in self.graph.edges(data=True):
                    edata["relations"] = set(edata.get("relations", []))
                    edata["sources"] = set(edata.get("sources", []))
                self._entity_index = {k: set(v) for k, v in data.get("entity_index", {}).items()}
                logger.info(f"加载图谱: {self.graph.number_of_nodes()} 节点, {self.graph.number_of_edges()} 边")
        except Exception as e:
            logger.warning(f"图谱加载失败: {e}")


_graph_instance: Optional[GraphRAG] = None


def get_graph_rag() -> GraphRAG:
    global _graph_instance
    if _graph_instance is None:
        _graph_instance = GraphRAG()
    return _graph_instance
