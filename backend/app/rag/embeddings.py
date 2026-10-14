"""
嵌入模型：OpenAI 兼容 HTTP 嵌入 + Redis 缓存包装。

原先叫 `ModelScopeHTTPEmbeddings`（写在 rag_chain.py 里），
但它其实只依赖 OpenAI 兼容协议，因此泛化命名后可同时服务
ModelScope 与智谱 —— 两者都是 `POST {base_url}/embeddings`。
"""

from __future__ import annotations

from typing import Any, List, Optional, Sequence

from app.core.logging import get_logger
from app.core.redis import get_bound_sync_client, get_keys, sync_cache_get_json, sync_cache_set_json

# 兼容旧调用方（SimpleTFIDFEmbeddings 仍在 app/embeddings 下维护）
from app.embeddings import SimpleTFIDFEmbeddings

logger = get_logger(__name__)

__all__ = [
    "SimpleTFIDFEmbeddings",
    "OpenAICompatEmbeddings",
    "CachedEmbeddings",
    "wrap_with_cache",
]


class OpenAICompatEmbeddings:
    """调用任何 OpenAI 兼容的 `/embeddings` 接口（ModelScope / 智谱 / OpenAI）。

    按 BATCH_SIZE 分批请求；批内数量不符时逐条回退，保证结果与输入一一对应。
    """

    BATCH_SIZE = 32
    TIMEOUT = 60

    def __init__(
        self,
        model_name: str,
        api_key: str,
        base_url: str,
        *,
        batch_size: Optional[int] = None,
        timeout: Optional[int] = None,
    ) -> None:
        import requests

        self.model_name = model_name
        self.api_key = api_key
        self.base_url = (base_url or "").rstrip("/")
        self.batch_size = batch_size or self.BATCH_SIZE
        self.timeout = timeout or self.TIMEOUT
        self.headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        self._session = requests.Session()

    # -- 内部 ------------------------------------------------------------

    def _request(self, inputs: Sequence[str]) -> List[dict]:
        url = f"{self.base_url}/embeddings"
        data = {"model": self.model_name, "input": list(inputs)}
        try:
            resp = self._session.post(
                url, json=data, headers=self.headers, timeout=self.timeout
            )
            resp.raise_for_status()
            return resp.json().get("data", [])
        except Exception as exc:  # noqa: BLE001
            logger.error(f"嵌入请求失败 ({self.model_name}): {exc}")
            return []

    # -- LangChain Embeddings 接口 ---------------------------------------

    def embed_query(self, text: str) -> List[float]:
        items = self._request([text])
        return list(items[0].get("embedding", [])) if items else []

    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        results: List[List[float]] = []
        for start in range(0, len(texts), self.batch_size):
            batch = texts[start : start + self.batch_size]
            items = self._request(batch)
            if len(items) == len(batch):
                results.extend(it.get("embedding", []) for it in items)
            else:
                # 返回数量不符 → 逐条重试，避免向量与文本错位
                logger.warning(
                    f"嵌入批量返回数量不符（{len(items)} != {len(batch)}），逐条重试"
                )
                for text in batch:
                    results.append(self.embed_query(text))
        return results

    def __del__(self) -> None:  # pragma: no cover
        try:
            self._session.close()
        except Exception:  # noqa: BLE001
            pass


class CachedEmbeddings:
    """嵌入结果缓存包装器。

    文本 → 向量是**纯函数**，因此缓存绝对安全，TTL 可以给得很长。
    收益集中在：同一份文档重复上传、同一问题反复提问、重建索引时的重复分块。

    ⚠️ 只包装远端嵌入模型。`SimpleTFIDFEmbeddings` 在 `embed_documents` 时会
    **增量扩词表**（有状态副作用），缓存会跳过该副作用并破坏词表增长，
    因此 `wrap_with_cache()` 对它会直接跳过。
    """

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self._ttl = 24 * 3600
        self._enabled = get_bound_sync_client() is not None
        self.hits = 0
        self.misses = 0

    # -- 内部 ------------------------------------------------------------

    def _key(self, text: str) -> str:
        return get_keys().cache_embedding(text)

    def _get(self, text: str) -> Optional[List[float]]:
        if not self._enabled:
            return None
        cached = sync_cache_get_json(self._key(text))
        if isinstance(cached, list) and cached:
            self.hits += 1
            return cached
        self.misses += 1
        return None

    def _put(self, text: str, vector: Any) -> None:
        if not self._enabled or not vector:
            return
        sync_cache_set_json(self._key(text), list(vector), self._ttl)

    # -- LangChain Embeddings 接口 ---------------------------------------

    def embed_query(self, text: str) -> List[float]:
        cached = self._get(text)
        if cached is not None:
            return cached
        vector = self._inner.embed_query(text)
        self._put(text, vector)
        return vector

    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        results: List[Optional[List[float]]] = [None] * len(texts)
        pending: List[int] = []

        for index, text in enumerate(texts):
            cached = self._get(text)
            if cached is not None:
                results[index] = cached
            else:
                pending.append(index)

        if pending:
            fresh = self._inner.embed_documents([texts[i] for i in pending])
            for offset, index in enumerate(pending):
                vector = fresh[offset] if offset < len(fresh) else []
                results[index] = vector
                self._put(texts[index], vector)

        return [r if r is not None else [] for r in results]

    # -- 可观测性 --------------------------------------------------------

    @property
    def stats(self) -> dict:
        total = self.hits + self.misses
        return {
            "hits": self.hits,
            "misses": self.misses,
            "hit_rate": round(self.hits / total, 3) if total else 0.0,
        }


def wrap_with_cache(embeddings: Any) -> Any:
    """给远端嵌入模型套上缓存；本地 TF-IDF 与 mock 直接原样返回。"""
    if isinstance(embeddings, SimpleTFIDFEmbeddings):
        return embeddings
    if embeddings.__class__.__name__.startswith(("Fake", "Mock")):
        return embeddings
    return CachedEmbeddings(embeddings)
